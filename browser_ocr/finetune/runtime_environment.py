from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import json
import os
import platform
import site
import subprocess
import sys
from pathlib import Path

from .dataset import DatasetError
from .native_runtime import native_runtime_identity
from .native_runtime import python_native_runtime_identity


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _installed_distributions() -> list[tuple[str, str]]:
    # Some libraries add private vendor directories to sys.path at import time.
    # Inventory only interpreter-managed site roots so the producer identity does
    # not depend on whether such a library happened to be imported already.
    roots = [Path(path).resolve() for path in site.getsitepackages()]
    if site.ENABLE_USER_SITE:
        user_site = Path(site.getusersitepackages()).resolve()
        if user_site not in roots:
            roots.append(user_site)
    distributions = {
        (str(dist.metadata.get("Name") or "").lower(), str(dist.version))
        for dist in importlib_metadata.distributions(path=[str(path) for path in roots])
        if dist.metadata.get("Name")
    }
    return sorted(distributions)


def _gpu_runtime_identity() -> dict[str, object]:
    import paddle

    from .training import probe_paddle_runtime

    report = dict(probe_paddle_runtime(paddle))
    try:
        nvidia = subprocess.run(
            [
                "nvidia-smi",
                "--query-gpu=uuid,name,driver_version,compute_cap",
                "--format=csv,noheader,nounits",
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise DatasetError("GPU OCR producer identity requires a working nvidia-smi runtime report") from exc
    rows = sorted(line.strip() for line in nvidia.stdout.splitlines() if line.strip())
    if not rows:
        raise DatasetError("GPU OCR producer identity received an empty nvidia-smi runtime report")
    report["nvidia_smi"] = rows
    return report


def runtime_environment_sha256(recognizer_device: str) -> str:
    finetune_root = Path(__file__).resolve().parent
    runtime_contract = {
        name: _sha256_file(finetune_root / name)
        for name in ("Dockerfile.train", "requirements-train.lock", "requirements-paddle-runtime.lock")
    }
    payload = {
        "python": sys.version,
        "python_implementation": platform.python_implementation(),
        "machine": platform.machine(),
        "system": platform.system(),
        "distributions": _installed_distributions(),
        "native_runtime": native_runtime_identity(),
        "python_native_runtime": python_native_runtime_identity(),
        "runtime_contract": runtime_contract,
        "runtime_identity_implementation_sha256": _sha256_file(Path(__file__)),
        "recognizer_device": recognizer_device,
    }
    if recognizer_device == "gpu":
        payload["gpu_runtime"] = {
            **_gpu_runtime_identity(),
            "cuda_visible_devices": os.environ.get("CUDA_VISIBLE_DEVICES"),
            "nvidia_visible_devices": os.environ.get("NVIDIA_VISIBLE_DEVICES"),
        }
    encoded = json.dumps(payload, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = ["runtime_environment_sha256"]
