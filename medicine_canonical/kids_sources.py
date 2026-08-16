from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .xlsx import inspect_xlsx_source


KIDS_BASE_URL = "https://www.drugsafe.or.kr"
KIDS_SOURCE_MANIFEST = ".kids-source-manifest.json"
MAX_XLSX_BYTES = 16 * 1024 * 1024


@dataclass(frozen=True)
class KidsDownload:
    filename: str
    category: str
    page_path: str

    @property
    def page_url(self) -> str:
        return f"{KIDS_BASE_URL}{self.page_path}"


KIDS_DOWNLOADS: tuple[KidsDownload, ...] = (
    KidsDownload("combination.xlsx", "combination_contraindication", "/iwt/ds/ko/useinfo/EgovDurInfoSerJoin.do"),
    KidsDownload("age.xlsx", "age_contraindication", "/iwt/ds/ko/useinfo/EgovDurInfoSerAge.do"),
    KidsDownload("pregnancy.xlsx", "pregnancy_contraindication", "/iwt/ds/ko/useinfo/EgovDurInfoSerPn.do"),
    KidsDownload("dose.xlsx", "dose_caution", "/iwt/ds/ko/useinfo/EgovDurInfoSerVolume.do"),
    KidsDownload("duration.xlsx", "duration_caution", "/iwt/ds/ko/useinfo/EgovDurInfoSerTerm.do"),
    KidsDownload("elderly.xlsx", "elderly_caution", "/iwt/ds/ko/useinfo/EgovDurInfoSerOld.do"),
    KidsDownload(
        "therapeutic_duplication.xlsx",
        "therapeutic_duplication_caution",
        "/iwt/ds/ko/useinfo/EgovDurInfoSerEff.do",
    ),
    KidsDownload("lactation.xlsx", "lactation_caution", "/iwt/ds/ko/useinfo/EgovDurInfoSerSuu.do"),
)


FetchPage = Callable[[str], str]
FetchAttachment = Callable[[str], bytes]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request_bytes(url: str, *, max_bytes: int, timeout: int = 45, attempts: int = 4) -> bytes:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "*/*",
                "User-Agent": "medicine-reference-sync/1.0",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                length = response.headers.get("Content-Length")
                if length and int(length) > max_bytes:
                    raise ValueError(f"KIDS response exceeds {max_bytes} bytes")
                data = response.read(max_bytes + 1)
                if len(data) > max_bytes:
                    raise ValueError(f"KIDS response exceeds {max_bytes} bytes")
                return data
        except urllib.error.HTTPError as exc:
            if exc.code not in {408, 429, 500, 502, 503, 504}:
                raise RuntimeError(f"KIDS request failed with HTTP {exc.code}: {url}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"KIDS request failed after {attempts} attempts: {url}: {last_error}") from last_error


def _fetch_page(url: str) -> str:
    return _request_bytes(url, max_bytes=4 * 1024 * 1024).decode("utf-8", "replace")


def _fetch_attachment(url: str) -> bytes:
    return _request_bytes(url, max_bytes=MAX_XLSX_BYTES)


def _current_attachment_id(html: str) -> str:
    centered_blocks = re.findall(
        r"<div\b[^>]*\balign\s*=\s*['\"]?center['\"]?[^>]*>(.*?)</div\s*>",
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    attachment_ids: list[str] = []
    for block in centered_blocks:
        match = re.search(
            r"fn_egov_downFile\(\s*['\"](FILE_\d+)['\"]\s*,\s*['\"]0['\"]\s*\)",
            block,
            flags=re.IGNORECASE,
        )
        if match:
            attachment_ids.append(match.group(1))
    unique = list(dict.fromkeys(attachment_ids))
    if len(unique) != 1:
        raise ValueError(
            f"expected exactly one current KIDS XLSX attachment in centered download block, found {len(unique)}"
        )
    return unique[0]


def _attachment_url(attachment_id: str) -> str:
    query = urllib.parse.urlencode({"atchFileId": attachment_id, "fileSn": "0"})
    return f"{KIDS_BASE_URL}/cmm/fms/FileDown.do?{query}"


def _validate_xlsx(path: Path, category: str) -> dict:
    try:
        inspected = inspect_xlsx_source(path, category)
    except Exception as exc:
        raise ValueError(f"{path.name} is not a valid KIDS XLSX source for {category}: {exc}") from exc
    if not inspected["effective_date"]:
        raise ValueError(f"{path.name} KIDS XLSX title does not contain an effective date")
    return inspected


def _manifest_bytes(sources: list[dict]) -> bytes:
    payload = {
        "schema_version": 1,
        "sources": sources,
    }
    return (json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n").encode("utf-8")


def _same_snapshot(output: Path, sources: list[dict]) -> bool:
    manifest = output / KIDS_SOURCE_MANIFEST
    if not manifest.is_file():
        return False
    try:
        current = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError):
        return False
    if current.get("schema_version") != 1 or current.get("sources") != sources:
        return False
    for source in sources:
        path = output / source["filename"]
        if not path.is_file():
            return False
        if _sha256_bytes(path.read_bytes()) != source["sha256"]:
            return False
    return True


def _replace_directory(staging: Path, output: Path) -> None:
    if not output.exists():
        os.replace(staging, output)
        return

    backup = output.with_name(f".{output.name}.previous")
    if backup.exists():
        raise RuntimeError(f"stale KIDS sync backup requires manual review: {backup}")
    os.replace(output, backup)
    try:
        os.replace(staging, output)
    except Exception:
        os.replace(backup, output)
        raise
    shutil.rmtree(backup)


def sync_kids_xlsx_sources(
    output_dir: str | Path,
    *,
    fetch_page: FetchPage | None = None,
    fetch_attachment: FetchAttachment | None = None,
) -> dict:
    output = Path(output_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    page_reader = fetch_page or _fetch_page
    attachment_reader = fetch_attachment or _fetch_attachment
    staging = Path(tempfile.mkdtemp(prefix=f".{output.name}.sync-", dir=output.parent))
    sources: list[dict] = []
    try:
        for spec in KIDS_DOWNLOADS:
            html = page_reader(spec.page_url)
            attachment_id = _current_attachment_id(html)
            download_url = _attachment_url(attachment_id)
            data = attachment_reader(download_url)
            target = staging / spec.filename
            target.write_bytes(data)
            inspected = _validate_xlsx(target, spec.category)
            sources.append(
                {
                    "filename": spec.filename,
                    "category": spec.category,
                    "page_url": spec.page_url,
                    "download_url": download_url,
                    "attachment_id": attachment_id,
                    "sha256": _sha256_bytes(data),
                    "size_bytes": len(data),
                    "title": inspected["title"],
                    "effective_date": inspected["effective_date"],
                    "sheet": inspected["sheet"],
                    "header_row": inspected["header_row"],
                }
            )

        manifest_data = _manifest_bytes(sources)
        (staging / KIDS_SOURCE_MANIFEST).write_bytes(manifest_data)
        snapshot_id = f"sha256:{_sha256_bytes(manifest_data)}"
        if output.is_dir() and _same_snapshot(output, sources):
            shutil.rmtree(staging)
            return {
                "status": "unchanged",
                "output_dir": str(output),
                "snapshot_id": snapshot_id,
                "manifest_path": str(output / KIDS_SOURCE_MANIFEST),
                "sources": sources,
            }

        _replace_directory(staging, output)
        return {
            "status": "updated",
            "output_dir": str(output),
            "snapshot_id": snapshot_id,
            "manifest_path": str(output / KIDS_SOURCE_MANIFEST),
            "sources": sources,
        }
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise


__all__ = ["KIDS_DOWNLOADS", "KIDS_SOURCE_MANIFEST", "sync_kids_xlsx_sources"]