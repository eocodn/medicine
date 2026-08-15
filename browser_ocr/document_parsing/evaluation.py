from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from .contract import DRAFT_FIELDS, SCHEMA_VERSION, Corpus, CorpusError, normalize_rows, rows_as_dicts


def _rows_by_id(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row["row_id"]): row for row in rows}


def _evidence(row: Mapping[str, Any], field: str) -> set[str]:
    raw = row.get("evidence", {}).get(field, [])
    return {str(value) for value in raw}


def _association_error(
    field: str,
    expected_row: Mapping[str, Any],
    predicted_row: Mapping[str, Any],
    expected_rows: Iterable[Mapping[str, Any]],
) -> tuple[int, int]:
    predicted_evidence = _evidence(predicted_row, field)
    expected_evidence = _evidence(expected_row, field)
    if predicted_evidence & expected_evidence:
        return 0, 0
    for other in expected_rows:
        if other["row_id"] == expected_row["row_id"]:
            continue
        if predicted_evidence & _evidence(other, field):
            return 1, 0
    return 0, 1


def _evaluate_normalized(
    expected: list[dict[str, Any]],
    predicted: list[dict[str, Any]],
) -> dict[str, Any]:
    expected_by_id = _rows_by_id(expected)
    predicted_by_id = _rows_by_id(predicted)
    expected_ids = set(expected_by_id)
    predicted_ids = set(predicted_by_id)
    matched_ids = sorted(expected_ids & predicted_ids)

    exact_fields = 0
    field_total = 0
    unresolved_fields = 0
    false_exact_fields = 0
    invented_fields = 0
    cross_medication_associations = 0
    unproven_associations = 0
    product_query_mismatches = 0
    product_exact_rows = 0

    for row_id in matched_ids:
        expected_row = expected_by_id[row_id]
        predicted_row = predicted_by_id[row_id]
        if predicted_row["product_query"] == expected_row["product_query"]:
            product_exact_rows += 1
        else:
            product_query_mismatches += 1
        cross, unproven = _association_error("product_query", expected_row, predicted_row, expected)
        cross_medication_associations += cross
        unproven_associations += unproven

        expected_draft = expected_row["draft"]
        predicted_draft = predicted_row["draft"]
        for field in sorted(DRAFT_FIELDS):
            expected_value = expected_draft.get(field)
            predicted_value = predicted_draft.get(field)
            if expected_value is None:
                if predicted_value is None:
                    continue
                false_exact_fields += 1
                invented_fields += 1
                cross, unproven = _association_error(field, expected_row, predicted_row, expected)
                cross_medication_associations += cross
                unproven_associations += unproven
                continue
            field_total += 1
            if predicted_value is None:
                unresolved_fields += 1
                continue
            if predicted_value == expected_value:
                exact_fields += 1
            else:
                false_exact_fields += 1
            cross, unproven = _association_error(field, expected_row, predicted_row, expected)
            cross_medication_associations += cross
            unproven_associations += unproven

    missing_rows = len(expected_ids - predicted_ids)
    unexpected_rows = len(predicted_ids - expected_ids)
    safety_pass = (
        false_exact_fields == 0
        and unexpected_rows == 0
        and cross_medication_associations == 0
        and unproven_associations == 0
        and product_query_mismatches == 0
    )
    return {
        "expected_rows": len(expected),
        "predicted_rows": len(predicted),
        "matched_rows": len(matched_ids),
        "missing_rows": missing_rows,
        "unexpected_rows": unexpected_rows,
        "product_exact_rows": product_exact_rows,
        "product_query_mismatches": product_query_mismatches,
        "field_total": field_total,
        "exact_fields": exact_fields,
        "unresolved_fields": unresolved_fields,
        "false_exact_fields": false_exact_fields,
        "invented_fields": invented_fields,
        "cross_medication_associations": cross_medication_associations,
        "unproven_associations": unproven_associations,
        "row_recall": len(matched_ids) / len(expected) if expected else 1.0,
        "field_exact_accuracy": exact_fields / field_total if field_total else 1.0,
        "safety_pass": safety_pass,
    }


def evaluate_case(expected_rows: object, predicted_rows: object) -> dict[str, Any]:
    expected = normalize_rows(expected_rows, "expected rows")
    predicted = normalize_rows(predicted_rows, "predicted rows", allow_empty=True)
    return _evaluate_normalized(expected, predicted)


def _prediction_rows_by_case(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        raise CorpusError("predictions must be an object")
    if set(value) != {"schema_version", "predictions"}:
        raise CorpusError("predictions must contain only schema_version and predictions")
    if value.get("schema_version") != SCHEMA_VERSION:
        raise CorpusError("unsupported predictions schema_version")
    predictions = value.get("predictions")
    if not isinstance(predictions, list):
        raise CorpusError("predictions must be a list")
    by_case: dict[str, object] = {}
    for index, raw in enumerate(predictions):
        if not isinstance(raw, Mapping) or set(raw) != {"case_id", "rows"}:
            raise CorpusError(f"predictions[{index}] must contain only case_id and rows")
        case_id = str(raw.get("case_id") or "").strip()
        if not case_id:
            raise CorpusError(f"predictions[{index}] case_id must not be empty")
        if case_id in by_case:
            raise CorpusError(f"duplicate prediction case_id: {case_id}")
        by_case[case_id] = raw.get("rows")
    return by_case


def evaluate_corpus(corpus: Corpus, predictions: object) -> dict[str, Any]:
    predictions_by_case = _prediction_rows_by_case(predictions)
    corpus_ids = {case.case_id for case in corpus.cases}
    unexpected_cases = sorted(set(predictions_by_case) - corpus_ids)
    if unexpected_cases:
        raise CorpusError(f"predictions contain unknown case_id values: {', '.join(unexpected_cases)}")

    totals: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    counted_keys = (
        "expected_rows",
        "predicted_rows",
        "matched_rows",
        "missing_rows",
        "unexpected_rows",
        "product_exact_rows",
        "product_query_mismatches",
        "field_total",
        "exact_fields",
        "unresolved_fields",
        "false_exact_fields",
        "invented_fields",
        "cross_medication_associations",
        "unproven_associations",
    )
    for case in corpus.cases:
        expected = rows_as_dicts(case.expected_rows)
        raw_predicted = predictions_by_case.get(case.case_id, [])
        predicted = normalize_rows(
            raw_predicted,
            f"predictions for {case.case_id}",
            allow_empty=True,
            valid_box_ids={box.box_id for box in case.boxes},
        )
        result = _evaluate_normalized(expected, predicted)
        for key in counted_keys:
            totals[key] += int(result[key])
        cases.append(
            {
                "case_id": case.case_id,
                "scenario_tags": list(case.scenario_tags),
                "risk_tags": list(case.risk_tags),
                **result,
            }
        )

    expected_rows = totals["expected_rows"]
    field_total = totals["field_total"]
    safety_pass = (
        totals["false_exact_fields"] == 0
        and totals["unexpected_rows"] == 0
        and totals["cross_medication_associations"] == 0
        and totals["unproven_associations"] == 0
        and totals["product_query_mismatches"] == 0
    )
    return {
        "status": "ok",
        "case_count": len(corpus.cases),
        **dict(totals),
        "row_recall": totals["matched_rows"] / expected_rows if expected_rows else 1.0,
        "field_exact_accuracy": totals["exact_fields"] / field_total if field_total else 1.0,
        "safety_pass": safety_pass,
        "cases": cases,
    }


__all__ = ["evaluate_case", "evaluate_corpus"]