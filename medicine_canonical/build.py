from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from medicine_reference.mfds_sources import PERMIT_SOURCE

from .canonical_job import (
    canonical_build_input_fingerprint,
    canonical_build_stage,
    checkpoint_result,
)
from .dose_criteria import materialize_dose_criteria
from .inspection import canonical_stats, verify_canonical_database
from .job_lifecycle import JobLifecycle, sqlite_heartbeat
from .mfds_ingredient import (
    IngredientFetchPage,
    import_mfds_ingredient_snapshots,
    sync_mfds_ingredient_sources,
)
from .linking import materialize_product_criterion_links
from .product_search_documents import materialize_product_search_documents
from .schema import SCHEMA, SCHEMA_VERSION
from .source_layout import MfdsSourceLayout
from .snapshot_io import (
    insert_source_snapshot,
    load_snapshot_metadata,
    sha256_file,
)
from .source_policy import CANONICAL_SOURCE_POLICY
from .sources import (
    DUR_ENDPOINTS,
    PERMIT_DATASET_KEY,
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


def _normalize_permit_status(cancel_name, cancel_date=None) -> str:
    raw = _text(cancel_name)
    if raw in PERMIT_STATUS_BY_CANCEL_NAME:
        return PERMIT_STATUS_BY_CANCEL_NAME[raw]
    return "inactive_unknown" if _text(cancel_date) else "unknown"


def _import_permit_snapshot(con: sqlite3.Connection, source_layout: MfdsSourceLayout) -> int:
    path = source_layout.path_for(PERMIT_SOURCE)
    meta = load_snapshot_metadata(path, label="API snapshot")
    if (
        meta["dataset_key"] != PERMIT_SOURCE.dataset_key
        or meta["source_family"] != PERMIT_SOURCE.source_family
    ):
        raise ValueError("permit snapshot provenance mismatch")
    insert_source_snapshot(con, meta, path)
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


def _import_dur_snapshots(
    con: sqlite3.Connection, source_layout: MfdsSourceLayout
) -> tuple[int, int]:
    rule_rows = 0
    flag_rows = 0
    for operation, spec in DUR_ENDPOINTS.items():
        path = source_layout.path_for(spec)
        meta = load_snapshot_metadata(path, label="API snapshot")
        expected_key = spec.dataset_key
        if meta["dataset_key"] != expected_key or meta["source_family"] != spec.source_family:
            raise ValueError(f"DUR snapshot provenance mismatch for {operation}")
        insert_source_snapshot(con, meta, path)
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


def sync_reference_sources(
    source_layout: MfdsSourceLayout,
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
        source_layout,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        workers=workers,
        progress=progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
    )
    ingredient_sources = sync_mfds_ingredient_sources(
        source_layout,
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
    source_layout: MfdsSourceLayout,
) -> dict:
    """Populate authoritative MFDS source tables without identity linking."""
    permit_rows = _import_permit_snapshot(con, source_layout)
    product_rule_rows, product_flag_rows = _import_dur_snapshots(con, source_layout)
    ingredient_result = import_mfds_ingredient_snapshots(con, source_layout)
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
    source_layout: MfdsSourceLayout,
    *,
    progress=None,
    checkpoint_path: str | Path | None = None,
) -> dict:
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(db_path.name + ".tmp")
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else db_path.with_name(db_path.name + ".build.checkpoint.json")
    )
    input_fingerprint = canonical_build_input_fingerprint(source_layout)
    try:
        lifecycle = JobLifecycle(
            "canonical-build",
            checkpoint,
            input_fingerprint=input_fingerprint,
            progress=progress,
            total_steps=4,
        )
    except RuntimeError:
        temp.unlink(missing_ok=True)
        raise
    started = time.monotonic()
    current_phase = "startup"
    lifecycle.started()
    try:
        phase = lifecycle.completed_phase
        if phase not in {None, "source_import", "materialized", "verified"}:
            lifecycle.discard(f"unknown completed phase {phase!r}")
        if phase is not None and lifecycle.artifacts.get("staged_db") != str(temp):
            lifecycle.discard("staged canonical database path changed")
        if phase == "source_import" and canonical_build_stage(temp) != "source":
            lifecycle.discard("authoritative staged database is not at source stage")
        if phase == "materialized" and canonical_build_stage(temp) != "complete":
            lifecycle.discard("authoritative staged database is not materialized")

        if phase is None:
            temp.unlink(missing_ok=True)
            current_phase = "source_import"
            lifecycle.step_started(current_phase, 1)
            with closing(sqlite3.connect(temp)) as con, sqlite_heartbeat(
                con, lifecycle, current_phase
            ):
                con.executescript(SCHEMA)
                con.execute("BEGIN")
                source_result = populate_canonical_source_tables(con, source_layout)
                con.executemany(
                    "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
                    [
                        ("schema_version", SCHEMA_VERSION),
                        ("source_policy", CANONICAL_SOURCE_POLICY),
                        ("build_stage", "source"),
                    ],
                )
                con.commit()
            lifecycle.checkpoint(
                current_phase,
                {"staged_db": str(temp), "source_result": source_result},
            )
            lifecycle.step_completed(current_phase, 1)
            phase = lifecycle.completed_phase
        else:
            source_result = checkpoint_result(lifecycle, "source_result")

        if phase == "source_import":
            current_phase = "materialized"
            lifecycle.step_started(current_phase, 2)
            with closing(sqlite3.connect(temp)) as con, sqlite_heartbeat(
                con, lifecycle, current_phase
            ):
                con.execute("BEGIN")
                search_result = materialize_product_search_documents(con, None)
                link_result = materialize_product_criterion_links(con)
                built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
                con.execute("UPDATE canonical_meta SET value='complete' WHERE key='build_stage'")
                con.execute(
                    "INSERT INTO canonical_meta(key,value) VALUES('built_at',?)",
                    (built_at,),
                )
                con.commit()
                con.execute("ANALYZE")
                con.execute("PRAGMA optimize")
                con.commit()
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_db": str(temp),
                    "source_result": source_result,
                    "search_result": search_result,
                    "link_result": link_result,
                },
            )
            lifecycle.step_completed(current_phase, 2)
            phase = lifecycle.completed_phase
        else:
            search_result = checkpoint_result(lifecycle, "search_result")
            link_result = checkpoint_result(lifecycle, "link_result")

        if phase == "materialized":
            current_phase = "verified"
            lifecycle.step_started(current_phase, 3)
            lifecycle.heartbeat(current_phase, force=True)
            verification = verify_canonical_database(temp)
            if verification["status"] != "verified":
                raise RuntimeError(
                    "canonical verification failed: " + "; ".join(verification["errors"])
                )
            staged_sha256 = sha256_file(temp)
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_db": str(temp),
                    "source_result": source_result,
                    "search_result": search_result,
                    "link_result": link_result,
                    "staged_sha256": staged_sha256,
                },
            )
            lifecycle.step_completed(current_phase, 3)
            phase = lifecycle.completed_phase

        if phase != "verified":
            lifecycle.discard(f"cannot commit from completed phase {phase!r}")
        staged_sha256 = lifecycle.artifacts.get("staged_sha256")
        if not isinstance(staged_sha256, str) or not staged_sha256:
            lifecycle.discard("verified checkpoint is missing staged sha256")

        current_phase = "commit"
        lifecycle.step_started(current_phase, 4)
        if temp.exists():
            if sha256_file(temp) != staged_sha256:
                lifecycle.discard("verified staged database bytes changed")
            os.replace(temp, db_path)
        elif db_path.exists():
            if sha256_file(db_path) != staged_sha256:
                lifecycle.discard("committed database does not match verified staged sha256")
        else:
            lifecycle.discard("verified staged and committed databases are both missing")
        lifecycle.step_completed(current_phase, 4)
        lifecycle.completed()
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        if lifecycle.completed_phase is None:
            temp.unlink(missing_ok=True)
        raise
    stats = canonical_stats(db_path)
    stats.update(
        {
            **source_result,
            "product_search": search_result,
            **link_result,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_dir": str(source_layout.product_dir),
            "ingredient_raw_dir": str(source_layout.ingredient_dir),
        }
    )
    return stats


def build_canonical_database(
    db_path: str | Path,
    *,
    service_key: str,
    source_layout: MfdsSourceLayout | None = None,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    ingredient_page_size: int = 500,
    api_workers: int = 8,
    progress: bool = True,
    job_progress=None,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
    ingredient_fetch_page: IngredientFetchPage | None = None,
) -> dict:
    db_path = Path(db_path)
    layout = source_layout or MfdsSourceLayout.for_database(db_path)
    sync_reference_sources(
        layout,
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
    return assemble_canonical_database(db_path, layout, progress=job_progress)


__all__ = [
    "assemble_canonical_database",
    "build_canonical_database",
    "populate_canonical_source_tables",
    "sync_reference_sources",
]
