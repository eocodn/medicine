from __future__ import annotations

import hashlib
import subprocess
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


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


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


__all__ = ["native_runtime_identity"]