from __future__ import annotations

import unittest

import paddle

from browser_ocr.document_parsing.parser_v51_inference_paddle import run_parser_v51_inference
from browser_ocr.document_parsing.parser_v51_model_paddle import ParserV51Model, ParserV51ModelConfig
from browser_ocr.document_parsing.parser_v51_runtime_decode import ParserV51RuntimeDecodeConfig


class ParserV51InferenceTest(unittest.TestCase):
    def setUp(self) -> None:
        paddle.set_device("cpu")

    def _config(self) -> ParserV51ModelConfig:
        return ParserV51ModelConfig(
            max_text_bytes=32,
            hidden_dim=48,
            text_embedding_dim=16,
            text_conv_dim=24,
            layers=1,
            heads=4,
            max_rows=4,
        )

    def _nodes(self):
        return [
            {
                "node_id": "blank",
                "text": "   ",
                "detector_confidence": 0.8,
                "recognizer_confidence": 0.0,
                "polygon": [[10, 10], [50, 10], [50, 30], [10, 30]],
            },
            {
                "node_id": "product",
                "text": "약A 500mg",
                "detector_confidence": 0.9,
                "recognizer_confidence": 0.95,
                "polygon": [[100, 100], [300, 100], [300, 140], [100, 140]],
            },
            {
                "node_id": "unused",
                "text": "약제비 계산서 영수증",
                "detector_confidence": 0.93,
                "recognizer_confidence": 0.98,
                "polygon": [[100, 300], [500, 300], [500, 340], [100, 340]],
            },
        ]

    def test_runtime_inference_canonicalizes_blank_nodes_before_model(self) -> None:
        paddle.seed(991)
        config = self._config()
        model = ParserV51Model(config)
        model.eval()
        decode = ParserV51RuntimeDecodeConfig(row_threshold=1.0)

        with_blank = run_parser_v51_inference(
            model=model,
            config=config,
            document_id="runtime",
            width=1000,
            height=1400,
            nodes=self._nodes(),
            decode_config=decode,
        )
        without_blank = run_parser_v51_inference(
            model=model,
            config=config,
            document_id="runtime",
            width=1000,
            height=1400,
            nodes=self._nodes()[1:],
            decode_config=decode,
        )

        self.assertEqual(with_blank, without_blank)
        self.assertEqual(with_blank.node_ids, ("product", "unused"))
        self.assertEqual(with_blank.rows, ())


if __name__ == "__main__":
    unittest.main()