from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import paddle

from browser_ocr.document_parsing.graph_training_paddle import (
    GraphTrainingConfig,
    run_graph_training,
)
from browser_ocr.document_parsing.training_dataset import write_parser_dataset


def _poly(x: float, y: float, w: float = 90, h: float = 28) -> list[list[float]]:
    return [[x, y], [x + w, y], [x + w, y + h], [x, y + h]]


def _node(node_id: str, text: str, x: float, y: float, role: str, group: str | None) -> dict[str, object]:
    return {
        "node_id": node_id,
        "text": text,
        "confidence": 0.98,
        "polygon": _poly(x, y),
        "target_region_ids": [f"gt-{node_id}"],
        "label_status": "labeled",
        "semantic_role": role,
        "association_group": group,
    }


def _document(document_id: str, split: str, offset: int) -> dict[str, object]:
    group_a = f"{document_id}-a"
    group_b = f"{document_id}-b"
    nodes = [
        _node("p1", f"가나다{offset}정", 80, 120, "product", group_a),
        _node("d1", "1정", 420, 120, "dose", group_a),
        _node("p2", f"라마바{offset}정", 80, 300, "product", group_b),
        _node("d2", "2정", 420, 300, "dose", group_b),
        _node("h1", "복약안내", 80, 40, "header", "document"),
        _node("o1", "17,100", 700, 900, "other", None),
    ]
    relations = [
        {"product_node_id": "p1", "field_node_id": "d1", "label": "same_medication"},
        {"product_node_id": "p1", "field_node_id": "d2", "label": "different_medication"},
        {"product_node_id": "p2", "field_node_id": "d1", "label": "different_medication"},
        {"product_node_id": "p2", "field_node_id": "d2", "label": "same_medication"},
    ]
    image_sha = hashlib.sha256(document_id.encode()).hexdigest()
    return {
        "document_id": document_id,
        "split": split,
        "source_kind": "synthetic",
        "source_binding": {"kind": "synthetic_truth", "truth_samples_sha256": "9" * 64},
        "image_sha256": image_sha,
        "width": 1000,
        "height": 1400,
        "layout_family": "prescription_table",
        "scenario_tags": ["prescription", "table"],
        "risk_tags": ["row_association"],
        "privacy": {"contains_patient_data": False, "deidentified": False},
        "provenance": None,
        "observation": {
            "kind": "oracle",
            "profile": {"producer": "unified_truth", "truth_samples_sha256": "9" * 64},
            "nodes": nodes,
        },
        "relations": relations,
        "gold_rows": [
            {"gold_row_id": group_a, "product_query": f"가나다{offset}정", "draft": {"dose_amount": 1, "dose_unit": "tablet"}},
            {"gold_row_id": group_b, "product_query": f"라마바{offset}정", "draft": {"dose_amount": 2, "dose_unit": "tablet"}},
        ],
        "gold_rows_reviewed": True,
        "annotation_status": "complete",
    }


def _datasets(root: Path) -> tuple[Path, Path]:
    train = write_parser_dataset(
        root / "train",
        dataset_id="parser-graph-train",
        documents=[_document("train-1", "train", 1), _document("train-2", "train", 2)],
    )
    val = write_parser_dataset(
        root / "val",
        dataset_id="parser-graph-val",
        documents=[_document("val-1", "val", 3), _document("val-2", "val", 4)],
    )
    return train, val


class GraphTrainingPaddleTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        paddle.set_device("cpu")

    def test_training_checkpoints_selects_validation_model_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, val = _datasets(root)
            run_dir = root / "run"
            config = GraphTrainingConfig(
                epochs=3,
                learning_rate=0.01,
                seed=41,
                hidden_dim=32,
                layers=2,
                neighbor_count=4,
                pair_hidden_dim=24,
                device="cpu",
            )
            result = run_graph_training(
                train_manifests=[train],
                val_manifests=[val],
                run_dir=run_dir,
                config=config,
            )
            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["epochs_completed"], 3)
            self.assertGreaterEqual(result["best_epoch"], 1)
            self.assertLessEqual(result["best_epoch"], 3)
            self.assertTrue(Path(result["best_checkpoint"]).is_file())
            self.assertEqual(len(result["history"]), 3)
            self.assertIn("selection_score", result["history"][0]["validation"])
            self.assertIn("relation_f0_5", result["history"][0]["validation"])

            cached = run_graph_training(
                train_manifests=[train],
                val_manifests=[val],
                run_dir=run_dir,
                config=config,
            )
            self.assertEqual(cached, result)

            with self.assertRaisesRegex(ValueError, "profile"):
                run_graph_training(
                    train_manifests=[train],
                    val_manifests=[val],
                    run_dir=run_dir,
                    config=GraphTrainingConfig(**{**config.__dict__, "learning_rate": 0.02}),
                )

    def test_training_resumes_after_failure_from_last_complete_epoch(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            train, val = _datasets(root)
            run_dir = root / "resume"
            config = GraphTrainingConfig(
                epochs=3,
                learning_rate=0.01,
                seed=7,
                hidden_dim=32,
                layers=1,
                neighbor_count=4,
                pair_hidden_dim=24,
                device="cpu",
            )
            from browser_ocr.document_parsing import graph_training_paddle as training

            original = training._train_epoch
            calls = 0

            def fail_on_second(*args, **kwargs):
                nonlocal calls
                calls += 1
                if calls == 2:
                    raise RuntimeError("synthetic interruption")
                return original(*args, **kwargs)

            with patch.object(training, "_train_epoch", side_effect=fail_on_second):
                with self.assertRaisesRegex(RuntimeError, "synthetic interruption"):
                    run_graph_training(
                        train_manifests=[train],
                        val_manifests=[val],
                        run_dir=run_dir,
                        config=config,
                    )

            result = run_graph_training(
                train_manifests=[train],
                val_manifests=[val],
                run_dir=run_dir,
                config=config,
            )
            self.assertEqual(result["epochs_completed"], 3)
            self.assertEqual([item["epoch"] for item in result["history"]], [1, 2, 3])

    def test_training_views_have_explicit_deterministic_sampling_weights(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            manifests = []
            for view_index in range(3):
                manifests.append(write_parser_dataset(
                    root / f"train-view-{view_index}",
                    dataset_id=f"parser-train-view-{view_index}",
                    documents=[
                        _document(f"view-{view_index}-doc-1", "train", view_index * 10 + 1),
                        _document(f"view-{view_index}-doc-2", "train", view_index * 10 + 2),
                    ],
                ))
            val = write_parser_dataset(
                root / "val",
                dataset_id="parser-weighted-val",
                documents=[_document("weighted-val", "val", 99)],
            )
            config = GraphTrainingConfig(
                epochs=1,
                learning_rate=0.01,
                seed=17,
                hidden_dim=32,
                layers=1,
                neighbor_count=4,
                pair_hidden_dim=24,
                device="cpu",
            )
            first = run_graph_training(
                train_manifests=manifests,
                train_weights=[0.6, 0.2, 0.2],
                val_manifests=[val],
                run_dir=root / "weighted-a",
                config=config,
            )
            second = run_graph_training(
                train_manifests=manifests,
                train_weights=[0.6, 0.2, 0.2],
                val_manifests=[val],
                run_dir=root / "weighted-b",
                config=config,
            )
            self.assertEqual(
                [view["weight"] for view in first["profile"]["train_views"]],
                [0.6, 0.2, 0.2],
            )
            self.assertEqual(first["history"][0]["train_view_steps"], second["history"][0]["train_view_steps"])
            steps = first["history"][0]["train_view_steps"]
            self.assertEqual(sum(steps.values()), 6)
            self.assertEqual(sorted(steps.values()), [1, 1, 4])

            with self.assertRaisesRegex(ValueError, "train_weights"):
                run_graph_training(
                    train_manifests=manifests,
                    train_weights=[1.0, 1.0],
                    val_manifests=[val],
                    run_dir=root / "bad-weights",
                    config=config,
                )
            with self.assertRaisesRegex(ValueError, "train_weights"):
                run_graph_training(
                    train_manifests=manifests,
                    val_manifests=[val],
                    run_dir=root / "missing-weights",
                    config=config,
                )


if __name__ == "__main__":
    unittest.main()