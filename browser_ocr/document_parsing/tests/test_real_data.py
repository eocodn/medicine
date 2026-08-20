from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.real_data import (
    ParserDatasetError,
    finalize_real_annotation,
    load_real_source_manifest,
    prepare_real_annotation,
)


def _write_image(path: Path) -> str:
    content = b"\xff\xd8\xfffixture-deidentified-photo"
    path.write_bytes(content)
    return hashlib.sha256(content).hexdigest()


class RealParserDataTest(unittest.TestCase):
    def _manifest(self, root: Path, *, split: str = "val", deidentified: bool = True) -> Path:
        image = root / "rx-001.jpg"
        digest = _write_image(image)
        samples = root / "samples.jsonl"
        samples.write_text(json.dumps({
            "document_id": "rx-001",
            "image": "rx-001.jpg",
            "image_sha256": digest,
            "split": split,
            "document_type": "prescription",
            "layout_family": "real_unknown",
            "privacy": {"contains_patient_data": False, "deidentified": deidentified},
            "provenance": {"source_id": "clinic-batch-a", "license_id": "private-deidentified"},
            "scenario_tags": ["prescription"],
            "risk_tags": ["real_photo"],
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": "real-rx-fixture",
            "source_kind": "real_deidentified",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
        }), encoding="utf-8")
        return manifest

    def test_real_source_manifest_rejects_train_and_non_deidentified_images(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "holdout"):
                load_real_source_manifest(self._manifest(root, split="train"))
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "deidentified"):
                load_real_source_manifest(self._manifest(root, deidentified=False))

    def test_real_source_requires_pseudonymous_image_filename(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            image = root / "patient-name.jpg"
            digest = _write_image(image)
            samples = root / "samples.jsonl"
            samples.write_text(json.dumps({
                "document_id": "rx-001",
                "image": "patient-name.jpg",
                "image_sha256": digest,
                "split": "val",
                "document_type": "prescription",
                "layout_family": "real_unknown",
                "privacy": {"contains_patient_data": False, "deidentified": True},
                "provenance": {"source_id": "clinic-batch-a", "license_id": "private-deidentified"},
                "scenario_tags": ["prescription"],
                "risk_tags": ["real_photo"],
            }, ensure_ascii=False) + "\n", encoding="utf-8")
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "dataset_id": "real-rx-fixture",
                "source_kind": "real_deidentified",
                "patient_data_policy": "forbid",
                "samples_file": "samples.jsonl",
            }), encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "pseudonymous document_id"):
                load_real_source_manifest(manifest)

    def test_prepare_then_finalize_annotation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = load_real_source_manifest(self._manifest(root))
            sample = source.samples[0]
            runtime_result = {
                "status": "ok",
                "profile": {
                    "schema_version": 2,
                    "recognizer_checkpoint_sha256": "b" * 64,
                    "parser": "geometry_rule_v2",
                    "implementation": {"parser": "c" * 64, "crop_refinement": "d" * 64},
                },
                "image": {"sha256": sample["image_sha256"], "width": 1200, "height": 1600},
                "regions": [
                    {"index": 1, "text": "가나다정", "recognition_score": 0.98, "polygon": [[10, 10], [100, 10], [100, 35], [10, 35]]},
                    {"index": 2, "text": "1정", "recognition_score": 0.97, "polygon": [[120, 10], [170, 10], [170, 35], [120, 35]]},
                ],
            }
            draft = prepare_real_annotation(sample, runtime_result)
            self.assertEqual(draft["annotation_status"], "draft")
            self.assertTrue(all(node["label_status"] == "unlabeled" for node in draft["observation"]["nodes"]))
            self.assertNotIn("parser", draft["observation"]["profile"])
            self.assertNotIn("parser", draft["observation"]["profile"]["implementation"])

            draft["observation"]["nodes"][0].update(label_status="labeled", semantic_role="product", association_group="m1")
            draft["observation"]["nodes"][1].update(label_status="labeled", semantic_role="dose", association_group="m1")
            draft["gold_rows"] = [{"gold_row_id": "m1", "product_query": "가나다정", "draft": {"dose_amount": 1, "dose_unit": "tablet"}}]
            completed = finalize_real_annotation(draft)
            self.assertEqual(completed["annotation_status"], "complete")
            self.assertEqual(completed["relations"][0]["label"], "same_medication")
            self.assertEqual(completed["source_kind"], "real_deidentified")

    def test_finalize_rejects_unlabeled_node(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = load_real_source_manifest(self._manifest(root))
            sample = source.samples[0]
            runtime_result = {
                "status": "ok",
                "profile": {},
                "image": {"sha256": sample["image_sha256"], "width": 1200, "height": 1600},
                "regions": [{"index": 1, "text": "가나다정", "recognition_score": 0.98, "polygon": [[10, 10], [100, 10], [100, 35], [10, 35]]}],
            }
            draft = prepare_real_annotation(sample, runtime_result)
            with self.assertRaisesRegex(ParserDatasetError, "unlabeled"):
                finalize_real_annotation(draft)


if __name__ == "__main__":
    unittest.main()
