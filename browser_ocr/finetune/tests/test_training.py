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

if __name__ == "__main__":
    unittest.main()
