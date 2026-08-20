from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass

from medicine_reference.mfds_remark_registry import ReviewedMfdsRemark, reviewed_mfds_remark


REFERENCE_CONTRACT_MAJOR = 1

REFERENCE_CONTRACT_META_DDL = """CREATE TABLE reference_contract_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)"""

REFERENCE_BUILD_META_DDL = """CREATE TABLE reference_build_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)"""

REFERENCE_CRITERION_SEMANTICS_DDL = """CREATE TABLE reference_criterion_semantics (
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    ordinal INTEGER NOT NULL,
    semantic_role TEXT NOT NULL CHECK(semantic_role IN ('informational','applicability_condition')),
    evaluation_mode TEXT NOT NULL CHECK(evaluation_mode IN ('resolved_at_build','runtime_evaluable','review_required')),
    evaluator_kind TEXT NOT NULL,
    fallback_action TEXT NOT NULL CHECK(fallback_action IN ('none','review_required')),
    qualifier_type TEXT NOT NULL,
    display_text TEXT NOT NULL,
    structured_payload_json TEXT NOT NULL,
    source_remark TEXT NOT NULL,
    PRIMARY KEY(criterion_rule_id, ordinal)
) WITHOUT ROWID"""


@dataclass(frozen=True)
class ReferenceSemanticFact:
    semantic_role: str
    evaluation_mode: str
    evaluator_kind: str
    fallback_action: str
    qualifier_type: str
    display_text: str
    structured_payload: dict[str, object]
    source_remark: str


def semantic_facts_for_reviewed_remark(
    reviewed: ReviewedMfdsRemark,
) -> tuple[ReferenceSemanticFact, ...]:
    """Translate one exact human review into zero or more contract facts.

    Build-scope remarks are consumed while linking and intentionally do not
    cross the app↔DB contract boundary.  The tuple return type is deliberate:
    future exact reviews may materialize multiple independent facts without
    changing the contract table shape or reintroducing free-text parsing.
    """
    common = {
        "qualifier_type": reviewed.qualifier_type,
        "display_text": reviewed.display_text,
        "source_remark": reviewed.remark,
    }
    if reviewed.mode == "composition_scope":
        return ()
    if reviewed.mode == "informational":
        return (
            ReferenceSemanticFact(
                semantic_role="informational",
                evaluation_mode="resolved_at_build",
                evaluator_kind="display_only",
                fallback_action="none",
                structured_payload={},
                **common,
            ),
        )
    if reviewed.mode == "review_required":
        return (
            ReferenceSemanticFact(
                semantic_role="applicability_condition",
                evaluation_mode="review_required",
                evaluator_kind="opaque_condition",
                fallback_action="review_required",
                structured_payload={},
                **common,
            ),
        )
    if reviewed.mode == "interaction_window":
        return (
            ReferenceSemanticFact(
                semantic_role="applicability_condition",
                evaluation_mode="runtime_evaluable",
                evaluator_kind="minimum_separation",
                fallback_action="review_required",
                structured_payload={
                    "hours": int(reviewed.value or "0"),
                    "direction": "symmetric",
                },
                **common,
            ),
        )
    if reviewed.mode == "form_exclusion":
        return (
            ReferenceSemanticFact(
                semantic_role="applicability_condition",
                evaluation_mode="runtime_evaluable",
                evaluator_kind="excluded_route",
                fallback_action="review_required",
                structured_payload={"route": reviewed.value},
                **common,
            ),
        )
    raise ValueError(f"unsupported reviewed MFDS REMARK mode: {reviewed.mode!r}")


def materialize_reference_semantics(
    source: sqlite3.Connection,
    target: sqlite3.Connection,
) -> int:
    target.execute(REFERENCE_CRITERION_SEMANTICS_DDL)
    inserted = 0
    rows = source.execute(
        """SELECT id,category,qualifier_note
           FROM ingredient_rules
           WHERE qualifier_note IS NOT NULL AND TRIM(qualifier_note) != ''
           ORDER BY id"""
    )
    for criterion_rule_id, category, source_remark in rows:
        reviewed = reviewed_mfds_remark(category, source_remark)
        if reviewed is None:
            continue
        for ordinal, fact in enumerate(semantic_facts_for_reviewed_remark(reviewed)):
            target.execute(
                """INSERT INTO reference_criterion_semantics(
                       criterion_rule_id,ordinal,semantic_role,evaluation_mode,evaluator_kind,
                       fallback_action,qualifier_type,display_text,structured_payload_json,source_remark
                   ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
                (
                    criterion_rule_id,
                    ordinal,
                    fact.semantic_role,
                    fact.evaluation_mode,
                    fact.evaluator_kind,
                    fact.fallback_action,
                    fact.qualifier_type,
                    fact.display_text,
                    json.dumps(
                        fact.structured_payload,
                        ensure_ascii=False,
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                    fact.source_remark,
                ),
            )
            inserted += 1
    return inserted


_LOGICAL_PROJECTIONS: tuple[tuple[str, str], ...] = (
    (
        "products",
        """SELECT item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                  permit_date,cancel_date,cancel_name,permit_status
           FROM products
           ORDER BY item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                    permit_date,cancel_date,cancel_name,permit_status""",
    ),
    (
        "product_identifiers",
        """SELECT item_seq,system,value FROM product_identifiers
           ORDER BY item_seq,system,value""",
    ),
    (
        "product_flags",
        """SELECT item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,details,change_date
           FROM product_flags
           ORDER BY item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,details,change_date""",
    ),
    (
        "product_rules",
        """SELECT category,item_seq,ingredient_code,ingredient_name,ingredient_name_en,
                  paired_item_seq,paired_ingredient_code,paired_ingredient_name,
                  paired_ingredient_name_en,effect_name,dosage_form,details,
                  notification_date,change_date
           FROM product_rules
           ORDER BY category,item_seq,ingredient_code,ingredient_name,ingredient_name_en,
                    paired_item_seq,paired_ingredient_code,paired_ingredient_name,
                    paired_ingredient_name_en,effect_name,dosage_form,details,
                    notification_date,change_date""",
    ),
    (
        "ingredient_rules",
        """SELECT category,sequence_text,ingredient_name,ingredient_name_ko,
                  paired_ingredient_name,rule_value,dosage_form,note,details
           FROM ingredient_rules
           ORDER BY category,sequence_text,ingredient_name,ingredient_name_ko,
                    paired_ingredient_name,rule_value,dosage_form,note,details""",
    ),
    (
        "dose_criteria",
        """SELECT i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason
           FROM dose_criteria d JOIN ingredient_rules i ON i.id=d.criterion_rule_id
           ORDER BY i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                    i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                    d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason""",
    ),
    (
        "product_criterion_links",
        """SELECT
                  p.category,p.item_seq,p.ingredient_code,p.ingredient_name,p.ingredient_name_en,
                  p.paired_item_seq,p.paired_ingredient_code,p.paired_ingredient_name,
                  p.paired_ingredient_name_en,p.effect_name,p.dosage_form,p.details,
                  p.notification_date,p.change_date,
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  l.match_method,l.pair_orientation
           FROM product_criterion_links l
           JOIN product_rules p ON p.id=l.product_rule_id
           JOIN ingredient_rules i ON i.id=l.criterion_rule_id
           ORDER BY
                  p.category,p.item_seq,p.ingredient_code,p.ingredient_name,p.ingredient_name_en,
                  p.paired_item_seq,p.paired_ingredient_code,p.paired_ingredient_name,
                  p.paired_ingredient_name_en,p.effect_name,p.dosage_form,p.details,
                  p.notification_date,p.change_date,
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  l.match_method,l.pair_orientation""",
    ),
    (
        "criterion_semantics",
        """SELECT
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,
                  s.fallback_action,s.qualifier_type,s.display_text,s.structured_payload_json
           FROM reference_criterion_semantics s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id
           ORDER BY
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,
                  s.fallback_action,s.qualifier_type,s.display_text,s.structured_payload_json""",
    ),
)


def logical_dataset_id(database: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    digest.update(f"reference-contract\0{REFERENCE_CONTRACT_MAJOR}\n".encode("utf-8"))
    for label, query in _LOGICAL_PROJECTIONS:
        digest.update(f"section\0{label}\n".encode("utf-8"))
        for row in database.execute(query):
            encoded = json.dumps(
                list(row),
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            digest.update(encoded)
            digest.update(b"\n")
    return f"sha256:{digest.hexdigest()}"


def write_contract_meta(database: sqlite3.Connection, dataset_id: str) -> None:
    database.execute(REFERENCE_CONTRACT_META_DDL)
    database.executemany(
        "INSERT INTO reference_contract_meta(key,value) VALUES(?,?)",
        [
            ("contract_major", str(REFERENCE_CONTRACT_MAJOR)),
            ("dataset_id", dataset_id),
        ],
    )


def write_build_meta(
    database: sqlite3.Connection,
    *,
    canonical_schema_version: str,
    physical_policy_version: str,
) -> None:
    database.execute(REFERENCE_BUILD_META_DDL)
    database.executemany(
        "INSERT INTO reference_build_meta(key,value) VALUES(?,?)",
        [
            ("canonical_schema_version", canonical_schema_version),
            ("physical_policy_version", physical_policy_version),
        ],
    )


__all__ = [
    "REFERENCE_CONTRACT_MAJOR",
    "ReferenceSemanticFact",
    "logical_dataset_id",
    "materialize_reference_semantics",
    "semantic_facts_for_reviewed_remark",
    "write_build_meta",
    "write_contract_meta",
]