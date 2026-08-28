from __future__ import annotations

import unittest

import numpy as np

from browser_ocr.document_parsing.parser_v51_runtime_decode import (
    ParserV51RuntimeMemory,
    decode_parser_v51_memory,
)
from browser_ocr.document_parsing.parser_v51_targets import ROW_FIELD_ROLES


class ParserV51RuntimeDecodeTest(unittest.TestCase):
    def _memory(self) -> ParserV51RuntimeMemory:
        rows = 2
        fields = len(ROW_FIELD_ROLES)
        nodes = 3
        tokens = 16
        hidden = 3
        field_states = np.zeros((rows, fields, hidden), dtype=np.float32)
        field_states[0, :, 2] = 1.0  # STOP by default.
        field_states[0, ROW_FIELD_ROLES.index("product")] = [1.0, 0.0, 0.0]
        field_states[0, ROW_FIELD_ROLES.index("dose")] = [0.0, 1.0, 0.0]

        node_keys = np.asarray(
            [
                [10.0, 0.0, 0.0],
                [0.0, 10.0, 0.0],
                [-10.0, -10.0, 0.0],  # unrelated node
                [0.0, 0.0, 10.0],  # STOP
            ],
            dtype=np.float32,
        )
        start_keys = np.zeros((nodes, tokens, hidden), dtype=np.float32)
        end_keys = np.zeros((nodes, tokens, hidden), dtype=np.float32)
        # product node: whole UTF-8 text "약A" (4 bytes), tokens 1..4.
        start_keys[0, 1] = [10.0, 0.0, 0.0]
        end_keys[0, 4] = [10.0, 0.0, 0.0]
        # dose node: whole UTF-8 text "1정" (4 bytes), tokens 1..4.
        start_keys[1, 1] = [0.0, 10.0, 0.0]
        end_keys[1, 4] = [0.0, 10.0, 0.0]

        evidence = np.zeros((nodes, tokens, hidden), dtype=np.float32)
        evidence[0, 1:5] = [-1.0, 0.0, 1.0]
        evidence[1, 1:5] = [0.0, -1.0, 1.0]
        valid = np.zeros((nodes, tokens), dtype=bool)
        valid[0, 1:5] = True
        valid[1, 1:5] = True
        valid[2, 1:5] = True
        return ParserV51RuntimeMemory(
            row_existence_logits=np.asarray([10.0, -10.0], dtype=np.float32),
            field_query_states=field_states,
            node_pointer_keys=node_keys,
            start_pointer_keys=start_keys,
            end_pointer_keys=end_keys,
            evidence_values=evidence,
            token_valid_mask=valid,
        )

    def test_decodes_only_selected_ocr_substrings(self) -> None:
        nodes = [
            {"node_id": "product", "text": "약A"},
            {"node_id": "dose", "text": "1정"},
            {"node_id": "unused", "text": "약제비 계산서 영수증"},
        ]
        rows = decode_parser_v51_memory(nodes=nodes, memory=self._memory())

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["product_query"], "약A")
        self.assertEqual(rows[0]["fields"]["dose"]["text"], "1정")
        self.assertNotIn("영수증", str(rows[0]))
        for evidence in [*rows[0]["product_evidence"], *rows[0]["fields"]["dose"]["evidence"]]:
            source = next(node["text"] for node in nodes if node["node_id"] == evidence["node_id"])
            self.assertEqual(evidence["text"], source[evidence["start_char"] : evidence["end_char"]])

    def test_utf8_pointer_is_constrained_to_character_boundaries(self) -> None:
        memory = self._memory()
        # Make the illegal middle bytes of "약" score higher than the legal
        # boundaries. Runtime decoding must still return complete characters.
        memory.start_pointer_keys[0, 2] = [100.0, 0.0, 0.0]
        memory.end_pointer_keys[0, 2] = [100.0, 0.0, 0.0]
        rows = decode_parser_v51_memory(
            nodes=[
                {"node_id": "product", "text": "약A"},
                {"node_id": "dose", "text": "1정"},
                {"node_id": "unused", "text": "receipt"},
            ],
            memory=memory,
        )
        self.assertEqual(rows[0]["product_query"], "약A")

    def test_product_evidence_is_required_for_a_row(self) -> None:
        memory = self._memory()
        memory.field_query_states[0, ROW_FIELD_ROLES.index("product")] = [0.0, 0.0, 1.0]
        rows = decode_parser_v51_memory(
            nodes=[
                {"node_id": "product", "text": "약A"},
                {"node_id": "dose", "text": "1정"},
                {"node_id": "unused", "text": "receipt"},
            ],
            memory=memory,
        )
        self.assertEqual(rows, [])

    def test_evidence_sequence_moves_forward_and_cannot_cycle(self) -> None:
        fields = len(ROW_FIELD_ROLES)
        field_states = np.zeros((1, fields, 3), dtype=np.float32)
        field_states[:, :, 2] = 1.0
        field_states[0, ROW_FIELD_ROLES.index("product")] = [1.0, 0.0, 0.0]
        node_keys = np.asarray([[10.0, 0.0, 0.0], [9.0, 0.0, 0.0]], dtype=np.float32)
        start_keys = np.zeros((1, 4, 3), dtype=np.float32)
        end_keys = np.zeros((1, 4, 3), dtype=np.float32)
        start_keys[0, 1] = [10.0, 0.0, 0.0]
        end_keys[0, 1] = [10.0, 0.0, 0.0]
        start_keys[0, 2] = [9.0, 0.0, 0.0]
        end_keys[0, 2] = [9.0, 0.0, 0.0]
        valid = np.zeros((1, 4), dtype=bool)
        valid[0, 1:3] = True
        memory = ParserV51RuntimeMemory(
            row_existence_logits=np.asarray([10.0], dtype=np.float32),
            field_query_states=field_states,
            node_pointer_keys=node_keys,
            start_pointer_keys=start_keys,
            end_pointer_keys=end_keys,
            evidence_values=np.zeros((1, 4, 3), dtype=np.float32),
            token_valid_mask=valid,
        )

        rows = decode_parser_v51_memory(nodes=[{"node_id": "n0", "text": "AB"}], memory=memory)

        self.assertEqual(rows[0]["product_query"], "AB")
        self.assertEqual([item["text"] for item in rows[0]["product_evidence"]], ["A", "B"])


if __name__ == "__main__":
    unittest.main()