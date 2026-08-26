from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path

from .dataset import DatasetError


def format_paddle_override(value: object) -> str:
    if isinstance(value, bool):
        return "True" if value else "False"
    if isinstance(value, list):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    if isinstance(value, float):
        return format(Decimal(str(value)), "f")
    return str(value)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def export_identity(export_dir: str | Path) -> dict[str, object]:
    root = Path(export_dir)
    required = [root / "split.json", *(root / f"{name}.txt" for name in ("train", "val", "test"))]
    missing = [path.name for path in required if not path.is_file()]
    if missing:
        raise DatasetError(f"Paddle export identity files are missing: {', '.join(missing)}")
    return {
        "schema_version": 1,
        "split_sha256": _sha256_file(root / "split.json"),
        "label_sha256": {
            name: _sha256_file(root / f"{name}.txt")
            for name in ("train", "val", "test")
        },
    }


def subset_label_file(source: str | Path, target: str | Path, count: int) -> int:
    source_path = Path(source)
    target_path = Path(target)
    lines = source_path.read_text(encoding="utf-8").splitlines()
    if len(lines) < count:
        raise DatasetError(f"label file {source_path} has only {len(lines)} rows; need {count}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_text("\n".join(lines[:count]) + "\n", encoding="utf-8")
    return count


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


def build_training_overrides(
    *,
    dataset_root: str,
    train_labels: str,
    val_labels: str,
    pretrained_model: str,
    checkpoint: str | None,
    output_dir: str,
    batch_size: int,
    epochs: int,
    learning_rate: float = 0.0005,
    warmup_epochs: int = 5,
) -> dict[str, object]:
    if batch_size <= 0:
        raise ValueError("batch_size must be positive")
    if epochs <= 0:
        raise ValueError("epochs must be positive")
    if learning_rate <= 0:
        raise ValueError("learning rate must be positive")
    if warmup_epochs < 0 or warmup_epochs >= epochs:
        raise ValueError("warmup epochs must be non-negative and less than total epochs")
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
        "Optimizer.lr.learning_rate": learning_rate,
        "Optimizer.lr.warmup_epoch": warmup_epochs,
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
