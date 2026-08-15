from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .learned_experiment import learned_rows_for_result, run_benchmark
from .learned_layout import load_model


def _load_json(path: str | Path) -> object:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read JSON file {path}: {exc}") from exc


def _emit(value: object, json_output: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=json_output, indent=None if json_output else 2))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ocr-document-learned",
        description="Learned medication-document layout research controls",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    benchmark = subparsers.add_parser("benchmark")
    benchmark.add_argument("--corpus", required=True)
    benchmark.add_argument("--results-root", required=True)
    benchmark.add_argument("--output-dir", required=True)
    benchmark.add_argument("--epochs", type=int, default=60)
    benchmark.add_argument("--seed", type=int, default=112)
    benchmark.add_argument("--semantic-samples")
    benchmark.add_argument("--semantic-per-role", type=int, default=2500)
    benchmark.add_argument("--semantic-epochs", type=int, default=12)
    benchmark.add_argument("--context-train-corpus")
    benchmark.add_argument("--skip-cross-validation", action="store_true")
    benchmark.add_argument("--json", action="store_true")

    predict = subparsers.add_parser("predict-result")
    predict.add_argument("--corpus", required=True)
    predict.add_argument("--sample-id", required=True)
    predict.add_argument("--result", required=True)
    predict.add_argument("--model", required=True)
    predict.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_output = bool(args.json)
    try:
        if args.command == "benchmark":
            report = run_benchmark(
                corpus_path=args.corpus,
                results_root=args.results_root,
                output_dir=args.output_dir,
                epochs=args.epochs,
                seed=args.seed,
                semantic_samples_path=args.semantic_samples,
                semantic_per_role=args.semantic_per_role,
                semantic_epochs=args.semantic_epochs,
                context_train_corpus_path=args.context_train_corpus,
                run_cross_validation=not args.skip_cross_validation,
            )
            _emit(report, json_output)
            return 0

        if args.command == "predict-result":
            corpus = _load_json(args.corpus)
            if not isinstance(corpus, dict) or not isinstance(corpus.get("samples"), list):
                raise ValueError("detection corpus must contain samples")
            sample = next((item for item in corpus["samples"] if item.get("id") == args.sample_id), None)
            if sample is None:
                raise ValueError(f"unknown sample_id: {args.sample_id}")
            result = _load_json(args.result)
            if not isinstance(result, dict):
                raise ValueError("full-document result must be an object")
            model = load_model(args.model)
            rows = learned_rows_for_result(sample, result, model)
            _emit(
                {
                    "status": "ok",
                    "sample_id": args.sample_id,
                    "model_id": model["model_id"],
                    "rows": rows,
                },
                json_output,
            )
            return 0

        raise ValueError(f"unsupported command: {args.command}")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        payload = {"status": "error", "error": str(exc)}
        if json_output:
            print(json.dumps(payload, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())