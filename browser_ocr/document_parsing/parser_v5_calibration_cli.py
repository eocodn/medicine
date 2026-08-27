from __future__ import annotations

import argparse
import json
import sys

from .parser_v5_calibration import build_parser_v5_calibration, load_parser_v5_calibration


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-v5-calibrate",
        description="Build Parser v5 train-only frozen-runtime OCR calibration",
    )
    parser.add_argument("--oracle-manifest", required=True)
    parser.add_argument("--runtime-manifest", required=True)
    parser.add_argument("--runtime-batch-result", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        destination = build_parser_v5_calibration(
            oracle_manifest=args.oracle_manifest,
            runtime_manifest=args.runtime_manifest,
            runtime_batch_result=args.runtime_batch_result,
            output_path=args.output,
        )
        artifact = load_parser_v5_calibration(destination)
        payload = {
            "status": "ok",
            "output": str(destination.resolve()),
            "document_count": artifact["document_count"],
            "source_fingerprint": artifact["source_fingerprint"],
            "producer_fingerprint": artifact["producer_fingerprint"],
            "calibration_fingerprint": artifact["calibration_fingerprint"],
        }
        print(json.dumps(payload, ensure_ascii=False, sort_keys=bool(args.json), indent=None if args.json else 2))
        return 0
    except ValueError as exc:
        payload = {"status": "error", "error": str(exc)}
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]