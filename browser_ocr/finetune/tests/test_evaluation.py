from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import Dataset, DatasetError
from browser_ocr.finetune.evaluation import evaluate_test_slices, prepare_test_slices


class SliceEvaluationPlanTest(unittest.TestCase):
    def _fixture(self, root: Path) -> tuple[Dataset, Path]:
        root.mkdir(parents=True, exist_ok=True)
        samples = (
            {
                "id": "a",
                "image": "images/a.png",
                "text": "1정",
                "semantic_tags": ["dose"],
                "risk_tags": ["exact_numeric"],
            },
            {
                "id": "b",
                "image": "images/b.png",
                "text": "문의 02-1234-5678",
                "semantic_tags": ["phone"],
                "risk_tags": ["hard_negative", "exact_numeric"],
            },
            {
                "id": "c",
                "image": "images/c.png",
                "text": "가나다정",
                "semantic_tags": ["product"],
                "risk_tags": ["small_print"],
            },
        )
        dataset = Dataset(
            root=root,
            manifest_path=root / "manifest.json",
            manifest={"dataset_id": "fixture"},
            samples=samples,
            fingerprint="f" * 64,
        )
        export = root / "export"
        export.mkdir()
        split = {
            "schema_version": 1,
            "dataset_id": "fixture",
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "layout_family",
            "splits": {"train": [], "val": [], "test": ["a", "b", "c"]},
        }
        (export / "split.json").write_text(json.dumps(split), encoding="utf-8")
        (export / "test.txt").write_text(
            "images/a.png\t1정\nimages/b.png\t문의 02-1234-5678\nimages/c.png\t가나다정\n",
            encoding="utf-8",
        )
        return dataset, export

    def test_prepare_test_slices_writes_overlapping_semantic_and_risk_labels(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset, export = self._fixture(root)
            plan = prepare_test_slices(
                dataset=dataset,
                export_dir=export,
                output_dir=root / "slices",
                expected_group_by="layout_family",
                required_semantic_tags=["dose", "phone", "product"],
                required_risk_tags=["exact_numeric", "hard_negative", "small_print"],
            )

            self.assertEqual(plan["test_count"], 3)
            self.assertEqual(plan["semantic"]["phone"]["count"], 1)
            self.assertEqual(plan["risk"]["exact_numeric"]["count"], 2)
            exact_path = Path(plan["risk"]["exact_numeric"]["label_file"])
            self.assertEqual(
                exact_path.read_text(encoding="utf-8"),
                "images/a.png\t1정\nimages/b.png\t문의 02-1234-5678\n",
            )

    def test_prepare_test_slices_fails_if_export_labels_or_required_coverage_disagree(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            dataset, export = self._fixture(root)
            (export / "test.txt").write_text("images/a.png\tWRONG\n", encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "test labels do not match"):
                prepare_test_slices(
                    dataset=dataset,
                    export_dir=export,
                    output_dir=root / "slices",
                    expected_group_by="layout_family",
                    required_semantic_tags=["dose"],
                    required_risk_tags=["exact_numeric"],
                )

            dataset, export = self._fixture(root / "second")
            with self.assertRaisesRegex(DatasetError, "required semantic slice is empty: duration"):
                prepare_test_slices(
                    dataset=dataset,
                    export_dir=export,
                    output_dir=root / "second" / "slices",
                    expected_group_by="layout_family",
                    required_semantic_tags=["duration"],
                    required_risk_tags=["exact_numeric"],
                )

    def test_evaluate_test_slices_records_pretrained_best_and_delta(self) -> None:
        calls: list[tuple[str, str, str]] = []

        def evaluate(path: Path, model: str, stem: str) -> dict[str, float]:
            calls.append((path.name, model, stem))
            if model == "pretrained":
                return {"acc": 0.5, "norm_edit_dis": 0.9, "fps": 100.0}
            return {"acc": 0.75, "norm_edit_dis": 0.95, "fps": 90.0}

        plan = {
            "semantic": {"dose": {"count": 2, "label_file": "/tmp/semantic-dose.txt"}},
            "risk": {"exact_numeric": {"count": 3, "label_file": "/tmp/risk-exact_numeric.txt"}},
        }
        result = evaluate_test_slices(plan, evaluate=evaluate)
        self.assertEqual(result["semantic"]["dose"]["delta"]["acc"], 0.25)
        self.assertAlmostEqual(result["risk"]["exact_numeric"]["delta"]["norm_edit_dis"], 0.05)
        self.assertEqual(len(calls), 4)


if __name__ == "__main__":
    unittest.main()