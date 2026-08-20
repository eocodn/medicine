from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.training_builders import (
    build_runtime_dataset,
    build_synthetic_dataset,
)
from browser_ocr.document_parsing.training_dataset import load_parser_dataset


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


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
            {"node_id": "p", "text": "가나다정", "confidence": 1.0, "polygon": _poly(10, 10), "semantic_role": "product", "association_group": "m1", "region_class": "medication", "critical": True},
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
                "profile": {
                    "schema_version": 2,
                    "recognizer_checkpoint_sha256": "b" * 64,
                    "detector_model": "PP-OCRv5_mobile_det",
                    "parser": "must-not-enter-observation-profile",
                    "implementation": {"parser": "c" * 64, "crop_refinement": "d" * 64},
                },
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
            self.assertEqual(document["observation"]["nodes"][0]["semantic_role"], "product")
            self.assertNotIn("parser", document["observation"]["profile"])
            self.assertNotIn("parser", document["observation"]["profile"]["implementation"])


if __name__ == "__main__":
    unittest.main()
