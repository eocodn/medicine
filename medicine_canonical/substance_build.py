from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import time
from collections import defaultdict
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .job_lifecycle import JobLifecycle, sqlite_heartbeat
from .schema import SCHEMA_VERSION
from .snapshot_io import sha256_file
from .substance_external import (
    ExternalEvidence,
    build_external_index,
    load_gsrs_names_snapshot,
    load_openfda_unii_snapshot,
)
from .substance_inspection import substance_stats, verify_substance_database
from .substance_job import substance_build_input_fingerprint
from .substance_ids import stable_external_substance_id, stable_substance_id
from .substance_matching import (
    MATCH_METHOD_PRIORITY,
    MatchEvidence,
    candidates_for_local_name,
)
from .substance_observations import (
    SourceIdentity,
    extract_domestic_identities,
    representative_name,
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
from .substance_text import normalize_substance_name


APP_TIMEZONE = ZoneInfo("Asia/Seoul")


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
        representative = representative_name(by_name[normalized_name])
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

    representatives = {name: representative_name(rows) for name, rows in by_name.items()}
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
            (substance_id, representative_name(grouped_rows), identity_status),
        )
        for normalized_name in normalized_names:
            con.execute(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                (normalized_name, substance_id, representative_name(by_name[normalized_name])),
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
    *,
    progress=None,
    checkpoint_path: str | Path | None = None,
) -> dict:
    db_path = Path(db_path)
    canonical_db_path = Path(canonical_db_path)
    raw_dir = Path(raw_dir)
    if not canonical_db_path.exists():
        raise FileNotFoundError(f"canonical source database not found: {canonical_db_path}")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    temp = db_path.with_name(db_path.name + ".tmp")
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else db_path.with_name(db_path.name + ".build.checkpoint.json")
    )
    input_fingerprint = substance_build_input_fingerprint(canonical_db_path, raw_dir)
    try:
        lifecycle = JobLifecycle(
            "substance-build",
            checkpoint,
            input_fingerprint=input_fingerprint,
            progress=progress,
            total_steps=3,
        )
    except RuntimeError:
        temp.unlink(missing_ok=True)
        raise
    started = time.monotonic()
    current_phase = "startup"
    lifecycle.started()
    try:
        phase = lifecycle.completed_phase
        if phase not in {None, "materialized", "verified"}:
            lifecycle.discard(f"unknown completed phase {phase!r}")
        if phase is not None and lifecycle.artifacts.get("staged_db") != str(temp):
            lifecycle.discard("staged substance database path changed")
        if phase is not None:
            staged_sha256 = lifecycle.artifacts.get("staged_sha256")
            if not isinstance(staged_sha256, str) or not staged_sha256:
                lifecycle.discard("substance checkpoint is missing staged sha256")
            candidate = temp if temp.exists() else db_path if phase == "verified" and db_path.exists() else None
            if candidate is None:
                lifecycle.discard("checkpointed substance database is missing")
            if sha256_file(candidate) != staged_sha256:
                lifecycle.discard("checkpointed substance database bytes changed")
            build_result = lifecycle.artifacts.get("build_result")
            if not isinstance(build_result, dict):
                lifecycle.discard("substance checkpoint build_result is missing or invalid")

        if phase is None:
            temp.unlink(missing_ok=True)
            current_phase = "materialized"
            lifecycle.step_started(current_phase, 1)
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
            with closing(sqlite3.connect(canonical_db_path)) as source, closing(
                sqlite3.connect(temp)
            ) as con, sqlite_heartbeat(source, lifecycle, current_phase), sqlite_heartbeat(
                con, lifecycle, current_phase
            ):
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
                observations, unparsed = extract_domestic_identities(source, set(external))
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
            build_result = {
                "canonical_source_snapshots": copied_snapshots,
                "external_preferred_source_rows": len(external_records),
                "external_name_source_rows": int(gsrs_names_meta["row_count"]),
            }
            staged_sha256 = sha256_file(temp)
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_db": str(temp),
                    "staged_sha256": staged_sha256,
                    "build_result": build_result,
                },
            )
            lifecycle.step_completed(current_phase, 1)
            phase = lifecycle.completed_phase

        if phase == "materialized":
            current_phase = "verified"
            lifecycle.step_started(current_phase, 2)
            lifecycle.heartbeat(current_phase, force=True)
            verification = verify_substance_database(temp)
            if verification["status"] != "verified":
                raise RuntimeError(
                    "canonical substance verification failed: "
                    + "; ".join(verification["errors"])
                )
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_db": str(temp),
                    "staged_sha256": staged_sha256,
                    "build_result": build_result,
                },
            )
            lifecycle.step_completed(current_phase, 2)
            phase = lifecycle.completed_phase

        if phase != "verified":
            lifecycle.discard(f"cannot commit from completed phase {phase!r}")
        current_phase = "commit"
        lifecycle.step_started(current_phase, 3)
        if temp.exists():
            if sha256_file(temp) != staged_sha256:
                lifecycle.discard("verified staged substance database bytes changed")
            os.replace(temp, db_path)
        elif db_path.exists():
            if sha256_file(db_path) != staged_sha256:
                lifecycle.discard("committed substance database does not match verified sha256")
        else:
            lifecycle.discard("verified staged and committed substance databases are both missing")
        lifecycle.step_completed(current_phase, 3)
        lifecycle.completed()
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        if lifecycle.completed_phase is None:
            temp.unlink(missing_ok=True)
        raise
    stats = substance_stats(db_path)
    stats.update(
        {
            **build_result,
            "elapsed_seconds": round(time.monotonic() - started, 3),
            "raw_dir": str(raw_dir),
        }
    )
    return stats


def rebuild_substance_database(
    db_path: str | Path,
    canonical_db_path: str | Path,
    raw_dir: str | Path,
    *,
    progress=None,
) -> dict:
    sync_substance_identity_sources(raw_dir, job_progress=progress)
    return assemble_substance_database(
        db_path,
        canonical_db_path,
        raw_dir,
        progress=progress,
    )


__all__ = [
    "assemble_substance_database",
    "normalize_substance_name",
    "rebuild_substance_database",
]