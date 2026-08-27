from __future__ import annotations

import argparse
import json
import sys

from .parser_v5_training_paddle import ParserV5TrainingConfig, train_parser_v5


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-parser-v5-train", description="Train Parser v5 structured document model")
    parser.add_argument("--train-manifest", action="append", required=True)
    parser.add_argument("--validation-manifest", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--learning-rate", type=float, default=0.002)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=112)
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = train_parser_v5(
            train_manifests=args.train_manifest,
            validation_manifests=args.validation_manifest,
            output_dir=args.output_dir,
            config=ParserV5TrainingConfig(
                epochs=args.epochs,
                learning_rate=args.learning_rate,
                weight_decay=args.weight_decay,
                seed=args.seed,
                device=args.device,
            ),
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True, indent=None if args.json else 2))
        return 0
    except (OSError, ValueError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())