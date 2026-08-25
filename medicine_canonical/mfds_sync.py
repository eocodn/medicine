from __future__ import annotations

import json
import math
import os
import shutil
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .snapshot_io import canonical_json, sha256_file, snapshot_metadata_path


APP_TIMEZONE = ZoneInfo("Asia/Seoul")


def _raise_service_error(payload: dict, label: str) -> None:
    if "OpenAPI_ServiceResponse" in payload:
        header = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        message = (
            header.get("errMsg")
            or header.get("returnAuthMsg")
            or f"{label} authorization failed"
        )
        raise RuntimeError(message)
    response = payload.get("response", payload)
    if not isinstance(response, dict):
        raise RuntimeError(f"{label} returned an invalid response envelope")
    header = response.get("header", {})
    code = str(header.get("resultCode", "00"))
    if code not in {"00", "0"}:
        raise RuntimeError(header.get("resultMsg") or f"{label} error {code}")
    body = response.get("body", {})
    int(body.get("totalCount") or body.get("total_count") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise RuntimeError(f"{label} returned invalid items")
    # Preserve the previous product-source validator's fail-closed behavior for
    # non-retryable HTTP JSON bodies. The parsed rows are intentionally ignored:
    # endpoint-specific success normalization remains in each source module.


def request_json(url: str, *, label: str, timeout: int = 45, attempts: int = 4) -> dict:
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
                    _raise_service_error(json.loads(body), label)
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


def sync_paginated_jsonl(
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
    missing = [
        page
        for page in range(1, total_pages + 1)
        if not (pages_dir / f"{page:06d}.jsonl").exists()
    ]
    started = time.monotonic()
    completed = total_pages - len(missing)

    def get_page(page: int) -> tuple[int, list[dict], int]:
        rows, reported = fetch_page(page, page_size)
        return page, rows, reported

    total_count_change: tuple[int, int] | None = None
    if missing:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(get_page, page): page for page in missing}
            for future in as_completed(futures):
                page, rows, reported = future.result()
                if total and reported and reported != total:
                    total_count_change = (total, reported)
                    for pending in futures:
                        pending.cancel()
                    break
                _write_page(pages_dir / f"{page:06d}.jsonl", rows)
                completed += 1
                if progress and (completed == total_pages or completed % 25 == 0):
                    print(
                        f"[canonical-sync] {dataset_key}: {completed:,}/{total_pages:,} pages",
                        file=sys.stderr,
                        flush=True,
                    )
    if total_count_change is not None:
        previous_total, reported_total = total_count_change
        # A count change proves the saved pages no longer belong to one
        # authoritative source snapshot. Do not let an outer workflow retry
        # resume this stale checkpoint; transient transport failures still keep
        # their partial pages and resume normally.
        shutil.rmtree(pages_dir, ignore_errors=True)
        raise RuntimeError(
            f"{dataset_key} totalCount changed during sync: "
            f"{previous_total} -> {reported_total}"
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
        # A complete merge that disagrees with the authoritative total proves
        # at least one successfully fetched page is not reusable. Discard the
        # checkpoint so the outer workflow retry starts from page 1; transport
        # failures still retain partial pages for resume.
        shutil.rmtree(pages_dir, ignore_errors=True)
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


__all__ = ["request_json", "sync_paginated_jsonl"]
