from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from .preprocessing import (
    IdentityResolver,
    canonicalize_link_ingredient_code,
    normalize_ingredient_identity,
    parse_ingredient_expression,
)
from .substance_text import normalize_substance_name


COMBINATION_CATEGORY = "combination_contraindication"
DUPLICATION_CATEGORY = "therapeutic_duplication_caution"
DOSE_CATEGORY = "dose_caution"


def signature_key(signature: frozenset[str]) -> str:
    return json.dumps(sorted(signature), ensure_ascii=False, separators=(",", ":"))


def _concept_id(category: str, ingredient_code: str) -> str:
    digest = hashlib.sha256(f"{category}\0{ingredient_code}".encode("utf-8")).hexdigest()[:24].upper()
    return "DURC_" + digest


def _method_for_resolution(preprocessed: bool, signature: frozenset[str]) -> str:
    if len(signature) > 1:
        return "permit_composition"
    return "ingredient_preprocessed" if preprocessed else "mfds_ingredient_code"


def _evidence_kind(*, preprocessed: bool, composition: bool = False) -> str:
    if composition:
        return "composition_scope"
    return "dur_scope_inference" if preprocessed else "mfds_code_scope"


def _record_key(category: str, ambiguity: dict) -> str:
    return json.dumps(
        [
            category,
            str(ambiguity["ingredient_name"]),
            sorted(ambiguity["candidate_codes"]),
            str(ambiguity["reason"]),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _load_substance_names(path: str | Path | None) -> dict[str, str]:
    if path is None:
        return {}
    db_path = Path(path)
    if not db_path.exists():
        raise FileNotFoundError(f"canonical substance database not found: {db_path}")
    with closing(sqlite3.connect(db_path)) as con:
        return {
            str(row[0]): str(row[1])
            for row in con.execute("SELECT normalized_name,substance_id FROM substance_names")
        }


def _source_substance_ids(name: object, name_to_substance: dict[str, str]) -> set[str]:
    if not name_to_substance:
        return set()
    atoms = parse_ingredient_expression(name)
    if len(atoms) <= 1:
        substance_id = name_to_substance.get(normalize_substance_name(name))
        return {substance_id} if substance_id else set()

    # A top-level multi-component source expression is composition, not a new
    # precise substance. Admit it only when every component independently maps
    # to exactly one canonical substance; partial component sets fail closed.
    result: set[str] = set()
    for atom in atoms:
        atom_ids = {
            name_to_substance[normalized]
            for candidate in atom.names
            if (normalized := normalize_substance_name(candidate)) in name_to_substance
        }
        if len(atom_ids) != 1:
            return set()
        result.update(atom_ids)
    return result


def _insert_product_signature(
    con: sqlite3.Connection,
    *,
    item_seq: str,
    signature_type: str,
    signature: frozenset[str],
    match_method: str,
    evidence_kind: str,
) -> None:
    if not signature:
        return
    con.execute(
        """INSERT OR IGNORE INTO dur_product_item_signatures(
               item_seq,signature_type,signature_key,component_count,
               match_method,evidence_kind
           ) VALUES(?,?,?,?,?,?)""",
        (
            item_seq,
            signature_type,
            signature_key(signature),
            len(signature),
            match_method,
            evidence_kind,
        ),
    )


def materialize_dur_ingredient_bridge(
    con: sqlite3.Connection,
    substance_db_path: str | Path | None = None,
) -> dict:
    """Materialize the regulatory DUR-scope bridge independently of product linking.

    The bridge may state that several precise substances participate in one MFDS
    DUR ingredient concept. It never rewrites or merges canonical substances.
    Controlled suffix handling is retained here only as DUR-scope compatibility
    evidence; downstream product linking consumes the materialized bridge and no
    longer performs chemical/name inference itself.
    """

    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        for table in (
            "dur_pair_ambiguities",
            "dur_single_ambiguities",
            "dur_criterion_pair_signatures",
            "dur_criterion_signatures",
            "dur_product_category_signatures",
            "dur_product_item_signatures",
            "dur_concept_substances",
            "dur_ingredient_code_map",
            "dur_ingredient_concepts",
        ):
            con.execute(f"DELETE FROM {table}")

        resolver = IdentityResolver()
        product_rows = list(
            con.execute(
                """SELECT id,category,item_seq,ingredient_name,ingredient_name_en,ingredient_code,
                          paired_item_seq,paired_ingredient_name,paired_ingredient_name_en,
                          paired_ingredient_code
                   FROM product_rules ORDER BY id"""
            )
        )
        for row in product_rows:
            resolver.add(
                row["category"],
                row["ingredient_name_en"],
                row["ingredient_code"],
                row["ingredient_name"],
            )
            resolver.add(
                row["category"],
                row["paired_ingredient_name_en"],
                row["paired_ingredient_code"],
                row["paired_ingredient_name"],
            )

        for row in con.execute(
            """SELECT i.category,c.mixture_ingredient_codes_json,c.mixture_ingredient_names_json
               FROM ingredient_rules i
               JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id
               WHERE c.mixture_type='복합'"""
        ):
            codes = json.loads(str(row["mixture_ingredient_codes_json"] or "[]"))
            names = json.loads(str(row["mixture_ingredient_names_json"] or "[]"))
            if not isinstance(codes, list) or not isinstance(names, list) or len(codes) != len(names):
                raise ValueError("MFDS criterion has invalid mixture ingredient identity payload")
            for code, name in zip(codes, names, strict=True):
                resolver.add(row["category"], name, code)

        name_to_substance = _load_substance_names(substance_db_path)
        concepts: dict[tuple[str, str], str] = {}
        for row in product_rows:
            for source_code, source_name in (
                (row["ingredient_code"], row["ingredient_name_en"]),
                (row["paired_ingredient_code"], row["paired_ingredient_name_en"]),
            ):
                raw_code = str(source_code or "").strip()
                code = canonicalize_link_ingredient_code(raw_code)
                if not code:
                    continue
                category = str(row["category"])
                concept_id = concepts.setdefault((category, code), _concept_id(category, code))
                con.execute(
                    """INSERT OR IGNORE INTO dur_ingredient_concepts(concept_id,category,ingredient_code)
                       VALUES(?,?,?)""",
                    (concept_id, category, code),
                )
                con.execute(
                    """INSERT OR IGNORE INTO dur_ingredient_code_map(
                           category,source_ingredient_code,canonical_ingredient_code,concept_id
                       ) VALUES(?,?,?,?)""",
                    (category, raw_code, code, concept_id),
                )
                for substance_id in sorted(_source_substance_ids(source_name, name_to_substance)):
                    con.execute(
                        """INSERT OR IGNORE INTO dur_concept_substances(
                               concept_id,category,ingredient_code,substance_id,source_name,evidence_kind
                           ) VALUES(?,?,?,?,?,'direct_source_identity')""",
                        (concept_id, category, code, substance_id, str(source_name or "").strip()),
                    )

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

        for item_seq, composition in product_compositions.items():
            _insert_product_signature(
                con,
                item_seq=item_seq,
                signature_type="code",
                signature=composition,
                match_method=(
                    "permit_composition" if len(composition) > 1 else "mfds_ingredient_code"
                ),
                evidence_kind="permit_composition",
            )
        for item_seq, hybrid in product_hybrid_compositions.items():
            _insert_product_signature(
                con,
                item_seq=item_seq,
                signature_type="hybrid",
                signature=hybrid,
                match_method="permit_composition",
                evidence_kind="hybrid_permit_composition",
            )

        product_categories: dict[str, set[str]] = defaultdict(set)
        product_category_codes: dict[tuple[str, str], set[str]] = defaultdict(set)
        for row in product_rows:
            item_seq = str(row["item_seq"])
            category = str(row["category"])
            product_categories[item_seq].add(category)
            code = canonicalize_link_ingredient_code(row["ingredient_code"])
            if code:
                product_category_codes[(item_seq, category)].add(code)
        for row in con.execute("SELECT item_seq,ingredient_text FROM products"):
            item_seq = str(row["item_seq"])
            for category in sorted(product_categories.get(item_seq, set())):
                composition = resolver.resolve_permit_composition(
                    row["ingredient_text"], category
                )
                evidence_kind = "category_permit_composition"
                if not composition:
                    atoms = parse_ingredient_expression(row["ingredient_text"])
                    direct_codes = product_category_codes.get((item_seq, category), set())
                    if len(atoms) == 1 and len(direct_codes) == 1:
                        composition = frozenset(direct_codes)
                        evidence_kind = "category_single_component_rule"
                if not composition:
                    continue
                con.execute(
                    """INSERT OR IGNORE INTO dur_product_category_signatures(
                           item_seq,category,signature_key,component_count,match_method,evidence_kind
                       ) VALUES(?,?,?,?,?,?)""",
                    (
                        item_seq,
                        category,
                        signature_key(composition),
                        len(composition),
                        "permit_composition" if len(composition) > 1 else "mfds_ingredient_code",
                        evidence_kind,
                    ),
                )

        criterion_signatures = 0
        pair_signatures = 0
        ambiguity_rows = 0
        for row in con.execute(
            """SELECT i.id,i.category,i.ingredient_name,i.paired_ingredient_name,
                      i.rule_value,i.dosage_form,
                      c.ingredient_code AS criterion_ingredient_code,
                      c.paired_ingredient_code AS criterion_paired_ingredient_code,
                      c.mixture_type AS criterion_mixture_type,
                      c.mixture_ingredient_codes_json AS criterion_mixture_ingredient_codes_json
               FROM ingredient_rules i
               LEFT JOIN ingredient_rule_codes c ON c.criterion_rule_id=i.id
               ORDER BY i.id"""
        ):
            criterion_id = int(row["id"])
            category = str(row["category"])
            raw_left = str(row["ingredient_name"] or "").strip()
            if not raw_left:
                continue
            explicit_left_code = canonicalize_link_ingredient_code(
                row["criterion_ingredient_code"]
            )
            if category == COMBINATION_CATEGORY:
                raw_right = str(row["paired_ingredient_name"] or "").strip()
                if not raw_right:
                    continue
                explicit_right_code = canonicalize_link_ingredient_code(
                    row["criterion_paired_ingredient_code"]
                )
                if explicit_left_code or explicit_right_code:
                    if not (explicit_left_code and explicit_right_code):
                        raise ValueError(
                            f"combination criterion {criterion_id} has incomplete MFDS ingredient codes"
                        )
                    con.execute(
                        """INSERT OR IGNORE INTO dur_criterion_pair_signatures(
                               criterion_rule_id,signature_type,left_signature_key,right_signature_key,
                               left_qualifier,right_qualifier,match_method,evidence_kind
                           ) VALUES(?,'code',?,?,NULL,NULL,'mfds_ingredient_code','mfds_criterion_code')""",
                        (
                            criterion_id,
                            signature_key(frozenset({explicit_left_code})),
                            signature_key(frozenset({explicit_right_code})),
                        ),
                    )
                    pair_signatures += 1
                    continue
                left = resolver.resolve_expression(raw_left, category)
                right = resolver.resolve_expression(raw_right, category)
                for left_sig in left.signatures:
                    for right_sig in right.signatures:
                        method = (
                            "permit_composition"
                            if len(left_sig) > 1 or len(right_sig) > 1
                            else (
                                "ingredient_preprocessed"
                                if left.preprocessed or right.preprocessed
                                else "mfds_ingredient_code"
                            )
                        )
                        con.execute(
                            """INSERT OR IGNORE INTO dur_criterion_pair_signatures(
                                   criterion_rule_id,signature_type,left_signature_key,right_signature_key,
                                   left_qualifier,right_qualifier,match_method,evidence_kind
                               ) VALUES(?,'code',?,?,?,?,?,?)""",
                            (
                                criterion_id,
                                signature_key(left_sig),
                                signature_key(right_sig),
                                left.qualifier,
                                right.qualifier,
                                method,
                                _evidence_kind(
                                    preprocessed=left.preprocessed or right.preprocessed,
                                    composition=len(left_sig) > 1 or len(right_sig) > 1,
                                ),
                            ),
                        )
                        pair_signatures += 1

                hybrid_left = resolver.resolve_hybrid_expression(raw_left, category)
                hybrid_right = resolver.resolve_hybrid_expression(raw_right, category)
                for left_sig in hybrid_left.signatures:
                    for right_sig in hybrid_right.signatures:
                        con.execute(
                            """INSERT OR IGNORE INTO dur_criterion_pair_signatures(
                                   criterion_rule_id,signature_type,left_signature_key,right_signature_key,
                                   left_qualifier,right_qualifier,match_method,evidence_kind
                               ) VALUES(?,'hybrid',?,?,?,?,?,'hybrid_composition_scope')""",
                            (
                                criterion_id,
                                signature_key(left_sig),
                                signature_key(right_sig),
                                hybrid_left.qualifier,
                                hybrid_right.qualifier,
                                "permit_composition",
                            ),
                        )
                        pair_signatures += 1

                if left.ambiguities and right.signatures:
                    for ambiguity in left.ambiguities:
                        record = {"category": category, **ambiguity}
                        key = _record_key(category, ambiguity)
                        for code in ambiguity["candidate_codes"]:
                            for right_sig in right.signatures:
                                con.execute(
                                    """INSERT OR IGNORE INTO dur_pair_ambiguities(
                                           left_signature_key,right_signature_key,record_key,record_json
                                       ) VALUES(?,?,?,?)""",
                                    (
                                        signature_key(frozenset({code})),
                                        signature_key(right_sig),
                                        key,
                                        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                                    ),
                                )
                                ambiguity_rows += 1
                if right.ambiguities and left.signatures:
                    for ambiguity in right.ambiguities:
                        record = {"category": category, **ambiguity}
                        key = _record_key(category, ambiguity)
                        for code in ambiguity["candidate_codes"]:
                            for left_sig in left.signatures:
                                con.execute(
                                    """INSERT OR IGNORE INTO dur_pair_ambiguities(
                                           left_signature_key,right_signature_key,record_key,record_json
                                       ) VALUES(?,?,?,?)""",
                                    (
                                        signature_key(left_sig),
                                        signature_key(frozenset({code})),
                                        key,
                                        json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                                    ),
                                )
                                ambiguity_rows += 1
                if left.ambiguities and right.ambiguities:
                    for left_ambiguity in left.ambiguities:
                        for right_ambiguity in right.ambiguities:
                            for ambiguity in (left_ambiguity, right_ambiguity):
                                record = {"category": category, **ambiguity}
                                key = _record_key(category, ambiguity)
                                for left_code in left_ambiguity["candidate_codes"]:
                                    for right_code in right_ambiguity["candidate_codes"]:
                                        con.execute(
                                            """INSERT OR IGNORE INTO dur_pair_ambiguities(
                                                   left_signature_key,right_signature_key,record_key,record_json
                                               ) VALUES(?,?,?,?)""",
                                            (
                                                signature_key(frozenset({left_code})),
                                                signature_key(frozenset({right_code})),
                                                key,
                                                json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                                            ),
                                        )
                                        ambiguity_rows += 1
                continue

            effect_key = (
                normalize_ingredient_identity(row["rule_value"])
                if category == DUPLICATION_CATEGORY
                else ""
            )
            if explicit_left_code:
                mixture_type = str(row["criterion_mixture_type"] or "").strip()
                if mixture_type not in {"단일", "복합"}:
                    raise ValueError(
                        f"MFDS criterion {criterion_id} has invalid mixture type {mixture_type!r}"
                    )
                raw_mixture_codes = json.loads(
                    str(row["criterion_mixture_ingredient_codes_json"] or "[]")
                )
                if not isinstance(raw_mixture_codes, list):
                    raise ValueError(f"MFDS criterion {criterion_id} has invalid mixture code payload")
                mixture_codes = {
                    code
                    for value in raw_mixture_codes
                    if (code := canonicalize_link_ingredient_code(value))
                }
                if mixture_type == "단일" and mixture_codes:
                    raise ValueError(f"MFDS criterion {criterion_id} marks 단일 with mixture codes")
                if mixture_type == "복합" and not mixture_codes:
                    raise ValueError(f"MFDS criterion {criterion_id} marks 복합 without mixture codes")
                composition = frozenset({explicit_left_code, *mixture_codes})
                con.execute(
                    """INSERT OR IGNORE INTO dur_criterion_signatures(
                           criterion_rule_id,category,effect_key,signature_type,signature_key,
                           qualifier,match_method,evidence_kind
                       ) VALUES(?,?,?,'code',?,NULL,'mfds_ingredient_code','mfds_criterion_composition')""",
                    (
                        criterion_id,
                        category,
                        effect_key,
                        signature_key(composition),
                    ),
                )
                criterion_signatures += 1
                continue
            resolved = resolver.resolve_expression(raw_left, category)
            for signature in resolved.signatures:
                method = _method_for_resolution(resolved.preprocessed, signature)
                con.execute(
                    """INSERT OR IGNORE INTO dur_criterion_signatures(
                           criterion_rule_id,category,effect_key,signature_type,signature_key,
                           qualifier,match_method,evidence_kind
                       ) VALUES(?,?,?,'code',?,?,?,?)""",
                    (
                        criterion_id,
                        category,
                        effect_key,
                        signature_key(signature),
                        resolved.qualifier,
                        method,
                        _evidence_kind(
                            preprocessed=resolved.preprocessed,
                            composition=len(signature) > 1,
                        ),
                    ),
                )
                criterion_signatures += 1
            for ambiguity in resolved.ambiguities:
                record = {"category": category, **ambiguity}
                for code in ambiguity["candidate_codes"]:
                    con.execute(
                        """INSERT OR IGNORE INTO dur_single_ambiguities(
                               criterion_rule_id,category,effect_key,signature_key,record_json,
                               rule_value,dosage_form
                           ) VALUES(?,?,?,?,?,?,?)""",
                        (
                            criterion_id,
                            category,
                            effect_key,
                            signature_key(frozenset({code})),
                            json.dumps(record, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                            row["rule_value"],
                            row["dosage_form"],
                        ),
                    )
                    ambiguity_rows += 1

            if category == DOSE_CATEGORY:
                for signature in resolver.extract_rule_value_korean_signatures(row["rule_value"], category):
                    con.execute(
                        """INSERT OR IGNORE INTO dur_criterion_signatures(
                               criterion_rule_id,category,effect_key,signature_type,signature_key,
                               qualifier,match_method,evidence_kind
                           ) VALUES(?,?,?,'rule_value',?,?,?,'rule_value_identity')""",
                        (
                            criterion_id,
                            category,
                            effect_key,
                            signature_key(signature),
                            resolved.qualifier,
                            "rule_value_identity",
                        ),
                    )
                    criterion_signatures += 1

        return {
            "dur_ingredient_concepts": con.execute(
                "SELECT COUNT(*) FROM dur_ingredient_concepts"
            ).fetchone()[0],
            "dur_concept_substances": con.execute(
                "SELECT COUNT(*) FROM dur_concept_substances"
            ).fetchone()[0],
            "product_signatures": con.execute(
                "SELECT COUNT(*) FROM dur_product_item_signatures"
            ).fetchone()[0],
            "criterion_signatures": con.execute(
                "SELECT COUNT(*) FROM dur_criterion_signatures"
            ).fetchone()[0],
            "criterion_pair_signatures": con.execute(
                "SELECT COUNT(*) FROM dur_criterion_pair_signatures"
            ).fetchone()[0],
            "bridge_ambiguity_rows": con.execute(
                "SELECT COUNT(*) FROM dur_single_ambiguities"
            ).fetchone()[0]
            + con.execute("SELECT COUNT(*) FROM dur_pair_ambiguities").fetchone()[0],
        }
    finally:
        con.row_factory = previous_factory


def ensure_dur_ingredient_bridge(con: sqlite3.Connection) -> None:
    rules = int(con.execute("SELECT COUNT(*) FROM product_rules").fetchone()[0])
    criteria = int(con.execute("SELECT COUNT(*) FROM ingredient_rules").fetchone()[0])
    if not rules or not criteria:
        return
    code_maps = int(con.execute("SELECT COUNT(*) FROM dur_ingredient_code_map").fetchone()[0])
    criterion_signatures = int(
        con.execute("SELECT COUNT(*) FROM dur_criterion_signatures").fetchone()[0]
    )
    pair_signatures = int(
        con.execute("SELECT COUNT(*) FROM dur_criterion_pair_signatures").fetchone()[0]
    )
    if code_maps and (criterion_signatures or pair_signatures):
        return
    materialize_dur_ingredient_bridge(con)


__all__ = [
    "ensure_dur_ingredient_bridge",
    "materialize_dur_ingredient_bridge",
    "signature_key",
]