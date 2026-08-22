from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paddle

from browser_ocr.document_parsing.document_graph import GraphEncoderSpec
from browser_ocr.document_parsing.graph_encoder_paddle import SparseDocumentGraphEncoder, architecture_manifest
from browser_ocr.document_parsing.graph_evaluation_paddle import run_graph_evaluation
from browser_ocr.document_parsing.training_dataset import write_parser_dataset


def _poly(x: float, y: float, w: float = 80, h: float = 24) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _document(document_id: str, split: str = "val") -> dict[str, object]:
    truth_sha = "b" * 64
    return {
        "document_id": document_id,
        "split": split,
        "source_kind": "synthetic",
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": truth_sha},
        "image_sha256": hashlib.sha256(document_id.encode()).hexdigest(),
        "width": 1000,
        "height": 1400,
        "layout_family": "fixture",
        "scenario_tags": ["medication_bag"],
        "risk_tags": ["row_association"],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "observation": {
            "kind": "oracle",
            "profile": {"producer": "unified_truth", "truth_samples_sha256": truth_sha},
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
        "gold_rows": [{
            "gold_row_id": "m1",
            "product_query": "가나다정",
            "draft": {"dose_amount": 1, "dose_unit": "tablet"},
        }],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


def _model_result(root: Path) -> Path:
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
    path = root / "result.json"
    path.write_text(json.dumps(result), encoding="utf-8")
    (root / "training-state.json").write_text(json.dumps({
        "schema_version": 1,
        "status": "completed",
        "profile": profile,
        "result_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }), encoding="utf-8")
    return path


def _exact_prediction(_: object, document: dict[str, object], **__: object) -> list[dict[str, object]]:
    return [{
        "row_id": "p",
        "product_query": "가나다정",
        "draft": {"dose_amount": 1, "dose_unit": "tablet"},
        "uncertainty_codes": [],
        "evidence": {"product_query": ["p"], "dose_amount": ["d"], "dose_unit": ["d"]},
    }]


class GraphEvaluationPaddleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_evaluation_is_resumable_and_aggregates_canonical_safety_metrics(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            model = _model_result(root / "model")
            manifest = write_parser_dataset(
                root / "dataset",
                dataset_id="eval-val",
                documents=[_document("doc-1"), _document("doc-2")],
            )
            output = root / "evaluation"
            calls = 0

            def interrupted(bundle: object, document: dict[str, object], **kwargs: object):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("synthetic evaluation interruption")
                return _exact_prediction(bundle, document, **kwargs)

            with patch(
                "browser_ocr.document_parsing.graph_evaluation_paddle.infer_graph_document",
                side_effect=interrupted,
            ):
                with self.assertRaisesRegex(RuntimeError, "synthetic evaluation interruption"):
                    run_graph_evaluation(
                        model_result=model,
                        dataset_manifests=[manifest],
                        output_dir=output,
                        device="cpu",
                    )

            with patch(
                "browser_ocr.document_parsing.graph_evaluation_paddle.infer_graph_document",
                side_effect=_exact_prediction,
            ) as inference:
                result = run_graph_evaluation(
                    model_result=model,
                    dataset_manifests=[manifest],
                    output_dir=output,
                    device="cpu",
                )
            self.assertEqual(inference.call_count, 1)
            self.assertEqual(result["document_count"], 2)
            self.assertEqual(result["metrics"]["matched_rows"], 2)
            self.assertEqual(result["metrics"]["false_exact_fields"], 0)
            self.assertEqual(result["metrics"]["cross_medication_associations"], 0)
            self.assertTrue(result["metrics"]["safety_pass"])
            with patch(
                "browser_ocr.document_parsing.graph_evaluation_paddle.infer_graph_document",
                side_effect=AssertionError("completed evaluation must be reused"),
            ):
                reused = run_graph_evaluation(
                    model_result=model,
                    dataset_manifests=[manifest],
                    output_dir=output,
                    device="cpu",
                )
            self.assertEqual(reused, result)

    def test_test_split_requires_explicit_unlock_flag_and_train_is_never_allowed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            model = _model_result(root / "model")
            test_manifest = write_parser_dataset(
                root / "test",
                dataset_id="locked-test",
                documents=[_document("test-doc", split="test")],
            )
            with self.assertRaisesRegex(ValueError, "allow_test"):
                run_graph_evaluation(
                    model_result=model,
                    dataset_manifests=[test_manifest],
                    output_dir=root / "blocked-test",
                    device="cpu",
                )

            train_manifest = write_parser_dataset(
                root / "train",
                dataset_id="train-not-eval",
                documents=[_document("train-doc", split="train")],
            )
            with self.assertRaisesRegex(ValueError, "train"):
                run_graph_evaluation(
                    model_result=model,
                    dataset_manifests=[train_manifest],
                    output_dir=root / "blocked-train",
                    device="cpu",
                    allow_test=True,
                )


if __name__ == "__main__":
    unittest.main()