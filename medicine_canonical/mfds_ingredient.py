from __future__ import annotations

import json
import re
import urllib.parse
import sqlite3
from typing import Callable

from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_API_BASE,
    MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION as MFDS_INGREDIENT_ENDPOINTS,
    MfdsSourceSpec,
)

from medicine_reference.mfds_remark_registry import reviewed_mfds_remark
from .source_layout import MfdsSourceLayout
from .snapshot_io import insert_source_snapshot, load_snapshot_metadata
from .sources import _request_json, _sync_paginated_jsonl


MFDS_INGREDIENT_PAGE_SIZE_MAX = 500
_MIXTURE_CODE_RE = re.compile(r"\[([A-Z]\d{6})\]")
_MIXTURE_KOREAN_NAME_RE = re.compile(r"\([^()]*[가-힣][^()]*\)\s*$")


IngredientFetchPage = Callable[[str, int, int], tuple[list[dict], int]]


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field(row: dict, *names: str):
    folded = {str(key).strip().casefold(): value for key, value in row.items()}
    for name in names:
        key = name.strip().casefold()
        if key in folded:
            return folded[key]
    return None


def _extract_ingredient_response(payload: dict, label: str) -> tuple[list[dict], int]:
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
    if not isinstance(body, dict):
        raise RuntimeError(f"{label} returned an invalid response body")
    total = int(body.get("totalCount") or body.get("total_count") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    if not isinstance(items, list):
        raise RuntimeError(f"{label} returned invalid items")

    rows: list[dict] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        # This service currently wraps each JSON row as {"item": {...}},
        # unlike the related MFDS product endpoint. Accept the direct shape as
        # well so XML-to-JSON gateway representation changes do not alter the
        # canonical row semantics.
        nested = item.get("item")
        if isinstance(nested, dict) and len(item) == 1:
            rows.append(nested)
        else:
            rows.append(item)
    return rows, total


def fetch_mfds_ingredient_page(
    service_key: str, operation: str, page: int, page_size: int
) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {"serviceKey": service_key, "pageNo": page, "numOfRows": page_size, "type": "json"},
        safe="%",
    )
    label = f"MFDS DUR ingredient {operation}"
    payload = _request_json(
        f"{MFDS_DUR_INGREDIENT_API_BASE}/{operation}?{params}", label=label
    )
    return _extract_ingredient_response(payload, label)


def sync_mfds_ingredient_sources(
    source_layout: MfdsSourceLayout,
    *,
    service_key: str,
    page_size: int = MFDS_INGREDIENT_PAGE_SIZE_MAX,
    workers: int = 8,
    progress: bool = True,
    fetch_page: IngredientFetchPage | None = None,
) -> dict:
    key = service_key.strip()
    if not key:
        raise ValueError("service key is required")
    if not 1 <= page_size <= MFDS_INGREDIENT_PAGE_SIZE_MAX:
        raise ValueError(
            f"page_size must be between 1 and {MFDS_INGREDIENT_PAGE_SIZE_MAX}"
        )
    if not 1 <= workers <= 16:
        raise ValueError("workers must be between 1 and 16")

    root = source_layout.ingredient_dir
    root.mkdir(parents=True, exist_ok=True)
    fetcher = fetch_page or (
        lambda operation, page, size: fetch_mfds_ingredient_page(key, operation, page, size)
    )
    sources = []
    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items():
        sources.append(
            _sync_paginated_jsonl(
                source_layout.path_for(spec),
                dataset_key=spec.dataset_key,
                source_family=spec.source_family,
                source_locator=spec.source_locator,
                page_size=page_size,
                workers=workers,
                fetch_page=lambda page, size, operation=operation: fetcher(operation, page, size),
                progress=progress,
            )
        )
    return {
        "raw_dir": str(root),
        "sources": sources,
        "source_rows": sum(int(source["row_count"]) for source in sources),
    }


def _required_text(row: dict, field: str, *, dataset_key: str, source_row: int) -> str:
    value = _text(_field(row, field))
    if not value:
        raise ValueError(f"{dataset_key} row {source_row} missing {field}")
    return value


def _parse_mixture_ingredients(
    value: object, *, dataset_key: str, source_row: int
) -> tuple[tuple[str, str], ...]:
    text = _text(value)
    if not text:
        return ()
    matches = list(_MIXTURE_CODE_RE.finditer(text))
    if not matches or text[: matches[0].start()].strip(" /\t\r\n"):
        raise ValueError(f"{dataset_key} row {source_row} has malformed MIX_INGR")

    by_code: dict[str, str] = {}
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        raw_name = text[match.end() : end].strip().strip("/").strip()
        name = _MIXTURE_KOREAN_NAME_RE.sub("", raw_name).strip().strip("/").strip()
        if not name:
            raise ValueError(f"{dataset_key} row {source_row} has MIX_INGR code without name")
        code = match.group(1)
        previous = by_code.get(code)
        if previous is not None and previous != name:
            raise ValueError(
                f"{dataset_key} row {source_row} maps MIX_INGR code {code} to multiple names"
            )
        by_code.setdefault(code, name)
    return tuple(by_code.items())


def _canonical_rule(
    row: dict, spec: MfdsSourceSpec, *, dataset_key: str, source_row: int
) -> dict:
    ingredient_code = _required_text(
        row, "INGR_CODE", dataset_key=dataset_key, source_row=source_row
    )
    ingredient_name = _required_text(
        row, "INGR_ENG_NAME", dataset_key=dataset_key, source_row=source_row
    )
    paired_name = None
    paired_code = None
    if spec.category == "combination_contraindication":
        paired_code = _required_text(
            row, "MIXTURE_INGR_CODE", dataset_key=dataset_key, source_row=source_row
        )
        paired_name = _required_text(
            row, "MIXTURE_INGR_ENG_NAME", dataset_key=dataset_key, source_row=source_row
        )

    mixture_type = None
    mixture_codes: tuple[str, ...] = ()
    mixture_names: tuple[str, ...] = ()
    if spec.category != "combination_contraindication":
        mixture_type = _required_text(
            row, "MIX_TYPE", dataset_key=dataset_key, source_row=source_row
        )
        if mixture_type not in {"단일", "복합"}:
            raise ValueError(
                f"{dataset_key} row {source_row} has unsupported MIX_TYPE {mixture_type!r}"
            )
        mixture_entries = _parse_mixture_ingredients(
            _field(row, "MIX_INGR"), dataset_key=dataset_key, source_row=source_row
        )
        mixture_codes = tuple(code for code, _name in mixture_entries)
        mixture_names = tuple(name for _code, name in mixture_entries)
        if mixture_type == "단일" and mixture_codes:
            raise ValueError(f"{dataset_key} row {source_row} marks 단일 with MIX_INGR")
        if mixture_type == "복합" and not mixture_codes:
            raise ValueError(f"{dataset_key} row {source_row} marks 복합 without MIX_INGR")
    rule_value = None
    if spec.rule_field:
        rule_value = _text(_field(row, spec.rule_field))
        if spec.rule_required and not rule_value:
            raise ValueError(f"{dataset_key} row {source_row} missing {spec.rule_field}")

    series_note = None
    if spec.category == "therapeutic_duplication_caution":
        series_note = _text(_field(row, "SERS_NAME"))
    remark = _text(_field(row, "REMARK"))

    return {
        "category": spec.category,
        "sequence_text": _text(_field(row, "DUR_SEQ")),
        "ingredient_code": ingredient_code,
        "ingredient_name": ingredient_name,
        "ingredient_name_ko": _text(_field(row, "INGR_NAME", "INGR_KOR_NAME")),
        "paired_ingredient_code": paired_code,
        "paired_ingredient_name": paired_name,
        "mixture_type": mixture_type,
        "mixture_ingredient_codes": mixture_codes,
        "mixture_ingredient_names": mixture_names,
        "rule_value": rule_value,
        "dosage_form": _text(_field(row, "FORM_NAME")),
        "note": series_note,
        "qualifier_note": remark,
        "details": _text(_field(row, "PROHBT_CONTENT")),
    }


def import_mfds_ingredient_snapshots(
    con: sqlite3.Connection, source_layout: MfdsSourceLayout
) -> dict:
    source_rows = 0
    imported_rows = 0
    deleted_rows = 0
    category_counts: dict[str, int] = {}

    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items():
        path = source_layout.path_for(spec)
        if not path.exists():
            raise FileNotFoundError(f"missing MFDS ingredient snapshot: {path}")
        meta = load_snapshot_metadata(path, label="MFDS ingredient snapshot")
        dataset_key = spec.dataset_key
        operation_name = str(spec.operation)
        if meta["dataset_key"] != spec.dataset_key:
            raise ValueError(f"MFDS ingredient snapshot dataset mismatch for {operation_name}")
        if meta["source_family"] != spec.source_family:
            raise ValueError(f"MFDS ingredient snapshot family mismatch for {operation_name}")
        if meta["source_locator"] != spec.source_locator:
            raise ValueError(f"MFDS ingredient snapshot locator mismatch for {operation_name}")
        insert_source_snapshot(con, meta, path)

        imported_source_rows = 0
        with path.open("r", encoding="utf-8") as handle:
            for source_row, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{dataset_key} row {source_row} is not a JSON object")
                reviewed_mfds_remark(spec.category, _text(_field(row, "REMARK")))
                state = _text(_field(row, "DEL_YN"))
                if state == "삭제":
                    deleted_rows += 1
                elif state == "정상":
                    canonical = _canonical_rule(
                        row, spec, dataset_key=dataset_key, source_row=source_row
                    )
                    cursor = con.execute(
                        """
                        INSERT INTO ingredient_rules(
                            source_dataset_key,source_row,category,sequence_text,ingredient_name,
                            ingredient_name_ko,paired_ingredient_name,rule_value,dosage_form,note,qualifier_note,details
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            dataset_key,
                            source_row,
                            canonical["category"],
                            canonical["sequence_text"],
                            canonical["ingredient_name"],
                            canonical["ingredient_name_ko"],
                            canonical["paired_ingredient_name"],
                            canonical["rule_value"],
                            canonical["dosage_form"],
                            canonical["note"],
                            canonical["qualifier_note"],
                            canonical["details"],
                        ),
                    )
                    con.execute(
                        """INSERT INTO ingredient_rule_codes(
                               criterion_rule_id,ingredient_code,paired_ingredient_code,
                               mixture_type,mixture_ingredient_codes_json,
                               mixture_ingredient_names_json
                           ) VALUES(?,?,?,?,?,?)""",
                        (
                            int(cursor.lastrowid),
                            canonical["ingredient_code"],
                            canonical["paired_ingredient_code"],
                            canonical["mixture_type"],
                            json.dumps(
                                canonical["mixture_ingredient_codes"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                            json.dumps(
                                canonical["mixture_ingredient_names"],
                                ensure_ascii=False,
                                separators=(",", ":"),
                            ),
                        ),
                    )
                    imported_rows += 1
                    category_counts[spec.category] = category_counts.get(spec.category, 0) + 1
                else:
                    raise ValueError(
                        f"{dataset_key} row {source_row} has unsupported DEL_YN {state!r}"
                    )
                imported_source_rows += 1

        if imported_source_rows != int(meta["row_count"]):
            raise RuntimeError(
                f"{operation} row mismatch: metadata {meta['row_count']}, imported {imported_source_rows}"
            )
        source_rows += imported_source_rows

    return {
        "source_snapshots": len(MFDS_INGREDIENT_ENDPOINTS),
        "source_rows": source_rows,
        "ingredient_rules": imported_rows,
        "deleted_rows_skipped": deleted_rows,
        "ingredient_rules_by_category": dict(sorted(category_counts.items())),
    }


__all__ = [
    "MFDS_INGREDIENT_ENDPOINTS",
    "fetch_mfds_ingredient_page",
    "import_mfds_ingredient_snapshots",
    "sync_mfds_ingredient_sources",
]