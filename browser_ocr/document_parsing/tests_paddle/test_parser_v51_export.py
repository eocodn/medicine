from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import onnxruntime as ort
import paddle

from browser_ocr.document_parsing.parser_v51_export_onnx import build_parser_v51_onnx_model, run_parser_v51_export_parity_case
from browser_ocr.document_parsing.parser_v51_model_paddle import ParserV51Model, ParserV51ModelConfig


class ParserV51ExportTest(unittest.TestCase):
    def test_memory_export_matches_paddle(self) -> None:
        paddle.set_device("cpu")
        paddle.seed(814)
        config = ParserV51ModelConfig(max_text_bytes=48, hidden_dim=64, text_embedding_dim=24, text_conv_dim=32, layers=1, heads=4, max_rows=4)
        model = ParserV51Model(config)
        model.eval()
        proto = build_parser_v51_onnx_model(model, config)
        with tempfile.TemporaryDirectory() as raw:
            path = Path(raw) / "model.onnx"
            path.write_bytes(proto.SerializeToString())
            session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
            result = run_parser_v51_export_parity_case(
                model=model,
                config=config,
                session=session,
                seed=8141,
                require_decode_success=False,
            )
        self.assertLessEqual(result["max_abs_delta"], 2e-5)
        self.assertTrue(result["token_valid_mask_equal"])
        self.assertTrue(result["decoded_rows_equal"])


if __name__ == "__main__":
    unittest.main()
