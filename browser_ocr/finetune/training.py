from __future__ import annotations

from .dataset import DatasetError


def probe_paddle_runtime(paddle: object) -> dict[str, object]:
    device = paddle.device
    if not device.is_compiled_with_cuda():
        raise DatasetError("PaddlePaddle was not compiled with CUDA support")
    device_count = device.cuda.device_count()
    if device_count < 1:
        raise DatasetError("no CUDA device is visible to PaddlePaddle")

    paddle.set_device("gpu:0")
    left = paddle.ones([64, 64], dtype="float32")
    product = paddle.matmul(left, left)
    device.synchronize()
    checksum = float(product.sum())
    expected_checksum = 262144.0
    if checksum != expected_checksum:
        raise DatasetError(
            f"GPU matrix multiplication checksum mismatch: expected {expected_checksum}, got {checksum}"
        )

    capability = device.cuda.get_device_capability(0)
    return {
        "schema_version": 1,
        "status": "ok",
        "paddle_version": paddle.__version__,
        "compiled_with_cuda": True,
        "device_count": device_count,
        "device_name": device.cuda.get_device_name(0),
        "compute_capability": list(capability),
        "cuda_version": paddle.version.cuda(),
        "cudnn_version": device.get_cudnn_version(),
        "matmul_checksum": checksum,
    }


def build_smoke_overrides(
    *,
    dataset_root: str,
    train_labels: str,
    val_labels: str,
    pretrained_model: str,
    output_dir: str,
    batch_size: int,
) -> dict[str, object]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    return {
        "Global.pretrained_model": pretrained_model,
        "Global.save_model_dir": output_dir,
        "Global.epoch_num": 1,
        "Global.print_batch_step": 1,
        "Global.save_epoch_step": 1,
        "Global.eval_batch_step": [0, 4],
        "Global.cal_metric_during_train": True,
        "Global.distributed": False,
        "Global.use_gpu": True,
        "Train.dataset.data_dir": dataset_root,
        "Train.dataset.label_file_list": [train_labels],
        "Train.sampler.first_bs": batch_size,
        "Train.loader.batch_size_per_card": batch_size,
        "Train.loader.num_workers": 2,
        "Eval.dataset.data_dir": dataset_root,
        "Eval.dataset.label_file_list": [val_labels],
        "Eval.loader.batch_size_per_card": batch_size,
        "Eval.loader.num_workers": 2,
    }


def build_baseline_overrides(
    *,
    dataset_root: str,
    train_labels: str,
    val_labels: str,
    pretrained_model: str,
    checkpoint: str | None,
    output_dir: str,
    batch_size: int,
    epochs: int,
) -> dict[str, object]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    overrides: dict[str, object] = {
        "Global.pretrained_model": pretrained_model,
        "Global.save_model_dir": output_dir,
        "Global.epoch_num": epochs,
        "Global.print_batch_step": 10,
        "Global.save_epoch_step": 1,
        "Global.eval_batch_step": [0, 100],
        "Global.cal_metric_during_train": True,
        "Global.distributed": False,
        "Global.use_gpu": True,
        "Train.dataset.data_dir": dataset_root,
        "Train.dataset.label_file_list": [train_labels],
        "Train.sampler.first_bs": batch_size,
        "Train.loader.batch_size_per_card": batch_size,
        "Train.loader.num_workers": 2,
        "Eval.dataset.data_dir": dataset_root,
        "Eval.dataset.label_file_list": [val_labels],
        "Eval.loader.batch_size_per_card": batch_size,
        "Eval.loader.num_workers": 2,
    }
    if checkpoint is not None:
        overrides["Global.checkpoints"] = checkpoint
    return overrides


def find_resume_checkpoint(model_dir: str | Path) -> Path | None:
    import re
    from pathlib import Path

    root = Path(model_dir)
    candidates: list[tuple[int, Path]] = []
    for params_path in root.glob("iter_epoch_*.pdparams"):
        match = re.fullmatch(r"iter_epoch_(\d+)\.pdparams", params_path.name)
        if match is None:
            continue
        prefix = params_path.with_suffix("")
        if all(Path(str(prefix) + suffix).is_file() for suffix in (".pdparams", ".pdopt", ".states")):
            candidates.append((int(match.group(1)), prefix))
    if not candidates:
        return None
    candidates.sort(key=lambda item: item[0])
    return candidates[-1][1]


def parse_eval_metrics(log_text: str) -> dict[str, float]:
    import re

    marker = "metric eval ***************"
    marker_index = log_text.rfind(marker)
    if marker_index < 0:
        raise DatasetError("PaddleOCR evaluation log does not contain the final metric section")
    section = log_text[marker_index + len(marker) :]
    metrics: dict[str, float] = {}
    for key in ("acc", "norm_edit_dis", "fps"):
        match = re.search(rf"ppocr INFO:\s+{re.escape(key)}:([-+0-9.eE]+)", section)
        if match is None:
            raise DatasetError(f"PaddleOCR evaluation log is missing metric {key}")
        metrics[key] = float(match.group(1))
    return metrics
