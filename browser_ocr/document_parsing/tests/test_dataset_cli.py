from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from browser_ocr.document_parsing.dataset_cli import _finalize_real, _prepare_real, build_parser, main
from browser_ocr.document_parsing.training_dataset import ParserDatasetError, load_parser_dataset, write_parser_dataset


class ParserDatasetCliTest(unittest.TestCase):
    def _real_source_and_result(self, root: Path) -> tuple[Path, Path]:
        source = root / "source"
        source.mkdir(parents=True)
        image = source / "rx-001.jpg"
        image_bytes = b"\xff\xd8\xffdeidentified"
        image.write_bytes(image_bytes)
        image_sha = hashlib.sha256(image_bytes).hexdigest()
        (source / "samples.jsonl").write_text(json.dumps({
            "document_id": "rx-001",
            "image": "rx-001.jpg",
            "image_sha256": image_sha,
            "split": "val",
            "document_type": "prescription",
            "layout_family": "real_unknown",
            "privacy": {"contains_patient_data": False, "deidentified": True},
            "provenance": {"source_id": "source-a", "license_id": "private-deidentified"},
            "scenario_tags": ["prescription"],
            "risk_tags": ["real_photo"],
        }) + "\n", encoding="utf-8")
        manifest = source / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": "real-fixture",
            "source_kind": "real_deidentified",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
        }), encoding="utf-8")
        results = root / "results" / "rx-001"
        results.mkdir(parents=True)
        profile = {
            "schema_version": 2,
            "image_sha256": image_sha,
            "baseline_result_sha256": "1" * 64,
            "recognizer_checkpoint_sha256": "2" * 64,
            "recognizer_config_sha256": "3" * 64,
            "recognizer_device": "cpu",
            "detector_manifest_sha256": "4" * 64,
            "detector_model": "PP-OCRv5_mobile_det",
            "detector_edge": 640,
            "detector_threads": 1,
            "detector_asset_sha256": "5" * 64,
            "parser": "geometry_rule_v2",
            "implementation": {
                "full_document": "6" * 64,
                "full_document_cli": "7" * 64,
                "crop_refinement": "8" * 64,
                "parser": "9" * 64,
                "parser_contract": "a" * 64,
                "detector_runtime": "b" * 64,
                "detector_benchmark": "c" * 64,
            },
        }
        (results / "result.json").write_text(json.dumps({
            "status": "ok",
            "profile": profile,
            "image": {"sha256": image_sha, "width": 1200, "height": 1600},
            "regions": [{
                "index": 1,
                "text": "가나다정",
                "recognition_score": 0.98,
                "polygon": [[10, 10], [100, 10], [100, 35], [10, 35]],
            }],
        }), encoding="utf-8")
        return manifest, root / "results"

    def test_parser_exposes_dataset_controls(self) -> None:
        parser = build_parser()
        args = parser.parse_args(["validate", "--manifest", "/tmp/manifest.json", "--json"])
        self.assertEqual(args.command, "validate")
        args = parser.parse_args([
            "build-synthetic", "--truth-samples", "/tmp/truth.jsonl", "--output-dir", "/tmp/out",
            "--dataset-id", "fixture", "--observation-kind", "synthetic_ocr", "--split", "train",
        ])
        self.assertEqual(args.observation_kind, "synthetic_ocr")

    def test_validate_reports_machine_readable_summary(self) -> None:
        document = {
            "document_id": "doc",
            "split": "val",
            "source_kind": "synthetic",
            "image_sha256": "a" * 64,
            "width": 100,
            "height": 100,
            "layout_family": "fixture",
            "scenario_tags": [],
            "risk_tags": [],
            "privacy": {"contains_patient_data": False, "deidentified": False},
            "observation": {"kind": "oracle", "profile": {}, "nodes": []},
            "relations": [],
            "gold_rows": [],
            "gold_rows_reviewed": True,
            "annotation_status": "complete",
        }
        with tempfile.TemporaryDirectory() as raw:
            manifest = write_parser_dataset(Path(raw), dataset_id="fixture", documents=[document])
            output = StringIO()
            with redirect_stdout(output):
                code = main(["validate", "--manifest", str(manifest), "--json"])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["documents"], 1)
            self.assertEqual(payload["splits"]["val"], 1)

    def test_prepare_real_rerun_preserves_human_labels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            output = root / "annotations-out"
            first = _prepare_real(str(source_manifest), str(results_root), str(output))
            self.assertFalse(first["reused"])
            annotation_path = output / "annotations" / "rx-001.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["observation"]["nodes"][0].update(
                label_status="labeled",
                semantic_role="product",
                association_group="m1",
            )
            annotation["gold_rows"] = [{"gold_row_id": "m1", "product_query": "가나다정", "draft": {}}]
            annotation["gold_rows_reviewed"] = True
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")

            second = _prepare_real(str(source_manifest), str(results_root), str(output))
            self.assertTrue(second["reused"])
            preserved = json.loads(annotation_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["observation"]["nodes"][0]["semantic_role"], "product")

    def test_finalize_real_rechecks_source_and_runtime_snapshot_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            output = root / "annotations-out"
            _prepare_real(str(source_manifest), str(results_root), str(output))
            annotation_path = output / "annotations" / "rx-001.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["observation"]["nodes"][0].update(
                label_status="labeled",
                semantic_role="product",
                association_group="m1",
            )
            annotation["gold_rows"] = [{"gold_row_id": "m1", "product_query": "가나다정", "draft": {}}]
            annotation["gold_rows_reviewed"] = True
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")

            runtime_path = results_root / "rx-001" / "result.json"
            runtime_path.write_text(runtime_path.read_text(encoding="utf-8") + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ParserDatasetError, "runtime OCR result SHA-256"):
                _finalize_real(str(output / "annotations"), "real-final", str(root / "final"))

    def test_prepare_real_recovers_orphaned_matching_draft_after_crash(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            output = root / "annotations-out"
            _prepare_real(str(source_manifest), str(results_root), str(output))
            annotation_path = output / "annotations" / "rx-001.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["observation"]["nodes"][0].update(
                label_status="labeled",
                semantic_role="product",
                association_group="m1",
            )
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")
            (output / "annotations" / "index.json").unlink()

            resumed = _prepare_real(str(source_manifest), str(results_root), str(output))
            self.assertTrue(resumed["resumed"])
            preserved = json.loads(annotation_path.read_text(encoding="utf-8"))
            self.assertEqual(preserved["observation"]["nodes"][0]["semantic_role"], "product")
            self.assertTrue((output / "annotations" / "index.json").is_file())

    def test_finalize_real_preserves_bound_source_provenance(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            output = root / "annotations-out"
            _prepare_real(str(source_manifest), str(results_root), str(output))
            annotation_path = output / "annotations" / "rx-001.json"
            annotation = json.loads(annotation_path.read_text(encoding="utf-8"))
            annotation["observation"]["nodes"][0].update(
                label_status="labeled",
                semantic_role="product",
                association_group="m1",
            )
            annotation["gold_rows"] = [{"gold_row_id": "m1", "product_query": "가나다정", "draft": {}}]
            annotation["gold_rows_reviewed"] = True
            annotation_path.write_text(json.dumps(annotation, ensure_ascii=False), encoding="utf-8")

            finalized = _finalize_real(str(output / "annotations"), "real-final", str(root / "final"))
            dataset = load_parser_dataset(finalized["manifest"])
            self.assertEqual(dataset.documents[0]["provenance"], {"source_id": "source-a", "license_id": "private-deidentified"})
            self.assertEqual(dataset.metadata["source_manifest_sha256"], hashlib.sha256(source_manifest.read_bytes()).hexdigest())
            self.assertEqual(dataset.metadata["source_samples_sha256"], hashlib.sha256((source_manifest.parent / "samples.jsonl").read_bytes()).hexdigest())

    def test_prepare_real_rejects_mixed_runtime_ocr_producers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            source_root = source_manifest.parent
            second_image = source_root / "rx-002.jpg"
            second_bytes = b"\xff\xd8\xffdeidentified-second"
            second_image.write_bytes(second_bytes)
            second_sha = hashlib.sha256(second_bytes).hexdigest()
            with (source_root / "samples.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "document_id": "rx-002",
                    "image": "rx-002.jpg",
                    "image_sha256": second_sha,
                    "split": "val",
                    "document_type": "prescription",
                    "layout_family": "real_unknown",
                    "privacy": {"contains_patient_data": False, "deidentified": True},
                    "provenance": {"source_id": "source-a", "license_id": "private-deidentified"},
                    "scenario_tags": ["prescription"],
                    "risk_tags": ["real_photo"],
                }) + "\n")
            first_result = json.loads((results_root / "rx-001" / "result.json").read_text(encoding="utf-8"))
            second_result = json.loads(json.dumps(first_result))
            second_result["image"]["sha256"] = second_sha
            second_result["profile"]["image_sha256"] = second_sha
            second_result["profile"]["detector_asset_sha256"] = "6" * 64
            second_dir = results_root / "rx-002"
            second_dir.mkdir()
            (second_dir / "result.json").write_text(json.dumps(second_result), encoding="utf-8")

            with self.assertRaisesRegex(ParserDatasetError, "mix different runtime OCR producers"):
                _prepare_real(str(source_manifest), str(results_root), str(root / "annotations-out"))

    def test_prepare_real_reuse_requires_exact_source_document_set(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source_manifest, results_root = self._real_source_and_result(root)
            source_root = source_manifest.parent
            second_image = source_root / "rx-002.jpg"
            second_bytes = b"\xff\xd8\xffdeidentified-second"
            second_image.write_bytes(second_bytes)
            second_sha = hashlib.sha256(second_bytes).hexdigest()
            with (source_root / "samples.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({
                    "document_id": "rx-002",
                    "image": "rx-002.jpg",
                    "image_sha256": second_sha,
                    "split": "val",
                    "document_type": "prescription",
                    "layout_family": "real_unknown",
                    "privacy": {"contains_patient_data": False, "deidentified": True},
                    "provenance": {"source_id": "source-a", "license_id": "private-deidentified"},
                    "scenario_tags": ["prescription"],
                    "risk_tags": ["real_photo"],
                }) + "\n")
            first_result = json.loads((results_root / "rx-001" / "result.json").read_text(encoding="utf-8"))
            second_result = json.loads(json.dumps(first_result))
            second_result["image"]["sha256"] = second_sha
            second_result["profile"]["image_sha256"] = second_sha
            second_dir = results_root / "rx-002"
            second_dir.mkdir()
            (second_dir / "result.json").write_text(json.dumps(second_result), encoding="utf-8")

            output = root / "annotations-out"
            _prepare_real(str(source_manifest), str(results_root), str(output))
            index_path = output / "annotations" / "index.json"
            index = json.loads(index_path.read_text(encoding="utf-8"))
            index["documents"] = [index["documents"][0], index["documents"][0]]
            index_path.write_text(json.dumps(index), encoding="utf-8")

            with self.assertRaisesRegex(ParserDatasetError, "document set"):
                _prepare_real(str(source_manifest), str(results_root), str(output))


if __name__ == "__main__":
    unittest.main()
