from __future__ import annotations

import math
import re
from dataclasses import dataclass
from statistics import median
from typing import Any, Iterable, Sequence

from .contract import SCHEMA_VERSION, Corpus, OcrBox
from .evaluation import evaluate_corpus


BASELINE_ID = "geometry_rule_v1"
_STRUCTURED_CONFIDENCE_FLOOR = 0.05
_HEADER_PRODUCT = re.compile(r"^(?:약품명|의약품명|제품명|처방의약품명|약명)$")
_HEADER_DOSE = re.compile(r"(?:1회.*(?:투약|투여|복용).*(?:량|용량)|1회량)")
_HEADER_FREQUENCY = re.compile(r"(?:1일.*(?:투약|투여|복용).*(?:횟수|회수)|1일횟수)")
_HEADER_DAYS = re.compile(r"(?:총.*(?:투약|투여|복용).*일수|(?:투약|투여|복용)일수)")
_PRODUCT_PREFIX = re.compile(r"^(?:약명|제품명|약품명|의약품명)[:：](.+)$")
_COMMON_REGIMEN = re.compile(r"^공통(?:복용법|용법|복약방법)[:：]")
_EXPLICIT_REGIMEN = re.compile(
    r"(?:복용법|용법|복약방법|복용량|투약량|투여량|복용횟수|투약횟수|투여횟수|복용일수|투약일수|투여일수)[:：]"
)
_DOSE = re.compile(r"(?:^|\s)(\d+(?:\.\d+)?)\s*(정|tablet|캡슐|capsule|포|mL|ml)(?=\s|$)", re.I)
_FREQUENCY = re.compile(r"(?:1\s*일|하루)\s*(\d+)\s*회")
_DURATION = re.compile(r"(\d+)\s*일(?!\s*\d+\s*회)")
_TABLE_DOSE = re.compile(r"^(\d+(?:\.\d+)?)\s*(정|tablet|캡슐|capsule|포|mL|ml)$", re.I)
_TABLE_FREQUENCY = re.compile(r"^(\d+)\s*회$")
_TABLE_DAYS = re.compile(r"^(\d+)\s*일$")


@dataclass(frozen=True)
class _Token:
    box_id: str
    text: str
    confidence: float
    points: tuple[tuple[float, float], ...]
    x1: float
    x2: float
    cx: float
    cy: float
    height: float


@dataclass
class _Line:
    items: list[_Token]
    cy: float
    height: float

    @property
    def text(self) -> str:
        return " ".join(item.text for item in self.items)

    @property
    def compact(self) -> str:
        return "".join(item.text.replace(" ", "") for item in self.items)


def _raw_token(box: OcrBox) -> _Token | None:
    points = tuple((float(x), float(y)) for x, y in box.polygon)
    if len(points) != 4 or any(not math.isfinite(value) for point in points for value in point):
        return None
    xs = [point[0] for point in points]
    ys = [point[1] for point in points]
    x1, x2 = min(xs), max(xs)
    y1, y2 = min(ys), max(ys)
    if x2 <= x1 or y2 <= y1:
        return None
    return _Token(
        box_id=box.box_id,
        text=box.text.strip(),
        confidence=box.confidence,
        points=points,
        x1=x1,
        x2=x2,
        cx=(x1 + x2) / 2,
        cy=(y1 + y2) / 2,
        height=y2 - y1,
    )


def _estimated_page_slope(tokens: Sequence[_Token]) -> float:
    slopes: list[float] = []
    for token in tokens:
        for left_index, right_index in ((0, 1), (3, 2)):
            left = token.points[left_index]
            right = token.points[right_index]
            dx = right[0] - left[0]
            if abs(dx) < 1:
                continue
            slope = (right[1] - left[1]) / dx
            if math.isfinite(slope) and abs(slope) <= 0.35:
                slopes.append(slope)
    return float(median(slopes)) if len(slopes) >= 2 else 0.0


def _document_lines(boxes: Iterable[OcrBox]) -> tuple[list[_Line], float, bool]:
    raw = [token for box in boxes if (token := _raw_token(box)) is not None and token.text]
    low_confidence = any(token.confidence < _STRUCTURED_CONFIDENCE_FLOOR for token in raw)
    raw = [token for token in raw if token.confidence >= _STRUCTURED_CONFIDENCE_FLOOR]
    slope = _estimated_page_slope(raw)
    tokens: list[_Token] = []
    for token in raw:
        deskewed_y = [y - slope * x for x, y in token.points]
        y1, y2 = min(deskewed_y), max(deskewed_y)
        tokens.append(
            _Token(
                box_id=token.box_id,
                text=token.text,
                confidence=token.confidence,
                points=token.points,
                x1=token.x1,
                x2=token.x2,
                cx=token.cx,
                cy=(y1 + y2) / 2,
                height=y2 - y1,
            )
        )
    tokens.sort(key=lambda item: (item.cy, item.x1))
    median_height = float(median([token.height for token in tokens])) if tokens else 20.0
    lines: list[_Line] = []
    for token in tokens:
        target = next(
            (
                line
                for line in lines
                if abs(line.cy - token.cy) <= max(median_height * 0.65, min(line.height, token.height) * 0.7)
            ),
            None,
        )
        if target is None:
            target = _Line(items=[], cy=token.cy, height=token.height)
            lines.append(target)
        target.items.append(token)
        target.items.sort(key=lambda item: item.x1)
        target.cy = sum(item.cy for item in target.items) / len(target.items)
        target.height = max(item.height for item in target.items)
    lines.sort(key=lambda line: line.cy)
    return lines, median_height, low_confidence


def _header_key(value: str) -> str | None:
    text = value.replace(" ", "")
    if _HEADER_PRODUCT.fullmatch(text):
        return "product"
    if _HEADER_DOSE.search(text):
        return "dose"
    if _HEADER_FREQUENCY.search(text):
        return "frequency"
    if _HEADER_DAYS.search(text):
        return "days"
    return None


def _header_anchors(line: _Line) -> list[tuple[str, float]]:
    anchors: list[tuple[str, float]] = []
    index = 0
    while index < len(line.items):
        match: tuple[str, float, int] | None = None
        for width in range(1, min(3, len(line.items) - index) + 1):
            group = line.items[index : index + width]
            key = _header_key("".join(item.text for item in group))
            if key is not None:
                match = (key, (group[0].x1 + group[-1].x2) / 2, width)
                break
        if match is None:
            index += 1
            continue
        anchors.append((match[0], match[1]))
        index += match[2]
    return anchors


def _unit(value: str) -> str:
    lowered = value.lower()
    if lowered in {"정", "tablet"}:
        return "tablet"
    if lowered in {"캡슐", "capsule"}:
        return "capsule"
    if lowered == "포":
        return "packet"
    if lowered == "ml":
        return "mL"
    return value


def _parse_regimen(value: str) -> dict[str, Any]:
    text = value.strip()
    result: dict[str, Any] = {}
    dose = _DOSE.search(text)
    if dose:
        amount = float(dose.group(1))
        result["dose_amount"] = int(amount) if amount.is_integer() else amount
        result["dose_unit"] = _unit(dose.group(2))
    frequency = _FREQUENCY.search(text)
    if frequency:
        result["frequency_per_day"] = int(frequency.group(1))
    duration_matches = list(_DURATION.finditer(text))
    if duration_matches:
        result["prescription_days"] = int(duration_matches[-1].group(1))
    return result


def _table_value(key: str, value: str) -> dict[str, Any]:
    compact = value.replace(" ", "")
    if key == "dose" and (match := _TABLE_DOSE.fullmatch(compact)):
        amount = float(match.group(1))
        return {
            "dose_amount": int(amount) if amount.is_integer() else amount,
            "dose_unit": _unit(match.group(2)),
        }
    if key == "frequency" and (match := _TABLE_FREQUENCY.fullmatch(compact)):
        return {"frequency_per_day": int(match.group(1))}
    if key == "days" and (match := _TABLE_DAYS.fullmatch(compact)):
        return {"prescription_days": int(match.group(1))}
    return {}


def _clean_product(value: str) -> str:
    compact = value.replace(" ", "").strip()
    compact = re.sub(r"^(?:약명|제품명|약품명|의약품명)[:：]", "", compact)
    compact = re.sub(r"^[0-9]+[.)]", "", compact)
    return compact.strip()


def _new_row(product_query: str, product_evidence: Sequence[str]) -> dict[str, Any]:
    if not product_evidence:
        raise ValueError("product evidence is required")
    return {
        "row_id": product_evidence[0],
        "product_query": product_query,
        "draft": {},
        "uncertainty_codes": [],
        "evidence": {"product_query": list(product_evidence)},
    }


def _table_rows(lines: Sequence[_Line]) -> tuple[list[dict[str, Any]], set[int]]:
    for header_index, header in enumerate(lines):
        anchors = _header_anchors(header)
        keys = {key for key, _ in anchors}
        if "product" not in keys or len(keys) < 3:
            continue
        anchors.sort(key=lambda item: item[1])
        boundaries = [(anchors[index][1] + anchors[index + 1][1]) / 2 for index in range(len(anchors) - 1)]
        rows: list[dict[str, Any]] = []
        consumed = {header_index}
        for line_index in range(header_index + 1, len(lines)):
            line = lines[line_index]
            if _header_anchors(line):
                break
            cells: dict[str, list[_Token]] = {}
            for item in line.items:
                column = next((index for index, boundary in enumerate(boundaries) if item.cx < boundary), len(anchors) - 1)
                key = anchors[column][0]
                cells.setdefault(key, []).append(item)
            product_items = cells.get("product", [])
            product = _clean_product("".join(item.text for item in product_items))
            if not product or not any(key in cells for key in ("dose", "frequency", "days")):
                continue
            row = _new_row(product, [item.box_id for item in product_items])
            for key in ("dose", "frequency", "days"):
                if key in cells:
                    items = cells[key]
                    parsed = _table_value(key, "".join(item.text for item in items))
                    row["draft"].update(parsed)
                    evidence = [item.box_id for item in items]
                    for field in parsed:
                        row["evidence"][field] = evidence
            rows.append(row)
            consumed.add(line_index)
        if rows:
            return rows, consumed
    return [], set()


def _labeled_product(line: _Line) -> tuple[str, list[str]] | None:
    match = _PRODUCT_PREFIX.fullmatch(line.compact)
    if match is None:
        return None
    product = _clean_product(match.group(1))
    return (product, [item.box_id for item in line.items]) if product else None


def _is_common_regimen(line: _Line) -> bool:
    return _COMMON_REGIMEN.match(line.compact) is not None


def _is_explicit_regimen(line: _Line) -> bool:
    return _EXPLICIT_REGIMEN.search(line.compact) is not None and not _is_common_regimen(line)


def _labeled_rows(lines: Sequence[_Line], median_height: float) -> tuple[list[dict[str, Any]], set[int]]:
    product_lines = [
        (index, product, evidence)
        for index, line in enumerate(lines)
        if (found := _labeled_product(line)) is not None
        for product, evidence in [found]
    ]
    if not product_lines:
        return [], set()
    rows = [_new_row(product, evidence) for _, product, evidence in product_lines]
    consumed: set[int] = {line_index for line_index, _, _ in product_lines}
    common_indexes = {index for index, line in enumerate(lines) if _is_common_regimen(line)}

    for row_index, (line_index, _, _) in enumerate(product_lines):
        next_product = product_lines[row_index + 1][0] if row_index + 1 < len(product_lines) else len(lines)
        previous_cy = lines[line_index].cy
        for candidate_index in range(line_index + 1, next_product):
            if candidate_index in common_indexes:
                break
            line = lines[candidate_index]
            if line.cy - previous_cy > max(55.0, median_height * 2.8):
                break
            previous_cy = line.cy
            if not _is_explicit_regimen(line):
                continue
            fields = _parse_regimen(line.text)
            if fields:
                rows[row_index]["draft"].update(fields)
                evidence = [item.box_id for item in line.items]
                for field in fields:
                    rows[row_index]["evidence"][field] = evidence
                consumed.add(candidate_index)

    for common_index in sorted(common_indexes):
        fields = _parse_regimen(lines[common_index].text)
        if not fields:
            continue
        members: list[int] = []
        cursor = common_index - 1
        lower_cy = lines[common_index].cy
        while cursor >= 0:
            line = lines[cursor]
            if lower_cy - line.cy > max(55.0, median_height * 2.8):
                break
            marker = next(
                (index for index, (product_index, _, _) in enumerate(product_lines) if product_index == cursor),
                None,
            )
            if marker is None:
                break
            members.insert(0, marker)
            lower_cy = line.cy
            cursor -= 1
        if not members:
            continue
        common_evidence = [item.box_id for item in lines[common_index].items]
        for row_index in members:
            for key, value in fields.items():
                if key not in rows[row_index]["draft"]:
                    rows[row_index]["draft"][key] = value
                    rows[row_index]["evidence"][key] = common_evidence
        consumed.add(common_index)

    if len(rows) > 1:
        unassociated = False
        for index, line in enumerate(lines):
            if index in consumed or _labeled_product(line) is not None or _is_common_regimen(line):
                continue
            if _parse_regimen(line.text):
                unassociated = True
                break
        if unassociated:
            for row in rows:
                row["uncertainty_codes"].append("UNRESOLVED_REGIMEN_ASSOCIATION")
    return rows, consumed


def parse_boxes(boxes: Iterable[OcrBox]) -> list[dict[str, Any]]:
    lines, median_height, low_confidence = _document_lines(boxes)
    rows, _ = _table_rows(lines)
    if not rows:
        rows, _ = _labeled_rows(lines, median_height)
    if low_confidence:
        for row in rows:
            if "LOW_CONFIDENCE_OCR" not in row["uncertainty_codes"]:
                row["uncertainty_codes"].append("LOW_CONFIDENCE_OCR")
    return rows[:24]


def run_baseline(corpus: Corpus) -> dict[str, Any]:
    predictions = {
        "schema_version": SCHEMA_VERSION,
        "predictions": [
            {"case_id": case.case_id, "rows": parse_boxes(case.boxes)}
            for case in corpus.cases
        ],
    }
    return {
        "status": "ok",
        "baseline": BASELINE_ID,
        "predictions": predictions,
        "evaluation": evaluate_corpus(corpus, predictions),
    }


__all__ = ["BASELINE_ID", "parse_boxes", "run_baseline"]