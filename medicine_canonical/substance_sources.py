from __future__ import annotations

import hashlib
import json
import os
import urllib.request
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


APP_TIMEZONE = ZoneInfo("Asia/Seoul")
OPENFDA_DOWNLOAD_MANIFEST = "https://api.fda.gov/download.json"
OPENFDA_UNII_DATASET_KEY = "openfda_unii:all"
OPENFDA_UNII_FILENAME = "openfda_unii.json.zip"

ManifestFetcher = Callable[[], dict]
PartitionFetcher = Callable[[str], bytes]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"Accept": "application/zip,application/octet-stream"})
    with urllib.request.urlopen(request, timeout=90) as response:
        return response.read()


def inspect_unii_archive(data: bytes) -> tuple[list[dict], dict]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            members = [name for name in archive.namelist() if not name.endswith("/")]
            if len(members) != 1:
                raise RuntimeError(f"openFDA UNII archive expected one JSON member, got {len(members)}")
            with archive.open(members[0]) as handle:
                payload = json.load(handle)
    except (zipfile.BadZipFile, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise RuntimeError(f"invalid openFDA UNII archive: {exc}") from exc
    results = payload.get("results")
    meta = payload.get("meta") or {}
    if not isinstance(results, list):
        raise RuntimeError("openFDA UNII archive results are not a list")
    for index, row in enumerate(results, start=1):
        if not isinstance(row, dict) or not str(row.get("substance_name") or "").strip() or not str(row.get("unii") or "").strip():
            raise RuntimeError(f"openFDA UNII archive row {index} is missing substance_name or unii")
    reported = ((meta.get("results") or {}).get("total"))
    if reported is not None and int(reported) != len(results):
        raise RuntimeError(f"openFDA UNII archive row-count mismatch: metadata {reported}, rows {len(results)}")
    return results, meta


def _write_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".write")
    with temp.open("wb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)


def _write_json_atomic(path: Path, payload: dict) -> None:
    data = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8")
    _write_atomic(path, data)


def sync_openfda_unii(
    raw_dir: str | Path,
    *,
    manifest_fetcher: ManifestFetcher | None = None,
    partition_fetcher: PartitionFetcher | None = None,
) -> dict:
    manifest_fetcher = manifest_fetcher or (lambda: _fetch_json(OPENFDA_DOWNLOAD_MANIFEST))
    partition_fetcher = partition_fetcher or _fetch_bytes
    manifest = manifest_fetcher()
    try:
        dataset = manifest["results"]["other"]["unii"]
        partitions = dataset["partitions"]
    except (KeyError, TypeError) as exc:
        raise RuntimeError("openFDA download manifest has no other/unii dataset") from exc
    if not isinstance(partitions, list) or len(partitions) != 1:
        raise RuntimeError(f"openFDA UNII expected one partition, got {len(partitions) if isinstance(partitions, list) else 'invalid'}")
    partition = partitions[0]
    url = str(partition.get("file") or "").strip()
    if not url:
        raise RuntimeError("openFDA UNII partition has no download URL")
    data = partition_fetcher(url)
    records, archive_meta = inspect_unii_archive(data)
    expected = int(dataset.get("total_records") or partition.get("records") or 0)
    if expected and len(records) != expected:
        raise RuntimeError(f"openFDA UNII row-count mismatch: manifest {expected}, archive {len(records)}")

    root = Path(raw_dir)
    path = root / OPENFDA_UNII_FILENAME
    _write_atomic(path, data)
    metadata = {
        "dataset_key": OPENFDA_UNII_DATASET_KEY,
        "source_family": "openfda_unii",
        "source_locator": url,
        "effective_date": str(dataset.get("export_date") or archive_meta.get("last_updated") or "").strip() or None,
        "fetched_at": datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
        "row_count": len(records),
        "sha256": _sha256_bytes(data),
        "manifest_locator": OPENFDA_DOWNLOAD_MANIFEST,
        "manifest_last_updated": (manifest.get("meta") or {}).get("last_updated"),
        "archive_last_updated": archive_meta.get("last_updated"),
        "authority": "FDA GSRS / UNII via openFDA",
        "license": archive_meta.get("license"),
    }
    _write_json_atomic(path.with_suffix(path.suffix + ".meta.json"), metadata)
    return metadata


__all__ = [
    "OPENFDA_DOWNLOAD_MANIFEST",
    "OPENFDA_UNII_DATASET_KEY",
    "OPENFDA_UNII_FILENAME",
    "inspect_unii_archive",
    "sync_openfda_unii",
]