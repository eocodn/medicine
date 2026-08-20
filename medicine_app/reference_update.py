from __future__ import annotations

import re
import sqlite3
from contextlib import closing
from pathlib import Path


REFERENCE_CONTRACT_MAJOR = 1
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _normalized_contract_major(value: object) -> int:
    text = str(value).strip()
    if not text.isdigit() or text.startswith("0"):
        raise ValueError("invalid expected reference contract major")
    major = int(text)
    if major <= 0:
        raise ValueError("invalid expected reference contract major")
    return major


def verify_reference_database(
    database: str | Path,
    expected_contract_major: int | str,
    expected_dataset_id: str,
) -> dict:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"reference database not found: {path}")
    contract_major = _normalized_contract_major(expected_contract_major)
    expected_dataset_id = str(expected_dataset_id).lower()
    if not _DATASET_ID.fullmatch(expected_dataset_id):
        raise ValueError("invalid expected reference dataset identity")

    uri = f"file:{path.resolve()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True, timeout=10)) as con:
        con.execute("PRAGMA query_only = ON")
        integrity = con.execute("PRAGMA integrity_check").fetchone()
        if not integrity or integrity[0] != "ok":
            raise ValueError("reference SQLite integrity check failed")
        foreign_keys = con.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_keys:
            raise ValueError(f"reference SQLite foreign key check failed: {len(foreign_keys)} violations")

        try:
            meta = {
                str(key): str(value)
                for key, value in con.execute("SELECT key,value FROM reference_contract_meta")
            }
        except sqlite3.DatabaseError as exc:
            raise ValueError("reference contract metadata is missing or invalid") from exc
        actual_major = _normalized_contract_major(meta.get("contract_major", ""))
        if actual_major != contract_major:
            raise ValueError("reference contract major does not match release")
        dataset_id = str(meta.get("dataset_id") or "").lower()
        if not _DATASET_ID.fullmatch(dataset_id):
            raise ValueError("reference contract dataset identity is invalid")
        if dataset_id != expected_dataset_id:
            raise ValueError("reference dataset identity does not match release")

    return {
        "status": "verified",
        "dataset_id": dataset_id,
        "contract_major": actual_major,
        "size_bytes": path.stat().st_size,
    }


__all__ = ["REFERENCE_CONTRACT_MAJOR", "verify_reference_database"]