from __future__ import annotations

import unittest

from browser_ocr.finetune.training import build_smoke_overrides


class TrainingPlanTest(unittest.TestCase):
    def test_smoke_overrides_are_bounded_single_gpu_and_checkpointed(self) -> None:
        overrides = build_smoke_overrides(
            dataset_root="/data",
            train_labels="/run/train.txt",
            val_labels="/run/val.txt",
            pretrained_model="/weights/model.pdparams",
            output_dir="/run/output",
            batch_size=16,
        )
        self.assertEqual(overrides["Global.pretrained_model"], "/weights/model.pdparams")
        self.assertEqual(overrides["Global.epoch_num"], 1)
        self.assertFalse(overrides["Global.distributed"])
        self.assertEqual(overrides["Global.save_epoch_step"], 1)
        self.assertEqual(overrides["Train.sampler.first_bs"], 16)
        self.assertEqual(overrides["Train.dataset.data_dir"], "/data")
        self.assertEqual(overrides["Train.dataset.label_file_list"], ["/run/train.txt"])
        self.assertEqual(overrides["Eval.dataset.label_file_list"], ["/run/val.txt"])

class _FakeScalar:
    def __float__(self) -> float:
        return 262144.0


class _FakeTensor:
    def sum(self) -> _FakeScalar:
        return _FakeScalar()


class _FakeCuda:
    def device_count(self) -> int:
        return 1

    def get_device_name(self, index: int) -> str:
        self._check(index)
        return "Fake GPU"

    def get_device_capability(self, index: int) -> tuple[int, int]:
        self._check(index)
        return (8, 9)

    def synchronize(self) -> None:
        return None

    @staticmethod
    def _check(index: int) -> None:
        if index != 0:
            raise AssertionError(index)


class _FakeDevice:
    cuda = _FakeCuda()

    @staticmethod
    def synchronize() -> None:
        return None

    @staticmethod
    def is_compiled_with_cuda() -> bool:
        return True

    @staticmethod
    def get_cudnn_version() -> int:
        return 90501


class _FakeVersion:
    @staticmethod
    def cuda() -> str:
        return "12.6"


class _FakePaddle:
    __version__ = "3.2.0"
    device = _FakeDevice()
    version = _FakeVersion()

    @staticmethod
    def set_device(device: str) -> None:
        if device != "gpu:0":
            raise AssertionError(device)

    @staticmethod
    def ones(shape: list[int], dtype: str) -> _FakeTensor:
        if shape != [64, 64] or dtype != "float32":
            raise AssertionError((shape, dtype))
        return _FakeTensor()

    @staticmethod
    def matmul(left: _FakeTensor, right: _FakeTensor) -> _FakeTensor:
        if not isinstance(left, _FakeTensor) or not isinstance(right, _FakeTensor):
            raise AssertionError("unexpected tensors")
        return _FakeTensor()


class TrainingRuntimeProbeTest(unittest.TestCase):
    def test_probe_reports_runtime_and_executes_gpu_matmul(self) -> None:
        from browser_ocr.finetune.training import probe_paddle_runtime

        report = probe_paddle_runtime(_FakePaddle())
        self.assertEqual(report["status"], "ok")
        self.assertEqual(report["paddle_version"], "3.2.0")
        self.assertEqual(report["device_name"], "Fake GPU")
        self.assertEqual(report["compute_capability"], [8, 9])
        self.assertEqual(report["cuda_version"], "12.6")
        self.assertEqual(report["cudnn_version"], 90501)
        self.assertEqual(report["matmul_checksum"], 262144.0)

class RecognizerTrainingPlanTest(unittest.TestCase):
    def test_training_overrides_resume_from_complete_checkpoint(self) -> None:
        from browser_ocr.finetune.training import build_training_overrides

        overrides = build_training_overrides(
            dataset_root="/data",
            train_labels="/export/train.txt",
            val_labels="/export/val.txt",
            pretrained_model="/weights/base.pdparams",
            checkpoint="/run/model/iter_epoch_3",
            output_dir="/run/model",
            batch_size=32,
            epochs=10,
        )
        self.assertEqual(overrides["Global.epoch_num"], 10)
        self.assertEqual(overrides["Global.checkpoints"], "/run/model/iter_epoch_3")
        self.assertEqual(overrides["Global.pretrained_model"], "/weights/base.pdparams")
        self.assertEqual(overrides["Global.save_epoch_step"], 1)
        self.assertEqual(overrides["Global.print_batch_step"], 10)
        self.assertEqual(overrides["Train.sampler.first_bs"], 32)

    def test_training_overrides_bind_explicit_learning_rate_and_warmup(self) -> None:
        from browser_ocr.finetune.training import build_training_overrides

        overrides = build_training_overrides(
            dataset_root="/data",
            train_labels="/export/train.txt",
            val_labels="/export/val.txt",
            pretrained_model="/weights/base.pdparams",
            checkpoint=None,
            output_dir="/run/model",
            batch_size=32,
            epochs=10,
            learning_rate=0.0001,
            warmup_epochs=1,
        )
        self.assertEqual(overrides["Optimizer.lr.learning_rate"], 0.0001)
        self.assertEqual(overrides["Optimizer.lr.warmup_epoch"], 1)

        with self.assertRaisesRegex(ValueError, "learning rate"):
            build_training_overrides(
                dataset_root="/data",
                train_labels="/export/train.txt",
                val_labels="/export/val.txt",
                pretrained_model="/weights/base.pdparams",
                checkpoint=None,
                output_dir="/run/model",
                batch_size=32,
                epochs=10,
                learning_rate=0.0,
                warmup_epochs=1,
            )

    def test_highest_complete_epoch_checkpoint_ignores_partial_newer_epoch(self) -> None:
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from browser_ocr.finetune.training import find_resume_checkpoint

        with TemporaryDirectory() as raw:
            model = Path(raw)
            for suffix in (".pdparams", ".pdopt", ".states"):
                (model / f"iter_epoch_2{suffix}").write_text("ok")
            (model / "iter_epoch_3.pdparams").write_text("partial")
            self.assertEqual(find_resume_checkpoint(model), model / "iter_epoch_2")

    def test_export_identity_changes_when_split_or_label_content_changes(self) -> None:
        import json
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from browser_ocr.finetune.training import export_identity

        with TemporaryDirectory() as raw:
            root = Path(raw)
            (root / "split.json").write_text(json.dumps({"splits": {"test": ["a"]}}), encoding="utf-8")
            for name in ("train", "val", "test"):
                (root / f"{name}.txt").write_text(f"images/{name}.png\t{name}\n", encoding="utf-8")
            first = export_identity(root)

            (root / "test.txt").write_text("images/test.png\tchanged\n", encoding="utf-8")
            second = export_identity(root)
            self.assertNotEqual(first, second)

            (root / "test.txt").write_text("images/test.png\ttest\n", encoding="utf-8")
            (root / "split.json").write_text(json.dumps({"splits": {"test": ["b"]}}), encoding="utf-8")
            third = export_identity(root)
            self.assertNotEqual(first, third)

class TrainingCommandStreamingTest(unittest.TestCase):
    def test_float_override_uses_plain_decimal_not_scientific_notation(self) -> None:
        from browser_ocr.finetune.train_cli import _format_override

        self.assertEqual(_format_override(0.00005), "0.00005")

    def test_stream_command_can_avoid_memory_capture_and_append_log(self) -> None:
        import sys
        from tempfile import TemporaryDirectory
        from pathlib import Path
        from browser_ocr.finetune.runner_io import stream_command

        with TemporaryDirectory() as raw:
            root = Path(raw)
            log = root / "train.log"
            first = stream_command(
                [sys.executable, "-c", "print('first')"],
                cwd=root,
                log_path=log,
                capture=False,
            )
            second = stream_command(
                [sys.executable, "-c", "print('second')"],
                cwd=root,
                log_path=log,
                capture=False,
                append=True,
            )
            self.assertEqual(first, "")
            self.assertEqual(second, "")
            self.assertEqual(log.read_text(encoding="utf-8"), "first\nsecond\n")

if __name__ == "__main__":
    unittest.main()
