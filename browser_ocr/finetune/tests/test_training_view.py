from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from browser_ocr.finetune.dataset import DatasetError, load_dataset
from browser_ocr.finetune.model_compat import audit_model_compatibility
from browser_ocr.finetune.training_view import prepare_unified_training_view


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    digest.update(path.read_bytes())
    return digest.hexdigest()


class UnifiedRecognitionTrainingViewTest(unittest.TestCase):
    def _source(self, root: Path) -> tuple[Path, Path, Path]:
        images = root / "images"
        images.mkdir(parents=True)
        rows = [
            ("train-clean", "가나다정", "train", ["critical-medication", "drug-exposure-seen", "difficulty-clean"]),
            ("train-ood", "라마바정", "train", ["critical-medication", "drug-exposure-seen", "difficulty-hard", "degradation-hard-ood"]),
            ("train-long", "가" * 26, "train", ["drug-exposure-seen", "difficulty-clean"]),
            ("val-clean", "사아자정", "val", ["critical-medication", "drug-exposure-unseen", "difficulty-medium"]),
            ("val-unknown", "○", "val", ["drug-exposure-unseen", "difficulty-clean"]),
            ("test-ood", "차카타정", "test", ["critical-medication", "drug-exposure-unseen", "difficulty-hard", "degradation-hard-ood"]),
        ]
        samples = []
        split_ids = {"train": [], "val": [], "test": []}
        for index, (sample_id, text, split, risk_tags) in enumerate(rows):
            image_path = images / f"{sample_id}.png"
            Image.new("L", (8, 8), 255 - index).save(image_path)
            split_ids[split].append(sample_id)
            samples.append({
                "id": sample_id,
                "image": f"images/{sample_id}.png",
                "image_sha256": sha256_file(image_path),
                "text": text,
                "origin": "synthetic",
                "document_type": "prescription",
                "document_id": f"doc-{sample_id}",
                "groups": {
                    "layout_family": "layout-a",
                    "source_family": "source-a",
                    "drug_family": f"drug-{sample_id}",
                },
                "semantic_tags": ["product"] if "critical-medication" in risk_tags else ["patient"],
                "risk_tags": risk_tags,
                "privacy": {"contains_patient_data": False, "deidentified": True},
                "provenance": {"source_id": "fixture", "license_id": "fixture"},
            })
        manifest = {
            "schema_version": 1,
            "dataset_id": "unified-recognition-fixture",
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
            "metadata": {
                "parent_corpus_id": "parent-fixture",
                "recognition_evaluation_policy": {"id": "severe-motion-downscale-jpeg-v1"},
                "drug_name_policy": {"assignment_seed": 161, "assignment_sha256": "f" * 64},
            },
        }
        manifest_path = root / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False) + "\n", encoding="utf-8")
        (root / "samples.jsonl").write_text(
            "".join(json.dumps(sample, ensure_ascii=False) + "\n" for sample in samples),
            encoding="utf-8",
        )
        dataset = load_dataset(manifest_path)
        split = {
            "schema_version": 1,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "document_id",
            "seed": 1161,
            "ratios": {"train": 0.8, "val": 0.1, "test": 0.1},
            "counts": {name: len(ids) for name, ids in split_ids.items()},
            "splits": split_ids,
            "assignment": "parent_document_split_v1",
        }
        split_path = root / "document-split.json"
        split_path.write_text(json.dumps(split, ensure_ascii=False) + "\n", encoding="utf-8")
        dictionary = root / "dict.txt"
        dictionary.write_text("\n".join(sorted(set("가나다라마바사아자차카타정"))) + "\n", encoding="utf-8")
        return manifest_path, split_path, dictionary

    def test_filters_incompatible_samples_and_train_ood_but_keeps_eval_ood(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            source = root / "source"
            source.mkdir()
            manifest, split, dictionary = self._source(source)
            output = root / "training-view"
            result = prepare_unified_training_view(
                manifest_path=manifest,
                split_path=split,
                output_dir=output,
                dictionary_path=dictionary,
                dictionary_sha256=sha256_file(dictionary),
                max_text_length=25,
                use_space_char=True,
            )

            self.assertEqual(result["status"], "completed")
            self.assertEqual(result["retained_counts"], {"train": 1, "val": 1, "test": 1})
            self.assertEqual(result["excluded"]["train_ood"], 1)
            self.assertEqual(result["excluded"]["overlength"], 1)
            self.assertEqual(result["excluded"]["unknown_character"], 1)
            self.assertEqual(result["excluded_sample_count"], 3)
            derived = load_dataset(output / "manifest.json")
            self.assertEqual({sample["id"] for sample in derived.samples}, {"train-clean", "val-clean", "test-ood"})
            self.assertEqual(
                audit_model_compatibility(
                    derived, dictionary, max_text_length=25, use_space_char=True,
                )["status"],
                "ok",
            )
            export = json.loads((output / "paddle" / "export.json").read_text(encoding="utf-8"))
            self.assertEqual(export["counts"], {"train": 1, "val": 1, "test": 1})
            self.assertEqual(export["data_dir"], str(output.resolve()))
            self.assertEqual(
                os.stat(source / "images" / "train-clean.png").st_ino,
                os.stat(output / "images" / "train-clean.png").st_ino,
            )
            repeated = prepare_unified_training_view(
                manifest_path=manifest,
                split_path=split,
                output_dir=output,
                dictionary_path=dictionary,
                dictionary_sha256=sha256_file(dictionary),
                max_text_length=25,
                use_space_char=True,
            )
            self.assertEqual(repeated, result)
            with self.assertRaisesRegex(DatasetError, "profile"):
                prepare_unified_training_view(
                    manifest_path=manifest,
                    split_path=split,
                    output_dir=output,
                    dictionary_path=dictionary,
                    dictionary_sha256=sha256_file(dictionary),
                    max_text_length=24,
                    use_space_char=True,
                )


if __name__ == "__main__":
    unittest.main()
