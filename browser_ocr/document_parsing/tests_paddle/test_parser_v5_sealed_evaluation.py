from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import paddle

from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_sealed_evaluation_paddle import evaluate_parser_v5_sealed_holdout
from browser_ocr.document_parsing.parser_v5_sealed_evaluation_cli import build_parser
from browser_ocr.document_parsing.parser_v5_validation_protocol import (
    authorize_parser_v5_holdout_open,
)
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile
from browser_ocr.document_parsing.tests_paddle.parser_v5_fixture import build_frozen_candidate


class ParserV5SealedEvaluationTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_cli_requires_all_authorization_and_holdout_inputs(self) -> None:
        args = build_parser().parse_args([
            "--candidate-freeze", "/tmp/freeze.json",
            "--training-result", "/tmp/result.json",
            "--holdout-envelope", "/tmp/envelope.json",
            "--open-record", "/tmp/open.json",
            "--holdout-manifest", "/tmp/holdout/manifest.json",
            "--output", "/tmp/evaluation.json",
        ])
        self.assertEqual(args.open_record, "/tmp/open.json")

    def test_evaluation_requires_matching_open_record_and_hash_bound_holdout(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, freeze = build_frozen_candidate(root)
            holdout_manifest = build_parser_v5_dataset(
                root / "holdout",
                dataset_id="sealed-eval-fixture",
                document_count=2,
                seed=4499,
                world_profile=ParserWorldProfile(medication_count=(0, 2), distractor_section_count=(2, 3)),
            )
            holdout = load_parser_v5_dataset(holdout_manifest)
            envelope = root / "holdout-envelope.json"
            envelope.write_text(json.dumps({
                "schema_version": 1,
                "holdout_id": "sealed-eval-001",
                "samples_sha256": holdout.samples_sha256,
                "document_count": len(holdout.samples),
                "partition_fingerprint": "c" * 64,
            }), encoding="utf-8")
            open_record = root / "open.json"
            authorize_parser_v5_holdout_open(
                candidate_freeze=freeze,
                holdout_envelope=envelope,
                open_record=open_record,
                unlock_holdout_id="sealed-eval-001",
            )

            output = root / "evaluation.json"
            evaluation = evaluate_parser_v5_sealed_holdout(
                candidate_freeze=freeze,
                training_result=result,
                holdout_envelope=envelope,
                open_record=open_record,
                holdout_manifest=holdout_manifest,
                output_path=output,
            )
            self.assertEqual(evaluation["status"], "ok")
            self.assertEqual(evaluation["documents"], 2)
            self.assertEqual(evaluation["holdout_id"], "sealed-eval-001")
            self.assertEqual(evaluation["holdout_samples_sha256"], holdout.samples_sha256)
            self.assertEqual(len(evaluation["evaluation_fingerprint"]), 64)
            self.assertEqual(evaluate_parser_v5_sealed_holdout(
                candidate_freeze=freeze,
                training_result=result,
                holdout_envelope=envelope,
                open_record=open_record,
                holdout_manifest=holdout_manifest,
                output_path=output,
            ), evaluation)

            tampered = json.loads(open_record.read_text(encoding="utf-8"))
            tampered["candidate_freeze_fingerprint"] = "d" * 64
            tampered_path = root / "tampered-open.json"
            tampered_path.write_text(json.dumps(tampered), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "open record"):
                evaluate_parser_v5_sealed_holdout(
                    candidate_freeze=freeze,
                    training_result=result,
                    holdout_envelope=envelope,
                    open_record=tampered_path,
                    holdout_manifest=holdout_manifest,
                    output_path=root / "bad.json",
                )


if __name__ == "__main__":
    unittest.main()