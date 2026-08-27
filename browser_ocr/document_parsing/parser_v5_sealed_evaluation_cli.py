from __future__ import annotations

import argparse
import json
from typing import Sequence

from .parser_v5_sealed_evaluation_paddle import evaluate_parser_v5_sealed_holdout


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-parser-v5-sealed-eval")
    parser.add_argument("--candidate-freeze", required=True)
    parser.add_argument("--training-result", required=True)
    parser.add_argument("--holdout-envelope", required=True)
    parser.add_argument("--open-record", required=True)
    parser.add_argument("--holdout-manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = evaluate_parser_v5_sealed_holdout(
            candidate_freeze=args.candidate_freeze,
            training_result=args.training_result,
            holdout_envelope=args.holdout_envelope,
            open_record=args.open_record,
            holdout_manifest=args.holdout_manifest,
            output_path=args.output,
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
            return 2
        raise
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"sealed holdout {result['holdout_id']}: {result['documents']} documents")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]