#!/usr/bin/env python3
from __future__ import annotations

import sys
import zipfile
from pathlib import Path


FORBIDDEN_PATH_MARKERS = (
    "ocr-intake",
    "ocr-assets",
    ".onnx",
)
FORBIDDEN_CONTENT_MARKERS = (
    b"MEDICINE_OCR_",
    b"ocr-intake",
    b"ocr-import",
    b"/ocr-assets/",
)


def verify(path: Path) -> None:
    if not path.is_file():
        raise ValueError(f"Android artifact is missing: {path}")

    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            lowered = info.filename.lower()
            if any(marker in lowered for marker in FORBIDDEN_PATH_MARKERS):
                raise ValueError(f"OCR payload path found in Android artifact: {info.filename}")
            if info.is_dir() or info.file_size > 8 * 1024 * 1024:
                continue
            payload = archive.read(info)
            if any(marker in payload for marker in FORBIDDEN_CONTENT_MARKERS):
                raise ValueError(f"OCR payload content found in Android artifact: {info.filename}")


def main(argv: list[str]) -> int:
    if len(argv) != 2:
        print(f"usage: {Path(argv[0]).name} <apk-or-aab>", file=sys.stderr)
        return 2
    try:
        verify(Path(argv[1]))
    except (OSError, ValueError, zipfile.BadZipFile) as error:
        print(str(error), file=sys.stderr)
        return 1
    print(f"verified no-OCR Android artifact: {argv[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))