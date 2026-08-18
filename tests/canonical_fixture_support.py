from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION as MFDS_INGREDIENT_ENDPOINTS,
)

from medicine_canonical.dose_criteria import parse_daily_dose_threshold
from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.source_policy import CANONICAL_SOURCE_POLICY
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_DATASET_KEY


PERMIT_SOURCE = PERMIT_DATASET_KEY
DUR_SOURCE = "mfds_dur:getUsjntTabooInfoList03"
CRITERION_SOURCE_BY_CATEGORY = {
    spec.category: f"mfds_dur_ingredient:{operation}"
    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items()
}


def expected_source_snapshots() -> list[tuple[str, str]]:
    return (
        [(PERMIT_DATASET_KEY, "mfds_permit_api")]
        + [(f"mfds_dur:{operation}", "mfds_dur_item_api") for operation in DUR_ENDPOINTS]
        + [(f"mfds_dur_ingredient:{operation}", "mfds_dur_ingredient_api") for operation in MFDS_INGREDIENT_ENDPOINTS]
    )


def create_canonical_fixture(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for ordinal, (key, family) in enumerate(expected_source_snapshots(), 1):
        con.execute(
            """INSERT INTO source_snapshots(
                   dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
               ) VALUES(?,?,?,?,?,?,?)""",
            (key, family, key, key, 1, f"{ordinal:064x}", "{}"),
        )
    con.executemany(
        "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
        [
            ("schema_version", SCHEMA_VERSION),
            ("build_stage", "complete"),
            ("built_at", "2026-08-13T15:00:00+09:00"),
            ("source_policy", CANONICAL_SOURCE_POLICY),
        ],
    )
    return con


def add_product(
    con: sqlite3.Connection,
    item_seq: str,
    name: str,
    ingredient: str,
    *,
    manufacturer: str = "제약",
    dosage_form: str | None = "정제",
    permit_status: str = "active",
    permit_date: str = "2020-01-01",
    cancel_date: str | None = None,
    cancel_name: str | None = "정상",
    edi: str | None = None,
) -> None:
    source_row = con.execute("SELECT COUNT(*)+1 FROM products").fetchone()[0]
    con.execute(
        """INSERT INTO products(
               item_seq,source_row,product_name,manufacturer,ingredient_text,dosage_form,
               permit_date,cancel_date,cancel_name,permit_status,source_dataset_key
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (item_seq, source_row, name, manufacturer, ingredient, dosage_form,
         permit_date, cancel_date, cancel_name, permit_status, PERMIT_SOURCE),
    )
    con.execute(
        "INSERT INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
        (item_seq, "MFDS_ITEM_SEQ", item_seq, PERMIT_SOURCE),
    )
    if edi:
        con.execute(
            "INSERT INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
            (item_seq, "EDI", edi, PERMIT_SOURCE),
        )

def add_linked_rule(
    con: sqlite3.Connection,
    *,
    category: str,
    item_seq: str,
    ingredient: str,
    rule_value: str | None = None,
    details: str | None = None,
    paired_item_seq: str | None = None,
    paired_ingredient: str | None = None,
    dosage_form: str | None = None,
    product_dosage_form: str | None = None,
    criterion_dosage_form: str | None = None,
    effect_name: str | None = None,
    criterion_qualifier_note: str | None = None,
) -> tuple[int, int]:
    product_dosage_form = dosage_form if product_dosage_form is None else product_dosage_form
    criterion_dosage_form = dosage_form if criterion_dosage_form is None else criterion_dosage_form
    product_source_row = con.execute("SELECT COUNT(*)+1 FROM product_rules").fetchone()[0]
    ingredient_code = f"D-{item_seq}"
    paired_code = f"D-{paired_item_seq}" if paired_item_seq else None
    cur = con.execute(
        """INSERT INTO product_rules(
               source_dataset_key,source_row,category,item_seq,ingredient_code,
               ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
               paired_ingredient_name,paired_ingredient_name_en,effect_name,dosage_form,details
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, product_source_row, category, item_seq, ingredient_code,
         ingredient, ingredient, paired_item_seq, paired_code,
         paired_ingredient, paired_ingredient, effect_name, product_dosage_form, details),
    )
    product_rule_id = int(cur.lastrowid)
    criterion_source_row = con.execute("SELECT COUNT(*)+1 FROM ingredient_rules").fetchone()[0]
    cur = con.execute(
        """INSERT INTO ingredient_rules(
               source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
               paired_ingredient_name,rule_value,dosage_form,qualifier_note,details,sequence_text
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (CRITERION_SOURCE_BY_CATEGORY[category], criterion_source_row, category, ingredient, ingredient,
         paired_ingredient, rule_value or effect_name, criterion_dosage_form, criterion_qualifier_note, details,
         str(criterion_source_row)),
    )
    criterion_rule_id = int(cur.lastrowid)
    con.execute(
        """INSERT INTO ingredient_rule_codes(
               criterion_rule_id,ingredient_code,paired_ingredient_code,
               mixture_type,mixture_ingredient_codes_json,mixture_ingredient_names_json
           ) VALUES(?,?,?,?,?,?)""",
        (
            criterion_rule_id,
            ingredient_code,
            paired_code,
            None if category == "combination_contraindication" else "단일",
            "[]",
            "[]",
        ),
    )
    if category == "dose_caution":
        amount, unit, status, reason = parse_daily_dose_threshold(rule_value or effect_name)
        con.execute(
            """INSERT INTO dose_criteria(
                   criterion_rule_id,maximum_daily_amount,maximum_daily_unit,parse_status,parse_reason
               ) VALUES(?,?,?,?,?)""",
            (criterion_rule_id, amount, unit, status, reason),
        )
    con.execute(
        """INSERT INTO product_criterion_links(
               product_rule_id,criterion_rule_id,match_method,pair_orientation
           ) VALUES(?,?,?,?)""",
        (product_rule_id, criterion_rule_id, "mfds_ingredient_code",
         "forward" if paired_item_seq else None),
    )
    return product_rule_id, criterion_rule_id


def add_unlinked_rule(
    con: sqlite3.Connection,
    *,
    category: str,
    item_seq: str,
    ingredient: str,
    paired_item_seq: str | None = None,
    paired_ingredient: str | None = None,
    details: str | None = None,
) -> int:
    source_row = con.execute("SELECT COUNT(*)+1 FROM product_rules").fetchone()[0]
    cur = con.execute(
        """INSERT INTO product_rules(
               source_dataset_key,source_row,category,item_seq,ingredient_code,
               ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
               paired_ingredient_name,paired_ingredient_name_en,details
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, source_row, category, item_seq, f"D-{item_seq}", ingredient, ingredient,
         paired_item_seq, f"D-{paired_item_seq}" if paired_item_seq else None,
         paired_ingredient, paired_ingredient, details),
    )
    return int(cur.lastrowid)

def add_flag(
    con: sqlite3.Connection,
    *,
    item_seq: str,
    category: str,
    details: str | None = None,
) -> None:
    source_row = con.execute("SELECT COUNT(*)+1 FROM product_flags").fetchone()[0]
    con.execute(
        """INSERT INTO product_flags(
               source_dataset_key,source_row,flag_ordinal,item_seq,category,flag_code,flag_name,details
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, source_row, 0, item_seq, category, category, category, details),
    )


__all__ = [
    "create_canonical_fixture", "add_product", "add_linked_rule", "add_unlinked_rule",
    "add_flag", "PERMIT_SOURCE", "DUR_SOURCE", "CRITERION_SOURCE_BY_CATEGORY",
]
