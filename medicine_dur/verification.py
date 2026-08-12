from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any


REQUIRED_SOURCE_KEYS = (
    "product:combination_contraindication",
    "product:age_contraindication",
    "product:pregnancy_contraindication",
    "product:dose_caution",
    "product:duration_caution",
    "product:elderly_caution",
    "product:therapeutic_duplication_caution",
    "product_item:dur_product_info",
    "product_item:split_caution",
    "ingredient:combination_contraindication",
    "ingredient:age_contraindication",
    "ingredient:pregnancy_contraindication",
    "ingredient:dose_caution",
    "ingredient:duration_caution",
    "ingredient:elderly_caution",
    "ingredient:therapeutic_duplication_caution",
    "ingredient:lactation_caution",
)

REQUIRED_HEADERS: dict[str, set[str]] = {
    "product:combination_contraindication": {
        "성분명A", "성분코드A", "제품명A", "제품코드A",
        "성분명B", "성분코드B", "제품명B", "제품코드B",
    },
    "product:age_contraindication": {"성분명", "성분코드", "제품명", "제품코드", "특정연령", "특정연령단위"},
    "product:pregnancy_contraindication": {"성분명", "성분코드", "제품명", "제품코드", "금기등급"},
    "product:dose_caution": {"성분명", "성분코드", "제품명", "제품코드", "1일최대투여량"},
    "product:duration_caution": {"성분명", "성분코드", "제품명", "제품코드", "최대투여기간일수"},
    "product:elderly_caution": {"성분명", "성분코드", "제품명", "제품코드"},
    "product:therapeutic_duplication_caution": {"성분코드", "성분명", "제품코드", "효능군"},
    "product_item:dur_product_info": {"ITEM_SEQ", "ITEM_NAME", "EDI_CODE", "TYPE_CODE", "TYPE_NAME"},
    "product_item:split_caution": {"ITEM_SEQ", "ITEM_NAME", "TYPE_NAME", "PROHBT_CONTENT"},
    "ingredient:combination_contraindication": {"연번", "유효성분 '1'", "유효성분 '2'"},
    "ingredient:age_contraindication": {"연번", "성분명", "연령기준", "제형"},
    "ingredient:pregnancy_contraindication": {"연번", "성분명", "임부금기(등급)"},
    "ingredient:dose_caution": {"연번", "성분명(국문)", "성분명(영문)", "제형", "1일 최대용량"},
    "ingredient:duration_caution": {"연번", "성분명(국문)", "성분명(영문)", "제형", "최대 투여기간"},
    "ingredient:elderly_caution": {"연번", "성분명(국문)", "성분명(영문)", "제형"},
    "ingredient:therapeutic_duplication_caution": {"연번", "효능군", "성분명(국문)", "성분명(영문)"},
    "ingredient:lactation_caution": {"연번", "성분명(국문)", "성분명(영문)"},
}

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"(?P<year>20\d{2})[.\-/년 ]+(?P<month>\d{1,2})[.\-/월 ]+(?P<day>\d{1,2})")
APP_TIMEZONE = timezone(timedelta(hours=9), "Asia/Seoul")


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _source_rows(con: sqlite3.Connection) -> list[dict[str, Any]]:
    if not _table_exists(con, "source_files"):
        return []
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(
        """SELECT dataset_key,source_kind,category,source_path,sha256,size_bytes,
                  row_count,imported_at,metadata_json
           FROM source_files ORDER BY dataset_key"""
    ).fetchall()]


def dataset_manifest(con: sqlite3.Connection) -> dict[str, Any]:
    """Return the fast runtime identity of the packaged DUR dataset.

    Full integrity/count/freshness checks belong to ``verify_database`` at
    build/release time. Runtime assessments only need a deterministic identity
    and proof that every required source is represented by a valid manifest row.
    """
    rows = _source_rows(con)
    by_key = {row["dataset_key"]: row for row in rows}
    missing = [key for key in REQUIRED_SOURCE_KEYS if key not in by_key]
    invalid = []
    for key in REQUIRED_SOURCE_KEYS:
        row = by_key.get(key)
        if not row:
            continue
        if not _SHA256_RE.fullmatch(str(row.get("sha256") or "").lower()):
            invalid.append(f"{key}:invalid_sha256")
        if int(row.get("row_count") or 0) <= 0:
            invalid.append(f"{key}:empty_source")
        try:
            metadata = json.loads(row.get("metadata_json") or "{}")
        except (TypeError, ValueError, json.JSONDecodeError):
            invalid.append(f"{key}:invalid_metadata")
        else:
            if not isinstance(metadata, dict):
                invalid.append(f"{key}:invalid_metadata")
                continue
            headers = metadata.get("header")
            if not isinstance(headers, list):
                invalid.append(f"{key}:missing_header_metadata")
                continue
            missing_headers = REQUIRED_HEADERS[key] - {str(value) for value in headers}
            if missing_headers:
                invalid.append(f"{key}:missing_headers={','.join(sorted(missing_headers))}")
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            f"{row['dataset_key']}\0{str(row['sha256']).lower()}\0{row['row_count']}\n".encode("utf-8")
        )
    verified = bool(rows) and not missing and not invalid
    return {
        "status": "verified" if verified else "not_verified",
        "dataset_id": f"sha256:{digest.hexdigest()}" if rows else None,
        "source_count": len(rows),
        "required_source_count": len(REQUIRED_SOURCE_KEYS),
        "missing_sources": missing,
        "invalid_sources": invalid,
        "imported_at": max((str(row.get("imported_at") or "") for row in rows), default=None) or None,
    }


def _parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value)
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        match = _DATE_RE.search(text)
        if not match:
            return None
        try:
            return date(int(match.group("year")), int(match.group("month")), int(match.group("day")))
        except ValueError:
            return None


def _freshness_by_source(con: sqlite3.Connection, rows: list[dict[str, Any]]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for row in rows:
        key = row["dataset_key"]
        # Product CSVs are current snapshots that may legitimately contain no
        # newly issued notices for years. A rule notice date is therefore not
        # the snapshot freshness date; the package/import time is authoritative.
        freshness = (
            _parse_date(row.get("imported_at"))
            if row.get("source_kind") in {"product", "product_item"}
            else None
        )
        if row.get("source_kind") == "ingredient":
            try:
                metadata = json.loads(row.get("metadata_json") or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                metadata = {}
            for value in (metadata.get("title"), metadata.get("effective_date"), metadata.get("date")):
                parsed = _parse_date(value)
                if parsed and (freshness is None or parsed > freshness):
                    freshness = parsed
        result[key] = freshness.isoformat() if freshness else None
    return result


def verify_database(
    db_path: str | Path,
    *,
    max_age_days: int = 730,
    max_snapshot_age_days: int = 90,
    as_of: date | None = None,
) -> dict[str, Any]:
    if max_age_days < 1:
        raise ValueError("max_age_days must be positive")
    if max_snapshot_age_days < 1:
        raise ValueError("max_snapshot_age_days must be positive")
    path = Path(db_path)
    uri = f"file:{path.resolve()}?mode=ro"
    con = sqlite3.connect(uri, uri=True, timeout=30)
    try:
        manifest = dataset_manifest(con)
        issues = [f"missing source: {key}" for key in manifest["missing_sources"]]
        issues.extend(f"invalid source: {item}" for item in manifest["invalid_sources"])
        integrity = con.execute("PRAGMA integrity_check").fetchone()[0]
        if integrity != "ok":
            issues.append(f"sqlite integrity_check: {integrity}")

        rows = _source_rows(con)
        for row in rows:
            source_path = Path(str(row["source_path"]))
            if not source_path.is_absolute():
                source_path = Path.cwd() / source_path
            if not source_path.is_file():
                issues.append(f"source file missing: {row['dataset_key']} path={row['source_path']}")
            else:
                actual_sha256 = _sha256_file(source_path)
                if actual_sha256 != str(row["sha256"]).lower():
                    issues.append(f"source hash mismatch: {row['dataset_key']}")
            table = {
                "product": "product_dur",
                "ingredient": "ingredient_dur",
                "product_item": "product_item_flags",
            }.get(row["source_kind"])
            if table is None:
                issues.append(f"unknown source kind: {row['source_kind']}")
                continue
            if not _table_exists(con, table):
                issues.append(f"missing table: {table}")
                continue
            actual = con.execute(
                f"SELECT COUNT(*) FROM {table} WHERE dataset_key=?", (row["dataset_key"],)
            ).fetchone()[0]
            if actual != int(row["row_count"]):
                issues.append(
                    f"row count mismatch: {row['dataset_key']} manifest={row['row_count']} actual={actual}"
                )

        today = as_of or datetime.now(APP_TIMEZONE).date()
        freshness = _freshness_by_source(con, rows)
        for key in REQUIRED_SOURCE_KEYS:
            value = freshness.get(key)
            parsed = _parse_date(value)
            if parsed is None:
                issues.append(f"freshness unknown: {key}")
            elif (today - parsed).days > max_age_days:
                issues.append(f"source stale: {key} effective={parsed.isoformat()}")
        for row in rows:
            imported = _parse_date(row.get("imported_at"))
            if imported is None:
                issues.append(f"snapshot import date unknown: {row['dataset_key']}")
            elif (today - imported).days > max_snapshot_age_days:
                issues.append(f"snapshot stale: {row['dataset_key']} imported={imported.isoformat()}")

        return {
            **manifest,
            "status": "verified" if manifest["status"] == "verified" and not issues else "failed",
            "integrity_check": integrity,
            "max_age_days": max_age_days,
            "max_snapshot_age_days": max_snapshot_age_days,
            "as_of": today.isoformat(),
            "source_freshness": freshness,
            "issues": issues,
        }
    finally:
        con.close()


__all__ = ["REQUIRED_HEADERS", "REQUIRED_SOURCE_KEYS", "dataset_manifest", "verify_database"]
