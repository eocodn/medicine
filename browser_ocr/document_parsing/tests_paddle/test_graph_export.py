from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import onnxruntime as ort
import paddle

from browser_ocr.document_parsing.document_graph import GraphEncoderSpec, build_document_graph, relation_pair_features
from browser_ocr.document_parsing.graph_encoder_paddle import SparseDocumentGraphEncoder, architecture_manifest, graph_tensors
from browser_ocr.document_parsing.graph_export_paddle import export_graph_model


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _document() -> dict[str, object]:
    return {
        "document_id": "export-fixture",
        "split": "val",
        "source_kind": "synthetic",
        "image_sha256": "a" * 64,
        "width": 1000,
        "height": 1400,
        "layout_family": "fixture",
        "scenario_tags": [],
        "risk_tags": [],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": "b" * 64},
        "observation": {
            "kind": "oracle",
            "profile": {"producer": "unified_truth", "truth_samples_sha256": "b" * 64},
            "nodes": [
                {
                    "node_id": "p",
                    "text": "가나다정",
                    "confidence": 1.0,
                    "polygon": _poly(100, 100),
                    "target_region_ids": ["p"],
                    "label_status": "labeled",
                    "semantic_role": "product",
                    "association_group": "m1",
                },
                {
                    "node_id": "d",
                    "text": "1정",
                    "confidence": 1.0,
                    "polygon": _poly(400, 100),
                    "target_region_ids": ["d"],
                    "label_status": "labeled",
                    "semantic_role": "dose",
                    "association_group": "m1",
                },
            ],
        },
        "relations": [{"product_node_id": "p", "field_node_id": "d", "label": "same_medication"}],
        "gold_rows": [],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


def _model_result(root: Path) -> Path:
    root.mkdir(parents=True)
    spec = GraphEncoderSpec(hidden_dim=32, layers=1, neighbor_count=4, pair_hidden_dim=24)
    with paddle.utils.unique_name.guard():
        model = SparseDocumentGraphEncoder(spec)
    checkpoint = root / "model.pdparams"
    paddle.save(model.state_dict(), str(checkpoint))
    profile = {"architecture": architecture_manifest(spec)}
    result = {
        "schema_version": 1,
        "status": "ok",
        "profile": profile,
        "best_checkpoint": str(checkpoint),
        "best_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "parameter_count": sum(int(parameter.numel()) for parameter in model.parameters()),
    }
    result_path = root / "result.json"
    result_path.write_text(json.dumps(result), encoding="utf-8")
    (root / "training-state.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "profile": profile,
        "result_sha256": hashlib.sha256(result_path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return result_path


class GraphExportPaddleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_export_is_dynamic_hash_bound_and_matches_paddle_outputs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result_path = _model_result(root / "model")
            output = root / "export"
            manifest = export_graph_model(model_result=result_path, output_dir=output)

            self.assertEqual(manifest["status"], "ok")
            self.assertEqual(manifest["model_format"], "onnx")
            self.assertEqual(manifest["source_model_result_sha256"], hashlib.sha256(result_path.read_bytes()).hexdigest())
            self.assertEqual(len(manifest["parity_cases"]), 2)
            self.assertLessEqual(manifest["parity_max_abs_delta"], manifest["parity_tolerance"])
            onnx_path = output / manifest["model_file"]
            self.assertTrue(onnx_path.is_file())
            self.assertEqual(manifest["model_sha256"], hashlib.sha256(onnx_path.read_bytes()).hexdigest())
            self.assertEqual(
                [item["name"] for item in manifest["inputs"]],
                ["node_features", "edge_index", "edge_features", "relation_index", "relation_features"],
            )

            graph = build_document_graph(_document(), neighbor_count=4)
            tensors = graph_tensors(graph)
            session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
            feeds = {
                "node_features": tensors.node_features.numpy(),
                "edge_index": tensors.edge_index.numpy(),
                "edge_features": tensors.edge_features.numpy(),
                "relation_index": tensors.relation_index.numpy(),
                "relation_features": tensors.relation_features.numpy(),
            }
            onnx_role, onnx_relation = session.run(None, feeds)

            spec = GraphEncoderSpec(hidden_dim=32, layers=1, neighbor_count=4, pair_hidden_dim=24)
            with paddle.utils.unique_name.guard():
                paddle_model = SparseDocumentGraphEncoder(spec)
            paddle_model.set_state_dict(paddle.load(str(root / "model" / "model.pdparams")))
            paddle_model.eval()
            role, relation = paddle_model(
                tensors.node_features,
                tensors.edge_index,
                tensors.edge_features,
                tensors.relation_index,
                tensors.relation_features,
            )
            self.assertLess(float(abs(onnx_role - role.numpy()).max()), 1e-5)
            self.assertLess(float(abs(onnx_relation - relation.numpy()).max()), 1e-5)

            empty_relation = session.run(None, {
                **feeds,
                "relation_index": feeds["relation_index"][:0],
                "relation_features": feeds["relation_features"][:0],
            })[1]
            self.assertEqual(empty_relation.shape, (0,))

            # Direct serialization is deterministic for the same bound checkpoint.
            second_output = root / "export-second"
            second_manifest = export_graph_model(model_result=result_path, output_dir=second_output)
            self.assertEqual(second_manifest["model_sha256"], manifest["model_sha256"])
            self.assertEqual((second_output / "parser.onnx").read_bytes(), onnx_path.read_bytes())

            # Reusing an intact completed export is idempotent and does not rewrite it.
            self.assertEqual(export_graph_model(model_result=result_path, output_dir=output), manifest)
            onnx_path.write_bytes(onnx_path.read_bytes() + b"tampered")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                export_graph_model(model_result=result_path, output_dir=output)


if __name__ == "__main__":
    unittest.main()