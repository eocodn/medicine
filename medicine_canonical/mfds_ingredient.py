from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
import urllib.parse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .dose_criteria import materialize_dose_criteria
from .schema import SCHEMA, SCHEMA_VERSION
from .sources import _request_json, _sync_paginated_jsonl


APP_TIMEZONE = ZoneInfo("Asia/Seoul")
MFDS_INGREDIENT_API_BASE = "https://apis.data.go.kr/1471000/DURIrdntInfoService03"
MFDS_INGREDIENT_SOURCE_FAMILY = "mfds_dur_ingredient_api"
MFDS_INGREDIENT_PAGE_SIZE_MAX = 500
MFDS_INGREDIENT_SOURCE_POLICY = MFDS_INGREDIENT_SOURCE_FAMILY


@dataclass(frozen=True)
class MfdsIngredientEndpoint:
    category: str
    filename: str
    rule_field: str | None = None
    rule_required: bool = True


MFDS_INGREDIENT_ENDPOINTS: dict[str, MfdsIngredientEndpoint] = {
    "getUsjntTabooInfoList02": MfdsIngredientEndpoint(
        "combination_contraindication", "dur_ingredient_combination.jsonl"
    ),
    "getSpcifyAgrdeTabooInfoList02": MfdsIngredientEndpoint(
        "age_contraindication", "dur_ingredient_age.jsonl", "AGE_BASE"
    ),
    "getPwnmTabooInfoList02": MfdsIngredientEndpoint(
        "pregnancy_contraindication", "dur_ingredient_pregnancy.jsonl", "GRADE"
    ),
    "getCpctyAtentInfoList02": MfdsIngredientEndpoint(
        "dose_caution", "dur_ingredient_dose.jsonl", "MAX_QTY", False
    ),
    "getMdctnPdAtentInfoList02": MfdsIngredientEndpoint(
        "duration_caution", "dur_ingredient_duration.jsonl", "MAX_DOSAGE_TERM"
    ),
    "getOdsnAtentInfoList02": MfdsIngredientEndpoint(
        "elderly_caution", "dur_ingredient_elderly.jsonl"
    ),
    "getEfcyDplctInfoList02": MfdsIngredientEndpoint(
        "therapeutic_duplication_caution", "dur_ingredient_duplication.jsonl", "EFFECT_CODE"
    ),
}


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


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True, default=str)


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
    payload = _request_json(f"{MFDS_INGREDIENT_API_BASE}/{operation}?{params}", label=label)
    return _extract_ingredient_response(payload, label)


def sync_mfds_ingredient_sources(
    raw_dir: str | Path,
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

    root = Path(raw_dir)
    root.mkdir(parents=True, exist_ok=True)
    fetcher = fetch_page or (
        lambda operation, page, size: fetch_mfds_ingredient_page(key, operation, page, size)
    )
    sources = []
    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items():
        sources.append(
            _sync_paginated_jsonl(
                root / spec.filename,
                dataset_key=f"mfds_dur_ingredient:{operation}",
                source_family=MFDS_INGREDIENT_SOURCE_FAMILY,
                source_locator=f"{MFDS_INGREDIENT_API_BASE}/{operation}",
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


def _load_snapshot_meta(path: Path) -> dict:
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not meta_path.exists():
        raise FileNotFoundError(f"missing MFDS ingredient snapshot metadata: {meta_path}")
    payload = json.loads(meta_path.read_text(encoding="utf-8"))
    required = {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
    missing = required - payload.keys()
    if missing:
        raise ValueError(f"invalid MFDS ingredient snapshot metadata: missing {sorted(missing)}")
    actual_sha = _sha256(path)
    if actual_sha != payload["sha256"]:
        raise ValueError(
            f"sha256 mismatch for MFDS ingredient snapshot {path}: "
            f"expected {payload['sha256']}, got {actual_sha}"
        )
    return payload


def _insert_source_snapshot(
    con: sqlite3.Connection, meta: dict, path: Path, *, dataset_key: str, operation: str
) -> None:
    expected_locator = f"{MFDS_INGREDIENT_API_BASE}/{operation}"
    if meta["dataset_key"] != dataset_key:
        raise ValueError(f"MFDS ingredient snapshot dataset mismatch for {operation}")
    if meta["source_family"] != MFDS_INGREDIENT_SOURCE_FAMILY:
        raise ValueError(f"MFDS ingredient snapshot family mismatch for {operation}")
    if meta["source_locator"] != expected_locator:
        raise ValueError(f"MFDS ingredient snapshot locator mismatch for {operation}")
    con.execute(
        """
        INSERT INTO source_snapshots(
            dataset_key,source_family,source_locator,snapshot_path,fetched_at,
            row_count,reported_row_count,sha256,metadata_json
        ) VALUES(?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_key,
            MFDS_INGREDIENT_SOURCE_FAMILY,
            expected_locator,
            str(path),
            meta.get("fetched_at"),
            int(meta["row_count"]),
            int(meta.get("reported_row_count") or 0),
            meta["sha256"],
            _json(meta),
        ),
    )


def _required_text(row: dict, field: str, *, dataset_key: str, source_row: int) -> str:
    value = _text(_field(row, field))
    if not value:
        raise ValueError(f"{dataset_key} row {source_row} missing {field}")
    return value


def _canonical_rule(
    row: dict, spec: MfdsIngredientEndpoint, *, dataset_key: str, source_row: int
) -> dict:
    ingredient_name = _required_text(
        row, "INGR_ENG_NAME", dataset_key=dataset_key, source_row=source_row
    )
    paired_name = None
    if spec.category == "combination_contraindication":
        paired_name = _required_text(
            row, "MIXTURE_INGR_ENG_NAME", dataset_key=dataset_key, source_row=source_row
        )
    rule_value = None
    if spec.rule_field:
        rule_value = _text(_field(row, spec.rule_field))
        if spec.rule_required and not rule_value:
            raise ValueError(f"{dataset_key} row {source_row} missing {spec.rule_field}")

    note_parts: list[str] = []
    if spec.category == "therapeutic_duplication_caution":
        series = _text(_field(row, "SERS_NAME"))
        if series:
            note_parts.append(series)
    remark = _text(_field(row, "REMARK"))
    if remark and remark not in note_parts:
        note_parts.append(remark)

    return {
        "category": spec.category,
        "sequence_text": _text(_field(row, "DUR_SEQ")),
        "ingredient_name": ingredient_name,
        "ingredient_name_ko": _text(_field(row, "INGR_NAME", "INGR_KOR_NAME")),
        "paired_ingredient_name": paired_name,
        "rule_value": rule_value,
        "dosage_form": _text(_field(row, "FORM_NAME")),
        "note": "\n".join(note_parts) or None,
        "details": _text(_field(row, "PROHBT_CONTENT")),
    }


def import_mfds_ingredient_snapshots(
    con: sqlite3.Connection, raw_dir: str | Path
) -> dict:
    root = Path(raw_dir)
    source_rows = 0
    imported_rows = 0
    deleted_rows = 0
    category_counts: dict[str, int] = {}

    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items():
        path = root / spec.filename
        if not path.exists():
            raise FileNotFoundError(f"missing MFDS ingredient snapshot: {path}")
        meta = _load_snapshot_meta(path)
        dataset_key = f"mfds_dur_ingredient:{operation}"
        _insert_source_snapshot(con, meta, path, dataset_key=dataset_key, operation=operation)

        imported_source_rows = 0
        with path.open("r", encoding="utf-8") as handle:
            for source_row, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                row = json.loads(line)
                if not isinstance(row, dict):
                    raise ValueError(f"{dataset_key} row {source_row} is not a JSON object")
                state = _text(_field(row, "DEL_YN"))
                if state == "삭제":
                    deleted_rows += 1
                elif state == "정상":
                    canonical = _canonical_rule(
                        row, spec, dataset_key=dataset_key, source_row=source_row
                    )
                    con.execute(
                        """
                        INSERT INTO ingredient_rules(
                            source_dataset_key,source_row,category,sequence_text,ingredient_name,
                            ingredient_name_ko,paired_ingredient_name,rule_value,dosage_form,note,details
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?)
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
                            canonical["details"],
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


def _preview_stats(db_path: Path) -> dict:
    with closing(sqlite3.connect(db_path)) as con:
        categories = {
            str(category): int(count)
            for category, count in con.execute(
                "SELECT category,COUNT(*) FROM ingredient_rules GROUP BY category ORDER BY category"
            )
        }
        return {
            "source_snapshots": int(con.execute("SELECT COUNT(*) FROM source_snapshots").fetchone()[0]),
            "source_rows": int(
                con.execute("SELECT COALESCE(SUM(row_count),0) FROM source_snapshots").fetchone()[0]
            ),
            "ingredient_rules": int(con.execute("SELECT COUNT(*) FROM ingredient_rules").fetchone()[0]),
            "ingredient_rules_by_category": categories,
            "dose_criteria": int(con.execute("SELECT COUNT(*) FROM dose_criteria").fetchone()[0]),
        }


def verify_mfds_ingredient_preview(db_path: str | Path) -> dict:
    path = Path(db_path)
    errors: list[str] = []
    if not path.is_file():
        return {"status": "invalid", "db_path": str(path), "errors": ["database not found"]}
    with closing(sqlite3.connect(path)) as con:
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            errors.append("SQLite integrity check failed")
        foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            errors.append(f"foreign key violations: {len(foreign_keys)}")
        families = {
            str(row[0]) for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")
        }
        if families != {MFDS_INGREDIENT_SOURCE_FAMILY}:
            errors.append("preview source family mismatch")
        expected_keys = {
            f"mfds_dur_ingredient:{operation}" for operation in MFDS_INGREDIENT_ENDPOINTS
        }
        actual_keys = {str(row[0]) for row in con.execute("SELECT dataset_key FROM source_snapshots")}
        if actual_keys != expected_keys:
            errors.append("preview source key mismatch")
        bad_hashes = int(
            con.execute("SELECT COUNT(*) FROM source_snapshots WHERE LENGTH(sha256) != 64").fetchone()[0]
        )
        if bad_hashes:
            errors.append(f"invalid source hashes: {bad_hashes}")
        expected_categories = {spec.category for spec in MFDS_INGREDIENT_ENDPOINTS.values()}
        actual_categories = {
            str(row[0]) for row in con.execute("SELECT DISTINCT category FROM ingredient_rules")
        }
        missing_categories = expected_categories - actual_categories
        if missing_categories:
            errors.append("missing ingredient categories: " + ", ".join(sorted(missing_categories)))
        dose_rules = int(
            con.execute("SELECT COUNT(*) FROM ingredient_rules WHERE category='dose_caution'").fetchone()[0]
        )
        dose_criteria = int(con.execute("SELECT COUNT(*) FROM dose_criteria").fetchone()[0])
        if dose_rules != dose_criteria:
            errors.append("dose criteria coverage mismatch")
        meta = dict(con.execute("SELECT key,value FROM canonical_meta"))
        if meta.get("schema_version") != SCHEMA_VERSION:
            errors.append("schema version mismatch")
        if meta.get("source_policy") != MFDS_INGREDIENT_SOURCE_POLICY:
            errors.append("preview source policy mismatch")
        if meta.get("build_stage") != "ingredient_preview":
            errors.append("preview build stage mismatch")
    return {"status": "verified" if not errors else "invalid", "db_path": str(path), "errors": errors}


def assemble_mfds_ingredient_preview(
    db_path: str | Path, raw_dir: str | Path
) -> dict:
    output = Path(db_path)
    root = Path(raw_dir)
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(temporary)) as con:
            con.executescript(SCHEMA)
            con.execute("BEGIN")
            import_result = import_mfds_ingredient_snapshots(con, root)
            dose_result = materialize_dose_criteria(con)
            con.executemany(
                "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
                [
                    ("schema_version", SCHEMA_VERSION),
                    ("source_policy", MFDS_INGREDIENT_SOURCE_POLICY),
                    ("build_stage", "ingredient_preview"),
                    ("built_at", datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")),
                ],
            )
            con.commit()
            con.execute("ANALYZE")
            con.execute("PRAGMA optimize")
            con.commit()
        verification = verify_mfds_ingredient_preview(temporary)
        if verification["status"] != "verified":
            raise RuntimeError(
                "MFDS ingredient preview verification failed: "
                + "; ".join(verification["errors"])
            )
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise

    stats = _preview_stats(output)
    stats.update(
        {
            "status": "built",
            "db_path": str(output),
            "raw_dir": str(root),
            "deleted_rows_skipped": import_result["deleted_rows_skipped"],
            "dose_criteria_parsed": dose_result.get("dose_criteria_parsed", 0),
            "dose_criteria_not_evaluable": dose_result.get("dose_criteria_not_evaluable", 0),
            "elapsed_seconds": round(time.monotonic() - started, 3),
        }
    )
    return stats


def build_mfds_ingredient_preview(
    db_path: str | Path,
    *,
    raw_dir: str | Path,
    service_key: str,
    page_size: int = MFDS_INGREDIENT_PAGE_SIZE_MAX,
    workers: int = 8,
    progress: bool = True,
    fetch_page: IngredientFetchPage | None = None,
) -> dict:
    sync_mfds_ingredient_sources(
        raw_dir,
        service_key=service_key,
        page_size=page_size,
        workers=workers,
        progress=progress,
        fetch_page=fetch_page,
    )
    return assemble_mfds_ingredient_preview(db_path, raw_dir)


__all__ = [
    "MFDS_INGREDIENT_API_BASE",
    "MFDS_INGREDIENT_ENDPOINTS",
    "MFDS_INGREDIENT_SOURCE_FAMILY",
    "MfdsIngredientEndpoint",
    "assemble_mfds_ingredient_preview",
    "build_mfds_ingredient_preview",
    "fetch_mfds_ingredient_page",
    "import_mfds_ingredient_snapshots",
    "sync_mfds_ingredient_sources",
    "verify_mfds_ingredient_preview",
]