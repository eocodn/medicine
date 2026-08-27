from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import onnxruntime as ort
import paddle

from browser_ocr.document_parsing.parser_v5_export_onnx import export_parser_v5_candidate
from browser_ocr.document_parsing.parser_v5_export_cli import build_parser
from browser_ocr.document_parsing.tests_paddle.parser_v5_fixture import build_frozen_candidate


class ParserV5ExportTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_export_is_freeze_bound_dynamic_and_matches_paddle(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, freeze = build_frozen_candidate(root)
            output = root / "export"

            manifest = export_parser_v5_candidate(
                candidate_freeze=freeze,
                training_result=result,
                output_dir=output,
            )
            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(manifest["model_format"], "onnx")
            self.assertEqual(manifest["model_file"], "parser.onnx")
            self.assertEqual(len(manifest["source_candidate_freeze_fingerprint"]), 64)
            self.assertLessEqual(manifest["parity_max_abs_delta"], manifest["parity_tolerance"])
            self.assertEqual(len(manifest["parity_cases"]), 2)

            session = ort.InferenceSession(str(output / "parser.onnx"), providers=["CPUExecutionProvider"])
            self.assertEqual(
                [item.name for item in session.get_inputs()],
                [
                    "token_ids",
                    "token_mask",
                    "node_scalars",
                    "relation_features",
                    "product_membership",
                    "product_available",
                    "field_node_index",
                    "field_role_index",
                ],
            )
            self.assertEqual(
                [item.name for item in session.get_outputs()],
                ["role_logits", "candidate_logits", "assignment_logits"],
            )

            second_output = root / "export-second"
            second_manifest = export_parser_v5_candidate(
                candidate_freeze=freeze,
                training_result=result,
                output_dir=second_output,
            )
            self.assertEqual(second_manifest["model_sha256"], manifest["model_sha256"])
            self.assertEqual((second_output / "parser.onnx").read_bytes(), (output / "parser.onnx").read_bytes())

            second = export_parser_v5_candidate(
                candidate_freeze=freeze,
                training_result=result,
                output_dir=output,
            )
            self.assertEqual(second, manifest)
            (output / "parser.onnx").write_bytes((output / "parser.onnx").read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export_parser_v5_candidate(
                    candidate_freeze=freeze,
                    training_result=result,
                    output_dir=output,
                )

    def test_export_parity_uses_cpu_reference_independent_of_training_device(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, freeze = build_frozen_candidate(root, training_device="gpu")
            manifest = export_parser_v5_candidate(
                candidate_freeze=freeze,
                training_result=result,
                output_dir=root / "export",
            )
            self.assertEqual(manifest["parity_reference"], {"framework": "paddle", "device": "cpu"})
            self.assertLessEqual(manifest["parity_max_abs_delta"], manifest["parity_tolerance"])

    def test_existing_export_rejects_stale_converter_identity(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result, freeze = build_frozen_candidate(root)
            output = root / "export"
            manifest = export_parser_v5_candidate(
                candidate_freeze=freeze,
                training_result=result,
                output_dir=output,
            )
            self.assertEqual(
                set(manifest["export_implementation_sha256"]),
                {"parser_v5_export_onnx.py", "parser_v5_export_parity.py"},
            )
            persisted = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            persisted["export_implementation_sha256"] = {
                "parser_v5_export_onnx.py": "0" * 64,
                "parser_v5_export_parity.py": "1" * 64,
            }
            (output / "manifest.json").write_text(json.dumps(persisted), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "implementation"):
                export_parser_v5_candidate(
                    candidate_freeze=freeze,
                    training_result=result,
                    output_dir=output,
                )

    def test_cli_requires_freeze_training_result_and_output_dir(self) -> None:
        args = build_parser().parse_args([
            "--candidate-freeze", "/tmp/freeze.json",
            "--training-result", "/tmp/result.json",
            "--output-dir", "/tmp/export",
        ])
        self.assertEqual(args.output_dir, "/tmp/export")


if __name__ == "__main__":
    unittest.main()