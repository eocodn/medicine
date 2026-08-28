from __future__ import annotations

import unittest

from browser_ocr.document_parsing.parser_v5_decode import (
    ParserV5DecodeConfig,
    decode_parser_v5_rows,
    select_parser_v5_instances,
)
from browser_ocr.document_parsing.parser_v5_model_input import PARSER_V5_ROLE_LABELS


def _roles(**values: float) -> list[float]:
    return [float(values.get(role, 0.0)) for role in PARSER_V5_ROLE_LABELS]


class ParserV5DecodeTest(unittest.TestCase):
    def test_truth_free_instances_and_structured_assignment_decode_rows(self) -> None:
        nodes = [
            {"node_id": "p1", "text": "가나정"},
            {"node_id": "p2", "text": "다라캡슐"},
            {"node_id": "d1", "text": "1정"},
            {"node_id": "f2", "text": "하루 2번"},
            {"node_id": "noise", "text": "합계 12,600원"},
        ]
        role_probabilities = [
            _roles(product=0.95),
            _roles(product=0.93),
            _roles(dose=0.91),
            _roles(frequency=0.89),
            _roles(context=0.97),
        ]
        candidate_probabilities = [0.98, 0.97, 0.94, 0.92, 0.96]
        products, fields = select_parser_v5_instances(
            role_labels=PARSER_V5_ROLE_LABELS,
            role_probabilities=role_probabilities,
            candidate_probabilities=candidate_probabilities,
        )
        self.assertEqual(products, (0, 1))
        self.assertEqual(fields, ((2, "dose"), (3, "frequency")))

        rows = decode_parser_v5_rows(
            nodes=nodes,
            product_node_indices=products,
            field_instances=fields,
            assignment_probabilities=[
                [0.90, 0.04, 0.06],
                [0.03, 0.91, 0.06],
            ],
        )
        self.assertEqual(rows[0]["product_query"], "가나정")
        self.assertEqual(rows[0]["fields"]["dose"]["text"], "1정")
        self.assertEqual(rows[1]["fields"]["frequency"]["text"], "하루 2번")

    def test_decoder_abstains_on_merged_multi_role_text_and_low_margin_assignment(self) -> None:
        nodes = [
            {"node_id": "p1", "text": "가나정"},
            {"node_id": "merged", "text": "1정 하루2번"},
            {"node_id": "dose", "text": "1정"},
        ]
        rows = decode_parser_v5_rows(
            nodes=nodes,
            product_node_indices=[0],
            field_instances=[(1, "dose"), (1, "frequency"), (2, "dose")],
            assignment_probabilities=[
                [0.95, 0.05],
                [0.95, 0.05],
                [0.54, 0.46],
            ],
            config=ParserV5DecodeConfig(assignment_threshold=0.55, assignment_margin=0.10),
        )
        self.assertEqual(rows[0]["fields"], {})

    def test_zero_medication_candidate_filter_emits_no_rows(self) -> None:
        products, fields = select_parser_v5_instances(
            role_labels=PARSER_V5_ROLE_LABELS,
            role_probabilities=[_roles(context=0.99), _roles(header=0.97)],
            candidate_probabilities=[0.99, 0.99],
        )
        self.assertEqual(products, ())
        self.assertEqual(fields, ())
        self.assertEqual(
            decode_parser_v5_rows(
                nodes=[{"node_id": "a", "text": "안내"}, {"node_id": "b", "text": "합계"}],
                product_node_indices=products,
                field_instances=fields,
                assignment_probabilities=[],
            ),
            [],
        )


if __name__ == "__main__":
    unittest.main()