from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from .schema import SCHEMA_VERSION
from .substance_schema import SUBSTANCE_SCHEMA_VERSION


def substance_stats(db_path: str | Path) -> dict:
    path = Path(db_path)
    if not path.exists():
        raise FileNotFoundError(f"canonical substance database not found: {path}")
    with closing(sqlite3.connect(path)) as con:
        substances = con.execute("SELECT COUNT(*) FROM substances").fetchone()[0]
        local_exact_names = con.execute("SELECT COUNT(*) FROM substance_names").fetchone()[0]
        resolved = con.execute(
            "SELECT COUNT(*) FROM substances WHERE identity_status='resolved_external_exact'"
        ).fetchone()[0]
        unsolved = con.execute(
            "SELECT COUNT(*) FROM substances WHERE identity_status='local_exact_unsolved'"
        ).fetchone()[0]
        source_identities = con.execute("SELECT COUNT(*) FROM source_identities").fetchone()[0]
        unparsed_source_expressions = con.execute(
            "SELECT COUNT(*) FROM source_unparsed_expressions"
        ).fetchone()[0]
        relations = con.execute("SELECT COUNT(*) FROM substance_relations").fetchone()[0]
        candidates = con.execute("SELECT COUNT(*) FROM substance_match_candidates").fetchone()[0]
        unsolved_reasons = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT reason,COUNT(*) FROM substance_unsolved GROUP BY reason ORDER BY reason"
            )
        }
        source_scopes = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT source_scope,COUNT(*) FROM source_identities GROUP BY source_scope ORDER BY source_scope"
            )
        }
        source_families = {
            row[0]: row[1]
            for row in con.execute(
                "SELECT source_family,COUNT(*) FROM source_snapshots GROUP BY source_family ORDER BY source_family"
            )
        }
        meta = dict(con.execute("SELECT key,value FROM substance_meta"))
    return {
        "db_path": str(path),
        "schema_version": meta.get("schema_version"),
        "built_at": meta.get("built_at"),
        "canonical_source_schema_version": meta.get("canonical_source_schema_version"),
        "canonical_source_fingerprint": meta.get("canonical_source_fingerprint"),
        "substances": substances,
        "local_exact_names": local_exact_names,
        "resolved_external_exact": resolved,
        "unsolved_substances": unsolved,
        "unsolved_reasons": unsolved_reasons,
        "source_identities": source_identities,
        "unparsed_source_expressions": unparsed_source_expressions,
        "source_scopes": source_scopes,
        "match_candidates": candidates,
        "substance_relations": relations,
        "source_families": source_families,
        "size_bytes": path.stat().st_size,
    }


def substance_unsolved_rows(
    db_path: str | Path,
    *,
    reason: str | None = None,
    limit: int = 100,
) -> dict:
    path = Path(db_path)
    if limit < 1:
        raise ValueError("limit must be positive")
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        params: list[object] = []
        where = ""
        if reason:
            where = "WHERE u.reason=?"
            params.append(reason)
        params.append(limit)
        rows = [
            dict(row)
            for row in con.execute(
                f"""SELECT s.substance_id,s.canonical_name,n.normalized_name,u.reason,u.detail_json
                     FROM substance_unsolved u
                     JOIN substances s ON s.substance_id=u.substance_id
                     JOIN substance_names n ON n.substance_id=s.substance_id
                     {where}
                     ORDER BY u.reason,n.normalized_name
                     LIMIT ?""",
                params,
            )
        ]
    return {
        "db_path": str(path),
        "reason": reason,
        "limit": limit,
        "count": len(rows),
        "unsolved": rows,
    }


def substance_unparsed_rows(db_path: str | Path, *, limit: int = 100) -> dict:
    path = Path(db_path)
    if limit < 1:
        raise ValueError("limit must be positive")
    with closing(sqlite3.connect(path)) as con:
        con.row_factory = sqlite3.Row
        rows = [
            dict(row)
            for row in con.execute(
                """SELECT source_dataset_key,source_scope,source_row,raw_text,reason
                   FROM source_unparsed_expressions
                   ORDER BY source_dataset_key,source_scope,source_row
                   LIMIT ?""",
                (limit,),
            )
        ]
    return {
        "db_path": str(path),
        "limit": limit,
        "count": len(rows),
        "unparsed": rows,
    }


def verify_substance_database(db_path: str | Path) -> dict:
    path = Path(db_path)
    errors: list[str] = []
    warnings: list[str] = []
    if not path.exists():
        return {
            "db_path": str(path),
            "status": "invalid",
            "errors": ["database not found"],
            "warnings": [],
        }
    try:
        with closing(sqlite3.connect(path)) as con:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                errors.append("integrity_check failed")
            foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
            if foreign_keys:
                errors.append(f"foreign key violations: {len(foreign_keys)}")
            meta = dict(con.execute("SELECT key,value FROM substance_meta"))
            if meta.get("schema_version") != SUBSTANCE_SCHEMA_VERSION:
                errors.append("schema version mismatch")
            if meta.get("canonical_source_schema_version") != SCHEMA_VERSION:
                errors.append("canonical source schema version mismatch")
            source_families = {
                row[0] for row in con.execute("SELECT DISTINCT source_family FROM source_snapshots")
            }
            if "openfda_unii" not in source_families:
                errors.append("missing openFDA UNII source snapshot")
            stats = substance_stats(path)
            if not stats["substances"]:
                errors.append("no substances materialized")
            nameless_substances = con.execute(
                """SELECT COUNT(*) FROM substances s
                   LEFT JOIN substance_names n ON n.substance_id=s.substance_id
                   WHERE n.substance_id IS NULL"""
            ).fetchone()[0]
            if nameless_substances:
                errors.append(f"substances missing local names: {nameless_substances}")
            missing_resolved_ids = con.execute(
                """SELECT COUNT(*) FROM substances s
                   LEFT JOIN substance_identifiers i
                     ON i.substance_id=s.substance_id AND i.system='UNII'
                   WHERE s.identity_status='resolved_external_exact' AND i.substance_id IS NULL"""
            ).fetchone()[0]
            if missing_resolved_ids:
                errors.append(f"resolved substances missing UNII: {missing_resolved_ids}")
            invalid_unsolved = con.execute(
                """SELECT COUNT(*) FROM substances s
                   LEFT JOIN substance_unsolved u ON u.substance_id=s.substance_id
                   WHERE s.identity_status='local_exact_unsolved' AND u.substance_id IS NULL"""
            ).fetchone()[0]
            if invalid_unsolved:
                errors.append(f"unsolved substances missing reason: {invalid_unsolved}")
            selected_without_identifier = con.execute(
                """SELECT COUNT(*) FROM substance_match_candidates c
                   LEFT JOIN substance_identifiers i
                     ON i.substance_id=c.substance_id AND i.system=c.system AND i.value=c.value
                   WHERE c.selected=1 AND i.substance_id IS NULL"""
            ).fetchone()[0]
            if selected_without_identifier:
                errors.append(
                    f"selected external candidates without identifier: {selected_without_identifier}"
                )
            mismatched_source_names = con.execute(
                """SELECT COUNT(*) FROM source_identities si
                   JOIN substance_names n ON n.normalized_name=si.normalized_name
                   WHERE n.substance_id != si.substance_id"""
            ).fetchone()[0]
            if mismatched_source_names:
                errors.append(
                    f"source identities mapped to wrong substance name group: {mismatched_source_names}"
                )
            bad_selected_names = con.execute(
                """SELECT COUNT(*)
                   FROM substance_match_candidates c
                   JOIN substance_identifiers i
                     ON i.substance_id=c.substance_id AND i.system=c.system
                   WHERE c.selected=1 AND c.value != i.value"""
            ).fetchone()[0]
            if bad_selected_names:
                errors.append(
                    f"selected exact-name candidates disagree with substance identifier: {bad_selected_names}"
                )
            if stats["unsolved_substances"]:
                warnings.append(
                    f"substance identities intentionally unsolved: {stats['unsolved_substances']}"
                )
            if stats["unparsed_source_expressions"]:
                warnings.append(
                    "source ingredient expressions intentionally left unparsed: "
                    f"{stats['unparsed_source_expressions']}"
                )
    except sqlite3.DatabaseError as exc:
        errors.append(f"database error: {exc}")
    return {
        "db_path": str(path),
        "status": "verified" if not errors else "invalid",
        "errors": errors,
        "warnings": warnings,
    }


__all__ = [
    "substance_stats",
    "substance_unparsed_rows",
    "substance_unsolved_rows",
    "verify_substance_database",
]