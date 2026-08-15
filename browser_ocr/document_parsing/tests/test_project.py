from __future__ import annotations

import hashlib
import json
import unittest
from pathlib import Path

from browser_ocr.document_parsing.contract import load_corpus


CORPUS = Path("browser_ocr/document_parsing/corpus/manifest.json")
LEARNED_RESULT = Path("browser_ocr/document_parsing/results/learned-layout-v2-summary.json")
CONTEXT_RESULT = Path("browser_ocr/document_parsing/results/learned-layout-context-v3-summary.json")
CONTEXT_MODEL = Path("browser_ocr/document_parsing/models/hashed-layout-context-v3.json")


class ProjectCorpusTest(unittest.TestCase):
    def test_seed_corpus_covers_historical_structure_failures(self) -> None:
        corpus = load_corpus(CORPUS)
        self.assertGreaterEqual(len(corpus.cases), 7)
        scenario_tags = {tag for case in corpus.cases for tag in case.scenario_tags}
        risk_tags = {tag for case in corpus.cases for tag in case.risk_tags}
        self.assertTrue(
            {
                "prescription_table",
                "medication_bag",
                "shared_regimen",
                "split_boxes",
                "skewed_geometry",
                "repeated_product",
            }.issubset(scenario_tags)
        )
        self.assertTrue(
            {
                "row_association",
                "shared_scope",
                "header_reconstruction",
                "product_reconstruction",
            }.issubset(risk_tags)
        )

    def test_learned_layout_result_is_not_promoted_without_unseen_layout_safety(self) -> None:
        result = json.loads(LEARNED_RESULT.read_text(encoding="utf-8"))
        self.assertEqual(result["decision"], "do_not_promote")
        self.assertFalse(result["real_data_evaluated"])
        capture = result["capture_profile_cv"]
        layout = result["layout_family_cv"]
        self.assertEqual(capture["learned"]["safety_pass_samples"], 36)
        self.assertEqual(capture["learned"]["totals"]["cross_medication_associations"], 0)
        self.assertEqual(layout["learned"]["totals"]["cross_medication_associations"], 0)
        self.assertGreater(layout["learned"]["totals"]["false_exact_fields"], 0)
        self.assertLess(
            layout["learned"]["metrics"]["critical_field_exact_accuracy"],
            layout["geometry_rule_v2"]["metrics"]["critical_field_exact_accuracy"],
        )


    def test_context_model_matches_rule_on_independent_synthetic_holdout(self) -> None:
        result = json.loads(CONTEXT_RESULT.read_text(encoding="utf-8"))
        self.assertEqual(result["model_id"], "hashed_layout_context_v3")
        self.assertEqual(result["decision"], "hold_for_real_validation")
        self.assertFalse(result["real_data_evaluated"])
        self.assertNotEqual(result["context_training"]["seed"], result["evaluation"]["seed"])
        self.assertEqual(result["context_training"]["sample_count"], 360)
        learned = result["evaluation"]["learned"]
        rule = result["evaluation"]["geometry_rule_v2"]
        self.assertEqual(learned["safety_pass_samples"], 36)
        self.assertEqual(learned["totals"]["false_exact_fields"], 0)
        self.assertEqual(learned["totals"]["cross_medication_associations"], 0)
        self.assertEqual(learned["totals"]["critical_field_exact"], rule["totals"]["critical_field_exact"])
        self.assertEqual(learned["totals"]["matched_rows"], rule["totals"]["matched_rows"])
        payload = CONTEXT_MODEL.read_bytes()
        self.assertEqual(hashlib.sha256(payload).hexdigest(), result["model_sha256"])
        self.assertEqual(len(payload), result["model_bytes"])
        self.assertLess(len(payload), 64 * 1024)


if __name__ == "__main__":
    unittest.main()