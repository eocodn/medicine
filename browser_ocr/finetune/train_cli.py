from __future__ import annotations

import argparse
import fcntl
import json
import shutil
import subprocess
import sys
from pathlib import Path

from .dataset import DatasetError, load_dataset
from .model_compat import audit_model_compatibility
from .recognizer_training import (
    V6RecognizerTrainingConfig,
    prepare_v6_recognizer_training,
    run_v6_recognizer_training,
)
from .runner_io import (
    json_file as _json_file,
    sha256_file as _sha256_file,
    verify_sha as _verify_sha,
    write_json_atomic as _write_json_atomic,
)
from .train_parser import build_parser
from .training import (
    build_smoke_overrides,
    format_paddle_override as _format_override,
    probe_paddle_runtime,
    subset_label_file,
)
from .training_view_runner import run_prepare_training_view


def run_probe() -> dict:
    try:
        import paddle
    except Exception as exc:
        raise DatasetError(f"could not import PaddlePaddle runtime: {exc}") from exc
    return probe_paddle_runtime(paddle)


def _v6_recognizer_kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "upstream_path": args.upstream,
        "paddleocr_root": args.paddleocr_root,
        "pretrained_model": args.pretrained_model,
        "manifest": args.manifest,
        "export_dir": args.export_dir,
        "run_dir": args.run_dir,
        "config": V6RecognizerTrainingConfig(
            epochs=args.epochs,
            batch_size=args.batch_size,
            learning_rate=args.learning_rate,
            warmup_epochs=args.warmup_epochs,
            num_workers=args.num_workers,
        ),
    }


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
        subset_label_file(export_dir / "train.txt", train_labels, args.train_samples)
        subset_label_file(export_dir / "val.txt", val_labels, args.val_samples)

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


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "probe":
            result = run_probe()
        elif args.command == "prepare-training-view":
            result = run_prepare_training_view(args)
        elif args.command == "smoke":
            result = run_smoke(args)
        elif args.command == "v6-preflight":
            result = prepare_v6_recognizer_training(**_v6_recognizer_kwargs(args))
        elif args.command == "v6-train":
            result = run_v6_recognizer_training(**_v6_recognizer_kwargs(args))
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
