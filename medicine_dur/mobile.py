from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .verification import dataset_manifest, verify_database


MOBILE_SCHEMA_VERSION = 2


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    os.replace(tmp, path)


def build_mobile_database(
    dur_db: str | Path,
    catalog_db: str | Path,
    output_db: str | Path,
    *,
    manifest_path: str | Path | None = None,
    require_verified_source: bool = True,
) -> dict[str, Any]:
    """Build the read-only reference snapshot packaged into the Android app.

    The mobile DB keeps all rows and safety-significant text, but removes raw
    catalog JSON, product-level ingredient columns which are duplicated by
    ``product_catalog``, and indexes not used by the runtime query plan. The
    source manifest is copied unchanged, so ``dataset_id`` remains identical.
    """
    dur_path = Path(dur_db).resolve()
    catalog_path = Path(catalog_db).resolve()
    output_path = Path(output_db).resolve()
    manifest_out = Path(manifest_path).resolve() if manifest_path else output_path.with_suffix(".manifest.json")

    if not dur_path.is_file():
        raise FileNotFoundError(f"DUR database not found: {dur_path}")
    if not catalog_path.is_file():
        raise FileNotFoundError(f"catalog database not found: {catalog_path}")
    if require_verified_source:
        verification = verify_database(dur_path)
        if verification["status"] != "verified":
            raise ValueError(f"DUR source database failed verification: {verification['issues']}")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_out.parent.mkdir(parents=True, exist_ok=True)
    tmp = output_path.with_name(output_path.name + ".tmp")
    for candidate in (tmp, Path(str(tmp) + "-wal"), Path(str(tmp) + "-shm")):
        candidate.unlink(missing_ok=True)

    con = sqlite3.connect(tmp)
    try:
        con.execute("PRAGMA journal_mode=OFF")
        con.execute("PRAGMA synchronous=OFF")
        con.execute("PRAGMA temp_store=MEMORY")
        con.execute("ATTACH DATABASE ? AS dur", (str(dur_path),))
        con.execute("ATTACH DATABASE ? AS cat", (str(catalog_path),))
        con.executescript(
            """
            CREATE TABLE source_files AS
            SELECT dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,imported_at,metadata_json
            FROM dur.source_files;
            CREATE UNIQUE INDEX idx_source_files_key ON source_files(dataset_key);

            CREATE TABLE product_dur AS
            SELECT dataset_key,source_row,category,product_code,paired_product_code,
                   rule_value,details,notice_no,notice_date
            FROM dur.product_dur;
            CREATE INDEX idx_product_runtime
                ON product_dur(product_code,category,paired_product_code);

            CREATE TABLE product_catalog AS
            SELECT product_code,product_name,ingredient_code,ingredient_name
            FROM dur.product_catalog;
            CREATE UNIQUE INDEX idx_product_catalog_code ON product_catalog(product_code);
            CREATE INDEX idx_product_catalog_ingredient
                ON product_catalog(ingredient_name COLLATE NOCASE);

            CREATE TABLE ingredient_dur AS
            SELECT dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
                   paired_ingredient_name,rule_value,dosage_form,note,details,sequence_text
            FROM dur.ingredient_dur;
            CREATE INDEX idx_ingredient_runtime
                ON ingredient_dur(category,ingredient_name,paired_ingredient_name);

            CREATE TABLE products AS
            SELECT item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
                   permit_date,cancel_date,cancel_name,permit_status,source
            FROM cat.products;
            CREATE UNIQUE INDEX idx_products_item_seq ON products(item_seq);
            CREATE INDEX idx_products_name ON products(product_name);
            CREATE INDEX idx_products_ingredient ON products(ingredient_name);
            CREATE INDEX idx_products_manufacturer ON products(manufacturer);
            CREATE INDEX idx_products_status ON products(permit_status);
            """
        )
        product_item_source_exists = con.execute(
            "SELECT 1 FROM dur.sqlite_master WHERE type='table' AND name='product_item_flags'"
        ).fetchone() is not None
        if product_item_source_exists:
            con.executescript(
                """
                CREATE TABLE product_item_flags AS
                SELECT dataset_key,item_seq,product_name,edi_code,category,flag_code,flag_name,
                       dosage_form,ingredient_name,details,change_date
                FROM dur.product_item_flags;
                CREATE UNIQUE INDEX idx_product_item_flag_identity
                    ON product_item_flags(item_seq,category);
                CREATE INDEX idx_product_item_flag_edi ON product_item_flags(edi_code);
                CREATE INDEX idx_product_item_flag_category ON product_item_flags(category);
                """
            )
        alias_source_exists = con.execute(
            "SELECT 1 FROM cat.sqlite_master WHERE type='table' AND name='ingredient_aliases'"
        ).fetchone() is not None
        if alias_source_exists:
            con.executescript(
                """
                CREATE TABLE ingredient_aliases AS
                SELECT alias_name,target_name,evidence_kind,evidence_count,
                       dur_dataset_id,built_at,provenance_json
                FROM cat.ingredient_aliases;
                CREATE UNIQUE INDEX idx_ingredient_alias_name ON ingredient_aliases(alias_name);
                CREATE INDEX idx_ingredient_alias_target ON ingredient_aliases(target_name);
                """
            )
        multi_alias_source_exists = con.execute(
            "SELECT 1 FROM cat.sqlite_master WHERE type='table' AND name='ingredient_multi_aliases'"
        ).fetchone() is not None
        if multi_alias_source_exists:
            con.executescript(
                """
                CREATE TABLE ingredient_multi_aliases AS
                SELECT alias_name,target_name,evidence_kind,evidence_count,
                       dur_dataset_id,built_at,provenance_json
                FROM cat.ingredient_multi_aliases;
                CREATE UNIQUE INDEX idx_ingredient_multi_alias_pair
                    ON ingredient_multi_aliases(alias_name,target_name);
                CREATE INDEX idx_ingredient_multi_alias_target
                    ON ingredient_multi_aliases(target_name);
                """
            )
        con.execute("ANALYZE")
        con.commit()
        con.execute("VACUUM")
    finally:
        con.close()

    source_con = sqlite3.connect(f"file:{dur_path}?mode=ro", uri=True)
    mobile_con = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    catalog_con = sqlite3.connect(f"file:{catalog_path}?mode=ro", uri=True)
    try:
        source_manifest = dataset_manifest(source_con)
        mobile_manifest = dataset_manifest(mobile_con)
        if source_manifest["dataset_id"] != mobile_manifest["dataset_id"]:
            raise RuntimeError("mobile dataset identity differs from source DUR dataset")
        if mobile_con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            raise RuntimeError("mobile SQLite integrity_check failed")
        source_dur_rows = source_con.execute("SELECT COUNT(*) FROM product_dur").fetchone()[0]
        mobile_dur_rows = mobile_con.execute("SELECT COUNT(*) FROM product_dur").fetchone()[0]
        source_ingredient_rows = source_con.execute("SELECT COUNT(*) FROM ingredient_dur").fetchone()[0]
        mobile_ingredient_rows = mobile_con.execute("SELECT COUNT(*) FROM ingredient_dur").fetchone()[0]
        source_product_flag_rows = (
            source_con.execute("SELECT COUNT(*) FROM product_item_flags").fetchone()[0]
            if source_con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_item_flags'"
            ).fetchone()
            else 0
        )
        mobile_product_flag_rows = (
            mobile_con.execute("SELECT COUNT(*) FROM product_item_flags").fetchone()[0]
            if mobile_con.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='product_item_flags'"
            ).fetchone()
            else 0
        )
        source_product_rows = catalog_con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        mobile_product_rows = mobile_con.execute("SELECT COUNT(*) FROM products").fetchone()[0]
        alias_source_exists = catalog_con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingredient_aliases'"
        ).fetchone() is not None
        source_alias_rows = (
            catalog_con.execute("SELECT COUNT(*) FROM ingredient_aliases").fetchone()[0]
            if alias_source_exists else 0
        )
        alias_mobile_exists = mobile_con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingredient_aliases'"
        ).fetchone() is not None
        mobile_alias_rows = (
            mobile_con.execute("SELECT COUNT(*) FROM ingredient_aliases").fetchone()[0]
            if alias_mobile_exists else 0
        )
        multi_alias_source_exists = catalog_con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingredient_multi_aliases'"
        ).fetchone() is not None
        source_multi_alias_rows = (
            catalog_con.execute("SELECT COUNT(*) FROM ingredient_multi_aliases").fetchone()[0]
            if multi_alias_source_exists else 0
        )
        multi_alias_mobile_exists = mobile_con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='ingredient_multi_aliases'"
        ).fetchone() is not None
        mobile_multi_alias_rows = (
            mobile_con.execute("SELECT COUNT(*) FROM ingredient_multi_aliases").fetchone()[0]
            if multi_alias_mobile_exists else 0
        )
        if (
            source_dur_rows, source_ingredient_rows, source_product_flag_rows, source_product_rows,
            source_alias_rows, source_multi_alias_rows
        ) != (
            mobile_dur_rows, mobile_ingredient_rows, mobile_product_flag_rows, mobile_product_rows,
            mobile_alias_rows, mobile_multi_alias_rows
        ):
            raise RuntimeError("mobile database row counts differ from source databases")
    finally:
        source_con.close()
        mobile_con.close()
        catalog_con.close()

    os.replace(tmp, output_path)
    payload = {
        "schema_version": MOBILE_SCHEMA_VERSION,
        "dataset_id": source_manifest["dataset_id"],
        "sha256": _sha256(output_path),
        "size_bytes": output_path.stat().st_size,
        "product_dur_rows": source_dur_rows,
        "ingredient_dur_rows": source_ingredient_rows,
        "product_item_flag_rows": source_product_flag_rows,
        "catalog_product_rows": source_product_rows,
        "ingredient_alias_rows": source_alias_rows,
        "ingredient_multi_alias_rows": source_multi_alias_rows,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _atomic_json(manifest_out, payload)
    return {**payload, "database": str(output_path), "manifest": str(manifest_out)}


__all__ = ["MOBILE_SCHEMA_VERSION", "build_mobile_database"]
