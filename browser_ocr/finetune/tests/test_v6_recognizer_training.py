from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from browser_ocr.finetune.dataset import DatasetError, load_dataset
from browser_ocr.finetune.recognizer_training import (
    V6RecognizerTrainingConfig,
    prepare_v6_recognizer_training,
    run_v6_recognizer_training,
)


PNG_1X1 = bytes.fromhex(
    "89504e470d0a1a0a0000000d4948445200000001000000010802000000907753de"
    "0000000c4944415408d763f8ffff3f0005fe02fea73581840000000049454e44ae426082"
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


def _fixture(root: Path) -> dict[str, Path]:
    paddle = root / "PaddleOCR"
    config = paddle / "configs/rec/model.yml"
    dictionary = paddle / "ppocr/utils/dict/korean.txt"
    train_script = paddle / "tools/train.py"
    for path in (config, dictionary, train_script):
        path.parent.mkdir(parents=True, exist_ok=True)
    config.write_text("Global:\n  model_name: korean_PP-OCRv5_mobile_rec\n", encoding="utf-8")
    dictionary.write_text("가\n나\n다\n라\n마\n바\n사\n아\n자\n", encoding="utf-8")
    train_script.write_text("print('fixture')\n", encoding="utf-8")

    pretrained = root / "korean_PP-OCRv5_mobile_rec_pretrained.pdparams"
    pretrained.write_bytes(b"recognizer-pretrain")
    upstream = root / "upstream.json"
    _write_json(
        upstream,
        {
            "schema_version": 1,
            "framework": "PaddleOCR",
            "recognizer": "korean_PP-OCRv5_mobile_rec",
            "paddleocr": {
                "commit": "b03f46425e8ff4442b268ce449e3eef758146cd4",
                "config_path": "configs/rec/model.yml",
                "config_sha256": _sha(config),
                "dictionary_path": "ppocr/utils/dict/korean.txt",
                "dictionary_sha256": _sha(dictionary),
            },
            "pretrained_model_bytes": pretrained.stat().st_size,
            "pretrained_model_sha256": _sha(pretrained),
            "model_contract": {"max_text_length": 25, "use_space_char": True},
            "pin_status": "training-smoke-verified",
            "training_enabled": True,
        },
    )

    dataset_root = root / "dataset"
    images = dataset_root / "images"
    images.mkdir(parents=True)
    samples = []
    for sample_id, document_id, text in (
        ("train-a", "doc-train", "가나다"),
        ("val-b", "doc-val", "라마바"),
        ("test-c", "doc-test", "사아자"),
    ):
        image = images / f"{sample_id}.png"
        image.write_bytes(PNG_1X1)
        samples.append(
            {
                "id": sample_id,
                "image": f"images/{sample_id}.png",
                "image_sha256": _sha(image),
                "text": text,
                "origin": "synthetic",
                "document_type": "prescription",
                "document_id": document_id,
                "groups": {
                    "layout_family": "layout-a",
                    "source_family": "source-a",
                    "drug_family": f"drug-{sample_id}",
                },
                "semantic_tags": ["product"],
                "risk_tags": ["difficulty-clean"] if sample_id != "test-c" else ["difficulty-hard", "degradation-hard-ood"],
                "privacy": {"contains_patient_data": False, "deidentified": False},
                "provenance": {"source_id": "fixture", "license_id": "synthetic-fixture"},
            }
        )
    manifest = dataset_root / "manifest.json"
    _write_json(
        manifest,
        {
            "schema_version": 1,
            "dataset_id": "v6-recognizer-fixture",
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
            "metadata": {
                "training_view_policy": {
                    "policy_id": "unified-recognition-training-view-v1",
                    "dictionary_sha256": _sha(dictionary),
                    "max_text_length": 25,
                    "use_space_char": True,
                    "train_excluded_risk_tag": "degradation-hard-ood",
                    "profile_sha256": "a" * 64,
                }
            },
        },
    )
    (dataset_root / "samples.jsonl").write_text(
        "".join(json.dumps(sample, ensure_ascii=False, sort_keys=True) + "\n" for sample in samples),
        encoding="utf-8",
    )
    dataset = load_dataset(manifest)

    export = dataset_root / "paddle"
    export.mkdir()
    counts = {"train": 1, "val": 1, "test": 1}
    splits = {"train": ["train-a"], "val": ["val-b"], "test": ["test-c"]}
    _write_json(
        export / "split.json",
        {
            "schema_version": 1,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "document_id",
            "assignment": "parent_document_split_filtered_training_v1",
            "counts": counts,
            "splits": splits,
        },
    )
    _write_json(
        export / "export.json",
        {
            "schema_version": 1,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "group_by": "document_id",
            "counts": counts,
        },
    )
    by_id = {sample["id"]: sample for sample in samples}
    for split, ids in splits.items():
        (export / f"{split}.txt").write_text(
            "".join(f"{by_id[sample_id]['image']}\t{by_id[sample_id]['text']}\n" for sample_id in ids),
            encoding="utf-8",
        )
    return {
        "upstream": upstream,
        "paddle": paddle,
        "pretrained": pretrained,
        "manifest": manifest,
        "export": export,
    }


class V6RecognizerTrainingTest(unittest.TestCase):
    def test_config_rejects_nonfinite_learning_rate(self) -> None:
        for value in (float("nan"), float("inf"), float("-inf")):
            with self.subTest(value=value):
                with self.assertRaisesRegex(DatasetError, "positive and finite"):
                    V6RecognizerTrainingConfig(learning_rate=value).validate()

    def test_agent_control_cli_reports_preflight_as_json(self) -> None:
        from browser_ocr.finetune.train_cli import main

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = _fixture(root)
            output = io.StringIO()
            with redirect_stdout(output):
                code = main(
                    [
                        "v6-preflight",
                        "--upstream",
                        str(paths["upstream"]),
                        "--paddleocr-root",
                        str(paths["paddle"]),
                        "--pretrained-model",
                        str(paths["pretrained"]),
                        "--manifest",
                        str(paths["manifest"]),
                        "--export-dir",
                        str(paths["export"]),
                        "--run-dir",
                        str(root / "run"),
                        "--epochs",
                        "3",
                        "--batch-size",
                        "2",
                        "--warmup-epochs",
                        "1",
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            payload = json.loads(output.getvalue())
            self.assertEqual(payload["status"], "ready")
            self.assertFalse(any("test.txt" in value for value in payload["command"]))

    def test_preflight_is_hash_bound_and_never_uses_test_for_optimization(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            result = prepare_v6_recognizer_training(
                upstream_path=paths["upstream"],
                paddleocr_root=paths["paddle"],
                pretrained_model=paths["pretrained"],
                manifest=paths["manifest"],
                export_dir=paths["export"],
                run_dir=Path(raw) / "run",
                config=V6RecognizerTrainingConfig(epochs=3, batch_size=2, warmup_epochs=1),
            )
            self.assertEqual(result["status"], "ready")
            self.assertEqual(result["profile"]["optimization_splits"], ["train", "val"])
            self.assertEqual(result["profile"]["promotion_evaluation_split"], "test")
            command = result["command"]
            self.assertTrue(any("Train.dataset.label_file_list" in value and "train.txt" in value for value in command))
            self.assertTrue(any("Eval.dataset.label_file_list" in value and "val.txt" in value for value in command))
            self.assertFalse(any("test.txt" in value for value in command))

    def test_preflight_rejects_same_count_label_membership_drift(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            paths = _fixture(Path(raw))
            test_row = (paths["export"] / "test.txt").read_text(encoding="utf-8")
            (paths["export"] / "train.txt").write_text(test_row, encoding="utf-8")
            with self.assertRaisesRegex(DatasetError, "labels do not match authoritative membership"):
                prepare_v6_recognizer_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddle"],
                    pretrained_model=paths["pretrained"],
                    manifest=paths["manifest"],
                    export_dir=paths["export"],
                    run_dir=Path(raw) / "run",
                    config=V6RecognizerTrainingConfig(epochs=3, batch_size=2, warmup_epochs=1),
                )

    def test_failed_training_resumes_and_completion_stays_pending_safety_eval(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = _fixture(root)
            run_dir = root / "run"
            config = V6RecognizerTrainingConfig(epochs=3, batch_size=2, warmup_epochs=1)

            def fail_after_epoch_one(command, *, cwd, log_path, on_progress):
                del command, cwd
                log_path.parent.mkdir(parents=True, exist_ok=True)
                log_path.write_text("epoch: [1/3]\n", encoding="utf-8")
                model = run_dir / "model"
                model.mkdir(parents=True, exist_ok=True)
                for suffix in (".pdparams", ".pdopt", ".states"):
                    (model / f"iter_epoch_1{suffix}").write_text("epoch-1", encoding="utf-8")
                on_progress(1, "epoch: [1/3]")
                raise DatasetError("synthetic interruption")

            common = dict(
                upstream_path=paths["upstream"],
                paddleocr_root=paths["paddle"],
                pretrained_model=paths["pretrained"],
                manifest=paths["manifest"],
                export_dir=paths["export"],
                run_dir=run_dir,
                config=config,
            )
            with patch("browser_ocr.finetune.recognizer_training._stream_training", side_effect=fail_after_epoch_one):
                with self.assertRaisesRegex(DatasetError, "synthetic interruption"):
                    run_v6_recognizer_training(**common)

            observed = {}

            def finish(command, *, cwd, log_path, on_progress):
                del cwd
                observed["command"] = command
                log_path.write_text("epoch: [3/3]\n", encoding="utf-8")
                model = run_dir / "model"
                for epoch in (2, 3):
                    for suffix in (".pdparams", ".pdopt", ".states"):
                        (model / f"iter_epoch_{epoch}{suffix}").write_text(f"epoch-{epoch}", encoding="utf-8")
                (model / "best_accuracy.pdparams").write_text("best", encoding="utf-8")
                (model / "config.yml").write_text("trained: true\n", encoding="utf-8")
                on_progress(3, "epoch: [3/3]")

            with patch("browser_ocr.finetune.recognizer_training._stream_training", side_effect=finish):
                result = run_v6_recognizer_training(**common)
            self.assertEqual(result["promotion_status"], "pending_project_safety_evaluation")
            self.assertTrue(any("Global.checkpoints=" in value and "iter_epoch_1" in value for value in observed["command"]))
            self.assertFalse(any("test.txt" in value for value in observed["command"]))

    def test_stale_running_state_without_complete_checkpoint_discards_partial_model_files(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            paths = _fixture(root)
            run_dir = root / "run"
            config = V6RecognizerTrainingConfig(epochs=3, batch_size=2, warmup_epochs=1)
            ready = prepare_v6_recognizer_training(
                upstream_path=paths["upstream"],
                paddleocr_root=paths["paddle"],
                pretrained_model=paths["pretrained"],
                manifest=paths["manifest"],
                export_dir=paths["export"],
                run_dir=run_dir,
                config=config,
            )
            run_dir.mkdir(parents=True)
            _write_json(
                run_dir / "training-state.json",
                {
                    "schema_version": 1,
                    "status": "running",
                    "profile": ready["profile"],
                    "current_epoch": 0,
                },
            )
            model = run_dir / "model"
            model.mkdir()
            partial = model / "iter_epoch_1.pdparams"
            partial.write_text("partial", encoding="utf-8")

            def finish(command, *, cwd, log_path, on_progress):
                del command, cwd
                self.assertFalse(partial.exists())
                log_path.write_text("epoch: [3/3]\n", encoding="utf-8")
                for epoch in (1, 2, 3):
                    for suffix in (".pdparams", ".pdopt", ".states"):
                        (model / f"iter_epoch_{epoch}{suffix}").write_text(f"epoch-{epoch}", encoding="utf-8")
                (model / "best_accuracy.pdparams").write_text("best", encoding="utf-8")
                (model / "config.yml").write_text("trained: true\n", encoding="utf-8")
                on_progress(3, "epoch: [3/3]")

            with patch("browser_ocr.finetune.recognizer_training._stream_training", side_effect=finish):
                result = run_v6_recognizer_training(
                    upstream_path=paths["upstream"],
                    paddleocr_root=paths["paddle"],
                    pretrained_model=paths["pretrained"],
                    manifest=paths["manifest"],
                    export_dir=paths["export"],
                    run_dir=run_dir,
                    config=config,
                )
            self.assertEqual(result["status"], "ok")


if __name__ == "__main__":
    unittest.main()