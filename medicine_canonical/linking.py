from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass
from typing import Iterable

from .preprocessing import (
    IdentityResolver,
    canonicalize_link_ingredient_code,
    normalize_ingredient_identity,
    normalize_korean_identity,
    qualifier_applies,
)

COMBINATION_CATEGORY = "combination_contraindication"
DUPLICATION_CATEGORY = "therapeutic_duplication_caution"
DOSE_CATEGORY = "dose_caution"


@dataclass(frozen=True)
class CriterionCandidate:
    criterion_id: int
    method: str
    qualifier: str | None = None
    rule_value: str | None = None
    dosage_form: str | None = None


@dataclass(frozen=True)
class PairCriterionCandidate:
    criterion_id: int
    method: str
    left_qualifier: str | None = None
    right_qualifier: str | None = None


@dataclass(frozen=True)
class AmbiguousSingleCandidate:
    criterion_id: int
    record_key: tuple[str, str, tuple[str, ...], str]
    record: dict
    rule_value: str | None
    dosage_form: str | None


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


def _method_for_resolution(preprocessed: bool, signature: frozenset[str]) -> str:
    if len(signature) > 1:
        return "permit_composition"
    return "ingredient_preprocessed" if preprocessed else "mfds_ingredient_code"


def _form_text(*values: object) -> str:
    return " ".join(str(value or "").strip() for value in values if str(value or "").strip())


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
        if _dose_text_equivalent(details, rule_value) or (
            _evidence_text(details) and _evidence_text(details) == _evidence_text(dosage_form)
        ):
            return direct_method or "product_detail_evidence"
        # Detailed MFDS product text is more specific than a name-only XLSX match.
        return None

    if direct_method:
        return direct_method

    korean = normalize_korean_identity(product_ingredient_name_ko)
    if korean and korean in normalize_korean_identity(rule_value):
        return "rule_value_identity"
    return None


def _record_key(category: str, ambiguity: dict) -> tuple[str, str, tuple[str, ...], str]:
    return (
        category,
        str(ambiguity["ingredient_name"]),
        tuple(ambiguity["candidate_codes"]),
        str(ambiguity["reason"]),
    )


def materialize_product_criterion_links(con: sqlite3.Connection) -> dict:
    """Materialize conservative product-DUR ↔ XLSX criterion links.

    Source fields are immutable. Link-time preprocessing may use reviewed code
    equivalences, source-declared parenthetical aliases/qualifiers, controlled
    active-moiety normalization, exact permit ITEM_SEQ ingredient composition,
    duplicate ingredient collapse, exact Korean dose-rule evidence, and MFDS
    product-detail evidence. An ambiguity is reported only if it actually blocks
    a current product rule; unrelated ambiguous XLSX names do not fail a build.
    """

    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        resolver = IdentityResolver()
        for row in con.execute(
            """
            SELECT category,ingredient_name,ingredient_name_en,ingredient_code,
                   paired_ingredient_name,paired_ingredient_name_en,paired_ingredient_code
            FROM product_rules
            """
        ):
            resolver.add(row["category"], row["ingredient_name_en"], row["ingredient_code"], row["ingredient_name"])
            resolver.add(
                row["category"], row["paired_ingredient_name_en"], row["paired_ingredient_code"], row["paired_ingredient_name"]
            )

        dur_forms_by_item: dict[str, set[str]] = defaultdict(set)
        for row in con.execute("SELECT item_seq,dosage_form FROM product_rules WHERE dosage_form IS NOT NULL"):
            form = str(row["dosage_form"] or "").strip()
            if form:
                dur_forms_by_item[str(row["item_seq"])].add(form)

        product_compositions: dict[str, frozenset[str]] = {}
        product_hybrid_compositions: dict[str, frozenset[str]] = {}
        for row in con.execute("SELECT item_seq,ingredient_text FROM products"):
            item_seq = str(row["item_seq"])
            signature = resolver.resolve_permit_composition(row["ingredient_text"])
            if signature:
                product_compositions[item_seq] = signature
            hybrid = resolver.resolve_hybrid_expression(row["ingredient_text"], None)
            if len(hybrid.signatures) == 1:
                product_hybrid_compositions[item_seq] = hybrid.signatures[0]

        direct_single: dict[tuple[str, str, str], list[CriterionCandidate]] = defaultdict(list)
        direct_pairs: dict[tuple[str, str], list[int]] = defaultdict(list)
        single_signatures: dict[tuple[str, str, frozenset[str]], list[CriterionCandidate]] = defaultdict(list)
        pair_signatures: dict[tuple[frozenset[str], frozenset[str]], list[PairCriterionCandidate]] = defaultdict(list)
        hybrid_pair_signatures: dict[tuple[frozenset[str], frozenset[str]], list[PairCriterionCandidate]] = defaultdict(list)
        rule_value_signatures: dict[tuple[str, frozenset[str]], list[CriterionCandidate]] = defaultdict(list)
        ambiguous_single_by_code: dict[tuple[str, str, str], list[AmbiguousSingleCandidate]] = defaultdict(list)
        ambiguous_pair_signatures: dict[
            tuple[frozenset[str], frozenset[str]], dict[tuple[str, str, tuple[str, ...], str], dict]
        ] = defaultdict(dict)

        for row in con.execute(
            """
            SELECT id,category,ingredient_name,paired_ingredient_name,rule_value,dosage_form
            FROM ingredient_rules
            """
        ):
            criterion_id = int(row["id"])
            category = str(row["category"])
            raw_left = str(row["ingredient_name"] or "").strip()
            if not raw_left:
                continue

            if category == COMBINATION_CATEGORY:
                raw_right = str(row["paired_ingredient_name"] or "").strip()
                if not raw_right:
                    continue
                left_direct = normalize_ingredient_identity(raw_left)
                right_direct = normalize_ingredient_identity(raw_right)
                if left_direct and right_direct:
                    direct_pairs[(left_direct, right_direct)].append(criterion_id)

                left = resolver.resolve_expression(raw_left, category)
                right = resolver.resolve_expression(raw_right, category)
                for left_sig in left.signatures:
                    for right_sig in right.signatures:
                        method = "permit_composition" if len(left_sig) > 1 or len(right_sig) > 1 else (
                            "ingredient_preprocessed" if left.preprocessed or right.preprocessed else "mfds_ingredient_code"
                        )
                        pair_signatures[(left_sig, right_sig)].append(
                            PairCriterionCandidate(criterion_id, method, left.qualifier, right.qualifier)
                        )

                hybrid_left = resolver.resolve_hybrid_expression(raw_left, category)
                hybrid_right = resolver.resolve_hybrid_expression(raw_right, category)
                for left_sig in hybrid_left.signatures:
                    for right_sig in hybrid_right.signatures:
                        hybrid_pair_signatures[(left_sig, right_sig)].append(
                            PairCriterionCandidate(criterion_id, "permit_composition", hybrid_left.qualifier, hybrid_right.qualifier)
                        )

                # Preserve the other resolved side so only a real product pair can surface the ambiguity.
                if left.ambiguities and right.signatures:
                    for ambiguity in left.ambiguities:
                        record = {"category": category, **ambiguity}
                        key = _record_key(category, ambiguity)
                        for code in ambiguity["candidate_codes"]:
                            for right_sig in right.signatures:
                                ambiguous_pair_signatures[(frozenset({code}), right_sig)][key] = record
                if right.ambiguities and left.signatures:
                    for ambiguity in right.ambiguities:
                        record = {"category": category, **ambiguity}
                        key = _record_key(category, ambiguity)
                        for code in ambiguity["candidate_codes"]:
                            for left_sig in left.signatures:
                                ambiguous_pair_signatures[(left_sig, frozenset({code}))][key] = record
                if left.ambiguities and right.ambiguities:
                    for left_ambiguity in left.ambiguities:
                        for right_ambiguity in right.ambiguities:
                            for ambiguity in (left_ambiguity, right_ambiguity):
                                record = {"category": category, **ambiguity}
                                key = _record_key(category, ambiguity)
                                for left_code in left_ambiguity["candidate_codes"]:
                                    for right_code in right_ambiguity["candidate_codes"]:
                                        ambiguous_pair_signatures[(frozenset({left_code}), frozenset({right_code}))][key] = record
                continue

            effect = normalize_ingredient_identity(row["rule_value"]) if category == DUPLICATION_CATEGORY else ""
            base_candidate = CriterionCandidate(
                criterion_id,
                "english_exact",
                rule_value=row["rule_value"],
                dosage_form=row["dosage_form"],
            )
            direct_single[_criterion_key(category, raw_left, row["rule_value"])].append(base_candidate)

            resolved = resolver.resolve_expression(raw_left, category)
            for signature in resolved.signatures:
                single_signatures[(category, effect, signature)].append(
                    CriterionCandidate(
                        criterion_id,
                        _method_for_resolution(resolved.preprocessed, signature),
                        resolved.qualifier,
                        row["rule_value"],
                        row["dosage_form"],
                    )
                )
            for ambiguity in resolved.ambiguities:
                record = {"category": category, **ambiguity}
                key = _record_key(category, ambiguity)
                for code in ambiguity["candidate_codes"]:
                    ambiguous_single_by_code[(category, effect, code)].append(
                        AmbiguousSingleCandidate(
                            criterion_id,
                            key,
                            record,
                            row["rule_value"],
                            row["dosage_form"],
                        )
                    )

            if category == DOSE_CATEGORY:
                for rule_signature in resolver.extract_rule_value_korean_signatures(row["rule_value"], category):
                    rule_value_signatures[(category, rule_signature)].append(
                        CriterionCandidate(
                            criterion_id,
                            "rule_value_identity",
                            rule_value=row["rule_value"],
                            dosage_form=row["dosage_form"],
                        )
                    )

        con.execute("DELETE FROM product_criterion_links")
        links = 0
        linked_products: set[int] = set()
        method_counts: dict[str, int] = defaultdict(int)
        blocked_unresolved: dict[tuple[str, str, tuple[str, ...], str], dict] = {}

        rows = con.execute(
            """
            SELECT r.id,r.category,r.item_seq,r.ingredient_code,r.ingredient_name,r.ingredient_name_en,
                   r.effect_name,r.dosage_form,r.details,
                   r.paired_item_seq,r.paired_ingredient_code,r.paired_ingredient_name_en,
                   p.dosage_form AS permit_dosage_form,
                   pp.dosage_form AS paired_permit_dosage_form
            FROM product_rules r
            LEFT JOIN products p ON p.item_seq=r.item_seq
            LEFT JOIN products pp ON pp.item_seq=r.paired_item_seq
            ORDER BY r.id
            """
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

                left_code = canonicalize_link_ingredient_code(row["ingredient_code"])
                right_code = canonicalize_link_ingredient_code(row["paired_ingredient_code"])
                left_single = frozenset({left_code}) if left_code else None
                right_single = frozenset({right_code}) if right_code else None
                left_comp = product_compositions.get(str(row["item_seq"]))
                right_comp = product_compositions.get(str(row["paired_item_seq"] or ""))
                left_hybrid = product_hybrid_compositions.get(str(row["item_seq"]))
                right_hybrid = product_hybrid_compositions.get(str(row["paired_item_seq"] or ""))
                if not left_hybrid and left_code:
                    left_hybrid = frozenset({"code:" + left_code})
                if not right_hybrid and right_code:
                    right_hybrid = frozenset({"code:" + right_code})
                left_options = [sig for sig in (left_single, left_comp) if sig]
                right_options = [sig for sig in (right_single, right_comp) if sig]

                if not matches:
                    left_form = _form_text(
                        row["dosage_form"], row["permit_dosage_form"],
                        " ".join(sorted(dur_forms_by_item.get(str(row["item_seq"]), set()))),
                    )
                    right_form = _form_text(
                        row["paired_permit_dosage_form"],
                        " ".join(sorted(dur_forms_by_item.get(str(row["paired_item_seq"] or ""), set()))),
                    )
                    found: dict[int, tuple[str, str]] = {}
                    for left_sig in dict.fromkeys(left_options):
                        for right_sig in dict.fromkeys(right_options):
                            for orientation, key in (
                                ("forward", (left_sig, right_sig)),
                                ("reverse", (right_sig, left_sig)),
                            ):
                                for candidate in pair_signatures.get(key, []):
                                    if orientation == "forward":
                                        left_ok = qualifier_applies(candidate.left_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.right_qualifier, right_form)
                                    else:
                                        left_ok = qualifier_applies(candidate.right_qualifier, left_form)
                                        right_ok = qualifier_applies(candidate.left_qualifier, right_form)
                                    if not (left_ok and right_ok):
                                        continue
                                    method = candidate.method
                                    if (left_comp and left_sig == left_comp and len(left_sig) > 1) or (
                                        right_comp and right_sig == right_comp and len(right_sig) > 1
                                    ):
                                        method = "permit_composition"
                                    found.setdefault(candidate.criterion_id, (method, orientation))
                    matches.extend((criterion_id, method, orientation) for criterion_id, (method, orientation) in found.items())

                if not matches and left_hybrid and right_hybrid:
                    found: dict[int, tuple[str, str]] = {}
                    for orientation, key in (
                        ("forward", (left_hybrid, right_hybrid)),
                        ("reverse", (right_hybrid, left_hybrid)),
                    ):
                        for candidate in hybrid_pair_signatures.get(key, []):
                            if orientation == "forward":
                                left_ok = qualifier_applies(candidate.left_qualifier, left_form)
                                right_ok = qualifier_applies(candidate.right_qualifier, right_form)
                            else:
                                left_ok = qualifier_applies(candidate.right_qualifier, left_form)
                                right_ok = qualifier_applies(candidate.left_qualifier, right_form)
                            if left_ok and right_ok:
                                found.setdefault(candidate.criterion_id, ("permit_composition", orientation))
                    matches.extend((criterion_id, method, orientation) for criterion_id, (method, orientation) in found.items())

                if not matches:
                    for left_sig in dict.fromkeys(left_options):
                        for right_sig in dict.fromkeys(right_options):
                            for key in ((left_sig, right_sig), (right_sig, left_sig)):
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

                code = canonicalize_link_ingredient_code(row["ingredient_code"])
                individual = frozenset({code}) if code else None
                composition = product_compositions.get(str(row["item_seq"]))
                form = _form_text(row["dosage_form"], row["permit_dosage_form"])

                if not matches:
                    found: dict[int, str] = {}
                    for signature in dict.fromkeys(sig for sig in (individual, composition) if sig):
                        for candidate in single_signatures.get((category, effect, signature), []):
                            if not qualifier_applies(candidate.qualifier, form):
                                continue
                            method = candidate.method
                            if composition and signature == composition and len(signature) > 1:
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
                    for signature in dict.fromkeys(sig for sig in (individual, composition) if sig):
                        for candidate in rule_value_signatures.get((category, signature), []):
                            method = _dose_candidate_method(
                                candidate,
                                product_ingredient_name_ko=row["ingredient_name"],
                                product_details=row["details"],
                                direct_method=None,
                            )
                            if method:
                                found.setdefault(candidate.criterion_id, method)
                    matches.extend((criterion_id, method, None) for criterion_id, method in found.items())

                if not matches and code and not direct_name_seen:
                    ambiguous_candidates = ambiguous_single_by_code.get((category, effect, code), [])
                    for candidate in ambiguous_candidates:
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
                            # A conflicting MFDS product detail is a known source-criterion gap,
                            # not an unresolved identity ambiguity. Keep it unlinked without guessing.
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
