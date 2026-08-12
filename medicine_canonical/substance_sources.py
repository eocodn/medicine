from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo


APP_TIMEZONE = ZoneInfo("Asia/Seoul")
OPENFDA_DOWNLOAD_MANIFEST = "https://api.fda.gov/download.json"
OPENFDA_UNII_DATASET_KEY = "openfda_unii:all"
OPENFDA_UNII_FILENAME = "openfda_unii.json.zip"
FDA_GSRS_UNII_NAMES_LATEST = "https://precision.fda.gov/uniisearch/archive/latest/UNIIs.zip"
FDA_GSRS_UNII_NAMES_DATASET_KEY = "fda_gsrs_unii_names:all"
FDA_GSRS_UNII_NAMES_FILENAME = "fda_gsrs_unii_names.zip"

ManifestFetcher = Callable[[], dict]
PartitionFetcher = Callable[[str], bytes]
ArchiveFetcher = Callable[[str], bytes]


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_bytes(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/zip,application/octet-stream",
            "User-Agent": "medicine-canonical/1.0",
        },
    )
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


def _gsrs_names_member(archive: zipfile.ZipFile) -> str:
    members = [
        name
        for name in archive.namelist()
        if not name.endswith("/")
        and Path(name).name.startswith("UNII_Names_")
        and name.lower().endswith(".txt")
    ]
    if len(members) != 1:
        raise RuntimeError(
            f"FDA GSRS UNII Names archive expected one UNII_Names text member, got {len(members)}"
        )
    return members[0]


def _gsrs_names_effective_date(member: str) -> str:
    token = Path(member).stem.removeprefix("UNII_Names_")
    try:
        return datetime.strptime(token, "%d%b%Y").date().isoformat()
    except ValueError as exc:
        raise RuntimeError(
            f"FDA GSRS UNII Names archive has unrecognized date token: {token}"
        ) from exc


def iter_gsrs_unii_names(data: bytes) -> Iterator[dict[str, str]]:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            member = _gsrs_names_member(archive)
            with archive.open(member) as raw, io.TextIOWrapper(
                raw, encoding="utf-8-sig", newline=""
            ) as text:
                reader = csv.DictReader(text, delimiter="\t")
                expected = {"Name", "TYPE", "UNII", "Display Name"}
                if set(reader.fieldnames or []) != expected:
                    raise RuntimeError(
                        "FDA GSRS UNII Names columns mismatch: "
                        f"expected {sorted(expected)}, got {reader.fieldnames}"
                    )
                for index, row in enumerate(reader, start=1):
                    name = str(row.get("Name") or "").strip()
                    name_type = str(row.get("TYPE") or "").strip()
                    unii = str(row.get("UNII") or "").strip()
                    display_name = str(row.get("Display Name") or "").strip()
                    if not name or not name_type or not unii or not display_name:
                        raise RuntimeError(
                            f"FDA GSRS UNII Names row {index} has an empty required field"
                        )
                    yield {
                        "name": name,
                        "name_type": name_type,
                        "unii": unii,
                        "display_name": display_name,
                    }
    except (zipfile.BadZipFile, UnicodeDecodeError, csv.Error) as exc:
        raise RuntimeError(f"invalid FDA GSRS UNII Names archive: {exc}") from exc


def inspect_gsrs_unii_names_archive(data: bytes) -> dict:
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            member = _gsrs_names_member(archive)
            effective_date = _gsrs_names_effective_date(member)
    except zipfile.BadZipFile as exc:
        raise RuntimeError(f"invalid FDA GSRS UNII Names archive: {exc}") from exc
    row_count = sum(1 for _ in iter_gsrs_unii_names(data))
    if row_count < 1:
        raise RuntimeError("FDA GSRS UNII Names archive is empty")
    return {
        "effective_date": effective_date,
        "member": member,
        "row_count": row_count,
    }


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


def sync_fda_gsrs_unii_names(
    raw_dir: str | Path,
    *,
    archive_fetcher: ArchiveFetcher | None = None,
) -> dict:
    archive_fetcher = archive_fetcher or _fetch_bytes
    data = archive_fetcher(FDA_GSRS_UNII_NAMES_LATEST)
    inspected = inspect_gsrs_unii_names_archive(data)
    root = Path(raw_dir)
    path = root / FDA_GSRS_UNII_NAMES_FILENAME
    _write_atomic(path, data)
    metadata = {
        "dataset_key": FDA_GSRS_UNII_NAMES_DATASET_KEY,
        "source_family": "fda_gsrs_unii_names",
        "source_locator": FDA_GSRS_UNII_NAMES_LATEST,
        "effective_date": inspected["effective_date"],
        "fetched_at": datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
        "row_count": inspected["row_count"],
        "sha256": _sha256_bytes(data),
        "archive_member": inspected["member"],
        "authority": "FDA GSRS / UNII Names via precisionFDA",
    }
    _write_json_atomic(path.with_suffix(path.suffix + ".meta.json"), metadata)
    return metadata


def sync_substance_identity_sources(raw_dir: str | Path) -> dict:
    return {
        "openfda_unii": sync_openfda_unii(raw_dir),
        "fda_gsrs_unii_names": sync_fda_gsrs_unii_names(raw_dir),
    }


__all__ = [
    "FDA_GSRS_UNII_NAMES_DATASET_KEY",
    "FDA_GSRS_UNII_NAMES_FILENAME",
    "FDA_GSRS_UNII_NAMES_LATEST",
    "OPENFDA_DOWNLOAD_MANIFEST",
    "OPENFDA_UNII_DATASET_KEY",
    "OPENFDA_UNII_FILENAME",
    "inspect_gsrs_unii_names_archive",
    "inspect_unii_archive",
    "iter_gsrs_unii_names",
    "sync_fda_gsrs_unii_names",
    "sync_openfda_unii",
    "sync_substance_identity_sources",
]
