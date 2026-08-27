from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from .parser_v5_calibration import build_parser_v5_calibration, load_parser_v5_calibration


def _runtime_records(path: str | Path) -> list[dict[str, Any]]:
    source = Path(path).resolve()
    records: list[dict[str, Any]] = []
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ValueError(f"could not read Parser v5 calibration runtime records: {source}") from exc
    for line_number, line in enumerate(lines, start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Parser v5 calibration runtime line {line_number} is invalid JSON") from exc
        if not isinstance(value, dict):
            raise ValueError(f"Parser v5 calibration runtime line {line_number} must be an object")
        records.append(value)
    if not records:
        raise ValueError("Parser v5 calibration runtime records are empty")
    return records


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-v5-calibrate",
        description="Build Parser v5 train-only frozen-runtime OCR calibration",
    )
    parser.add_argument("--dataset-manifest", required=True)
    parser.add_argument("--runtime-records", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        destination = build_parser_v5_calibration(
            dataset_manifest=args.dataset_manifest,
            runtime_records=_runtime_records(args.runtime_records),
            output_path=args.output,
        )
        artifact = load_parser_v5_calibration(destination)
        payload = {
            "status": "ok",
            "output": str(destination.resolve()),
            "dataset_id": artifact["dataset_id"],
            "document_count": artifact["document_count"],
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