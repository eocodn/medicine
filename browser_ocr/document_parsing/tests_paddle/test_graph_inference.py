from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

import paddle

from browser_ocr.document_parsing.document_graph import GraphEncoderSpec
from browser_ocr.document_parsing.graph_encoder_paddle import SparseDocumentGraphEncoder, architecture_manifest
from browser_ocr.document_parsing.graph_inference_paddle import (
    build_inference_graph,
    load_graph_model,
    score_graph_document,
    score_graph_relations,
)


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _document() -> dict[str, object]:
    return {
        "document_id": "inference-fixture",
        "width": 1000,
        "height": 1400,
        "observation": {
            "nodes": [
                {
                    "node_id": "p",
                    "text": "가나다정",
                    "confidence": 0.98,
                    "polygon": _poly(100, 100),
                    "target_region_ids": ["gt-p"],
                    "label_status": "labeled",
                    "semantic_role": "product",
                    "association_group": "m1",
                },
                {
                    "node_id": "d",
                    "text": "1정",
                    "confidence": 0.97,
                    "polygon": _poly(400, 100),
                    "target_region_ids": ["gt-d"],
                    "label_status": "labeled",
                    "semantic_role": "dose",
                    "association_group": "m1",
                },
            ],
        },
        "relations": [{"product_node_id": "p", "field_node_id": "d", "label": "same_medication"}],
    }


def _write_model_result(root: Path, spec: GraphEncoderSpec) -> Path:
    with paddle.utils.unique_name.guard():
        model = SparseDocumentGraphEncoder(spec)
    for parameter in model.parameters():
        parameter.set_value(paddle.zeros_like(parameter))
    checkpoint = root / "model.pdparams"
    paddle.save(model.state_dict(), str(checkpoint))
    result = {
        "schema_version": 1,
        "status": "ok",
        "profile": {"architecture": architecture_manifest(spec)},
        "best_checkpoint": str(checkpoint),
        "best_checkpoint_sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest(),
        "parameter_count": sum(int(parameter.numel()) for parameter in model.parameters()),
    }
    path = root / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    (root / "training-state.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "profile": result["profile"],
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return path


class GraphInferencePaddleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_inference_graph_cannot_observe_gold_roles_groups_or_relations(self) -> None:
        first = _document()
        second = json.loads(json.dumps(first))
        second["observation"]["nodes"][0]["semantic_role"] = "other"
        second["observation"]["nodes"][0]["association_group"] = None
        second["observation"]["nodes"][1]["semantic_role"] = "header"
        second["observation"]["nodes"][1]["association_group"] = "document"
        second["relations"] = []

        left = build_inference_graph(first, neighbor_count=4)
        right = build_inference_graph(second, neighbor_count=4)
        self.assertEqual(left.nodes, right.nodes)
        self.assertEqual(left.edges, right.edges)
        self.assertEqual(left.relations, ())
        self.assertTrue(all(not node.supervised and node.role_target is None for node in left.nodes))

    def test_checkpoint_bound_model_scores_roles_and_requested_relation_pairs(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = _write_model_result(
                root,
                GraphEncoderSpec(hidden_dim=32, layers=1, neighbor_count=4, pair_hidden_dim=24),
            )
            bundle = load_graph_model(result, device="cpu")
            graph, role_scores, hidden = score_graph_document(bundle, _document())
            self.assertEqual(set(role_scores), {"p", "d"})
            for scores in role_scores.values():
                self.assertAlmostEqual(sum(scores.values()), 1.0, places=6)
            relations = score_graph_relations(bundle, graph, hidden, [("p", "d")])
            self.assertEqual(set(relations), {("p", "d")})
            self.assertAlmostEqual(relations[("p", "d")], 0.5, places=6)

            checkpoint = Path(bundle.checkpoint)
            checkpoint.write_bytes(checkpoint.read_bytes() + b"mutated")
            with self.assertRaisesRegex(ValueError, "SHA-256"):
                load_graph_model(result, device="cpu")

    def test_model_load_requires_authoritative_completed_training_state(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            result = _write_model_result(
                root,
                GraphEncoderSpec(hidden_dim=32, layers=1, neighbor_count=4, pair_hidden_dim=24),
            )
            state_path = root / "training-state.json"
            state = json.loads(state_path.read_text(encoding="utf-8"))
            state["status"] = "failed"
            state_path.write_text(json.dumps(state), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "not completed"):
                load_graph_model(result, device="cpu")


if __name__ == "__main__":
    unittest.main()