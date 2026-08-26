from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .dataset import DatasetError
from .runner_io import json_file, verify_sha
from .training_view import prepare_unified_training_view


def run_prepare_training_view(args: argparse.Namespace) -> dict:
    upstream = json_file(Path(args.upstream).resolve())
    if upstream.get("pin_status") != "training-smoke-verified" or not upstream.get("training_enabled"):
        raise DatasetError("training runtime has not passed the pinned smoke gate")
    source_root = Path(args.paddleocr_root).resolve()
    paddle_info = upstream["paddleocr"]
    dictionary = source_root / paddle_info["dictionary_path"]
    verify_sha(dictionary, paddle_info["dictionary_sha256"], "PaddleOCR dictionary")
    contract = upstream["model_contract"]

    def progress(done: int, total: int) -> None:
        print(f"[ocr-finetune] training-view {done}/{total}", file=sys.stderr, flush=True)

    return prepare_unified_training_view(
        manifest_path=args.manifest,
        split_path=args.split,
        output_dir=args.output_dir,
        dictionary_path=dictionary,
        dictionary_sha256=paddle_info["dictionary_sha256"],
        max_text_length=contract["max_text_length"],
        use_space_char=contract["use_space_char"],
        progress=progress,
    )
