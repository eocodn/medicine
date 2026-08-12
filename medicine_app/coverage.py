from __future__ import annotations

import re
import sqlite3
import unicodedata
from typing import Any, Mapping

from .ingredient_alias_curated import is_reviewed_exact_edi_identity_conflict


_ANNOTATION_RE = re.compile(r"\(\s*분류번호\s*:[^)]+\)\s*$", re.IGNORECASE)
_DUR_DOSE_SUFFIX_RE = re.compile(r"_\((?=[^)]*\d)(?=[^)]*/)[^)]*\)\s*$")
_INGREDIENT_STRENGTH_SUFFIX_RE = re.compile(
    r"\s+(?:\d+(?:\.\d+)?|\.\d+)\s*(?:mcg|μg|ug|mg|g|kg|ml|l|%)"
    r"(?:\s*\([^)]*\))?\s*$",
    re.IGNORECASE,
)


def normalize_ingredient_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _ANNOTATION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([+/])\s*", r"\1", text)
    return text.strip()


def normalize_product_name(value: Any) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _DUR_DOSE_SUFFIX_RE.sub("", text)
    return re.sub(r"[\s_]+", "", text)


def _normalize_product_mapping_ingredient(value: Any) -> str:
    """Strip only a terminal quantitative strength annotation for product matching.

    DUR product labels sometimes append a strength such as ``0.5g(25mg/mL)``
    to an otherwise exact ingredient identity. This helper is deliberately
    narrower than ingredient-level normalization: salts and other identity
    words are never removed.
    """
    normalized = normalize_ingredient_name(value)
    return _INGREDIENT_STRENGTH_SUFFIX_RE.sub("", normalized).strip()


def _escape_like(value: str) -> str:
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def split_edi_codes(value: Any) -> list[str]:
    if not value:
        return []
    seen: set[str] = set()
    result: list[str] = []
    for part in str(value).split(","):
        code = part.strip()
        if code and code not in seen:
            seen.add(code)
            result.append(code)
    return result


def ingredient_index(con: sqlite3.Connection) -> set[str]:
    names: set[str] = set()
    try:
        rows = con.execute(
            "SELECT ingredient_name,paired_ingredient_name FROM ingredient_dur"
        ).fetchall()
    except sqlite3.OperationalError:
        return names
    for row in rows:
        for value in row:
            normalized = normalize_ingredient_name(value)
            if normalized:
                names.add(normalized)
    return names


def split_ingredient_components(value: Any) -> list[str]:
    """Split explicit top-level ingredient delimiters without parsing chemistry.

    Slashes and commas inside parenthetical strength/annotation text are kept
    intact. This prevents strings such as ``5mg(5mg/mL)`` from being treated as
    multi-ingredient while still handling published ``A+B``, ``A/B`` and
    ``A,B`` product labels.
    """
    normalized = normalize_ingredient_name(value)
    if not normalized:
        return []
    parts: list[str] = []
    buffer: list[str] = []
    depth = 0
    for char in normalized:
        if char in "([{":
            depth += 1
            buffer.append(char)
            continue
        if char in ")]}":
            depth = max(depth - 1, 0)
            buffer.append(char)
            continue
        if depth == 0 and char in "+/,":
            part = normalize_ingredient_name("".join(buffer))
            if part:
                parts.append(part)
            buffer = []
            continue
        buffer.append(char)
    part = normalize_ingredient_name("".join(buffer))
    if part:
        parts.append(part)
    return parts


def _resolve_candidate_identities(
    value: str,
    ingredient_index: set[str],
    ingredient_aliases: Mapping[str, str],
    ingredient_multi_aliases: Mapping[str, tuple[str, ...]],
) -> tuple[list[str], bool]:
    multi_targets = tuple(ingredient_multi_aliases.get(value, ()))
    if multi_targets and all(target in ingredient_index for target in multi_targets):
        return sorted(set(multi_targets)), True
    if value in ingredient_index:
        return [value], False
    alias_target = ingredient_aliases.get(value)
    if alias_target in ingredient_index:
        return [alias_target], True
    strengthless = _INGREDIENT_STRENGTH_SUFFIX_RE.sub("", value).strip()
    if strengthless != value:
        multi_targets = tuple(ingredient_multi_aliases.get(strengthless, ()))
        if multi_targets and all(target in ingredient_index for target in multi_targets):
            return sorted(set(multi_targets)), True
        if strengthless in ingredient_index:
            return [strengthless], False
        alias_target = ingredient_aliases.get(strengthless)
        if alias_target in ingredient_index:
            return [alias_target], True
    return [], False


def _candidate_parts(
    value: Any,
    ingredient_index: set[str],
    ingredient_aliases: Mapping[str, str] | None = None,
    ingredient_multi_aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> tuple[list[str], list[str], bool]:
    normalized = normalize_ingredient_name(value)
    if not normalized:
        return [], [], False
    aliases = ingredient_aliases or {}
    multi_aliases = ingredient_multi_aliases or {}
    targets, used_alias = _resolve_candidate_identities(
        normalized, ingredient_index, aliases, multi_aliases
    )
    if targets:
        return targets, [], used_alias
    parts = split_ingredient_components(normalized)
    if len(parts) == 1 and re.search(r"\d", normalized):
        return [], [normalized], False
    matched: list[str] = []
    unmatched: list[str] = []
    alias_used = False
    for part in parts:
        targets, used_alias = _resolve_candidate_identities(
            part, ingredient_index, aliases, multi_aliases
        )
        if targets:
            matched.extend(targets)
            alias_used = alias_used or used_alias
        else:
            unmatched.append(part)
    return matched, unmatched, alias_used


def resolve_safety_mapping(
    con: sqlite3.Connection,
    *,
    edi_value: Any,
    catalog_product_name: Any,
    catalog_ingredient: Any,
    known_ingredients: set[str] | None = None,
    ingredient_aliases: Mapping[str, str] | None = None,
    ingredient_multi_aliases: Mapping[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    edi_codes = split_edi_codes(edi_value)
    product_rows: list[Mapping[str, Any]] = []
    if edi_codes:
        placeholders = ",".join("?" for _ in edi_codes)
        con.row_factory = sqlite3.Row
        product_rows = list(con.execute(
            f"""SELECT product_code,product_name,ingredient_code,ingredient_name
                FROM product_catalog WHERE product_code IN ({placeholders})
                ORDER BY product_code""",
            edi_codes,
        ).fetchall())
    product_mapping_method = "edi_exact" if product_rows else "none"
    if not product_rows and not edi_codes and catalog_product_name and catalog_ingredient:
        raw_ingredient = str(catalog_ingredient).strip()
        normalized_ingredient = normalize_ingredient_name(catalog_ingredient)
        normalized_name = normalize_product_name(catalog_product_name)
        candidate_rows = con.execute(
            """SELECT product_code,product_name,ingredient_code,ingredient_name
            FROM product_catalog
            WHERE ingredient_name=? COLLATE NOCASE
               OR ingredient_name LIKE ? ESCAPE '\\' COLLATE NOCASE
            ORDER BY product_code""",
            (raw_ingredient, f"{_escape_like(raw_ingredient)} %"),
        ).fetchall()
        matched_rows: list[Mapping[str, Any]] = []
        strength_annotation_used = False
        for row in candidate_rows:
            if normalize_product_name(row["product_name"]) != normalized_name:
                continue
            exact_ingredient = normalize_ingredient_name(row["ingredient_name"])
            if exact_ingredient == normalized_ingredient:
                matched_rows.append(row)
                continue
            if _normalize_product_mapping_ingredient(row["ingredient_name"]) == normalized_ingredient:
                matched_rows.append(row)
                strength_annotation_used = True
        product_rows = matched_rows
        if product_rows:
            product_mapping_method = (
                "normalized_name_ingredient_strength_unique"
                if strength_annotation_used
                else "normalized_name_ingredient_unique"
            )
    matched_product_codes = sorted({str(row["product_code"]) for row in product_rows})
    selected_product_code = matched_product_codes[0] if len(matched_product_codes) == 1 else None
    if len(matched_product_codes) > 1:
        if product_mapping_method == "normalized_name_ingredient_unique":
            product_mapping_method = "normalized_name_ingredient_ambiguous"
        elif product_mapping_method == "normalized_name_ingredient_strength_unique":
            product_mapping_method = "normalized_name_ingredient_strength_ambiguous"

    known_ingredients = known_ingredients if known_ingredients is not None else ingredient_index(con)
    if product_mapping_method.startswith("normalized_name_ingredient_strength_"):
        # The fallback proved that the DUR label differs only by a terminal
        # quantitative strength annotation, so retain the catalog's exact base
        # ingredient for the separate ingredient-level DUR bridge.
        canonical_names = [catalog_ingredient]
    else:
        canonical_names = [row["ingredient_name"] for row in product_rows if row["ingredient_name"]]
    matched_ingredients: list[str] = []
    unmatched_ingredients: list[str] = []
    alias_used = False

    # An exact EDI code proves product identity, but a corrupted DUR product
    # ingredient label must not override a different MFDS ingredient when both
    # labels independently resolve to established ingredient-level DUR identities.
    # In that case product-level DUR checks remain usable through the exact code,
    # while ingredient-level checks fail closed until the source conflict is fixed.
    ingredient_identity_conflict = False
    catalog_multi_expansion: list[str] = []
    if product_mapping_method == "edi_exact" and canonical_names and catalog_ingredient:
        catalog_matched, catalog_unmatched, _ = _candidate_parts(
            catalog_ingredient, known_ingredients, ingredient_aliases, ingredient_multi_aliases
        )
        dur_matched: list[str] = []
        dur_unmatched: list[str] = []
        for name in canonical_names:
            matched, unmatched, _ = _candidate_parts(
                name, known_ingredients, ingredient_aliases, ingredient_multi_aliases
            )
            dur_matched.extend(matched)
            dur_unmatched.extend(unmatched)
        multi_aliases = ingredient_multi_aliases or {}
        catalog_components = split_ingredient_components(catalog_ingredient)
        if (
            any(component in multi_aliases for component in catalog_components)
            and catalog_matched
            and not catalog_unmatched
            and dur_matched
            and not dur_unmatched
            and set(dur_matched).issubset(set(catalog_matched))
        ):
            catalog_multi_expansion = sorted(set(catalog_matched))
        if (
            catalog_matched
            and not catalog_unmatched
            and dur_matched
            and not dur_unmatched
            and is_reviewed_exact_edi_identity_conflict(
                selected_product_code, catalog_matched, dur_matched
            )
        ):
            ingredient_identity_conflict = True

    if ingredient_identity_conflict:
        mapping_method = "conflicting_exact_edi_identity"
        ingredient_status = "not_evaluable"
    elif canonical_names:
        # Product-code linkage identifies the product, but ingredient-level DUR
        # identity still requires an exact known component or a separately
        # materialized alias backed by authoritative source evidence.
        for name in canonical_names:
            matched, unmatched, used_alias = _candidate_parts(
                name, known_ingredients, ingredient_aliases, ingredient_multi_aliases
            )
            matched_ingredients.extend(matched)
            unmatched_ingredients.extend(unmatched)
            alias_used = alias_used or used_alias
        if (
            catalog_multi_expansion
            and not unmatched_ingredients
            and set(matched_ingredients).issubset(set(catalog_multi_expansion))
        ):
            matched_ingredients.extend(catalog_multi_expansion)
            alias_used = True
        matched_ingredients = sorted(set(matched_ingredients))
        unmatched_ingredients = sorted(set(unmatched_ingredients))
        mapping_method = "validated_alias" if alias_used else "product_code_exact"
        if matched_ingredients and not unmatched_ingredients:
            ingredient_status = "matched"
        elif matched_ingredients:
            ingredient_status = "partial"
        else:
            ingredient_status = "not_evaluable"
    else:
        matched, unmatched, alias_used = _candidate_parts(
            catalog_ingredient, known_ingredients, ingredient_aliases, ingredient_multi_aliases
        )
        matched_ingredients = sorted(set(matched))
        unmatched_ingredients = sorted(set(unmatched))
        mapping_method = "validated_alias" if alias_used else "catalog_exact"
        if matched_ingredients and not unmatched_ingredients:
            ingredient_status = "matched"
        elif matched_ingredients:
            ingredient_status = "partial"
        else:
            ingredient_status = "not_evaluable"

    reason = None
    if ingredient_identity_conflict:
        reason = "식약처 성분과 DUR 제품 성분이 서로 다른 DUR 성분 기준으로 연결되어 자동 성분 판정을 중단했습니다."
    elif ingredient_status == "not_evaluable":
        reason = "식약처 성분명을 DUR 성분 기준에 단일하게 연결하지 못했습니다."
    elif ingredient_status == "partial":
        reason = "일부 성분만 DUR 성분 기준에 단일하게 연결되었습니다."

    ingredient_code = None
    if selected_product_code:
        codes = {row["ingredient_code"] for row in product_rows if row["product_code"] == selected_product_code and row["ingredient_code"]}
        if len(codes) == 1:
            ingredient_code = next(iter(codes))

    return {
        "edi_codes": edi_codes,
        "matched_product_codes": matched_product_codes,
        "product_code": selected_product_code,
        "product_mapping_method": product_mapping_method,
        "product_status": (
            "matched" if selected_product_code else "ambiguous" if len(matched_product_codes) > 1 else "not_matched"
        ),
        "ingredient_code": ingredient_code,
        "ingredients": matched_ingredients,
        "ingredient_status": ingredient_status,
        "ingredient_mapping_method": mapping_method,
        "unmapped_ingredients": unmatched_ingredients,
        "ingredient_reason": reason,
    }


def coverage_summary(
    product: Mapping[str, Any],
    dataset: Mapping[str, Any],
    person: Mapping[str, Any],
    *,
    relevant_profile_categories: set[str] | None = None,
) -> dict[str, Any]:
    ingredient_status = product.get("ingredient_mapping_status") or "not_evaluable"
    product_status = product.get("product_mapping_status") or "not_matched"
    profile_gaps: list[str] = []
    reproductive_applicable = person.get("sex") != "male"
    relevant = relevant_profile_categories
    if reproductive_applicable and person.get("pregnancy_status") == "unknown" and (
        relevant is None or "pregnancy_contraindication" in relevant
    ):
        profile_gaps.append("pregnancy_contraindication")
    if reproductive_applicable and person.get("lactation_status", "unknown") == "unknown" and (
        relevant is None or "lactation_caution" in relevant
    ):
        profile_gaps.append("lactation_caution")

    not_evaluable_checks: list[dict[str, Any]] = []
    if dataset.get("status") != "verified":
        not_evaluable_checks.append({
            "category": "dataset",
            "result": "not_evaluable",
            "reason": "필수 DUR 원본 manifest를 검증하지 못했습니다.",
        })
    if product_status == "ambiguous":
        not_evaluable_checks.append({
            "category": "product_mapping",
            "result": "not_evaluable",
            "reason": "여러 DUR 제품코드가 연결되어 제품 단위 규칙을 하나로 확정할 수 없습니다.",
        })
    elif product_status != "matched":
        not_evaluable_checks.append({
            "category": "product_mapping",
            "result": "not_evaluable",
            "reason": "DUR 제품코드가 연결되지 않아 제품 단위 규칙을 확인할 수 없습니다.",
        })
    if ingredient_status != "matched":
        not_evaluable_checks.append({
            "category": "ingredient_mapping",
            "result": "not_evaluable",
            "reason": product.get("ingredient_mapping_reason") or "DUR 성분 기준과의 연결을 확정할 수 없습니다.",
        })
    for category in profile_gaps:
        reason = (
            "임신 여부가 미확정이라 임부금기 적용 여부를 판정할 수 없습니다."
            if category == "pregnancy_contraindication"
            else "수유 여부가 미입력이라 수유부주의 적용 여부를 판정할 수 없습니다."
        )
        not_evaluable_checks.append({"category": category, "result": "not_evaluable", "reason": reason})
    limited = (
        dataset.get("status") != "verified"
        or product_status != "matched"
        or ingredient_status not in {"matched"}
        or bool(profile_gaps)
    )
    if limited:
        message = "일부 항목은 자동으로 확인하지 못했어요."
    else:
        message = "현재 프로필과 DUR 데이터 범위에서 확인했어요."
    return {
        "status": "limited" if limited else "complete",
        "message": message,
        "dataset": dict(dataset),
        "product": {
            "status": product_status,
            "edi_codes": list(product.get("edi_codes") or []),
            "matched_product_codes": list(product.get("matched_product_codes") or []),
        },
        "ingredient": {
            "status": ingredient_status,
            "mapping_method": product.get("ingredient_mapping_method"),
            "ingredients": list(product.get("safety_ingredients") or []),
            "unmapped_ingredients": list(product.get("unmapped_ingredients") or []),
            "reason": product.get("ingredient_mapping_reason"),
        },
        "profile": {
            "not_evaluable_categories": profile_gaps,
        },
        "not_evaluable_checks": not_evaluable_checks,
    }


__all__ = [
    "coverage_summary", "ingredient_index", "normalize_ingredient_name", "resolve_safety_mapping",
    "split_edi_codes", "split_ingredient_components",
]
