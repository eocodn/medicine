from __future__ import annotations

import re
import sqlite3
from collections import Counter, defaultdict, deque
from typing import Any

from .coverage import (
    ingredient_index,
    normalize_ingredient_name,
    split_edi_codes,
    split_ingredient_components,
)
from .ingredient_alias_curated import (
    MANUALLY_REVIEWED_INGREDIENT_ALIASES,
    MANUAL_REVIEW_MULTI_IDENTITY,
    is_reviewed_exact_edi_identity_conflict,
)


_ALIAS_EVIDENCE_KIND = "authoritative_identity_graph"
_AS_ACTIVE_RE = re.compile(r"^(?P<form>.+?)\s*\(\s*as\s+(?P<active>[^()]+)\)\s*$", re.IGNORECASE)
_TRAILING_STRENGTH_RE = re.compile(
    r"\s+(?:\d+(?:\.\d+)?|\.\d+)\s*(?:mcg|μg|ug|mg|g|kg|ml|l|%)\s*$",
    re.IGNORECASE,
)


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _unique_components(value: Any) -> list[str]:
    return sorted(set(split_ingredient_components(value)))


def _edge_key(left: str, right: str) -> tuple[str, str]:
    return (left, right) if left < right else (right, left)


def _add_edge(
    adjacency: dict[str, set[str]],
    evidence_by_edge: dict[tuple[str, str], list[dict[str, Any]]],
    left: str,
    right: str,
    evidence: dict[str, Any],
) -> None:
    left = normalize_ingredient_name(left)
    right = normalize_ingredient_name(right)
    if not left or not right or left == right:
        return
    adjacency[left].add(right)
    adjacency[right].add(left)
    evidence_by_edge[_edge_key(left, right)].append(evidence)


def _explicit_active_moiety(value: Any) -> tuple[str, str] | None:
    normalized = normalize_ingredient_name(value)
    if not normalized or len(_unique_components(normalized)) != 1:
        return None
    match = _AS_ACTIVE_RE.match(normalized)
    if not match:
        return None
    form = normalize_ingredient_name(match.group("form"))
    active = normalize_ingredient_name(match.group("active"))
    active = _TRAILING_STRENGTH_RE.sub("", active).strip()
    if not form or not active:
        return None
    return form, active


def _proof_path(
    alias_name: str,
    target_name: str,
    adjacency: dict[str, set[str]],
    evidence_by_edge: dict[tuple[str, str], list[dict[str, Any]]],
) -> tuple[int, list[dict[str, Any]]]:
    queue: deque[str] = deque([target_name])
    parent: dict[str, str | None] = {target_name: None}
    while queue and alias_name not in parent:
        node = queue.popleft()
        for neighbor in sorted(adjacency[node]):
            if neighbor in parent:
                continue
            parent[neighbor] = node
            queue.append(neighbor)
    if alias_name not in parent:
        raise RuntimeError("alias evidence graph lost target path")

    proof: list[dict[str, Any]] = []
    support_count = 0
    node = alias_name
    while node != target_name:
        next_node = parent[node]
        if next_node is None:
            raise RuntimeError("alias evidence path terminated before target")
        records = evidence_by_edge[_edge_key(node, next_node)]
        support_count += len(records)
        representative = dict(records[0])
        representative["from"] = node
        representative["to"] = next_node
        representative["support_count"] = len(records)
        proof.append(representative)
        node = next_node
    return support_count, proof


def _unique_graph_targets(
    adjacency: dict[str, set[str]], known_ingredients: set[str]
) -> dict[str, str]:
    resolved: dict[str, str] = {}
    visited: set[str] = set()
    for seed in sorted(adjacency):
        if seed in visited:
            continue
        stack = [seed]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(adjacency[node] - visited)
        targets = component & known_ingredients
        if len(targets) == 1:
            target = next(iter(targets))
            for node in component:
                resolved[node] = target
    return resolved


def derive_validated_ingredient_aliases(
    dur_con: sqlite3.Connection,
    catalog_con: sqlite3.Connection,
) -> dict[str, Any]:
    """Derive aliases only from explicit identities published by current sources.

    Three authoritative relations are admitted into one graph:
    1. an active MFDS catalog product and its exact EDI-linked DUR product row,
    2. single-ingredient DUR names carrying the same product + ingredient code,
    3. a single-ingredient DUR product that explicitly declares ``(as <active moiety>)``,
    4. a multi-ingredient exact EDI pair where all but one component cancel to
       already validated identities.

    A connected component is accepted only when it contains exactly one name
    already present in the ingredient-level DUR dataset. Multiple known DUR
    identities make the whole unresolved portion ambiguous. No generic salt,
    ester, hydrate, spelling, or active-moiety stripping is performed.
    """
    dur_con.row_factory = sqlite3.Row
    catalog_con.row_factory = sqlite3.Row
    known_ingredients = ingredient_index(dur_con)
    adjacency: dict[str, set[str]] = defaultdict(set)
    evidence_by_edge: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)

    catalog_rows = catalog_con.execute(
        """SELECT item_seq,product_name,ingredient_name,edi_code
           FROM products
           WHERE permit_status='active'
             AND edi_code IS NOT NULL
             AND TRIM(edi_code)<>''
           ORDER BY item_seq"""
    ).fetchall()
    edi_component_shapes: dict[str, set[tuple[str, ...]]] = defaultdict(set)
    for row in catalog_rows:
        shape = tuple(_unique_components(row["ingredient_name"]))
        for edi_code in split_edi_codes(row["edi_code"]):
            edi_component_shapes[edi_code].add(shape)
    safe_single_product_codes = {
        edi_code
        for edi_code, shapes in edi_component_shapes.items()
        if shapes and all(len(shape) == 1 for shape in shapes)
    }

    exact_edi_evidence_products = 0
    exact_edi_identity_conflicts = 0
    edi_component_pairs: list[tuple[list[str], list[str], dict[str, Any]]] = []
    observed_exact_edi_components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in catalog_rows:
        catalog_parts = _unique_components(row["ingredient_name"])
        edi_codes = split_edi_codes(row["edi_code"])
        if not edi_codes or not catalog_parts:
            continue
        placeholders = ",".join("?" for _ in edi_codes)
        product_rows = dur_con.execute(
            f"""SELECT product_code,product_name,ingredient_name
                FROM product_catalog
                WHERE product_code IN ({placeholders})
                ORDER BY product_code""",
            edi_codes,
        ).fetchall()
        if len(product_rows) != 1:
            continue
        dur_parts = _unique_components(product_rows[0]["ingredient_name"])
        if not dur_parts:
            continue
        evidence = {
            "catalog_item_seq": str(row["item_seq"]),
            "catalog_product_name": str(row["product_name"] or ""),
            "dur_product_code": str(product_rows[0]["product_code"] or ""),
            "dur_product_name": str(product_rows[0]["product_name"] or ""),
        }
        edi_component_pairs.append((catalog_parts, dur_parts, evidence))
        for component in sorted(set(catalog_parts + dur_parts)):
            observed_exact_edi_components[component].append(evidence)
        if len(catalog_parts) != 1 or len(dur_parts) != 1:
            continue
        if is_reviewed_exact_edi_identity_conflict(
            str(product_rows[0]["product_code"] or ""), catalog_parts, dur_parts
        ):
            # This exact source conflict was reviewed against MFDS product data.
            # The guard is tuple-specific so intentional DUR naming differences
            # remain usable, while a future corrected source automatically stops
            # matching this rejection.
            exact_edi_identity_conflicts += 1
            continue
        exact_edi_evidence_products += 1
        _add_edge(
            adjacency,
            evidence_by_edge,
            catalog_parts[0],
            dur_parts[0],
            {"evidence_kind": "active_exact_edi_product", **evidence},
        )

    explicit_active_relations = 0
    observed_product_catalog_components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if _table_exists(dur_con, "product_catalog"):
        for product_row in dur_con.execute(
            """SELECT product_code,ingredient_name
               FROM product_catalog
               WHERE ingredient_name IS NOT NULL AND TRIM(ingredient_name)<>''"""
        ).fetchall():
            product_components = split_ingredient_components(product_row["ingredient_name"])
            product_catalog_evidence = {
                "evidence_kind": "dur_product_catalog_observation",
                "product_code": str(product_row["product_code"] or ""),
                "source_name": str(product_row["ingredient_name"] or ""),
            }
            for component in product_components:
                observed_product_catalog_components[component].append(product_catalog_evidence)
            if str(product_row["product_code"] or "") not in safe_single_product_codes:
                continue
            for component in product_components:
                explicit = _explicit_active_moiety(component)
                if explicit is None:
                    continue
                full_name = normalize_ingredient_name(component)
                form_name, active_name = explicit
                evidence = {
                    "evidence_kind": "dur_product_catalog_explicit_active_moiety",
                    "product_code": str(product_row["product_code"] or ""),
                    "source_name": str(component),
                }
                _add_edge(adjacency, evidence_by_edge, full_name, form_name, evidence)
                _add_edge(adjacency, evidence_by_edge, full_name, active_name, evidence)
                explicit_active_relations += 1

    code_variants: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = defaultdict(
        lambda: defaultdict(list)
    )
    eligible_identity_keys: set[tuple[str, str]] = set()
    if _table_exists(dur_con, "product_catalog"):
        for product_row in dur_con.execute(
            """SELECT product_code,ingredient_code,ingredient_name
               FROM product_catalog
               WHERE product_code IS NOT NULL AND ingredient_code IS NOT NULL"""
        ).fetchall():
            parts = _unique_components(product_row["ingredient_name"])
            if len(parts) != 1:
                continue
            product_code = str(product_row["product_code"])
            if product_code not in safe_single_product_codes:
                continue
            key = (product_code, str(product_row["ingredient_code"]))
            eligible_identity_keys.add(key)
            code_variants[key][parts[0]].append({
                "evidence_kind": "dur_product_catalog_identity",
                "product_code": key[0],
                "ingredient_code": key[1],
                "source_name": str(product_row["ingredient_name"] or ""),
            })
    product_dur_rows = 0
    observed_product_dur_components: dict[str, list[dict[str, Any]]] = defaultdict(list)
    if _table_exists(dur_con, "product_dur"):
        rows = dur_con.execute(
            """
            SELECT MIN(dataset_key) AS dataset_key, MIN(source_row) AS source_row,
                   ingredient_name AS source_name, ingredient_code, product_code,
                   'primary' AS side
            FROM product_dur
            WHERE ingredient_name IS NOT NULL AND TRIM(ingredient_name)<>''
            GROUP BY ingredient_name,ingredient_code,product_code
            UNION ALL
            SELECT MIN(dataset_key) AS dataset_key, MIN(source_row) AS source_row,
                   paired_ingredient_name AS source_name, paired_ingredient_code AS ingredient_code,
                   paired_product_code AS product_code, 'paired' AS side
            FROM product_dur
            WHERE paired_ingredient_name IS NOT NULL AND TRIM(paired_ingredient_name)<>''
            GROUP BY paired_ingredient_name,paired_ingredient_code,paired_product_code
            """
        ).fetchall()
        product_dur_rows = len(rows)
        for row in rows:
            name = row["source_name"]
            code = row["ingredient_code"]
            product_code = row["product_code"]
            side = row["side"]
            parts = _unique_components(name)
            source_record = {
                "evidence_kind": "dur_ingredient_code",
                "dataset_key": str(row["dataset_key"] or ""),
                "source_row": row["source_row"],
                "ingredient_code": str(code or ""),
                "product_code": str(product_code or ""),
                "source_name": str(name or ""),
                "side": side,
            }
            for component in parts:
                observed_product_dur_components[component].append(source_record)
            if code and product_code and len(parts) == 1:
                key = (str(product_code), str(code))
                if key in eligible_identity_keys:
                    code_variants[key][parts[0]].append(source_record)

            if str(product_code or "") not in safe_single_product_codes:
                continue
            explicit = _explicit_active_moiety(name)
            if explicit is None:
                continue
            full_name = normalize_ingredient_name(name)
            form_name, active_name = explicit
            explicit_record = {
                "evidence_kind": "dur_explicit_active_moiety",
                "dataset_key": str(row["dataset_key"] or ""),
                "source_row": row["source_row"],
                "ingredient_code": str(code or ""),
                "product_code": str(product_code or ""),
                "source_name": str(name or ""),
                "side": side,
            }
            _add_edge(adjacency, evidence_by_edge, full_name, form_name, explicit_record)
            _add_edge(adjacency, evidence_by_edge, full_name, active_name, explicit_record)
            explicit_active_relations += 1

    product_ingredient_keys_with_variants = 0
    for (product_code, ingredient_code), variants in sorted(code_variants.items()):
        names = sorted(variants)
        if len(names) < 2:
            continue
        product_ingredient_keys_with_variants += 1
        anchor = names[0]
        for variant in names[1:]:
            evidence = {
                "evidence_kind": "same_dur_product_and_ingredient_code",
                "product_code": product_code,
                "ingredient_code": ingredient_code,
                "left_sources": variants[anchor][:2],
                "right_sources": variants[variant][:2],
            }
            # Keep one proof record per published name-pair while retaining the
            # number of supporting source rows in the record itself.
            evidence["source_support_count"] = len(variants[anchor]) + len(variants[variant])
            _add_edge(adjacency, evidence_by_edge, anchor, variant, evidence)

    manually_reviewed_relations = 0
    for alias_name, record in sorted(MANUALLY_REVIEWED_INGREDIENT_ALIASES.items()):
        alias_name = normalize_ingredient_name(alias_name)
        target_name = normalize_ingredient_name(record["target"])
        exact_observations = observed_exact_edi_components.get(alias_name, [])
        product_dur_observations = observed_product_dur_components.get(alias_name, [])
        product_catalog_observations = observed_product_catalog_components.get(alias_name, [])
        observations = exact_observations or product_dur_observations or product_catalog_observations
        if not observations or target_name not in known_ingredients:
            continue
        if exact_observations:
            observation_kind = "active_exact_edi_product"
        elif product_dur_observations:
            observation_kind = "current_product_dur"
        else:
            observation_kind = "current_dur_product_catalog"
        _add_edge(
            adjacency,
            evidence_by_edge,
            alias_name,
            target_name,
            {
                "evidence_kind": "manual_reviewed_identity",
                "review_basis": record["basis"],
                "observation_kind": observation_kind,
                "observation_count": len(observations),
                **observations[0],
            },
        )
        manually_reviewed_relations += 1

    reviewed_multi_aliases: dict[str, dict[str, Any]] = {}
    for alias_name, target_names in sorted(MANUAL_REVIEW_MULTI_IDENTITY.items()):
        alias_name = normalize_ingredient_name(alias_name)
        targets = sorted({normalize_ingredient_name(target) for target in target_names})
        exact_observations = observed_exact_edi_components.get(alias_name, [])
        product_dur_observations = observed_product_dur_components.get(alias_name, [])
        product_catalog_observations = observed_product_catalog_components.get(alias_name, [])
        observations = exact_observations or product_dur_observations or product_catalog_observations
        if not observations or not targets or not all(target in known_ingredients for target in targets):
            continue
        if exact_observations:
            observation_kind = "active_exact_edi_product"
        elif product_dur_observations:
            observation_kind = "current_product_dur"
        else:
            observation_kind = "current_dur_product_catalog"
        reviewed_multi_aliases[alias_name] = {
            "targets": targets,
            "evidence_kind": "manual_reviewed_multi_identity",
            "evidence_count": len(observations),
            "evidence": [
                {
                    "evidence_kind": "manual_reviewed_multi_identity",
                    "observation_kind": observation_kind,
                    "observation_count": len(observations),
                    **observations[0],
                }
            ],
        }

    component_elimination_relations = 0
    for _ in range(12):
        resolved_graph = _unique_graph_targets(adjacency, known_ingredients)
        added = 0
        for catalog_parts, dur_parts, evidence in edi_component_pairs:
            if len(catalog_parts) != len(dur_parts) or len(catalog_parts) < 2:
                continue

            def target_for(part: str) -> str | None:
                if part in known_ingredients:
                    return part
                return resolved_graph.get(part)

            catalog_targets = [(part, target_for(part)) for part in catalog_parts]
            dur_targets = [(part, target_for(part)) for part in dur_parts]
            catalog_counts = Counter(target for _, target in catalog_targets if target)
            dur_counts = Counter(target for _, target in dur_targets if target)
            catalog_residual = catalog_counts - dur_counts
            dur_residual = dur_counts - catalog_counts
            catalog_unresolved = [part for part, target in catalog_targets if target is None]
            dur_unresolved = [part for part, target in dur_targets if target is None]

            candidate: tuple[str, str] | None = None
            if (
                len(dur_unresolved) == 1
                and not catalog_unresolved
                and sum(catalog_residual.values()) == 1
                and not dur_residual
            ):
                candidate = (dur_unresolved[0], next(catalog_residual.elements()))
            elif (
                len(catalog_unresolved) == 1
                and not dur_unresolved
                and sum(dur_residual.values()) == 1
                and not catalog_residual
            ):
                candidate = (catalog_unresolved[0], next(dur_residual.elements()))

            if candidate is None:
                continue
            alias_name, target_name = candidate
            if alias_name == target_name or target_name in adjacency[alias_name]:
                continue
            _add_edge(
                adjacency,
                evidence_by_edge,
                alias_name,
                target_name,
                {
                    "evidence_kind": "exact_edi_component_elimination",
                    **evidence,
                    "catalog_components": catalog_parts,
                    "dur_components": dur_parts,
                },
            )
            added += 1
        component_elimination_relations += added
        if added == 0:
            break

    aliases: dict[str, dict[str, Any]] = {}
    ambiguous: dict[str, dict[str, Any]] = {}
    visited: set[str] = set()
    for seed in sorted(adjacency):
        if seed in visited:
            continue
        stack = [seed]
        component: set[str] = set()
        while stack:
            node = stack.pop()
            if node in visited:
                continue
            visited.add(node)
            component.add(node)
            stack.extend(adjacency[node] - visited)
        targets = sorted(component & known_ingredients)
        if len(targets) == 1:
            target = targets[0]
            for alias_name in sorted(component - {target}):
                evidence_count, proof = _proof_path(
                    alias_name, target, adjacency, evidence_by_edge
                )
                aliases[alias_name] = {
                    "target": target,
                    "evidence_kind": _ALIAS_EVIDENCE_KIND,
                    "evidence_count": evidence_count,
                    "evidence": proof,
                }
        elif len(targets) > 1:
            for alias_name in sorted(component - known_ingredients):
                ambiguous[alias_name] = {
                    "targets": targets,
                    "evidence_counts": {
                        target: sum(
                            len(evidence_by_edge[_edge_key(target, neighbor)])
                            for neighbor in adjacency[target]
                            if neighbor in component
                        )
                        for target in targets
                    },
                }

    return {
        "aliases": aliases,
        "multi_aliases": reviewed_multi_aliases,
        "ambiguous": ambiguous,
        "validated_aliases": len(aliases),
        "validated_multi_aliases": len(reviewed_multi_aliases),
        "ambiguous_aliases": len(ambiguous),
        "eligible_active_edi_products": len(catalog_rows),
        "mfds_confirmed_single_ingredient_product_codes": len(safe_single_product_codes),
        "exact_edi_evidence_products": exact_edi_evidence_products,
        "exact_edi_identity_conflicts": exact_edi_identity_conflicts,
        "product_dur_identity_rows_scanned": product_dur_rows,
        "product_ingredient_keys_with_name_variants": product_ingredient_keys_with_variants,
        "explicit_active_moiety_relations": explicit_active_relations,
        "manually_reviewed_relations": manually_reviewed_relations,
        "component_elimination_relations": component_elimination_relations,
    }
