from __future__ import annotations

import math
from typing import Any, Mapping, Sequence


PARSER_V5_SCHEMA_VERSION = 5
SEMANTIC_ROLES = {
    "product",
    "dose",
    "frequency",
    "duration",
    "instruction",
    "schedule",
    "header",
    "context",
    "other",
}


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be an object")
    return value


def _require_nonempty_text(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{label} must be non-empty")
    return text


def _validate_polygon(value: object, *, width: int, height: int, label: str) -> None:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError(f"{label} must contain four points")
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError(f"{label} point must contain x/y")
        x, y = point
        if isinstance(x, bool) or isinstance(y, bool) or not isinstance(x, (int, float)) or not isinstance(y, (int, float)):
            raise ValueError(f"{label} coordinates must be numeric")
        if not math.isfinite(float(x)) or not math.isfinite(float(y)):
            raise ValueError(f"{label} coordinates must be finite")
        if not 0.0 <= float(x) <= width or not 0.0 <= float(y) <= height:
            raise ValueError(f"{label} must stay inside the document")


def validate_parser_v5_document(document: Mapping[str, Any]) -> None:
    doc = _require_mapping(document, "parser v5 document")
    if doc.get("schema_version") != PARSER_V5_SCHEMA_VERSION:
        raise ValueError("parser v5 document schema_version must be 5")
    _require_nonempty_text(doc.get("document_id"), "parser v5 document_id")
    width = doc.get("width")
    height = doc.get("height")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ValueError("parser v5 width must be a positive integer")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise ValueError("parser v5 height must be a positive integer")
    for forbidden in ("layout_family", "template_id", "layout_id"):
        if forbidden in doc:
            raise ValueError(f"parser v5 document must not expose {forbidden}")

    sections = doc.get("sections")
    medications = doc.get("medications")
    spans = doc.get("spans")
    if not isinstance(sections, list) or not isinstance(medications, list) or not isinstance(spans, list):
        raise ValueError("parser v5 sections, medications and spans must be lists")

    section_ids: set[str] = set()
    for index, raw in enumerate(sections):
        section = _require_mapping(raw, f"parser v5 section {index}")
        section_id = _require_nonempty_text(section.get("section_id"), f"parser v5 section {index}.section_id")
        if section_id in section_ids:
            raise ValueError("parser v5 section ids must be unique")
        section_ids.add(section_id)
        _require_nonempty_text(section.get("kind"), f"parser v5 section {section_id}.kind")

    medication_ids: set[str] = set()
    for index, raw in enumerate(medications):
        medication = _require_mapping(raw, f"parser v5 medication {index}")
        medication_id = _require_nonempty_text(
            medication.get("medication_id"), f"parser v5 medication {index}.medication_id"
        )
        if medication_id in medication_ids:
            raise ValueError("parser v5 medication ids must be unique")
        medication_ids.add(medication_id)
        _require_nonempty_text(medication.get("product_name"), f"parser v5 medication {medication_id}.product_name")

    span_ids: set[str] = set()
    reading_orders: set[int] = set()
    for index, raw in enumerate(spans):
        span = _require_mapping(raw, f"parser v5 span {index}")
        span_id = _require_nonempty_text(span.get("span_id"), f"parser v5 span {index}.span_id")
        if span_id in span_ids:
            raise ValueError("parser v5 span ids must be unique")
        span_ids.add(span_id)
        section_id = _require_nonempty_text(span.get("section_id"), f"parser v5 span {span_id}.section_id")
        if section_id not in section_ids:
            raise ValueError(f"parser v5 span {span_id} references unknown section")
        _require_nonempty_text(span.get("text"), f"parser v5 span {span_id}.text")
        role = span.get("semantic_role")
        if role not in SEMANTIC_ROLES:
            raise ValueError(f"parser v5 span {span_id} semantic_role is unsupported")
        group = span.get("association_group")
        if group is not None and group not in medication_ids:
            raise ValueError(f"parser v5 span {span_id} references unknown medication")
        if role == "product" and group is None:
            raise ValueError(f"parser v5 product span {span_id} must belong to a medication")
        order = span.get("reading_order")
        if isinstance(order, bool) or not isinstance(order, int) or order < 0 or order in reading_orders:
            raise ValueError("parser v5 span reading_order values must be unique non-negative integers")
        reading_orders.add(order)
        _validate_polygon(span.get("polygon"), width=width, height=height, label=f"parser v5 span {span_id}.polygon")


def validate_parser_v5_observation(observation: Mapping[str, Any]) -> None:
    value = _require_mapping(observation, "parser v5 observation")
    _require_nonempty_text(value.get("document_id"), "parser v5 observation document_id")
    if value.get("profile_revision") != 1:
        raise ValueError("parser v5 observation profile_revision must be 1")
    nodes = value.get("nodes")
    if not isinstance(nodes, list):
        raise ValueError("parser v5 observation nodes must be a list")
    node_ids: set[str] = set()
    for index, raw in enumerate(nodes):
        node = _require_mapping(raw, f"parser v5 observation node {index}")
        node_id = _require_nonempty_text(node.get("node_id"), f"parser v5 observation node {index}.node_id")
        if node_id in node_ids:
            raise ValueError("parser v5 observation node ids must be unique")
        node_ids.add(node_id)
        _require_nonempty_text(node.get("text"), f"parser v5 observation node {node_id}.text")
        source_span_ids = node.get("source_span_ids")
        targets = node.get("targets")
        if not isinstance(source_span_ids, list) or not isinstance(targets, list):
            raise ValueError(f"parser v5 observation node {node_id} provenance must use lists")
        for score_name in ("detector_confidence", "recognizer_confidence"):
            score = node.get(score_name)
            if isinstance(score, bool) or not isinstance(score, (int, float)) or not 0.0 <= float(score) <= 1.0:
                raise ValueError(f"parser v5 observation node {node_id} {score_name} must be in [0, 1]")
        for target in targets:
            target_map = _require_mapping(target, f"parser v5 observation node {node_id} target")
            if target_map.get("semantic_role") not in SEMANTIC_ROLES:
                raise ValueError(f"parser v5 observation node {node_id} target role is unsupported")
            if target_map.get("label_status") not in {"labeled", "ambiguous"}:
                raise ValueError(f"parser v5 observation node {node_id} target label_status is unsupported")


def validate_parser_v5_pair(document: Mapping[str, Any], observation: Mapping[str, Any]) -> None:
    validate_parser_v5_document(document)
    validate_parser_v5_observation(observation)
    if observation.get("document_id") != document.get("document_id"):
        raise ValueError("parser v5 observation document_id does not match semantic truth")
    spans = {str(span["span_id"]): span for span in document["spans"]}
    for node in observation["nodes"]:
        node_id = str(node["node_id"])
        source_ids = [str(source_id) for source_id in node["source_span_ids"]]
        if len(source_ids) != len(set(source_ids)):
            raise ValueError(f"parser v5 observation node {node_id} source_span_ids must be unique")
        targets = node["targets"]
        target_ids = [str(target["source_span_id"]) for target in targets]
        if len(target_ids) != len(set(target_ids)):
            raise ValueError(f"parser v5 observation node {node_id} targets must be unique by source span")
        if set(target_ids) != set(source_ids):
            raise ValueError(f"parser v5 observation node {node_id} targets disagree with provenance")
        for target in targets:
            source_id = str(target["source_span_id"])
            source = spans.get(source_id)
            if source is None:
                raise ValueError(f"parser v5 observation node {node_id} references unknown source span")
            if target.get("semantic_role") != source.get("semantic_role"):
                raise ValueError(f"parser v5 observation node {node_id} target role disagrees with semantic truth")
            if target.get("association_group") != source.get("association_group"):
                raise ValueError(f"parser v5 observation node {node_id} target association disagrees with semantic truth")


__all__ = [
    "PARSER_V5_SCHEMA_VERSION",
    "SEMANTIC_ROLES",
    "validate_parser_v5_document",
    "validate_parser_v5_observation",
    "validate_parser_v5_pair",
]