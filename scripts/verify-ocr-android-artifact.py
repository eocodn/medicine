#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

RUNTIME_ROOT = "assets/ocr-assets/"
MANIFEST_PATH = RUNTIME_ROOT + "runtime-manifest.json"


def verify(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Android artifact is missing: {path}")
    with zipfile.ZipFile(path) as archive:
        names = set(archive.namelist())
        if MANIFEST_PATH not in names:
            raise ValueError("packaged OCR runtime manifest is missing")
        try:
            manifest = json.loads(archive.read(MANIFEST_PATH))
        except (json.JSONDecodeError, UnicodeDecodeError) as error:
            raise ValueError("packaged OCR runtime manifest is invalid") from error
        if manifest.get("schema_version") != 1 or not isinstance(manifest.get("files"), dict):
            raise ValueError("packaged OCR runtime manifest contract is unsupported")
        files = manifest["files"]
        if not files:
            raise ValueError("packaged OCR runtime manifest has no payload files")
        for relative, expected in files.items():
            if not isinstance(relative, str) or relative.startswith("/") or ".." in Path(relative).parts:
                raise ValueError(f"invalid OCR runtime path in manifest: {relative!r}")
            member = RUNTIME_ROOT + relative
            if member not in names:
                raise ValueError(f"packaged OCR runtime file is missing: {relative}")
            payload = archive.read(member)
            if expected.get("size_bytes") != len(payload):
                raise ValueError(f"packaged OCR runtime size mismatch: {relative}")
            digest = hashlib.sha256(payload).hexdigest()
            if expected.get("sha256") != digest:
                raise ValueError(f"packaged OCR runtime hash mismatch: {relative}")
        if "assets/ocr-intake.js" not in names:
            raise ValueError("packaged OCR intake UI is missing")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <apk-or-aab>", file=sys.stderr)
        return 2
    try:
        verify(Path(argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"verified packaged OCR Android artifact: {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
