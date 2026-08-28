from __future__ import annotations

import json
import hashlib
import tempfile
import unittest
from contextlib import redirect_stdout
from copy import deepcopy
from io import StringIO
from pathlib import Path

from browser_ocr.document_parsing.parser_v5_dataset import (
    build_parser_v5_dataset,
    load_parser_v5_dataset,
)
from browser_ocr.document_parsing.parser_v5_dataset_cli import main
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile


class ParserV5DatasetTest(unittest.TestCase):
    def test_build_is_byte_deterministic_for_same_profiles_and_seed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = root / "first"
            second = root / "second"
            kwargs = {
                "dataset_id": "fixture-v5",
                "document_count": 8,
                "seed": 481,
                "world_profile": ParserWorldProfile(medication_count=(0, 3), distractor_section_count=(2, 5)),
                "observation_profile": ObservationProfile(false_positive_count=(1, 2)),
            }
            first_manifest = build_parser_v5_dataset(first, **kwargs)
            second_manifest = build_parser_v5_dataset(second, **kwargs)

            self.assertEqual(first_manifest.read_bytes(), second_manifest.read_bytes())
            self.assertEqual((first / "samples.jsonl").read_bytes(), (second / "samples.jsonl").read_bytes())
            loaded = load_parser_v5_dataset(first_manifest)
            self.assertEqual(len(loaded.samples), 8)
            self.assertEqual(loaded.dataset_id, "fixture-v5")

    def test_loader_rejects_sample_tampering_even_when_json_remains_valid(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=2, seed=9)
            samples_path = root / "samples.jsonl"
            lines = samples_path.read_text(encoding="utf-8").splitlines()
            sample = json.loads(lines[0])
            sample["observation"]["nodes"][0]["text"] += "X"
            lines[0] = json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            samples_path.write_text("\n".join(lines) + "\n", encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "samples SHA-256"):
                load_parser_v5_dataset(manifest)

    def test_loader_rejects_provenance_mismatch_after_manifest_hash_is_rebound(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=1, seed=17)
            samples_path = root / "samples.jsonl"
            sample = json.loads(samples_path.read_text(encoding="utf-8"))
            node = next(node for node in sample["observation"]["nodes"] if node["targets"])
            node["targets"][0]["semantic_role"] = "header"
            samples_path.write_text(
                json.dumps(sample, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            import hashlib

            manifest["samples_sha256"] = hashlib.sha256(samples_path.read_bytes()).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "target role disagrees"):
                load_parser_v5_dataset(manifest_path)

    def test_rebuild_reuses_identical_artifact_and_rejects_profile_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            first = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=3, seed=20)
            first_bytes = first.read_bytes()
            second = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=3, seed=20)
            self.assertEqual(first_bytes, second.read_bytes())

            with self.assertRaisesRegex(ValueError, "already contains a different Parser v5 dataset"):
                build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=4, seed=20)

    def test_loader_rejects_self_consistent_stale_generator_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=2, seed=64)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            generator = manifest["generation"]["generator"]
            generator["sources"]["parser_v5_world.py"] = "0" * 64
            generator["fingerprint"] = hashlib.sha256(
                json.dumps(
                    generator["sources"],
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "generator identity disagrees"):
                load_parser_v5_dataset(manifest_path)

    def test_loader_rejects_legacy_dataset_schema_instead_of_reusing_it(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifest_path = build_parser_v5_dataset(root, dataset_id="fixture-v5", document_count=1, seed=65)
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["schema_version"] = 1
            manifest["builder"] = "structured_world_observation_v1"
            manifest["generation"].pop("generator")
            manifest_path.write_text(
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "schema_version must be 2"):
                load_parser_v5_dataset(manifest_path)

    def test_cli_build_and_validate_emit_machine_readable_summary(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            output = StringIO()
            with redirect_stdout(output):
                code = main([
                    "build",
                    "--output-dir", str(root),
                    "--dataset-id", "cli-v5",
                    "--document-count", "5",
                    "--seed", "31",
                    "--json",
                ])
            self.assertEqual(code, 0)
            built = json.loads(output.getvalue())
            self.assertEqual(built["documents"], 5)

            output = StringIO()
            with redirect_stdout(output):
                code = main(["validate", "--manifest", built["manifest"], "--json"])
            self.assertEqual(code, 0)
            validated = json.loads(output.getvalue())
            self.assertEqual(validated["dataset_id"], "cli-v5")
            self.assertEqual(validated["documents"], 5)


if __name__ == "__main__":
    unittest.main()