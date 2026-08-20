from __future__ import annotations

import sqlite3
from typing import Any, Mapping

from .interaction_timing import parse_interaction_timing
from .reference_contracts.v1 import normalized_semantic_record


_SUPPORTED_RUNTIME_EVALUATORS = frozenset({"minimum_separation", "excluded_route"})


def _canonical_ingredient(row: Mapping[str, Any]) -> str | None:
    return row.get("criterion_ingredient_name") or row.get("ingredient_name")


def _canonical_paired_ingredient(row: Mapping[str, Any]) -> str | None:
    return row.get("criterion_paired_ingredient_name") or row.get("paired_ingredient_name")


def _semantic_requires_review(semantic: Mapping[str, Any]) -> bool:
    mode = str(semantic.get("evaluation_mode") or "")
    if mode == "review_required":
        return True
    if mode == "runtime_evaluable":
        evaluator = str(semantic.get("evaluator_kind") or "")
        return (
            evaluator not in _SUPPORTED_RUNTIME_EVALUATORS
            and semantic.get("fallback_action") == "review_required"
        )
    return False


def _legacy_dev_semantics(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Keep canonical-DB development flows usable without shipping the registry."""
    dataset_key = str(row.get("criterion_source_dataset_key") or row.get("dataset_key") or "")
    remark = str(row.get("criterion_qualifier_note") or row.get("qualifier_note") or "").strip()
    if not dataset_key.startswith("mfds_dur_ingredient:") or not remark:
        return []
    try:
        from medicine_reference.mfds_remark_registry import reviewed_mfds_remark
        from medicine_canonical.reference_contracts.v1 import semantic_facts_for_reviewed_remark

        reviewed = reviewed_mfds_remark(row.get("category"), remark)
        facts = semantic_facts_for_reviewed_remark(reviewed) if reviewed is not None else ()
        return [
            {
                "semantic_role": fact.semantic_role,
                "evaluation_mode": fact.evaluation_mode,
                "evaluator_kind": fact.evaluator_kind,
                "fallback_action": fact.fallback_action,
                "qualifier_type": fact.qualifier_type,
                "display_text": fact.display_text,
                "structured_payload": dict(fact.structured_payload),
                "source_remark": fact.source_remark,
            }
            for fact in facts
        ]
    except ImportError:
        # A contract DB must never take this path. If a non-contract runtime DB
        # reaches an APK, keep the finding visible rather than guessing.
        return [{
            "semantic_role": "applicability_condition",
            "evaluation_mode": "review_required",
            "evaluator_kind": "opaque_condition",
            "fallback_action": "review_required",
            "qualifier_type": "source_note",
            "display_text": remark,
            "structured_payload": {},
            "source_remark": remark,
        }]


def _mfds_semantics(
    con: sqlite3.Connection,
    row: Mapping[str, Any],
) -> list[dict[str, Any]]:
    criterion_rule_id = row.get("criterion_rule_id")
    if criterion_rule_id is None:
        return _legacy_dev_semantics(row)
    try:
        expectation = con.execute(
            """SELECT expected_fact_count FROM reference_semantic_expectations
               WHERE criterion_rule_id=?""",
            (criterion_rule_id,),
        ).fetchone()
        records = con.execute(
            """SELECT semantic_role,evaluation_mode,evaluator_kind,fallback_action,
                      qualifier_type,display_text,structured_payload_json,source_remark
               FROM reference_criterion_semantics
               WHERE criterion_rule_id=? ORDER BY ordinal""",
            (criterion_rule_id,),
        ).fetchall()
    except sqlite3.DatabaseError:
        return _legacy_dev_semantics(row)
    if expectation is not None and len(records) != int(expectation[0]):
        source_remark = str(row.get("criterion_qualifier_note") or "").strip()
        return [{
            "semantic_role": "applicability_condition",
            "evaluation_mode": "review_required",
            "evaluator_kind": "opaque_condition",
            "fallback_action": "review_required",
            "qualifier_type": "source_note",
            "display_text": source_remark or "세부 적용 조건 확인 필요",
            "structured_payload": {},
            "source_remark": source_remark,
            "runtime_error_kind": "missing_contract_semantics",
        }]
    result: list[dict[str, Any]] = []
    for record in records:
        raw = {
            "semantic_role": record[0],
            "evaluation_mode": record[1],
            "evaluator_kind": record[2],
            "fallback_action": record[3],
            "qualifier_type": record[4],
            "display_text": record[5],
            "structured_payload_json": record[6],
            "source_remark": record[7],
        }
        try:
            result.append(normalized_semantic_record(raw))
        except ValueError:
            # Published contract DBs are rejected before activation when a known
            # evaluator payload is malformed. If storage corruption or a
            # non-contract development DB still reaches runtime, keep the rule
            # visible for professional review instead of guessing a permissive
            # evaluator value such as a zero-hour separation.
            source_remark = str(record[7] or record[5] or "").strip()
            result.append({
                "semantic_role": "applicability_condition",
                "evaluation_mode": "review_required",
                "evaluator_kind": "opaque_condition",
                "fallback_action": "review_required",
                "qualifier_type": record[4] or "source_note",
                "display_text": record[5] or source_remark,
                "structured_payload": {},
                "source_remark": source_remark,
            })
    return result


def _mfds_qualifiers(con: sqlite3.Connection, row: Mapping[str, Any]) -> list[dict[str, object]]:
    qualifiers: list[dict[str, object]] = []
    for semantic in _mfds_semantics(con, row):
        informational = semantic.get("semantic_role") == "informational"
        qualifiers.append({
            "type": semantic.get("qualifier_type"),
            "text": semantic.get("display_text"),
            "source_remark": semantic.get("source_remark"),
            "mode": "informational" if informational else "condition",
            "requires_review": _semantic_requires_review(semantic),
        })
    return qualifiers


def _mfds_criterion_note_requires_review(
    row: Mapping[str, Any],
    con: sqlite3.Connection | None = None,
) -> bool:
    semantics = _mfds_semantics(con, row) if con is not None else _legacy_dev_semantics(row)
    if not semantics:
        return False
    if row.get("match_method") == "mfds_unanimous_value":
        return True
    return any(_semantic_requires_review(semantic) for semantic in semantics)


def _details_with_professional_review(details: Any) -> str:
    advice = "세부 적용 조건이 있어 의사 또는 약사에게 확인하세요."
    text = str(details or "").strip()
    return f"{text} {advice}" if text else advice


def _remark_interaction_timing(
    row: Mapping[str, Any],
    details: Any,
    con: sqlite3.Connection | None = None,
) -> dict[str, Any]:
    semantics = _mfds_semantics(con, row) if con is not None else _legacy_dev_semantics(row)
    timing = next(
        (
            semantic
            for semantic in semantics
            if semantic.get("evaluator_kind") == "minimum_separation"
        ),
        None,
    )
    if timing is not None:
        payload = timing.get("structured_payload") or {}
        hours = int(payload.get("hours") or 0)
        return {
            "status": "structured",
            "kind": "minimum_separation",
            "hours": hours,
            "amount": hours,
            "unit": "시간",
            "direction": payload.get("direction") or "symmetric",
            "source_text": timing.get("source_remark"),
        }
    unknown_runtime_conditions = [
        semantic
        for semantic in semantics
        if semantic.get("semantic_role") == "applicability_condition"
        and semantic.get("evaluation_mode") == "runtime_evaluable"
        and semantic.get("evaluator_kind") not in _SUPPORTED_RUNTIME_EVALUATORS
        and semantic.get("fallback_action") == "review_required"
    ]
    if unknown_runtime_conditions:
        # New evaluator kinds are conservative contract extensions. An older APK
        # must not fall back to unrelated text parsing and suppress the finding.
        return {
            "status": "not_evaluable",
            "kind": "unknown_contract_evaluator",
            "reason": "reference condition evaluator is not supported by this app version",
            "source_text": unknown_runtime_conditions[0].get("source_remark"),
        }
    review_required_conditions = [
        semantic
        for semantic in semantics
        if semantic.get("semantic_role") == "applicability_condition"
        and semantic.get("evaluation_mode") == "review_required"
        and semantic.get("fallback_action") == "review_required"
    ]
    if review_required_conditions:
        error_kind = review_required_conditions[0].get("runtime_error_kind")
        return {
            "status": "not_evaluable",
            "kind": error_kind or "review_required_condition",
            "reason": "reference condition requires professional review",
            "source_text": review_required_conditions[0].get("source_remark"),
        }
    return parse_interaction_timing(
        details or "", _canonical_ingredient(row), _canonical_paired_ingredient(row)
    )


def _dedupe_qualifiers(values: list[dict[str, object]]) -> list[dict[str, object]]:
    seen: set[tuple[object, object, object]] = set()
    result: list[dict[str, object]] = []
    for value in values:
        key = (value.get("type"), value.get("text"), value.get("source_remark"))
        if key in seen:
            continue
        seen.add(key)
        result.append(value)
    return result


__all__ = [
    "_dedupe_qualifiers",
    "_details_with_professional_review",
    "_mfds_criterion_note_requires_review",
    "_mfds_qualifiers",
    "_mfds_semantics",
    "_remark_interaction_timing",
]