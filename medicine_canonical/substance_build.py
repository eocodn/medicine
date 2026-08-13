from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .schema import SCHEMA_VERSION
from .substance_external import (
    ExternalEvidence,
    build_external_index,
    load_gsrs_names_snapshot,
    load_openfda_unii_snapshot,
)
from .substance_inspection import substance_stats, verify_substance_database
from .substance_ids import stable_external_substance_id, stable_substance_id
from .substance_matching import (
    MATCH_METHOD_PRIORITY,
    MatchEvidence,
    candidates_for_local_name,
)
from .substance_relation_resolution import select_source_relations
from .substance_reviewed_aliases import (
    ActiveReviewedAliases,
    load_reviewed_alias_corpora,
    reviewed_alias_meta_rows,
    validate_active_reviewed_aliases,
)
from .substance_reviewed_relations import (
    APPROVED_FORM_RELATION_CORPUS_PATH,
    ApprovedFormRelation,
    load_approved_form_relation_corpus,
    reviewed_form_relation_meta_rows,
)
from .substance_schema import SUBSTANCE_SCHEMA, SUBSTANCE_SCHEMA_VERSION
from .substance_sources import sync_substance_identity_sources
from .substance_text import (
    normalize_substance_name,
    split_top_level as _split_top_level,
    text_or_none as _text,
)


APP_TIMEZONE = ZoneInfo("Asia/Seoul")


@dataclass
class SourceIdentity:
    dataset_key: str
    scope: str
    source_row: int
    ingredient_code: str | None
    name_en: str | None
    name_ko: str | None
    normalized_name: str
    occurrence_count: int


def _canonical_source_fingerprint(con: sqlite3.Connection) -> str:
    schema_row = con.execute("SELECT value FROM canonical_meta WHERE key='schema_version'").fetchone()
    if not schema_row or schema_row[0] != SCHEMA_VERSION:
        raise ValueError(f"canonical source DB schema must be v{SCHEMA_VERSION}")
    digest = hashlib.sha256()
    digest.update(f"schema:{schema_row[0]}\n".encode())
    for row in con.execute(
        "SELECT dataset_key,source_family,sha256 FROM source_snapshots ORDER BY dataset_key"
    ):
        digest.update(("\t".join(str(value) for value in row) + "\n").encode("utf-8"))
    return digest.hexdigest()


def _copy_canonical_snapshots(source: sqlite3.Connection, target: sqlite3.Connection) -> int:
    rows = source.execute(
        """SELECT dataset_key,source_family,source_locator,snapshot_path,effective_date,
                  fetched_at,row_count,sha256,metadata_json
           FROM source_snapshots ORDER BY dataset_key"""
    ).fetchall()
    target.executemany(
        """INSERT INTO source_snapshots(
               dataset_key,source_family,source_locator,snapshot_path,effective_date,
               fetched_at,row_count,sha256,metadata_json
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        rows,
    )
    return len(rows)


def _aggregate_identity(
    bucket: dict[tuple, SourceIdentity],
    *,
    dataset_key: object,
    scope: str,
    source_row: object,
    occurrence_count: int,
    ingredient_code: object = None,
    name_en: object = None,
    name_ko: object = None,
) -> None:
    english = _text(name_en)
    korean = _text(name_ko)
    normalized = normalize_substance_name(english or korean)
    if not normalized:
        return
    key = (
        str(dataset_key),
        scope,
        _text(ingredient_code),
        english,
        korean,
        normalized,
    )
    row_number = int(source_row or 0)
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = SourceIdentity(
            dataset_key=str(dataset_key),
            scope=scope,
            source_row=row_number,
            ingredient_code=_text(ingredient_code),
            name_en=english,
            name_ko=korean,
            normalized_name=normalized,
            occurrence_count=int(occurrence_count),
        )
        return
    existing.occurrence_count += int(occurrence_count)
    existing.source_row = min(existing.source_row, row_number)


def _extract_domestic_identities(
    con: sqlite3.Connection,
    external_names: set[str],
) -> tuple[list[SourceIdentity], list[tuple[str, str, int, str, str]]]:
    con.row_factory = sqlite3.Row
    bucket: dict[tuple, SourceIdentity] = {}

    for scope, prefix in (("dur_rule_primary", ""), ("dur_rule_paired", "paired_")):
        rows = con.execute(
            f"""SELECT source_dataset_key,MIN(source_row) AS first_row,COUNT(*) AS n,
                       {prefix}ingredient_code AS ingredient_code,
                       {prefix}ingredient_name_en AS name_en,
                       {prefix}ingredient_name AS name_ko
                FROM product_rules
                WHERE ({prefix}ingredient_name_en IS NOT NULL AND TRIM({prefix}ingredient_name_en)<>'')
                   OR ({prefix}ingredient_name IS NOT NULL AND TRIM({prefix}ingredient_name)<>'')
                GROUP BY source_dataset_key,{prefix}ingredient_code,
                         {prefix}ingredient_name_en,{prefix}ingredient_name"""
        ).fetchall()
        for row in rows:
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope=scope,
                source_row=row["first_row"],
                occurrence_count=row["n"],
                ingredient_code=row["ingredient_code"],
                name_en=row["name_en"],
                name_ko=row["name_ko"],
            )

    xlsx_rows = con.execute(
        """SELECT source_dataset_key,source_row,ingredient_name,ingredient_name_ko,
                  paired_ingredient_name
           FROM ingredient_rules"""
    ).fetchall()
    for row in xlsx_rows:
        primary = _split_top_level(row["ingredient_name"], frozenset({"/", "+"}))
        for component in dict.fromkeys(primary):
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="xlsx_primary",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
                name_ko=row["ingredient_name_ko"] if len(primary) == 1 else None,
            )
        paired = _split_top_level(row["paired_ingredient_name"], frozenset({"/", "+"}))
        for component in dict.fromkeys(paired):
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="xlsx_paired",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
            )

    trusted_atomic_names = {item.normalized_name for item in bucket.values()} | external_names
    unparsed: list[tuple[str, str, int, str, str]] = []
    for row in con.execute(
        """SELECT source_dataset_key,source_row,ingredient_text
           FROM products
           WHERE ingredient_text IS NOT NULL AND TRIM(ingredient_text)<>''"""
    ):
        raw_text = str(row["ingredient_text"]).strip()
        parts = _split_top_level(raw_text, frozenset({"/"}))
        # Slash is overloaded in MFDS permit text: it separates ingredients, but
        # is also used in ratios and biological strain designations. Split only
        # when every resulting atom is independently known.
        if "/" in raw_text and (
            len(parts) < 2
            or any(normalize_substance_name(part) not in trusted_atomic_names for part in parts)
        ):
            unparsed.append(
                (
                    str(row["source_dataset_key"]),
                    "permit_composition",
                    int(row["source_row"]),
                    raw_text,
                    "ambiguous_composition_delimiter",
                )
            )
            continue
        seen: set[str] = set()
        for component in parts or [raw_text]:
            normalized = normalize_substance_name(component)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="permit_component",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
            )

    identities = sorted(
        bucket.values(),
        key=lambda item: (
            item.normalized_name,
            item.dataset_key,
            item.scope,
            item.ingredient_code or "",
            item.name_en or "",
            item.name_ko or "",
        ),
    )
    return identities, unparsed


def _representative_name(observations: list[SourceIdentity]) -> str:
    english = sorted({row.name_en for row in observations if row.name_en}, key=lambda value: (len(value), value.casefold(), value))
    if english:
        return english[0]
    korean = sorted({row.name_ko for row in observations if row.name_ko}, key=lambda value: (len(value), value))
    if not korean:
        raise RuntimeError("substance identity has no representative source name")
    return korean[0]


def _insert_substance_layer(
    con: sqlite3.Connection,
    observations: list[SourceIdentity],
    external: dict[str, dict[str, ExternalEvidence]],
    reviewed_aliases: ActiveReviewedAliases,
    reviewed_form_relations: dict[str, ApprovedFormRelation],
) -> int:
    by_name: dict[str, list[SourceIdentity]] = defaultdict(list)
    for observation in observations:
        by_name[observation.normalized_name].append(observation)
    name_to_substance: dict[str, str] = {}
    name_evidence: dict[str, dict[str, MatchEvidence]] = {}
    group_names: dict[str, list[str]] = defaultdict(list)
    group_unii: dict[str, str] = {}

    # Exact external matches remain highest priority. Structured matching is only
    # admitted for explicit source wrappers/aliases and tightly scoped typography
    # transformations; ambiguous exact names never fall through to those rules.
    for normalized_name in sorted(by_name):
        representative = _representative_name(by_name[normalized_name])
        evidence_rows = candidates_for_local_name(
            representative,
            external,
            normalize_substance_name,
            approved_typos=reviewed_aliases.typos,
            approved_nomenclature_aliases=reviewed_aliases.nomenclature,
        )
        evidence_by_unii = {row.unii: row for row in evidence_rows}
        name_evidence[normalized_name] = evidence_by_unii
        candidate_uniis = sorted(evidence_by_unii)
        if len(candidate_uniis) == 1:
            unii = candidate_uniis[0]
            substance_id = stable_external_substance_id("UNII", unii)
            group_unii[substance_id] = unii
        else:
            substance_id = stable_substance_id(normalized_name)
        name_to_substance[normalized_name] = substance_id
        group_names[substance_id].append(normalized_name)

    representatives = {name: _representative_name(rows) for name, rows in by_name.items()}
    name_relations = select_source_relations(
        representatives,
        name_evidence,
        name_to_substance,
        reviewed_form_relations,
        normalize_substance_name,
    )

    pending_relations: list[tuple[str, str, str, str, str]] = []

    for substance_id in sorted(group_names):
        normalized_names = sorted(group_names[substance_id])
        grouped_rows = [row for name in normalized_names for row in by_name[name]]
        resolved = substance_id in group_unii
        selected_evidence: list[MatchEvidence] = []
        if resolved:
            selected_unii = group_unii[substance_id]
            selected_evidence = [
                name_evidence[name][selected_unii]
                for name in normalized_names
                if selected_unii in name_evidence[name]
            ]
        has_exact = any(row.match_method == "normalized_name_exact" for row in selected_evidence)
        has_source_relation = any(name in name_relations for name in normalized_names)
        identity_status = (
            "resolved_external_exact"
            if resolved and has_exact
            else "resolved_external_structured"
            if resolved
            else "resolved_source_relation"
            if has_source_relation
            else "local_exact_unsolved"
        )
        con.execute(
            "INSERT INTO substances(substance_id,canonical_name,identity_status) VALUES(?,?,?)",
            (substance_id, _representative_name(grouped_rows), identity_status),
        )
        for normalized_name in normalized_names:
            con.execute(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                (normalized_name, substance_id, _representative_name(by_name[normalized_name])),
            )

        if resolved:
            best = sorted(
                selected_evidence,
                key=lambda row: (
                    MATCH_METHOD_PRIORITY[row.match_method],
                    row.dataset_key,
                    row.external_name,
                ),
            )[0]
            con.execute(
                """INSERT INTO substance_identifiers(
                       substance_id,system,value,evidence_source_dataset_key,match_method
                   ) VALUES(?,?,?,?,?)""",
                (substance_id, "UNII", group_unii[substance_id], best.dataset_key, best.match_method),
            )

        for normalized_name in normalized_names:
            candidate_map = name_evidence[normalized_name]
            candidate_uniis = sorted(candidate_map)
            selected_unii = group_unii.get(substance_id)
            for unii in candidate_uniis:
                evidence = candidate_map[unii]
                con.execute(
                    """INSERT INTO substance_match_candidates(
                           substance_id,normalized_name,system,value,external_name,match_method,
                           evidence_source_dataset_key,selected
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        substance_id, normalized_name, "UNII", unii, evidence.external_name,
                        evidence.match_method, evidence.dataset_key, 1 if selected_unii == unii else 0,
                    ),
                )
            if resolved or normalized_name in name_relations:
                continue
            reason = "external_exact_multiple_matches" if candidate_uniis else "external_exact_no_match"
            con.execute(
                "INSERT INTO substance_unsolved(substance_id,reason,detail_json) VALUES(?,?,?)",
                (
                    substance_id,
                    reason,
                    json.dumps(
                        {"normalized_name": normalized_name, "candidate_uniis": candidate_uniis},
                        ensure_ascii=False, separators=(",", ":"), sort_keys=True,
                    ),
                ),
            )

        for normalized_name in normalized_names:
            selected_relation = name_relations.get(normalized_name)
            if selected_relation is None:
                continue
            relation = selected_relation.relation
            source_row = sorted(
                by_name[normalized_name],
                key=lambda row: (row.dataset_key, row.scope, row.source_row),
            )[0]
            pending_relations.append(
                (
                    substance_id,
                    relation.relation_type,
                    name_to_substance[relation.base_normalized_name],
                    source_row.dataset_key,
                    json.dumps(
                        {
                            "match_method": selected_relation.match_method,
                            "normalized_name": normalized_name,
                            "base_normalized_name": relation.base_normalized_name,
                            "qualifier": relation.qualifier,
                            **(
                                {"base_unii": selected_relation.base_unii}
                                if selected_relation.base_unii
                                else {}
                            ),
                            **(
                                {"review_basis": selected_relation.review_basis}
                                if selected_relation.review_basis
                                else {}
                            ),
                            **(
                                {"reviewed_at": selected_relation.reviewed_at}
                                if selected_relation.reviewed_at
                                else {}
                            ),
                        },
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                )
            )

    con.executemany(
        """INSERT INTO substance_relations(
               subject_substance_id,relation_type,object_substance_id,
               evidence_source_dataset_key,evidence_json
           ) VALUES(?,?,?,?,?)""",
        pending_relations,
    )

    con.executemany(
        """INSERT INTO source_identities(
               source_dataset_key,source_scope,source_row,ingredient_code,name_en,name_ko,
               normalized_name,occurrence_count,substance_id
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                row.dataset_key, row.scope, row.source_row, row.ingredient_code, row.name_en, row.name_ko,
                row.normalized_name, row.occurrence_count, name_to_substance[row.normalized_name],
            )
            for row in observations
        ],
    )
    return sum(
        relation.match_method == "reviewed_source_form_relation"
        for relation in name_relations.values()
    )


def assemble_substance_database(
    db_path: str | Path,
    canonical_db_path: str | Path,
    raw_dir: str | Path,
) -> dict:
    db_path = Path(db_path)
    canonical_db_path = Path(canonical_db_path)
    raw_dir = Path(raw_dir)
    if not canonical_db_path.exists():
        raise FileNotFoundError(f"canonical source database not found: {canonical_db_path}")
    external_records, external_meta, external_path = load_openfda_unii_snapshot(raw_dir)
    gsrs_names_data, gsrs_names_meta, gsrs_names_path = load_gsrs_names_snapshot(raw_dir)
    external = build_external_index(
        external_records,
        gsrs_names_data,
        normalize_substance_name,
    )
    reviewed_corpora = load_reviewed_alias_corpora(normalize_substance_name)
    reviewed_form_relation_corpus = load_approved_form_relation_corpus(
        APPROVED_FORM_RELATION_CORPUS_PATH,
        normalize_substance_name,
    )
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(db_path.name + ".tmp")
    temp.unlink(missing_ok=True)
    started = time.monotonic()
    try:
        with closing(sqlite3.connect(canonical_db_path)) as source, closing(sqlite3.connect(temp)) as con:
            fingerprint = _canonical_source_fingerprint(source)
            con.executescript(SUBSTANCE_SCHEMA)
            con.execute("BEGIN")
            copied_snapshots = _copy_canonical_snapshots(source, con)
            con.executemany(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,effective_date,
                       fetched_at,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                [
                    (
                        meta["dataset_key"],
                        meta["source_family"],
                        meta["source_locator"],
                        str(path),
                        meta.get("effective_date"),
                        meta.get("fetched_at"),
                        int(meta["row_count"]),
                        meta["sha256"],
                        json.dumps(meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                    )
                    for meta, path in (
                        (external_meta, external_path),
                        (gsrs_names_meta, gsrs_names_path),
                    )
                ],
            )
            observations, unparsed = _extract_domestic_identities(
                source,
                set(external),
            )
            reviewed_aliases = validate_active_reviewed_aliases(
                reviewed_corpora,
                external,
                normalize_substance_name,
                {row.normalized_name for row in observations},
            )
            con.executemany(
                """INSERT INTO source_unparsed_expressions(
                       source_dataset_key,source_scope,source_row,raw_text,reason
                   ) VALUES(?,?,?,?,?)""",
                unparsed,
            )
            active_reviewed_form_relations = _insert_substance_layer(
                con,
                observations,
                external,
                reviewed_aliases,
                reviewed_form_relation_corpus,
            )
            built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            con.executemany(
                "INSERT INTO substance_meta(key,value) VALUES(?,?)",
                [
                    ("schema_version", SUBSTANCE_SCHEMA_VERSION),
                    ("built_at", built_at),
                    ("canonical_source_schema_version", SCHEMA_VERSION),
                    ("canonical_source_fingerprint", fingerprint),
                    (
                        "external_identity_policy",
                        "openfda_preferred_or_gsrs_of_cn_sys_exact_plus_reviewed_aliases_and_explicit_source_structure",
                    ),
                    *reviewed_alias_meta_rows(reviewed_corpora, reviewed_aliases),
                    *reviewed_form_relation_meta_rows(
                        reviewed_form_relation_corpus,
                        active_reviewed_form_relations,
                    ),
                    (
                        "relation_policy",
                        "explicit_source_form_relations_only_no_chemical_suffix_inference",
                    ),
                ],
            )
            con.commit()
            con.execute("ANALYZE")
            con.execute("PRAGMA optimize")
            con.commit()
        verification = verify_substance_database(temp)
        if verification["status"] != "verified":
            raise RuntimeError("canonical substance verification failed: " + "; ".join(verification["errors"]))
        os.replace(temp, db_path)
    except Exception:
        temp.unlink(missing_ok=True)
        raise
    stats = substance_stats(db_path)
    stats.update(
        {
            "canonical_source_snapshots": copied_snapshots,
            "external_preferred_source_rows": len(external_records),
            "external_name_source_rows": int(gsrs_names_meta["row_count"]),
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_dir": str(raw_dir),
        }
    )
    return stats


def rebuild_substance_database(
    db_path: str | Path,
    canonical_db_path: str | Path,
    raw_dir: str | Path,
) -> dict:
    sync_substance_identity_sources(raw_dir)
    return assemble_substance_database(db_path, canonical_db_path, raw_dir)


__all__ = [
    "assemble_substance_database",
    "normalize_substance_name",
    "rebuild_substance_database",
]