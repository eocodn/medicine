from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping


SCHEMA_VERSION = 2
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_TAG_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_ISSUE_RE = re.compile(r"^[A-Z][A-Z0-9_]{0,63}$")
_CORPUS_FIELDS = {"schema_version", "cases"}
_CASE_FIELDS = {
    "case_id",
    "source_kind",
    "scenario_tags",
    "risk_tags",
    "boxes",
    "expected_rows",
}
_BOX_FIELDS = {"box_id", "text", "confidence", "polygon"}
DRAFT_FIELDS = {
    "dosage_text",
    "dose_amount",
    "dose_unit",
    "frequency_per_day",
    "meal_relation",
    "administration_route",
    "as_needed",
    "prescription_days",
    "schedule_times",
    "start_date",
    "end_date",
}
_ROW_FIELDS = {"row_id", "product_query", "draft", "uncertainty_codes", "evidence"}
_EVIDENCE_FIELDS = {"product_query", *DRAFT_FIELDS}


class CorpusError(ValueError):
    pass


@dataclass(frozen=True)
class OcrBox:
    box_id: str
    text: str
    confidence: float
    polygon: tuple[tuple[float, float], ...]


@dataclass(frozen=True)
class ExpectedRow:
    row_id: str
    product_query: str
    draft: Mapping[str, Any]
    uncertainty_codes: tuple[str, ...]
    evidence: Mapping[str, tuple[str, ...]]


@dataclass(frozen=True)
class CorpusCase:
    case_id: str
    source_kind: str
    scenario_tags: tuple[str, ...]
    risk_tags: tuple[str, ...]
    boxes: tuple[OcrBox, ...]
    expected_rows: tuple[ExpectedRow, ...]


@dataclass(frozen=True)
class Corpus:
    schema_version: int
    cases: tuple[CorpusCase, ...]


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise CorpusError(f"{label} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise CorpusError(f"unsupported {label} fields: {', '.join(map(str, unknown))}")


def _require_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise CorpusError(f"{label} must use 1-64 ASCII letters, digits, '.', '_' or '-'")
    return text


def _require_tags(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value:
        raise CorpusError(f"{label} must be a non-empty list")
    tags: list[str] = []
    for raw in value:
        tag = str(raw or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise CorpusError(f"{label} values must be lowercase ASCII tags")
        if tag in tags:
            raise CorpusError(f"{label} values must be unique")
        tags.append(tag)
    return tuple(tags)


def _point(value: object, label: str) -> tuple[float, float]:
    if not isinstance(value, list) or len(value) != 2:
        raise CorpusError(f"{label} polygon points must contain exactly two coordinates")
    coordinates: list[float] = []
    for coordinate in value:
        if isinstance(coordinate, bool) or not isinstance(coordinate, (int, float)):
            raise CorpusError(f"{label} polygon coordinates must be numeric")
        number = float(coordinate)
        if not math.isfinite(number):
            raise CorpusError(f"{label} polygon coordinates must be finite")
        coordinates.append(number)
    return coordinates[0], coordinates[1]


def _box(value: object, case_id: str) -> OcrBox:
    data = _require_mapping(value, f"box in {case_id}")
    _reject_unknown(data, _BOX_FIELDS, f"box in {case_id}")
    box_id = _require_id(data.get("box_id"), f"box_id in {case_id}")
    text = str(data.get("text") or "").strip()
    if not text or len(text) > 512 or "\n" in text or "\r" in text:
        raise CorpusError(f"text in {case_id}/{box_id} must contain 1-512 single-line characters")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise CorpusError(f"confidence in {case_id}/{box_id} must be numeric")
    confidence_number = float(confidence)
    if not math.isfinite(confidence_number) or not 0.0 <= confidence_number <= 1.0:
        raise CorpusError(f"confidence in {case_id}/{box_id} must be between 0 and 1")
    polygon = data.get("polygon")
    if not isinstance(polygon, list) or len(polygon) != 4:
        raise CorpusError(f"polygon in {case_id}/{box_id} must contain exactly four points")
    return OcrBox(
        box_id=box_id,
        text=text,
        confidence=confidence_number,
        polygon=tuple(_point(point, f"{case_id}/{box_id}") for point in polygon),
    )


def _normalize_evidence(
    value: object,
    *,
    label: str,
    row_id: str,
    draft: Mapping[str, Any],
    valid_box_ids: set[str] | None,
) -> dict[str, list[str]]:
    data = _require_mapping(value, f"evidence in {label}")
    _reject_unknown(data, _EVIDENCE_FIELDS, f"evidence in {label}")
    normalized: dict[str, list[str]] = {}
    for field, raw_ids in data.items():
        if not isinstance(raw_ids, list) or not raw_ids or len(raw_ids) > 32:
            raise CorpusError(f"evidence for {field} in {label} must contain 1-32 box ids")
        ids: list[str] = []
        for raw_id in raw_ids:
            box_id = _require_id(raw_id, f"evidence box id for {field} in {label}")
            if valid_box_ids is not None and box_id not in valid_box_ids:
                raise CorpusError(f"evidence for {field} in {label} references unknown box_id {box_id}")
            if box_id not in ids:
                ids.append(box_id)
        normalized[field] = ids
    product_evidence = normalized.get("product_query")
    if not product_evidence:
        raise CorpusError(f"product_query evidence is required in {label}")
    if row_id != product_evidence[0]:
        raise CorpusError(f"row_id in {label} must equal the first product_query evidence box id")
    for field, field_value in draft.items():
        if field_value is not None and field not in normalized:
            raise CorpusError(f"evidence for non-null draft field {field} is required in {label}")
    for field in normalized:
        if field == "product_query":
            continue
        if field not in draft or draft[field] is None:
            raise CorpusError(f"evidence for unresolved draft field {field} is not allowed in {label}")
    return normalized


def normalize_rows(
    value: object,
    label: str = "rows",
    *,
    allow_empty: bool = False,
    valid_box_ids: set[str] | None = None,
) -> list[dict[str, Any]]:
    minimum = 0 if allow_empty else 1
    if not isinstance(value, list) or not minimum <= len(value) <= 24:
        raise CorpusError(f"{label} must contain between {minimum} and 24 medication rows")
    rows: list[dict[str, Any]] = []
    row_ids: set[str] = set()
    for index, raw in enumerate(value):
        data = _require_mapping(raw, f"{label}[{index}]")
        _reject_unknown(data, _ROW_FIELDS, f"{label}[{index}]")
        row_id = _require_id(data.get("row_id"), f"row_id in {label}[{index}]")
        if row_id in row_ids:
            raise CorpusError(f"row_id values must be unique in {label}")
        row_ids.add(row_id)
        product_query = str(data.get("product_query") or "").strip()
        if not product_query or len(product_query) > 256:
            raise CorpusError(f"product_query in {label}[{index}] must contain 1-256 characters")
        draft = _require_mapping(data.get("draft"), f"draft in {label}[{index}]")
        _reject_unknown(draft, DRAFT_FIELDS, f"draft in {label}[{index}]")
        issues = data.get("uncertainty_codes")
        if not isinstance(issues, list) or len(issues) > 16:
            raise CorpusError(f"uncertainty_codes in {label}[{index}] must be a list with at most 16 values")
        normalized_issues: list[str] = []
        for raw_issue in issues:
            issue = str(raw_issue or "").strip()
            if not _ISSUE_RE.fullmatch(issue):
                raise CorpusError(f"invalid uncertainty code in {label}[{index}]")
            if issue not in normalized_issues:
                normalized_issues.append(issue)
        evidence = _normalize_evidence(
            data.get("evidence"),
            label=f"{label}[{index}]",
            row_id=row_id,
            draft=draft,
            valid_box_ids=valid_box_ids,
        )
        rows.append(
            {
                "row_id": row_id,
                "product_query": product_query,
                "draft": dict(draft),
                "uncertainty_codes": normalized_issues,
                "evidence": evidence,
            }
        )
    return rows


def _case(value: object) -> CorpusCase:
    data = _require_mapping(value, "corpus case")
    _reject_unknown(data, _CASE_FIELDS, "corpus case")
    case_id = _require_id(data.get("case_id"), "case_id")
    if data.get("source_kind") != "synthetic":
        raise CorpusError(f"source_kind for {case_id} must be 'synthetic'")
    boxes_raw = data.get("boxes")
    if not isinstance(boxes_raw, list) or not boxes_raw:
        raise CorpusError(f"boxes for {case_id} must be a non-empty list")
    boxes = tuple(_box(raw, case_id) for raw in boxes_raw)
    box_ids = [box.box_id for box in boxes]
    if len(box_ids) != len(set(box_ids)):
        raise CorpusError(f"box_id values must be unique in {case_id}")
    rows = normalize_rows(
        data.get("expected_rows"),
        f"expected_rows in {case_id}",
        valid_box_ids=set(box_ids),
    )
    expected_rows = tuple(
        ExpectedRow(
            row_id=row["row_id"],
            product_query=row["product_query"],
            draft=row["draft"],
            uncertainty_codes=tuple(row["uncertainty_codes"]),
            evidence={key: tuple(value) for key, value in row["evidence"].items()},
        )
        for row in rows
    )
    return CorpusCase(
        case_id=case_id,
        source_kind="synthetic",
        scenario_tags=_require_tags(data.get("scenario_tags"), f"scenario_tags for {case_id}"),
        risk_tags=_require_tags(data.get("risk_tags"), f"risk_tags for {case_id}"),
        boxes=boxes,
        expected_rows=expected_rows,
    )


def _load_json(path: str | Path) -> Mapping[str, Any]:
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise CorpusError(f"could not read corpus {path}: {exc}") from exc
    return _require_mapping(value, "corpus manifest")


def load_corpus(path: str | Path) -> Corpus:
    data = _load_json(path)
    _reject_unknown(data, _CORPUS_FIELDS, "corpus manifest")
    if data.get("schema_version") != SCHEMA_VERSION:
        raise CorpusError(f"unsupported corpus schema_version: {data.get('schema_version')!r}")
    cases_raw = data.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        raise CorpusError("cases must be a non-empty list")
    cases = tuple(_case(raw) for raw in cases_raw)
    case_ids = [case.case_id for case in cases]
    if len(case_ids) != len(set(case_ids)):
        raise CorpusError("case_id values must be unique")
    return Corpus(schema_version=SCHEMA_VERSION, cases=cases)


def rows_as_dicts(rows: Iterable[ExpectedRow]) -> list[dict[str, Any]]:
    return [
        {
            "row_id": row.row_id,
            "product_query": row.product_query,
            "draft": dict(row.draft),
            "uncertainty_codes": list(row.uncertainty_codes),
            "evidence": {key: list(value) for key, value in row.evidence.items()},
        }
        for row in rows
    ]


__all__ = [
    "Corpus",
    "CorpusCase",
    "CorpusError",
    "DRAFT_FIELDS",
    "ExpectedRow",
    "OcrBox",
    "SCHEMA_VERSION",
    "load_corpus",
    "normalize_rows",
    "rows_as_dicts",
]