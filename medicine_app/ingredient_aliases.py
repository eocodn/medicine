from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from medicine_dur.verification import dataset_manifest

from .coverage import normalize_ingredient_name
from .ingredient_alias_graph import derive_validated_ingredient_aliases


def _table_exists(con: sqlite3.Connection, table: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)
    ).fetchone() is not None


def _summary_report(report: dict[str, Any]) -> dict[str, Any]:
    return {
        **{key: value for key, value in report.items() if key != "aliases"},
        "aliases": {
            alias_name: {
                "target": record["target"],
                "evidence_kind": record["evidence_kind"],
                "evidence_count": record["evidence_count"],
            }
            for alias_name, record in report["aliases"].items()
        },
    }


def inspect_validated_ingredient_aliases(
    dur_db: str | Path,
    catalog_db: str | Path,
) -> dict[str, Any]:
    dur_path = Path(dur_db).resolve()
    catalog_path = Path(catalog_db).resolve()
    dur = sqlite3.connect(f"file:{dur_path}?mode=ro", uri=True, timeout=30)
    catalog = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True, timeout=30)
    try:
        report = derive_validated_ingredient_aliases(dur, catalog)
        return {
            **_summary_report(report),
            "dur_dataset_id": dataset_manifest(dur).get("dataset_id"),
            "catalog_db": str(catalog_path),
        }
    finally:
        catalog.close()
        dur.close()


def materialize_validated_ingredient_aliases(
    dur_db: str | Path,
    catalog_db: str | Path,
) -> dict[str, Any]:
    dur_path = Path(dur_db).resolve()
    catalog_path = Path(catalog_db).resolve()
    if not dur_path.is_file():
        raise FileNotFoundError(f"DUR database not found: {dur_path}")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"catalog database not found: {catalog_path}")

    dur = sqlite3.connect(f"file:{dur_path}?mode=ro", uri=True, timeout=30)
    catalog = sqlite3.connect(catalog_path, timeout=30)
    try:
        report = derive_validated_ingredient_aliases(dur, catalog)
        dur_dataset_id = dataset_manifest(dur).get("dataset_id")
        built_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        catalog.execute("BEGIN IMMEDIATE")
        catalog.executescript(
            """
            CREATE TABLE IF NOT EXISTS ingredient_aliases (
                alias_name TEXT PRIMARY KEY,
                target_name TEXT NOT NULL,
                evidence_kind TEXT NOT NULL,
                evidence_count INTEGER NOT NULL,
                dur_dataset_id TEXT,
                built_at TEXT NOT NULL,
                provenance_json TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_ingredient_alias_target
                ON ingredient_aliases(target_name);
            DELETE FROM ingredient_aliases;
            """
        )
        catalog.executemany(
            """INSERT INTO ingredient_aliases(
                alias_name,target_name,evidence_kind,evidence_count,
                dur_dataset_id,built_at,provenance_json
            ) VALUES(?,?,?,?,?,?,?)""",
            [
                (
                    alias_name,
                    record["target"],
                    record["evidence_kind"],
                    record["evidence_count"],
                    dur_dataset_id,
                    built_at,
                    json.dumps(
                        record["evidence"],
                        ensure_ascii=False,
                        separators=(",", ":"),
                    ),
                )
                for alias_name, record in report["aliases"].items()
            ],
        )
        if _table_exists(catalog, "catalog_meta"):
            catalog.executemany(
                "INSERT OR REPLACE INTO catalog_meta(key,value) VALUES(?,?)",
                [
                    ("ingredient_alias_dur_dataset_id", dur_dataset_id or ""),
                    ("ingredient_alias_built_at", built_at),
                    ("ingredient_alias_count", str(report["validated_aliases"])),
                ],
            )
        catalog.commit()
        return {
            **_summary_report(report),
            "dur_dataset_id": dur_dataset_id,
            "built_at": built_at,
            "catalog_db": str(catalog_path),
        }
    except Exception:
        catalog.rollback()
        raise
    finally:
        catalog.close()
        dur.close()


def load_materialized_ingredient_aliases(
    catalog_con: sqlite3.Connection,
    *,
    dur_dataset_id: str | None,
) -> dict[str, str]:
    if not _table_exists(catalog_con, "ingredient_aliases"):
        return {}
    catalog_con.row_factory = sqlite3.Row
    if dur_dataset_id is None:
        rows = catalog_con.execute(
            """SELECT alias_name,target_name FROM ingredient_aliases
               WHERE dur_dataset_id IS NULL
               ORDER BY alias_name"""
        ).fetchall()
    else:
        rows = catalog_con.execute(
            """SELECT alias_name,target_name FROM ingredient_aliases
               WHERE dur_dataset_id=?
               ORDER BY alias_name""",
            (dur_dataset_id,),
        ).fetchall()
    return {
        normalize_ingredient_name(row["alias_name"]): normalize_ingredient_name(row["target_name"])
        for row in rows
        if normalize_ingredient_name(row["alias_name"])
        and normalize_ingredient_name(row["target_name"])
    }


__all__ = [
    "derive_validated_ingredient_aliases",
    "inspect_validated_ingredient_aliases",
    "load_materialized_ingredient_aliases",
    "materialize_validated_ingredient_aliases",
]
