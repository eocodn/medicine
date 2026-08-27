from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_calibration import build_parser_v5_calibration
from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_development_views import build_parser_v5_development_views
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile
from browser_ocr.document_parsing.parser_v5_validation_protocol import (
    authorize_parser_v5_holdout_open,
    freeze_parser_v5_candidate,
    load_parser_v5_candidate_freeze,
)
from browser_ocr.document_parsing.parser_v5_validation_cli import build_parser
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class ParserV5ValidationProtocolTest(unittest.TestCase):
    def test_cli_exposes_freeze_and_open_commands(self) -> None:
        parser = build_parser()
        freeze = parser.parse_args([
            "freeze",
            "--training-result", "/tmp/result.json",
            "--development-manifest", "/tmp/dev.json",
            "--calibration-artifact", "/tmp/calibration.json",
            "--output", "/tmp/freeze.json",
        ])
        self.assertEqual(freeze.command, "freeze")
        opened = parser.parse_args([
            "open",
            "--candidate-freeze", "/tmp/freeze.json",
            "--holdout-envelope", "/tmp/holdout.json",
            "--open-record", "/tmp/open.json",
            "--unlock-holdout-id", "sealed-001",
        ])
        self.assertEqual(opened.command, "open")

    def _candidate(self, root: Path):
        train_manifest = build_parser_v5_dataset(
            root / "train",
            dataset_id="v5-train-freeze",
            document_count=1,
            seed=1901,
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
        train = load_parser_v5_dataset(train_manifest)
        runtime_records = []
        for sample in train.samples:
            truth = sample["truth"]
            runtime_records.append({
                "document_id": truth["document_id"],
                "source_split": "train",
                "producer_fingerprint": "a" * 64,
                "nodes": [{
                    "index": index,
                    "text": span["text"],
                    "detector_confidence": 0.95,
                    "recognizer_confidence": 0.94,
                    "polygon": span["polygon"],
                } for index, span in enumerate(truth["spans"], start=1)],
            })
        calibration = build_parser_v5_calibration(
            dataset_manifest=train_manifest,
            runtime_records=runtime_records,
            output_path=root / "calibration.json",
        )
        dev_manifests = build_parser_v5_development_views(root / "dev", documents_per_view=1, seed=1902)
        checkpoint = root / "model.pdparams"
        checkpoint.write_bytes(b"frozen-parser-v5-checkpoint")
        dev_ids = {
            load_parser_v5_dataset(path).dataset_id: load_parser_v5_dataset(path).samples_sha256
            for path in dev_manifests.values()
        }
        profile = {
            "schema_version": 1,
            "model_id": "parser_v5_global_structured_v1",
            "train_datasets": [{"dataset_id": train.dataset_id, "samples_sha256": train.samples_sha256}],
            "validation_datasets": [
                {"dataset_id": dataset_id, "samples_sha256": samples_sha256}
                for dataset_id, samples_sha256 in sorted(dev_ids.items())
            ],
            "config": {"max_text_bytes": 96},
        }
        profile_sha256 = hashlib.sha256(
            json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        result = {
            "schema_version": 1,
            "status": "ok",
            "profile": profile,
            "profile_sha256": profile_sha256,
            "history": [],
            "best_epoch": 1,
            "best_validation": {"views": {dataset_id: {} for dataset_id in dev_ids}},
            "best_checkpoint": str(checkpoint),
            "best_checkpoint_sha256": _sha(checkpoint),
        }
        result_path = root / "result.json"
        result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
        return result_path, dev_manifests, calibration

    def test_freeze_binds_candidate_development_matrix_calibration_and_decode_policy(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, dev, calibration = self._candidate(root)
            freeze_path = freeze_parser_v5_candidate(
                training_result=result,
                development_manifests=list(dev.values()),
                calibration_artifact=calibration,
                output_path=root / "freeze.json",
            )
            freeze = load_parser_v5_candidate_freeze(freeze_path)
            self.assertEqual(len(freeze["development_views"]), 10)
            self.assertEqual(len(freeze["freeze_fingerprint"]), 64)
            self.assertEqual(freeze["checkpoint_sha256"], _sha(root / "model.pdparams"))
            self.assertIn("assignment_margin", freeze["decode_policy"])
            self.assertEqual(freeze["calibration_fingerprint"], json.loads(calibration.read_text())["calibration_fingerprint"])

    def test_holdout_open_requires_explicit_id_and_cannot_be_reused_by_modified_freeze(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, dev, calibration = self._candidate(root)
            freeze_path = freeze_parser_v5_candidate(
                training_result=result,
                development_manifests=list(dev.values()),
                calibration_artifact=calibration,
                output_path=root / "freeze.json",
            )
            envelope = root / "sealed-holdout.json"
            envelope.write_text(json.dumps({
                "schema_version": 1,
                "holdout_id": "sealed-2026-001",
                "samples_sha256": "b" * 64,
                "document_count": 40,
                "partition_fingerprint": "c" * 64,
            }), encoding="utf-8")
            open_record = root / "open-record.json"
            with self.assertRaisesRegex(ValueError, "unlock"):
                authorize_parser_v5_holdout_open(
                    candidate_freeze=freeze_path,
                    holdout_envelope=envelope,
                    open_record=open_record,
                    unlock_holdout_id="wrong-id",
                )
            first = authorize_parser_v5_holdout_open(
                candidate_freeze=freeze_path,
                holdout_envelope=envelope,
                open_record=open_record,
                unlock_holdout_id="sealed-2026-001",
            )
            second = authorize_parser_v5_holdout_open(
                candidate_freeze=freeze_path,
                holdout_envelope=envelope,
                open_record=open_record,
                unlock_holdout_id="sealed-2026-001",
            )
            self.assertEqual(first, second)

            modified = json.loads(freeze_path.read_text(encoding="utf-8"))
            modified["checkpoint_sha256"] = "e" * 64
            modified.pop("freeze_fingerprint")
            canonical = json.dumps(modified, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
            modified["freeze_fingerprint"] = hashlib.sha256(canonical).hexdigest()
            modified_path = root / "modified-freeze.json"
            modified_path.write_text(json.dumps(modified, ensure_ascii=False), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "fresh sealed holdout"):
                authorize_parser_v5_holdout_open(
                    candidate_freeze=modified_path,
                    holdout_envelope=envelope,
                    open_record=open_record,
                    unlock_holdout_id="sealed-2026-001",
                )


if __name__ == "__main__":
    unittest.main()