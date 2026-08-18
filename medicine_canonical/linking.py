from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Iterable

from .dur_bridge import (
    COMBINATION_CATEGORY,
    DUPLICATION_CATEGORY,
    ensure_dur_ingredient_bridge,
)
from .form_scope import mfds_form_scope_applies
from .mfds_scope_overrides import mfds_product_scope_allows
from .linking_candidates import (
    CriterionCandidate,
    PairCriterionCandidate,
    code_options as _code_options,
    load_bridge_maps as _load_bridge_maps,
)
from .preprocessing import canonicalize_link_ingredient_code, normalize_ingredient_identity


DOSE_CATEGORY = "dose_caution"
_STRUCTURED_VALUE_FALLBACK_CATEGORIES = frozenset({
    "age_contraindication",
    "pregnancy_contraindication",
    "dose_caution",
    "duration_caution",
})
_PLACEHOLDER_EVIDENCE = frozenset({"-", "_"})


def _insert_links(
    con: sqlite3.Connection,
    product_rule_id: int,
    criteria: Iterable[tuple[int, str, str | None]],
) -> tuple[int, dict[str, int]]:
    inserted = 0
    methods: dict[str, int] = defaultdict(int)
    for criterion_rule_id, match_method, pair_orientation in criteria:
        cursor = con.execute(
            """
            INSERT OR IGNORE INTO product_criterion_links(
                product_rule_id,criterion_rule_id,match_method,pair_orientation
            ) VALUES(?,?,?,?)
            """,
            (product_rule_id, criterion_rule_id, match_method, pair_orientation),
        )
        if cursor.rowcount:
            inserted += cursor.rowcount
            methods[match_method] += cursor.rowcount
    return inserted, methods


def _criterion_form_applies(
    candidate: CriterionCandidate | PairCriterionCandidate,
    product_form: object,
) -> bool:
    if isinstance(candidate, CriterionCandidate) and candidate.all_forms_scope:
        return True
    return mfds_form_scope_applies(candidate.dosage_form, product_form)


def _criterion_product_scope_applies(
    candidate: CriterionCandidate,
    *,
    category: object,
    ingredient_code: object,
    item_seq: object,
) -> bool:
    return mfds_product_scope_allows(
        category,
        ingredient_code,
        item_seq,
        candidate.rule_value,
    )


def _evidence_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s,]+", "", text)


def _dose_text_equivalent(left: object, right: object) -> bool:
    left_text = _evidence_text(left)
    right_text = _evidence_text(right)
    if not left_text or not right_text:
        return False
    for particle in ("으로서", "로서"):
        left_text = left_text.replace(particle, "")
        right_text = right_text.replace(particle, "")
    return left_text == right_text


def _normalized_whitespace(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    return " ".join(text.split())


def _meaningful_evidence(value: object) -> str | None:
    text = _normalized_whitespace(value)
    if not text or text in _PLACEHOLDER_EVIDENCE:
        return None
    if not re.search(r"[0-9A-Za-z가-힣]", text):
        return None
    return text


def _one_structured_value(candidates: list[CriterionCandidate]) -> str | None:
    if not candidates:
        return None
    values: set[str] = set()
    for candidate in candidates:
        value = _meaningful_evidence(candidate.rule_value)
        if value is None:
            return None
        values.add(value)
    return next(iter(values)) if len(values) == 1 else None


def _same_code_fallback_matches(
    candidates: list[CriterionCandidate],
    *,
    category: str,
    ingredient_code: object,
    item_seq: object,
    product_details: object,
) -> list[tuple[int, str, None]]:
    if category not in _STRUCTURED_VALUE_FALLBACK_CATEGORIES:
        return []
    eligible = [
        candidate
        for candidate in candidates
        if _criterion_product_scope_applies(
            candidate,
            category=category,
            ingredient_code=ingredient_code,
            item_seq=item_seq,
        )
    ]
    if not eligible:
        return []

    product_evidence = _meaningful_evidence(product_details)
    if product_evidence is not None:
        detail_matches = [
            candidate
            for candidate in eligible
            if _meaningful_evidence(candidate.details) == product_evidence
        ]
        if detail_matches and _one_structured_value(detail_matches) is not None:
            return [
                (candidate.criterion_id, "mfds_details_exact", None)
                for candidate in detail_matches
            ]

    if _one_structured_value(eligible) is not None:
        return [
            (candidate.criterion_id, "mfds_unanimous_value", None)
            for candidate in eligible
        ]
    return []


def _dose_candidate_method(
    candidate: CriterionCandidate,
    *,
    product_details: object,
    direct_method: str,
) -> str | None:
    """Keep a code-proven dose link only when item detail does not contradict it."""
    details = str(product_details or "").strip()
    if not details:
        return direct_method
    if (
        _dose_text_equivalent(details, candidate.rule_value)
        or _dose_text_equivalent(details, candidate.details)
        or (_evidence_text(details) and _evidence_text(details) == _evidence_text(candidate.dosage_form))
    ):
        return direct_method
    return None


def materialize_product_criterion_links(con: sqlite3.Connection) -> dict:
    """Link MFDS item-level DUR rules to MFDS ingredient criteria by official codes.

    Ingredient criteria without the MFDS code payload are invalid upstream. The
    linker therefore has no name-only criterion fallback: product-specific work is
    limited to item/category composition, dosage-form scope, explicit reviewed
    product-scope overrides, dose-detail conflicts, and combination orientation.
    """
    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        ensure_dur_ingredient_bridge(con)
        (
            code_map,
            item_signatures,
            category_item_signatures,
            single_signatures,
            pair_signatures,
            same_code_candidates,
        ) = _load_bridge_maps(con)

        dur_forms_by_item: dict[str, set[str]] = defaultdict(set)
        for row in con.execute(
            "SELECT item_seq,dosage_form FROM product_rules WHERE dosage_form IS NOT NULL"
        ):
            form = str(row["dosage_form"] or "").strip()
            if form:
                dur_forms_by_item[str(row["item_seq"])].add(form)

        con.execute("DELETE FROM product_criterion_links")
        links = 0
        linked_products: set[int] = set()
        method_counts: dict[str, int] = defaultdict(int)

        rows = con.execute(
            """SELECT r.id,r.category,r.item_seq,r.ingredient_code,r.effect_name,
                      r.dosage_form,r.details,r.paired_item_seq,r.paired_ingredient_code,
                      p.dosage_form AS permit_dosage_form,
                      pp.dosage_form AS paired_permit_dosage_form
               FROM product_rules r
               LEFT JOIN products p ON p.item_seq=r.item_seq
               LEFT JOIN products pp ON pp.item_seq=r.paired_item_seq
               ORDER BY r.id"""
        )
        for row in rows:
            product_rule_id = int(row["id"])
            category = str(row["category"])
            matches: list[tuple[int, str, str | None]] = []

            if category == COMBINATION_CATEGORY:
                left_code_options = _code_options(
                    category,
                    row["ingredient_code"],
                    row["item_seq"],
                    code_map,
                    item_signatures,
                    category_item_signatures,
                )
                right_code_options = _code_options(
                    category,
                    row["paired_ingredient_code"],
                    row["paired_item_seq"],
                    code_map,
                    item_signatures,
                    category_item_signatures,
                )
                strict_left_form = row["dosage_form"] or row["permit_dosage_form"]
                strict_right_form = row["paired_permit_dosage_form"] or " ".join(
                    sorted(dur_forms_by_item.get(str(row["paired_item_seq"] or ""), set()))
                )
                found: dict[int, tuple[str, str]] = {}
                for left_sig in left_code_options:
                    for right_sig in right_code_options:
                        for orientation, key in (
                            ("forward", (left_sig.signature_key, right_sig.signature_key)),
                            ("reverse", (right_sig.signature_key, left_sig.signature_key)),
                        ):
                            for candidate in pair_signatures.get(key, []):
                                product_form = (
                                    strict_left_form if orientation == "forward" else strict_right_form
                                )
                                if not _criterion_form_applies(candidate, product_form):
                                    continue
                                method = candidate.method
                                if (
                                    left_sig.method == "permit_composition"
                                    and left_sig.component_count > 1
                                ) or (
                                    right_sig.method == "permit_composition"
                                    and right_sig.component_count > 1
                                ):
                                    method = "permit_composition"
                                found.setdefault(candidate.criterion_id, (method, orientation))
                matches.extend(
                    (criterion_id, method, orientation)
                    for criterion_id, (method, orientation) in found.items()
                )
            else:
                effect = (
                    normalize_ingredient_identity(row["effect_name"])
                    if category == DUPLICATION_CATEGORY
                    else ""
                )
                code_options = _code_options(
                    category,
                    row["ingredient_code"],
                    row["item_seq"],
                    code_map,
                    item_signatures,
                    category_item_signatures,
                )
                strict_form = row["dosage_form"] or row["permit_dosage_form"]
                found: dict[int, str] = {}
                for product_sig in code_options:
                    for candidate in single_signatures.get(
                        (category, effect, product_sig.signature_key), []
                    ):
                        if not _criterion_product_scope_applies(
                            candidate,
                            category=category,
                            ingredient_code=row["ingredient_code"],
                            item_seq=row["item_seq"],
                        ):
                            continue
                        if (
                            candidate.exact_composition_scope
                            and product_sig.evidence_kind
                            not in {
                                "permit_composition",
                                "category_permit_composition",
                                "category_single_component_rule",
                            }
                        ):
                            continue
                        if not _criterion_form_applies(candidate, strict_form):
                            continue
                        method = candidate.method
                        if (
                            product_sig.method == "permit_composition"
                            and product_sig.component_count > 1
                        ):
                            method = "permit_composition"
                        if category == DOSE_CATEGORY:
                            method = _dose_candidate_method(
                                candidate,
                                product_details=row["details"],
                                direct_method=method,
                            )
                            if not method:
                                continue
                        found.setdefault(candidate.criterion_id, method)
                matches.extend(
                    (criterion_id, method, None)
                    for criterion_id, method in found.items()
                )

            if not matches and category != COMBINATION_CATEGORY:
                direct_code = canonicalize_link_ingredient_code(row["ingredient_code"])
                if direct_code:
                    matches.extend(
                        _same_code_fallback_matches(
                            same_code_candidates.get((category, direct_code), []),
                            category=category,
                            ingredient_code=row["ingredient_code"],
                            item_seq=row["item_seq"],
                            product_details=row["details"],
                        )
                    )

            if not matches:
                continue
            inserted, inserted_methods = _insert_links(con, product_rule_id, matches)
            if inserted:
                links += inserted
                linked_products.add(product_rule_id)
                for method, count in inserted_methods.items():
                    method_counts[method] += count

        total_product_rules = int(con.execute("SELECT COUNT(*) FROM product_rules").fetchone()[0])
        return {
            "product_criterion_links": links,
            "linked_product_rules": len(linked_products),
            "unlinked_product_rules": total_product_rules - len(linked_products),
            "criterion_link_methods": dict(sorted(method_counts.items())),
        }
    finally:
        con.row_factory = previous_factory


__all__ = ["canonicalize_link_ingredient_code", "materialize_product_criterion_links"]
