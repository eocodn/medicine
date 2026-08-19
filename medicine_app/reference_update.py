from __future__ import annotations

import hashlib
import re
import sqlite3
from contextlib import closing
from pathlib import Path

from .canonical_runtime import canonical_manifest

# This value participates in the published mobile dataset identity. Keep it in
# lockstep with medicine_canonical.mobile.MOBILE_DATA_POLICY_VERSION; tests
# intentionally fail if publisher and on-device runtime policy diverge.
MOBILE_DATA_POLICY_VERSION = "7"
_DATASET_ID = re.compile(r"^sha256:[0-9a-f]{64}$")


def _mobile_dataset_id(con: sqlite3.Connection) -> str:
    rows = con.execute(
        "SELECT dataset_key,sha256,row_count FROM source_snapshots ORDER BY dataset_key"
    ).fetchall()
    if not rows:
        raise ValueError("reference runtime policy has no source snapshots")
    digest = hashlib.sha256()
    digest.update(f"mobile-data-policy\0{MOBILE_DATA_POLICY_VERSION}\n".encode("utf-8"))
    for dataset_key, sha256, row_count in rows:
        digest.update(f"{dataset_key}\0{str(sha256).lower()}\0{row_count}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def verify_reference_database(
    database: str | Path,
    expected_schema_version: str,
    expected_dataset_id: str,
) -> dict:
    path = Path(database)
    if not path.is_file():
        raise FileNotFoundError(f"reference database not found: {path}")
    if not expected_schema_version or not str(expected_schema_version).isdigit():
        raise ValueError("invalid expected reference schema version")
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

        runtime = canonical_manifest(con)
        if runtime.get("status") != "verified":
            raise ValueError("reference runtime policy verification failed")
        schema_version = str(runtime.get("schema_version") or "")
        if schema_version != str(expected_schema_version):
            raise ValueError("reference runtime schema version does not match release")
        dataset_id = _mobile_dataset_id(con)
        if dataset_id != expected_dataset_id:
            raise ValueError("reference dataset identity does not match release")

    return {
        "status": "verified",
        "dataset_id": dataset_id,
        "schema_version": schema_version,
        "size_bytes": path.stat().st_size,
    }


__all__ = ["MOBILE_DATA_POLICY_VERSION", "verify_reference_database"]
