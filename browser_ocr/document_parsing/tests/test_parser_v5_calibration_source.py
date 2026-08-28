from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_calibration import (
    build_parser_v5_calibration,
    load_parser_v5_calibration,
)
from browser_ocr.document_parsing.parser_v5_calibration_source import load_parser_v5_calibration_source
from browser_ocr.document_parsing.training_dataset import write_parser_dataset


def _poly(x: float, y: float, w: float = 30, h: float = 16) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _rotate_90(polygon: list[list[float]], *, source_height: float) -> list[list[float]]:
    return [[source_height - y, x] for x, y in polygon]


def _runtime_profile(image_sha256: str) -> dict:
    return {
        "schema_version": 3,
        "image_sha256": image_sha256,
        "recognizer_result_sha256": "1" * 64,
        "recognizer_checkpoint_sha256": "2" * 64,
        "recognizer_config_sha256": "3" * 64,
        "recognizer_device": "cpu",
        "detector_manifest_sha256": "4" * 64,
        "detector_model": "PP-OCRv5_mobile_det",
        "detector_edge": 640,
        "detector_threads": 1,
        "detector_asset_sha256": "5" * 64,
        "detector_onnx_sha256": "e" * 64,
        "detector_config_sha256": "f" * 64,
        "inference_runtime_sha256": "0" * 64,
        "paddleocr_source_sha256": "6" * 64,
        "paddleocr_dictionary_sha256": "7" * 64,
        "implementation": {
            "full_document": "8" * 64,
            "full_document_runtime": "7" * 64,
            "recognizer_runtime": "8" * 64,
            "full_document_cli": "9" * 64,
            "crop_refinement": "b" * 64,
            "orientation": "1" * 64,
            "orientation_runtime": "2" * 64,
            "detector_runtime": "c" * 64,
            "detector_benchmark": "d" * 64,
        },
    }


def _producer() -> dict:
    value = _runtime_profile("a" * 64)
    value.pop("image_sha256")
    return value


def _document(document_id: str, *, rotated: bool, runtime: bool) -> dict:
    truth_sha = "9" * 64
    image_sha = hashlib.sha256(document_id.encode()).hexdigest()
    source_width, source_height = 100, 200
    product = _poly(10, 20)
    dose = _poly(10, 60)
    if runtime and rotated:
        width, height = source_height, source_width
        product = _rotate_90(product, source_height=source_height)
        dose = _rotate_90(dose, source_height=source_height)
    else:
        width, height = source_width, source_height
    if runtime:
        profile = _runtime_profile(image_sha)
        kind = "runtime_ocr"
        node_ids = ("region-0001", "region-0002")
        confidences = (0.91, 0.83)
    else:
        profile = {"producer": "unified_truth", "truth_samples_sha256": truth_sha}
        kind = "oracle"
        node_ids = ("gt-product", "gt-dose")
        confidences = (1.0, 1.0)
    return {
        "document_id": document_id,
        "split": "train",
        "source_kind": "synthetic",
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": truth_sha},
        "image_sha256": image_sha,
        "width": width,
        "height": height,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription"],
        "risk_tags": ["row_association"],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "observation": {
            "kind": kind,
            "profile": profile,
            "nodes": [
                {
                    "node_id": node_ids[0],
                    "text": "가나다정",
                    "confidence": confidences[0],
                    "polygon": product,
                    "target_region_ids": ["gt-product"],
                    "label_status": "labeled",
                    "semantic_role": "product",
                    "association_group": "med-1",
                },
                {
                    "node_id": node_ids[1],
                    "text": "1정",
                    "confidence": confidences[1],
                    "polygon": dose,
                    "target_region_ids": ["gt-dose"],
                    "label_status": "labeled",
                    "semantic_role": "dose",
                    "association_group": "med-1",
                },
            ],
        },
        "relations": [
            {"product_node_id": node_ids[0], "field_node_id": node_ids[1], "label": "same_medication"}
        ],
        "gold_rows": [
            {
                "gold_row_id": "med-1",
                "product_query": "가나다정",
                "draft": {"dose_amount": 1, "dose_unit": "tablet"},
            }
        ],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ParserV5CalibrationSourceTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Path, Path, Path]:
        truth_sha = "9" * 64
        oracle_docs = [
            _document("doc-a", rotated=False, runtime=False),
            _document("doc-b", rotated=False, runtime=False),
        ]
        runtime_docs = [
            _document("doc-a", rotated=False, runtime=True),
            _document("doc-b", rotated=True, runtime=True),
        ]
        oracle = write_parser_dataset(
            root / "oracle",
            dataset_id="cal-oracle",
            documents=oracle_docs,
            metadata={
                "builder": "parser_training_builder_v1",
                "truth_samples_sha256": truth_sha,
                "observation_kind": "oracle",
                "split": "train",
                "seed": 7,
            },
        )
        runtime = write_parser_dataset(
            root / "runtime-dataset",
            dataset_id="cal-runtime",
            documents=runtime_docs,
            metadata={
                "builder": "parser_runtime_builder_v2",
                "truth_samples_sha256": truth_sha,
                "observation_kind": "runtime_ocr",
                "split": "train",
                "ocr_producer": _producer(),
            },
        )

        batch_root = root / "runtime-batch"
        result_hashes: dict[str, str] = {}
        for document, rotation in zip(runtime_docs, (0, 90), strict=True):
            document_id = document["document_id"]
            raw_dir = batch_root / "runtime" / document_id
            raw_dir.mkdir(parents=True)
            raw = {
                "schema_version": 2,
                "status": "ok",
                "image": {
                    "path": f"/images/{document_id}.jpg",
                    "sha256": document["image_sha256"],
                    "source_width": 100,
                    "source_height": 200,
                    "width": document["width"],
                    "height": document["height"],
                },
                "profile": _runtime_profile(document["image_sha256"]),
                "regions": [
                    {
                        "crop": "region-0001.png",
                        "index": 1,
                        "text": document["observation"]["nodes"][0]["text"],
                        "polygon": document["observation"]["nodes"][0]["polygon"],
                        "detection_score": 0.73,
                        "recognition_score": 0.91,
                    },
                    {
                        "crop": "region-0002.png",
                        "index": 2,
                        "text": document["observation"]["nodes"][1]["text"],
                        "polygon": document["observation"]["nodes"][1]["polygon"],
                        "detection_score": 0.81,
                        "recognition_score": 0.83,
                    },
                ],
                "stages": {
                    "detection": {"status": "ok"},
                    "orientation": {"status": "ok", "applied_rotation_degrees": rotation},
                    "recognition": {"status": "ok"},
                },
                "text_lines": ["가나다정", "1정"],
            }
            raw_path = raw_dir / "result.json"
            raw_path.write_text(json.dumps(raw, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            result_hashes[document_id] = _sha(raw_path)
        batch = {
            "status": "ok",
            "documents": 2,
            "profile": {"truth_samples_sha256": truth_sha, "ocr_producer": _producer()},
            "runtime_results": result_hashes,
        }
        batch_path = batch_root / "result.json"
        batch_path.write_text(json.dumps(batch, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
        return oracle, runtime, batch_path

    def test_source_binds_oracle_runtime_and_raw_confidences_across_orientation(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            oracle, runtime, batch = self._fixture(Path(raw))
            source = load_parser_v5_calibration_source(
                oracle_manifest=oracle,
                runtime_manifest=runtime,
                runtime_batch_result=batch,
            )
            self.assertEqual(source.document_count, 2)
            self.assertEqual(source.truth_samples_sha256, "9" * 64)
            self.assertEqual(len(source.source_fingerprint), 64)
            self.assertEqual(len(source.producer_fingerprint), 64)
            rotated = next(item for item in source.documents if item["document_id"] == "doc-b")
            self.assertEqual(rotated["rotation_degrees"], 90)
            self.assertEqual(rotated["runtime_nodes"][0]["detector_confidence"], 0.73)
            self.assertEqual(rotated["runtime_nodes"][0]["recognizer_confidence"], 0.91)
            self.assertEqual(rotated["runtime_nodes"][0]["target_region_ids"], ["gt-product"])

    def test_calibration_uses_aligned_truth_and_does_not_count_orientation_as_geometry_noise(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oracle, runtime, batch = self._fixture(root)
            output = build_parser_v5_calibration(
                oracle_manifest=oracle,
                runtime_manifest=runtime,
                runtime_batch_result=batch,
                output_path=root / "calibration.json",
            )
            artifact = load_parser_v5_calibration(output)
            self.assertEqual(artifact["schema_version"], 2)
            self.assertEqual(artifact["document_count"], 2)
            self.assertEqual(artifact["summary"]["drop_rate"], 0.0)
            self.assertEqual(artifact["summary"]["split_rate"], 0.0)
            self.assertEqual(artifact["summary"]["merge_rate"], 0.0)
            self.assertEqual(artifact["summary"]["recognition_error_rate"], 0.0)
            self.assertAlmostEqual(artifact["summary"]["geometry_shift_mean"], 0.0, places=9)
            self.assertEqual(artifact["summary"]["reading_order_inversion_rate"], 0.0)
            self.assertEqual(artifact["summary"]["detector_confidence_p10"], 0.73)
            self.assertEqual(artifact["summary"]["recognizer_confidence_p10"], 0.83)
            self.assertEqual(
                artifact["source_fingerprint"],
                load_parser_v5_calibration_source(
                    oracle_manifest=oracle,
                    runtime_manifest=runtime,
                    runtime_batch_result=batch,
                ).source_fingerprint,
            )

    def test_source_rejects_non_train_or_different_truth_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oracle, runtime, batch = self._fixture(root)
            runtime_manifest = json.loads(runtime.read_text(encoding="utf-8"))
            runtime_manifest["metadata"]["truth_samples_sha256"] = "8" * 64
            runtime_manifest["metadata_sha256"] = hashlib.sha256(
                json.dumps(
                    runtime_manifest["metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
                ).encode()
            ).hexdigest()
            runtime.write_text(json.dumps(runtime_manifest, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "truth identity|truth_samples_sha256"):
                load_parser_v5_calibration_source(
                    oracle_manifest=oracle,
                    runtime_manifest=runtime,
                    runtime_batch_result=batch,
                )

    def test_source_rejects_raw_result_hash_or_runtime_alignment_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            oracle, runtime, batch = self._fixture(root)
            batch_value = json.loads(batch.read_text(encoding="utf-8"))
            batch_value["runtime_results"]["doc-a"] = "f" * 64
            batch.write_text(json.dumps(batch_value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "raw runtime result SHA-256"):
                load_parser_v5_calibration_source(
                    oracle_manifest=oracle,
                    runtime_manifest=runtime,
                    runtime_batch_result=batch,
                )

            oracle, runtime, batch = self._fixture(root / "second")
            raw_result = batch.parent / "runtime" / "doc-a" / "result.json"
            raw_value = json.loads(raw_result.read_text(encoding="utf-8"))
            raw_value["regions"][0]["text"] = "다른문자"
            raw_result.write_text(json.dumps(raw_value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            batch_value = json.loads(batch.read_text(encoding="utf-8"))
            batch_value["runtime_results"]["doc-a"] = _sha(raw_result)
            batch.write_text(json.dumps(batch_value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "runtime dataset.*raw runtime"):
                load_parser_v5_calibration_source(
                    oracle_manifest=oracle,
                    runtime_manifest=runtime,
                    runtime_batch_result=batch,
                )


if __name__ == "__main__":
    unittest.main()