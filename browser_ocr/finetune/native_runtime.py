from __future__ import annotations

import hashlib
import importlib.metadata as importlib_metadata
import subprocess
from functools import lru_cache
from pathlib import Path


_CONTENT_BOUND_PACKAGE_NAMES = {
    "libc6",
    "libgcc-s1",
    "libstdc++6",
    "libgomp1",
    "libglib2.0-0",
    "libglib2.0-0t64",
    "libgl1",
    "libgl1-mesa-dri",
    "libglvnd0",
    "libglx0",
    "libglx-mesa0",
    "zlib1g",
}
_CONTENT_BOUND_PYTHON_DISTRIBUTIONS = {
    "numpy",
    "onnxruntime",
    "onnxruntime-gpu",
    "opencv-contrib-python",
    "opencv-python",
    "opencv-python-headless",
    "paddlepaddle",
    "paddlepaddle-gpu",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@lru_cache(maxsize=1024)
def _sha256_file_snapshot(path_text: str, size: int, mtime_ns: int, ctime_ns: int) -> str:
    path = Path(path_text)
    before = path.stat()
    if (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (size, mtime_ns, ctime_ns):
        raise RuntimeError(f"OCR producer native payload changed before fingerprinting: {path}")
    digest = _sha256_file(path)
    after = path.stat()
    if (after.st_size, after.st_mtime_ns, after.st_ctime_ns) != (size, mtime_ns, ctime_ns):
        raise RuntimeError(f"OCR producer native payload changed during fingerprinting: {path}")
    return digest


def _sha256_stable_snapshot(path: Path) -> str:
    stat = path.stat()
    return _sha256_file_snapshot(
        str(path),
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _run_dpkg(*args: str) -> str:
    try:
        result = subprocess.run(
            ["dpkg-query", *args],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("OCR producer identity requires a working dpkg-query runtime inventory") from exc
    return result.stdout


def _is_native_python_payload(path: Path) -> bool:
    name = path.name.lower()
    return ".so" in name or name.endswith((".pyd", ".dll", ".dylib"))


def python_native_runtime_identity() -> dict[str, dict[str, str]]:
    payloads: dict[str, dict[str, str]] = {}
    for distribution in importlib_metadata.distributions():
        name = str(distribution.metadata.get("Name") or "").lower()
        if not name or (name not in _CONTENT_BOUND_PYTHON_DISTRIBUTIONS and not name.startswith("nvidia-")):
            continue
        files = distribution.files or ()
        native_files: dict[str, str] = {}
        for relative in sorted(files, key=lambda item: str(item)):
            relative_path = Path(str(relative))
            if not _is_native_python_payload(relative_path):
                continue
            resolved = Path(distribution.locate_file(relative)).resolve()
            if not resolved.is_file():
                raise RuntimeError(f"OCR producer Python native payload is missing: {name}/{relative_path}")
            native_files[relative_path.as_posix()] = _sha256_stable_snapshot(resolved)
        if native_files:
            payloads[name] = native_files
        elif name in _CONTENT_BOUND_PYTHON_DISTRIBUTIONS:
            raise RuntimeError(f"OCR producer Python distribution has no native payload inventory: {name}")
    return dict(sorted(payloads.items()))


def native_runtime_identity() -> dict[str, object]:
    rows = sorted(
        line.strip()
        for line in _run_dpkg("-W", "-f=${binary:Package}\t${Version}\n").splitlines()
        if line.strip()
    )
    if not rows:
        raise RuntimeError("OCR producer native runtime package inventory is empty")

    selected_packages = []
    for row in rows:
        package, separator, version = row.partition("\t")
        if not separator or not package or not version:
            raise RuntimeError("OCR producer native runtime package inventory is malformed")
        base_name = package.split(":", 1)[0]
        if base_name in _CONTENT_BOUND_PACKAGE_NAMES:
            selected_packages.append(package)

    libraries: dict[str, str] = {}
    for package in sorted(selected_packages):
        for raw_path in _run_dpkg("-L", package).splitlines():
            path = Path(raw_path.strip())
            if not path.is_absolute() or ".so" not in path.name or not path.is_file():
                continue
            libraries[str(path)] = _sha256_file(path)
    if not libraries:
        raise RuntimeError("OCR producer native runtime library inventory is empty")
    return {"packages": rows, "libraries": dict(sorted(libraries.items()))}


__all__ = ["native_runtime_identity", "python_native_runtime_identity"]