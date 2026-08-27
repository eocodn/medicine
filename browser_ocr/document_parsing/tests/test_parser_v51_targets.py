from __future__ import annotations

import copy
import unittest

from browser_ocr.document_parsing.parser_v51_targets import (
    build_parser_v51_row_targets,
    observed_piece_text,
)


class ParserV51TargetsTest(unittest.TestCase):
    def _pair(self) -> tuple[dict, dict]:
        document = {
            "schema_version": 5,
            "document_id": "direct-row-doc",
            "width": 1000,
            "height": 1400,
            "sections": [
                {"section_id": "meds", "kind": "medications"},
                {"section_id": "noise", "kind": "warning"},
            ],
            "medications": [{"medication_id": "med-01", "product_name": "약A"}],
            "spans": [
                {
                    "span_id": "product",
                    "section_id": "meds",
                    "text": "약A",
                    "semantic_role": "product",
                    "association_group": "med-01",
                    "polygon": [[50, 100], [200, 100], [200, 140], [50, 140]],
                    "reading_order": 0,
                },
                {
                    "span_id": "dose",
                    "section_id": "meds",
                    "text": "1정",
                    "semantic_role": "dose",
                    "association_group": "med-01",
                    "polygon": [[220, 100], [300, 100], [300, 140], [220, 140]],
                    "reading_order": 1,
                },
                {
                    "span_id": "frequency",
                    "section_id": "meds",
                    "text": "1일3회",
                    "semantic_role": "frequency",
                    "association_group": "med-01",
                    "polygon": [[320, 100], [430, 100], [430, 140], [320, 140]],
                    "reading_order": 2,
                },
                {
                    "span_id": "warning",
                    "section_id": "noise",
                    "text": "문의는 약사에게",
                    "semantic_role": "context",
                    "association_group": None,
                    "polygon": [[50, 300], [400, 300], [400, 340], [50, 340]],
                    "reading_order": 3,
                },
            ],
        }
        observation = {
            "document_id": "direct-row-doc",
            "profile_revision": 2,
            "nodes": [
                {
                    "node_id": "obs-1",
                    "text": "약A",
                    "detector_confidence": 0.9,
                    "recognizer_confidence": 0.95,
                    "polygon": [[50, 100], [200, 100], [200, 140], [50, 140]],
                    "source_span_ids": ["product"],
                    "source_segments": [{"source_span_id": "product", "start_char": 0, "end_char": 2}],
                    "targets": [
                        {
                            "source_span_id": "product",
                            "semantic_role": "product",
                            "association_group": "med-01",
                            "label_status": "labeled",
                        }
                    ],
                    "operation": "identity",
                },
                {
                    "node_id": "obs-2",
                    "text": "1정 1일3회",
                    "detector_confidence": 0.88,
                    "recognizer_confidence": 0.91,
                    "polygon": [[220, 100], [430, 100], [430, 140], [220, 140]],
                    "source_span_ids": ["dose", "frequency"],
                    "source_segments": [
                        {"source_span_id": "dose", "start_char": 0, "end_char": 2},
                        {"source_span_id": "frequency", "start_char": 3, "end_char": 7},
                    ],
                    "targets": [
                        {
                            "source_span_id": "dose",
                            "semantic_role": "dose",
                            "association_group": "med-01",
                            "label_status": "labeled",
                        },
                        {
                            "source_span_id": "frequency",
                            "semantic_role": "frequency",
                            "association_group": "med-01",
                            "label_status": "labeled",
                        },
                    ],
                    "operation": "merge",
                },
                {
                    "node_id": "obs-3",
                    "text": "문의는 약사에게",
                    "detector_confidence": 0.92,
                    "recognizer_confidence": 0.99,
                    "polygon": [[50, 300], [400, 300], [400, 340], [50, 340]],
                    "source_span_ids": ["warning"],
                    "source_segments": [{"source_span_id": "warning", "start_char": 0, "end_char": 8}],
                    "targets": [
                        {
                            "source_span_id": "warning",
                            "semantic_role": "context",
                            "association_group": None,
                            "label_status": "labeled",
                        }
                    ],
                    "operation": "identity",
                },
            ],
        }
        return document, observation

    def test_direct_targets_extract_distinct_subspans_from_one_merged_node(self) -> None:
        document, observation = self._pair()
        targets = build_parser_v51_row_targets(document, observation)

        self.assertEqual(len(targets.rows), 1)
        row = targets.rows[0]
        dose = row.field("dose").pieces[0]
        frequency = row.field("frequency").pieces[0]
        self.assertEqual(dose.node_id, frequency.node_id)
        self.assertEqual(observed_piece_text(observation["nodes"][1]["text"], dose), "1정")
        self.assertEqual(observed_piece_text(observation["nodes"][1]["text"], frequency), "1일3회")
        self.assertNotEqual((dose.start_byte, dose.end_byte), (frequency.start_byte, frequency.end_byte))

    def test_irrelevant_header_context_taxonomy_does_not_change_row_targets(self) -> None:
        document, observation = self._pair()
        baseline = build_parser_v51_row_targets(document, observation)
        changed_document = copy.deepcopy(document)
        changed_observation = copy.deepcopy(observation)
        changed_document["spans"][-1]["semantic_role"] = "header"
        changed_observation["nodes"][-1]["targets"][0]["semantic_role"] = "header"

        changed = build_parser_v51_row_targets(changed_document, changed_observation)
        self.assertEqual(changed, baseline)

    def test_row_is_not_supervised_when_product_text_is_not_observable(self) -> None:
        document, observation = self._pair()
        observation["nodes"] = observation["nodes"][1:]

        targets = build_parser_v51_row_targets(document, observation)
        self.assertEqual(targets.rows, ())

    def test_utf8_character_ranges_are_converted_to_byte_offsets(self) -> None:
        document, observation = self._pair()
        product = build_parser_v51_row_targets(document, observation).rows[0].field("product").pieces[0]

        self.assertEqual((product.start_char, product.end_char), (0, 2))
        self.assertEqual((product.start_byte, product.end_byte), (0, len("약A".encode("utf-8"))))


if __name__ == "__main__":
    unittest.main()