from __future__ import annotations

from collections import Counter
from typing import Any, Iterable, Mapping

from .contract import Corpus, CorpusError, normalize_rows, rows_as_dicts


def _rows_by_product(rows: Iterable[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    return {str(row["product_query"]): row for row in rows}


def evaluate_case(expected_rows: object, predicted_rows: object) -> dict[str, Any]:
    expected = normalize_rows(expected_rows, "expected rows")
    predicted = normalize_rows(predicted_rows, "predicted rows", allow_empty=True)
    expected_by_product = _rows_by_product(expected)
    predicted_by_product = _rows_by_product(predicted)

    expected_products = set(expected_by_product)
    predicted_products = set(predicted_by_product)
    matched_products = sorted(expected_products & predicted_products)

    exact_fields = 0
    field_total = 0
    unresolved_fields = 0
    false_exact_fields = 0
    cross_medication_associations = 0

    for product_query in matched_products:
        expected_row = expected_by_product[product_query]
        predicted_row = predicted_by_product[product_query]
        expected_draft = expected_row["draft"]
        predicted_draft = predicted_row["draft"]
        for field, expected_value in expected_draft.items():
            if expected_value is None:
                continue
            field_total += 1
            predicted_value = predicted_draft.get(field)
            if predicted_value is None:
                unresolved_fields += 1
                continue
            if predicted_value == expected_value:
                exact_fields += 1
                continue
            false_exact_fields += 1
            for other_product, other_row in expected_by_product.items():
                if other_product == product_query:
                    continue
                if other_row["draft"].get(field) == predicted_value:
                    cross_medication_associations += 1
                    break

    missing_rows = len(expected_products - predicted_products)
    unexpected_rows = len(predicted_products - expected_products)
    return {
        "expected_rows": len(expected),
        "predicted_rows": len(predicted),
        "matched_rows": len(matched_products),
        "missing_rows": missing_rows,
        "unexpected_rows": unexpected_rows,
        "field_total": field_total,
        "exact_fields": exact_fields,
        "unresolved_fields": unresolved_fields,
        "false_exact_fields": false_exact_fields,
        "cross_medication_associations": cross_medication_associations,
        "row_recall": len(matched_products) / len(expected) if expected else 1.0,
        "field_exact_accuracy": exact_fields / field_total if field_total else 1.0,
        "safety_pass": false_exact_fields == 0 and unexpected_rows == 0,
    }


def _predictions_by_case(value: object) -> dict[str, list[dict[str, Any]]]:
    if not isinstance(value, Mapping):
        raise CorpusError("predictions must be an object")
    if set(value) != {"schema_version", "predictions"}:
        raise CorpusError("predictions must contain only schema_version and predictions")
    if value.get("schema_version") != 1:
        raise CorpusError("unsupported predictions schema_version")
    predictions = value.get("predictions")
    if not isinstance(predictions, list):
        raise CorpusError("predictions must be a list")
    by_case: dict[str, list[dict[str, Any]]] = {}
    for index, raw in enumerate(predictions):
        if not isinstance(raw, Mapping) or set(raw) != {"case_id", "rows"}:
            raise CorpusError(f"predictions[{index}] must contain only case_id and rows")
        case_id = str(raw.get("case_id") or "").strip()
        if not case_id:
            raise CorpusError(f"predictions[{index}] case_id must not be empty")
        if case_id in by_case:
            raise CorpusError(f"duplicate prediction case_id: {case_id}")
        by_case[case_id] = normalize_rows(raw.get("rows"), f"predictions for {case_id}", allow_empty=True)
    return by_case


def evaluate_corpus(corpus: Corpus, predictions: object) -> dict[str, Any]:
    predictions_by_case = _predictions_by_case(predictions)
    corpus_ids = {case.case_id for case in corpus.cases}
    unexpected_cases = sorted(set(predictions_by_case) - corpus_ids)
    if unexpected_cases:
        raise CorpusError(f"predictions contain unknown case_id values: {', '.join(unexpected_cases)}")

    totals: Counter[str] = Counter()
    cases: list[dict[str, Any]] = []
    for case in corpus.cases:
        predicted_rows = predictions_by_case.get(case.case_id)
        if predicted_rows is None:
            predicted_rows = []
            result = {
                "expected_rows": len(case.expected_rows),
                "predicted_rows": 0,
                "matched_rows": 0,
                "missing_rows": len(case.expected_rows),
                "unexpected_rows": 0,
                "field_total": sum(
                    1 for row in case.expected_rows for value in row.draft.values() if value is not None
                ),
                "exact_fields": 0,
                "unresolved_fields": 0,
                "false_exact_fields": 0,
                "cross_medication_associations": 0,
                "row_recall": 0.0,
                "field_exact_accuracy": 0.0,
                "safety_pass": True,
            }
        else:
            result = evaluate_case(rows_as_dicts(case.expected_rows), predicted_rows)
        for key in (
            "expected_rows",
            "predicted_rows",
            "matched_rows",
            "missing_rows",
            "unexpected_rows",
            "field_total",
            "exact_fields",
            "unresolved_fields",
            "false_exact_fields",
            "cross_medication_associations",
        ):
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
    return {
        "status": "ok",
        "case_count": len(corpus.cases),
        **dict(totals),
        "row_recall": totals["matched_rows"] / expected_rows if expected_rows else 1.0,
        "field_exact_accuracy": totals["exact_fields"] / field_total if field_total else 1.0,
        "safety_pass": totals["false_exact_fields"] == 0 and totals["unexpected_rows"] == 0,
        "cases": cases,
    }


__all__ = ["evaluate_case", "evaluate_corpus"]