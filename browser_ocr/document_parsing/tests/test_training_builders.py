from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.training_builders import (
    build_runtime_dataset,
    build_synthetic_dataset,
)
from browser_ocr.document_parsing.training_dataset import ParserDatasetError, load_parser_dataset


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _runtime_profile() -> dict:
    return {
        "schema_version": 2,
        "image_sha256": "a" * 64,
        "baseline_result_sha256": "1" * 64,
        "recognizer_checkpoint_sha256": "b" * 64,
        "recognizer_config_sha256": "2" * 64,
        "recognizer_device": "cpu",
        "detector_manifest_sha256": "3" * 64,
        "detector_model": "PP-OCRv5_mobile_det",
        "detector_edge": 640,
        "detector_threads": 1,
        "detector_asset_sha256": "4" * 64,
        "detector_onnx_sha256": "e" * 64,
        "detector_config_sha256": "f" * 64,
        "inference_runtime_sha256": "0" * 64,
        "paddleocr_source_sha256": "e" * 64,
        "paddleocr_dictionary_sha256": "f" * 64,
        "parser": "must-not-enter-observation-profile",
        "implementation": {
            "full_document": "5" * 64,
            "full_document_cli": "6" * 64,
            "crop_refinement": "d" * 64,
            "parser": "c" * 64,
            "parser_contract": "7" * 64,
            "detector_runtime": "8" * 64,
            "detector_benchmark": "9" * 64,
        },
    }


def _truth_sample() -> dict:
    return {
        "document_id": "synthetic-000001",
        "split": "val",
        "image_sha256": "a" * 64,
        "width": 1280,
        "height": 1600,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription", "table"],
        "risk_tags": ["row_association"],
        "nodes": [
            {"node_id": "p", "text": "가나다정", "confidence": 1.0, "polygon": _poly(5, 5, 100, 34), "natural_text_polygon": _poly(10, 10), "semantic_role": "product", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "d", "text": "1정", "confidence": 1.0, "polygon": _poly(120, 10), "semantic_role": "dose", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "f", "text": "3회", "confidence": 1.0, "polygon": _poly(220, 10), "semantic_role": "frequency", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "t", "text": "5일", "confidence": 1.0, "polygon": _poly(320, 10), "semantic_role": "duration", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "h", "text": "복용안내", "confidence": 1.0, "polygon": _poly(10, 80), "semantic_role": "header", "association_group": "document", "region_class": "context", "critical": False},
        ],
        "positive_edges": [
            {"product_node_id": "p", "field_node_id": "d", "relation": "same_medication"},
            {"product_node_id": "p", "field_node_id": "f", "relation": "same_medication"},
            {"product_node_id": "p", "field_node_id": "t", "relation": "same_medication"},
        ],
        "expected_rows": [
            {
                "row_id": "p",
                "product_query": "가나다정",
                "draft": {"dose_amount": 1, "dose_unit": "tablet", "frequency_per_day": 3, "prescription_days": 5},
                "uncertainty_codes": [],
                "evidence": {
                    "product_query": ["p"],
                    "dose_amount": ["d"],
                    "dose_unit": ["d"],
                    "frequency_per_day": ["f"],
                    "prescription_days": ["t"],
                },
            }
        ],
    }


def _write_truth(root: Path) -> Path:
    path = root / "truth.jsonl"
    path.write_text(json.dumps(_truth_sample(), ensure_ascii=False) + "\n", encoding="utf-8")
    return path


class ParserTrainingBuildersTest(unittest.TestCase):
    def test_oracle_builder_preserves_split_and_gold(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = build_synthetic_dataset(
                truth_samples_path=_write_truth(root),
                output_dir=root / "oracle",
                dataset_id="oracle-fixture",
                observation_kind="oracle",
                split="val",
                seed=17,
            )
            dataset = load_parser_dataset(manifest)
            self.assertEqual(len(dataset.documents), 1)
            document = dataset.documents[0]
            self.assertEqual(document["split"], "val")
            self.assertEqual(document["gold_rows"][0]["gold_row_id"], "m1")
            self.assertEqual(document["observation"]["kind"], "oracle")
            self.assertEqual(document["observation"]["nodes"][0]["polygon"], _poly(10, 10))
            self.assertEqual(sum(r["label"] == "same_medication" for r in document["relations"]), 3)

    def test_synthetic_ocr_builder_is_deterministic_and_marks_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            truth = _write_truth(root)
            first = build_synthetic_dataset(
                truth_samples_path=truth,
                output_dir=root / "a",
                dataset_id="synth-a",
                observation_kind="synthetic_ocr",
                split="val",
                seed=991,
            )
            second = build_synthetic_dataset(
                truth_samples_path=truth,
                output_dir=root / "b",
                dataset_id="synth-b",
                observation_kind="synthetic_ocr",
                split="val",
                seed=991,
            )
            a = load_parser_dataset(first).documents[0]
            b = load_parser_dataset(second).documents[0]
            self.assertEqual(a["observation"], b["observation"])
            self.assertEqual(a["relations"], b["relations"])
            self.assertEqual(a["observation"]["profile"]["seed"], 991)

    def test_runtime_builder_aligns_actual_ocr_nodes_to_truth(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            truth = _write_truth(root)
            result_root = root / "results" / "synthetic-000001"
            result_root.mkdir(parents=True)
            result_root.joinpath("result.json").write_text(json.dumps({
                "status": "ok",
                "profile": _runtime_profile(),
                "image": {"sha256": "a" * 64, "width": 1280, "height": 1600},
                "regions": [
                    {"index": 1, "text": "가나다정", "recognition_score": 0.99, "polygon": _poly(10, 10)},
                    {"index": 2, "text": "1정", "recognition_score": 0.98, "polygon": _poly(120, 10)},
                ],
            }), encoding="utf-8")
            manifest = build_runtime_dataset(
                truth_samples_path=truth,
                results_root=root / "results",
                output_dir=root / "runtime",
                dataset_id="runtime-fixture",
                split="val",
            )
            document = load_parser_dataset(manifest).documents[0]
            self.assertEqual(document["observation"]["kind"], "runtime_ocr")
            self.assertEqual(document["source_binding"], {
                "kind": "synthetic_truth",
                "truth_samples_sha256": hashlib.sha256(truth.read_bytes()).hexdigest(),
            })
            self.assertEqual(document["observation"]["nodes"][0]["semantic_role"], "product")
            self.assertNotIn("parser", document["observation"]["profile"])
            self.assertNotIn("parser", document["observation"]["profile"]["implementation"])

    def test_runtime_builder_rejects_unpinned_ocr_profile(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            truth = _write_truth(root)
            result_root = root / "results" / "synthetic-000001"
            result_root.mkdir(parents=True)
            result_root.joinpath("result.json").write_text(json.dumps({
                "status": "ok",
                "profile": {},
                "image": {"sha256": "a" * 64, "width": 1280, "height": 1600},
                "regions": [],
            }), encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "runtime.*profile"):
                build_runtime_dataset(
                    truth_samples_path=truth,
                    results_root=root / "results",
                    output_dir=root / "runtime",
                    dataset_id="runtime-unpinned",
                    split="val",
                )

    def test_runtime_builder_rejects_mixed_ocr_producers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = _truth_sample()
            second = json.loads(json.dumps(first))
            second["document_id"] = "synthetic-000002"
            second["image_sha256"] = "b" * 64
            truth = root / "truth.jsonl"
            truth.write_text(
                json.dumps(first, ensure_ascii=False) + "\n" + json.dumps(second, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            for sample, checkpoint in ((first, "b"), (second, "e")):
                result_root = root / "results" / sample["document_id"]
                result_root.mkdir(parents=True)
                profile = _runtime_profile()
                profile["image_sha256"] = sample["image_sha256"]
                profile["recognizer_checkpoint_sha256"] = checkpoint * 64
                result_root.joinpath("result.json").write_text(json.dumps({
                    "status": "ok",
                    "profile": profile,
                    "image": {"sha256": sample["image_sha256"], "width": 1280, "height": 1600},
                    "regions": [],
                }), encoding="utf-8")

            with self.assertRaisesRegex(ParserDatasetError, "mix different runtime OCR producers"):
                build_runtime_dataset(
                    truth_samples_path=truth,
                    results_root=root / "results",
                    output_dir=root / "runtime",
                    dataset_id="runtime-mixed",
                    split="val",
                )


if __name__ == "__main__":
    unittest.main()
