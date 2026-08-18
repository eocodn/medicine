from __future__ import annotations

import csv
from collections import defaultdict
from importlib.resources import files


_OVERRIDE_RESOURCE = "data/mfds_product_scope_overrides.tsv"


def _load_scope_overrides() -> tuple[
    frozenset[tuple[str, str]],
    dict[tuple[str, str, str], frozenset[str]],
]:
    groups: set[tuple[str, str]] = set()
    allowed: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    resource = files("medicine_canonical").joinpath(_OVERRIDE_RESOURCE)
    with resource.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected = {"category", "ingredient_code", "item_seq", "allowed_rule_value", "rationale"}
        if set(reader.fieldnames or ()) != expected:
            raise RuntimeError("invalid MFDS product-scope override header")
        for line_number, row in enumerate(reader, start=2):
            category = str(row["category"] or "").strip()
            ingredient_code = str(row["ingredient_code"] or "").strip()
            item_seq = str(row["item_seq"] or "").strip()
            rule_value = str(row["allowed_rule_value"] or "").strip()
            rationale = str(row["rationale"] or "").strip()
            if not all((category, ingredient_code, item_seq, rule_value, rationale)):
                raise RuntimeError(
                    f"invalid MFDS product-scope override row {line_number}: empty field"
                )
            groups.add((category, ingredient_code))
            allowed[(category, ingredient_code, item_seq)].add(rule_value)
    return frozenset(groups), {key: frozenset(values) for key, values in allowed.items()}


_OVERRIDE_GROUPS, _ALLOWED_RULE_VALUES = _load_scope_overrides()


def mfds_product_scope_allows(
    category: object,
    ingredient_code: object,
    item_seq: object,
    rule_value: object,
) -> bool:
    """Return whether an MFDS criterion is allowed for an explicitly-scoped product group.

    Only the few reviewed MFDS ingredient groups that cannot be safely resolved from
    structured API fields enter this registry. Membership in such a group makes the
    registry an allow-list: newly seen or otherwise unreviewed ITEM_SEQ values fail
    closed instead of inheriting a concentration-specific rule.
    """

    group = (str(category or "").strip(), str(ingredient_code or "").strip())
    if group not in _OVERRIDE_GROUPS:
        return True
    allowed = _ALLOWED_RULE_VALUES.get((*group, str(item_seq or "").strip()))
    if not allowed:
        return False
    return str(rule_value or "").strip() in allowed


def mfds_product_scope_is_explicit(category: object, ingredient_code: object) -> bool:
    return (
        str(category or "").strip(),
        str(ingredient_code or "").strip(),
    ) in _OVERRIDE_GROUPS


def scope_override_group_count() -> int:
    return len(_OVERRIDE_GROUPS)


def scope_override_row_count() -> int:
    return sum(len(values) for values in _ALLOWED_RULE_VALUES.values())


__all__ = [
    "mfds_product_scope_allows",
    "mfds_product_scope_is_explicit",
    "scope_override_group_count",
    "scope_override_row_count",
]