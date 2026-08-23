from __future__ import annotations

import argparse
import json
import sys

from .export_stage import DetectorExportError, prepare_paddle_export, run_paddle_export


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--training-result", required=True)
    parser.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-detector-export-paddle",
        description="Hash-bound Paddle inference export for a completed detector training result",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_common(subparsers.add_parser("preflight"))
    _add_common(subparsers.add_parser("export"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {
        "training_result": args.training_result,
        "paddleocr_root": args.paddleocr_root,
        "output_dir": args.output_dir,
    }
    try:
        if args.command == "preflight":
            result = prepare_paddle_export(**kwargs)
        elif args.command == "export":
            result = run_paddle_export(**kwargs)
        else:  # pragma: no cover - argparse owns the command domain.
            raise DetectorExportError(f"unsupported command: {args.command}")
        print(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
            if args.json
            else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return 0
    except (DetectorExportError, OSError, ValueError, KeyError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())