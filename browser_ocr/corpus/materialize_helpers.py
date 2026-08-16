from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path


def _read_object(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"expected JSON object: {path}")
    return value


def _atomic_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f"{path.name}.partial-{os.getpid()}")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _jobs_fingerprint(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _ordered_crop_batches(
    jobs: list[dict],
    *,
    start_index: int,
    worker_count: int,
) -> list[list[list[tuple[int, dict]]]]:
    if worker_count <= 0:
        raise ValueError("recognition crop worker_count must be positive")
    groups: list[list[tuple[int, dict]]] = []
    current: list[tuple[int, dict]] = []
    current_image: str | None = None
    for offset, job in enumerate(jobs):
        image = str(job.get("image") or "") if isinstance(job, dict) else ""
        if current and image != current_image:
            groups.append(current)
            current = []
        current.append((start_index + offset, job))
        current_image = image
    if current:
        groups.append(current)
    return [groups[index:index + worker_count] for index in range(0, len(groups), worker_count)]


def _crop_worker_count() -> int:
    configured = os.environ.get("OCR_CORPUS_CROP_WORKERS")
    if configured is not None:
        try:
            workers = int(configured)
        except ValueError as exc:
            raise ValueError("OCR_CORPUS_CROP_WORKERS must be a positive integer") from exc
        if workers <= 0:
            raise ValueError("OCR_CORPUS_CROP_WORKERS must be a positive integer")
        return workers
    return min(8, max(1, os.cpu_count() or 1))


def crop_jobs(path: Path, state_path: Path) -> dict:
    import cv2

    from browser_ocr.detection.detector_benchmark import rectify_text_crop

    payload = _read_object(path)
    jobs = payload.get("jobs")
    if not isinstance(jobs, list):
        raise ValueError("crop jobs must contain jobs array")
    fingerprint = _jobs_fingerprint(path)
    if state_path.is_file():
        state = _read_object(state_path)
        if state.get("jobs_sha256") != fingerprint or state.get("total") != len(jobs):
            raise ValueError("recognition crop checkpoint profile mismatch")
        completed = int(state.get("completed", -1))
        if completed < 0 or completed > len(jobs):
            raise ValueError("recognition crop checkpoint has invalid completed count")
        for index, job in enumerate(jobs[:completed], start=1):
            output_path = Path(str(job.get("output") or ""))
            if not output_path.is_file():
                raise ValueError(f"recognition crop checkpoint output missing at job {index}: {output_path}")
        if state.get("status") == "completed":
            return {"schema_version": 1, "status": "completed", "processed": completed, "resumed_from": completed}
    else:
        completed = 0
        _atomic_json(state_path, {
            "schema_version": 1,
            "status": "running",
            "jobs_sha256": fingerprint,
            "total": len(jobs),
            "completed": 0,
        })

    resumed_from = completed
    report_every = max(1, len(jobs) // 20)
    next_report = ((completed // report_every) + 1) * report_every
    worker_count = _crop_worker_count()
    batches = _ordered_crop_batches(
        jobs[completed:],
        start_index=completed + 1,
        worker_count=worker_count,
    )

    def process_group(group: list[tuple[int, dict]]) -> None:
        first_ordinal, first_job = group[0]
        if not isinstance(first_job, dict):
            raise ValueError(f"crop job {first_ordinal} must be an object")
        image_path = str(first_job.get("image") or "")
        if not image_path:
            raise ValueError(f"crop job {first_ordinal} is missing image/output")
        image = cv2.imread(image_path, cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"could not decode crop source image: {image_path}")
        for ordinal, job in group:
            if not isinstance(job, dict):
                raise ValueError(f"crop job {ordinal} must be an object")
            if str(job.get("image") or "") != image_path:
                raise ValueError(f"crop job {ordinal} source image grouping mismatch")
            output_path = Path(str(job.get("output") or ""))
            polygon = job.get("polygon")
            if not output_path.name:
                raise ValueError(f"crop job {ordinal} is missing image/output")
            crop = rectify_text_crop(image, polygon)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            temporary = output_path.with_name(
                f"{output_path.stem}.partial-{os.getpid()}-{ordinal}{output_path.suffix}"
            )
            if not cv2.imwrite(str(temporary), crop):
                raise ValueError(f"could not write recognition crop: {temporary}")
            os.replace(temporary, output_path)

    with ThreadPoolExecutor(max_workers=worker_count, thread_name_prefix="ocr-crop") as executor:
        for batch in batches:
            futures = [executor.submit(process_group, group) for group in batch]
            for future in futures:
                future.result()
            completed = batch[-1][-1][0]
            # Claim only a fully completed ordered prefix. If a worker fails,
            # retrying may overwrite a bounded tail but cannot skip a crop.
            _atomic_json(state_path, {
                "schema_version": 1,
                "status": "running",
                "jobs_sha256": fingerprint,
                "total": len(jobs),
                "completed": completed,
            })
            if completed >= next_report or completed == len(jobs):
                print(f"[ocr-corpus] recognition-crops {completed}/{len(jobs)}", file=sys.stderr, flush=True)
                while next_report <= completed:
                    next_report += report_every

    _atomic_json(state_path, {
        "schema_version": 1,
        "status": "completed",
        "jobs_sha256": fingerprint,
        "total": len(jobs),
        "completed": completed,
    })
    return {"schema_version": 1, "status": "completed", "processed": completed, "resumed_from": resumed_from}


def finalize_recognition(manifest_path: Path, assignment_path: Path, paddle_output: Path) -> dict:
    from browser_ocr.finetune.dataset import export_paddle, load_dataset

    dataset = load_dataset(manifest_path)
    assignment = _read_object(assignment_path)
    splits = assignment.get("splits")
    if not isinstance(splits, dict) or set(splits) != {"train", "val", "test"}:
        raise ValueError("recognition split assignment must contain train, val and test")
    all_ids = [sample_id for name in ("train", "val", "test") for sample_id in splits[name]]
    expected = {sample["id"] for sample in dataset.samples}
    if len(all_ids) != len(set(all_ids)) or set(all_ids) != expected:
        raise ValueError("recognition split assignment must cover every crop exactly once")
    document_sizes = Counter(sample["document_id"] for sample in dataset.samples)
    split = {
        "schema_version": 1,
        "parent_corpus_id": str(assignment["parent_corpus_id"]),
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "group_by": "document_id",
        "seed": int(assignment["seed"]),
        "ratios": dict(assignment["ratios"]),
        "component_count": int(assignment["document_count"]),
        "max_component_size": max(document_sizes.values(), default=0),
        "counts": {name: len(splits[name]) for name in ("train", "val", "test")},
        "splits": {name: list(splits[name]) for name in ("train", "val", "test")},
        "assignment": "parent_document_split_v1",
    }
    split_path = manifest_path.parent / "document-split.json"
    split_path.write_text(json.dumps(split, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    export = export_paddle(dataset, split, paddle_output)
    return {
        "schema_version": 1,
        "status": "completed",
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "split": split,
        "paddle_export": export,
    }


def main() -> None:
    parser = argparse.ArgumentParser(prog="ocr-corpus-helper")
    sub = parser.add_subparsers(dest="command", required=True)
    crop = sub.add_parser("crop")
    crop.add_argument("--jobs", required=True)
    crop.add_argument("--state", required=True)
    finalize = sub.add_parser("finalize-recognition")
    finalize.add_argument("--manifest", required=True)
    finalize.add_argument("--assignments", required=True)
    finalize.add_argument("--paddle-output", required=True)
    args = parser.parse_args()
    if args.command == "crop":
        result = crop_jobs(Path(args.jobs).resolve(), Path(args.state).resolve())
    else:
        result = finalize_recognition(
            Path(args.manifest).resolve(),
            Path(args.assignments).resolve(),
            Path(args.paddle_output).resolve(),
        )
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()
