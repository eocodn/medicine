from __future__ import annotations

import argparse
import json
import sys

from .candidate_convert import (
    CandidateConversionError,
    prepare_candidate_conversion,
    run_candidate_conversion,
)


def _add_common(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--stage-manifest", required=True)
    parser.add_argument("--output-dir", required=True)
    parser.add_argument("--json", action="store_true")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-detector-convert",
        description="Convert a hash-bound Paddle detector stage to a parity-verified ONNX candidate",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    _add_common(subparsers.add_parser("preflight"))
    _add_common(subparsers.add_parser("convert"))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    kwargs = {"stage_manifest": args.stage_manifest, "output_dir": args.output_dir}
    try:
        if args.command == "preflight":
            result = prepare_candidate_conversion(**kwargs)
        elif args.command == "convert":
            result = run_candidate_conversion(**kwargs)
        else:  # pragma: no cover - argparse owns the command domain.
            raise CandidateConversionError(f"unsupported command: {args.command}")
        print(
            json.dumps(result, ensure_ascii=False, sort_keys=True)
            if args.json
            else json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True)
        )
        return 0
    except (CandidateConversionError, OSError, ValueError, KeyError) as exc:
        if getattr(args, "json", False):
            print(json.dumps({"status": "error", "error": str(exc)}, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())