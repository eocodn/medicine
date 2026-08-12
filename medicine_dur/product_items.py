from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Callable


API_BASE = "https://apis.data.go.kr/1471000/DURPrdlstInfoService03"
PAGE_SIZE_MAX = 500

PRODUCT_ITEM_SOURCES = {
    "dur_product_info.jsonl": {
        "dataset_key": "product_item:dur_product_info",
        "category": "dur_product_info",
        "operation": "getDurPrdlstInfoList03",
    },
    "extended_release_split_caution.jsonl": {
        "dataset_key": "product_item:split_caution",
        "category": "split_caution",
        "operation": "getSeobangjeongPartitnAtentInfoList03",
    },
}

FLAG_CATEGORY_BY_CODE = {
    "B": "age_contraindication",
    "C": "pregnancy_contraindication",
    "D": "dose_caution",
    "E": "duration_caution",
    "F": "elderly_caution",
    "I": "additive_caution",
}

FLAG_NAME_BY_CODE = {
    "B": "특정연령대금기",
    "C": "임부금기",
    "D": "용량주의",
    "E": "투여기간주의",
    "F": "노인주의",
    "I": "첨가제주의",
}


FetchPage = Callable[[str, int, int], tuple[list[dict], int]]


def _field(row: dict, name: str):
    target = name.strip().casefold()
    for key, value in row.items():
        if str(key).strip().casefold() == target:
            return value
    return None


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_product_item_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS product_item_flags (
            dataset_key TEXT NOT NULL,
            item_seq TEXT NOT NULL,
            product_name TEXT NOT NULL,
            edi_code TEXT,
            category TEXT NOT NULL,
            flag_code TEXT NOT NULL,
            flag_name TEXT NOT NULL,
            dosage_form TEXT,
            ingredient_name TEXT,
            details TEXT,
            change_date TEXT,
            PRIMARY KEY(item_seq, category)
        );
        CREATE INDEX IF NOT EXISTS idx_product_item_flags_edi
            ON product_item_flags(edi_code);
        CREATE INDEX IF NOT EXISTS idx_product_item_flags_category
            ON product_item_flags(category);
        """
    )


def _extract_response(payload: dict) -> tuple[list[dict], int]:
    if "OpenAPI_ServiceResponse" in payload:
        header = payload["OpenAPI_ServiceResponse"].get("cmmMsgHeader", {})
        message = header.get("errMsg") or header.get("returnAuthMsg") or "MFDS DUR API authorization failed"
        raise RuntimeError(message)
    response = payload.get("response", payload)
    header = response.get("header", {}) if isinstance(response, dict) else {}
    result_code = str(header.get("resultCode", "00"))
    if result_code not in {"00", "0"}:
        raise RuntimeError(header.get("resultMsg") or f"MFDS DUR API error {result_code}")
    body = response.get("body", {}) if isinstance(response, dict) else {}
    total = int(body.get("totalCount") or 0)
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    return list(items), total


def fetch_product_item_page(
    service_key: str,
    operation: str,
    page: int,
    page_size: int,
    *,
    timeout: int = 30,
) -> tuple[list[dict], int]:
    params = urllib.parse.urlencode(
        {
            "serviceKey": service_key,
            "pageNo": page,
            "numOfRows": page_size,
            "type": "json",
        },
        safe="%",
    )
    request = urllib.request.Request(
        f"{API_BASE}/{operation}?{params}",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            raise RuntimeError(f"MFDS DUR API HTTP {exc.code}") from exc
    return _extract_response(payload)


def _write_checkpoint(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".write")
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(temp, path)


def _sync_endpoint(
    raw_dir: Path,
    filename: str,
    operation: str,
    *,
    service_key: str,
    page_size: int,
    progress: bool,
    fetch_page: FetchPage | None,
) -> dict:
    output = raw_dir / filename
    partial = output.with_suffix(output.suffix + ".part")
    checkpoint = output.with_suffix(output.suffix + ".checkpoint.json")
    fetcher = fetch_page or (
        lambda op, page, size: fetch_product_item_page(service_key, op, page, size)
    )

    next_page = 1
    total_count = 0
    source_rows = 0
    if partial.exists() and checkpoint.exists():
        try:
            state = json.loads(checkpoint.read_text(encoding="utf-8"))
            if state.get("operation") == operation and state.get("page_size") == page_size:
                next_page = int(state["next_page"])
                total_count = int(state.get("total_count") or 0)
                source_rows = int(state.get("source_rows") or 0)
            else:
                raise ValueError
        except (ValueError, KeyError, json.JSONDecodeError):
            partial.unlink(missing_ok=True)
            checkpoint.unlink(missing_ok=True)
            next_page = 1
            total_count = 0
            source_rows = 0
    else:
        partial.unlink(missing_ok=True)
        checkpoint.unlink(missing_ok=True)

    started = time.monotonic()
    while True:
        items, reported_total = fetcher(operation, next_page, page_size)
        if reported_total:
            total_count = reported_total
        if not items:
            break
        with partial.open("a", encoding="utf-8") as handle:
            for item in items:
                handle.write(json.dumps(item, ensure_ascii=False, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        source_rows += len(items)
        next_page += 1
        _write_checkpoint(
            checkpoint,
            {
                "operation": operation,
                "page_size": page_size,
                "next_page": next_page,
                "total_count": total_count,
                "source_rows": source_rows,
            },
        )
        if progress:
            print(
                f"[sync] {operation}: {source_rows:,}/{total_count or '?'} rows",
                file=sys.stderr,
                flush=True,
            )
        if total_count and source_rows >= total_count:
            break

    if total_count and source_rows != total_count:
        raise RuntimeError(
            f"MFDS DUR snapshot count mismatch for {operation}: expected {total_count}, got {source_rows}"
        )
    os.replace(partial, output)
    checkpoint.unlink(missing_ok=True)
    return {
        "operation": operation,
        "path": str(output),
        "source_rows": source_rows,
        "total_count": total_count,
        "elapsed_seconds": round(time.monotonic() - started, 3),
        "sha256": _sha256(output),
    }


def sync_product_item_sources(
    raw_dir: str | Path,
    *,
    service_key: str,
    page_size: int = PAGE_SIZE_MAX,
    progress: bool = True,
    fetch_page: FetchPage | None = None,
) -> dict:
    service_key = service_key.strip()
    if not service_key:
        raise ValueError("service key is required")
    if page_size < 1 or page_size > PAGE_SIZE_MAX:
        raise ValueError(f"page_size must be between 1 and {PAGE_SIZE_MAX}")
    root = Path(raw_dir)
    root.mkdir(parents=True, exist_ok=True)
    sources = []
    for filename, spec in PRODUCT_ITEM_SOURCES.items():
        sources.append(
            _sync_endpoint(
                root,
                filename,
                spec["operation"],
                service_key=service_key,
                page_size=page_size,
                progress=progress,
                fetch_page=fetch_page,
            )
        )
    return {
        "raw_dir": str(root),
        "sources": sources,
        "source_rows": sum(int(item["source_rows"]) for item in sources),
    }


def _source_metadata(path: Path) -> tuple[list[dict], list[str]]:
    rows: list[dict] = []
    headers: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError(f"invalid product item JSON row in {path}")
            rows.append(row)
            headers.update(str(key).strip() for key in row)
    return rows, sorted(headers)


def _insert_source_file(
    con: sqlite3.Connection,
    *,
    dataset_key: str,
    category: str,
    path: Path,
    imported_rows: int,
    source_rows: int,
    headers: list[str],
) -> None:
    con.execute(
        """
        INSERT INTO source_files(
            dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?)
        """,
        (
            dataset_key,
            "product_item",
            category,
            str(path),
            _sha256(path),
            path.stat().st_size,
            imported_rows,
            json.dumps(
                {"header": headers, "encoding": "utf-8", "source_item_rows": source_rows},
                ensure_ascii=False,
                separators=(",", ":"),
            ),
        ),
    )


def _import_dur_product_info(con: sqlite3.Connection, path: Path) -> int:
    dataset_key = PRODUCT_ITEM_SOURCES[path.name]["dataset_key"]
    rows, headers = _source_metadata(path)
    imported = 0
    sql = """
        INSERT INTO product_item_flags(
            dataset_key,item_seq,product_name,edi_code,category,flag_code,flag_name,
            dosage_form,ingredient_name,details,change_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(item_seq,category) DO UPDATE SET
            dataset_key=excluded.dataset_key,
            product_name=excluded.product_name,
            edi_code=excluded.edi_code,
            flag_code=excluded.flag_code,
            flag_name=excluded.flag_name,
            change_date=excluded.change_date
    """
    for row in rows:
        item_seq = _text(_field(row, "ITEM_SEQ"))
        product_name = _text(_field(row, "ITEM_NAME"))
        if not item_seq or not product_name:
            raise ValueError("DUR product-info row is missing ITEM_SEQ or ITEM_NAME")
        codes = [part.strip() for part in str(_field(row, "TYPE_CODE") or "").split(",") if part.strip()]
        names = [part.strip() for part in str(_field(row, "TYPE_NAME") or "").split(",") if part.strip()]
        for index, code in enumerate(codes):
            category = FLAG_CATEGORY_BY_CODE.get(code, f"dur_flag_{code.casefold()}")
            flag_name = names[index] if index < len(names) else FLAG_NAME_BY_CODE.get(code, code)
            con.execute(
                sql,
                (
                    dataset_key,
                    item_seq,
                    product_name,
                    _text(_field(row, "EDI_CODE")),
                    category,
                    code,
                    flag_name,
                    None,
                    _text(_field(row, "MATERIAL_NAME")),
                    None,
                    _text(_field(row, "CHANGE_DATE")),
                ),
            )
            imported += 1
    _insert_source_file(
        con,
        dataset_key=dataset_key,
        category="dur_product_info",
        path=path,
        imported_rows=imported,
        source_rows=len(rows),
        headers=headers,
    )
    return imported


def _import_split_caution(con: sqlite3.Connection, path: Path) -> int:
    dataset_key = PRODUCT_ITEM_SOURCES[path.name]["dataset_key"]
    rows, headers = _source_metadata(path)
    imported = 0
    for row in rows:
        item_seq = _text(_field(row, "ITEM_SEQ"))
        product_name = _text(_field(row, "ITEM_NAME"))
        if not item_seq or not product_name:
            raise ValueError("split-caution row is missing ITEM_SEQ or ITEM_NAME")
        details = _text(_field(row, "PROHBT_CONTENT"))
        remark = _text(_field(row, "REMARK"))
        if remark:
            details = f"{details}\n{remark}" if details else remark
        con.execute(
            """
            INSERT INTO product_item_flags(
                dataset_key,item_seq,product_name,edi_code,category,flag_code,flag_name,
                dosage_form,ingredient_name,details,change_date
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_seq,category) DO UPDATE SET
                dataset_key=excluded.dataset_key,
                product_name=excluded.product_name,
                flag_code=excluded.flag_code,
                flag_name=excluded.flag_name,
                dosage_form=excluded.dosage_form,
                ingredient_name=excluded.ingredient_name,
                details=excluded.details,
                change_date=excluded.change_date
            """,
            (
                dataset_key,
                item_seq,
                product_name,
                None,
                "split_caution",
                "S",
                "서방정분할주의",
                _text(_field(row, "FORM_CODE_NAME")),
                _text(_field(row, "MAIN_INGR")),
                details,
                _text(_field(row, "CHANGE_DATE")),
            ),
        )
        imported += 1
    _insert_source_file(
        con,
        dataset_key=dataset_key,
        category="split_caution",
        path=path,
        imported_rows=imported,
        source_rows=len(rows),
        headers=headers,
    )
    return imported


def import_product_item_sources(
    con: sqlite3.Connection,
    raw_dir: str | Path,
) -> tuple[int, int]:
    create_product_item_schema(con)
    root = Path(raw_dir)
    source_count = 0
    row_count = 0
    for filename, spec in PRODUCT_ITEM_SOURCES.items():
        path = root / filename
        if not path.exists():
            continue
        if spec["category"] == "dur_product_info":
            row_count += _import_dur_product_info(con, path)
        else:
            row_count += _import_split_caution(con, path)
        source_count += 1
    return source_count, row_count


__all__ = [
    "PRODUCT_ITEM_SOURCES",
    "create_product_item_schema",
    "fetch_product_item_page",
    "import_product_item_sources",
    "sync_product_item_sources",
]
