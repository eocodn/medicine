from __future__ import annotations

import json
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_calibration import (
    build_parser_v5_calibration,
    load_parser_v5_calibration,
)
from browser_ocr.document_parsing.parser_v5_calibration_cli import main
from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile


class ParserV5CalibrationTest(unittest.TestCase):
    def _fixture(self, root: Path):
        manifest = build_parser_v5_dataset(
            root / "dataset",
            dataset_id="calibration-train",
            document_count=2,
            seed=901,
            world_profile=ParserWorldProfile(medication_count=(1, 1), distractor_section_count=(1, 1)),
            observation_profile=ObservationProfile(
                text_corruption_rate=0,
                drop_rate=0,
                duplicate_rate=0,
                split_rate=0,
                merge_rate=0,
                geometry_jitter=0,
                false_positive_count=(0, 0),
                reading_order_shuffle_rate=0,
            ),
        )
        dataset = load_parser_v5_dataset(manifest)
        records = []
        for sample in dataset.samples:
            truth = sample["truth"]
            nodes = []
            for index, span in enumerate(truth["spans"], start=1):
                nodes.append({
                    "index": index,
                    "text": span["text"],
                    "detector_confidence": 0.95,
                    "recognizer_confidence": 0.93,
                    "polygon": span["polygon"],
                })
            records.append({
                "document_id": truth["document_id"],
                "source_split": "train",
                "producer_fingerprint": "a" * 64,
                "nodes": nodes,
            })
        return manifest, records

    def test_exact_runtime_observations_produce_zero_error_calibration(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, records = self._fixture(root)
            artifact = build_parser_v5_calibration(
                dataset_manifest=manifest,
                runtime_records=records,
                output_path=root / "calibration.json",
            )
            loaded = load_parser_v5_calibration(artifact)
            summary = loaded["summary"]
            self.assertEqual(summary["drop_rate"], 0.0)
            self.assertEqual(summary["split_rate"], 0.0)
            self.assertEqual(summary["merge_rate"], 0.0)
            self.assertEqual(summary["false_positive_rate"], 0.0)
            self.assertEqual(summary["recognition_error_rate"], 0.0)
            self.assertEqual(loaded["producer_fingerprint"], "a" * 64)

    def test_calibration_detects_drop_merge_false_positive_and_text_error(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, records = self._fixture(root)
            first = records[0]
            removed = first["nodes"].pop(0)
            first["nodes"][0]["text"] += "X"
            left = first["nodes"].pop(1)
            right = first["nodes"].pop(1)
            xs = [point[0] for node in (left, right) for point in node["polygon"]]
            ys = [point[1] for node in (left, right) for point in node["polygon"]]
            first["nodes"].append({
                "index": 999,
                "text": f"{left['text']} {right['text']}",
                "detector_confidence": 0.81,
                "recognizer_confidence": 0.79,
                "polygon": [[min(xs), min(ys)], [max(xs), min(ys)], [max(xs), max(ys)], [min(xs), max(ys)]],
            })
            first["nodes"].append({
                "index": 1000,
                "text": "잡음123",
                "detector_confidence": 0.42,
                "recognizer_confidence": 0.31,
                "polygon": [[5, 5], [45, 5], [45, 25], [5, 25]],
            })
            self.assertTrue(removed)
            artifact = build_parser_v5_calibration(
                dataset_manifest=manifest,
                runtime_records=records,
                output_path=root / "calibration.json",
            )
            summary = load_parser_v5_calibration(artifact)["summary"]
            self.assertGreater(summary["drop_rate"], 0.0)
            self.assertGreater(summary["merge_rate"], 0.0)
            self.assertGreater(summary["false_positive_rate"], 0.0)
            self.assertGreater(summary["recognition_error_rate"], 0.0)

    def test_calibration_rejects_non_train_records_and_mixed_producers(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, records = self._fixture(root)
            records[0]["source_split"] = "val"
            with self.assertRaisesRegex(ValueError, "train-only"):
                build_parser_v5_calibration(
                    dataset_manifest=manifest,
                    runtime_records=records,
                    output_path=root / "bad.json",
                )
            records[0]["source_split"] = "train"
            records[1]["producer_fingerprint"] = "b" * 64
            with self.assertRaisesRegex(ValueError, "producer"):
                build_parser_v5_calibration(
                    dataset_manifest=manifest,
                    runtime_records=records,
                    output_path=root / "mixed.json",
                )

    def test_cli_builds_machine_readable_calibration_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest, records = self._fixture(root)
            records_path = root / "runtime.jsonl"
            records_path.write_text(
                "".join(json.dumps(record, ensure_ascii=False) + "\n" for record in records),
                encoding="utf-8",
            )
            output = StringIO()
            destination = root / "calibration.json"
            with redirect_stdout(output):
                code = main([
                    "--dataset-manifest", str(manifest),
                    "--runtime-records", str(records_path),
                    "--output", str(destination),
                    "--json",
                ])
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ok")
            self.assertEqual(payload["document_count"], 2)
            self.assertEqual(payload["output"], str(destination.resolve()))


if __name__ == "__main__":
    unittest.main()