from __future__ import annotations

import re
from collections.abc import Iterable


def dosage_form_tags(value: object) -> set[str]:
    """Return only conservative, route/form tags supported by runtime decisions."""
    text = str(value or "").strip()
    if not text:
        return set()
    tags: set[str] = set()

    if "점안" in text:
        tags.add("점안제")
    if "점이" in text:
        tags.add("점이제")
    if "점비" in text:
        tags.add("점비제")
    if "주사" in text or "수액" in text:
        tags.add("주사제")
    if "흡입" in text:
        tags.add("흡입제")
    if "크림" in text:
        tags.add("크림제")
    if "연고" in text:
        tags.add("연고제")
    if "로션" in text:
        tags.add("로션제")
    if "겔" in text:
        tags.add("겔제")
    if "피부액" in text or "외용액" in text:
        tags.add("외용액제")
    if "좌제" in text:
        tags.add("좌제")
    if "경피흡수" in text:
        tags.add("경피흡수제")
    if "첩부" in text or "카타플라스마" in text:
        tags.add("첩부제")

    if "구강정" in text:
        tags.add("구강정")
    if "박칼정" in text:
        tags.add("박칼정")
    if "설하정" in text:
        tags.add("설하정")
    if "구강붕해필름" in text or "구강용해필름" in text:
        tags.add("구강붕해필름")

    if "캡슐" in text:
        tags.add("캡슐제")
    if re.search(r"(?:^|[,\s(])정제(?:$|[,\s)])", text) or any(
        marker in text for marker in (
            "필름코팅정", "나정", "서방정", "장용정", "구강붕해정", "다층정",
            "저작정", "츄어블정", "당의정", "발포정",
        )
    ):
        tags.add("정제")
    if "시럽" in text:
        tags.add("시럽제")
    if "과립" in text:
        tags.add("과립제")
    if "세립" in text:
        tags.add("세립제")
    if "산제" in text and not any(marker in text for marker in ("외용", "피부", "주사")):
        tags.add("산제")
    if "액제" in text and not any(
        marker in text for marker in ("점안", "점이", "점비", "주사", "피부", "외용")
    ):
        tags.add("액제")
    if "경구" in text:
        tags.add("액제" if "액" in text else "경구제")
    return tags


def infer_administration_route(forms: Iterable[object]) -> str:
    """Infer a route only when every authoritative form points to one route."""
    resolved: set[str] = set()
    saw_form = False
    for raw in forms:
        text = str(raw or "").strip()
        if not text:
            continue
        saw_form = True
        tags = dosage_form_tags(text)
        route: str | None = None
        if "주사제" in tags:
            route = "injection"
        elif "점안제" in tags:
            route = "ophthalmic"
        elif "점이제" in tags:
            route = "otic"
        elif "점비제" in tags:
            route = "nasal"
        elif "흡입제" in tags:
            route = "inhaled"
        elif tags & {"크림제", "연고제", "로션제", "겔제", "외용액제", "경피흡수제", "첩부제"}:
            route = "topical"
        elif tags & {"정제", "캡슐제", "시럽제", "과립제", "세립제", "산제", "액제", "경구제"}:
            route = "oral"
        if route is None:
            return "unknown"
        resolved.add(route)
    return next(iter(resolved)) if saw_form and len(resolved) == 1 else "unknown"


__all__ = ["dosage_form_tags", "infer_administration_route"]