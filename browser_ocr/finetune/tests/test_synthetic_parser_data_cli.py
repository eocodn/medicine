from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from browser_ocr.document_parsing.training_dataset import load_parser_dataset
from browser_ocr.finetune.synthetic_parser_data_cli import build_parser, run_synthetic_batch


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _producer() -> dict[str, object]:
    return {
        "schema_version": 3,
        "recognizer_result_sha256": "1" * 64,
        "recognizer_checkpoint_sha256": "2" * 64,
        "recognizer_config_sha256": "3" * 64,
        "recognizer_device": "cpu",
        "detector_manifest_sha256": "4" * 64,
        "detector_model": "PP-OCRv5_mobile_det",
        "detector_edge": 640,
        "detector_threads": 1,
        "detector_asset_sha256": "5" * 64,
        "detector_onnx_sha256": "6" * 64,
        "detector_config_sha256": "7" * 64,
        "inference_runtime_sha256": "8" * 64,
        "paddleocr_source_sha256": "9" * 64,
        "paddleocr_dictionary_sha256": "a" * 64,
        "implementation": {
            "full_document": "b" * 64,
            "full_document_cli": "c" * 64,
            "full_document_runtime": "3" * 64,
            "recognizer_runtime": "4" * 64,
            "crop_refinement": "d" * 64,
            "orientation": "1" * 64,
            "orientation_runtime": "2" * 64,
            "detector_runtime": "e" * 64,
            "detector_benchmark": "f" * 64,
        },
    }


def _truth_sample(document_id: str, split: str, image_sha256: str, y: int) -> dict[str, object]:
    return {
        "document_id": document_id,
        "split": split,
        "image_sha256": image_sha256,
        "width": 1280,
        "height": 1600,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription", "table"],
        "risk_tags": ["row_association"],
        "nodes": [
            {"node_id": "p", "text": "가나다정", "confidence": 1.0, "polygon": _poly(10, y), "natural_text_polygon": _poly(10, y), "semantic_role": "product", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "d", "text": "1정", "confidence": 1.0, "polygon": _poly(120, y), "semantic_role": "dose", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "f", "text": "3회", "confidence": 1.0, "polygon": _poly(220, y), "semantic_role": "frequency", "association_group": "m1", "region_class": "medication", "critical": True},
            {"node_id": "t", "text": "5일", "confidence": 1.0, "polygon": _poly(320, y), "semantic_role": "duration", "association_group": "m1", "region_class": "medication", "critical": True},
        ],
        "positive_edges": [
            {"product_node_id": "p", "field_node_id": "d", "relation": "same_medication"},
            {"product_node_id": "p", "field_node_id": "f", "relation": "same_medication"},
            {"product_node_id": "p", "field_node_id": "t", "relation": "same_medication"},
        ],
        "expected_rows": [{
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
        }],
    }


class SyntheticParserDataCliTest(unittest.TestCase):
    def _source(self, root: Path) -> tuple[Path, Path, list[dict[str, object]]]:
        corpus_root = root / "corpus"
        images = corpus_root / "images"
        images.mkdir(parents=True)
        samples: list[dict[str, object]] = []
        truth: list[dict[str, object]] = []
        for index, split in enumerate(("train", "val", "test"), start=1):
            document_id = f"synthetic-{index:06d}"
            image_path = images / f"{document_id}.jpg"
            image_path.write_bytes(f"fake-jpeg-{index}".encode())
            image_sha = hashlib.sha256(image_path.read_bytes()).hexdigest()
            samples.append({
                "id": document_id,
                "image": f"images/{document_id}.jpg",
                "image_sha256": image_sha,
                "split": split,
                "width": 1280,
                "height": 1600,
            })
            truth.append(_truth_sample(document_id, split, image_sha, 20 * index))
        manifest = corpus_root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 3,
            "corpus_id": "synthetic-runtime-fixture",
            "synthetic_only": True,
            "samples": samples,
        }), encoding="utf-8")
        truth_path = root / "truth.jsonl"
        truth_path.write_text("".join(json.dumps(item, ensure_ascii=False) + "\n" for item in truth), encoding="utf-8")
        return manifest, truth_path, truth

    def _args(self, root: Path, manifest: Path, truth: Path):
        return build_parser().parse_args([
            "--corpus-manifest", str(manifest),
            "--truth-samples", str(truth),
            "--recognizer-result", str(root / "recognizer_result.json"),
            "--output-dir", str(root / "out"),
            "--recognizer-device", "cpu",
        ])

    def test_defaults_match_current_full_document_path(self) -> None:
        args = build_parser().parse_args([
            "--corpus-manifest", "/corpus/manifest.json",
            "--truth-samples", "/corpus/views/parsing/samples.jsonl",
            "--recognizer-result", "/run/recognizer_result.json",
            "--output-dir", "/run/runtime-parser",
        ])
        self.assertEqual(args.detector_model, "PP-OCRv5_mobile_det")
        self.assertEqual(args.detector_edge, 640)
        self.assertEqual(args.detector_threads, 1)
        self.assertEqual(args.recognizer_device, "gpu")
        self.assertIsNone(args.max_new_documents)

    def test_batch_rejects_nonpositive_document_chunk(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, truth_path, _ = self._source(root)
            args = self._args(root, manifest, truth_path)
            args.max_new_documents = 0
            with self.assertRaisesRegex(Exception, "max-new-documents"):
                run_synthetic_batch(args)

    def test_batch_runs_one_frozen_ocr_producer_and_builds_runtime_split_datasets(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, truth_path, truth = self._source(root)
            args = self._args(root, manifest, truth_path)
            producer = _producer()
            calls: list[str] = []

            def fake_run(full_args):
                document_id = Path(full_args.image).stem
                sample = next(item for item in truth if item["document_id"] == document_id)
                calls.append(document_id)
                output = Path(full_args.output_dir)
                output.mkdir(parents=True, exist_ok=True)
                profile = {**producer, "image_sha256": sample["image_sha256"]}
                result = {
                    "schema_version": 2,
                    "status": "ok",
                    "profile": profile,
                    "image": {
                        "path": full_args.image,
                        "sha256": sample["image_sha256"],
                        "width": 1280,
                        "height": 1600,
                        "source_width": 1280,
                        "source_height": 1600,
                    },
                    "stages": {"orientation": {"applied_rotation_degrees": 0}},
                    "regions": [
                        {"index": index, "text": node["text"], "recognition_score": 0.98, "polygon": node.get("natural_text_polygon") or node["polygon"]}
                        for index, node in enumerate(sample["nodes"], start=1)
                    ],
                    "text_lines": [node["text"] for node in sample["nodes"]],
                }
                (output / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                return result

            constructions = []

            class FakeRuntime:
                def __init__(self, runtime_args):
                    constructions.append(runtime_args)

                def run(self, *, image_path: Path, output_dir: Path):
                    full_args = type("Args", (), {"image": str(image_path), "output_dir": str(output_dir)})()
                    return fake_run(full_args)

            with patch("browser_ocr.finetune.synthetic_parser_data_cli.build_ocr_producer_profile", return_value=producer), patch(
                "browser_ocr.finetune.synthetic_parser_data_cli.FullDocumentRuntime", FakeRuntime
            ):
                result = run_synthetic_batch(args)

            self.assertEqual(result["status"], "ok")
            self.assertEqual(len(constructions), 1)
            self.assertEqual(calls, [item["document_id"] for item in truth])
            self.assertEqual(result["documents"], 3)
            for split in ("train", "val", "test"):
                dataset = load_parser_dataset(Path(result["datasets"][split]))
                self.assertEqual(len(dataset.documents), 1)
                self.assertEqual(dataset.documents[0]["split"], split)
                self.assertEqual(dataset.documents[0]["observation"]["kind"], "runtime_ocr")
                self.assertEqual(dataset.documents[0]["gold_rows"][0]["product_query"], "가나다정")

            with patch("browser_ocr.finetune.synthetic_parser_data_cli.build_ocr_producer_profile", return_value=producer), patch(
                "browser_ocr.finetune.synthetic_parser_data_cli.FullDocumentRuntime", side_effect=AssertionError("must reuse completed batch without model initialization")
            ):
                reused = run_synthetic_batch(args)
            self.assertEqual(reused, result)


    def test_batch_stops_cleanly_at_document_chunk_and_resumes_next_process(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, truth_path, truth = self._source(root)
            args = self._args(root, manifest, truth_path)
            args.max_new_documents = 2
            producer = _producer()
            calls: list[str] = []

            class FakeRuntime:
                def __init__(self, runtime_args):
                    pass

                def run(self, *, image_path: Path, output_dir: Path):
                    document_id = image_path.stem
                    sample = next(item for item in truth if item["document_id"] == document_id)
                    calls.append(document_id)
                    output_dir.mkdir(parents=True, exist_ok=True)
                    profile = {**producer, "image_sha256": sample["image_sha256"]}
                    result = {
                        "schema_version": 2,
                        "status": "ok",
                        "profile": profile,
                        "image": {
                            "path": str(image_path),
                            "sha256": sample["image_sha256"],
                            "width": 1280,
                            "height": 1600,
                            "source_width": 1280,
                            "source_height": 1600,
                        },
                        "stages": {"orientation": {"applied_rotation_degrees": 0}},
                        "regions": [
                            {"index": index, "text": node["text"], "recognition_score": 0.98, "polygon": node.get("natural_text_polygon") or node["polygon"]}
                            for index, node in enumerate(sample["nodes"], start=1)
                        ],
                        "text_lines": [node["text"] for node in sample["nodes"]],
                    }
                    (output_dir / "result.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
                    return result

            with patch("browser_ocr.finetune.synthetic_parser_data_cli.build_ocr_producer_profile", return_value=producer), patch(
                "browser_ocr.finetune.synthetic_parser_data_cli.FullDocumentRuntime", FakeRuntime
            ):
                partial = run_synthetic_batch(args)

            self.assertEqual(partial["status"], "partial")
            self.assertEqual(partial["completed"], 2)
            self.assertEqual(partial["remaining"], 1)
            self.assertEqual(calls, [truth[0]["document_id"], truth[1]["document_id"]])
            state = json.loads((root / "out" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "running")
            self.assertEqual(state["completed"], 2)
            self.assertFalse((root / "out" / "result.json").exists())

            calls.clear()
            with patch("browser_ocr.finetune.synthetic_parser_data_cli.build_ocr_producer_profile", return_value=producer), patch(
                "browser_ocr.finetune.synthetic_parser_data_cli.FullDocumentRuntime", FakeRuntime
            ):
                completed = run_synthetic_batch(args)

            self.assertEqual(completed["status"], "ok")
            self.assertEqual(calls, [truth[2]["document_id"]])
            state = json.loads((root / "out" / "state.json").read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "completed")
            self.assertEqual(state["completed"], 3)

    def test_batch_rejects_truth_and_corpus_document_mismatch_before_ocr(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, truth_path, _ = self._source(root)
            lines = truth_path.read_text(encoding="utf-8").splitlines()
            truth_path.write_text("\n".join(lines[:-1]) + "\n", encoding="utf-8")
            args = self._args(root, manifest, truth_path)
            with patch("browser_ocr.finetune.synthetic_parser_data_cli.build_ocr_producer_profile", return_value=_producer()), patch(
                "browser_ocr.finetune.synthetic_parser_data_cli.FullDocumentRuntime", side_effect=AssertionError("must not run OCR")
            ):
                with self.assertRaisesRegex(Exception, "document set"):
                    run_synthetic_batch(args)


if __name__ == "__main__":
    unittest.main()