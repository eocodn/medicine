from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo


API_BASE = "https://apis.data.go.kr/1471000/DrugPrdtPrmsnInfoService07/getDrugPrdtPrmsnInq07"
SOURCE_NAME = "mfds_product_permit_api"
APP_TIMEZONE = ZoneInfo("Asia/Seoul")

SCHEMA = """
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;

CREATE TABLE catalog_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE products (
    item_seq TEXT PRIMARY KEY,
    product_name TEXT NOT NULL,
    manufacturer TEXT,
    ingredient_name TEXT,
    dosage_form TEXT,
    edi_code TEXT,
    permit_date TEXT,
    cancel_date TEXT,
    source TEXT NOT NULL,
    raw_json TEXT NOT NULL
);

CREATE INDEX idx_products_name ON products(product_name);
CREATE INDEX idx_products_ingredient ON products(ingredient_name);
CREATE INDEX idx_products_manufacturer ON products(manufacturer);
CREATE INDEX idx_products_edi_code ON products(edi_code);
CREATE INDEX idx_products_cancel_date ON products(cancel_date);
"""


FetchPage = Callable[[int, int], tuple[list[dict], int]]


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def _value(record: dict, *names: str):
    lowered = {str(key).lower(): value for key, value in record.items()}
    for name in names:
        if name.lower() in lowered:
            return lowered[name.lower()]
    return None


def _date_text(value) -> str | None:
    value = _text(value)
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return value


def _normalize_product(record: dict) -> tuple:
    item_seq = _text(
        _value(
            record,
            "ITEM_SEQ",
            "item_seq",
            "PRDLST_STDR_CODE",
            "prdlst_stdr_code",
            "PRDUCT_PRMISN_NO",
            "prduct_prmisn_no",
        )
    )
    product_name = _text(_value(record, "ITEM_NAME", "item_name"))
    if not item_seq or not product_name:
        raise ValueError("MFDS product row is missing ITEM_SEQ or ITEM_NAME")
    ingredient = _text(
        _value(
            record,
            "ITEM_INGR_NAME",
            "item_ingr_name",
            "MAIN_ITEM_INGR",
            "main_item_ingr",
            "MATERIAL_NAME",
            "material_name",
        )
    )
    dosage_form = _text(
        _value(record, "FORM_CODE_NAME", "form_code_name", "DOSAGE_FORM", "dosage_form")
    )
    return (
        item_seq,
        product_name,
        _text(_value(record, "ENTP_NAME", "entp_name")),
        ingredient,
        dosage_form,
        _text(_value(record, "EDI_CODE", "edi_code")),
        _date_text(_value(record, "ITEM_PERMIT_DATE", "item_permit_date")),
        _date_text(_value(record, "CANCEL_DATE", "cancel_date")),
        SOURCE_NAME,
        json.dumps(record, ensure_ascii=False, separators=(",", ":"), default=str),
    )


def _extract_response(payload: dict) -> tuple[list[dict], int]:
    if "OpenAPI_ServiceResponse" in payload:
        header = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        message = header.get("errMsg") or header.get("returnAuthMsg") or "MFDS API authorization failed"
        raise RuntimeError(message)
    response = payload.get("response", payload)
    header = response.get("header", {}) if isinstance(response, dict) else {}
    result_code = str(header.get("resultCode", "00"))
    if result_code not in {"00", "0"}:
        raise RuntimeError(header.get("resultMsg") or f"MFDS API error {result_code}")
    body = response.get("body", {}) if isinstance(response, dict) else {}
    total = int(body.get("totalCount") or body.get("total_count") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return list(items), total


def fetch_mfds_page(service_key: str, page: int, page_size: int, timeout: int = 30) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": page_size,
            "type": "json",
        },
        safe="%",
    )
    request = urllib.request.Request(f"{API_BASE}?{params}", headers={"Accept": "application/json"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"MFDS API HTTP {exc.code}") from exc
        return _extract_response(payload)
    return _extract_response(payload)


def _write_checkpoint(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".write")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def sync_catalog(
    db_path: str | Path,
    *,
    service_key: str,
    page_size: int = 100,
    progress: bool = True,
    fetch_page: FetchPage | None = None,
) -> dict:
    service_key = service_key.strip()
    if not service_key:
        raise ValueError("service key is required")
    if page_size < 1 or page_size > 1000:
        raise ValueError("page_size must be between 1 and 1000")

    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp_path = db_path.with_name(db_path.name + ".tmp")
    checkpoint_path = db_path.with_name(db_path.name + ".checkpoint.json")
    fetcher = fetch_page or (lambda page, size: fetch_mfds_page(service_key, page, size))

    next_page = 1
    total_count = 0
    resumed = False
    if temp_path.exists() and checkpoint_path.exists():
        try:
            checkpoint = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            if checkpoint.get("page_size") == page_size and checkpoint.get("source") == SOURCE_NAME:
                next_page = int(checkpoint["next_page"])
                total_count = int(checkpoint.get("total_count") or 0)
                resumed = True
        except (ValueError, KeyError, json.JSONDecodeError):
            resumed = False

    if not resumed:
        temp_path.unlink(missing_ok=True)
        checkpoint_path.unlink(missing_ok=True)
        con = sqlite3.connect(temp_path)
        try:
            con.executescript(SCHEMA)
            con.commit()
        finally:
            con.close()

    started = time.monotonic()
    con = sqlite3.connect(temp_path)
    try:
        while True:
            items, reported_total = fetcher(next_page, page_size)
            if reported_total:
                total_count = reported_total
            if not items:
                break
            rows = [_normalize_product(item) for item in items]
            con.executemany(
                """
                INSERT INTO products(
                    item_seq,product_name,manufacturer,ingredient_name,dosage_form,
                    edi_code,permit_date,cancel_date,source,raw_json
                ) VALUES(?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_seq) DO UPDATE SET
                    product_name=excluded.product_name,
                    manufacturer=excluded.manufacturer,
                    ingredient_name=excluded.ingredient_name,
                    dosage_form=excluded.dosage_form,
                    edi_code=excluded.edi_code,
                    permit_date=excluded.permit_date,
                    cancel_date=excluded.cancel_date,
                    source=excluded.source,
                    raw_json=excluded.raw_json
                """,
                rows,
            )
            con.commit()
            product_count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
            next_page += 1
            _write_checkpoint(
                checkpoint_path,
                {
                    "source": SOURCE_NAME,
                    "page_size": page_size,
                    "next_page": next_page,
                    "total_count": total_count,
                    "products": product_count,
                },
            )
            if progress:
                elapsed = max(time.monotonic() - started, 0.001)
                print(
                    f"[catalog] {product_count:,}/{total_count or '?'} products · page {next_page - 1} · {product_count / elapsed:,.0f}/s",
                    file=sys.stderr,
                    flush=True,
                )
            if total_count and product_count >= total_count:
                break

        product_count = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        synced_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
        con.executemany(
            "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES(?,?)",
            [
                ("source", SOURCE_NAME),
                ("api_base", API_BASE),
                ("synced_at", synced_at),
                ("reported_total_count", str(total_count)),
                ("products", str(product_count)),
            ],
        )
        con.execute("ANALYZE")
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            raise RuntimeError(f"catalog integrity check failed: {integrity}")
        con.commit()
    finally:
        con.close()

    os.replace(temp_path, db_path)
    checkpoint_path.unlink(missing_ok=True)
    stats = catalog_stats(db_path)
    stats["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return stats


def catalog_stats(db_path: str | Path) -> dict:
    db_path = Path(db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"catalog database not found: {db_path}")
    con = sqlite3.connect(db_path)
    try:
        products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        active = con.execute(
            "SELECT COUNT(*) FROM products WHERE cancel_date IS NULL OR TRIM(cancel_date)=''"
        ).fetchone()[0]
        meta = dict(con.execute("SELECT key,value FROM catalog_meta").fetchall())
    finally:
        con.close()
    return {
        "db_path": str(db_path),
        "products": products,
        "active_products": active,
        "size_bytes": db_path.stat().st_size,
        "source": meta.get("source"),
        "synced_at": meta.get("synced_at"),
        "reported_total_count": int(meta.get("reported_total_count") or 0),
    }
