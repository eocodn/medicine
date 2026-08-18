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

from .dose_criteria import materialize_dose_criteria
from .inspection import canonical_stats, verify_canonical_database
from .mfds_ingredient import (
    IngredientFetchPage,
    import_mfds_ingredient_snapshots,
    sync_mfds_ingredient_sources,
)
from .linking import materialize_product_criterion_links
from .schema import SCHEMA, SCHEMA_VERSION
from .source_policy import CANONICAL_SOURCE_POLICY
from .sources import (
    DUR_ENDPOINTS,
    PERMIT_DATASET_KEY,
    PERMIT_FILENAME,
    DurFetchPage,
    PermitFetchPage,
    sync_canonical_api_sources,
)

APP_TIMEZONE = ZoneInfo("Asia/Seoul")
FLAG_CATEGORY_BY_CODE = {
    "B": "age_contraindication",
    "C": "pregnancy_contraindication",
    "D": "dose_caution",
    "E": "duration_caution",
    "F": "elderly_caution",
}
FLAG_NAME_BY_CODE = {
    "B": "특정연령대금기",
    "C": "임부금기",
    "D": "용량주의",
    "E": "투여기간주의",
    "F": "노인주의",
}
IGNORED_DUR_PRODUCT_FLAG_CODES = {"I"}
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
            source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name,
            ingredient_name_en,paired_item_seq,paired_ingredient_code,paired_ingredient_name,
            paired_ingredient_name_en,effect_name,dosage_form,
            details,notification_date,change_date
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_key,
            source_row,
            category,
            item_seq,
            _text(_field(row, "INGR_CODE")),
            _text(_field(row, "INGR_NAME", "INGR_KOR_NAME")),
            _text(_field(row, "INGR_ENG_NAME")),
            _text(_field(row, "MIXTURE_ITEM_SEQ")),
            _text(_field(row, "MIXTURE_INGR_CODE")),
            _text(_field(row, "MIXTURE_INGR_KOR_NAME", "MIXTURE_INGR_NAME")),
            _text(_field(row, "MIXTURE_INGR_ENG_NAME")),
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
        if code in IGNORED_DUR_PRODUCT_FLAG_CODES:
            continue
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


def _default_ingredient_raw_dir(raw_root: Path) -> Path:
    return raw_root.parent / "mfds_ingredient"


def sync_reference_sources(
    raw_dir: str | Path,
    ingredient_raw_dir: str | Path,
    *,
    service_key: str,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    ingredient_page_size: int = 500,
    workers: int = 8,
    progress: bool = True,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
    ingredient_fetch_page: IngredientFetchPage | None = None,
) -> dict:
    """Sync the complete authoritative MFDS source set used by canonical builds."""
    product_sources = sync_canonical_api_sources(
        raw_dir,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        workers=workers,
        progress=progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
    )
    ingredient_sources = sync_mfds_ingredient_sources(
        ingredient_raw_dir,
        service_key=service_key,
        page_size=ingredient_page_size,
        workers=workers,
        progress=progress,
        fetch_page=ingredient_fetch_page,
    )
    return {
        "product_sources": product_sources,
        "ingredient_sources": ingredient_sources,
        "source_rows": int(product_sources["source_rows"])
        + int(ingredient_sources["source_rows"]),
    }


def populate_canonical_source_tables(
    con: sqlite3.Connection,
    raw_dir: str | Path,
    ingredient_raw_dir: str | Path,
) -> dict:
    """Populate authoritative MFDS source tables without identity linking."""
    raw_root = Path(raw_dir)
    ingredient_root = Path(ingredient_raw_dir)
    permit_rows = _import_permit_snapshot(con, raw_root)
    product_rule_rows, product_flag_rows = _import_dur_snapshots(con, raw_root)
    ingredient_result = import_mfds_ingredient_snapshots(con, ingredient_root)
    dose_result = materialize_dose_criteria(con)
    return {
        "permit_source_rows": permit_rows,
        "product_rule_rows_imported": product_rule_rows,
        "product_flag_rows_imported": product_flag_rows,
        "ingredient_rule_rows_imported": ingredient_result["ingredient_rules"],
        "ingredient_source_rows": ingredient_result["source_rows"],
        "ingredient_deleted_rows_skipped": ingredient_result["deleted_rows_skipped"],
        **dose_result,
    }


def assemble_canonical_database(
    db_path: str | Path,
    raw_dir: str | Path,
    ingredient_raw_dir: str | Path | None = None,
) -> dict:
    db_path = Path(db_path)
    raw_root = Path(raw_dir)
    ingredient_root = (
        Path(ingredient_raw_dir)
        if ingredient_raw_dir is not None
        else _default_ingredient_raw_dir(raw_root)
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(db_path.name + ".tmp")
    temp.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(temp)) as con:
            con.executescript(SCHEMA)
            con.execute("BEGIN")
            source_result = populate_canonical_source_tables(con, raw_root, ingredient_root)
            link_result = materialize_product_criterion_links(con)
            built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            con.executemany(
                "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
                [
                    ("schema_version", SCHEMA_VERSION),
                    ("built_at", built_at),
                    ("source_policy", CANONICAL_SOURCE_POLICY),
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
            **source_result,
            **link_result,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_dir": str(raw_root),
            "ingredient_raw_dir": str(ingredient_root),
        }
    )
    return stats


def build_canonical_database(
    db_path: str | Path,
    *,
    service_key: str,
    raw_dir: str | Path | None = None,
    ingredient_raw_dir: str | Path | None = None,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    ingredient_page_size: int = 500,
    api_workers: int = 8,
    progress: bool = True,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
    ingredient_fetch_page: IngredientFetchPage | None = None,
) -> dict:
    db_path = Path(db_path)
    raw_root = Path(raw_dir) if raw_dir is not None else db_path.parent / f"{db_path.stem}.sources"
    ingredient_root = (
        Path(ingredient_raw_dir)
        if ingredient_raw_dir is not None
        else _default_ingredient_raw_dir(raw_root)
    )
    sync_reference_sources(
        raw_root,
        ingredient_root,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        ingredient_page_size=ingredient_page_size,
        workers=api_workers,
        progress=progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
        ingredient_fetch_page=ingredient_fetch_page,
    )
    return assemble_canonical_database(db_path, raw_root, ingredient_root)


__all__ = [
    "assemble_canonical_database",
    "build_canonical_database",
    "populate_canonical_source_tables",
    "sync_reference_sources",
]
