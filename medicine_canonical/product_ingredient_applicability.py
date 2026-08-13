from __future__ import annotations

import json
import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from .substance_text import normalize_substance_name, split_top_level


INGREDIENT_APPLICABILITY_CATEGORIES = frozenset({"lactation_caution"})
_DIRECTIONAL_APPLICABILITY_RELATIONS = frozenset(
    {
        "active_moiety_of",
        "salt_of",
        "ester_of",
        "hydrate_of",
        "physical_form_of",
        "formulation_of",
    }
)
_EQUIVALENT_RELATION = "equivalent_to"
_SCOPE_KEY_RE = re.compile(r"[^a-z0-9]+")


@dataclass(frozen=True)
class Criterion:
    criterion_rule_id: int
    category: str
    normalized_name: str
    substance_id: str | None
    composition_substance_ids: frozenset[str]
    scope_key: str


@dataclass(frozen=True)
class ProductComponent:
    normalized_name: str
    substance_id: str | None
    scope_key: str


@dataclass(frozen=True)
class RelationEvidence:
    relation_type: str
    object_substance_id: str
    evidence_source_dataset_key: str
    evidence_json: str


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _scope_key(value: object) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).casefold().replace("β", "beta")
    return _SCOPE_KEY_RE.sub("", text)


def _load_substance_maps(
    substance_db_path: str | Path | None,
) -> tuple[dict[str, str], dict[str, list[RelationEvidence]]]:
    if substance_db_path is None:
        return {}, {}
    path = Path(substance_db_path)
    if not path.exists():
        raise FileNotFoundError(f"canonical substance database not found: {path}")
    with closing(sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)) as con:
        names = {
            str(row[0]): str(row[1])
            for row in con.execute("SELECT normalized_name,substance_id FROM substance_names")
        }
        relations: dict[str, list[RelationEvidence]] = defaultdict(list)
        for row in con.execute(
            """SELECT subject_substance_id,relation_type,object_substance_id,
                      evidence_source_dataset_key,evidence_json
               FROM substance_relations"""
        ):
            subject, relation_type, obj, source_key, evidence_json = map(str, row)
            if relation_type in _DIRECTIONAL_APPLICABILITY_RELATIONS:
                relations[subject].append(
                    RelationEvidence(relation_type, obj, source_key, evidence_json)
                )
            elif relation_type == _EQUIVALENT_RELATION:
                relations[subject].append(
                    RelationEvidence(relation_type, obj, source_key, evidence_json)
                )
                relations[obj].append(
                    RelationEvidence(relation_type, subject, source_key, evidence_json)
                )
        return names, dict(relations)


def _criteria(
    con: sqlite3.Connection,
    substance_names: dict[str, str],
) -> list[Criterion]:
    result: list[Criterion] = []
    placeholders = ",".join("?" for _ in INGREDIENT_APPLICABILITY_CATEGORIES)
    for row in con.execute(
        f"""SELECT id,category,ingredient_name
            FROM ingredient_rules
            WHERE category IN ({placeholders}) AND ingredient_name IS NOT NULL
            ORDER BY id""",
        tuple(sorted(INGREDIENT_APPLICABILITY_CATEGORIES)),
    ):
        raw_name = str(row[2])
        normalized = normalize_substance_name(raw_name)
        if not normalized:
            continue
        parts = split_top_level(raw_name, frozenset({"/", "+"})) or [raw_name]
        component_names = tuple(
            value for part in parts if (value := normalize_substance_name(part))
        )
        component_ids = tuple(substance_names.get(name) for name in component_names)
        is_composition = len(component_names) > 1
        composition_ids = (
            frozenset(str(value) for value in component_ids if value)
            if is_composition and all(component_ids)
            else frozenset()
        )
        result.append(
            Criterion(
                criterion_rule_id=int(row[0]),
                category=str(row[1]),
                normalized_name=normalized,
                substance_id=(substance_names.get(component_names[0]) if len(component_names) == 1 else None),
                composition_substance_ids=composition_ids,
                scope_key=_scope_key(normalized),
            )
        )
    return result


def _product_components(
    raw_value: object,
    substance_names: dict[str, str],
) -> tuple[list[ProductComponent], bool]:
    raw = str(raw_value or "").strip()
    if not raw:
        return [], False
    parts = split_top_level(raw, frozenset({"/"})) or [raw]
    components = [
        ProductComponent(
            normalized_name=normalize_substance_name(part),
            substance_id=substance_names.get(normalize_substance_name(part)),
            scope_key=_scope_key(normalize_substance_name(part)),
        )
        for part in parts
        if normalize_substance_name(part)
    ]
    # Match the substance builder's fail-closed composition policy. A slash is
    # only an atomic ingredient delimiter when every component has an identity.
    complete = bool(components) and (
        "/" not in raw or all(component.substance_id for component in components)
    )
    return components, complete


def _official_names_by_item(con: sqlite3.Connection) -> dict[str, dict[str, list[dict]]]:
    result: dict[str, dict[str, list[dict]]] = defaultdict(lambda: defaultdict(list))
    for row in con.execute(
        """SELECT id,source_dataset_key,source_row,category,item_seq,ingredient_name_en,
                  paired_item_seq,paired_ingredient_name_en
           FROM product_rules"""
    ):
        rule_id = int(row[0])
        source_dataset_key = str(row[1])
        source_row = int(row[2])
        category = str(row[3])
        for side, item_seq, ingredient_name in (
            ("primary", row[4], row[5]),
            ("paired", row[6], row[7]),
        ):
            item = str(item_seq or "").strip()
            normalized = normalize_substance_name(ingredient_name)
            if not item or not normalized:
                continue
            result[item][normalized].append(
                {
                    "product_rule_id": rule_id,
                    "source_dataset_key": source_dataset_key,
                    "source_row": source_row,
                    "source_category": category,
                    "side": side,
                    "ingredient_name": str(ingredient_name),
                }
            )
    return {item: dict(names) for item, names in result.items()}


def _signature_criteria(con: sqlite3.Connection) -> dict[tuple[str, str], list[int]]:
    result: dict[tuple[str, str], list[int]] = defaultdict(list)
    placeholders = ",".join("?" for _ in INGREDIENT_APPLICABILITY_CATEGORIES)
    for row in con.execute(
        f"""SELECT criterion_rule_id,category,signature_key
            FROM dur_criterion_signatures
            WHERE signature_type='code' AND category IN ({placeholders})""",
        tuple(sorted(INGREDIENT_APPLICABILITY_CATEGORIES)),
    ):
        result[(str(row[1]), str(row[2]))].append(int(row[0]))
    return dict(result)


def _item_signatures(con: sqlite3.Connection) -> dict[str, list[str]]:
    result: dict[str, list[str]] = defaultdict(list)
    for row in con.execute(
        """SELECT item_seq,signature_key
           FROM dur_product_item_signatures WHERE signature_type='code'"""
    ):
        result[str(row[0])].append(str(row[1]))
    return dict(result)


def _insert_link(
    con: sqlite3.Connection,
    *,
    item_seq: str,
    criterion: Criterion,
    match_method: str,
    evidence_kind: str,
    evidence: object,
) -> None:
    con.execute(
        """INSERT OR IGNORE INTO product_ingredient_criterion_links(
               item_seq,criterion_rule_id,category,match_method,evidence_kind,evidence_json
           ) VALUES(?,?,?,?,?,?)""",
        (
            item_seq,
            criterion.criterion_rule_id,
            criterion.category,
            match_method,
            evidence_kind,
            _json(evidence),
        ),
    )


def _candidate_reason(
    criterion: Criterion,
    component: ProductComponent,
    *,
    composition_complete: bool,
) -> str:
    if not composition_complete:
        return "product_composition_unresolved"
    if component.substance_id is None:
        return "product_component_identity_unresolved"
    if criterion.substance_id is None and not criterion.composition_substance_ids:
        return "criterion_identity_unresolved"
    return "scope_relation_unproven"


def materialize_product_ingredient_criterion_links(
    con: sqlite3.Connection,
    substance_db_path: str | Path | None = None,
) -> dict:
    """Materialize product applicability for ingredient-only DUR criteria.

    Positive links require regulatory code scope, exact precise identity, an
    authoritative typed substance relation, or same-ITEM_SEQ official MFDS DUR
    naming. A normalized-name prefix is deliberately *not* a match rule: it is
    used only to keep plausible salt/form candidates observable as unresolved.
    """

    previous_factory = con.row_factory
    con.row_factory = sqlite3.Row
    try:
        con.execute("DELETE FROM product_ingredient_criterion_links")
        con.execute("DELETE FROM product_ingredient_criterion_unresolved")
        substance_names, relation_map = _load_substance_maps(substance_db_path)
        criteria = _criteria(con, substance_names)
        criterion_by_id = {criterion.criterion_rule_id: criterion for criterion in criteria}
        criteria_by_substance: dict[str, list[Criterion]] = defaultdict(list)
        criteria_by_name: dict[str, list[Criterion]] = defaultdict(list)
        scope_candidates: dict[str, list[Criterion]] = defaultdict(list)
        for criterion in criteria:
            criteria_by_name[criterion.normalized_name].append(criterion)
            if criterion.substance_id:
                criteria_by_substance[criterion.substance_id].append(criterion)
            if len(criterion.scope_key) >= 5:
                scope_candidates[criterion.scope_key[:4]].append(criterion)

        official_names = _official_names_by_item(con)
        signature_criteria = _signature_criteria(con)
        item_signatures = _item_signatures(con)
        method_counts: dict[str, int] = defaultdict(int)
        unresolved_reason_counts: dict[str, int] = defaultdict(int)

        for product in con.execute(
            "SELECT item_seq,ingredient_text FROM products ORDER BY item_seq"
        ):
            item_seq = str(product[0])
            components, composition_complete = _product_components(product[1], substance_names)
            selected: dict[int, tuple[str, str, object]] = {}

            # Existing category-scoped DUR bridge evidence remains the strongest
            # regulatory applicability statement and does not need substance DBs.
            for signature in item_signatures.get(item_seq, []):
                for category in INGREDIENT_APPLICABILITY_CATEGORIES:
                    for criterion_id in signature_criteria.get((category, signature), []):
                        selected.setdefault(
                            criterion_id,
                            (
                                "dur_scope_signature",
                                "dur_scope_signature",
                                {"signature_key": signature},
                            ),
                        )

            if composition_complete:
                product_substance_ids = frozenset(
                    component.substance_id
                    for component in components
                    if component.substance_id
                )
                for criterion in criteria:
                    if (
                        criterion.composition_substance_ids
                        and product_substance_ids == criterion.composition_substance_ids
                    ):
                        selected.setdefault(
                            criterion.criterion_rule_id,
                            (
                                "precise_substance_exact",
                                "precise_substance_identity",
                                {
                                    "product_components": [
                                        component.normalized_name for component in components
                                    ],
                                    "substance_ids": sorted(product_substance_ids),
                                    "criterion_composition": criterion.normalized_name,
                                },
                            ),
                        )

                for component in components:
                    if not component.substance_id:
                        continue
                    for criterion in criteria_by_substance.get(component.substance_id, []):
                        selected.setdefault(
                            criterion.criterion_rule_id,
                            (
                                "precise_substance_exact",
                                "precise_substance_identity",
                                {
                                    "product_component": component.normalized_name,
                                    "substance_id": component.substance_id,
                                },
                            ),
                        )
                    for relation in relation_map.get(component.substance_id, []):
                        for criterion in criteria_by_substance.get(
                            relation.object_substance_id, []
                        ):
                            selected.setdefault(
                                criterion.criterion_rule_id,
                                (
                                    "reviewed_substance_relation",
                                    "reviewed_substance_relation",
                                    {
                                        "product_component": component.normalized_name,
                                        "subject_substance_id": component.substance_id,
                                        "relation_type": relation.relation_type,
                                        "object_substance_id": relation.object_substance_id,
                                        "evidence_source_dataset_key": relation.evidence_source_dataset_key,
                                        "relation_evidence_json": relation.evidence_json,
                                    },
                                ),
                            )

            for normalized_name, evidence_rows in official_names.get(item_seq, {}).items():
                for criterion in criteria_by_name.get(normalized_name, []):
                    selected.setdefault(
                        criterion.criterion_rule_id,
                        (
                            "same_item_official_dur_name",
                            "same_item_official_dur_scope",
                            {
                                "ingredient_name": normalized_name,
                                "product_rule_evidence": evidence_rows,
                            },
                        ),
                    )

            for criterion_id, (method, kind, evidence) in selected.items():
                criterion = criterion_by_id[criterion_id]
                _insert_link(
                    con,
                    item_seq=item_seq,
                    criterion=criterion,
                    match_method=method,
                    evidence_kind=kind,
                    evidence=evidence,
                )
                method_counts[method] += 1

            unresolved: dict[int, tuple[str, dict]] = {}
            for component in components:
                if not component.scope_key:
                    continue
                for criterion in scope_candidates.get(component.scope_key[:4], []):
                    if criterion.criterion_rule_id in selected:
                        continue
                    candidate_kind: str | None = None
                    if (
                        component.scope_key == criterion.scope_key
                        and component.normalized_name == criterion.normalized_name
                    ):
                        # In an incomplete composition, even an exact-looking atom
                        # cannot prove applicability because the source delimiter
                        # was not safely parsed as composition.
                        if composition_complete:
                            continue
                        candidate_kind = "exact_component_in_unresolved_composition"
                    elif component.scope_key == criterion.scope_key:
                        # Typography/punctuation normalization is useful to avoid a
                        # false clear, but never strong enough to create identity.
                        candidate_kind = "normalized_scope_key_equivalent"
                    elif (
                        component.scope_key.startswith(criterion.scope_key)
                        and component.scope_key != criterion.scope_key
                    ):
                        candidate_kind = "normalized_name_prefix"
                    else:
                        continue
                    reason = _candidate_reason(
                        criterion, component, composition_complete=composition_complete
                    )
                    unresolved.setdefault(
                        criterion.criterion_rule_id,
                        (
                            reason,
                            {
                                "product_component": component.normalized_name,
                                "product_substance_id": component.substance_id,
                                "criterion_name": criterion.normalized_name,
                                "criterion_substance_id": criterion.substance_id,
                                "candidate_kind": candidate_kind,
                            },
                        ),
                    )
            for criterion_id, (reason, evidence) in unresolved.items():
                criterion = criterion_by_id[criterion_id]
                con.execute(
                    """INSERT INTO product_ingredient_criterion_unresolved(
                           item_seq,criterion_rule_id,category,reason,evidence_json
                       ) VALUES(?,?,?,?,?)""",
                    (item_seq, criterion_id, criterion.category, reason, _json(evidence)),
                )
                unresolved_reason_counts[reason] += 1

        return {
            "product_ingredient_criterion_links": con.execute(
                "SELECT COUNT(*) FROM product_ingredient_criterion_links"
            ).fetchone()[0],
            "linked_product_ingredient_items": con.execute(
                "SELECT COUNT(DISTINCT item_seq) FROM product_ingredient_criterion_links"
            ).fetchone()[0],
            "ingredient_criterion_link_methods": dict(sorted(method_counts.items())),
            "unresolved_product_ingredient_criteria": con.execute(
                "SELECT COUNT(*) FROM product_ingredient_criterion_unresolved"
            ).fetchone()[0],
            "unresolved_product_ingredient_reasons": dict(
                sorted(unresolved_reason_counts.items())
            ),
        }
    finally:
        con.row_factory = previous_factory


__all__ = [
    "INGREDIENT_APPLICABILITY_CATEGORIES",
    "materialize_product_ingredient_criterion_links",
]
