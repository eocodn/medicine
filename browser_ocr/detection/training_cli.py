from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .training import (
    DetectorTrainingConfig,
    DetectorTrainingError,
    prepare_detector_training,
    run_detector_training,
)


HERE = Path(__file__).resolve().parent


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--upstream", default=str(HERE / "training-upstream.json"))
    parser.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    parser.add_argument(
        "--pretrained-model",
        default="/artifacts/ocr/training/sources/PPLCNetV3_x0_75_ocr_det.pdparams",
    )
    parser.add_argument("--corpus-manifest", required=True)
    parser.add_argument("--detection-export", required=True)
    parser.add_argument("--run-dir", required=True)
    parser.add_argument("--epochs", type=int, default=6)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=0.0001)
    parser.add_argument("--warmup-epochs", type=int, default=1)
    parser.add_argument("--num-workers", type=int, default=2)
    parser.add_argument("--eval-batch-step", type=int, default=10)
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-detector-train",
        description="Strict PP-OCRv5 mobile detector fine-tune preflight/training runner",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_common(subparsers.add_parser("preflight"))
    _add_common(subparsers.add_parser("train"))
    return parser


def _config(args: argparse.Namespace) -> DetectorTrainingConfig:
    return DetectorTrainingConfig(
        epochs=args.epochs,
        batch_size=args.batch_size,
        learning_rate=args.learning_rate,
        warmup_epochs=args.warmup_epochs,
        num_workers=args.num_workers,
        eval_batch_step=args.eval_batch_step,
    )


def _kwargs(args: argparse.Namespace) -> dict[str, object]:
    return {
        "upstream_path": args.upstream,
        "paddleocr_root": args.paddleocr_root,
        "pretrained_model": args.pretrained_model,
        "corpus_manifest": args.corpus_manifest,
        "detection_export": args.detection_export,
        "run_dir": args.run_dir,
        "config": _config(args),
    }


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "preflight":
            result = prepare_detector_training(**_kwargs(args))
        elif args.command == "train":
            result = run_detector_training(**_kwargs(args))
        else:  # pragma: no cover - argparse owns the command domain.
            raise DetectorTrainingError(f"unsupported command: {args.command}")
        print(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
            if args.json
            else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return 0
    except (DetectorTrainingError, OSError, ValueError, KeyError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())