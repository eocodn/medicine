from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from .dur_bridge import signature_key
from .mfds_remark_registry import reviewed_mfds_remark


@dataclass(frozen=True)
class CriterionCandidate:
    criterion_id: int
    method: str
    rule_value: str | None = None
    dosage_form: str | None = None
    details: str | None = None
    exact_composition_scope: bool = False
    all_forms_scope: bool = False


@dataclass(frozen=True)
class PairCriterionCandidate:
    criterion_id: int
    method: str
    dosage_form: str | None = None


@dataclass(frozen=True)
class ProductSignature:
    signature_key: str
    component_count: int
    method: str
    evidence_kind: str


def _evidence_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s,]+", "", text)


def load_bridge_maps(con: sqlite3.Connection):
    code_map: dict[tuple[str, str], str] = {}
    for row in con.execute(
        """SELECT category,source_ingredient_code,canonical_ingredient_code
           FROM dur_ingredient_code_map"""
    ):
        code_map[(str(row[0]), str(row[1]))] = str(row[2])

    item_signatures: dict[tuple[str, str], list[ProductSignature]] = defaultdict(list)
    for row in con.execute(
        """SELECT item_seq,signature_type,signature_key,component_count,match_method,evidence_kind
           FROM dur_product_item_signatures"""
    ):
        item_signatures[(str(row[0]), str(row[1]))].append(
            ProductSignature(str(row[2]), int(row[3]), str(row[4]), str(row[5]))
        )

    category_item_signatures: dict[tuple[str, str], list[ProductSignature]] = defaultdict(list)
    for row in con.execute(
        """SELECT item_seq,category,signature_key,component_count,match_method,evidence_kind
           FROM dur_product_category_signatures"""
    ):
        category_item_signatures[(str(row[0]), str(row[1]))].append(
            ProductSignature(str(row[2]), int(row[3]), str(row[4]), str(row[5]))
        )

    single_signatures: dict[tuple[str, str, str], list[CriterionCandidate]] = defaultdict(list)
    for row in con.execute(
        """SELECT s.criterion_rule_id,s.category,s.effect_key,s.signature_key,
                  s.match_method,i.rule_value,i.dosage_form,i.details,i.qualifier_note,s.evidence_kind
           FROM dur_criterion_signatures s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id"""
    ):
        qualifier = reviewed_mfds_remark(row[1], row[8])
        all_compositions = bool(
            qualifier and qualifier.mode == "composition_scope" and qualifier.value == "all"
        )
        candidate = CriterionCandidate(
            criterion_id=int(row[0]),
            method=str(row[4]),
            rule_value=row[5],
            dosage_form=row[6],
            details=row[7],
            exact_composition_scope=(
                str(row[9]) == "mfds_criterion_composition" and not all_compositions
            ),
            all_forms_scope=_evidence_text(row[7]) == "모든제형",
        )
        single_signatures[(str(row[1]), str(row[2]), str(row[3]))].append(candidate)

    pair_signatures: dict[tuple[str, str], list[PairCriterionCandidate]] = defaultdict(list)
    for row in con.execute(
        """SELECT s.criterion_rule_id,s.left_signature_key,s.right_signature_key,
                  s.match_method,i.dosage_form
           FROM dur_criterion_pair_signatures s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id"""
    ):
        pair_signatures[(str(row[1]), str(row[2]))].append(
            PairCriterionCandidate(int(row[0]), str(row[3]), row[4])
        )

    return code_map, item_signatures, category_item_signatures, single_signatures, pair_signatures


def code_signature(
    category: str,
    raw_code: object,
    code_map: dict[tuple[str, str], str],
) -> ProductSignature | None:
    source_code = str(raw_code or "").strip()
    canonical_code = code_map.get((category, source_code))
    if not canonical_code:
        return None
    return ProductSignature(
        signature_key(frozenset({canonical_code})),
        1,
        "mfds_ingredient_code",
        "mfds_code_scope",
    )


def code_options(
    category: str,
    raw_code: object,
    item_seq: object,
    code_map: dict[tuple[str, str], str],
    item_signatures: dict[tuple[str, str], list[ProductSignature]],
    category_item_signatures: dict[tuple[str, str], list[ProductSignature]],
) -> list[ProductSignature]:
    values: list[ProductSignature] = []
    direct = code_signature(category, raw_code, code_map)
    if direct:
        values.append(direct)
    values.extend(item_signatures.get((str(item_seq or ""), "code"), []))
    values.extend(category_item_signatures.get((str(item_seq or ""), category), []))
    deduped: dict[str, ProductSignature] = {}
    evidence_rank = {
        "mfds_code_scope": 0,
        "permit_composition": 1,
        "category_permit_composition": 2,
        "category_single_component_rule": 2,
    }
    for value in values:
        current = deduped.get(value.signature_key)
        if current is None or evidence_rank.get(value.evidence_kind, 0) > evidence_rank.get(
            current.evidence_kind, 0
        ):
            deduped[value.signature_key] = value
    return list(deduped.values())


__all__ = [
    "CriterionCandidate",
    "PairCriterionCandidate",
    "ProductSignature",
    "code_options",
    "code_signature",
    "load_bridge_maps",
]
