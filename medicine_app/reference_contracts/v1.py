from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Mapping
from contextlib import closing
from pathlib import Path
from typing import Any


REFERENCE_CONTRACT_MAJOR = 1
_REQUIRED_PHYSICAL_POLICY_VERSION = "9"
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
_SUPPORTED_EXCLUDED_ROUTES = frozenset({
    "oral", "injection", "ophthalmic", "otic", "nasal", "inhaled", "topical",
})

# This is the public logical surface consumed by the contract-v1 APK runtime.
# Do not add canonical provenance or physical-storage-only columns here: doing
# so would turn a server-side storage change into an app compatibility break.
REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "reference_contract_meta": frozenset({"key", "value"}),
    "canonical_meta": frozenset({"key", "value"}),
    "source_snapshots": frozenset({
        "dataset_key", "source_family", "effective_date", "fetched_at", "row_count", "sha256",
    }),
    "products": frozenset({
        "item_seq", "product_name", "manufacturer", "ingredient_text", "dosage_form",
        "permit_date", "cancel_date", "cancel_name", "permit_status",
    }),
    "product_identifiers": frozenset({"item_seq", "system", "value"}),
    "product_flags": frozenset({
        "item_seq", "category", "flag_code", "flag_name", "ingredient_name", "dosage_form",
        "details", "change_date", "source_dataset_key", "source_row", "flag_ordinal",
    }),
    "product_rules": frozenset({
        "id", "source_dataset_key", "source_row", "category", "item_seq", "paired_item_seq",
        "effect_name", "dosage_form", "details",
    }),
    "product_criterion_links": frozenset({"product_rule_id", "criterion_rule_id"}),
    "product_rule_criteria": frozenset({
        "criterion_rule_id", "product_source_dataset_key", "product_source_row",
        "criterion_source_dataset_key", "criterion_source_row", "category", "item_seq",
        "ingredient_name", "paired_item_seq", "paired_ingredient_name", "effect_name",
        "product_dosage_form", "product_details", "criterion_ingredient_name",
        "criterion_paired_ingredient_name", "criterion_rule_value", "criterion_dosage_form",
        "criterion_note", "criterion_qualifier_note", "criterion_details",
        "criterion_maximum_daily_amount", "criterion_maximum_daily_unit",
        "criterion_dose_parse_status", "criterion_dose_parse_reason", "match_method",
    }),
    "reference_semantic_expectations": frozenset({
        "criterion_rule_id", "expected_fact_count",
    }),
    "reference_criterion_semantics": frozenset({
        "criterion_rule_id", "ordinal", "semantic_role", "evaluation_mode", "evaluator_kind",
        "fallback_action", "qualifier_type", "display_text", "structured_payload_json",
        "source_remark",
    }),
}


def normalized_contract_major(value: object) -> int:
    text = str(value).strip()
    if not text.isdigit() or text.startswith("0"):
        raise ValueError("invalid expected reference contract major")
    major = int(text)
    if major <= 0:
        raise ValueError("invalid expected reference contract major")
    return major


def _semantic_payload(record: Mapping[str, Any]) -> dict[str, Any]:
    if "structured_payload" in record:
        payload = record.get("structured_payload")
    else:
        raw = record.get("structured_payload_json")
        try:
            payload = json.loads(str(raw))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValueError("reference semantic payload is invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("reference semantic payload must be an object")
    return dict(payload)


def normalized_semantic_record(record: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one contract-v1 semantic row and return parsed payload data.

    Unknown future runtime evaluators are permitted only through the explicit
    review-required compatibility path. Known evaluator payloads are strict so
    malformed values can never become permissive runtime defaults.
    """
    role = str(record.get("semantic_role") or "")
    mode = str(record.get("evaluation_mode") or "")
    evaluator = str(record.get("evaluator_kind") or "")
    fallback = str(record.get("fallback_action") or "")
    payload = _semantic_payload(record)

    expected: tuple[str, str, str] | None = None
    if evaluator == "display_only":
        expected = ("informational", "resolved_at_build", "none")
    elif evaluator == "opaque_condition":
        expected = ("applicability_condition", "review_required", "review_required")
    elif evaluator == "minimum_separation":
        expected = ("applicability_condition", "runtime_evaluable", "review_required")
        hours = payload.get("hours")
        if not isinstance(hours, int) or isinstance(hours, bool) or hours <= 0:
            raise ValueError("minimum_separation semantic payload requires positive integer hours")
        if payload.get("direction") != "symmetric":
            raise ValueError("minimum_separation semantic payload requires symmetric direction")
    elif evaluator == "excluded_route":
        expected = ("applicability_condition", "runtime_evaluable", "review_required")
        if payload.get("route") not in _SUPPORTED_EXCLUDED_ROUTES:
            raise ValueError("excluded_route semantic payload requires supported route")
    elif not (
        role == "applicability_condition"
        and mode == "runtime_evaluable"
        and fallback == "review_required"
    ):
        raise ValueError("unknown reference semantic evaluator lacks conservative fallback")

    if expected is not None and (role, mode, fallback) != expected:
        raise ValueError(f"reference semantic evaluator {evaluator} has invalid mode/fallback")
    return {
        "semantic_role": role,
        "evaluation_mode": mode,
        "evaluator_kind": evaluator,
        "fallback_action": fallback,
        "qualifier_type": record.get("qualifier_type"),
        "display_text": record.get("display_text"),
        "structured_payload": payload,
        "source_remark": record.get("source_remark"),
    }


def _verify_schema(con: sqlite3.Connection) -> None:
    objects = {
        str(name)
        for name, kind in con.execute(
            "SELECT name,type FROM sqlite_master WHERE type IN ('table','view')"
        )
        if kind in {"table", "view"}
    }
    for name, required_columns in REQUIRED_COLUMNS.items():
        if name not in objects:
            raise ValueError(f"reference contract schema is missing object: {name}")
        columns = {str(row[1]) for row in con.execute(f'PRAGMA table_info("{name}")')}
        missing = sorted(required_columns - columns)
        if missing:
            raise ValueError(
                f"reference contract schema object {name} is missing columns: {', '.join(missing)}"
            )
        # Force SQLite to resolve views and their backing objects now rather than
        # accepting a syntactically present but unusable logical contract view.
        projection = ",".join(f'"{column}"' for column in sorted(required_columns))
        try:
            con.execute(f'SELECT {projection} FROM "{name}" LIMIT 0').fetchall()
        except sqlite3.DatabaseError as exc:
            raise ValueError(f"reference contract schema object {name} is not queryable") from exc


def _verify_product_search_index(con: sqlite3.Connection) -> None:
    try:
        build_meta = {
            str(key): str(value)
            for key, value in con.execute("SELECT key,value FROM reference_build_meta")
        }
    except sqlite3.DatabaseError as exc:
        raise ValueError("reference physical build metadata is missing or invalid") from exc
    if build_meta.get("physical_policy_version") != _REQUIRED_PHYSICAL_POLICY_VERSION:
        raise ValueError("reference physical policy is unsupported by this runtime")
    definitions = {
        name: sql
        for name, sql in con.execute(
            """SELECT name,sql FROM sqlite_master
               WHERE type='table' AND name IN ('product_search_fts','product_search_ocr_fts')"""
        )
    }
    primary = str(definitions.get("product_search_fts") or "").lower()
    ocr = str(definitions.get("product_search_ocr_fts") or "").lower()
    if "fts5" not in primary or "trigram" not in primary:
        raise ValueError("reference product search index is missing or invalid")
    if "fts5" not in ocr or "unicode61" not in ocr:
        raise ValueError("reference OCR product search index is missing or invalid")
    try:
        products = con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        indexed = con.execute("SELECT COUNT(DISTINCT item_seq) FROM product_search_fts").fetchone()[0]
        ocr_indexed = con.execute(
            "SELECT COUNT(DISTINCT item_seq) FROM product_search_ocr_fts"
        ).fetchone()[0]
        con.execute(
            "SELECT item_seq FROM product_search_fts WHERE product_search_fts MATCH ? LIMIT 1",
            ('"abc"',),
        ).fetchall()
        con.execute(
            "SELECT item_seq FROM product_search_ocr_fts WHERE product_search_ocr_fts MATCH ? LIMIT 1",
            ('"ab"',),
        ).fetchall()
    except sqlite3.DatabaseError as exc:
        raise ValueError("reference product search index is not queryable") from exc
    if indexed != products or ocr_indexed != products:
        raise ValueError("reference product search index coverage does not match products")


def _verify_semantics(con: sqlite3.Connection) -> None:
    con.row_factory = sqlite3.Row
    expectations = {
        int(row["criterion_rule_id"]): int(row["expected_fact_count"])
        for row in con.execute(
            """SELECT criterion_rule_id,expected_fact_count
               FROM reference_semantic_expectations ORDER BY criterion_rule_id"""
        ).fetchall()
    }
    if any(count <= 0 for count in expectations.values()):
        raise ValueError("reference semantic expectation count is invalid")
    rows = con.execute(
        """SELECT criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
                  fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
           FROM reference_criterion_semantics
           ORDER BY criterion_rule_id,ordinal"""
    ).fetchall()
    actual_counts: dict[int, int] = {}
    for row in rows:
        criterion_rule_id = int(row["criterion_rule_id"])
        actual_counts[criterion_rule_id] = actual_counts.get(criterion_rule_id, 0) + 1
        try:
            normalized_semantic_record(dict(row))
        except ValueError as exc:
            raise ValueError(
                "reference semantic row "
                f"{row['criterion_rule_id']}:{row['ordinal']} is invalid: {exc}"
            ) from exc
    if set(actual_counts) != set(expectations):
        raise ValueError("reference semantic expectations do not cover materialized semantic rows")
    for criterion_rule_id, expected_count in expectations.items():
        if actual_counts.get(criterion_rule_id, 0) != expected_count:
            raise ValueError(
                "reference semantic materialization count does not match expectation for "
                f"criterion {criterion_rule_id}"
            )


def verify_reference_database(
    database: str | Path,
    expected_contract_major: int | str,
    expected_dataset_id: str,
) -> dict:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"reference database not found: {path}")
    contract_major = normalized_contract_major(expected_contract_major)
    if contract_major != REFERENCE_CONTRACT_MAJOR:
        raise ValueError("reference contract major is unsupported by this runtime")
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

        _verify_schema(con)
        _verify_product_search_index(con)
        _verify_semantics(con)
        try:
            meta = {
                str(key): str(value)
                for key, value in con.execute("SELECT key,value FROM reference_contract_meta")
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError("reference contract metadata is missing or invalid") from exc
        actual_major = normalized_contract_major(meta.get("contract_major", ""))
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


__all__ = [
    "REFERENCE_CONTRACT_MAJOR",
    "REQUIRED_COLUMNS",
    "normalized_contract_major",
    "normalized_semantic_record",
    "verify_reference_database",
]