from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterable

from medicine_dur.verification import REQUIRED_HEADERS, REQUIRED_SOURCE_KEYS


def install_verified_dur_fixture_metadata(
    con: sqlite3.Connection,
    *,
    ingredients: Iterable[str],
) -> None:
    """Give small unit-test DUR databases production-like manifest/mapping coverage.

    The focused tests still own the actual product rules they exercise. These
    identity-only ingredient rows establish the separate ingredient bridge so
    the eight-category evaluator can distinguish a verified clear lookup from
    an intentionally incomplete mapping fixture.
    """
    con.executescript(
        """
        CREATE TABLE IF NOT EXISTS source_files (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL UNIQUE,
            source_kind TEXT NOT NULL,
            category TEXT NOT NULL,
            source_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS ingredient_dur (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            ingredient_name TEXT,
            ingredient_name_ko TEXT,
            paired_ingredient_name TEXT,
            rule_value TEXT,
            dosage_form TEXT,
            note TEXT,
            details TEXT,
            sequence_text TEXT
        );
        """
    )
    for index, key in enumerate(REQUIRED_SOURCE_KEYS, 1):
        kind, category = key.split(":", 1)
        con.execute(
            """INSERT OR REPLACE INTO source_files(
                dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                key,
                kind,
                category,
                f"fixture/{key}",
                f"{index:064x}",
                1,
                1,
                json.dumps(
                    {"title": "fixture 2026.08.01", "header": sorted(REQUIRED_HEADERS[key])},
                    ensure_ascii=False,
                ),
            ),
        )
    for index, ingredient in enumerate(dict.fromkeys(ingredients), 1):
        con.execute(
            """INSERT INTO ingredient_dur(
                dataset_key,source_row,category,ingredient_name,sequence_text
            ) VALUES(?,?,?,?,?)""",
            ("fixture:ingredient_identity", index, "fixture_identity", ingredient, str(index)),
        )
