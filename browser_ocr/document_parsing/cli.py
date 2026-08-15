from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .baseline import run_baseline
from .contract import CorpusError, load_corpus
from .evaluation import evaluate_corpus


def _emit(value: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _load_json(path: str | Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"could not read JSON file {path}: {exc}") from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-document-parse",
        description="Medication-document parsing research controls",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate-corpus")
    validate.add_argument("--corpus", required=True)
    validate.add_argument("--json", action="store_true")

    evaluate = subparsers.add_parser("evaluate")
    evaluate.add_argument("--corpus", required=True)
    evaluate.add_argument("--predictions", required=True)
    evaluate.add_argument("--json", action="store_true")

    baseline = subparsers.add_parser("run-baseline")
    baseline.add_argument("--corpus", required=True)
    baseline.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_output = bool(args.json)
    try:
        corpus = load_corpus(args.corpus)
        if args.command == "validate-corpus":
            _emit(
                {
                    "status": "ok",
                    "schema_version": corpus.schema_version,
                    "case_count": len(corpus.cases),
                    "box_count": sum(len(case.boxes) for case in corpus.cases),
                    "expected_row_count": sum(len(case.expected_rows) for case in corpus.cases),
                    "scenario_tags": sorted({tag for case in corpus.cases for tag in case.scenario_tags}),
                    "risk_tags": sorted({tag for case in corpus.cases for tag in case.risk_tags}),
                },
                json_output,
            )
            return 0
        if args.command == "evaluate":
            result = evaluate_corpus(corpus, _load_json(args.predictions))
            _emit(result, json_output)
            return 0 if result["safety_pass"] else 3
        if args.command == "run-baseline":
            result = run_baseline(corpus)
            _emit(result, json_output)
            return 0 if result["evaluation"]["safety_pass"] else 3
        raise CorpusError(f"unsupported command: {args.command}")
    except CorpusError as exc:
        error = {"status": "error", "error": str(exc)}
        if json_output:
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())