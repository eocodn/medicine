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


def _runtime_profile(image_sha256: str = "a" * 64) -> dict:
    return {
        "schema_version": 2,
        "image_sha256": image_sha256,
        "baseline_result_sha256": "1" * 64,
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
            "full_document_cli": "9" * 64,
            "crop_refinement": "b" * 64,
            "orientation": "1" * 64,
            "orientation_runtime": "2" * 64,
            "detector_runtime": "c" * 64,
            "detector_benchmark": "d" * 64,
        },
    }


def _doc(*, source_kind: str = "synthetic", split: str = "train") -> dict:
    document = {
        "document_id": "doc-001",
        "split": split,
        "source_kind": source_kind,
        "source_binding": (
            {"kind": "synthetic_truth", "truth_samples_sha256": "9" * 64}
            if source_kind == "synthetic"
            else {
                "kind": "real_source",
                "source_dataset_id": "real-fixture",
                "source_manifest_sha256": "c" * 64,
                "source_samples_sha256": "d" * 64,
            }
        ),
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
            "profile": {"producer": "unified_truth", "truth_samples_sha256": "9" * 64},
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
        "gold_rows_reviewed": True,
    }
    if source_kind == "real_deidentified":
        document["provenance"] = {"source_id": "fixture-source", "license_id": "private-deidentified"}
    return document


def _rewrite_samples(manifest_path: Path, documents: list[dict]) -> None:
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    samples_path = manifest_path.parent / manifest["samples_file"]
    payload = "".join(
        json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"
        for document in documents
    ).encode("utf-8")
    samples_path.write_bytes(payload)
    manifest["samples_sha256"] = hashlib.sha256(payload).hexdigest()
    manifest["document_count"] = len(documents)
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    state_path = manifest_path.parent / ".dataset-state.json"
    if state_path.is_file():
        state = json.loads(state_path.read_text(encoding="utf-8"))
        state["profile"]["samples_sha256"] = manifest["samples_sha256"]
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


class ParserTrainingDatasetContractTest(unittest.TestCase):
    def test_strict_artifact_rejects_empty_document_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "at least one|empty"):
                write_parser_dataset(Path(raw), dataset_id="empty-dataset", documents=[])

    def test_loader_requires_authoritative_completed_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(root, dataset_id="state-bound", documents=[_doc()])
            state_path = root / ".dataset-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "running"
            state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "completed|running|state"):
                load_parser_dataset(manifest_path)

    def test_metadata_must_describe_actual_document_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "metadata.*split|split.*metadata"):
                write_parser_dataset(
                    Path(raw) / "split",
                    dataset_id="metadata-split-mismatch",
                    documents=[_doc(split="train")],
                    metadata={"split": "test"},
                )
            with self.assertRaisesRegex(ParserDatasetError, "metadata.*observation|observation.*metadata"):
                write_parser_dataset(
                    Path(raw) / "observation",
                    dataset_id="metadata-observation-mismatch",
                    documents=[_doc()],
                    metadata={"observation_kind": "runtime_ocr"},
                )
            with self.assertRaisesRegex(ParserDatasetError, "truth_samples_sha256"):
                write_parser_dataset(
                    Path(raw) / "truth",
                    dataset_id="metadata-truth-mismatch",
                    documents=[_doc()],
                    metadata={
                        "builder": "parser_training_builder_v1",
                        "truth_samples_sha256": "8" * 64,
                        "observation_kind": "oracle",
                        "split": "train",
                        "seed": 112,
                    },
                )

    def test_non_runtime_observation_profiles_are_strict_and_non_free_text(self) -> None:
        oracle = _doc()
        oracle["observation"]["profile"]["patient_name"] = "홍길동"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "observation.*profile|profile.*unsupported"):
                write_parser_dataset(Path(raw), dataset_id="oracle-profile-pii", documents=[oracle])

        synthetic = _doc()
        synthetic["observation"]["kind"] = "synthetic_ocr"
        synthetic["observation"]["profile"] = {
            "producer": "deterministic_synthetic_ocr",
            "revision": 1,
            "seed": 112,
            "truth_samples_sha256": "9" * 64,
            "note": "010-1234-5678",
        }
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "observation.*profile|profile.*unsupported"):
                write_parser_dataset(Path(raw), dataset_id="synthetic-profile-pii", documents=[synthetic])

    def test_loader_rejects_non_standard_json_constants(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(root, dataset_id="strict-json", documents=[_doc()])
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            samples_path = root / manifest["samples_file"]
            raw_sample = samples_path.read_text(encoding="utf-8").strip()
            raw_sample = raw_sample.replace('"producer":"unified_truth"', '"bad":NaN,"producer":"unified_truth"')
            payload = (raw_sample + "\n").encode("utf-8")
            samples_path.write_bytes(payload)
            manifest["samples_sha256"] = hashlib.sha256(payload).hexdigest()
            manifest_path.write_text(json.dumps(manifest, sort_keys=True) + "\n", encoding="utf-8")
            state_path = root / ".dataset-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["profile"]["samples_sha256"] = manifest["samples_sha256"]
            state_path.write_text(json.dumps(state, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "strict JSON|invalid JSON|NaN|constant"):
                load_parser_dataset(manifest_path)

    def test_round_trips_strict_document_dataset(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = write_parser_dataset(
                root,
                dataset_id="parser-fixture-v1",
                documents=[_doc()],
                metadata={"seed": 1},
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

    def test_real_deidentified_artifact_rejects_duplicate_image_hashes(self) -> None:
        first = _doc(source_kind="real_deidentified", split="val")
        first["observation"]["kind"] = "runtime_ocr"
        first["observation"]["profile"] = _runtime_profile()
        second = _doc(source_kind="real_deidentified", split="test")
        second["document_id"] = "doc-002"
        second["observation"]["kind"] = "runtime_ocr"
        second["observation"]["profile"] = _runtime_profile()
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "duplicate real.*image SHA-256|image SHA-256.*unique"):
                write_parser_dataset(Path(raw), dataset_id="duplicate-real-image", documents=[first, second])

    def test_loader_rejects_duplicate_real_image_hashes_in_repacked_artifact(self) -> None:
        first = _doc(source_kind="real_deidentified", split="val")
        first["observation"]["kind"] = "runtime_ocr"
        first["observation"]["profile"] = _runtime_profile()
        second = _doc(source_kind="real_deidentified", split="test")
        second["document_id"] = "doc-002"
        second["image_sha256"] = "b" * 64
        second["observation"]["kind"] = "runtime_ocr"
        second["observation"]["profile"] = _runtime_profile("b" * 64)
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(root, dataset_id="repacked-real-image", documents=[first, second])
            dataset = load_parser_dataset(manifest_path)
            forged = [dict(document) for document in dataset.documents]
            forged[1]["image_sha256"] = first["image_sha256"]
            forged[1]["observation"] = dict(forged[1]["observation"])
            forged[1]["observation"]["profile"] = dict(forged[1]["observation"]["profile"])
            forged[1]["observation"]["profile"]["image_sha256"] = first["image_sha256"]
            _rewrite_samples(manifest_path, forged)
            with self.assertRaisesRegex(ParserDatasetError, "duplicate real.*image SHA-256|image SHA-256.*unique"):
                load_parser_dataset(manifest_path)

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

    def test_runtime_observation_accepts_hash_bound_trained_detector_candidate_id(self) -> None:
        document = _doc()
        document["observation"]["kind"] = "runtime_ocr"
        document["observation"]["profile"] = _runtime_profile()
        document["observation"]["profile"]["detector_model"] = "PP-OCRv5_mobile_det_candidate_955718a0048c"
        with tempfile.TemporaryDirectory() as raw:
            manifest = write_parser_dataset(Path(raw), dataset_id="trained-detector-runtime", documents=[document])
            loaded = load_parser_dataset(manifest)
            self.assertEqual(
                loaded.documents[0]["observation"]["profile"]["detector_model"],
                "PP-OCRv5_mobile_det_candidate_955718a0048c",
            )

    def test_runtime_observation_requires_canonical_pinned_profile(self) -> None:
        document = _doc(source_kind="real_deidentified", split="val")
        document["observation"]["kind"] = "runtime_ocr"
        document["observation"]["profile"] = {}
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "runtime OCR profile"):
                write_parser_dataset(Path(raw), dataset_id="bad-runtime-profile", documents=[document])

        document["observation"]["profile"] = {**_runtime_profile(), "patient_name": "홍길동"}
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "unsupported runtime OCR profile fields"):
                write_parser_dataset(Path(raw), dataset_id="bad-runtime-extra", documents=[document])

        document["observation"]["profile"] = _runtime_profile()
        document["observation"]["profile"]["detector_model"] = "patient-john-ssn"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "detector_model"):
                write_parser_dataset(Path(raw), dataset_id="bad-runtime-model-id", documents=[document])

        document["observation"]["profile"] = _runtime_profile()
        document["observation"]["profile"]["implementation"]["patient_id"] = "secret"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "unsupported runtime OCR implementation fields"):
                write_parser_dataset(Path(raw), dataset_id="bad-runtime-implementation-extra", documents=[document])

    def test_real_provenance_license_id_must_be_supported_deidentified_source_id(self) -> None:
        document = _doc(source_kind="real_deidentified", split="val")
        document["observation"]["kind"] = "runtime_ocr"
        document["observation"]["profile"] = _runtime_profile()
        document["provenance"]["license_id"] = "patient-hong-gildong-rrn"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "license_id.*supported.*license id"):
                write_parser_dataset(Path(raw), dataset_id="identifying-license", documents=[document])

    def test_observation_polygon_must_be_inside_image_and_non_degenerate(self) -> None:
        invalid_polygons = [
            [[-10, 10], [80, 10], [80, 30], [-10, 30]],
            [[10, 10], [10, 10], [10, 10], [10, 10]],
            [[10, 10], [100, 100], [10, 100], [80, 10]],
        ]
        for index, polygon in enumerate(invalid_polygons):
            with self.subTest(polygon=polygon), tempfile.TemporaryDirectory() as raw:
                document = _doc()
                document["observation"]["nodes"][0]["polygon"] = polygon
                with self.assertRaisesRegex(ParserDatasetError, "polygon.*image|polygon.*area|polygon.*degenerate|polygon.*convex"):
                    write_parser_dataset(Path(raw), dataset_id=f"bad-polygon-{index}", documents=[document])

    def test_medication_roles_cannot_use_reserved_document_group(self) -> None:
        document = _doc()
        for node in document["observation"]["nodes"]:
            node["association_group"] = "document"
        document["gold_rows"] = []
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "document.*association_group|association_group.*document"):
                write_parser_dataset(Path(raw), dataset_id="reserved-medication-group", documents=[document])

    def test_manifest_metadata_rejects_arbitrary_patient_identifying_fields_on_write_and_load(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "metadata"):
                write_parser_dataset(
                    root / "write",
                    dataset_id="identifying-metadata-write",
                    documents=[_doc()],
                    metadata={"patient_name": "홍길동", "note": "주민번호 900101"},
                )
            with self.assertRaisesRegex(ParserDatasetError, "metadata.*builder|builder.*unsupported"):
                write_parser_dataset(
                    root / "builder-write",
                    dataset_id="identifying-builder-write",
                    documents=[_doc()],
                    metadata={"builder": "patient-hong-gildong-rrn"},
                )

            manifest_path = write_parser_dataset(
                root / "load",
                dataset_id="identifying-metadata-load",
                documents=[_doc()],
                metadata={"seed": 1},
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["metadata"] = {"patient_name": "홍길동", "note": "주민번호 900101"}
            encoded = json.dumps(
                manifest["metadata"], ensure_ascii=False, sort_keys=True, separators=(",", ":")
            ).encode("utf-8")
            manifest["metadata_sha256"] = hashlib.sha256(encoded).hexdigest()
            manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "metadata"):
                load_parser_dataset(manifest_path)

    def test_runtime_artifact_rejects_mixed_producers_on_write_and_load(self) -> None:
        first = _doc(split="val")
        first["observation"]["kind"] = "runtime_ocr"
        first["observation"]["profile"] = _runtime_profile()
        second = _doc(split="val")
        second["document_id"] = "doc-002"
        second["observation"]["kind"] = "runtime_ocr"
        second["observation"]["profile"] = _runtime_profile()
        second["observation"]["profile"]["recognizer_checkpoint_sha256"] = "f" * 64
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "runtime OCR producer|mix.*producer"):
                write_parser_dataset(Path(raw), dataset_id="mixed-runtime-writer", documents=[first, second])

        second["observation"]["profile"] = _runtime_profile()
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(root, dataset_id="mixed-runtime-loader", documents=[first, second])
            dataset = load_parser_dataset(manifest_path)
            forged = [dict(document) for document in dataset.documents]
            forged[1]["observation"] = dict(forged[1]["observation"])
            forged[1]["observation"]["profile"] = dict(forged[1]["observation"]["profile"])
            forged[1]["observation"]["profile"]["recognizer_checkpoint_sha256"] = "f" * 64
            _rewrite_samples(manifest_path, forged)
            with self.assertRaisesRegex(ParserDatasetError, "runtime OCR producer|mix.*producer"):
                load_parser_dataset(manifest_path)

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

    def test_relation_label_must_match_endpoint_association_groups(self) -> None:
        same_with_different_groups = _doc()
        same_with_different_groups["observation"]["nodes"][1]["association_group"] = "med-2"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "association_group"):
                write_parser_dataset(
                    Path(raw),
                    dataset_id="bad-relation-same",
                    documents=[same_with_different_groups],
                )

        different_with_same_group = _doc()
        different_with_same_group["relations"][0]["label"] = "different_medication"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "association_group"):
                write_parser_dataset(
                    Path(raw),
                    dataset_id="bad-relation-different",
                    documents=[different_with_same_group],
                )

    def test_complete_document_requires_full_relation_supervision(self) -> None:
        document = _doc()
        document["relations"] = []
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "relation supervision"):
                write_parser_dataset(Path(raw), dataset_id="missing-relations", documents=[document])

    def test_gold_draft_values_are_strict_json_domain_values(self) -> None:
        document = _doc()
        document["gold_rows"][0]["draft"]["dose_amount"] = float("nan")
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "dose_amount"):
                write_parser_dataset(Path(raw), dataset_id="nan-gold", documents=[document])

        invalid_values = {
            "dose_amount": "not-a-number",
            "frequency_per_day": 1.5,
            "prescription_days": 0,
            "schedule_times": "08:00",
            "meal_relation": "sometimes",
            "administration_route": "teleport",
            "as_needed": "yes",
            "start_date": "2026-99-99",
        }
        for field, value in invalid_values.items():
            with self.subTest(field=field), tempfile.TemporaryDirectory() as raw:
                document = _doc()
                document["gold_rows"][0]["draft"] = {field: value}
                with self.assertRaisesRegex(ParserDatasetError, field):
                    write_parser_dataset(Path(raw), dataset_id=f"bad-{field}", documents=[document])

    def test_complete_document_requires_gold_for_each_labeled_medication_group(self) -> None:
        document = _doc()
        document["gold_rows"][0]["gold_row_id"] = "different-group"
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "med-1.*image gold|missing from image gold"):
                write_parser_dataset(Path(raw), dataset_id="missing-group-gold", documents=[document])

        document = _doc()
        document["observation"]["nodes"][0].update(semantic_role="other", association_group=None)
        document["relations"] = []
        document["gold_rows"] = []
        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaisesRegex(ParserDatasetError, "med-1.*image gold|missing from image gold"):
                write_parser_dataset(Path(raw), dataset_id="dose-group-empty-gold", documents=[document])

    def test_gold_draft_rejects_cross_field_product_invariants(self) -> None:
        invalid_drafts = [
            {"as_needed": True, "frequency_per_day": 1},
            {"as_needed": True, "schedule_times": ["08:00"]},
            {"frequency_per_day": 2, "schedule_times": ["08:00"]},
            {"start_date": "2026-08-01", "end_date": "2026-07-31"},
            {"start_date": "2026-08-01", "end_date": "2026-08-05", "prescription_days": 3},
        ]
        for index, draft in enumerate(invalid_drafts):
            with self.subTest(draft=draft), tempfile.TemporaryDirectory() as raw:
                document = _doc()
                document["gold_rows"][0]["draft"] = draft
                with self.assertRaises(ParserDatasetError):
                    write_parser_dataset(Path(raw), dataset_id=f"bad-cross-field-{index}", documents=[document])

    def test_completed_dataset_rejects_persisted_manifest_metadata_tampering(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = write_parser_dataset(
                root,
                dataset_id="metadata-bound",
                documents=[_doc()],
                metadata={"seed": 1},
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["metadata"] = {"seed": 999}
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "metadata"):
                write_parser_dataset(
                    root,
                    dataset_id="metadata-bound",
                    documents=[_doc()],
                    metadata={"seed": 1},
                )


if __name__ == "__main__":
    unittest.main()
