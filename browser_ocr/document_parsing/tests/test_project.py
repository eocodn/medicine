from __future__ import annotations

import unittest
from pathlib import Path

from browser_ocr.document_parsing.contract import load_corpus


CORPUS = Path("browser_ocr/document_parsing/corpus/manifest.json")


class ProjectCorpusTest(unittest.TestCase):
    def test_seed_corpus_covers_historical_structure_failures(self) -> None:
        corpus = load_corpus(CORPUS)
        self.assertGreaterEqual(len(corpus.cases), 6)
        scenario_tags = {tag for case in corpus.cases for tag in case.scenario_tags}
        risk_tags = {tag for case in corpus.cases for tag in case.risk_tags}
        self.assertTrue(
            {
                "prescription_table",
                "medication_bag",
                "shared_regimen",
                "split_boxes",
                "skewed_geometry",
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


if __name__ == "__main__":
    unittest.main()