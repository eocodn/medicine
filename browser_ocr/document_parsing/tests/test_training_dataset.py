from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.training_dataset import (
    ParserDatasetError,
    load_parser_dataset,
    write_parser_dataset,
)


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _doc(*, source_kind: str = "synthetic", split: str = "train") -> dict:
    return {
        "document_id": "doc-001",
        "split": split,
        "source_kind": source_kind,
        "image_sha256": "a" * 64,
        "width": 1280,
        "height": 1600,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription", "table"],
        "risk_tags": ["row_association"],
        "privacy": {
            "contains_patient_data": False,
            "deidentified": source_kind == "real_deidentified",
        },
        "observation": {
            "kind": "oracle",
            "profile": {"producer": "fixture"},
            "nodes": [
                {
                    "node_id": "n-product",
                    "text": "가나다정",
                    "confidence": 1.0,
                    "polygon": _poly(10, 10),
                    "target_region_ids": ["gt-product"],
                    "label_status": "labeled",
                    "semantic_role": "product",
                    "association_group": "med-1",
                },
                {
                    "node_id": "n-dose",
                    "text": "1정",
                    "confidence": 1.0,
                    "polygon": _poly(120, 10),
                    "target_region_ids": ["gt-dose"],
                    "label_status": "labeled",
                    "semantic_role": "dose",
                    "association_group": "med-1",
                },
            ],
        },
        "relations": [
            {
                "product_node_id": "n-product",
                "field_node_id": "n-dose",
                "label": "same_medication",
            }
        ],
        "gold_rows": [
            {
                "gold_row_id": "med-1",
                "product_query": "가나다정",
                "draft": {"dose_amount": 1, "dose_unit": "tablet"},
            }
        ],
    }


class ParserTrainingDatasetContractTest(unittest.TestCase):
    def test_round_trips_strict_document_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = write_parser_dataset(
                root,
                dataset_id="parser-fixture-v1",
                documents=[_doc()],
                metadata={"source": "unit-test"},
            )
            dataset = load_parser_dataset(manifest)
            self.assertEqual(dataset.dataset_id, "parser-fixture-v1")
            self.assertEqual(len(dataset.documents), 1)
            self.assertEqual(dataset.documents[0]["observation"]["nodes"][0]["semantic_role"], "product")
            self.assertTrue(dataset.fingerprint)

    def test_real_deidentified_is_holdout_only(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "real_deidentified.*train"):
                write_parser_dataset(root, dataset_id="bad-real-train", documents=[_doc(source_kind="real_deidentified", split="train")])

    def test_real_requires_explicit_privacy_and_runtime_observation(self) -> None:
        document = _doc(source_kind="real_deidentified", split="val")
        document["privacy"]["deidentified"] = False
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "deidentified"):
                write_parser_dataset(Path(raw), dataset_id="bad-real-privacy", documents=[document])

        document = _doc(source_kind="real_deidentified", split="val")
        document["observation"]["kind"] = "oracle"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "runtime_ocr"):
                write_parser_dataset(Path(raw), dataset_id="bad-real-observation", documents=[document])

    def test_ambiguous_nodes_must_not_carry_role_or_group_labels(self) -> None:
        document = _doc()
        node = document["observation"]["nodes"][0]
        node["label_status"] = "ambiguous"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "ambiguous"):
                write_parser_dataset(Path(raw), dataset_id="bad-ambiguous", documents=[document])

    def test_manifest_binds_sample_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(root, dataset_id="hash-bound", documents=[_doc()])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            samples_path = root / manifest["samples_file"]
            samples_path.write_text(samples_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "SHA-256"):
                load_parser_dataset(manifest_path)

    def test_completed_dataset_is_idempotent_and_rejects_profile_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = write_parser_dataset(
                root,
                dataset_id="stable-output",
                documents=[_doc()],
                metadata={"seed": 1},
            )
            first_bytes = first.read_bytes()
            second = write_parser_dataset(
                root,
                dataset_id="stable-output",
                documents=[_doc()],
                metadata={"seed": 1},
            )
            self.assertEqual(second, first)
            self.assertEqual(first.read_bytes(), first_bytes)

            changed = _doc()
            changed["observation"]["nodes"][0]["text"] = "다른약정"
            with self.assertRaisesRegex(ParserDatasetError, "profile differs"):
                write_parser_dataset(
                    root,
                    dataset_id="stable-output",
                    documents=[changed],
                    metadata={"seed": 2},
                )


if __name__ == "__main__":
    unittest.main()
