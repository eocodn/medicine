from __future__ import annotations

import hashlib
import random
from dataclasses import dataclass
from typing import Any

from .parser_v5_contract import PARSER_V5_SCHEMA_VERSION, validate_parser_v5_document
from .parser_v5_semantic_grammar import (
    PRODUCT_VOCABULARIES,
    WORDING_VOCABULARIES,
    sample_distractor_texts,
    sample_medication_fields,
    sample_product_name,
)


_DISTRACTOR_KINDS = (
    "patient",
    "clinic",
    "receipt",
    "billing",
    "header",
    "warning",
    "storage",
    "legal",
    "general_context",
)


@dataclass(frozen=True)
class ParserWorldProfile:
    medication_count: tuple[int, int] = (0, 5)
    distractor_section_count: tuple[int, int] = (1, 6)
    width: int = 1200
    height: int = 1800
    counterfactual_context_rate: float = 0.0
    geometry_scramble_rate: float = 0.0
    product_vocabulary: str = "train"
    wording_vocabulary: str = "train"

    def __post_init__(self) -> None:
        _validate_range(self.medication_count, "medication_count")
        _validate_range(self.distractor_section_count, "distractor_section_count")
        if self.width < 600 or self.height < 800:
            raise ValueError("parser world page dimensions are too small")
        for name in ("counterfactual_context_rate", "geometry_scramble_rate"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0.0 <= float(value) <= 1.0:
                raise ValueError(f"parser world {name} must be in [0, 1]")
        if self.product_vocabulary not in PRODUCT_VOCABULARIES:
            raise ValueError("parser world product_vocabulary is unsupported")
        if self.wording_vocabulary not in WORDING_VOCABULARIES:
            raise ValueError("parser world wording_vocabulary is unsupported")


def _validate_range(value: tuple[int, int], label: str) -> None:
    if len(value) != 2 or any(isinstance(item, bool) or not isinstance(item, int) for item in value):
        raise ValueError(f"{label} must contain two integers")
    if value[0] < 0 or value[1] < value[0]:
        raise ValueError(f"{label} must be a non-negative inclusive range")


def _rng(seed: int, document_index: int) -> random.Random:
    digest = hashlib.sha256(f"parser-v5-world:{seed}:{document_index}".encode("utf-8")).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def _rect(x: float, y: float, width: float, height: float) -> list[list[float]]:
    return [[x, y], [x + width, y], [x + width, y + height], [x, y + height]]


def _span(
    *,
    span_id: str,
    section_id: str,
    text: str,
    semantic_role: str,
    association_group: str | None,
    x: float,
    y: float,
    width: float,
    height: float = 34.0,
) -> dict[str, Any]:
    return {
        "span_id": span_id,
        "section_id": section_id,
        "text": text,
        "semantic_role": semantic_role,
        "association_group": association_group,
        "polygon": _rect(x, y, width, height),
    }


def _build_medication_spans(
    *,
    rng: random.Random,
    section_id: str,
    medication_ids: list[str],
    y1: float,
    y2: float,
    page_width: int,
    product_vocabulary: str,
    wording_vocabulary: str,
) -> list[dict[str, Any]]:
    if not medication_ids:
        return []
    row_height = max(54.0, min(118.0, (y2 - y1 - 16.0) / len(medication_ids)))
    x_margin = 52.0 + rng.uniform(0.0, 38.0)
    usable = page_width - x_margin * 2.0
    columns = (0.00, 0.32, 0.48, 0.64, 0.79, 0.90)
    roles = ("product", "dose", "frequency", "duration", "instruction", "schedule")
    widths = (0.29, 0.13, 0.14, 0.12, 0.16, 0.09)
    spans: list[dict[str, Any]] = []
    for row_index, medication_id in enumerate(medication_ids):
        y = y1 + 10.0 + row_index * row_height + rng.uniform(-3.0, 3.0)
        wording = sample_medication_fields(rng, wording_vocabulary)
        values = (
            sample_product_name(rng, product_vocabulary),
            wording["dose"],
            wording["frequency"],
            wording["duration"],
            wording["instruction"],
            wording["schedule"],
        )
        for role, value, column, width_fraction in zip(roles, values, columns, widths):
            spans.append(
                _span(
                    span_id=f"span-{medication_id}-{role}",
                    section_id=section_id,
                    text=value,
                    semantic_role=role,
                    association_group=medication_id,
                    x=x_margin + usable * column,
                    y=y,
                    width=max(54.0, usable * width_fraction - 10.0),
                )
            )
    return spans


def _build_distractor_spans(
    *,
    rng: random.Random,
    section_id: str,
    kind: str,
    y1: float,
    y2: float,
    page_width: int,
    wording_vocabulary: str,
) -> list[dict[str, Any]]:
    texts = list(sample_distractor_texts(rng, kind, wording_vocabulary))
    rng.shuffle(texts)
    count = 1 if y2 - y1 < 90 else rng.randint(1, len(texts))
    role = "header" if kind == "header" else "context"
    spans: list[dict[str, Any]] = []
    for index, text in enumerate(texts[:count]):
        x = rng.uniform(50.0, min(280.0, page_width * 0.30))
        y = y1 + 12.0 + index * 42.0
        max_width = page_width - x - 52.0
        width = min(max_width, max(160.0, 14.0 * len(text)))
        spans.append(
            _span(
                span_id=f"span-{section_id}-{index + 1:02d}",
                section_id=section_id,
                text=text,
                semantic_role=role,
                association_group=None,
                x=x,
                y=min(y, y2 - 38.0),
                width=width,
            )
        )
    return spans


def _inject_counterfactual_context(
    spans: list[dict[str, Any]],
    sections: list[dict[str, Any]],
    *,
    rng: random.Random,
    rate: float,
    page_width: int,
    page_height: int,
) -> None:
    if rate <= 0.0:
        return
    medication_spans = [span for span in spans if span.get("association_group") is not None]
    selected = [span for span in medication_spans if rng.random() < rate]
    if not selected:
        return
    section_id = f"section-{len(sections) + 1:02d}"
    sections.append({"section_id": section_id, "kind": "general_context"})
    for index, source in enumerate(selected, start=1):
        width = min(max(90.0, 14.0 * len(str(source["text"]))), page_width * 0.45)
        height = 34.0
        x = rng.uniform(30.0, max(30.0, page_width - width - 30.0))
        y = rng.uniform(30.0, max(30.0, page_height - height - 30.0))
        spans.append(_span(
            span_id=f"span-{section_id}-cf-{index:03d}",
            section_id=section_id,
            text=str(source["text"]),
            semantic_role="context",
            association_group=None,
            x=x,
            y=y,
            width=width,
            height=height,
        ))


def _scramble_geometry(
    spans: list[dict[str, Any]],
    *,
    rng: random.Random,
    rate: float,
    page_width: int,
    page_height: int,
) -> None:
    if rate <= 0.0:
        return
    grouped: dict[str, list[dict[str, Any]]] = {}
    ungrouped: list[dict[str, Any]] = []
    for span in spans:
        group = span.get("association_group")
        if group is None:
            ungrouped.append(span)
        else:
            grouped.setdefault(str(group), []).append(span)

    selected_groups = [group for group in sorted(grouped) if rng.random() < rate]
    row_centers = [
        page_height * (index + 1) / (len(selected_groups) + 1)
        for index in range(len(selected_groups))
    ]
    rng.shuffle(row_centers)
    for group, center_y in zip(selected_groups, row_centers, strict=True):
        members = grouped[group]
        x_slots = [float(span["polygon"][0][0]) for span in members]
        shuffled_slots = list(x_slots)
        rng.shuffle(shuffled_slots)
        if len(shuffled_slots) > 1 and shuffled_slots == x_slots:
            shuffled_slots = shuffled_slots[1:] + shuffled_slots[:1]
        for span, x in zip(members, shuffled_slots, strict=True):
            x1, y1 = span["polygon"][0]
            x2, y2 = span["polygon"][2]
            width = max(1.0, float(x2) - float(x1))
            height = max(1.0, float(y2) - float(y1))
            nx = max(0.0, min(float(x), page_width - width))
            ny = max(0.0, min(center_y - height / 2.0 + rng.uniform(-2.0, 2.0), page_height - height))
            span["polygon"] = _rect(nx, ny, width, height)

    for span in ungrouped:
        if rng.random() >= rate:
            continue
        x1, y1 = span["polygon"][0]
        x2, y2 = span["polygon"][2]
        width = max(1.0, float(x2) - float(x1))
        height = max(1.0, float(y2) - float(y1))
        x = rng.uniform(0.0, max(0.0, page_width - width))
        y = rng.uniform(0.0, max(0.0, page_height - height))
        span["polygon"] = _rect(x, y, width, height)


def generate_parser_world(
    *,
    seed: int,
    document_index: int,
    profile: ParserWorldProfile | None = None,
) -> dict[str, Any]:
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("parser world seed must be an integer")
    if isinstance(document_index, bool) or not isinstance(document_index, int) or document_index < 0:
        raise ValueError("parser world document_index must be a non-negative integer")
    selected = profile or ParserWorldProfile()
    rng = _rng(seed, document_index)
    medication_count = rng.randint(*selected.medication_count)
    distractor_count = rng.randint(*selected.distractor_section_count)
    medication_ids = [f"med-{index + 1:02d}" for index in range(medication_count)]

    kinds = [rng.choice(_DISTRACTOR_KINDS) for _ in range(distractor_count)]
    if medication_count:
        kinds.append("medications")
    rng.shuffle(kinds)

    sections: list[dict[str, Any]] = []
    spans: list[dict[str, Any]] = []
    top = 54.0
    bottom = selected.height - 54.0
    section_height = (bottom - top) / max(1, len(kinds))
    medication_section_seen = False
    for section_index, kind in enumerate(kinds):
        section_id = f"section-{section_index + 1:02d}"
        y1 = top + section_index * section_height
        y2 = top + (section_index + 1) * section_height
        sections.append({"section_id": section_id, "kind": kind})
        if kind == "medications" and not medication_section_seen:
            medication_section_seen = True
            spans.extend(
                _build_medication_spans(
                    rng=rng,
                    section_id=section_id,
                    medication_ids=medication_ids,
                    y1=y1,
                    y2=y2,
                    page_width=selected.width,
                    product_vocabulary=selected.product_vocabulary,
                    wording_vocabulary=selected.wording_vocabulary,
                )
            )
        else:
            spans.extend(
                _build_distractor_spans(
                    rng=rng,
                    section_id=section_id,
                    kind=kind,
                    y1=y1,
                    y2=y2,
                    page_width=selected.width,
                    wording_vocabulary=selected.wording_vocabulary,
                )
            )

    _inject_counterfactual_context(
        spans,
        sections,
        rng=rng,
        rate=float(selected.counterfactual_context_rate),
        page_width=selected.width,
        page_height=selected.height,
    )
    _scramble_geometry(
        spans,
        rng=rng,
        rate=float(selected.geometry_scramble_rate),
        page_width=selected.width,
        page_height=selected.height,
    )
    spans.sort(key=lambda span: (span["polygon"][0][1], span["polygon"][0][0], span["span_id"]))
    for reading_order, span in enumerate(spans):
        span["reading_order"] = reading_order

    product_by_medication = {
        span["association_group"]: span["text"]
        for span in spans
        if span["semantic_role"] == "product" and span["association_group"] is not None
    }
    document = {
        "schema_version": PARSER_V5_SCHEMA_VERSION,
        "document_id": f"parser-v5-{seed}-{document_index:06d}",
        "width": selected.width,
        "height": selected.height,
        "sections": sections,
        "medications": [
            {"medication_id": medication_id, "product_name": product_by_medication[medication_id]}
            for medication_id in medication_ids
        ],
        "spans": spans,
    }
    validate_parser_v5_document(document)
    return document


__all__ = ["ParserWorldProfile", "generate_parser_world"]