from __future__ import annotations

import re
import unicodedata


# MFDS uses slash-like separators between alternative dosage forms. A comma is
# part of one authoritative form label (for example ``정량흡입제, 분말제`` or
# ``경질캡슐제, 산제``), so splitting on commas would erase a material subtype.
_SEPARATOR_RE = re.compile(r"[/;|]+")


def _normalize(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", "", text)


def _tokens(value: object) -> tuple[str, ...]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return ()
    values = tuple(_normalize(part) for part in _SEPARATOR_RE.split(text) if _normalize(part))
    return values or (_normalize(text),)


def _generic_family(token: str) -> str | None:
    """Return a family only for deliberately broad official form labels.

    Precise MFDS labels remain exact. ``정제`` covers ordinary immediate-release
    tablet forms but not modified-release/enteric/etc. tablets; ``캡슐`` likewise
    covers ordinary capsule forms but not modified-release/enteric capsules.
    """

    generic = {
        "정제": "immediate_tablet",
        "주사제": "injection",
        "캡슐": "standard_capsule",
        "캡슐제": "standard_capsule",
        "산제": "powder",
        "과립제": "granule",
        "세립제": "fine_granule",
        "시럽": "syrup",
        "시럽제": "syrup",
        "액제": "liquid",
        "크림": "cream",
        "크림제": "cream",
        "연고": "ointment",
        "연고제": "ointment",
        "로션제": "lotion",
        "겔": "gel",
        "겔제": "gel",
        "점안제": "ophthalmic",
        "점이제": "otic",
        "점비제": "nasal",
        "흡입제": "inhaled",
        "좌제": "suppository",
        "첩부제": "patch",
    }
    return generic.get(token)


def _product_families(token: str) -> set[str]:
    # In compound labels the first comma-delimited part is the dosage-form family;
    # the suffix describes contents/state and must not independently match another
    # generic form (e.g. capsule-with-powder is a capsule, not a powder product).
    primary = token.split(",", 1)[0]
    families: set[str] = set()
    if "주사" in primary or "수액" in primary:
        families.add("injection")
    if "캡슐" in primary:
        families.add("capsule")
        if not any(marker in primary for marker in ("서방", "장용")):
            families.add("standard_capsule")
    if primary == "정제" or any(
        marker in primary
        for marker in (
            "필름코팅정",
            "나정",
            "서방정",
            "장용정",
            "구강붕해정",
            "다층정",
            "저작정",
            "츄어블정",
            "당의정",
            "발포정",
        )
    ):
        families.add("tablet")
        if not any(
            marker in primary
            for marker in (
                "서방",
                "장용",
                "구강붕해",
                "다층",
                "저작",
                "츄어블",
                "발포",
            )
        ):
            families.add("immediate_tablet")
    if "산제" in primary and "주사" not in primary:
        families.add("powder")
    if "과립" in primary:
        families.add("granule")
    if "세립" in primary:
        families.add("fine_granule")
    if "시럽" in primary:
        families.add("syrup")
    if primary == "액제" or "경구용액" in primary:
        families.add("liquid")
    if "크림" in primary:
        families.add("cream")
    if "연고" in primary:
        families.add("ointment")
    if "로션" in primary:
        families.add("lotion")
    if "겔" in primary:
        families.add("gel")
    if "점안" in primary:
        families.add("ophthalmic")
    if "점이" in primary:
        families.add("otic")
    if "점비" in primary:
        families.add("nasal")
    if "흡입" in primary:
        families.add("inhaled")
    if "좌제" in primary:
        families.add("suppository")
    if "첩부" in primary or "카타플라스마" in primary:
        families.add("patch")
    return families


def mfds_form_scope_applies(criterion_form: object, product_form: object) -> bool:
    """Return whether a product is inside an MFDS ingredient criterion form scope.

    Missing criterion scope means the rule is unscoped. Missing product form fails
    closed for a scoped criterion. Precise MFDS form tokens require an exact token
    match; only explicit broad labels are allowed to match standard subtypes.
    """

    criterion_tokens = _tokens(criterion_form)
    if not criterion_tokens:
        return True
    product_tokens = _tokens(product_form)
    if not product_tokens:
        return False

    product_token_set = set(product_tokens)
    for criterion_token in criterion_tokens:
        if criterion_token in product_token_set:
            return True
        family = _generic_family(criterion_token)
        if family and any(family in _product_families(token) for token in product_tokens):
            return True
    return False


__all__ = ["mfds_form_scope_applies"]
