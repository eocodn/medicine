from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from pathlib import Path


RUNTIME_TABLES = (
    "canonical_meta", "source_snapshots", "products", "product_identifiers",
    "product_rules", "product_flags", "ingredient_rules", "product_criterion_links",
    "product_ingredient_criterion_links", "product_ingredient_criterion_unresolved",
)
RUNTIME_VIEWS = ("product_rule_criteria", "product_ingredient_criteria")


def _dataset_id(con: sqlite3.Connection) -> str:
    digest = hashlib.sha256()
    rows = con.execute(
        "SELECT dataset_key,sha256,row_count FROM source_snapshots ORDER BY dataset_key"
    ).fetchall()
    if not rows:
        raise ValueError("canonical source snapshots are empty")
    for dataset_key, sha256, row_count in rows:
        digest.update(f"{dataset_key}\0{str(sha256).lower()}\0{row_count}\n".encode("utf-8"))
    return f"sha256:{digest.hexdigest()}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def build_mobile_database(
    canonical_db: str | Path,
    output_db: str | Path,
    *,
    manifest_path: str | Path | None = None,
) -> dict:
    source = Path(canonical_db)
    output = Path(output_db)
    manifest = Path(manifest_path) if manifest_path else output.with_name("mobile.manifest.json")
    if not source.is_file():
        raise FileNotFoundError(f"canonical database not found: {source}")
    output.parent.mkdir(parents=True, exist_ok=True)
    manifest.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(output.name + ".tmp")
    temporary.unlink(missing_ok=True)

    src = sqlite3.connect(f"file:{source.resolve()}?mode=ro", uri=True)
    try:
        schema_version = src.execute(
            "SELECT value FROM canonical_meta WHERE key='schema_version'"
        ).fetchone()
        build_stage = src.execute(
            "SELECT value FROM canonical_meta WHERE key='build_stage'"
        ).fetchone()
        if not schema_version or schema_version[0] != "7" or not build_stage or build_stage[0] != "complete":
            raise ValueError("canonical runtime requires complete schema v7 database")
        dataset_id = _dataset_id(src)
        objects = {
            (kind, name): sql
            for kind, name, sql in src.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE sql IS NOT NULL"
            )
        }

        dst = sqlite3.connect(temporary)
        try:
            dst.execute("PRAGMA foreign_keys=OFF")
            for table in RUNTIME_TABLES:
                ddl = objects.get(("table", table))
                if not ddl:
                    raise ValueError(f"canonical runtime table missing: {table}")
                dst.execute(ddl)
            escaped = str(source.resolve()).replace("'", "''")
            dst.execute(f"ATTACH DATABASE '{escaped}' AS source_db")
            for table in RUNTIME_TABLES:
                dst.execute(f'INSERT INTO "{table}" SELECT * FROM source_db."{table}"')
            dst.commit()
            dst.execute("DETACH DATABASE source_db")
            for kind, name, sql in src.execute(
                "SELECT type,name,sql FROM sqlite_master WHERE type='index' AND sql IS NOT NULL"
            ):
                table = src.execute(
                    "SELECT tbl_name FROM sqlite_master WHERE type='index' AND name=?", (name,)
                ).fetchone()[0]
                if table in RUNTIME_TABLES:
                    dst.execute(sql)
            for view in RUNTIME_VIEWS:
                ddl = objects.get(("view", view))
                if not ddl:
                    raise ValueError(f"canonical runtime view missing: {view}")
                dst.execute(ddl)
            dst.commit()
            dst.execute("PRAGMA foreign_keys=ON")
            if dst.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise RuntimeError("mobile canonical integrity check failed")
            fk_errors = dst.execute("PRAGMA foreign_key_check").fetchall()
            if fk_errors:
                raise RuntimeError(f"mobile canonical foreign key violations: {len(fk_errors)}")
            dst.execute("ANALYZE")
            dst.execute("PRAGMA optimize")
            dst.commit()
        finally:
            dst.close()
        os.replace(temporary, output)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise
    finally:
        src.close()

    payload = {
        "dataset_id": dataset_id,
        "schema_version": "7",
        "sha256": _sha256(output),
        "size_bytes": output.stat().st_size,
    }
    manifest.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"db_path": str(output), "manifest_path": str(manifest), **payload}


__all__ = ["build_mobile_database"]
