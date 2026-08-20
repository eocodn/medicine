from __future__ import annotations

import hashlib
import re
import sqlite3
from typing import Any, Mapping

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DIRECT_ITEM_RULE_CATEGORIES = frozenset({
    "elderly_caution", "therapeutic_duplication_caution"
})


def _direct_item_rule_is_resolved(row: Mapping[str, Any]) -> bool:
    category = str(row.get("category") or "")
    if category == "elderly_caution":
        return True
    if category == "therapeutic_duplication_caution":
        return bool(str(row.get("effect_name") or "").strip())
    return False


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
        and str(meta.get("schema_version") or "").isdigit()
        and meta.get("build_stage") == "complete"
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
        "missing_sources": [],
        "unexpected_sources": [],
        "misclassified_sources": [],
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


def resolved_product_rows(
    con: sqlite3.Connection,
    target_item_seq: str,
    category: str,
) -> list[dict[str, Any]]:
    rows = linked_product_rows(con, target_item_seq, category)
    if category not in _DIRECT_ITEM_RULE_CATEGORIES:
        return rows
    linked_product_keys = {
        (row.get("product_source_dataset_key"), row.get("product_source_row"))
        for row in rows
    }
    con.row_factory = sqlite3.Row
    direct = con.execute(
        """SELECT r.* FROM product_rules r
           WHERE r.item_seq=? AND r.category=? ORDER BY r.source_dataset_key,r.source_row""",
        (target_item_seq, category),
    ).fetchall()
    for raw in direct:
        row = dict(raw)
        source_key = (row.get("source_dataset_key"), row.get("source_row"))
        # Therapeutic-duplication ingredient criteria may carry narrower qualifiers
        # (for example a form exclusion).  They can enrich the exact ITEM_SEQ
        # rule, but must never erase its authoritative effect group.  Elderly
        # criteria have no executable narrowing today, so linked rows remain the
        # enriched representation there and avoid duplicate findings.
        if (
            source_key in linked_product_keys
            and category != "therapeutic_duplication_caution"
        ):
            continue
        if not _direct_item_rule_is_resolved(row):
            continue
        rows.append({
            **row,
            "product_source_dataset_key": row.get("source_dataset_key"),
            "product_source_row": row.get("source_row"),
            "criterion_source_dataset_key": None,
            "criterion_source_row": None,
            "criterion_ingredient_name": None,
            "criterion_paired_ingredient_name": None,
            "criterion_rule_value": None,
            "criterion_dosage_form": None,
            "criterion_note": None,
            "criterion_qualifier_note": None,
            "criterion_details": None,
            "product_dosage_form": row.get("dosage_form"),
            "product_details": row.get("details"),
            "dataset_key": row.get("source_dataset_key"),
            "product_code": row.get("item_seq"),
            "paired_product_code": row.get("paired_item_seq"),
            "rule_value": None,
            "maximum_daily_amount": None,
            "maximum_daily_unit": None,
            "dose_parse_status": None,
            "dose_parse_reason": None,
            "note": None,
            "qualifier_note": None,
            "match_method": "mfds_item_rule",
            "pair_orientation": None,
        })
    return rows


def linked_categories(con: sqlite3.Connection, target_item_seq: str) -> set[str]:
    categories = {
        str(row[0])
        for row in con.execute(
            "SELECT DISTINCT category FROM product_rule_criteria WHERE item_seq=?",
            (target_item_seq,),
        )
        if row[0]
    }
    for category, effect_name in con.execute(
        "SELECT category,effect_name FROM product_rules WHERE item_seq=?",
        (target_item_seq,),
    ):
        direct_row = {"category": category, "effect_name": effect_name}
        if _direct_item_rule_is_resolved(direct_row):
            categories.add(str(category))
    return categories


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
        category = str(row["category"])
        if _direct_item_rule_is_resolved(row):
            continue
        issues.setdefault(category, []).append(row)
    return issues


__all__ = [
    "canonical_manifest", "category_resolution_issues", "has_unlinked_product_rule",
    "item_seq", "linked_categories",
    "linked_product_rows", "resolved_product_rows", "product_flags", "unlinked_product_rules",
]
