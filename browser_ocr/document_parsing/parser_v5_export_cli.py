from __future__ import annotations

import argparse
import json
from typing import Sequence

from .parser_v5_export_onnx import export_parser_v5_candidate


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-parser-v5-export")
    parser.add_argument("--candidate-freeze", required=True)
    parser.add_argument("--training-result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_parser_v5_candidate(
            candidate_freeze=args.candidate_freeze,
            training_result=args.training_result,
            output_dir=args.output_dir,
        )
    except ValueError as exc:
        if args.json:
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True))
            return 2
        raise
    if args.json:
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    else:
        print(f"Parser v5 ONNX: {result['model_file']} {result['model_sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]