from __future__ import annotations

import argparse
import json
import sys

from .parser_v51_training_paddle import ParserV51TrainingConfig, train_parser_v51


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-v51-train",
        description="Train Parser v5.1 direct medication-row decoder",
    )
    parser.add_argument("--train-manifest", action="append", required=True)
    parser.add_argument("--validation-manifest", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--epochs", type=int, default=8)
    parser.add_argument("--learning-rate", type=float, default=0.001)
    parser.add_argument("--weight-decay", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=62451)
    parser.add_argument("--device", choices=("cpu", "gpu"), default="gpu")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = train_parser_v51(
            train_manifests=args.train_manifest,
            validation_manifests=args.validation_manifest,
            output_dir=args.output_dir,
            config=ParserV51TrainingConfig(
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