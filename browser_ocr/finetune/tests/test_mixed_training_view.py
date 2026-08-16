from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from browser_ocr.finetune.dataset import DatasetError, load_dataset
from browser_ocr.finetune.mixed_training_view import (
    MIXED_TRAINING_VIEW_POLICY_ID,
    prepare_mixed_training_view,
)
from browser_ocr.finetune.selected_finetune import validate_selected_training_view


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def sample(sample_id: str, text: str, image: str, image_sha: str, *, exposure: str) -> dict:
    return {
        "id": sample_id,
        "image": image,
        "image_sha256": image_sha,
        "text": text,
        "origin": "synthetic",
        "document_type": "prescription",
        "document_id": f"doc-{sample_id}",
        "groups": {
            "layout_family": f"layout-{sample_id}",
            "source_family": f"source-{sample_id}",
            "drug_family": f"drug-{sample_id}",
        },
        "semantic_tags": ["product"],
        "risk_tags": [f"drug-exposure-{exposure}", "difficulty-clean", "critical-medication"],
        "privacy": {"contains_patient_data": False, "deidentified": True},
        "provenance": {"source_id": "fixture", "license_id": "fixture"},
    }


class MixedRecognitionTrainingViewTest(unittest.TestCase):
    def _write_dataset(self, root: Path, dataset_id: str, rows: list[tuple[str, str, str]], metadata: dict | None = None) -> Path:
        (root / "images").mkdir(parents=True)
        samples = []
        for index, (sample_id, text, exposure) in enumerate(rows):
            image = root / "images" / f"{sample_id}.png"
            Image.new("L", (8, 8), 250 - index).save(image)
            samples.append(sample(sample_id, text, f"images/{sample_id}.png", sha256_file(image), exposure=exposure))
        (root / "samples.jsonl").write_text(
            "".join(json.dumps(item, ensure_ascii=False) + "\n" for item in samples), encoding="utf-8"
        )
        manifest = root / "manifest.json"
        manifest.write_text(json.dumps({
            "schema_version": 1,
            "dataset_id": dataset_id,
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
            "metadata": metadata or {},
        }, ensure_ascii=False) + "\n", encoding="utf-8")
        return manifest

    def _fixture(self, root: Path) -> tuple[Path, Path, Path, Path, Path]:
        historical_root = root / "historical"
        historical_root.mkdir()
        historical_manifest = self._write_dataset(
            historical_root,
            "historical-fixture",
            [
                ("h-train-a", "가나다정", "seen"),
                ("h-train-b", "라마바정", "seen"),
                ("h-val", "사아자정", "seen"),
                ("h-test", "차카타정", "seen"),
            ],
        )
        historical_dataset = load_dataset(historical_manifest)
        historical_split = {
            "schema_version": 1,
            "dataset_id": historical_dataset.manifest["dataset_id"],
            "dataset_fingerprint": historical_dataset.fingerprint,
            "group_by": "source_family",
            "seed": 112,
            "counts": {"train": 2, "val": 1, "test": 1},
            "splits": {"train": ["h-train-a", "h-train-b"], "val": ["h-val"], "test": ["h-test"]},
            "assignment": "stable_family_hash_v1",
        }
        historical_split_path = historical_root / "split.json"
        historical_split_path.write_text(json.dumps(historical_split) + "\n", encoding="utf-8")
        historical_split_sha = sha256_file(historical_split_path)

        unified_root = root / "unified"
        unified_root.mkdir()
        unified_manifest = self._write_dataset(
            unified_root,
            "unified-training-fixture",
            [
                ("u-train", "가나다정", "seen"),
                ("u-val", "파하정", "unseen"),
                ("u-test", "거너정", "unseen"),
            ],
            metadata={
                "training_view_policy": {
                    "policy_id": "unified-recognition-training-view-v1",
                    "profile_sha256": "9" * 64,
                    "dictionary_sha256": "a" * 64,
                    "max_text_length": 25,
                    "use_space_char": True,
                    "train_excluded_risk_tag": "degradation-hard-ood",
                },
                "drug_name_policy": {
                    "historical_exposure": {
                        "source_dataset_id": historical_dataset.manifest["dataset_id"],
                        "source_dataset_fingerprint": historical_dataset.fingerprint,
                        "source_train_split_sha256": historical_split_sha,
                        "source_train_sample_count": 2,
                    }
                },
            },
        )
        unified_dataset = load_dataset(unified_manifest)
        unified_export = unified_root / "paddle"
        unified_export.mkdir()
        unified_split = {
            "schema_version": 1,
            "dataset_id": unified_dataset.manifest["dataset_id"],
            "dataset_fingerprint": unified_dataset.fingerprint,
            "group_by": "document_id",
            "seed": 1161,
            "ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
            "counts": {"train": 1, "val": 1, "test": 1},
            "splits": {"train": ["u-train"], "val": ["u-val"], "test": ["u-test"]},
            "assignment": "parent_document_split_filtered_training_v1",
        }
        (unified_export / "split.json").write_text(json.dumps(unified_split) + "\n", encoding="utf-8")
        (unified_export / "export.json").write_text(json.dumps({
            "dataset_id": unified_dataset.manifest["dataset_id"],
            "dataset_fingerprint": unified_dataset.fingerprint,
            "group_by": "document_id",
            "counts": unified_split["counts"],
        }) + "\n", encoding="utf-8")
        by_id = {item["id"]: item for item in unified_dataset.samples}
        for name, ids in unified_split["splits"].items():
            (unified_export / f"{name}.txt").write_text(
                "".join(f"{by_id[sample_id]['image']}\t{by_id[sample_id]['text']}\n" for sample_id in ids),
                encoding="utf-8",
            )

        dictionary = root / "dict.txt"
        dictionary.write_text("\n".join(sorted(set("가나다정라마바사아자차카타파하거너"))) + "\n", encoding="utf-8")
        return historical_manifest, historical_split_path, unified_manifest, unified_export, dictionary

    def test_mixed_view_adds_only_historical_train_and_keeps_unified_eval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            historical_manifest, historical_split, unified_manifest, unified_export, dictionary = self._fixture(root)
            output = root / "mixed"
            result = prepare_mixed_training_view(
                historical_manifest_path=historical_manifest,
                historical_split_path=historical_split,
                unified_manifest_path=unified_manifest,
                unified_export_dir=unified_export,
                output_dir=output,
                dictionary_path=dictionary,
                dictionary_sha256=sha256_file(dictionary),
                max_text_length=25,
                use_space_char=True,
            )
            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["retained_counts"], {"train": 3, "val": 1, "test": 1})
            mixed = load_dataset(output / "manifest.json")
            self.assertEqual(mixed.manifest["metadata"]["training_view_policy"]["policy_id"], MIXED_TRAINING_VIEW_POLICY_ID)
            split = json.loads((output / "paddle" / "split.json").read_text(encoding="utf-8"))
            self.assertTrue(all(sample_id.startswith("hist-") or sample_id.startswith("unified-") for sample_id in split["splits"]["train"]))
            self.assertTrue(all(sample_id.startswith("unified-") for sample_id in split["splits"]["val"] + split["splits"]["test"]))
            self.assertFalse(any(sample_id.startswith("hist-") for sample_id in split["splits"]["val"] + split["splits"]["test"]))

            # Historical images are copied because real execution mounts that source read-only/cross-device.
            hist_source = root / "historical" / "images" / "h-train-a.png"
            hist_mixed = output / "images" / "historical" / "h-train-a.png"
            self.assertNotEqual(os.stat(hist_source).st_ino, os.stat(hist_mixed).st_ino)
            # Unified images stay on the current workspace filesystem and are hard-linked.
            unified_source = root / "unified" / "images" / "u-train.png"
            unified_mixed = output / "images" / "unified" / "u-train.png"
            self.assertEqual(os.stat(unified_source).st_ino, os.stat(unified_mixed).st_ino)

            validated = validate_selected_training_view(
                mixed,
                output / "paddle",
                expected_dictionary_sha256=sha256_file(dictionary),
                expected_max_text_length=25,
                expected_use_space_char=True,
            )
            self.assertEqual(validated["counts"], {"train": 3, "val": 1, "test": 1})

    def test_mixed_view_fails_when_unified_holdout_is_not_bound_to_historical_train_split(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            historical_manifest, historical_split, unified_manifest, unified_export, dictionary = self._fixture(root)
            historical_body = json.loads(historical_split.read_text(encoding="utf-8"))
            # Semantically identical split, different authoritative bytes/hash.
            historical_split.write_text(json.dumps(historical_body, indent=2) + "\n", encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "historical exposure.*split"):
                prepare_mixed_training_view(
                    historical_manifest_path=historical_manifest,
                    historical_split_path=historical_split,
                    unified_manifest_path=unified_manifest,
                    unified_export_dir=unified_export,
                    output_dir=root / "mixed",
                    dictionary_path=dictionary,
                    dictionary_sha256=sha256_file(dictionary),
                    max_text_length=25,
                    use_space_char=True,
                )


if __name__ == "__main__":
    unittest.main()
