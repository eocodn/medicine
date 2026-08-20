from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from medicine_reference.mfds_remark_registry import ReviewedMfdsRemark, reviewed_mfds_remark

from .v1_identity import (
    logical_dataset_id,
    logical_dataset_id_oracle,
)


REFERENCE_CONTRACT_MAJOR = 1

REFERENCE_CONTRACT_META_DDL = """CREATE TABLE reference_contract_meta (
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

REFERENCE_SEMANTIC_EXPECTATIONS_DDL = """CREATE TABLE reference_semantic_expectations (
    criterion_rule_id INTEGER PRIMARY KEY REFERENCES ingredient_rules(id),
    expected_fact_count INTEGER NOT NULL CHECK(expected_fact_count > 0)
) WITHOUT ROWID"""

PRODUCT_RULE_CRITERIA_VIEW_DDL = """CREATE VIEW product_rule_criteria AS
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


def _normalized_schema_sql(value: str | None) -> str:
    return " ".join(str(value or "").strip().rstrip(";").split())


def _verify_frozen_runtime_views(database: sqlite3.Connection) -> None:
    row = database.execute(
        "SELECT type,sql FROM sqlite_master WHERE name='product_rule_criteria'"
    ).fetchone()
    if row is None or row[0] != "view":
        raise ValueError("reference product_rule_criteria runtime object must be the frozen view")
    if _normalized_schema_sql(row[1]) != _normalized_schema_sql(PRODUCT_RULE_CRITERIA_VIEW_DDL):
        raise ValueError("reference product_rule_criteria runtime view definition is not contract-v1")


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
    target.execute(REFERENCE_SEMANTIC_EXPECTATIONS_DDL)
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
        facts = semantic_facts_for_reviewed_remark(reviewed)
        if facts:
            target.execute(
                """INSERT INTO reference_semantic_expectations(
                       criterion_rule_id,expected_fact_count
                   ) VALUES(?,?)""",
                (criterion_rule_id, len(facts)),
            )
        for ordinal, fact in enumerate(facts):
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


def _verify_reviewed_semantic_materialization(database: sqlite3.Connection) -> None:
    """Prove the exported semantic rows exactly match the server review registry.

    The APK intentionally does not ship the exact REMARK registry.  Therefore
    the publisher-side contract verifier is the authoritative boundary which
    must prove that every reviewed source qualifier with runtime facts was
    materialized completely, while build-resolved composition-scope reviews
    remain intentionally fact-free.
    """
    rows = database.execute(
        """SELECT id,category,qualifier_note
           FROM ingredient_rules
           WHERE qualifier_note IS NOT NULL AND TRIM(qualifier_note) != ''
           ORDER BY id"""
    ).fetchall()
    for criterion_rule_id, category, source_remark in rows:
        reviewed = reviewed_mfds_remark(category, source_remark)
        if reviewed is None:
            raise ValueError(
                "reference semantic materialization contains an unreviewed source REMARK"
            )
        expected = semantic_facts_for_reviewed_remark(reviewed)
        expectation = database.execute(
            """SELECT expected_fact_count FROM reference_semantic_expectations
               WHERE criterion_rule_id=?""",
            (criterion_rule_id,),
        ).fetchone()
        if expected:
            if expectation is None or int(expectation[0]) != len(expected):
                raise ValueError(
                    "reference semantic materialization expectation is missing or incorrect"
                )
        elif expectation is not None:
            raise ValueError(
                "reference semantic materialization unexpectedly exports build-resolved facts"
            )

        actual = database.execute(
            """SELECT ordinal,semantic_role,evaluation_mode,evaluator_kind,fallback_action,
                      qualifier_type,display_text,structured_payload_json,source_remark
               FROM reference_criterion_semantics
               WHERE criterion_rule_id=? ORDER BY ordinal""",
            (criterion_rule_id,),
        ).fetchall()
        expected_rows = [
            (
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
            )
            for ordinal, fact in enumerate(expected)
        ]
        if actual != expected_rows:
            raise ValueError(
                "reference semantic materialization does not match reviewed source semantics"
            )

    extra_expectation = database.execute(
        """SELECT e.criterion_rule_id
           FROM reference_semantic_expectations e
           LEFT JOIN ingredient_rules i ON i.id=e.criterion_rule_id
           WHERE i.id IS NULL OR i.qualifier_note IS NULL OR TRIM(i.qualifier_note)=''
           LIMIT 1"""
    ).fetchone()
    extra_semantic = database.execute(
        """SELECT s.criterion_rule_id
           FROM reference_criterion_semantics s
           LEFT JOIN ingredient_rules i ON i.id=s.criterion_rule_id
           WHERE i.id IS NULL OR i.qualifier_note IS NULL OR TRIM(i.qualifier_note)=''
           LIMIT 1"""
    ).fetchone()
    if extra_expectation is not None or extra_semantic is not None:
        raise ValueError("reference semantic materialization has orphaned semantic facts")


def write_contract_meta(database: sqlite3.Connection, dataset_id: str) -> None:
    database.execute(REFERENCE_CONTRACT_META_DDL)
    database.executemany(
        "INSERT INTO reference_contract_meta(key,value) VALUES(?,?)",
        [
            ("contract_major", str(REFERENCE_CONTRACT_MAJOR)),
            ("dataset_id", dataset_id),
        ],
    )


def export_reference_database(
    canonical_db: str | Path,
    output_db: str | Path,
    *,
    manifest_path: str | Path | None = None,
    physical_policy_version: str | None = None,
    progress=None,
) -> dict:
    """Frozen contract-v1 exporter entry point.

    Future contract majors get separate versioned entry points.  The shared
    mobile builder may continue to own physical storage mechanics, but release
    orchestration must call this versioned function so N-1 never silently
    switches to a newer logical contract exporter.
    """
    from medicine_canonical.mobile import MOBILE_PHYSICAL_POLICY_VERSION, _build_mobile_database

    selected_physical_policy = (
        MOBILE_PHYSICAL_POLICY_VERSION
        if physical_policy_version is None
        else physical_policy_version
    )
    result = _build_mobile_database(
        canonical_db,
        output_db,
        contract_major=REFERENCE_CONTRACT_MAJOR,
        materialize_semantics=materialize_reference_semantics,
        logical_dataset_id=lambda database: logical_dataset_id(
            database,
            physical_policy_version=selected_physical_policy,
            progress=progress,
        ),
        write_contract_meta=write_contract_meta,
        product_rule_criteria_view_ddl=PRODUCT_RULE_CRITERIA_VIEW_DDL,
        manifest_path=manifest_path,
        physical_policy_version=selected_physical_policy,
        progress=progress,
    )
    if result.get("contract_major") != REFERENCE_CONTRACT_MAJOR:
        raise RuntimeError("contract-v1 exporter emitted the wrong contract major")
    return result


def verify_built_reference_database(
    database: str | Path,
    contract_major: int,
    dataset_id: str,
) -> dict:
    """Verify a just-built C1 artifact without repeating its logical hash pass.

    This entry point is safe only for the trusted exporter path which already
    computed ``dataset_id`` from the same in-memory SQLite database.  The
    publisher rebinds that trust to the final immutable SHA/size before any
    external state change.  Arbitrary databases must use
    :func:`verify_reference_database`, which recomputes logical identity.
    """
    if contract_major != REFERENCE_CONTRACT_MAJOR:
        raise ValueError("contract-v1 verifier received a different contract major")
    from medicine_app.reference_contracts.v1 import verify_reference_database as runtime_verify

    result = runtime_verify(
        database,
        expected_contract_major=contract_major,
        expected_dataset_id=dataset_id,
    )
    uri = f"file:{Path(database).resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as con:
        _verify_frozen_runtime_views(con)
        _verify_reviewed_semantic_materialization(con)
    return result


def verify_reference_database(
    database: str | Path,
    contract_major: int,
    dataset_id: str,
) -> dict:
    """Frozen strict server-side verifier for an arbitrary C1 release candidate."""
    result = verify_built_reference_database(database, contract_major, dataset_id)
    uri = f"file:{Path(database).resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as con:
        # Keep the arbitrary-file verifier independent from the optimized
        # exporter.  The oracle SQL is the frozen C1 identity specification;
        # reusing the fast executor here would let one implementation defect
        # validate itself.  Normal build->publish avoids this expensive pass by
        # carrying an in-process byte-bound VerifiedContractArtifact instead.
        actual_dataset_id = logical_dataset_id_oracle(con)
        if actual_dataset_id != str(dataset_id).lower():
            raise ValueError("reference logical dataset identity does not match release")
    return result


__all__ = [
    "REFERENCE_CONTRACT_MAJOR",
    "PRODUCT_RULE_CRITERIA_VIEW_DDL",
    "ReferenceSemanticFact",
    "export_reference_database",
    "logical_dataset_id",
    "logical_dataset_id_oracle",
    "materialize_reference_semantics",
    "semantic_facts_for_reviewed_remark",
    "verify_built_reference_database",
    "verify_reference_database",
    "write_contract_meta",
]
