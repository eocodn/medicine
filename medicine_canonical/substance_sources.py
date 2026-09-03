from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import time
import urllib.request
import zipfile
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import Callable, Iterator
from zoneinfo import ZoneInfo

from .job_lifecycle import ProgressCallback, progress_bar
from .release_io import maybe_report_progress
from .snapshot_io import sha256_file


APP_TIMEZONE = ZoneInfo("Asia/Seoul")
SUBSTANCE_HEARTBEAT_INTERVAL_SECONDS = 5.0
OPENFDA_DOWNLOAD_MANIFEST = "https://api.fda.gov/download.json"
OPENFDA_UNII_DATASET_KEY = "openfda_unii:all"
OPENFDA_UNII_FILENAME = "openfda_unii.json.zip"
FDA_GSRS_UNII_NAMES_LATEST = "https://precision.fda.gov/uniisearch/archive/latest/UNIIs.zip"
FDA_GSRS_UNII_NAMES_DATASET_KEY = "fda_gsrs_unii_names:all"
FDA_GSRS_UNII_NAMES_FILENAME = "fda_gsrs_unii_names.zip"

ManifestFetcher = Callable[[], dict]
PartitionFetcher = Callable[[str], bytes]
ArchiveFetcher = Callable[[str], bytes]


class _SubstanceSyncObserver:
    def __init__(
        self,
        dataset_key: str,
        checkpoint_path: Path,
        progress: ProgressCallback | None,
    ) -> None:
        self.dataset_key = dataset_key
        self.checkpoint_path = checkpoint_path
        self.progress = progress
        self._last_heartbeat = 0.0

    def _emit(self, status: str, **extra: object) -> None:
        if self.progress is None:
            return
        self.progress(
            {
                "job": "substance-source-sync",
                "dataset_key": self.dataset_key,
                "status": status,
                **extra,
            }
        )

    def started(self, *, resumed: bool) -> None:
        self._emit("started", resumed=resumed)

    def progress_update(self, current: int, total: int) -> None:
        self._emit(
            "progress",
            current=current,
            total=total,
            bar=progress_bar(current, total),
        )
        self.heartbeat("download")

    def heartbeat(self, phase: str, *, force: bool = False) -> None:
        now = time.monotonic()
        if not force and now - self._last_heartbeat < SUBSTANCE_HEARTBEAT_INTERVAL_SECONDS:
            return
        self._last_heartbeat = now
        self._emit("heartbeat", phase=phase)

    def checkpoint(self, *, sha256: str, size_bytes: int) -> None:
        self._emit(
            "checkpoint",
            checkpoint_path=str(self.checkpoint_path),
            sha256=sha256,
            size_bytes=size_bytes,
        )

    def completed(self, *, row_count: int, resumed: bool) -> None:
        self._emit("completed", row_count=row_count, resumed=resumed)

    def failed(self, error: BaseException) -> None:
        self._emit("failed", error=type(error).__name__, detail=str(error))


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _fetch_json(url: str) -> dict:
    request = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=45) as response:
        return json.loads(response.read().decode("utf-8"))


def _fetch_bytes(
    url: str,
    *,
    progress: Callable[[int, int], None] | None = None,
) -> bytes:
    request = urllib.request.Request(
        url,
        headers={
            "Accept": "application/zip,application/octet-stream",
            "User-Agent": "medicine-canonical/1.0",
        },
    )
    with urllib.request.urlopen(request, timeout=90) as response:
        length = response.headers.get("Content-Length")
        total = int(length) if length and str(length).isdigit() else 0
        payload = bytearray()
        processed = 0
        last_reported = 0
        while True:
            chunk = response.read(1024 * 1024)
            if not chunk:
                break
            payload.extend(chunk)
            processed += len(chunk)
            last_reported = maybe_report_progress(
                progress,
                processed,
                total,
                last_reported,
            )
        if progress is not None and processed != last_reported:
            progress(processed, total or processed)
        return bytes(payload)


def _fetch_with_heartbeat(
    fetch: Callable[[], bytes],
    observer: _SubstanceSyncObserver,
) -> bytes:
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(fetch)
        while True:
            done, _ = wait(
                {future},
                timeout=SUBSTANCE_HEARTBEAT_INTERVAL_SECONDS,
                return_when=FIRST_COMPLETED,
            )
            if done:
                return future.result()
            observer.heartbeat("download", force=True)


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
                fieldnames = reader.fieldnames or []
                title_case_headers = {"Name", "TYPE", "UNII", "Display Name"}
                uppercase_headers = {"NAME", "TYPE", "UNII", "DISPLAY_NAME"}
                if len(fieldnames) != 4 or frozenset(fieldnames) not in {frozenset(title_case_headers), frozenset(uppercase_headers)}:
                    raise RuntimeError(
                        "FDA GSRS UNII Names columns mismatch: "
                        f"expected one of {sorted(title_case_headers)} or {sorted(uppercase_headers)}, got {reader.fieldnames}"
                    )
                name_column = "NAME" if "NAME" in fieldnames else "Name"
                display_name_column = "DISPLAY_NAME" if "DISPLAY_NAME" in fieldnames else "Display Name"
                for index, row in enumerate(reader, start=1):
                    name = str(row.get(name_column) or "").strip()
                    name_type = str(row.get("TYPE") or "").strip()
                    unii = str(row.get("UNII") or "").strip()
                    display_name = str(row.get(display_name_column) or "").strip()
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


def _load_reusable_openfda_snapshot(
    path: Path,
    metadata_path: Path,
    *,
    source_locator: str,
    effective_date: str,
    manifest_last_updated: object,
    expected_rows: int,
) -> dict | None:
    if expected_rows <= 0 or not effective_date or not path.is_file() or not metadata_path.is_file():
        return None
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(metadata, dict):
        return None
    if (
        metadata.get("dataset_key") != OPENFDA_UNII_DATASET_KEY
        or metadata.get("source_family") != "openfda_unii"
        or metadata.get("source_locator") != source_locator
        or metadata.get("effective_date") != effective_date
        or metadata.get("manifest_last_updated") != manifest_last_updated
        or metadata.get("row_count") != expected_rows
    ):
        return None
    expected_sha256 = metadata.get("sha256")
    if not isinstance(expected_sha256, str) or sha256_file(path) != expected_sha256:
        return None
    return metadata


def sync_openfda_unii(
    raw_dir: str | Path,
    *,
    manifest_fetcher: ManifestFetcher | None = None,
    partition_fetcher: PartitionFetcher | None = None,
    job_progress: ProgressCallback | None = None,
) -> dict:
    manifest_fetcher = manifest_fetcher or (lambda: _fetch_json(OPENFDA_DOWNLOAD_MANIFEST))
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
    expected = int(dataset.get("total_records") or partition.get("records") or 0)
    root = Path(raw_dir)
    path = root / OPENFDA_UNII_FILENAME
    metadata_path = path.with_suffix(path.suffix + ".meta.json")
    observer = _SubstanceSyncObserver(
        OPENFDA_UNII_DATASET_KEY,
        metadata_path,
        job_progress,
    )
    manifest_last_updated = (manifest.get("meta") or {}).get("last_updated")
    export_date = str(dataset.get("export_date") or "").strip()
    reusable = _load_reusable_openfda_snapshot(
        path,
        metadata_path,
        source_locator=url,
        effective_date=export_date,
        manifest_last_updated=manifest_last_updated,
        expected_rows=expected,
    )
    if reusable is not None:
        observer.started(resumed=True)
        size_bytes = path.stat().st_size
        observer.progress_update(size_bytes, size_bytes)
        observer.checkpoint(sha256=str(reusable["sha256"]), size_bytes=size_bytes)
        observer.completed(row_count=int(reusable["row_count"]), resumed=True)
        return reusable

    observer.started(resumed=False)
    try:
        if partition_fetcher is None:
            data = _fetch_with_heartbeat(
                lambda: _fetch_bytes(url, progress=observer.progress_update),
                observer,
            )
        else:
            data = _fetch_with_heartbeat(lambda: partition_fetcher(url), observer)
        observer.progress_update(len(data), len(data))
        records, archive_meta = inspect_unii_archive(data)
        if expected and len(records) != expected:
            raise RuntimeError(
                f"openFDA UNII row-count mismatch: manifest {expected}, archive {len(records)}"
            )
        _write_atomic(path, data)
        metadata = {
            "dataset_key": OPENFDA_UNII_DATASET_KEY,
            "source_family": "openfda_unii",
            "source_locator": url,
            "effective_date": str(
                dataset.get("export_date") or archive_meta.get("last_updated") or ""
            ).strip()
            or None,
            "fetched_at": datetime.now(APP_TIMEZONE).isoformat(timespec="seconds"),
            "row_count": len(records),
            "sha256": _sha256_bytes(data),
            "manifest_locator": OPENFDA_DOWNLOAD_MANIFEST,
            "manifest_last_updated": manifest_last_updated,
            "archive_last_updated": archive_meta.get("last_updated"),
            "authority": "FDA GSRS / UNII via openFDA",
            "license": archive_meta.get("license"),
        }
        _write_json_atomic(metadata_path, metadata)
        observer.checkpoint(sha256=metadata["sha256"], size_bytes=len(data))
        observer.completed(row_count=len(records), resumed=False)
        return metadata
    except Exception as exc:
        observer.failed(exc)
        raise


def sync_fda_gsrs_unii_names(
    raw_dir: str | Path,
    *,
    archive_fetcher: ArchiveFetcher | None = None,
    job_progress: ProgressCallback | None = None,
) -> dict:
    root = Path(raw_dir)
    path = root / FDA_GSRS_UNII_NAMES_FILENAME
    metadata_path = path.with_suffix(path.suffix + ".meta.json")
    observer = _SubstanceSyncObserver(
        FDA_GSRS_UNII_NAMES_DATASET_KEY,
        metadata_path,
        job_progress,
    )
    observer.started(resumed=False)
    try:
        if archive_fetcher is None:
            data = _fetch_with_heartbeat(
                lambda: _fetch_bytes(
                    FDA_GSRS_UNII_NAMES_LATEST,
                    progress=observer.progress_update,
                ),
                observer,
            )
        else:
            data = _fetch_with_heartbeat(
                lambda: archive_fetcher(FDA_GSRS_UNII_NAMES_LATEST),
                observer,
            )
        observer.progress_update(len(data), len(data))
        inspected = inspect_gsrs_unii_names_archive(data)
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
        _write_json_atomic(metadata_path, metadata)
        observer.checkpoint(sha256=metadata["sha256"], size_bytes=len(data))
        observer.completed(row_count=int(inspected["row_count"]), resumed=False)
        return metadata
    except Exception as exc:
        observer.failed(exc)
        raise


def sync_substance_identity_sources(
    raw_dir: str | Path,
    *,
    job_progress: ProgressCallback | None = None,
) -> dict:
    return {
        "openfda_unii": sync_openfda_unii(raw_dir, job_progress=job_progress),
        "fda_gsrs_unii_names": sync_fda_gsrs_unii_names(
            raw_dir,
            job_progress=job_progress,
        ),
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
