from __future__ import annotations

import argparse
import json
import sys

from .graph_export_paddle import export_graph_model


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-export-model",
        description="Export a completed sparse graph parser checkpoint as a verified ONNX artifact",
    )
    parser.add_argument("--model-result", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = export_graph_model(model_result=args.model_result, output_dir=args.output_dir)
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