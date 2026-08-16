from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import Dataset, DatasetError
from browser_ocr.finetune.selected_finetune import (
    build_selected_training_overrides,
    validate_selected_training_view,
)


class SelectedCheckpointFineTuneTest(unittest.TestCase):
    def _dataset(self) -> Dataset:
        samples = (
            {
                "id": "train-a",
                "image": "images/train-a.png",
                "text": "가나다정",
                "document_id": "doc-train",
                "risk_tags": ["drug-exposure-seen", "difficulty-clean"],
            },
            {
                "id": "val-b",
                "image": "images/val-b.png",
                "text": "라마바정",
                "document_id": "doc-val",
                "risk_tags": ["drug-exposure-unseen", "difficulty-medium"],
            },
            {
                "id": "test-c",
                "image": "images/test-c.png",
                "text": "사아자정",
                "document_id": "doc-test",
                "risk_tags": ["drug-exposure-unseen", "difficulty-hard", "degradation-hard-ood"],
            },
        )
        return Dataset(
            root=Path("/fixture"),
            manifest_path=Path("/fixture/manifest.json"),
            manifest={
                "dataset_id": "training-view-fixture",
                "metadata": {
                    "training_view_policy": {
                        "policy_id": "unified-recognition-training-view-v1",
                        "dictionary_sha256": "a" * 64,
                        "max_text_length": 25,
                        "use_space_char": True,
                        "train_excluded_risk_tag": "degradation-hard-ood",
                    }
                },
            },
            samples=samples,
            fingerprint="f" * 64,
        )

    def _export(self, root: Path, dataset: Dataset) -> Path:
        export = root / "paddle"
        export.mkdir()
        split = {
            "schema_version": 1,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "document_id",
            "seed": 1161,
            "counts": {"train": 1, "val": 1, "test": 1},
            "splits": {"train": ["train-a"], "val": ["val-b"], "test": ["test-c"]},
            "assignment": "parent_document_split_filtered_training_v1",
        }
        (export / "split.json").write_text(json.dumps(split) + "\n", encoding="utf-8")
        (export / "export.json").write_text(json.dumps({
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "document_id",
            "counts": split["counts"],
        }) + "\n", encoding="utf-8")
        for name, sample_id in (("train", "train-a"), ("val", "val-b"), ("test", "test-c")):
            sample = next(sample for sample in dataset.samples if sample["id"] == sample_id)
            (export / f"{name}.txt").write_text(f"{sample['image']}\t{sample['text']}\n", encoding="utf-8")
        return export

    def test_training_view_keeps_ood_out_of_optimization_but_allows_it_in_test(self) -> None:
        dataset = self._dataset()
        with tempfile.TemporaryDirectory() as raw:
            export = self._export(Path(raw), dataset)
            validated = validate_selected_training_view(
                dataset,
                export,
                expected_dictionary_sha256="a" * 64,
                expected_max_text_length=25,
                expected_use_space_char=True,
            )
            self.assertEqual(validated["counts"], {"train": 1, "val": 1, "test": 1})

            bad_samples = tuple(
                {**sample, "risk_tags": [*sample["risk_tags"], "degradation-hard-ood"]}
                if sample["id"] == "train-a" else sample
                for sample in dataset.samples
            )
            bad = Dataset(dataset.root, dataset.manifest_path, dataset.manifest, bad_samples, dataset.fingerprint)
            with self.assertRaisesRegex(DatasetError, "training split contains held-out OOD"):
                validate_selected_training_view(
                    bad,
                    export,
                    expected_dictionary_sha256="a" * 64,
                    expected_max_text_length=25,
                    expected_use_space_char=True,
                )

    def test_selected_checkpoint_is_the_initial_weight_not_a_resume_checkpoint(self) -> None:
        initial = build_selected_training_overrides(
            dataset_root=Path("/dataset"),
            export_dir=Path("/dataset/paddle"),
            initial_checkpoint=Path("/selected/best_accuracy.pdparams"),
            resume_checkpoint=None,
            output_dir=Path("/run/model"),
            batch_size=32,
            epochs=4,
            learning_rate=0.00005,
            warmup_epochs=1,
        )
        self.assertEqual(initial["Global.pretrained_model"], "/selected/best_accuracy.pdparams")
        self.assertNotIn("Global.checkpoints", initial)
        self.assertEqual(initial["Global.eval_batch_step"], [0, 1000])

        resumed = build_selected_training_overrides(
            dataset_root=Path("/dataset"),
            export_dir=Path("/dataset/paddle"),
            initial_checkpoint=Path("/selected/best_accuracy.pdparams"),
            resume_checkpoint=Path("/run/model/iter_epoch_2"),
            output_dir=Path("/run/model"),
            batch_size=32,
            epochs=4,
            learning_rate=0.00005,
            warmup_epochs=1,
        )
        self.assertEqual(resumed["Global.pretrained_model"], "/selected/best_accuracy.pdparams")
        self.assertEqual(resumed["Global.checkpoints"], "/run/model/iter_epoch_2")
        self.assertEqual(resumed["Global.eval_batch_step"], [0, 1000])


if __name__ == "__main__":
    unittest.main()
