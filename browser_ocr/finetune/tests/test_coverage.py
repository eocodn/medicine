from __future__ import annotations

import unittest

from browser_ocr.finetune.coverage import audit_coverage


class CoverageAuditTest(unittest.TestCase):
    def test_required_strata_and_scripts_fail_closed_when_under_minimum(self) -> None:
        plan = {
            "required_document_types": ["prescription", "medication_bag"],
            "required_scripts": ["korean", "latin", "digit"],
            "required_semantic_strata": ["product", "dose"],
            "required_risk_strata": ["exact_numeric", "hard_negative"],
        }
        stats = {
            "document_types": {"prescription": 5, "medication_bag": 5},
            "scripts": {"korean": 10, "latin": 1, "digit": 9},
            "semantic_tags": {"product": 5, "dose": 3},
            "risk_tags": {"exact_numeric": 8},
        }
        report = audit_coverage(stats, plan, minimum_per_stratum=2)
        self.assertEqual(report["status"], "insufficient")
        self.assertEqual(report["missing_scripts"], ["latin"])
        self.assertEqual(report["missing_risk_strata"], ["hard_negative"])
        self.assertEqual(report["missing_document_types"], [])
        self.assertEqual(report["missing_semantic_strata"], [])

    def test_complete_coverage_is_ok(self) -> None:
        plan = {
            "required_document_types": ["prescription"],
            "required_scripts": ["korean"],
            "required_semantic_strata": ["product"],
            "required_risk_strata": ["exact_numeric"],
        }
        stats = {
            "document_types": {"prescription": 3},
            "scripts": {"korean": 3},
            "semantic_tags": {"product": 3},
            "risk_tags": {"exact_numeric": 3},
        }
        self.assertEqual(audit_coverage(stats, plan, minimum_per_stratum=2)["status"], "ok")


if __name__ == "__main__":
    unittest.main()
