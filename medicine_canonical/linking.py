from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Iterable


_ANNOTATION_RE = re.compile(r"\(\s*분류번호\s*:[^)]+\)\s*$", re.IGNORECASE)
COMBINATION_CATEGORY = "combination_contraindication"
DUPLICATION_CATEGORY = "therapeutic_duplication_caution"


def normalize_ingredient_identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _ANNOTATION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([+/])\s*", r"\1", text)
    return text.strip()


def _add_identity(name_codes: dict[str, set[str]], name: object, code: object) -> None:
    normalized_name = normalize_ingredient_identity(name)
    normalized_code = str(code or "").strip()
    if normalized_name and normalized_code:
        name_codes[normalized_name].add(normalized_code)


def _criterion_key(category: str, ingredient_name: object, rule_value: object) -> tuple[str, str, str]:
    effect = normalize_ingredient_identity(rule_value) if category == DUPLICATION_CATEGORY else ""
    return category, normalize_ingredient_identity(ingredient_name), effect


def _product_key(category: str, ingredient_name_en: object, effect_name: object) -> tuple[str, str, str]:
    effect = normalize_ingredient_identity(effect_name) if category == DUPLICATION_CATEGORY else ""
    return category, normalize_ingredient_identity(ingredient_name_en), effect


def _insert_links(
    con: sqlite3.Connection,
    product_rule_id: int,
    criteria: Iterable[int],
    *,
    match_method: str,
    pair_orientation: str | None,
) -> int:
    inserted = 0
    for criterion_rule_id in criteria:
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO product_criterion_links(
                product_rule_id,criterion_rule_id,
                match_method,pair_orientation
            ) VALUES(?,?,?,?)
            """,
            (
                product_rule_id,
                criterion_rule_id,
                match_method,
                pair_orientation,
            ),
        )
        inserted += cursor.rowcount
    return inserted


def materialize_product_criterion_links(con: sqlite3.Connection) -> dict:
    """Join product-specific DUR membership to XLSX criteria using official identities.

    Direct normalized English-name equality is preferred. If the product-side API
    uses a different published salt/form name, the bridge may fall back to the
    MFDS DUR ``INGR_CODE`` only when the XLSX English name maps to exactly one
    ingredient code observed in the same canonical DUR snapshots. No heuristic
    salt stripping or legacy alias source participates in this relation.
    """

    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        name_codes: dict[str, set[str]] = defaultdict(set)
        for row in con.execute(
            """
            SELECT ingredient_name_en,ingredient_code,
                   paired_ingredient_name_en,paired_ingredient_code
            FROM product_rules
            """
        ):
            _add_identity(name_codes, row["ingredient_name_en"], row["ingredient_code"])
            _add_identity(name_codes, row["paired_ingredient_name_en"], row["paired_ingredient_code"])

        direct_single: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        code_single: dict[tuple[str, str, str], list[int]] = defaultdict(list)
        direct_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
        code_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)

        for row in con.execute(
            """
            SELECT id,category,ingredient_name,
                   paired_ingredient_name,rule_value
            FROM ingredient_rules
            """
        ):
            criterion = int(row["id"])
            category = row["category"]
            left = normalize_ingredient_identity(row["ingredient_name"])
            if not left:
                continue

            if category == COMBINATION_CATEGORY:
                right = normalize_ingredient_identity(row["paired_ingredient_name"])
                if not right:
                    continue
                direct_pairs[(left, right)].append(criterion)
                left_codes = name_codes.get(left, set())
                right_codes = name_codes.get(right, set())
                if len(left_codes) == 1 and len(right_codes) == 1:
                    code_pairs[(next(iter(left_codes)), next(iter(right_codes)))].append(criterion)
                continue

            direct_single[_criterion_key(category, left, row["rule_value"])].append(criterion)
            codes = name_codes.get(left, set())
            if len(codes) == 1:
                effect = normalize_ingredient_identity(row["rule_value"]) if category == DUPLICATION_CATEGORY else ""
                code_single[(category, next(iter(codes)), effect)].append(criterion)

        con.execute("DELETE FROM product_criterion_links")
        links = 0
        linked_products: set[int] = set()
        methods: dict[str, int] = defaultdict(int)

        for row in con.execute(
            """
            SELECT id,category,ingredient_code,ingredient_name_en,
                   paired_ingredient_code,paired_ingredient_name_en,effect_name
            FROM product_rules
            ORDER BY id
            """
        ):
            product_rule_id = int(row["id"])
            category = row["category"]
            criteria: list[int] = []
            method = "english_exact"
            orientation: str | None = None

            if category == COMBINATION_CATEGORY:
                left = normalize_ingredient_identity(row["ingredient_name_en"])
                right = normalize_ingredient_identity(row["paired_ingredient_name_en"])
                if left and right:
                    criteria = direct_pairs.get((left, right), [])
                    orientation = "forward"
                    if not criteria:
                        criteria = direct_pairs.get((right, left), [])
                        orientation = "reverse"
                if not criteria:
                    left_code = str(row["ingredient_code"] or "").strip()
                    right_code = str(row["paired_ingredient_code"] or "").strip()
                    if left_code and right_code:
                        criteria = code_pairs.get((left_code, right_code), [])
                        orientation = "forward"
                        if not criteria:
                            criteria = code_pairs.get((right_code, left_code), [])
                            orientation = "reverse"
                    method = "mfds_ingredient_code"
            else:
                criteria = direct_single.get(
                    _product_key(category, row["ingredient_name_en"], row["effect_name"]), []
                )
                if not criteria:
                    ingredient_code = str(row["ingredient_code"] or "").strip()
                    effect = normalize_ingredient_identity(row["effect_name"]) if category == DUPLICATION_CATEGORY else ""
                    if ingredient_code:
                        criteria = code_single.get((category, ingredient_code, effect), [])
                    method = "mfds_ingredient_code"

            if not criteria:
                continue
            inserted = _insert_links(
                con,
                product_rule_id,
                criteria,
                match_method=method,
                pair_orientation=orientation if category == COMBINATION_CATEGORY else None,
            )
            if inserted:
                links += inserted
                linked_products.add(product_rule_id)
                methods[method] += inserted

        total_product_rules = con.execute("SELECT COUNT(*) FROM product_rules").fetchone()[0]
        return {
            "product_criterion_links": links,
            "linked_product_rules": len(linked_products),
            "unlinked_product_rules": int(total_product_rules) - len(linked_products),
            "criterion_link_methods": dict(sorted(methods.items())),
        }
    finally:
        con.row_factory = previous_factory
