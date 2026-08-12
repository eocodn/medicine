from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .schema import CORE_SOURCE_FAMILIES, SCHEMA, SCHEMA_VERSION
from .sources import (
    DUR_ENDPOINTS,
    PERMIT_DATASET_KEY,
    PERMIT_FILENAME,
    DurFetchPage,
    PermitFetchPage,
    sync_canonical_api_sources,
)
from .xlsx import XLSX_DATASETS, import_xlsx_sources

APP_TIMEZONE = ZoneInfo("Asia/Seoul")
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
PERMIT_STATUS_BY_CANCEL_NAME = {
    "정상": "active",
    "유효기간만료": "expired",
    "취하": "withdrawn",
    "폐업": "business_closed",
    "행정(취소)": "canceled",
    "취소": "canceled",
}


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _field(row: dict, *names: str):
    lower = {str(k).strip().casefold(): v for k, v in row.items()}
    for name in names:
        if name.strip().casefold() in lower:
            return lower[name.strip().casefold()]
    return None


def _date_text(value) -> str | None:
    value = _text(value)
    if not value:
        return None
    digits = "".join(ch for ch in value if ch.isdigit())
    if len(digits) == 8:
        return f"{digits[:4]}-{digits[4:6]}-{digits[6:8]}"
    return value


def _json(row: dict) -> str:
    return json.dumps(row, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)



def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def _load_meta(path: Path) -> dict:
    meta = path.with_suffix(path.suffix + ".meta.json")
    if not meta.exists():
        raise FileNotFoundError(f"missing API snapshot metadata: {meta}")
    payload = json.loads(meta.read_text(encoding="utf-8"))
    required = {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"invalid API snapshot metadata {meta}: missing {sorted(missing)}")
    actual_sha = _sha256(path)
    if actual_sha != payload["sha256"]:
        raise ValueError(f"sha256 mismatch for API snapshot {path}: expected {payload['sha256']}, got {actual_sha}")
    return payload


def _insert_source_snapshot(con: sqlite3.Connection, meta: dict, snapshot_path: Path) -> None:
    con.execute(
        """
        INSERT INTO source_snapshots(
            dataset_key,source_family,source_locator,snapshot_path,fetched_at,row_count,reported_row_count,sha256,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            meta["dataset_key"],
            meta["source_family"],
            meta["source_locator"],
            str(snapshot_path),
            meta.get("fetched_at"),
            int(meta["row_count"]),
            int(meta.get("reported_row_count") or 0),
            meta["sha256"],
            _json(meta),
        ),
    )


def _normalize_permit_status(cancel_name, cancel_date=None) -> str:
    raw = _text(cancel_name)
    if raw in PERMIT_STATUS_BY_CANCEL_NAME:
        return PERMIT_STATUS_BY_CANCEL_NAME[raw]
    return "inactive_unknown" if _text(cancel_date) else "unknown"


def _import_permit_snapshot(con: sqlite3.Connection, raw_dir: Path) -> int:
    path = raw_dir / PERMIT_FILENAME
    meta = _load_meta(path)
    if meta["dataset_key"] != PERMIT_DATASET_KEY or meta["source_family"] != "mfds_permit_api":
        raise ValueError("permit snapshot provenance mismatch")
    _insert_source_snapshot(con, meta, path)
    count = 0
    with path.open("r", encoding="utf-8") as handle:
        for source_row, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            row = json.loads(line)
            item_seq = _text(_field(row, "ITEM_SEQ", "PRDLST_STDR_CODE", "PRDUCT_PRMISN_NO"))
            product_name = _text(_field(row, "ITEM_NAME"))
            if not item_seq or not product_name:
                raise ValueError(f"permit row {source_row} missing ITEM_SEQ or ITEM_NAME")
            cancel_date = _date_text(_field(row, "CANCEL_DATE"))
            cancel_name = _text(_field(row, "CANCEL_NAME"))
            con.execute(
                """
                INSERT INTO products(
                    item_seq,source_row,product_name,manufacturer,ingredient_text,dosage_form,permit_date,
                    cancel_date,cancel_name,permit_status,source_dataset_key
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(item_seq) DO UPDATE SET
                    source_row=excluded.source_row,
                    product_name=excluded.product_name,
                    manufacturer=excluded.manufacturer,
                    ingredient_text=excluded.ingredient_text,
                    dosage_form=excluded.dosage_form,
                    permit_date=excluded.permit_date,
                    cancel_date=excluded.cancel_date,
                    cancel_name=excluded.cancel_name,
                    permit_status=excluded.permit_status,
                    source_dataset_key=excluded.source_dataset_key
                """,
                (
                    item_seq,
                    source_row,
                    product_name,
                    _text(_field(row, "ENTP_NAME")),
                    _text(_field(row, "ITEM_INGR_NAME", "MAIN_ITEM_INGR", "MATERIAL_NAME")),
                    _text(_field(row, "FORM_CODE_NAME", "DOSAGE_FORM")),
                    _date_text(_field(row, "ITEM_PERMIT_DATE")),
                    cancel_date,
                    cancel_name,
                    _normalize_permit_status(cancel_name, cancel_date),
                    PERMIT_DATASET_KEY,
                ),
            )
            con.execute(
                "INSERT OR IGNORE INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
                (item_seq, "MFDS_ITEM_SEQ", item_seq, PERMIT_DATASET_KEY),
            )
            edi = _text(_field(row, "EDI_CODE"))
            if edi:
                for value in (part.strip() for part in edi.split(",")):
                    if value:
                        con.execute(
                            "INSERT OR IGNORE INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
                            (item_seq, "EDI", value, PERMIT_DATASET_KEY),
                        )
            count += 1
    if count != int(meta["row_count"]):
        raise RuntimeError(f"permit snapshot row mismatch: metadata {meta['row_count']}, imported {count}")
    return count


def _import_rule_row(con: sqlite3.Connection, dataset_key: str, source_row: int, category: str, row: dict) -> None:
    item_seq = _text(_field(row, "ITEM_SEQ"))
    if not item_seq:
        raise ValueError(f"{dataset_key} row {source_row} missing ITEM_SEQ")
    con.execute(
        """
        INSERT INTO product_rules(
            source_dataset_key,source_row,category,item_seq,ingredient_name,
            paired_item_seq,paired_ingredient_name,effect_name,dosage_form,
            details,notification_date,change_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_key,
            source_row,
            category,
            item_seq,
            _text(_field(row, "INGR_NAME", "INGR_KOR_NAME")),
            _text(_field(row, "MIXTURE_ITEM_SEQ")),
            _text(_field(row, "MIXTURE_INGR_KOR_NAME", "MIXTURE_INGR_NAME")),
            _text(_field(row, "EFFECT_NAME")),
            _text(_field(row, "FORM_NAME", "FORM_CODE_NAME")),
            _text(_field(row, "PROHBT_CONTENT")),
            _date_text(_field(row, "NOTIFICATION_DATE")),
            _date_text(_field(row, "CHANGE_DATE")),
        ),
    )


def _import_flag_row(con: sqlite3.Connection, dataset_key: str, source_row: int, row: dict) -> int:
    item_seq = _text(_field(row, "ITEM_SEQ"))
    if not item_seq:
        raise ValueError(f"{dataset_key} row {source_row} missing ITEM_SEQ")
    codes = [part.strip() for part in (_text(_field(row, "TYPE_CODE")) or "").split(",") if part.strip()]
    names = [part.strip() for part in (_text(_field(row, "TYPE_NAME")) or "").split(",") if part.strip()]
    if not codes:
        raise ValueError(f"{dataset_key} row {source_row} missing TYPE_CODE")
    count = 0
    for ordinal, code in enumerate(codes, start=1):
        category = FLAG_CATEGORY_BY_CODE.get(code)
        if not category:
            raise ValueError(f"unsupported DUR product flag code {code!r} at row {source_row}")
        flag_name = names[ordinal - 1] if ordinal - 1 < len(names) else FLAG_NAME_BY_CODE[code]
        con.execute(
            """
            INSERT INTO product_flags(
                source_dataset_key,source_row,flag_ordinal,item_seq,category,flag_code,
                flag_name,ingredient_name,dosage_form,details,change_date
            ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                dataset_key,
                source_row,
                ordinal,
                item_seq,
                category,
                code,
                flag_name,
                _text(_field(row, "MATERIAL_NAME", "MAIN_INGR")),
                _text(_field(row, "FORM_CODE_NAME", "FORM_NAME")),
                _text(_field(row, "PROHBT_CONTENT")),
                _date_text(_field(row, "CHANGE_DATE")),
            ),
        )
        count += 1
    return count


def _import_split_row(con: sqlite3.Connection, dataset_key: str, source_row: int, row: dict) -> int:
    item_seq = _text(_field(row, "ITEM_SEQ"))
    if not item_seq:
        raise ValueError(f"{dataset_key} row {source_row} missing ITEM_SEQ")
    con.execute(
        """
        INSERT INTO product_flags(
            source_dataset_key,source_row,flag_ordinal,item_seq,category,flag_code,
            flag_name,ingredient_name,dosage_form,details,change_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_key,
            source_row,
            1,
            item_seq,
            "split_caution",
            "S",
            _text(_field(row, "TYPE_NAME")) or "서방정분할주의",
            _text(_field(row, "MAIN_INGR")),
            _text(_field(row, "FORM_CODE_NAME", "FORM_NAME")),
            _text(_field(row, "PROHBT_CONTENT")),
            _date_text(_field(row, "CHANGE_DATE")),
        ),
    )
    return 1


def _import_dur_snapshots(con: sqlite3.Connection, raw_dir: Path) -> tuple[int, int]:
    rule_rows = 0
    flag_rows = 0
    for operation, spec in DUR_ENDPOINTS.items():
        path = raw_dir / spec.filename
        meta = _load_meta(path)
        expected_key = f"mfds_dur:{operation}"
        if meta["dataset_key"] != expected_key or meta["source_family"] != "mfds_dur_item_api":
            raise ValueError(f"DUR snapshot provenance mismatch for {operation}")
        _insert_source_snapshot(con, meta, path)
        imported_source_rows = 0
        with path.open("r", encoding="utf-8") as handle:
            for source_row, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if spec.kind == "rule":
                    _import_rule_row(con, expected_key, source_row, spec.category, row)
                    rule_rows += 1
                elif spec.kind == "flags":
                    flag_rows += _import_flag_row(con, expected_key, source_row, row)
                elif spec.kind == "split":
                    flag_rows += _import_split_row(con, expected_key, source_row, row)
                else:
                    raise ValueError(f"unknown DUR endpoint kind {spec.kind}")
                imported_source_rows += 1
        if imported_source_rows != int(meta["row_count"]):
            raise RuntimeError(
                f"{operation} row mismatch: metadata {meta['row_count']}, imported {imported_source_rows}"
            )
    return rule_rows, flag_rows


def assemble_canonical_database(
    db_path: str | Path,
    kids_dir: str | Path,
    raw_dir: str | Path,
) -> dict:
    db_path = Path(db_path)
    raw_root = Path(raw_dir)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(db_path.name + ".tmp")
    temp.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(temp)) as con:
            con.executescript(SCHEMA)
            con.execute("BEGIN")
            permit_rows = _import_permit_snapshot(con, raw_root)
            product_rule_rows, product_flag_rows = _import_dur_snapshots(con, raw_root)
            xlsx_result = import_xlsx_sources(con, kids_dir)
            built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            con.executemany(
                "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
                [
                    ("schema_version", SCHEMA_VERSION),
                    ("built_at", built_at),
                    ("source_policy", "mfds_permit_api+mfds_dur_item_api+kids_mfds_xlsx"),
                ],
            )
            con.commit()
            con.execute("ANALYZE")
            con.execute("PRAGMA optimize")
            con.commit()
        verification = verify_canonical_database(temp)
        if verification["status"] != "verified":
            raise RuntimeError("canonical verification failed: " + "; ".join(verification["errors"]))
        os.replace(temp, db_path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    stats = canonical_stats(db_path)
    stats.update(
        {
            "permit_source_rows": permit_rows,
            "product_rule_rows_imported": product_rule_rows,
            "product_flag_rows_imported": product_flag_rows,
            "ingredient_rule_rows_imported": xlsx_result["ingredient_rules"],
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_dir": str(raw_root),
        }
    )
    return stats


def build_canonical_database(
    db_path: str | Path,
    kids_dir: str | Path,
    *,
    service_key: str,
    raw_dir: str | Path | None = None,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    api_workers: int = 8,
    progress: bool = True,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
) -> dict:
    db_path = Path(db_path)
    raw_root = Path(raw_dir) if raw_dir is not None else db_path.parent / f"{db_path.stem}.sources"
    sync_canonical_api_sources(
        raw_root,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        workers=api_workers,
        progress=progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
    )
    return assemble_canonical_database(db_path, kids_dir, raw_root)

def canonical_stats(db_path: str | Path) -> dict:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"canonical database not found: {path}")
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        active = con.execute("SELECT COUNT(*) FROM products WHERE permit_status='active'").fetchone()[0]
        product_rules = con.execute("SELECT COUNT(*) FROM product_rules").fetchone()[0]
        product_flags = con.execute("SELECT COUNT(*) FROM product_flags").fetchone()[0]
        ingredient_rules = con.execute("SELECT COUNT(*) FROM ingredient_rules").fetchone()[0]
        source_snapshots = con.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0]
        source_families = {
            row[0]: row[1]
            for row in con.execute("SELECT source_family,COUNT(*) FROM source_snapshots GROUP BY source_family")
        }
        categories = [
            dict(row)
            for row in con.execute(
                """
                SELECT 'product_rule' AS scope,category,COUNT(*) AS rows FROM product_rules GROUP BY category
                UNION ALL
                SELECT 'product_flag',category,COUNT(*) FROM product_flags GROUP BY category
                UNION ALL
                SELECT 'ingredient_rule',category,COUNT(*) FROM ingredient_rules GROUP BY category
                ORDER BY scope,category
                """
            )
        ]
        orphan_rules = con.execute(
            "SELECT COUNT(*) FROM product_rules r LEFT JOIN products p ON p.item_seq=r.item_seq WHERE p.item_seq IS NULL"
        ).fetchone()[0]
        orphan_pairs = con.execute(
            """SELECT COUNT(*) FROM product_rules r LEFT JOIN products p ON p.item_seq=r.paired_item_seq
               WHERE r.paired_item_seq IS NOT NULL AND p.item_seq IS NULL"""
        ).fetchone()[0]
        orphan_flags = con.execute(
            "SELECT COUNT(*) FROM product_flags f LEFT JOIN products p ON p.item_seq=f.item_seq WHERE p.item_seq IS NULL"
        ).fetchone()[0]
        meta = dict(con.execute("SELECT key,value FROM canonical_meta").fetchall())
    return {
        "db_path": str(path),
        "schema_version": meta.get("schema_version"),
        "built_at": meta.get("built_at"),
        "products": products,
        "active_products": active,
        "product_rules": product_rules,
        "product_flags": product_flags,
        "ingredient_rules": ingredient_rules,
        "source_snapshots": source_snapshots,
        "source_families": source_families,
        "orphan_product_rules": orphan_rules,
        "orphan_paired_product_rules": orphan_pairs,
        "orphan_product_flags": orphan_flags,
        "categories": categories,
        "size_bytes": path.stat().st_size,
    }


def verify_canonical_database(db_path: str | Path) -> dict:
    path = Path(db_path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {"db_path": str(path), "status": "invalid", "errors": ["database not found"], "warnings": []}
    try:
        with closing(sqlite3.connect(path)) as con:
            integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
            if integrity != "ok":
                errors.append(f"integrity_check: {integrity}")
            families = {row[0] for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")}
            unsupported = families - CORE_SOURCE_FAMILIES
            missing_families = CORE_SOURCE_FAMILIES - families
            if unsupported:
                errors.append("unsupported source families: " + ", ".join(sorted(unsupported)))
            if missing_families:
                errors.append("missing core source families: " + ", ".join(sorted(missing_families)))
            expected_keys = {PERMIT_DATASET_KEY}
            expected_keys.update(f"mfds_dur:{operation}" for operation in DUR_ENDPOINTS)
            expected_keys.update(f"kids_mfds_xlsx:{category}" for category in XLSX_DATASETS.values())
            actual_keys = {row[0] for row in con.execute("SELECT dataset_key FROM source_snapshots")}
            missing_keys = expected_keys - actual_keys
            extra_keys = actual_keys - expected_keys
            if missing_keys:
                errors.append("missing source snapshots: " + ", ".join(sorted(missing_keys)))
            if extra_keys:
                errors.append("unexpected source snapshots: " + ", ".join(sorted(extra_keys)))
            bad_hashes = con.execute(
                "SELECT COUNT(*) FROM source_snapshots WHERE LENGTH(sha256) != 64"
            ).fetchone()[0]
            if bad_hashes:
                errors.append(f"invalid source hashes: {bad_hashes}")
            schema_version = con.execute(
                "SELECT value FROM canonical_meta WHERE key='schema_version'"
            ).fetchone()
            if not schema_version or schema_version[0] != SCHEMA_VERSION:
                errors.append("schema version mismatch")
            stats = canonical_stats(path)
            if stats["products"] == 0:
                errors.append("no products imported")
            if stats["product_rules"] == 0:
                errors.append("no product rules imported")
            if stats["ingredient_rules"] == 0:
                errors.append("no ingredient rules imported")
            if stats["orphan_product_rules"]:
                warnings.append(f"product rules with ITEM_SEQ absent from permit snapshot: {stats['orphan_product_rules']}")
            if stats["orphan_paired_product_rules"]:
                warnings.append(f"paired product rules absent from permit snapshot: {stats['orphan_paired_product_rules']}")
            if stats["orphan_product_flags"]:
                warnings.append(f"product flags with ITEM_SEQ absent from permit snapshot: {stats['orphan_product_flags']}")
    except sqlite3.DatabaseError as exc:
        errors.append(f"database error: {exc}")
    return {
        "db_path": str(path),
        "status": "verified" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
    }
