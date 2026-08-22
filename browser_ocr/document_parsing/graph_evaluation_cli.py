from __future__ import annotations

import argparse
import json
import sys

from .graph_decode import DecodeConfig
from .graph_evaluation_paddle import run_graph_evaluation


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-eval-model",
        description="Evaluate a trained sparse graph parser on strict val/test parser datasets",
    )
    parser.add_argument("--model-result", required=True)
    parser.add_argument("--dataset-manifest", action="append", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--product-threshold", type=float, default=0.75)
    parser.add_argument("--product-margin", type=float, default=0.18)
    parser.add_argument("--field-threshold", type=float, default=0.62)
    parser.add_argument("--field-margin", type=float, default=0.10)
    parser.add_argument("--relation-threshold", type=float, default=0.72)
    parser.add_argument("--relation-margin", type=float, default=0.12)
    parser.add_argument("--device", choices=("gpu", "cpu"), default="gpu")
    parser.add_argument(
        "--allow-test",
        action="store_true",
        help="explicitly unlock test-split evaluation after candidate freeze",
    )
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = DecodeConfig(
            product_threshold=args.product_threshold,
            product_margin=args.product_margin,
            field_threshold=args.field_threshold,
            field_margin=args.field_margin,
            relation_threshold=args.relation_threshold,
            relation_margin=args.relation_margin,
        )
        result = run_graph_evaluation(
            model_result=args.model_result,
            dataset_manifests=args.dataset_manifest,
            output_dir=args.output_dir,
            config=config,
            device=args.device,
            allow_test=args.allow_test,
        )
        print(json.dumps(result, ensure_ascii=False, sort_keys=bool(args.json), indent=None if args.json else 2))
        return 0
    except (OSError, ValueError, RuntimeError) as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]