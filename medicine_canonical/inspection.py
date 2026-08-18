from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .mfds_remark_registry import reviewed_mfds_remark
from .schema import CORE_SOURCE_FAMILIES, SCHEMA_VERSION
from .source_policy import EXPECTED_CANONICAL_SOURCE_FAMILIES, EXPECTED_CANONICAL_SOURCE_KEYS


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
        dose_criteria = con.execute("SELECT COUNT(*) FROM dose_criteria").fetchone()[0]
        dose_criteria_parsed = con.execute(
            "SELECT COUNT(*) FROM dose_criteria WHERE parse_status='parsed'"
        ).fetchone()[0]
        dose_criteria_not_evaluable = con.execute(
            "SELECT COUNT(*) FROM dose_criteria WHERE parse_status='not_evaluable'"
        ).fetchone()[0]
        linked_dose_products = con.execute(
            """SELECT COUNT(DISTINCT r.item_seq)
               FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               WHERE r.category='dose_caution'"""
        ).fetchone()[0]
        active_linked_dose_products = con.execute(
            """SELECT COUNT(DISTINCT r.item_seq)
               FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               JOIN products p ON p.item_seq=r.item_seq
               WHERE r.category='dose_caution' AND p.permit_status='active'"""
        ).fetchone()[0]
        dose_evaluable_sql = """
            WITH per_product AS (
                SELECT r.item_seq,
                       COUNT(*) AS linked_rows,
                       SUM(CASE WHEN d.parse_status='parsed' THEN 1 ELSE 0 END) AS parsed_rows,
                       COUNT(DISTINCT CASE WHEN d.parse_status='parsed'
                           THEN d.maximum_daily_amount || char(0) || d.maximum_daily_unit END) AS thresholds
                FROM product_criterion_links l
                JOIN product_rules r ON r.id=l.product_rule_id
                LEFT JOIN dose_criteria d ON d.criterion_rule_id=l.criterion_rule_id
                WHERE r.category='dose_caution'
                GROUP BY r.item_seq
            )
            SELECT COUNT(*) FROM per_product
            WHERE linked_rows=parsed_rows AND thresholds=1
        """
        quantitatively_evaluable_dose_products = con.execute(dose_evaluable_sql).fetchone()[0]
        active_quantitatively_evaluable_dose_products = con.execute(
            """WITH per_product AS (
                   SELECT r.item_seq,p.permit_status,COUNT(*) AS linked_rows,
                          SUM(CASE WHEN d.parse_status='parsed' THEN 1 ELSE 0 END) AS parsed_rows,
                          COUNT(DISTINCT CASE WHEN d.parse_status='parsed'
                              THEN d.maximum_daily_amount || char(0) || d.maximum_daily_unit END) AS thresholds
                   FROM product_criterion_links l
                   JOIN product_rules r ON r.id=l.product_rule_id
                   JOIN products p ON p.item_seq=r.item_seq
                   LEFT JOIN dose_criteria d ON d.criterion_rule_id=l.criterion_rule_id
                   WHERE r.category='dose_caution'
                   GROUP BY r.item_seq,p.permit_status
               )
               SELECT COUNT(*) FROM per_product
               WHERE permit_status='active' AND linked_rows=parsed_rows AND thresholds=1"""
        ).fetchone()[0]
        dur_ingredient_concepts = con.execute(
            "SELECT COUNT(*) FROM dur_ingredient_concepts"
        ).fetchone()[0]
        dur_concept_substances = con.execute(
            "SELECT COUNT(*) FROM dur_concept_substances"
        ).fetchone()[0]
        dur_product_signatures = con.execute(
            "SELECT COUNT(*) FROM dur_product_item_signatures"
        ).fetchone()[0]
        dur_criterion_signatures = con.execute(
            "SELECT COUNT(*) FROM dur_criterion_signatures"
        ).fetchone()[0]
        dur_criterion_pair_signatures = con.execute(
            "SELECT COUNT(*) FROM dur_criterion_pair_signatures"
        ).fetchone()[0]
        product_criterion_links = con.execute("SELECT COUNT(*) FROM product_criterion_links").fetchone()[0]
        linked_product_rules = con.execute(
            "SELECT COUNT(DISTINCT product_rule_id) FROM product_criterion_links"
        ).fetchone()[0]
        criterion_link_methods = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT match_method,COUNT(*) FROM product_criterion_links GROUP BY match_method ORDER BY match_method"
            )
        }
        criterion_link_coverage = [
            dict(row)
            for row in con.execute(
                """
                WITH linked AS (
                    SELECT product_rule_id
                    FROM product_criterion_links
                    GROUP BY product_rule_id
                )
                SELECT r.category,
                       COUNT(*) AS product_rules,
                       SUM(CASE WHEN l.product_rule_id IS NOT NULL THEN 1 ELSE 0 END) AS linked_product_rules
                FROM product_rules r
                LEFT JOIN linked l
                  ON l.product_rule_id=r.id
                GROUP BY r.category
                ORDER BY r.category
                """
            )
        ]
        missing_product_rule_identity = con.execute(
            "SELECT COUNT(*) FROM product_rules WHERE ingredient_code IS NULL OR ingredient_name_en IS NULL"
        ).fetchone()[0]
        missing_paired_rule_identity = con.execute(
            """
            SELECT COUNT(*) FROM product_rules
            WHERE paired_item_seq IS NOT NULL
              AND (paired_ingredient_code IS NULL OR paired_ingredient_name_en IS NULL)
            """
        ).fetchone()[0]
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
        "dose_criteria": dose_criteria,
        "dose_criteria_parsed": dose_criteria_parsed,
        "dose_criteria_not_evaluable": dose_criteria_not_evaluable,
        "linked_dose_products": linked_dose_products,
        "active_linked_dose_products": active_linked_dose_products,
        "quantitatively_evaluable_dose_products": quantitatively_evaluable_dose_products,
        "active_quantitatively_evaluable_dose_products": active_quantitatively_evaluable_dose_products,
        "dur_ingredient_concepts": dur_ingredient_concepts,
        "dur_concept_substances": dur_concept_substances,
        "dur_product_signatures": dur_product_signatures,
        "dur_criterion_signatures": dur_criterion_signatures,
        "dur_criterion_pair_signatures": dur_criterion_pair_signatures,
        "product_criterion_links": product_criterion_links,
        "linked_product_rules": linked_product_rules,
        "unlinked_product_rules": product_rules - linked_product_rules,
        "criterion_link_methods": criterion_link_methods,
        "criterion_link_coverage": criterion_link_coverage,
        "product_rules_missing_ingredient_identity": missing_product_rule_identity,
        "paired_product_rules_missing_ingredient_identity": missing_paired_rule_identity,
        "source_snapshots": source_snapshots,
        "source_families": source_families,
        "orphan_product_rules": orphan_rules,
        "orphan_paired_product_rules": orphan_pairs,
        "orphan_product_flags": orphan_flags,
        "categories": categories,
        "size_bytes": path.stat().st_size,
    }


def canonical_product_criteria(
    db_path: str | Path,
    item_seq: str,
    *,
    category: str | None = None,
    limit: int = 100,
) -> dict:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"canonical database not found: {path}")
    item_seq = str(item_seq or "").strip()
    if not item_seq:
        raise ValueError("item_seq is required")
    if limit < 1:
        raise ValueError("limit must be positive")
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        params: list[object] = [item_seq]
        where = "item_seq=?"
        if category:
            where += " AND category=?"
            params.append(category)
        params.append(limit)
        rows = [
            dict(row)
            for row in con.execute(
                f"""
                SELECT *
                FROM product_rule_criteria
                WHERE {where}
                ORDER BY category,product_source_dataset_key,product_source_row,
                         criterion_source_dataset_key,criterion_source_row
                LIMIT ?
                """,
                params,
            )
        ]
    return {
        "db_path": str(path),
        "item_seq": item_seq,
        "category": category,
        "limit": limit,
        "count": len(rows),
        "criteria": rows,
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
            source_rows = list(con.execute("SELECT dataset_key,source_family FROM source_snapshots"))
            actual_keys = {row[0] for row in source_rows}
            missing_keys = EXPECTED_CANONICAL_SOURCE_KEYS - actual_keys
            extra_keys = actual_keys - EXPECTED_CANONICAL_SOURCE_KEYS
            if missing_keys:
                errors.append("missing source snapshots: " + ", ".join(sorted(missing_keys)))
            if extra_keys:
                errors.append("unexpected source snapshots: " + ", ".join(sorted(extra_keys)))
            wrong_families = [
                f"{key}={family}"
                for key, family in source_rows
                if key in EXPECTED_CANONICAL_SOURCE_FAMILIES
                and EXPECTED_CANONICAL_SOURCE_FAMILIES[key] != family
            ]
            if wrong_families:
                errors.append("source snapshot family mismatch: " + ", ".join(sorted(wrong_families)))
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
            for category, qualifier_note, source_row in con.execute(
                """SELECT category,qualifier_note,source_row FROM ingredient_rules
                   WHERE source_dataset_key LIKE 'mfds_dur_ingredient:%'
                     AND qualifier_note IS NOT NULL AND TRIM(qualifier_note) != ''"""
            ):
                try:
                    reviewed_mfds_remark(category, qualifier_note)
                except ValueError as exc:
                    errors.append(f"{exc} (source row {source_row})")
            dose_rule_count = con.execute(
                "SELECT COUNT(*) FROM ingredient_rules WHERE category='dose_caution'"
            ).fetchone()[0]
            if stats["dose_criteria"] != dose_rule_count:
                errors.append(
                    f"dose criteria materialization mismatch: {stats['dose_criteria']}/{dose_rule_count}"
                )
            if dose_rule_count and stats["dose_criteria_parsed"] == 0:
                errors.append("no quantitative dose criteria are parsable")
            if stats["linked_dose_products"] and stats["quantitatively_evaluable_dose_products"] == 0:
                errors.append("linked dose-caution products have zero quantitative evaluability")
            if (
                stats["active_linked_dose_products"]
                and stats["active_quantitatively_evaluable_dose_products"] == 0
            ):
                errors.append("active linked dose-caution products have zero quantitative evaluability")
            if stats["dur_ingredient_concepts"] == 0:
                errors.append("no DUR ingredient concepts materialized")
            if stats["dur_product_signatures"] == 0:
                errors.append("no DUR product-side bridge signatures materialized")
            if (
                stats["dur_criterion_signatures"] == 0
                and stats["dur_criterion_pair_signatures"] == 0
            ):
                errors.append("no DUR criterion bridge signatures materialized")
            if stats["product_criterion_links"] == 0:
                errors.append("no product/ingredient criterion links materialized")
            foreign_key_errors = con.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_key_errors:
                errors.append(f"foreign key violations: {len(foreign_key_errors)}")
            category_mismatches = con.execute(
                """
                SELECT COUNT(*)
                FROM product_criterion_links l
                JOIN product_rules r ON r.id=l.product_rule_id
                JOIN ingredient_rules i ON i.id=l.criterion_rule_id
                WHERE r.category != i.category
                """
            ).fetchone()[0]
            if category_mismatches:
                errors.append(f"product/ingredient criterion category mismatches: {category_mismatches}")
            invalid_orientations = con.execute(
                """
                SELECT COUNT(*)
                FROM product_criterion_links l
                JOIN product_rules r ON r.id=l.product_rule_id
                WHERE (r.category='combination_contraindication' AND l.pair_orientation IS NULL)
                   OR (r.category!='combination_contraindication' AND l.pair_orientation IS NOT NULL)
                """
            ).fetchone()[0]
            if invalid_orientations:
                errors.append(f"invalid product/ingredient criterion pair orientations: {invalid_orientations}")
            if stats["orphan_product_rules"]:
                warnings.append(f"product rules with ITEM_SEQ absent from permit snapshot: {stats['orphan_product_rules']}")
            if stats["orphan_paired_product_rules"]:
                warnings.append(f"paired product rules absent from permit snapshot: {stats['orphan_paired_product_rules']}")
            if stats["orphan_product_flags"]:
                warnings.append(f"product flags with ITEM_SEQ absent from permit snapshot: {stats['orphan_product_flags']}")
            if stats["product_rules_missing_ingredient_identity"]:
                warnings.append(
                    "product rules missing MFDS ingredient code/English identity: "
                    f"{stats['product_rules_missing_ingredient_identity']}"
                )
            if stats["paired_product_rules_missing_ingredient_identity"]:
                warnings.append(
                    "paired product rules missing MFDS ingredient code/English identity: "
                    f"{stats['paired_product_rules_missing_ingredient_identity']}"
                )
    except sqlite3.DatabaseError as exc:
        errors.append(f"database error: {exc}")
    return {
        "db_path": str(path),
        "status": "verified" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
    }