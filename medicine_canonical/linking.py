from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from typing import Iterable

from .dur_bridge import (
    COMBINATION_CATEGORY,
    DOSE_CATEGORY,
    DUPLICATION_CATEGORY,
    ensure_dur_ingredient_bridge,
)
from .form_scope import mfds_form_scope_applies
from .linking_candidates import (
    AmbiguousSingleCandidate,
    CriterionCandidate,
    PairCriterionCandidate,
    code_options as _code_options,
    code_signature as _code_signature,
    hybrid_options as _hybrid_options,
    load_bridge_maps as _load_bridge_maps,
)
from .preprocessing import (
    canonicalize_link_ingredient_code,
    normalize_ingredient_identity,
    normalize_korean_identity,
    qualifier_applies,
)


def _criterion_key(category: str, ingredient_name: object, rule_value: object) -> tuple[str, str, str]:
    effect = normalize_ingredient_identity(rule_value) if category == DUPLICATION_CATEGORY else ""
    return category, normalize_ingredient_identity(ingredient_name), effect


def _product_key(category: str, ingredient_name_en: object, effect_name: object) -> tuple[str, str, str]:
    effect = normalize_ingredient_identity(effect_name) if category == DUPLICATION_CATEGORY else ""
    return category, normalize_ingredient_identity(ingredient_name_en), effect


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


def _form_text(*values: object) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip())


def _criterion_form_applies(
    candidate: CriterionCandidate | PairCriterionCandidate,
    product_form: object,
) -> bool:
    if isinstance(candidate, CriterionCandidate) and candidate.all_forms_scope:
        return True
    if not candidate.strict_form_scope:
        return True
    return mfds_form_scope_applies(candidate.dosage_form, product_form)


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


def _dose_details_conflict(
    candidate: CriterionCandidate | AmbiguousSingleCandidate,
    product_details: object,
) -> bool:
    details = str(product_details or "").strip()
    if not details:
        return False
    return not (
        _dose_text_equivalent(details, candidate.rule_value)
        or (_evidence_text(details) and _evidence_text(details) == _evidence_text(candidate.dosage_form))
    )


def _dose_candidate_method(
    candidate: CriterionCandidate | AmbiguousSingleCandidate,
    *,
    product_ingredient_name_ko: object,
    product_details: object,
    direct_method: str | None,
) -> str | None:
    """Return a safe dose-link method, or None when product detail contradicts the XLSX row."""
    details = str(product_details or "").strip()
    rule_value = candidate.rule_value
    dosage_form = candidate.dosage_form
    if details:
        if _dose_text_equivalent(details, rule_value) or _dose_text_equivalent(
            details, getattr(candidate, "details", None)
        ) or (
            _evidence_text(details) and _evidence_text(details) == _evidence_text(dosage_form)
        ):
            return direct_method or "product_detail_evidence"
        return None
    if direct_method:
        return direct_method
    korean = normalize_korean_identity(product_ingredient_name_ko)
    if korean and korean in normalize_korean_identity(rule_value):
        return "rule_value_identity"
    return None


def materialize_product_criterion_links(con: sqlite3.Connection) -> dict:
    """Link product DUR rows to XLSX criteria using the materialized DUR bridge.

    Ingredient identity/scope preprocessing is owned by ``dur_bridge``. This
    function retains only product-specific responsibilities: exact source-name
    matches, dosage-form applicability, dose-detail conflicts, pair orientation,
    duplication effect groups, and reporting of ambiguities that block a product.
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
            rule_value_signatures,
            pair_signatures,
            hybrid_pair_signatures,
            ambiguous_single_by_signature,
            ambiguous_pair_signatures,
        ) = _load_bridge_maps(con)

        dur_forms_by_item: dict[str, set[str]] = defaultdict(set)
        for row in con.execute("SELECT item_seq,dosage_form FROM product_rules WHERE dosage_form IS NOT NULL"):
            form = str(row["dosage_form"] or "").strip()
            if form:
                dur_forms_by_item[str(row["item_seq"])].add(form)

        direct_single: dict[tuple[str, str, str], list[CriterionCandidate]] = defaultdict(list)
        direct_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
        for row in con.execute(
            """SELECT i.id,i.category,i.ingredient_name,i.paired_ingredient_name,
                      i.rule_value,i.dosage_form,i.details
               FROM ingredient_rules i
               LEFT JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id
               WHERE c.criterion_rule_id IS NULL"""
        ):
            criterion_id = int(row["id"])
            category = str(row["category"])
            raw_left = str(row["ingredient_name"] or "").strip()
            if not raw_left:
                continue
            if category == COMBINATION_CATEGORY:
                raw_right = str(row["paired_ingredient_name"] or "").strip()
                if raw_right:
                    left = normalize_ingredient_identity(raw_left)
                    right = normalize_ingredient_identity(raw_right)
                    if left and right:
                        direct_pairs[(left, right)].append(criterion_id)
                continue
            direct_single[_criterion_key(category, raw_left, row["rule_value"])].append(
                CriterionCandidate(
                    criterion_id,
                    "english_exact",
                    rule_value=row["rule_value"],
                    dosage_form=row["dosage_form"],
                    details=row["details"],
                )
            )

        con.execute("DELETE FROM product_criterion_links")
        links = 0
        linked_products: set[int] = set()
        method_counts: dict[str, int] = defaultdict(int)
        blocked_unresolved: dict[tuple[str, str, tuple[str, ...], str], dict] = {}

        rows = con.execute(
            """SELECT r.id,r.category,r.item_seq,r.ingredient_code,r.ingredient_name,r.ingredient_name_en,
                      r.effect_name,r.dosage_form,r.details,r.paired_item_seq,r.paired_ingredient_code,
                      r.paired_ingredient_name_en,p.dosage_form AS permit_dosage_form,
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
                left_name = normalize_ingredient_identity(row["ingredient_name_en"])
                right_name = normalize_ingredient_identity(row["paired_ingredient_name_en"])
                if left_name and right_name:
                    ids = direct_pairs.get((left_name, right_name), [])
                    orientation = "forward"
                    if not ids:
                        ids = direct_pairs.get((right_name, left_name), [])
                        orientation = "reverse"
                    matches.extend((criterion_id, "english_exact", orientation) for criterion_id in ids)

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
                left_form = _form_text(
                    row["dosage_form"],
                    row["permit_dosage_form"],
                    " ".join(sorted(dur_forms_by_item.get(str(row["item_seq"]), set()))),
                )
                right_form = _form_text(
                    row["paired_permit_dosage_form"],
                    " ".join(sorted(dur_forms_by_item.get(str(row["paired_item_seq"] or ""), set()))),
                )
                strict_left_form = row["dosage_form"] or row["permit_dosage_form"]
                strict_right_form = row["paired_permit_dosage_form"]
                if not matches:
                    found: dict[int, tuple[str, str]] = {}
                    for left_sig in left_code_options:
                        for right_sig in right_code_options:
                            for orientation, key in (
                                ("forward", (left_sig.signature_key, right_sig.signature_key)),
                                ("reverse", (right_sig.signature_key, left_sig.signature_key)),
                            ):
                                for candidate in pair_signatures.get(key, []):
                                    scoped_product_form = (
                                        strict_left_form if orientation == "forward" else strict_right_form
                                    )
                                    if not _criterion_form_applies(candidate, scoped_product_form):
                                        continue
                                    if orientation == "forward":
                                        left_ok = qualifier_applies(candidate.left_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.right_qualifier, right_form)
                                    else:
                                        left_ok = qualifier_applies(candidate.right_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.left_qualifier, right_form)
                                    if not (left_ok and right_ok):
                                        continue
                                    method = candidate.method
                                    if (
                                        left_sig.method == "permit_composition" and left_sig.component_count > 1
                                    ) or (
                                        right_sig.method == "permit_composition" and right_sig.component_count > 1
                                    ):
                                        method = "permit_composition"
                                    found.setdefault(candidate.criterion_id, (method, orientation))
                    matches.extend(
                        (criterion_id, method, orientation)
                        for criterion_id, (method, orientation) in found.items()
                    )

                if not matches:
                    left_hybrid = _hybrid_options(
                        category, row["ingredient_code"], row["item_seq"], code_map, item_signatures
                    )
                    right_hybrid = _hybrid_options(
                        category,
                        row["paired_ingredient_code"],
                        row["paired_item_seq"],
                        code_map,
                        item_signatures,
                    )
                    found: dict[int, tuple[str, str]] = {}
                    for left_sig in left_hybrid:
                        for right_sig in right_hybrid:
                            for orientation, key in (
                                ("forward", (left_sig.signature_key, right_sig.signature_key)),
                                ("reverse", (right_sig.signature_key, left_sig.signature_key)),
                            ):
                                for candidate in hybrid_pair_signatures.get(key, []):
                                    scoped_product_form = (
                                        strict_left_form if orientation == "forward" else strict_right_form
                                    )
                                    if not _criterion_form_applies(candidate, scoped_product_form):
                                        continue
                                    if orientation == "forward":
                                        left_ok = qualifier_applies(candidate.left_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.right_qualifier, right_form)
                                    else:
                                        left_ok = qualifier_applies(candidate.right_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.left_qualifier, right_form)
                                    if left_ok and right_ok:
                                        found.setdefault(candidate.criterion_id, ("permit_composition", orientation))
                    matches.extend(
                        (criterion_id, method, orientation)
                        for criterion_id, (method, orientation) in found.items()
                    )

                if not matches:
                    for left_sig in left_code_options:
                        for right_sig in right_code_options:
                            for key in (
                                (left_sig.signature_key, right_sig.signature_key),
                                (right_sig.signature_key, left_sig.signature_key),
                            ):
                                blocked_unresolved.update(ambiguous_pair_signatures.get(key, {}))
            else:
                effect = normalize_ingredient_identity(row["effect_name"]) if category == DUPLICATION_CATEGORY else ""
                direct_candidates = direct_single.get(
                    _product_key(category, row["ingredient_name_en"], row["effect_name"]), []
                )
                direct_name_seen = bool(direct_candidates)
                if category == DOSE_CATEGORY:
                    for candidate in direct_candidates:
                        method = _dose_candidate_method(
                            candidate,
                            product_ingredient_name_ko=row["ingredient_name"],
                            product_details=row["details"],
                            direct_method="english_exact",
                        )
                        if method:
                            matches.append((candidate.criterion_id, method, None))
                else:
                    matches.extend((candidate.criterion_id, "english_exact", None) for candidate in direct_candidates)

                code_options = _code_options(
                    category,
                    row["ingredient_code"],
                    row["item_seq"],
                    code_map,
                    item_signatures,
                    category_item_signatures,
                )
                form = _form_text(row["dosage_form"], row["permit_dosage_form"])
                strict_form = row["dosage_form"] or row["permit_dosage_form"]
                if not matches:
                    found: dict[int, str] = {}
                    for product_sig in code_options:
                        for candidate in single_signatures.get(
                            (category, effect, product_sig.signature_key), []
                        ):
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
                            if not qualifier_applies(candidate.qualifier, form):
                                continue
                            method = candidate.method
                            if product_sig.method == "permit_composition" and product_sig.component_count > 1:
                                method = "permit_composition"
                            if category == DOSE_CATEGORY:
                                method = _dose_candidate_method(
                                    candidate,
                                    product_ingredient_name_ko=row["ingredient_name"],
                                    product_details=row["details"],
                                    direct_method=method,
                                )
                                if not method:
                                    continue
                            found.setdefault(candidate.criterion_id, method)
                    matches.extend((criterion_id, method, None) for criterion_id, method in found.items())

                if not matches and category == DOSE_CATEGORY:
                    found: dict[int, str] = {}
                    for product_sig in code_options:
                        for candidate in rule_value_signatures.get(
                            (category, product_sig.signature_key), []
                        ):
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
                            method = _dose_candidate_method(
                                candidate,
                                product_ingredient_name_ko=row["ingredient_name"],
                                product_details=row["details"],
                                direct_method=None,
                            )
                            if method:
                                found.setdefault(candidate.criterion_id, method)
                    matches.extend((criterion_id, method, None) for criterion_id, method in found.items())

                if not matches and not direct_name_seen:
                    direct_code = _code_signature(category, row["ingredient_code"], code_map)
                    direct_code_keys = {direct_code.signature_key} if direct_code else set()
                    for signature in direct_code_keys:
                        for candidate in ambiguous_single_by_signature.get(
                            (category, effect, signature), []
                        ):
                            method = None
                            if category == DOSE_CATEGORY:
                                method = _dose_candidate_method(
                                    candidate,
                                    product_ingredient_name_ko=row["ingredient_name"],
                                    product_details=row["details"],
                                    direct_method=None,
                                )
                            if method:
                                matches.append((candidate.criterion_id, method, None))
                            elif category == DOSE_CATEGORY and _dose_details_conflict(candidate, row["details"]):
                                continue
                            else:
                                blocked_unresolved[candidate.record_key] = candidate.record

            if not matches:
                continue
            inserted, inserted_methods = _insert_links(con, product_rule_id, matches)
            if inserted:
                links += inserted
                linked_products.add(product_rule_id)
                for method, count in inserted_methods.items():
                    method_counts[method] += count

        total_product_rules = int(con.execute("SELECT COUNT(*) FROM product_rules").fetchone()[0])
        unresolved = [blocked_unresolved[key] for key in sorted(blocked_unresolved)]
        return {
            "product_criterion_links": links,
            "linked_product_rules": len(linked_products),
            "unlinked_product_rules": total_product_rules - len(linked_products),
            "criterion_link_methods": dict(sorted(method_counts.items())),
            "unresolved_link_ambiguities": unresolved,
            "unresolved_link_ambiguity_count": len(unresolved),
            "unresolved_link_identities": unresolved,
            "unresolved_link_identity_count": len(unresolved),
        }
    finally:
        con.row_factory = previous_factory


__all__ = ["canonicalize_link_ingredient_code", "materialize_product_criterion_links"]
