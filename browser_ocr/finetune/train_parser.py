from __future__ import annotations

import argparse


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="ocr-finetune-train")
    subparsers = parser.add_subparsers(dest="command", required=True)
    probe = subparsers.add_parser("probe")
    probe.add_argument("--json", action="store_true")

    prepare_training = subparsers.add_parser("prepare-training-view")
    prepare_training.add_argument("--upstream", default="/workspace/browser_ocr/finetune/upstream.json")
    prepare_training.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    prepare_training.add_argument("--manifest", required=True)
    prepare_training.add_argument("--split", required=True)
    prepare_training.add_argument("--output-dir", required=True)
    prepare_training.add_argument("--json", action="store_true")

    smoke = subparsers.add_parser("smoke")
    smoke.add_argument("--upstream", default="/workspace/browser_ocr/finetune/upstream.json")
    smoke.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
    smoke.add_argument("--pretrained-model", required=True)
    smoke.add_argument("--manifest", required=True)
    smoke.add_argument("--export-dir", required=True)
    smoke.add_argument("--run-dir", required=True)
    smoke.add_argument("--expected-group-by", default="drug_family")
    smoke.add_argument("--train-samples", type=int, default=128)
    smoke.add_argument("--val-samples", type=int, default=64)
    smoke.add_argument("--batch-size", type=int, default=16)
    smoke.add_argument("--json", action="store_true")

    for name in ("v6-preflight", "v6-train"):
        v6 = subparsers.add_parser(name)
        v6.add_argument("--upstream", default="/workspace/browser_ocr/finetune/upstream.json")
        v6.add_argument("--paddleocr-root", default="/opt/PaddleOCR")
        v6.add_argument(
            "--pretrained-model",
            default="/artifacts/ocr/training/sources/korean_PP-OCRv5_mobile_rec_pretrained.pdparams",
        )
        v6.add_argument("--manifest", required=True)
        v6.add_argument("--export-dir", required=True)
        v6.add_argument("--run-dir", required=True)
        v6.add_argument("--epochs", type=int, default=4)
        v6.add_argument("--batch-size", type=int, default=32)
        v6.add_argument("--learning-rate", type=float, default=0.00005)
        v6.add_argument("--warmup-epochs", type=int, default=1)
        v6.add_argument("--num-workers", type=int, default=2)
        v6.add_argument("--json", action="store_true")
    return parser
