from __future__ import annotations

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from .dataset import DatasetError, load_dataset
from .model_compat import audit_model_compatibility
from .training import (
    build_baseline_overrides,
    build_smoke_overrides,
    find_resume_checkpoint,
    parse_eval_metrics,
    probe_paddle_runtime,
)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _json_file(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"JSON file must contain an object: {path}")
    return value


def _write_json_atomic(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _verify_sha(path: Path, expected: str, label: str) -> None:
    if not path.is_file():
        raise DatasetError(f"{label} does not exist: {path}")
    actual = _sha256_file(path)
    if actual != expected:
        raise DatasetError(f"{label} SHA-256 mismatch: expected {expected}, got {actual}")


def _format_override(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return str(value)


def _subset_labels(source: Path, target: Path, count: int) -> int:
    lines = source.read_text(encoding="utf-8").splitlines()
    if len(lines) < count:
        raise DatasetError(f"label file {source} has only {len(lines)} rows; need {count}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines[:count]) + "\n", encoding="utf-8")
    return count


def run_probe() -> dict:
    try:
        import paddle
    except Exception as exc:
        raise DatasetError(f"could not import PaddlePaddle runtime: {exc}") from exc
    return probe_paddle_runtime(paddle)



def _validated_baseline_inputs(args: argparse.Namespace) -> dict[str, object]:
    upstream = _json_file(Path(args.upstream).resolve())
    if upstream.get("pin_status") != "training-smoke-verified" or not upstream.get("training_enabled"):
        raise DatasetError("training runtime has not passed the pinned smoke gate")

    source_root = Path(args.paddleocr_root).resolve()
    paddle_info = upstream["paddleocr"]
    config_path = source_root / paddle_info["config_path"]
    dictionary_path = source_root / paddle_info["dictionary_path"]
    _verify_sha(config_path, paddle_info["config_sha256"], "PaddleOCR config")
    _verify_sha(dictionary_path, paddle_info["dictionary_sha256"], "PaddleOCR dictionary")

    weight = Path(args.pretrained_model).resolve()
    if not weight.is_file() or weight.stat().st_size != upstream["pretrained_model_bytes"]:
        raise DatasetError("pretrained model byte count does not match upstream pin")
    _verify_sha(weight, upstream["pretrained_model_sha256"], "pretrained model")

    dataset = load_dataset(args.manifest)
    contract = upstream["model_contract"]
    compatibility = audit_model_compatibility(
        dataset,
        dictionary_path,
        max_text_length=contract["max_text_length"],
        use_space_char=contract["use_space_char"],
    )
    if compatibility["status"] != "ok":
        raise DatasetError("dataset is incompatible with pinned recognizer contract")

    export_dir = Path(args.export_dir).resolve()
    export_meta = _json_file(export_dir / "export.json")
    if export_meta.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("Paddle export fingerprint does not match dataset")
    if export_meta.get("group_by") != args.expected_group_by:
        raise DatasetError("Paddle export holdout axis does not match requested baseline profile")
    counts = export_meta.get("counts")
    if not isinstance(counts, dict) or any(not isinstance(counts.get(key), int) or counts[key] <= 0 for key in ("train", "val", "test")):
        raise DatasetError("Paddle export counts are missing or invalid")
    for split_name in ("train", "val", "test"):
        label_path = export_dir / f"{split_name}.txt"
        if not label_path.is_file():
            raise DatasetError(f"Paddle export label file is missing: {label_path}")
        with label_path.open("r", encoding="utf-8") as handle:
            line_count = sum(1 for _ in handle)
        if line_count != counts[split_name]:
            raise DatasetError(
                f"Paddle export {split_name} count mismatch: metadata={counts[split_name]}, labels={line_count}"
            )
    return {
        "upstream": upstream,
        "source_root": source_root,
        "paddle_info": paddle_info,
        "config_path": config_path,
        "weight": weight,
        "dataset": dataset,
        "export_dir": export_dir,
        "export_meta": export_meta,
    }


def _stream_command(
    command: list[str],
    *,
    cwd: Path,
    log_path: Path,
    capture: bool = True,
    append: bool = False,
) -> str:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    captured: list[str] = []
    with log_path.open("a" if append else "w", encoding="utf-8") as log:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="", flush=True)
            log.write(line)
            log.flush()
            if capture:
                captured.append(line)
        process.stdout.close()
        return_code = process.wait()
    if return_code != 0:
        raise DatasetError(f"command failed with exit code {return_code}: {command[1]}")
    return "".join(captured)


def _evaluate_model(
    *,
    source_root: Path,
    config_path: Path,
    dataset_root: Path,
    test_labels: Path,
    batch_size: int,
    log_path: Path,
    pretrained_model: Path | None = None,
    checkpoint: Path | None = None,
) -> dict[str, float]:
    if (pretrained_model is None) == (checkpoint is None):
        raise DatasetError("evaluation requires exactly one model source")
    overrides: dict[str, object] = {
        "Global.use_gpu": True,
        "Global.distributed": False,
        "Eval.dataset.data_dir": str(dataset_root),
        "Eval.dataset.label_file_list": [str(test_labels)],
        "Eval.loader.batch_size_per_card": batch_size,
        "Eval.loader.num_workers": 2,
        "Eval.loader.shuffle": False,
    }
    if checkpoint is not None:
        overrides["Global.checkpoints"] = str(checkpoint)
    else:
        overrides["Global.pretrained_model"] = str(pretrained_model)
    command = [sys.executable, "tools/eval.py", "-c", str(config_path), "-o"]
    command.extend(f"{key}={_format_override(value)}" for key, value in overrides.items())
    return parse_eval_metrics(_stream_command(command, cwd=source_root, log_path=log_path))


def run_baseline(args: argparse.Namespace) -> dict:
    context = _validated_baseline_inputs(args)
    upstream = context["upstream"]
    source_root = context["source_root"]
    paddle_info = context["paddle_info"]
    config_path = context["config_path"]
    weight = context["weight"]
    dataset = context["dataset"]
    export_dir = context["export_dir"]
    export_meta = context["export_meta"]

    if not isinstance(args.epochs, int) or args.epochs <= 0:
        raise DatasetError("epochs must be a positive integer")
    if not isinstance(args.batch_size, int) or args.batch_size <= 0:
        raise DatasetError("batch size must be a positive integer")

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / ".baseline.lock"
    profile = {
        "schema_version": 1,
        "dataset_id": dataset.manifest["dataset_id"],
        "dataset_fingerprint": dataset.fingerprint,
        "export_group_by": export_meta["group_by"],
        "export_counts": export_meta["counts"],
        "upstream_commit": paddle_info["commit"],
        "pretrained_model_sha256": upstream["pretrained_model_sha256"],
        "epochs": args.epochs,
        "batch_size": args.batch_size,
    }
    state_path = run_dir / "baseline-state.json"
    result_path = run_dir / "baseline-result.json"
    model_dir = run_dir / "model"

    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"baseline training is already active in {run_dir}") from exc

        if result_path.exists():
            result = _json_file(result_path)
            if result.get("profile") != profile:
                raise DatasetError("completed baseline profile does not match requested configuration")
            best_checkpoint = Path(result["best_checkpoint"])
            _verify_sha(best_checkpoint, result["best_checkpoint_sha256"], "baseline best checkpoint")
            return result

        if state_path.exists():
            state = _json_file(state_path)
            if state.get("profile") != profile:
                raise DatasetError("baseline state profile does not match requested configuration")
        else:
            if model_dir.exists():
                raise DatasetError("baseline model directory exists without authoritative state")
            state = {"schema_version": 1, "status": "initializing", "profile": profile}
            _write_json_atomic(state_path, state)

        pretrained_eval_path = run_dir / "pretrained-test.json"
        if pretrained_eval_path.exists():
            pretrained_eval = _json_file(pretrained_eval_path)
            if pretrained_eval.get("profile") != profile:
                raise DatasetError("cached pretrained evaluation profile mismatch")
        else:
            state["status"] = "evaluating-pretrained"
            _write_json_atomic(state_path, state)
            pretrained_metrics = _evaluate_model(
                source_root=source_root,
                config_path=config_path,
                dataset_root=dataset.root,
                test_labels=export_dir / "test.txt",
                batch_size=args.batch_size,
                log_path=run_dir / "pretrained-test.log",
                pretrained_model=weight,
            )
            pretrained_eval = {"schema_version": 1, "profile": profile, "metrics": pretrained_metrics}
            _write_json_atomic(pretrained_eval_path, pretrained_eval)

        resume = find_resume_checkpoint(model_dir)
        if resume is None and model_dir.exists():
            shutil.rmtree(model_dir)
        model_dir.mkdir(parents=True, exist_ok=True)
        overrides = build_baseline_overrides(
            dataset_root=str(dataset.root),
            train_labels=str(export_dir / "train.txt"),
            val_labels=str(export_dir / "val.txt"),
            pretrained_model=str(weight),
            checkpoint=str(resume) if resume else None,
            output_dir=str(model_dir),
            batch_size=args.batch_size,
            epochs=args.epochs,
        )
        command = [sys.executable, "tools/train.py", "-c", str(config_path), "-o"]
        command.extend(f"{key}={_format_override(value)}" for key, value in overrides.items())
        state.update(
            {
                "status": "training",
                "resume_checkpoint": str(resume) if resume else None,
                "command": command,
            }
        )
        _write_json_atomic(state_path, state)
        try:
            _stream_command(
                command,
                cwd=source_root,
                log_path=run_dir / "train.log",
                capture=False,
                append=resume is not None,
            )
        except DatasetError:
            state["status"] = "failed"
            recovered = find_resume_checkpoint(model_dir)
            state["recoverable_checkpoint"] = str(recovered) if recovered else None
            _write_json_atomic(state_path, state)
            raise

        best_prefix = model_dir / "best_accuracy"
        best_checkpoint = Path(str(best_prefix) + ".pdparams")
        if not best_checkpoint.is_file():
            raise DatasetError("baseline training completed without best_accuracy.pdparams")
        final_epoch = model_dir / f"iter_epoch_{args.epochs}"
        if not all(Path(str(final_epoch) + suffix).is_file() for suffix in (".pdparams", ".pdopt", ".states")):
            raise DatasetError(f"baseline training did not persist a complete epoch {args.epochs} checkpoint")

        state["status"] = "evaluating-best"
        state["best_checkpoint"] = str(best_checkpoint)
        _write_json_atomic(state_path, state)
        best_metrics = _evaluate_model(
            source_root=source_root,
            config_path=config_path,
            dataset_root=dataset.root,
            test_labels=export_dir / "test.txt",
            batch_size=args.batch_size,
            log_path=run_dir / "best-test.log",
            checkpoint=best_prefix,
        )
        pretrained_metrics = pretrained_eval["metrics"]
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "pretrained_test": pretrained_metrics,
            "best_test": best_metrics,
            "delta": {
                "acc": best_metrics["acc"] - pretrained_metrics["acc"],
                "norm_edit_dis": best_metrics["norm_edit_dis"] - pretrained_metrics["norm_edit_dis"],
            },
            "best_checkpoint": str(best_checkpoint),
            "best_checkpoint_sha256": _sha256_file(best_checkpoint),
            "final_epoch_checkpoint": str(final_epoch) + ".pdparams",
            "final_epoch_checkpoint_sha256": _sha256_file(Path(str(final_epoch) + ".pdparams")),
            "train_log": str(run_dir / "train.log"),
        }
        _write_json_atomic(result_path, result)
        state["status"] = "completed"
        state["result"] = str(result_path)
        _write_json_atomic(state_path, state)
        return result

def run_smoke(args: argparse.Namespace) -> dict:
    upstream_path = Path(args.upstream).resolve()
    upstream = _json_file(upstream_path)
    if upstream.get("pin_status") not in {"source-and-weights-pinned", "training-smoke-verified"}:
        raise DatasetError("upstream model/source pin is not complete")

    source_root = Path(args.paddleocr_root).resolve()
    paddle_info = upstream["paddleocr"]
    config_path = source_root / paddle_info["config_path"]
    dictionary_path = source_root / paddle_info["dictionary_path"]
    _verify_sha(config_path, paddle_info["config_sha256"], "PaddleOCR config")
    _verify_sha(dictionary_path, paddle_info["dictionary_sha256"], "PaddleOCR dictionary")

    weight = Path(args.pretrained_model).resolve()
    if not weight.is_file() or weight.stat().st_size != upstream["pretrained_model_bytes"]:
        raise DatasetError("pretrained model byte count does not match upstream pin")
    _verify_sha(weight, upstream["pretrained_model_sha256"], "pretrained model")

    dataset = load_dataset(args.manifest)
    contract = upstream["model_contract"]
    compatibility = audit_model_compatibility(
        dataset,
        dictionary_path,
        max_text_length=contract["max_text_length"],
        use_space_char=contract["use_space_char"],
    )
    if compatibility["status"] != "ok":
        raise DatasetError("dataset is incompatible with pinned recognizer contract")

    export_dir = Path(args.export_dir).resolve()
    export_meta = _json_file(export_dir / "export.json")
    if export_meta.get("dataset_fingerprint") != dataset.fingerprint:
        raise DatasetError("Paddle export fingerprint does not match dataset")
    if export_meta.get("group_by") != args.expected_group_by:
        raise DatasetError("Paddle export holdout axis does not match requested smoke profile")

    run_dir = Path(args.run_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)
    lock_path = run_dir / ".smoke.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise DatasetError(f"smoke training is already active in {run_dir}") from exc

        result_path = run_dir / "smoke-result.json"
        if result_path.exists():
            result = _json_file(result_path)
            checkpoint = Path(result["checkpoint"])
            _verify_sha(checkpoint, result["checkpoint_sha256"], "smoke checkpoint")
            return result

        model_dir = run_dir / "model"
        labels_dir = run_dir / "labels"
        if model_dir.exists():
            shutil.rmtree(model_dir)
        labels_dir.mkdir(parents=True, exist_ok=True)
        train_labels = labels_dir / "train.txt"
        val_labels = labels_dir / "val.txt"
        _subset_labels(export_dir / "train.txt", train_labels, args.train_samples)
        _subset_labels(export_dir / "val.txt", val_labels, args.val_samples)

        overrides = build_smoke_overrides(
            dataset_root=str(dataset.root),
            train_labels=str(train_labels),
            val_labels=str(val_labels),
            pretrained_model=str(weight),
            output_dir=str(model_dir),
            batch_size=args.batch_size,
        )
        command = [sys.executable, "tools/train.py", "-c", str(config_path), "-o"]
        command.extend(f"{key}={_format_override(value)}" for key, value in overrides.items())
        state = {
            "schema_version": 1,
            "status": "running",
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "upstream_commit": paddle_info["commit"],
            "pretrained_model_sha256": upstream["pretrained_model_sha256"],
            "train_samples": args.train_samples,
            "val_samples": args.val_samples,
            "batch_size": args.batch_size,
            "command": command,
        }
        _write_json_atomic(run_dir / "smoke-state.json", state)

        log_path = run_dir / "train.log"
        with log_path.open("w", encoding="utf-8") as log:
            process = subprocess.Popen(
                command,
                cwd=source_root,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                bufsize=1,
            )
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                log.write(line)
                log.flush()
            return_code = process.wait()
        if return_code != 0:
            state["status"] = "failed"
            state["return_code"] = return_code
            _write_json_atomic(run_dir / "smoke-state.json", state)
            raise DatasetError(f"PaddleOCR smoke training failed with exit code {return_code}")

        checkpoint = model_dir / "latest.pdparams"
        if not checkpoint.is_file():
            raise DatasetError("smoke training completed without latest.pdparams checkpoint")
        result = {
            "schema_version": 1,
            "status": "ok",
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "upstream_commit": paddle_info["commit"],
            "pretrained_model_sha256": upstream["pretrained_model_sha256"],
            "train_samples": args.train_samples,
            "val_samples": args.val_samples,
            "batch_size": args.batch_size,
            "checkpoint": str(checkpoint),
            "checkpoint_sha256": _sha256_file(checkpoint),
            "log": str(log_path),
        }
        _write_json_atomic(result_path, result)
        state["status"] = "completed"
        _write_json_atomic(run_dir / "smoke-state.json", state)
        return result


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-finetune-train")
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe")
    probe.add_argument("--json", action="store_true")

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--upstream", default="/workspace/browser_ocr/finetune/upstream.json")
    smoke.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    smoke.add_argument("--pretrained-model", required=True)
    smoke.add_argument("--manifest", required=True)
    smoke.add_argument("--export-dir", required=True)
    smoke.add_argument("--run-dir", required=True)
    smoke.add_argument("--expected-group-by", default="drug_family")
    smoke.add_argument("--train-samples", type=int, default=128)
    smoke.add_argument("--val-samples", type=int, default=64)
    smoke.add_argument("--batch-size", type=int, default=16)
    smoke.add_argument("--json", action="store_true")

    baseline = subparsers.add_parser("baseline")
    baseline.add_argument("--upstream", default="/workspace/browser_ocr/finetune/upstream.json")
    baseline.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    baseline.add_argument("--pretrained-model", required=True)
    baseline.add_argument("--manifest", required=True)
    baseline.add_argument("--export-dir", required=True)
    baseline.add_argument("--run-dir", required=True)
    baseline.add_argument("--expected-group-by", default="drug_family")
    baseline.add_argument("--epochs", type=int, default=10)
    baseline.add_argument("--batch-size", type=int, default=32)
    baseline.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            result = run_probe()
        elif args.command == "smoke":
            result = run_smoke(args)
        elif args.command == "baseline":
            result = run_baseline(args)
        else:
            raise DatasetError(f"unsupported command: {args.command}")
        if args.json:
            print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        else:
            print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (DatasetError, ValueError, KeyError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
