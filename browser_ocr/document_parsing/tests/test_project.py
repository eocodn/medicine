from __future__ import annotations

import json
import unittest
from pathlib import Path

from browser_ocr.document_parsing.contract import load_corpus


CORPUS = Path("browser_ocr/document_parsing/corpus/manifest.json")
LEARNED_RESULT = Path("browser_ocr/document_parsing/results/learned-layout-v2-summary.json")


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


if __name__ == "__main__":
    unittest.main()