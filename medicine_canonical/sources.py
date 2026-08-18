from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from medicine_reference.mfds_sources import (
    MFDS_DUR_ITEM_API_BASE,
    MFDS_DUR_ITEM_SOURCES_BY_OPERATION,
    MFDS_PERMIT_API_BASE,
    PERMIT_SOURCE,
)

from .source_layout import MfdsSourceLayout
from .snapshot_io import canonical_json, sha256_file, snapshot_metadata_path

APP_TIMEZONE = ZoneInfo("Asia/Seoul")
PERMIT_DATASET_KEY = PERMIT_SOURCE.dataset_key
PERMIT_PAGE_SIZE_MAX = 500
DUR_PAGE_SIZE_MAX = 500
PERMIT_FILENAME = PERMIT_SOURCE.filename
DUR_ENDPOINTS = MFDS_DUR_ITEM_SOURCES_BY_OPERATION

PermitFetchPage = Callable[[int, int], tuple[list[dict], int]]
DurFetchPage = Callable[[str, int, int], tuple[list[dict], int]]


def _extract_response(payload: dict, label: str) -> tuple[list[dict], int]:
    if "OpenAPI_ServiceResponse" in payload:
        header = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        message = header.get("errMsg") or header.get("returnAuthMsg") or f"{label} authorization failed"
        raise RuntimeError(message)
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        raise RuntimeError(f"{label} returned an invalid response envelope")
    header = response.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in {"00", "0"}:
        raise RuntimeError(header.get("resultMsg") or f"{label} error {code}")
    body = response.get("body", {})
    total = int(body.get("totalCount") or body.get("total_count") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise RuntimeError(f"{label} returned invalid items")
    return [row for row in items if isinstance(row, dict)], total


def _request_json(url: str, *, label: str, timeout: int = 45, attempts: int = 4) -> dict:
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"Accept": "application/json"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", "replace")
            retryable = exc.code in {408, 429, 500, 502, 503, 504}
            if not retryable:
                try:
                    payload = json.loads(body)
                    _extract_response(payload, label)
                except json.JSONDecodeError:
                    pass
                raise RuntimeError(f"{label} HTTP {exc.code}: {body[:240]}") from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
            last_error = exc
        if attempt + 1 < attempts:
            print(
                f"{label}: retry {attempt + 2}/{attempts} after {type(last_error).__name__}",
                file=sys.stderr,
                flush=True,
            )
            time.sleep(0.5 * (2**attempt))
    raise RuntimeError(f"{label} failed after {attempts} attempts: {last_error}") from last_error


def fetch_permit_page(service_key: str, page: int, page_size: int) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size, "type": "json"}, safe="%"
    )
    return _extract_response(
        _request_json(f"{MFDS_PERMIT_API_BASE}?{params}", label="MFDS permit API"),
        "MFDS permit API",
    )


def fetch_dur_page(service_key: str, operation: str, page: int, page_size: int) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size, "type": "json"}, safe="%"
    )
    url = f"{MFDS_DUR_ITEM_API_BASE}/{operation}?{params}"
    return _extract_response(_request_json(url, label=f"MFDS DUR {operation}"), f"MFDS DUR {operation}")


def _write_json_atomic(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".write")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _write_page(path: Path, rows: list[dict]) -> int:
    temp = path.with_name(path.name + ".write")
    with temp.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(canonical_json(row) + "\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp, path)
    return len(rows)


def _sync_paginated_jsonl(
    output: Path,
    *,
    dataset_key: str,
    source_family: str,
    source_locator: str,
    page_size: int,
    workers: int,
    fetch_page: Callable[[int, int], tuple[list[dict], int]],
    progress: bool,
) -> dict:
    output.parent.mkdir(parents=True, exist_ok=True)
    pages_dir = output.with_name(output.name + ".pages")
    state_path = pages_dir / "state.json"
    meta_path = snapshot_metadata_path(output)

    resumed = False
    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
            resumed = (
                state.get("dataset_key") == dataset_key
                and int(state.get("page_size") or 0) == page_size
                and int(state.get("total_pages") or 0) > 0
            )
        except (OSError, ValueError, json.JSONDecodeError):
            resumed = False
    if not resumed:
        shutil.rmtree(pages_dir, ignore_errors=True)
        pages_dir.mkdir(parents=True, exist_ok=True)
        if progress:
            print(
                f"[canonical-sync] {dataset_key}: fetch first page",
                file=sys.stderr,
                flush=True,
            )
        first_rows, total = fetch_page(1, page_size)
        total_pages = max(1, math.ceil(total / page_size)) if total else (1 if first_rows else 0)
        state = {
            "dataset_key": dataset_key,
            "page_size": page_size,
            "total_count": total,
            "total_pages": total_pages,
        }
        _write_page(pages_dir / "000001.jsonl", first_rows)
        _write_json_atomic(state_path, state)
    else:
        total = int(state.get("total_count") or 0)
        total_pages = int(state["total_pages"])

    if total_pages == 0:
        output.write_text("", encoding="utf-8")
    missing = [p for p in range(1, total_pages + 1) if not (pages_dir / f"{p:06d}.jsonl").exists()]
    started = time.monotonic()
    completed = total_pages - len(missing)

    def get_page(page: int) -> tuple[int, list[dict], int]:
        rows, reported = fetch_page(page, page_size)
        return page, rows, reported

    if missing:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(get_page, page): page for page in missing}
            for future in as_completed(futures):
                page, rows, reported = future.result()
                if total and reported and reported != total:
                    raise RuntimeError(
                        f"{dataset_key} totalCount changed during sync: {total} -> {reported}"
                    )
                _write_page(pages_dir / f"{page:06d}.jsonl", rows)
                completed += 1
                if progress and (completed == total_pages or completed % 25 == 0):
                    print(
                        f"[canonical-sync] {dataset_key}: {completed:,}/{total_pages:,} pages",
                        file=sys.stderr,
                        flush=True,
                    )

    temp_output = output.with_name(output.name + ".write")
    row_count = 0
    with temp_output.open("wb") as target:
        for page in range(1, total_pages + 1):
            page_path = pages_dir / f"{page:06d}.jsonl"
            with page_path.open("rb") as source:
                for line in source:
                    if line.strip():
                        row_count += 1
                    target.write(line)
        target.flush()
        os.fsync(target.fileno())
    if total and row_count != total:
        temp_output.unlink(missing_ok=True)
        raise RuntimeError(f"{dataset_key} row-count mismatch: expected {total}, got {row_count}")
    os.replace(temp_output, output)
    fetched_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
    metadata = {
        "dataset_key": dataset_key,
        "source_family": source_family,
        "source_locator": source_locator,
        "fetched_at": fetched_at,
        "row_count": row_count,
        "reported_row_count": total,
        "sha256": sha256_file(output),
        "page_size": page_size,
        "total_pages": total_pages,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "hash_basis": "canonical-json-lines-in-api-page-order",
    }
    _write_json_atomic(meta_path, metadata)
    shutil.rmtree(pages_dir, ignore_errors=True)
    return metadata


def sync_canonical_api_sources(
    source_layout: MfdsSourceLayout,
    *,
    service_key: str,
    permit_page_size: int = PERMIT_PAGE_SIZE_MAX,
    dur_page_size: int = 500,
    workers: int = 8,
    progress: bool = True,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
) -> dict:
    key = service_key.strip()
    if not key:
        raise ValueError("service key is required")
    if not 1 <= permit_page_size <= PERMIT_PAGE_SIZE_MAX:
        raise ValueError(f"permit_page_size must be between 1 and {PERMIT_PAGE_SIZE_MAX}")
    if not 1 <= dur_page_size <= DUR_PAGE_SIZE_MAX:
        raise ValueError(f"dur_page_size must be between 1 and {DUR_PAGE_SIZE_MAX}")
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")
    root = source_layout.product_dir
    root.mkdir(parents=True, exist_ok=True)
    permit_fetcher = permit_fetch_page or (lambda page, size: fetch_permit_page(key, page, size))
    dur_fetcher = dur_fetch_page or (lambda operation, page, size: fetch_dur_page(key, operation, page, size))

    sources = []
    sources.append(
        _sync_paginated_jsonl(
            source_layout.path_for(PERMIT_SOURCE),
            dataset_key=PERMIT_SOURCE.dataset_key,
            source_family=PERMIT_SOURCE.source_family,
            source_locator=PERMIT_SOURCE.source_locator,
            page_size=permit_page_size,
            workers=workers,
            fetch_page=permit_fetcher,
            progress=progress,
        )
    )
    for operation, spec in DUR_ENDPOINTS.items():
        sources.append(
            _sync_paginated_jsonl(
                source_layout.path_for(spec),
                dataset_key=spec.dataset_key,
                source_family=spec.source_family,
                source_locator=spec.source_locator,
                page_size=dur_page_size,
                workers=workers,
                fetch_page=lambda page, size, operation=operation: dur_fetcher(operation, page, size),
                progress=progress,
            )
        )
    return {
        "raw_dir": str(root),
        "sources": sources,
        "source_rows": sum(int(s["row_count"]) for s in sources),
    }
