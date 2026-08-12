from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
import unicodedata
from collections import defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .schema import SCHEMA_VERSION
from .substance_inspection import substance_stats, verify_substance_database
from .substance_schema import SUBSTANCE_SCHEMA, SUBSTANCE_SCHEMA_VERSION
from .substance_sources import (
    OPENFDA_UNII_DATASET_KEY,
    OPENFDA_UNII_FILENAME,
    inspect_unii_archive,
    sync_openfda_unii,
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


def _text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def normalize_substance_name(value: object) -> str:
    """Normalize only Unicode, case and whitespace for exact identity matching.

    This deliberately does not strip salts, hydrates, esters, strengths,
    punctuation or formulation words. Those require an explicit relationship or
    reviewed identity source and are not part of schema-v1 automatic matching.
    """
    text = unicodedata.normalize("NFKC", str(value or "")).strip().casefold()
    return re.sub(r"\s+", " ", text)


def _split_top_level(value: object, separators: frozenset[str]) -> list[str]:
    text = unicodedata.normalize("NFKC", str(value or "")).strip()
    if not text:
        return []
    parts: list[str] = []
    current: list[str] = []
    stack: list[str] = []
    closing = {")": "(", "]": "[", "}": "{"}
    for char in text:
        if char in "([{":
            stack.append(char)
        elif char in closing and stack and stack[-1] == closing[char]:
            stack.pop()
        if not stack and char in separators:
            piece = "".join(current).strip()
            if piece:
                parts.append(piece)
            current = []
        else:
            current.append(char)
    piece = "".join(current).strip()
    if piece:
        parts.append(piece)
    return parts


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _stable_substance_id(normalized_name: str) -> str:
    # The opaque local key is anchored to the strict local exact identity, not to
    # UNII. External identifiers can therefore be added or removed without making
    # an external coding system our primary key.
    digest = hashlib.sha256(("local-exact\0" + normalized_name).encode("utf-8")).hexdigest()
    return "SUB_" + digest[:20].upper()


def _stable_external_substance_id(system: str, value: str) -> str:
    # Keep our own opaque identifier while making exact external convergence
    # deterministic across rebuilds. The external code remains an attributed
    # identifier, not the literal primary-key value.
    digest = hashlib.sha256((f"external-group\0{system}\0{value}").encode("utf-8")).hexdigest()
    return "SUB_" + digest[:20].upper()


def _load_unii_snapshot(raw_dir: Path) -> tuple[list[dict], dict, Path]:
    path = raw_dir / OPENFDA_UNII_FILENAME
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    if not path.exists() or not meta_path.exists():
        raise FileNotFoundError(f"missing openFDA UNII snapshot or metadata under {raw_dir}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    required = {"dataset_key", "source_family", "source_locator", "row_count", "sha256"}
    missing = required - meta.keys()
    if missing:
        raise ValueError(f"invalid openFDA UNII metadata: missing {sorted(missing)}")
    if meta["dataset_key"] != OPENFDA_UNII_DATASET_KEY or meta["source_family"] != "openfda_unii":
        raise ValueError("openFDA UNII snapshot provenance mismatch")
    actual_sha = _sha256_file(path)
    if actual_sha != meta["sha256"]:
        raise ValueError(f"sha256 mismatch for openFDA UNII snapshot: expected {meta['sha256']}, got {actual_sha}")
    records, archive_meta = inspect_unii_archive(path.read_bytes())
    if len(records) != int(meta["row_count"]):
        raise RuntimeError(f"openFDA UNII row-count mismatch: metadata {meta['row_count']}, archive {len(records)}")
    merged = dict(meta)
    merged["archive_meta"] = archive_meta
    return records, merged, path


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


def _external_index(records: list[dict]) -> dict[str, dict[str, set[str]]]:
    index: dict[str, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
    for row in records:
        name = str(row["substance_name"]).strip()
        unii = str(row["unii"]).strip()
        normalized = normalize_substance_name(name)
        if normalized and unii:
            index[normalized][unii].add(name)
    return index


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
    external_records: list[dict],
) -> None:
    by_name: dict[str, list[SourceIdentity]] = defaultdict(list)
    for observation in observations:
        by_name[observation.normalized_name].append(observation)
    external = _external_index(external_records)
    name_to_substance: dict[str, str] = {}
    name_candidates: dict[str, list[str]] = {}
    group_names: dict[str, list[str]] = defaultdict(list)
    group_unii: dict[str, str] = {}

    # Decide each exact local spelling independently before grouping. A unique
    # exact UNII match may converge multiple spellings into one substance, while
    # ambiguous names remain separate and observable even if one candidate UNII
    # is selected by some other spelling.
    for normalized_name in sorted(by_name):
        candidate_uniis = sorted(external.get(normalized_name, {}))
        name_candidates[normalized_name] = candidate_uniis
        if len(candidate_uniis) == 1:
            unii = candidate_uniis[0]
            substance_id = _stable_external_substance_id("UNII", unii)
            group_unii[substance_id] = unii
        else:
            substance_id = _stable_substance_id(normalized_name)
        name_to_substance[normalized_name] = substance_id
        group_names[substance_id].append(normalized_name)

    for substance_id in sorted(group_names):
        normalized_names = sorted(group_names[substance_id])
        grouped_rows = [row for name in normalized_names for row in by_name[name]]
        resolved = substance_id in group_unii
        con.execute(
            "INSERT INTO substances(substance_id,canonical_name,identity_status) VALUES(?,?,?)",
            (
                substance_id,
                _representative_name(grouped_rows),
                "resolved_external_exact" if resolved else "local_exact_unsolved",
            ),
        )
        for normalized_name in normalized_names:
            con.execute(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                (
                    normalized_name,
                    substance_id,
                    _representative_name(by_name[normalized_name]),
                ),
            )

        if resolved:
            con.execute(
                """INSERT INTO substance_identifiers(
                       substance_id,system,value,evidence_source_dataset_key,match_method
                   ) VALUES(?,?,?,?,?)""",
                (
                    substance_id,
                    "UNII",
                    group_unii[substance_id],
                    OPENFDA_UNII_DATASET_KEY,
                    "normalized_name_exact",
                ),
            )

        for normalized_name in normalized_names:
            candidate_map = external.get(normalized_name, {})
            candidate_uniis = name_candidates[normalized_name]
            selected_unii = group_unii.get(substance_id)
            for unii in candidate_uniis:
                external_name = sorted(
                    candidate_map[unii], key=lambda value: (value.casefold(), value)
                )[0]
                con.execute(
                    """INSERT INTO substance_match_candidates(
                           substance_id,normalized_name,system,value,external_name,match_method,
                           evidence_source_dataset_key,selected
                       ) VALUES(?,?,?,?,?,?,?,?)""",
                    (
                        substance_id,
                        normalized_name,
                        "UNII",
                        unii,
                        external_name,
                        "normalized_name_exact",
                        OPENFDA_UNII_DATASET_KEY,
                        1 if selected_unii == unii else 0,
                    ),
                )
            if resolved:
                continue
            reason = (
                "external_exact_multiple_matches"
                if candidate_uniis
                else "external_exact_no_match"
            )
            con.execute(
                "INSERT INTO substance_unsolved(substance_id,reason,detail_json) VALUES(?,?,?)",
                (
                    substance_id,
                    reason,
                    json.dumps(
                        {"normalized_name": normalized_name, "candidate_uniis": candidate_uniis},
                        ensure_ascii=False,
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )

    con.executemany(
        """INSERT INTO source_identities(
               source_dataset_key,source_scope,source_row,ingredient_code,name_en,name_ko,
               normalized_name,occurrence_count,substance_id
           ) VALUES(?,?,?,?,?,?,?,?,?)""",
        [
            (
                row.dataset_key,
                row.scope,
                row.source_row,
                row.ingredient_code,
                row.name_en,
                row.name_ko,
                row.normalized_name,
                row.occurrence_count,
                name_to_substance[row.normalized_name],
            )
            for row in observations
        ],
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
    external_records, external_meta, external_path = _load_unii_snapshot(raw_dir)
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
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,effective_date,
                       fetched_at,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?,?,?)""",
                (
                    external_meta["dataset_key"],
                    external_meta["source_family"],
                    external_meta["source_locator"],
                    str(external_path),
                    external_meta.get("effective_date"),
                    external_meta.get("fetched_at"),
                    int(external_meta["row_count"]),
                    external_meta["sha256"],
                    json.dumps(external_meta, ensure_ascii=False, separators=(",", ":"), sort_keys=True),
                ),
            )
            observations, unparsed = _extract_domestic_identities(
                source,
                set(_external_index(external_records)),
            )
            con.executemany(
                """INSERT INTO source_unparsed_expressions(
                       source_dataset_key,source_scope,source_row,raw_text,reason
                   ) VALUES(?,?,?,?,?)""",
                unparsed,
            )
            _insert_substance_layer(con, observations, external_records)
            built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            con.executemany(
                "INSERT INTO substance_meta(key,value) VALUES(?,?)",
                [
                    ("schema_version", SUBSTANCE_SCHEMA_VERSION),
                    ("built_at", built_at),
                    ("canonical_source_schema_version", SCHEMA_VERSION),
                    ("canonical_source_fingerprint", fingerprint),
                    ("external_identity_policy", "openfda_unii_normalized_name_exact_only"),
                    ("relation_policy", "no_automatic_relations_in_v1"),
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
            "external_source_rows": len(external_records),
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
    sync_openfda_unii(raw_dir)
    return assemble_substance_database(db_path, canonical_db_path, raw_dir)


__all__ = [
    "assemble_substance_database",
    "normalize_substance_name",
    "rebuild_substance_database",
]