from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Iterable


_ANNOTATION_RE = re.compile(r"\(\s*분류번호\s*:[^)]+\)\s*$", re.IGNORECASE)
COMBINATION_CATEGORY = "combination_contraindication"
DUPLICATION_CATEGORY = "therapeutic_duplication_caution"

# Link-time equivalences only. These do not rewrite source data in product_rules.
# Each pair was observed in the current canonical MFDS DUR snapshots as the same
# ingredient name using category-specific codes. Do not add entries silently:
# new ambiguous identities are surfaced by materialization/verification instead.
_LINK_CODE_EQUIVALENCES = {
    "D001289": "D000274",  # Ketorolac
    "D000195": "D000982",  # Naproxen
    "D000983": "D000309",  # Piroxicam
    "D000904": "D000719",  # Mizolastine
}


def canonicalize_link_ingredient_code(value: object) -> str:
    """Return the code identity used only while linking product DUR to XLSX criteria."""
    code = str(value or "").strip()
    return _LINK_CODE_EQUIVALENCES.get(code, code)


def normalize_ingredient_identity(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    text = _ANNOTATION_RE.sub("", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"\s*([+/])\s*", r"\1", text)
    return text.strip()


def _add_identity(name_codes: dict[str, set[str]], name: object, code: object) -> None:
    normalized_name = normalize_ingredient_identity(name)
    normalized_code = canonicalize_link_ingredient_code(code)
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


def _ambiguity_record(category: str, ingredient_name: str, codes: set[str]) -> tuple[str, str, tuple[str, ...]]:
    return category, ingredient_name, tuple(sorted(codes))


def _ambiguity_dict(record: tuple[str, str, tuple[str, ...]]) -> dict:
    category, ingredient_name, codes = record
    return {
        "category": category,
        "ingredient_name": ingredient_name,
        "candidate_codes": list(codes),
    }


def materialize_product_criterion_links(con: sqlite3.Connection) -> dict:
    """Join product-specific DUR membership to XLSX criteria using official identities.

    Direct normalized English-name equality is preferred. If the product-side API
    uses a different published salt/form name, the bridge may use the MFDS DUR
    ``INGR_CODE``. Exactly four observed cross-category code variants are collapsed
    at link time; source codes remain untouched. Any new code ambiguity that blocks
    a fallback match is reported explicitly instead of being silently guessed.
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
        ambiguous_single: dict[tuple[str, str, str], set[tuple[str, str, tuple[str, ...]]]] = defaultdict(set)
        direct_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
        code_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
        ambiguous_pairs: dict[tuple[str, str], set[tuple[str, str, tuple[str, ...]]]] = defaultdict(set)

        for row in con.execute(
            """
            SELECT id,category,ingredient_name,
                   paired_ingredient_name,rule_value
            FROM ingredient_rules
            """
        ):
            criterion = int(row["id"])
            category = row["category"]
            raw_left = str(row["ingredient_name"] or "").strip()
            left = normalize_ingredient_identity(raw_left)
            if not left:
                continue

            if category == COMBINATION_CATEGORY:
                raw_right = str(row["paired_ingredient_name"] or "").strip()
                right = normalize_ingredient_identity(raw_right)
                if not right:
                    continue
                direct_pairs[(left, right)].append(criterion)
                left_codes = name_codes.get(left, set())
                right_codes = name_codes.get(right, set())
                if len(left_codes) == 1 and len(right_codes) == 1:
                    code_pairs[(next(iter(left_codes)), next(iter(right_codes)))].append(criterion)
                elif left_codes and right_codes:
                    records: set[tuple[str, str, tuple[str, ...]]] = set()
                    if len(left_codes) > 1:
                        records.add(_ambiguity_record(category, raw_left, left_codes))
                    if len(right_codes) > 1:
                        records.add(_ambiguity_record(category, raw_right, right_codes))
                    for left_code in left_codes:
                        for right_code in right_codes:
                            ambiguous_pairs[(left_code, right_code)].update(records)
                continue

            direct_single[_criterion_key(category, left, row["rule_value"])].append(criterion)
            codes = name_codes.get(left, set())
            effect = normalize_ingredient_identity(row["rule_value"]) if category == DUPLICATION_CATEGORY else ""
            if len(codes) == 1:
                code_single[(category, next(iter(codes)), effect)].append(criterion)
            elif len(codes) > 1:
                record = _ambiguity_record(category, raw_left, codes)
                for code in codes:
                    ambiguous_single[(category, code, effect)].add(record)

        con.execute("DELETE FROM product_criterion_links")
        links = 0
        linked_products: set[int] = set()
        methods: dict[str, int] = defaultdict(int)
        unresolved: set[tuple[str, str, tuple[str, ...]]] = set()

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
                    left_code = canonicalize_link_ingredient_code(row["ingredient_code"])
                    right_code = canonicalize_link_ingredient_code(row["paired_ingredient_code"])
                    if left_code and right_code:
                        criteria = code_pairs.get((left_code, right_code), [])
                        orientation = "forward"
                        if not criteria:
                            criteria = code_pairs.get((right_code, left_code), [])
                            orientation = "reverse"
                        if not criteria:
                            unresolved.update(ambiguous_pairs.get((left_code, right_code), set()))
                            unresolved.update(ambiguous_pairs.get((right_code, left_code), set()))
                    method = "mfds_ingredient_code"
            else:
                criteria = direct_single.get(
                    _product_key(category, row["ingredient_name_en"], row["effect_name"]), []
                )
                if not criteria:
                    ingredient_code = canonicalize_link_ingredient_code(row["ingredient_code"])
                    effect = normalize_ingredient_identity(row["effect_name"]) if category == DUPLICATION_CATEGORY else ""
                    if ingredient_code:
                        criteria = code_single.get((category, ingredient_code, effect), [])
                        if not criteria:
                            unresolved.update(ambiguous_single.get((category, ingredient_code, effect), set()))
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
        unresolved_rows = [_ambiguity_dict(record) for record in sorted(unresolved)]
        return {
            "product_criterion_links": links,
            "linked_product_rules": len(linked_products),
            "unlinked_product_rules": int(total_product_rules) - len(linked_products),
            "criterion_link_methods": dict(sorted(methods.items())),
            "unresolved_link_ambiguities": unresolved_rows,
            "unresolved_link_ambiguity_count": len(unresolved_rows),
        }
    finally:
        con.row_factory = previous_factory
