from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Iterable, Sequence

from .contract import OcrBox


_STRUCTURED_CONFIDENCE_FLOOR = 0.05


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


__all__ = ["_Line", "_Token", "_document_lines"]
