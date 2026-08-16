from __future__ import annotations

import hashlib
import os
import zipfile
from pathlib import Path

from .xlsx import XLSX_DATASETS


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _expected_files() -> list[str]:
    return sorted(XLSX_DATASETS)


def pack_kids_bundle(kids_dir: str | Path, output_path: str | Path) -> dict:
    root = Path(kids_dir)
    output = Path(output_path)
    expected = _expected_files()
    missing = [name for name in expected if not (root / name).is_file()]
    if missing:
        raise FileNotFoundError(f"missing required KIDS source files: {', '.join(missing)}")
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    try:
        with zipfile.ZipFile(
            temporary,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as archive:
            for filename in expected:
                data = (root / filename).read_bytes()
                info = zipfile.ZipInfo(filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = zipfile.ZIP_DEFLATED
                info.external_attr = 0o600 << 16
                archive.writestr(info, data, compresslevel=9)
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    return {
        "bundle_path": str(output),
        "sha256": _sha256(output),
        "size_bytes": output.stat().st_size,
        "files": expected,
    }


def extract_kids_bundle(bundle_path: str | Path, output_dir: str | Path) -> dict:
    bundle = Path(bundle_path)
    output = Path(output_dir)
    expected = _expected_files()
    with zipfile.ZipFile(bundle, "r") as archive:
        members = archive.infolist()
        names = sorted(info.filename for info in members)
        if names != expected:
            raise ValueError("KIDS source bundle must contain exactly the expected XLSX files at archive root")
        for info in members:
            if info.is_dir() or Path(info.filename).name != info.filename:
                raise ValueError("KIDS source bundle members must be flat files")
        output.mkdir(parents=True, exist_ok=True)
        for info in members:
            target = output / info.filename
            temporary = target.with_name(target.name + ".tmp")
            temporary.write_bytes(archive.read(info))
            os.replace(temporary, target)
    return {
        "bundle_path": str(bundle),
        "sha256": _sha256(bundle),
        "size_bytes": bundle.stat().st_size,
        "files": expected,
        "output_dir": str(output),
    }


__all__ = ["extract_kids_bundle", "pack_kids_bundle"]
