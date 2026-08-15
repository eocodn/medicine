from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from browser_ocr.finetune.dataset import Dataset, DatasetError
from browser_ocr.finetune.fixed_eval import (
    audit_fixed_eval_reference_compatibility,
    build_fixed_eval_plan,
    evaluate_fixed_predictions,
)


class FixedRecognitionEvaluationTest(unittest.TestCase):
    def _dataset(self) -> Dataset:
        rows = [
            ("a", "product", "seen", "clean", False, ["augmentation-jpeg-compression"], "가나다정"),
            ("b", "dose", "seen", "medium", False, ["augmentation-downscale"], "1정"),
            ("c", "frequency", "unseen", "clean", False, [], "1일 3회"),
            ("d", "duration", "unseen", "medium", False, ["augmentation-jpeg-compression"], "7일분"),
            ("e", "product", "unseen", "hard", False, ["augmentation-motion-blur", "augmentation-downscale", "augmentation-jpeg-compression"], "라마바정"),
            ("f", "dose", "unseen", "hard", True, ["augmentation-motion-blur", "augmentation-downscale", "augmentation-jpeg-compression"], "0.5정"),
            ("g", "frequency", "unseen", "hard", True, ["augmentation-motion-blur", "augmentation-downscale", "augmentation-jpeg-compression"], "하루 2회"),
            ("h", "duration", "seen", "hard", False, ["augmentation-motion-blur", "augmentation-jpeg-compression"], "5일분"),
        ]
        samples = []
        for sample_id, role, exposure, difficulty, ood, augmentations, text in rows:
            risk = [
                "critical-medication",
                f"drug-exposure-{exposure}",
                f"difficulty-{difficulty}",
                *augmentations,
            ]
            if ood:
                risk.append("degradation-hard-ood")
            samples.append(
                {
                    "id": sample_id,
                    "image": f"images/{sample_id}.png",
                    "text": text,
                    "document_id": f"doc-{sample_id}",
                    "semantic_tags": [role],
                    "risk_tags": risk,
                }
            )
        return Dataset(
            root=Path("/fixture"),
            manifest_path=Path("/fixture/manifest.json"),
            manifest={
                "dataset_id": "fixed-eval-fixture",
                "metadata": {
                    "recognition_evaluation_policy": {"id": "severe-motion-downscale-jpeg-v1"},
                    "drug_name_policy": {"assignment_seed": 161, "assignment_sha256": "f" * 64},
                },
            },
            samples=tuple(samples),
            fingerprint="e" * 64,
        )

    def test_plan_separates_seen_unseen_familiar_and_hard_ood_without_reinference(self) -> None:
        dataset = self._dataset()
        plan = build_fixed_eval_plan(dataset, minimum_required_count=1)

        self.assertEqual(plan["policy_id"], "fixed-recognition-eval-v2")
        self.assertEqual(plan["drug_assignment_seed"], 161)
        self.assertEqual(plan["slices"]["seen-drug-unseen-image"]["sample_ids"], ["a", "b", "h"])
        self.assertEqual(plan["slices"]["unseen-drug-familiar-degradation"]["sample_ids"], ["c", "d"])
        self.assertEqual(plan["slices"]["unseen-drug-hard-in-domain"]["sample_ids"], ["e"])
        self.assertEqual(plan["slices"]["unseen-drug-hard-ood"]["sample_ids"], ["f", "g"])
        self.assertEqual(plan["slices"]["product-seen"]["sample_ids"], ["a"])
        self.assertEqual(plan["slices"]["product-unseen"]["sample_ids"], ["e"])
        self.assertIn("augmentation-component-motion-blur", plan["slices"])
        self.assertTrue(any(name.startswith("augmentation-combination-") for name in plan["slices"]))

    def test_plan_fails_closed_when_required_ood_support_is_missing(self) -> None:
        dataset = self._dataset()
        stripped = tuple({**sample, "risk_tags": [tag for tag in sample["risk_tags"] if tag != "degradation-hard-ood"]} for sample in dataset.samples)
        without_ood = Dataset(dataset.root, dataset.manifest_path, dataset.manifest, stripped, dataset.fingerprint)
        with self.assertRaisesRegex(DatasetError, "unseen-drug-hard-ood"):
            build_fixed_eval_plan(without_ood, minimum_required_count=1)

    def test_metrics_report_exact_and_normalized_edit_similarity_for_every_slice(self) -> None:
        dataset = self._dataset()
        plan = build_fixed_eval_plan(dataset, minimum_required_count=1)
        predictions = {
            sample["id"]: {"text": sample["text"], "score": 0.99}
            for sample in dataset.samples
        }
        predictions["f"] = {"text": "0.6정", "score": 0.8}
        result = evaluate_fixed_predictions(dataset, predictions, plan)

        self.assertEqual(result["overall"]["count"], 8)
        self.assertEqual(result["overall"]["exact_count"], 7)
        self.assertEqual(result["overall"]["exact_accuracy"], 0.875)
        self.assertLess(result["overall"]["normalized_edit_similarity"], 1.0)
        self.assertEqual(result["slices"]["unseen-drug-hard-ood"]["count"], 2)
        self.assertEqual(result["slices"]["unseen-drug-hard-ood"]["exact_accuracy"], 0.5)

        with self.assertRaisesRegex(DatasetError, "prediction coverage"):
            evaluate_fixed_predictions(dataset, {"a": predictions["a"]}, plan)

    def test_reference_compatibility_allows_unsupported_noncritical_context_only(self) -> None:
        dataset = self._dataset()
        context = {
            "id": "context",
            "image": "images/context.png",
            "text": "○" + "가" * 30,
            "document_id": "doc-context",
            "semantic_tags": ["patient"],
            "risk_tags": ["drug-exposure-seen", "difficulty-clean"],
        }
        extended = Dataset(
            dataset.root,
            dataset.manifest_path,
            dataset.manifest,
            (*dataset.samples, context),
            dataset.fingerprint,
        )
        characters = sorted(set("".join(sample["text"] for sample in dataset.samples).replace(" ", "")))
        with tempfile.TemporaryDirectory() as raw:
            dictionary = Path(raw) / "dict.txt"
            dictionary.write_text("\n".join(characters) + "\n", encoding="utf-8")
            compatibility = audit_fixed_eval_reference_compatibility(
                extended,
                dictionary,
                max_text_length=25,
                use_space_char=True,
            )

        self.assertEqual(compatibility["overall"]["status"], "incompatible")
        self.assertEqual(compatibility["overall"]["overlength_sample_count"], 1)
        self.assertEqual(compatibility["overall"]["unknown_characters"], {"○": 1})
        self.assertEqual(compatibility["critical"]["status"], "ok")

    def test_reference_compatibility_rejects_unsupported_critical_medication(self) -> None:
        dataset = self._dataset()
        bad_samples = tuple(
            {**sample, "text": "가" * 26} if sample["id"] == "a" else sample
            for sample in dataset.samples
        )
        bad = Dataset(dataset.root, dataset.manifest_path, dataset.manifest, bad_samples, dataset.fingerprint)
        characters = sorted(set("".join(sample["text"] for sample in bad.samples).replace(" ", "")))
        with tempfile.TemporaryDirectory() as raw:
            dictionary = Path(raw) / "dict.txt"
            dictionary.write_text("\n".join(characters) + "\n", encoding="utf-8")
            compatibility = audit_fixed_eval_reference_compatibility(
                bad,
                dictionary,
                max_text_length=25,
                use_space_char=True,
            )

        self.assertEqual(compatibility["critical"]["status"], "incompatible")
        self.assertEqual(compatibility["critical"]["overlength_sample_count"], 1)


if __name__ == "__main__":
    unittest.main()