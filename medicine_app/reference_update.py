from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


REFERENCE_CONTRACT_MAJOR = 1
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_REQUIRED_CONTRACT_COLUMNS: dict[str, frozenset[str]] = {
    "reference_contract_meta": frozenset({"key", "value"}),
    "canonical_meta": frozenset({"key", "value"}),
    "source_snapshots": frozenset({
        "dataset_key", "source_family", "sha256", "row_count", "fetched_at", "effective_date",
    }),
    "products": frozenset({"item_seq", "product_name", "dosage_form", "permit_status"}),
    "product_identifiers": frozenset({"item_seq", "system", "value"}),
    "product_flags": frozenset({"item_seq", "category", "flag_code", "flag_name", "details"}),
    "ingredient_rules": frozenset({
        "id", "category", "sequence_text", "ingredient_name", "paired_ingredient_name",
        "rule_value", "dosage_form", "note", "qualifier_note", "details",
    }),
    "dose_criteria": frozenset({
        "criterion_rule_id", "maximum_daily_amount", "maximum_daily_unit", "parse_status", "parse_reason",
    }),
    "product_rules": frozenset({
        "id", "category", "item_seq", "paired_item_seq", "effect_name", "dosage_form", "details",
    }),
    "product_criterion_links": frozenset({
        "product_rule_id", "criterion_rule_id", "match_method", "pair_orientation",
    }),
    "product_rule_criteria": frozenset({
        "criterion_rule_id", "category", "item_seq", "paired_item_seq",
        "product_dosage_form", "product_details", "criterion_rule_value",
        "criterion_dosage_form", "criterion_qualifier_note", "criterion_details",
        "criterion_maximum_daily_amount", "criterion_maximum_daily_unit",
        "criterion_dose_parse_status", "criterion_dose_parse_reason", "match_method",
    }),
    "reference_criterion_semantics": frozenset({
        "criterion_rule_id", "ordinal", "semantic_role", "evaluation_mode", "evaluator_kind",
        "fallback_action", "qualifier_type", "display_text", "structured_payload_json", "source_remark",
    }),
}


def _normalized_contract_major(value: object) -> int:
    text = str(value).strip()
    if not text.isdigit() or text.startswith("0"):
        raise ValueError("invalid expected reference contract major")
    major = int(text)
    if major <= 0:
        raise ValueError("invalid expected reference contract major")
    return major


def _verify_contract_schema(con: sqlite3.Connection) -> None:
    objects = {
        str(name)
        for name, kind in con.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')"
        )
        if kind in {"table", "view"}
    }
    for name, required_columns in _REQUIRED_CONTRACT_COLUMNS.items():
        if name not in objects:
            raise ValueError(f"reference contract schema is missing object: {name}")
        columns = {str(row[1]) for row in con.execute(f'PRAGMA table_info("{name}")')}
        missing = sorted(required_columns - columns)
        if missing:
            raise ValueError(
                f"reference contract schema object {name} is missing columns: {', '.join(missing)}"
            )


def verify_reference_database(
    database: str | Path,
    expected_contract_major: int | str,
    expected_dataset_id: str,
) -> dict:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"reference database not found: {path}")
    contract_major = _normalized_contract_major(expected_contract_major)
    expected_dataset_id = str(expected_dataset_id).lower()
    if not _DATASET_ID.fullmatch(expected_dataset_id):
        raise ValueError("invalid expected reference dataset identity")

    uri = f"file:{path.resolve()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as con:
        con.execute("PRAGMA query_only = ON")
        integrity = con.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("reference SQLite integrity check failed")
        foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError(f"reference SQLite foreign key check failed: {len(foreign_keys)} violations")

        _verify_contract_schema(con)

        try:
            meta = {
                str(key): str(value)
                for key, value in con.execute("SELECT key,value FROM reference_contract_meta")
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError("reference contract metadata is missing or invalid") from exc
        actual_major = _normalized_contract_major(meta.get("contract_major", ""))
        if actual_major != contract_major:
            raise ValueError("reference contract major does not match release")
        dataset_id = str(meta.get("dataset_id") or "").lower()
        if not _DATASET_ID.fullmatch(dataset_id):
            raise ValueError("reference contract dataset identity is invalid")
        if dataset_id != expected_dataset_id:
            raise ValueError("reference dataset identity does not match release")

    return {
        "status": "verified",
        "dataset_id": dataset_id,
        "contract_major": actual_major,
        "size_bytes": path.stat().st_size,
    }


__all__ = ["REFERENCE_CONTRACT_MAJOR", "verify_reference_database"]