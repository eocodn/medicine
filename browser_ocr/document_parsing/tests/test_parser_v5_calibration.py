from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_calibration import load_parser_v5_calibration
from browser_ocr.document_parsing.parser_v5_calibration_cli import build_parser


def _artifact(path: Path) -> Path:
    source_identity = {
        "schema_version": 1,
        "producer_fingerprint": "a" * 64,
        "document_count": 3,
        "oracle_dataset": {"samples_sha256": "b" * 64},
        "runtime_dataset": {"samples_sha256": "c" * 64},
    }
    source_fingerprint = hashlib.sha256(
        json.dumps(source_identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    payload = {
        "schema_version": 2,
        "source_identity": source_identity,
        "source_fingerprint": source_fingerprint,
        "producer_fingerprint": "a" * 64,
        "document_count": 3,
        "summary": {"drop_rate": 0.1},
        "recommended_observation_profile": {
            "text_corruption_rate": 0.1,
            "drop_rate": 0.1,
            "duplicate_rate": 0.0,
            "split_rate": 0.0,
            "merge_rate": 0.0,
            "geometry_jitter": 0.0,
            "false_positive_count": [0, 1],
            "reading_order_shuffle_rate": 0.0,
        },
    }
    value = {
        **payload,
        "calibration_fingerprint": hashlib.sha256(
            json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
    }
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    return path


class ParserV5CalibrationTest(unittest.TestCase):
    def test_cli_requires_independent_oracle_runtime_and_batch_sources(self) -> None:
        args = build_parser().parse_args([
            "--oracle-manifest", "/tmp/oracle.json",
            "--runtime-manifest", "/tmp/runtime.json",
            "--runtime-batch-result", "/tmp/result.json",
            "--output", "/tmp/calibration.json",
            "--json",
        ])
        self.assertEqual(args.oracle_manifest, "/tmp/oracle.json")
        self.assertEqual(args.runtime_manifest, "/tmp/runtime.json")
        self.assertEqual(args.runtime_batch_result, "/tmp/result.json")

    def test_loader_binds_source_and_rejects_rebound_or_tampered_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            path = _artifact(Path(raw) / "calibration.json")
            loaded = load_parser_v5_calibration(path)
            self.assertEqual(loaded["schema_version"], 2)
            self.assertEqual(loaded["document_count"], 3)

            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["source_identity"]["document_count"] = 4
            path.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source fingerprint"):
                load_parser_v5_calibration(path)

            path = _artifact(Path(raw) / "calibration-2.json")
            tampered = json.loads(path.read_text(encoding="utf-8"))
            tampered["summary"]["drop_rate"] = 0.9
            path.write_text(json.dumps(tampered, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "calibration fingerprint"):
                load_parser_v5_calibration(path)


if __name__ == "__main__":
    unittest.main()