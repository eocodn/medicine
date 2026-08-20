from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path

from .inspection import verify_canonical_database
from .reference_contracts.v1 import (
    REFERENCE_CONTRACT_MAJOR,
    logical_dataset_id,
    materialize_reference_semantics,
    write_build_meta,
    write_contract_meta,
)


MOBILE_PHYSICAL_POLICY_VERSION = "8"
# Compatibility alias for server-side callers while the old name is retired.
# It is intentionally not part of dataset identity anymore.
MOBILE_DATA_POLICY_VERSION = MOBILE_PHYSICAL_POLICY_VERSION
RUNTIME_TABLES = (
    "canonical_meta", "source_snapshots", "products", "product_identifiers",
    "product_rules", "product_flags", "ingredient_rules", "dose_criteria", "product_criterion_links",
)
RUNTIME_VIEWS: tuple[str, ...] = ()
COPIED_RUNTIME_TABLES = (
    "canonical_meta", "source_snapshots", "products", "product_identifiers",
    "product_flags", "ingredient_rules", "dose_criteria",
)
# Mobile queries are intentionally narrower than canonical build/linking queries.
# Keep only indexes that serve runtime lookup paths; copying every canonical
# builder index adds hundreds of MB without helping on-device reads.
RUNTIME_INDEXES = (
    "idx_products_status",
    "idx_product_rules_runtime",
    "idx_product_flags_item_category",
)
MOBILE_RUNTIME_INDEX_DDL = {
    "idx_product_rules_runtime": (
        "CREATE INDEX idx_product_rules_runtime "
        "ON mobile_product_rules(item_seq, category_text_id, paired_item_seq)"
    ),
}

# Product rules dominate the mobile DB and repeat a small vocabulary of source,
# category, ingredient, form, detail and date strings hundreds of thousands of
# times. Keep ITEM_SEQ strings directly in the lookup table because they are the
# runtime identity and hot lookup keys, but dictionary-code the repeated payload.
# A compatibility view named product_rules restores the canonical row shape so
# the shared runtime SQL remains identical between canonical and mobile DBs.
MOBILE_RULE_SOURCES_DDL = """CREATE TABLE mobile_rule_sources (
    id INTEGER PRIMARY KEY,
    dataset_key TEXT NOT NULL UNIQUE REFERENCES source_snapshots(dataset_key)
)"""
MOBILE_RULE_TEXTS_DDL = """CREATE TABLE mobile_rule_texts (
    id INTEGER PRIMARY KEY,
    value TEXT NOT NULL UNIQUE
)"""
MOBILE_PRODUCT_RULES_DDL = """CREATE TABLE mobile_product_rules (
    id INTEGER PRIMARY KEY,
    source_dataset_id INTEGER NOT NULL REFERENCES mobile_rule_sources(id),
    source_row INTEGER NOT NULL,
    category_text_id INTEGER NOT NULL REFERENCES mobile_rule_texts(id),
    item_seq TEXT NOT NULL,
    ingredient_code_text_id INTEGER REFERENCES mobile_rule_texts(id),
    ingredient_name_text_id INTEGER REFERENCES mobile_rule_texts(id),
    ingredient_name_en_text_id INTEGER REFERENCES mobile_rule_texts(id),
    paired_item_seq TEXT,
    paired_ingredient_code_text_id INTEGER REFERENCES mobile_rule_texts(id),
    paired_ingredient_name_text_id INTEGER REFERENCES mobile_rule_texts(id),
    paired_ingredient_name_en_text_id INTEGER REFERENCES mobile_rule_texts(id),
    effect_name_text_id INTEGER REFERENCES mobile_rule_texts(id),
    dosage_form_text_id INTEGER REFERENCES mobile_rule_texts(id),
    details_text_id INTEGER REFERENCES mobile_rule_texts(id),
    notification_date_text_id INTEGER REFERENCES mobile_rule_texts(id),
    change_date_text_id INTEGER REFERENCES mobile_rule_texts(id)
)"""
MOBILE_PRODUCT_CRITERION_LINKS_DDL = """CREATE TABLE mobile_product_criterion_links (
    product_rule_id INTEGER NOT NULL REFERENCES mobile_product_rules(id),
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    match_method_code INTEGER NOT NULL CHECK(match_method_code BETWEEN 0 AND 3),
    pair_orientation_code INTEGER CHECK(pair_orientation_code IN (0,1) OR pair_orientation_code IS NULL),
    PRIMARY KEY(product_rule_id, criterion_rule_id)
) WITHOUT ROWID"""
MOBILE_PRODUCT_CRITERION_LINKS_VIEW_DDL = """CREATE VIEW product_criterion_links AS
SELECT
    product_rule_id,
    criterion_rule_id,
    CASE match_method_code
        WHEN 0 THEN 'mfds_ingredient_code'
        WHEN 1 THEN 'permit_composition'
        WHEN 2 THEN 'mfds_details_exact'
        WHEN 3 THEN 'mfds_unanimous_value'
    END AS match_method,
    CASE pair_orientation_code
        WHEN 0 THEN 'forward'
        WHEN 1 THEN 'reverse'
        ELSE NULL
    END AS pair_orientation
FROM mobile_product_criterion_links"""
MOBILE_PRODUCT_RULES_VIEW_DDL = """CREATE VIEW product_rules AS
SELECT
    r.id,
    src.dataset_key AS source_dataset_key,
    r.source_row,
    category.value AS category,
    r.item_seq,
    ingredient_code.value AS ingredient_code,
    ingredient_name.value AS ingredient_name,
    ingredient_name_en.value AS ingredient_name_en,
    r.paired_item_seq,
    paired_ingredient_code.value AS paired_ingredient_code,
    paired_ingredient_name.value AS paired_ingredient_name,
    paired_ingredient_name_en.value AS paired_ingredient_name_en,
    effect_name.value AS effect_name,
    dosage_form.value AS dosage_form,
    details.value AS details,
    notification_date.value AS notification_date,
    change_date.value AS change_date
FROM mobile_product_rules r
JOIN mobile_rule_sources src ON src.id=r.source_dataset_id
JOIN mobile_rule_texts category ON category.id=r.category_text_id
LEFT JOIN mobile_rule_texts ingredient_code ON ingredient_code.id=r.ingredient_code_text_id
LEFT JOIN mobile_rule_texts ingredient_name ON ingredient_name.id=r.ingredient_name_text_id
LEFT JOIN mobile_rule_texts ingredient_name_en ON ingredient_name_en.id=r.ingredient_name_en_text_id
LEFT JOIN mobile_rule_texts paired_ingredient_code ON paired_ingredient_code.id=r.paired_ingredient_code_text_id
LEFT JOIN mobile_rule_texts paired_ingredient_name ON paired_ingredient_name.id=r.paired_ingredient_name_text_id
LEFT JOIN mobile_rule_texts paired_ingredient_name_en ON paired_ingredient_name_en.id=r.paired_ingredient_name_en_text_id
LEFT JOIN mobile_rule_texts effect_name ON effect_name.id=r.effect_name_text_id
LEFT JOIN mobile_rule_texts dosage_form ON dosage_form.id=r.dosage_form_text_id
LEFT JOIN mobile_rule_texts details ON details.id=r.details_text_id
LEFT JOIN mobile_rule_texts notification_date ON notification_date.id=r.notification_date_text_id
LEFT JOIN mobile_rule_texts change_date ON change_date.id=r.change_date_text_id"""
MOBILE_PRODUCT_RULE_CRITERIA_VIEW_DDL = """CREATE VIEW product_rule_criteria AS
SELECT
    i.id AS criterion_rule_id,
    r.source_dataset_key AS product_source_dataset_key,
    r.source_row AS product_source_row,
    i.source_dataset_key AS criterion_source_dataset_key,
    i.source_row AS criterion_source_row,
    r.category,
    r.item_seq,
    r.ingredient_code,
    r.ingredient_name,
    r.ingredient_name_en,
    r.paired_item_seq,
    r.paired_ingredient_code,
    r.paired_ingredient_name,
    r.paired_ingredient_name_en,
    r.effect_name,
    r.dosage_form AS product_dosage_form,
    r.details AS product_details,
    i.sequence_text AS criterion_sequence_text,
    i.ingredient_name AS criterion_ingredient_name,
    i.ingredient_name_ko AS criterion_ingredient_name_ko,
    i.paired_ingredient_name AS criterion_paired_ingredient_name,
    i.rule_value AS criterion_rule_value,
    i.dosage_form AS criterion_dosage_form,
    i.note AS criterion_note,
    i.qualifier_note AS criterion_qualifier_note,
    i.details AS criterion_details,
    d.maximum_daily_amount AS criterion_maximum_daily_amount,
    d.maximum_daily_unit AS criterion_maximum_daily_unit,
    d.parse_status AS criterion_dose_parse_status,
    d.parse_reason AS criterion_dose_parse_reason,
    l.match_method,
    l.pair_orientation
FROM product_criterion_links l
JOIN product_rules r ON r.id = l.product_rule_id
JOIN ingredient_rules i ON i.id = l.criterion_rule_id
LEFT JOIN dose_criteria d ON d.criterion_rule_id = i.id"""
MOBILE_RULE_TEXT_COLUMNS = (
    "category", "ingredient_code", "ingredient_name", "ingredient_name_en",
    "paired_ingredient_code", "paired_ingredient_name", "paired_ingredient_name_en",
    "effect_name", "dosage_form", "details", "notification_date", "change_date",
)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _assert_product_rule_source_identity_unique(con: sqlite3.Connection) -> None:
    duplicate = con.execute(
        """SELECT source_dataset_key,source_row,COUNT(*) AS row_count
           FROM product_rules
           GROUP BY source_dataset_key,source_row
           HAVING COUNT(*) > 1
           LIMIT 1"""
    ).fetchone()
    if duplicate:
        raise ValueError(
            "product_rules source identity is not unique: "
            f"{duplicate[0]} row {duplicate[1]} appears {duplicate[2]} times"
        )


def _populate_compact_product_rules(dst: sqlite3.Connection) -> None:
    dst.execute(MOBILE_RULE_SOURCES_DDL)
    dst.execute(MOBILE_RULE_TEXTS_DDL)
    dst.execute(MOBILE_PRODUCT_RULES_DDL)
    dst.execute(MOBILE_PRODUCT_CRITERION_LINKS_DDL)
    dst.execute(
        """INSERT INTO mobile_rule_sources(dataset_key)
           SELECT DISTINCT source_dataset_key
           FROM source_db.product_rules
           ORDER BY source_dataset_key"""
    )
    text_union = " UNION ".join(
        f"SELECT {column} AS value FROM source_db.product_rules WHERE {column} IS NOT NULL"
        for column in MOBILE_RULE_TEXT_COLUMNS
    )
    dst.execute(
        f"INSERT INTO mobile_rule_texts(value) "
        f"SELECT DISTINCT value FROM ({text_union}) ORDER BY value"
    )
    text_id = lambda column: f"(SELECT id FROM mobile_rule_texts WHERE value=s.{column})"
    dst.execute(
        f"""INSERT INTO mobile_product_rules(
               id,source_dataset_id,source_row,category_text_id,item_seq,
               ingredient_code_text_id,ingredient_name_text_id,ingredient_name_en_text_id,
               paired_item_seq,paired_ingredient_code_text_id,paired_ingredient_name_text_id,
               paired_ingredient_name_en_text_id,effect_name_text_id,dosage_form_text_id,
               details_text_id,notification_date_text_id,change_date_text_id
           )
           SELECT
               s.id,
               (SELECT id FROM mobile_rule_sources WHERE dataset_key=s.source_dataset_key),
               s.source_row,{text_id('category')},s.item_seq,
               {text_id('ingredient_code')},{text_id('ingredient_name')},{text_id('ingredient_name_en')},
               s.paired_item_seq,{text_id('paired_ingredient_code')},{text_id('paired_ingredient_name')},
               {text_id('paired_ingredient_name_en')},{text_id('effect_name')},{text_id('dosage_form')},
               {text_id('details')},{text_id('notification_date')},{text_id('change_date')}
           FROM source_db.product_rules s"""
    )
    dst.execute(
        """INSERT INTO mobile_product_criterion_links(
               product_rule_id,criterion_rule_id,match_method_code,pair_orientation_code
           )
           SELECT
               product_rule_id,
               criterion_rule_id,
               CASE match_method
                   WHEN 'mfds_ingredient_code' THEN 0
                   WHEN 'permit_composition' THEN 1
                   WHEN 'mfds_details_exact' THEN 2
                   WHEN 'mfds_unanimous_value' THEN 3
                   ELSE 99
               END,
               CASE
                   WHEN pair_orientation IS NULL THEN NULL
                   WHEN pair_orientation='forward' THEN 0
                   WHEN pair_orientation='reverse' THEN 1
                   ELSE 99
               END
           FROM source_db.product_criterion_links
           ORDER BY product_rule_id,criterion_rule_id"""
    )
    dst.execute(MOBILE_PRODUCT_RULES_VIEW_DDL)
    dst.execute(MOBILE_PRODUCT_CRITERION_LINKS_VIEW_DDL)


def build_mobile_database(
    canonical_db: str | Path,
    output_db: str | Path,
    *,
    manifest_path: str | Path | None = None,
    physical_policy_version: str = MOBILE_PHYSICAL_POLICY_VERSION,
) -> dict:
    source = Path(canonical_db)
    output = Path(output_db)
    manifest = Path(manifest_path) if manifest_path else output.with_name("mobile.manifest.json")
    if not source.is_file():
        raise FileNotFoundError(f"canonical database not found: {source}")
    verification = verify_canonical_database(source)
    if verification["status"] != "verified":
        details = "; ".join(verification["errors"]) or "unknown verification failure"
        raise ValueError(f"canonical verification failed: {details}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)

    physical_policy_version = str(physical_policy_version).strip()
    if not physical_policy_version:
        raise ValueError("mobile physical policy version is required")

    dataset_id: str | None = None
    src = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    try:
        schema_version = src.execute(
            "SELECT value FROM canonical_meta WHERE key='schema_version'"
        ).fetchone()
        build_stage = src.execute(
            "SELECT value FROM canonical_meta WHERE key='build_stage'"
        ).fetchone()
        if not schema_version or not build_stage or build_stage[0] != "complete":
            raise ValueError("reference exporter requires a complete canonical database")
        _assert_product_rule_source_identity_unique(src)
        objects = {
            (kind, name): sql
            for kind, name, sql in src.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }

        dst = sqlite3.connect(temporary)
        try:
            dst.execute("PRAGMA foreign_keys=OFF")
            for table in COPIED_RUNTIME_TABLES:
                ddl = objects.get(("table", table))
                if not ddl:
                    raise ValueError(f"canonical runtime table missing: {table}")
                dst.execute(ddl)
            escaped = str(source.resolve()).replace("'", "''")
            dst.execute(f"ATTACH DATABASE '{escaped}' AS source_db")
            for table in COPIED_RUNTIME_TABLES:
                dst.execute(f'INSERT INTO "{table}" SELECT * FROM source_db."{table}"')
            _populate_compact_product_rules(dst)
            materialize_reference_semantics(src, dst)
            dst.commit()
            dst.execute("DETACH DATABASE source_db")
            source_indexes = {
                name: sql
                for name, sql in src.execute(
                    "SELECT name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
                )
            }
            missing_indexes = [
                name
                for name in RUNTIME_INDEXES
                if name not in MOBILE_RUNTIME_INDEX_DDL and name not in source_indexes
            ]
            if missing_indexes:
                raise ValueError(
                    f"canonical runtime index missing: {', '.join(missing_indexes)}"
                )
            for name in RUNTIME_INDEXES:
                dst.execute(MOBILE_RUNTIME_INDEX_DDL.get(name, source_indexes.get(name)))
            for view in RUNTIME_VIEWS:
                ddl = objects.get(("view", view))
                if not ddl:
                    raise ValueError(f"canonical runtime view missing: {view}")
                dst.execute(ddl)
            dst.execute(MOBILE_PRODUCT_RULE_CRITERIA_VIEW_DDL)
            dataset_id = logical_dataset_id(dst)
            write_contract_meta(dst, dataset_id)
            write_build_meta(
                dst,
                canonical_schema_version=str(schema_version[0]),
                physical_policy_version=physical_policy_version,
            )
            dst.commit()
            # Bulk loading millions of WITHOUT ROWID links leaves measurable
            # page slack even in a freshly created database. Compact once on
            # the build host so the installed artifact does not carry it.
            dst.execute("VACUUM")
            dst.execute("PRAGMA foreign_keys=ON")
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("mobile canonical integrity check failed")
            fk_errors = dst.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise RuntimeError(f"mobile canonical foreign key violations: {len(fk_errors)}")
            dst.execute("ANALYZE")
            dst.execute("PRAGMA optimize")
            dst.commit()
        finally:
            dst.close()
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        src.close()

    if dataset_id is None:
        raise RuntimeError("reference contract dataset identity was not materialized")
    payload = {
        "contract_major": REFERENCE_CONTRACT_MAJOR,
        "dataset_id": dataset_id,
        "sha256": _sha256(output),
        "size_bytes": output.stat().st_size,
        "canonical_schema_version": str(schema_version[0]),
        "physical_policy_version": physical_policy_version,
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"db_path": str(output), "manifest_path": str(manifest), **payload}


__all__ = [
    "MOBILE_PHYSICAL_POLICY_VERSION",
    "REFERENCE_CONTRACT_MAJOR",
    "RUNTIME_INDEXES",
    "build_mobile_database",
]
