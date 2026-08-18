from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any, Mapping

# Keep this allowlist in the Android-packaged medicine_app tree. The canonical
# builder owns the same release policy separately; tests require them to match so
# mobile runtime verification never depends on the omitted medicine_canonical package.
_RUNTIME_SOURCE_FAMILIES = {
    "mfds_permit:products": "mfds_permit_api",
    "mfds_dur:getUsjntTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getSpcifyAgrdeTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getPwnmTabooInfoList03": "mfds_dur_item_api",
    "mfds_dur:getCpctyAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getMdctnPdAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getOdsnAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur:getEfcyDplctInfoList03": "mfds_dur_item_api",
    "mfds_dur:getDurPrdlstInfoList03": "mfds_dur_item_api",
    "mfds_dur:getSeobangjeongPartitnAtentInfoList03": "mfds_dur_item_api",
    "mfds_dur_ingredient:getUsjntTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getPwnmTabooInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getCpctyAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getMdctnPdAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getOdsnAtentInfoList02": "mfds_dur_ingredient_api",
    "mfds_dur_ingredient:getEfcyDplctInfoList02": "mfds_dur_ingredient_api",
}
_RUNTIME_SOURCE_KEYS = frozenset(_RUNTIME_SOURCE_FAMILIES)


_CANONICAL_SCHEMA_VERSION = "9"
_REQUIRED_FAMILIES = set(_RUNTIME_SOURCE_FAMILIES.values())
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def item_seq(product: Mapping[str, Any]) -> str | None:
    value = product.get("catalog_item_seq") or product.get("product_ref") or product.get("product_code")
    text = str(value or "").strip()
    return text or None


def canonical_manifest(con: sqlite3.Connection) -> dict[str, Any]:
    con.row_factory = sqlite3.Row
    meta = {str(row[0]): str(row[1]) for row in con.execute("SELECT key,value FROM canonical_meta")}
    rows = [dict(row) for row in con.execute(
        "SELECT dataset_key,source_family,sha256,row_count,fetched_at,effective_date FROM source_snapshots ORDER BY dataset_key"
    )]
    families = {str(row["source_family"]) for row in rows}
    actual_keys = {str(row["dataset_key"]) for row in rows}
    missing_sources = sorted(_RUNTIME_SOURCE_KEYS - actual_keys)
    unexpected_sources = sorted(actual_keys - _RUNTIME_SOURCE_KEYS)
    misclassified_sources = sorted(
        str(row["dataset_key"])
        for row in rows
        if str(row["dataset_key"]) in _RUNTIME_SOURCE_FAMILIES
        and _RUNTIME_SOURCE_FAMILIES[str(row["dataset_key"])] != str(row["source_family"])
    )
    invalid = [
        str(row["dataset_key"])
        for row in rows
        if not _SHA256_RE.fullmatch(str(row.get("sha256") or "").lower())
        or int(row.get("row_count") or 0) <= 0
    ]
    digest = hashlib.sha256()
    for row in rows:
        digest.update(
            f"{row['dataset_key']}\0{str(row['sha256']).lower()}\0{row['row_count']}\n".encode("utf-8")
        )
    verified = (
        bool(rows)
        and meta.get("schema_version") == _CANONICAL_SCHEMA_VERSION
        and meta.get("build_stage") == "complete"
        and families == _REQUIRED_FAMILIES
        and not missing_sources
        and not unexpected_sources
        and not misclassified_sources
        and not invalid
    )
    return {
        "status": "verified" if verified else "not_verified",
        "dataset_id": f"sha256:{digest.hexdigest()}" if rows else None,
        "schema_version": meta.get("schema_version"),
        "built_at": meta.get("built_at"),
        "source_count": len(rows),
        "source_families": sorted(families),
        "invalid_sources": invalid,
        "missing_sources": missing_sources,
        "unexpected_sources": unexpected_sources,
        "misclassified_sources": misclassified_sources,
    }


def product_flags(con: sqlite3.Connection, target_item_seq: str) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    return [dict(row) for row in con.execute(
        """SELECT item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,
                  details,change_date,source_dataset_key,source_row
           FROM product_flags WHERE item_seq=? ORDER BY category,source_row,flag_ordinal""",
        (target_item_seq,),
    )]


def linked_product_rows(
    con: sqlite3.Connection,
    target_item_seq: str,
    category: str,
) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    rows = con.execute(
        """SELECT * FROM product_rule_criteria
           WHERE item_seq=? AND category=?
           ORDER BY product_source_dataset_key,product_source_row,
                    criterion_source_dataset_key,criterion_source_row""",
        (target_item_seq, category),
    ).fetchall()
    result: list[dict[str, Any]] = []
    for raw in rows:
        row = dict(raw)
        result.append({
            **row,
            "dataset_key": row.get("criterion_source_dataset_key"),
            "source_row": row.get("criterion_source_row"),
            "product_code": row.get("item_seq"),
            "paired_product_code": row.get("paired_item_seq"),
            "ingredient_name": row.get("criterion_ingredient_name") or row.get("ingredient_name"),
            "paired_ingredient_name": row.get("criterion_paired_ingredient_name") or row.get("paired_ingredient_name"),
            "rule_value": row.get("criterion_rule_value"),
            "dosage_form": row.get("criterion_dosage_form"),
            "product_dosage_form": row.get("product_dosage_form"),
            "maximum_daily_amount": row.get("criterion_maximum_daily_amount"),
            "maximum_daily_unit": row.get("criterion_maximum_daily_unit"),
            "dose_parse_status": row.get("criterion_dose_parse_status"),
            "dose_parse_reason": row.get("criterion_dose_parse_reason"),
            "note": row.get("criterion_note"),
            "qualifier_note": row.get("criterion_qualifier_note"),
            # MFDS product detail retains product-specific timing/quantity evidence.
            "details": row.get("product_details") or row.get("criterion_details"),
            "notice_no": None,
            "notice_date": None,
        })
    return result


def linked_categories(con: sqlite3.Connection, target_item_seq: str) -> set[str]:
    return {
        str(row[0])
        for row in con.execute(
            "SELECT DISTINCT category FROM product_rule_criteria WHERE item_seq=?",
            (target_item_seq,),
        )
        if row[0]
    }


def unlinked_product_rules(
    con: sqlite3.Connection,
    target_item_seq: str,
    category: str | None = None,
) -> list[dict[str, Any]]:
    con.row_factory = sqlite3.Row
    params: list[Any] = [target_item_seq]
    category_sql = ""
    if category:
        category_sql = " AND r.category=?"
        params.append(category)
    return [dict(row) for row in con.execute(
        f"""SELECT r.*
            FROM product_rules r
            LEFT JOIN product_criterion_links l ON l.product_rule_id=r.id
            WHERE r.item_seq=? {category_sql}
            GROUP BY r.id
            HAVING COUNT(l.criterion_rule_id)=0
            ORDER BY r.category,r.source_dataset_key,r.source_row""",
        params,
    )]


def has_unlinked_product_rule(con: sqlite3.Connection, target_item_seq: str, category: str) -> bool:
    return bool(unlinked_product_rules(con, target_item_seq, category))


def category_resolution_issues(con: sqlite3.Connection, target_item_seq: str) -> dict[str, list[dict[str, Any]]]:
    issues: dict[str, list[dict[str, Any]]] = {}
    for row in unlinked_product_rules(con, target_item_seq):
        issues.setdefault(str(row["category"]), []).append(row)
    return issues


__all__ = [
    "canonical_manifest", "category_resolution_issues", "has_unlinked_product_rule",
    "item_seq", "linked_categories",
    "linked_product_rows", "product_flags", "unlinked_product_rules",
]
