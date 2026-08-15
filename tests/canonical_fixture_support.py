from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from medicine_canonical.dose_criteria import parse_daily_dose_threshold
from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_DATASET_KEY
from medicine_canonical.xlsx import XLSX_DATASETS


PERMIT_SOURCE = PERMIT_DATASET_KEY
DUR_SOURCE = "mfds_dur:getUsjntTabooInfoList03"
XLSX_SOURCE = "kids_mfds_xlsx:lactation_caution"


def expected_source_snapshots() -> list[tuple[str, str]]:
    return (
        [(PERMIT_DATASET_KEY, "mfds_permit_api")]
        + [(f"mfds_dur:{operation}", "mfds_dur_item_api") for operation in DUR_ENDPOINTS]
        + [(f"kids_mfds_xlsx:{category}", "kids_mfds_xlsx") for category in XLSX_DATASETS.values()]
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
            ("source_policy", "mfds_permit_api+mfds_dur_item_api+kids_mfds_xlsx"),
            ("unresolved_link_ambiguities", "[]"),
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
    criterion_note: str | None = None,
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
               paired_ingredient_name,rule_value,dosage_form,note,details,sequence_text
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (XLSX_SOURCE, criterion_source_row, category, ingredient, ingredient,
         paired_ingredient, rule_value or effect_name, criterion_dosage_form, criterion_note, details,
         str(criterion_source_row)),
    )
    criterion_rule_id = int(cur.lastrowid)
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
        (product_rule_id, criterion_rule_id, "english_exact",
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

def add_lactation(
    con: sqlite3.Connection,
    *,
    item_seq: str,
    ingredient: str,
    details: str = "수유부주의",
    unresolved: bool = False,
) -> int:
    source_row = con.execute("SELECT COUNT(*)+1 FROM ingredient_rules").fetchone()[0]
    cur = con.execute(
        """INSERT INTO ingredient_rules(
               source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
               details,sequence_text
           ) VALUES(?,?,'lactation_caution',?,?,?,?)""",
        (XLSX_SOURCE, source_row, ingredient, ingredient, details, str(source_row)),
    )
    criterion_id = int(cur.lastrowid)
    if unresolved:
        con.execute(
            """INSERT INTO product_ingredient_criterion_unresolved(
                   item_seq,criterion_rule_id,category,reason,evidence_json
               ) VALUES(?,?,'lactation_caution','scope_relation_unproven','{}')""",
            (item_seq, criterion_id),
        )
    else:
        con.execute(
            """INSERT INTO product_ingredient_criterion_links(
                   item_seq,criterion_rule_id,category,match_method,evidence_kind,evidence_json
               ) VALUES(?,?,'lactation_caution','precise_substance_exact','precise_substance_identity','{}')""",
            (item_seq, criterion_id),
        )
    return criterion_id


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
    "add_lactation", "add_flag", "PERMIT_SOURCE", "DUR_SOURCE", "XLSX_SOURCE",
]
