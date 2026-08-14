from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .dataset import DatasetError, build_split, dataset_stats, export_paddle, load_dataset


def _emit(value: object, json_output: bool) -> None:
    if json_output:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    else:
        print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def _json_file(path: str | Path) -> dict:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise DatasetError(f"could not read JSON file {path}: {exc}") from exc
    if not isinstance(value, dict):
        raise DatasetError(f"JSON file must contain an object: {path}")
    return value


def _write_json(path: str | Path, value: object) -> None:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    temporary.replace(target)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-finetune", description="Medicine OCR fine-tuning dataset tools")
    subparsers = parser.add_subparsers(dest="command", required=True)

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--json", action="store_true")

    stats = subparsers.add_parser("stats")
    stats.add_argument("--manifest", required=True)
    stats.add_argument("--json", action="store_true")

    split = subparsers.add_parser("split")
    split.add_argument("--manifest", required=True)
    split.add_argument("--group-by", required=True, choices=["layout_family", "source_family", "drug_family"])
    split.add_argument("--seed", type=int, default=112)
    split.add_argument("--train-ratio", type=float, default=0.8)
    split.add_argument("--val-ratio", type=float, default=0.1)
    split.add_argument("--test-ratio", type=float, default=0.1)
    split.add_argument("--output", required=True)
    split.add_argument("--json", action="store_true")

    export = subparsers.add_parser("export-paddle")
    export.add_argument("--manifest", required=True)
    export.add_argument("--split", required=True)
    export.add_argument("--output-dir", required=True)
    export.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    json_output = bool(getattr(args, "json", False))
    try:
        def load_progress(done: int, total: int) -> None:
            print(f"[ocr-finetune] validate {done}/{total}", file=sys.stderr)

        dataset = load_dataset(args.manifest, progress=load_progress)
        if args.command == "validate":
            _emit({"status": "ok", **dataset_stats(dataset)}, json_output)
            return 0
        if args.command == "stats":
            _emit(dataset_stats(dataset), json_output)
            return 0
        if args.command == "split":
            result = build_split(
                dataset,
                group_by=args.group_by,
                seed=args.seed,
                ratios=(args.train_ratio, args.val_ratio, args.test_ratio),
            )
            _write_json(args.output, result)
            _emit(result, json_output)
            return 0
        if args.command == "export-paddle":
            split = _json_file(args.split)

            def progress(done: int, total: int) -> None:
                print(f"[ocr-finetune] export {done}/{total}", file=sys.stderr)

            result = export_paddle(dataset, split, args.output_dir, progress=progress)
            _emit(result, json_output)
            return 0
        raise DatasetError(f"unsupported command: {args.command}")
    except DatasetError as exc:
        error = {"status": "error", "error": str(exc)}
        if json_output:
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
