from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass

from .dur_bridge import signature_key
from .preprocessing import canonicalize_link_ingredient_code


@dataclass(frozen=True)
class CriterionCandidate:
    criterion_id: int
    method: str
    qualifier: str | None = None
    rule_value: str | None = None
    dosage_form: str | None = None
    details: str | None = None
    strict_form_scope: bool = False
    exact_composition_scope: bool = False
    all_forms_scope: bool = False


@dataclass(frozen=True)
class PairCriterionCandidate:
    criterion_id: int
    method: str
    left_qualifier: str | None = None
    right_qualifier: str | None = None
    dosage_form: str | None = None
    strict_form_scope: bool = False


@dataclass(frozen=True)
class AmbiguousSingleCandidate:
    criterion_id: int
    record_key: tuple[str, str, tuple[str, ...], str]
    record: dict
    rule_value: str | None
    dosage_form: str | None


@dataclass(frozen=True)
class ProductSignature:
    signature_key: str
    component_count: int
    method: str
    evidence_kind: str


def _evidence_text(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"[\s,]+", "", text)


def _record_key(category: str, ambiguity: dict) -> tuple[str, str, tuple[str, ...], str]:
    return (
        category,
        str(ambiguity["ingredient_name"]),
        tuple(ambiguity["candidate_codes"]),
        str(ambiguity.get("reason", "active_moiety_multiple_codes")),
    )


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
    rule_value_signatures: dict[tuple[str, str], list[CriterionCandidate]] = defaultdict(list)
    for row in con.execute(
        """SELECT s.criterion_rule_id,s.category,s.effect_key,s.signature_type,s.signature_key,
                  s.qualifier,s.match_method,i.rule_value,i.dosage_form,i.details,i.note,
                  CASE WHEN c.criterion_rule_id IS NULL THEN 0 ELSE 1 END AS strict_form_scope,
                  s.evidence_kind
           FROM dur_criterion_signatures s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id
           LEFT JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id"""
    ):
        all_compositions = _evidence_text(row[10]) == "단일제·복합제포함"
        candidate = CriterionCandidate(
            criterion_id=int(row[0]),
            method=str(row[6]),
            qualifier=row[5],
            rule_value=row[7],
            dosage_form=row[8],
            details=row[9],
            strict_form_scope=bool(row[11]),
            exact_composition_scope=(
                str(row[12]) == "mfds_criterion_composition" and not all_compositions
            ),
            all_forms_scope=_evidence_text(row[9]) == "모든제형",
        )
        if row[3] == "rule_value":
            rule_value_signatures[(str(row[1]), str(row[4]))].append(candidate)
        else:
            single_signatures[(str(row[1]), str(row[2]), str(row[4]))].append(candidate)

    pair_signatures: dict[tuple[str, str], list[PairCriterionCandidate]] = defaultdict(list)
    hybrid_pair_signatures: dict[tuple[str, str], list[PairCriterionCandidate]] = defaultdict(list)
    for row in con.execute(
        """SELECT s.criterion_rule_id,s.signature_type,s.left_signature_key,s.right_signature_key,
                  s.left_qualifier,s.right_qualifier,s.match_method,i.dosage_form,
                  CASE WHEN c.criterion_rule_id IS NULL THEN 0 ELSE 1 END AS strict_form_scope
           FROM dur_criterion_pair_signatures s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id
           LEFT JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id"""
    ):
        candidate = PairCriterionCandidate(
            int(row[0]), str(row[6]), row[4], row[5], row[7], bool(row[8])
        )
        target = hybrid_pair_signatures if row[1] == "hybrid" else pair_signatures
        target[(str(row[2]), str(row[3]))].append(candidate)

    ambiguous_single: dict[tuple[str, str, str], list[AmbiguousSingleCandidate]] = defaultdict(list)
    for row in con.execute(
        """SELECT criterion_rule_id,category,effect_key,signature_key,record_json,rule_value,dosage_form
           FROM dur_single_ambiguities"""
    ):
        record = json.loads(row[4])
        ambiguous_single[(str(row[1]), str(row[2]), str(row[3]))].append(
            AmbiguousSingleCandidate(
                int(row[0]), _record_key(str(row[1]), record), record, row[5], row[6]
            )
        )

    ambiguous_pair: dict[tuple[str, str], dict[tuple[str, str, tuple[str, ...], str], dict]] = defaultdict(dict)
    for row in con.execute(
        "SELECT left_signature_key,right_signature_key,record_json FROM dur_pair_ambiguities"
    ):
        record = json.loads(row[2])
        key = _record_key(str(record["category"]), record)
        ambiguous_pair[(str(row[0]), str(row[1]))][key] = record

    return (
        code_map,
        item_signatures,
        category_item_signatures,
        single_signatures,
        rule_value_signatures,
        pair_signatures,
        hybrid_pair_signatures,
        ambiguous_single,
        ambiguous_pair,
    )


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


def hybrid_options(
    category: str,
    raw_code: object,
    item_seq: object,
    code_map: dict[tuple[str, str], str],
    item_signatures: dict[tuple[str, str], list[ProductSignature]],
) -> list[ProductSignature]:
    values = item_signatures.get((str(item_seq or ""), "hybrid"), [])
    if values:
        return values
    source_code = str(raw_code or "").strip()
    canonical_code = code_map.get((category, source_code))
    if not canonical_code:
        return []
    return [
        ProductSignature(
            signature_key(frozenset({"code:" + canonical_code})),
            1,
            "permit_composition",
            "mfds_code_scope",
        )
    ]


__all__ = [
    "AmbiguousSingleCandidate",
    "CriterionCandidate",
    "PairCriterionCandidate",
    "ProductSignature",
    "code_options",
    "code_signature",
    "hybrid_options",
    "load_bridge_maps",
]
