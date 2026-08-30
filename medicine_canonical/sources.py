from __future__ import annotations

import urllib.parse
from typing import Callable

from medicine_reference.mfds_sources import (
    MFDS_DUR_ITEM_API_BASE,
    MFDS_DUR_ITEM_SOURCES_BY_OPERATION,
    MFDS_PERMIT_API_BASE,
    PERMIT_SOURCE,
)

from .mfds_sync import request_json, sync_paginated_jsonl
from .source_layout import MfdsSourceLayout

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


def preflight_permit_api(service_key: str, *, timeout: float = 8.0) -> dict:
    key = service_key.strip()
    if not key:
        raise ValueError("service key is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    params = urllib.parse.urlencode(
        {"serviceKey": key, "pageNo": 1, "numOfRows": 1, "type": "json"},
        safe="%",
    )
    rows, total = _extract_response(
        request_json(
            f"{MFDS_PERMIT_API_BASE}?{params}",
            label="MFDS permit API preflight",
            timeout=timeout,
            attempts=1,
        ),
        "MFDS permit API preflight",
    )
    return {
        "status": "available",
        "dataset_key": PERMIT_SOURCE.dataset_key,
        "total_count": total,
        "sample_rows": len(rows),
    }


def fetch_permit_page(service_key: str, page: int, page_size: int) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size, "type": "json"}, safe="%"
    )
    return _extract_response(
        request_json(f"{MFDS_PERMIT_API_BASE}?{params}", label="MFDS permit API"),
        "MFDS permit API",
    )


def fetch_dur_page(service_key: str, operation: str, page: int, page_size: int) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size, "type": "json"}, safe="%"
    )
    url = f"{MFDS_DUR_ITEM_API_BASE}/{operation}?{params}"
    return _extract_response(request_json(url, label=f"MFDS DUR {operation}"), f"MFDS DUR {operation}")


def sync_canonical_api_sources(
    source_layout: MfdsSourceLayout,
    *,
    service_key: str,
    permit_page_size: int = PERMIT_PAGE_SIZE_MAX,
    dur_page_size: int = 500,
    workers: int = 8,
    progress: bool = True,
    job_progress=None,
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
        sync_paginated_jsonl(
            source_layout.path_for(PERMIT_SOURCE),
            dataset_key=PERMIT_SOURCE.dataset_key,
            source_family=PERMIT_SOURCE.source_family,
            source_locator=PERMIT_SOURCE.source_locator,
            page_size=permit_page_size,
            workers=workers,
            fetch_page=permit_fetcher,
            progress=progress,
            job_progress=job_progress,
        )
    )
    for operation, spec in DUR_ENDPOINTS.items():
        sources.append(
            sync_paginated_jsonl(
                source_layout.path_for(spec),
                dataset_key=spec.dataset_key,
                source_family=spec.source_family,
                source_locator=spec.source_locator,
                page_size=dur_page_size,
                workers=workers,
                fetch_page=lambda page, size, operation=operation: dur_fetcher(operation, page, size),
                progress=progress,
                job_progress=job_progress,
            )
        )
    return {
        "raw_dir": str(root),
        "sources": sources,
        "source_rows": sum(int(s["row_count"]) for s in sources),
    }
