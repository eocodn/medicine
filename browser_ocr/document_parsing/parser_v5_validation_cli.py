from __future__ import annotations

import argparse
import json
import sys

from .parser_v5_validation_protocol import (
    authorize_parser_v5_holdout_open,
    freeze_parser_v5_candidate,
    load_parser_v5_candidate_freeze,
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-parser-v5-validate",
        description="Parser v5 candidate-freeze and sealed-holdout validation controls",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    freeze = subparsers.add_parser("freeze")
    freeze.add_argument("--training-result", required=True)
    freeze.add_argument("--development-manifest", action="append", required=True)
    freeze.add_argument("--calibration-artifact", required=True)
    freeze.add_argument("--output", required=True)
    freeze.add_argument("--json", action="store_true")

    opened = subparsers.add_parser("open")
    opened.add_argument("--candidate-freeze", required=True)
    opened.add_argument("--holdout-envelope", required=True)
    opened.add_argument("--open-record", required=True)
    opened.add_argument("--unlock-holdout-id", required=True)
    opened.add_argument("--json", action="store_true")
    return parser


def _emit(value: object, *, json_output: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=json_output, indent=None if json_output else 2))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_output = bool(args.json)
    try:
        if args.command == "freeze":
            path = freeze_parser_v5_candidate(
                training_result=args.training_result,
                development_manifests=args.development_manifest,
                calibration_artifact=args.calibration_artifact,
                output_path=args.output,
            )
            freeze = load_parser_v5_candidate_freeze(path)
            _emit(
                {
                    "status": "ok",
                    "output": str(path),
                    "freeze_fingerprint": freeze["freeze_fingerprint"],
                    "checkpoint_sha256": freeze["checkpoint_sha256"],
                    "development_view_count": len(freeze["development_views"]),
                },
                json_output=json_output,
            )
            return 0
        if args.command == "open":
            record = authorize_parser_v5_holdout_open(
                candidate_freeze=args.candidate_freeze,
                holdout_envelope=args.holdout_envelope,
                open_record=args.open_record,
                unlock_holdout_id=args.unlock_holdout_id,
            )
            _emit({"status": "ok", **record}, json_output=json_output)
            return 0
        raise ValueError(f"unsupported Parser v5 validation command: {args.command}")
    except ValueError as exc:
        payload = {"status": "error", "error": str(exc)}
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["build_parser", "main"]