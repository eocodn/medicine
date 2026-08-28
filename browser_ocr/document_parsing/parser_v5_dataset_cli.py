from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from .parser_v5_observation import ObservationProfile
from .parser_v5_world import ParserWorldProfile


def _emit(value: object, *, compact: bool) -> None:
    print(json.dumps(value, ensure_ascii=False, sort_keys=compact, indent=None if compact else 2))


def _profile_file(path: str | None) -> tuple[ParserWorldProfile, ObservationProfile]:
    if path is None:
        return ParserWorldProfile(), ObservationProfile()
    source = Path(path)
    try:
        value = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not read Parser v5 profile: {source}") from exc
    if not isinstance(value, dict) or set(value) != {"world", "observation"}:
        raise ValueError("Parser v5 profile must contain world and observation objects")
    world = value["world"]
    observation = value["observation"]
    if not isinstance(world, dict) or not isinstance(observation, dict):
        raise ValueError("Parser v5 world and observation profiles must be objects")
    for name in ("medication_count", "distractor_section_count"):
        if name in world:
            world[name] = tuple(world[name])
    if "false_positive_count" in observation:
        observation["false_positive_count"] = tuple(observation["false_positive_count"])
    return ParserWorldProfile(**world), ObservationProfile(**observation)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-parser-v5-data", description="Parser v5 structured dataset controls")
    subparsers = parser.add_subparsers(dest="command", required=True)

    build = subparsers.add_parser("build")
    build.add_argument("--output-dir", required=True)
    build.add_argument("--dataset-id", required=True)
    build.add_argument("--document-count", required=True, type=int)
    build.add_argument("--seed", required=True, type=int)
    build.add_argument("--profile")
    build.add_argument("--json", action="store_true")

    validate = subparsers.add_parser("validate")
    validate.add_argument("--manifest", required=True)
    validate.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "build":
            world, observation = _profile_file(args.profile)
            manifest = build_parser_v5_dataset(
                args.output_dir,
                dataset_id=args.dataset_id,
                document_count=args.document_count,
                seed=args.seed,
                world_profile=world,
                observation_profile=observation,
            )
            dataset = load_parser_v5_dataset(manifest)
            _emit(
                {
                    "status": "ok",
                    "dataset_id": dataset.dataset_id,
                    "documents": len(dataset.samples),
                    "samples_sha256": dataset.samples_sha256,
                    "manifest": str(dataset.manifest_path),
                },
                compact=bool(args.json),
            )
            return 0
        if args.command == "validate":
            dataset = load_parser_v5_dataset(args.manifest)
            _emit(
                {
                    "status": "ok",
                    "dataset_id": dataset.dataset_id,
                    "documents": len(dataset.samples),
                    "samples_sha256": dataset.samples_sha256,
                    "generation": dataset.generation,
                },
                compact=bool(args.json),
            )
            return 0
        raise ValueError(f"unsupported Parser v5 command: {args.command}")
    except ValueError as exc:
        error = {"status": "error", "error": str(exc)}
        if getattr(args, "json", False):
            print(json.dumps(error, ensure_ascii=False, sort_keys=True), file=sys.stderr)
        else:
            print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())