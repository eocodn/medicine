from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .build import populate_canonical_source_tables, sync_reference_sources
from .dur_bridge import materialize_dur_ingredient_bridge
from .inspection import canonical_stats, verify_canonical_database
from .linking import materialize_product_criterion_links
from .schema import SCHEMA, SCHEMA_VERSION
from .source_policy import CANONICAL_SOURCE_POLICY
from .mfds_ingredient import IngredientFetchPage
from .sources import DurFetchPage, PermitFetchPage
from .substance_build import assemble_substance_database
from .substance_inspection import substance_stats, verify_substance_database


APP_TIMEZONE = ZoneInfo("Asia/Seoul")


def _insert_source_stage_meta(con: sqlite3.Connection) -> None:
    con.executemany(
        "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
        [
            ("schema_version", SCHEMA_VERSION),
            ("source_policy", CANONICAL_SOURCE_POLICY),
            ("build_stage", "source"),
        ],
    )


def assemble_integrated_databases(
    canonical_db_path: str | Path,
    substance_db_path: str | Path,
    canonical_raw_dir: str | Path,
    substance_raw_dir: str | Path,
    ingredient_raw_dir: str | Path,
) -> dict:
    """Build source → substance → DUR bridge → product links in that order."""
    canonical_db = Path(canonical_db_path)
    substance_db = Path(substance_db_path)
    canonical_raw = Path(canonical_raw_dir)
    substance_raw = Path(substance_raw_dir)
    ingredient_raw = Path(ingredient_raw_dir)
    canonical_db.parent.mkdir(parents=True, exist_ok=True)
    substance_db.parent.mkdir(parents=True, exist_ok=True)
    staged_canonical = canonical_db.with_name(canonical_db.name + ".integrated.tmp")
    staged_substance = substance_db.with_name(substance_db.name + ".integrated.stage")
    staged_canonical.unlink(missing_ok=True)
    staged_substance.unlink(missing_ok=True)
    staged_substance.with_name(staged_substance.name + ".tmp").unlink(missing_ok=True)
    started = time.monotonic()

    try:
        with closing(sqlite3.connect(staged_canonical)) as con:
            con.executescript(SCHEMA)
            con.execute("BEGIN")
            source_result = populate_canonical_source_tables(
                con, canonical_raw, ingredient_raw
            )
            _insert_source_stage_meta(con)
            con.commit()

        substance_result = assemble_substance_database(
            staged_substance,
            staged_canonical,
            substance_raw,
        )

        with closing(sqlite3.connect(staged_canonical)) as con:
            con.execute("BEGIN")
            bridge_result = materialize_dur_ingredient_bridge(con, staged_substance)
            link_result = materialize_product_criterion_links(con)
            built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
            con.execute("DELETE FROM canonical_meta WHERE key IN ('built_at','build_stage','dur_bridge_substance_schema_version','dur_bridge_substance_source_fingerprint')")
            con.executemany(
                "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
                [
                    ("built_at", built_at),
                    ("build_stage", "complete"),
                    ("dur_bridge_substance_schema_version", str(substance_result["schema_version"])),
                    (
                        "dur_bridge_substance_source_fingerprint",
                        str(substance_result["canonical_source_fingerprint"]),
                    ),
                ],
            )
            con.commit()
            con.execute("ANALYZE")
            con.execute("PRAGMA optimize")
            con.commit()

        canonical_verification = verify_canonical_database(staged_canonical)
        if canonical_verification["status"] != "verified":
            raise RuntimeError(
                "integrated canonical verification failed: "
                + "; ".join(canonical_verification["errors"])
            )
        substance_verification = verify_substance_database(staged_substance)
        if substance_verification["status"] != "verified":
            raise RuntimeError(
                "integrated substance verification failed: "
                + "; ".join(substance_verification["errors"])
            )

        # Product canonical is self-contained after link materialization. Replace
        # the substance DB first and canonical DB last so a failed final rename
        # cannot expose a canonical DB that expects a substance generation which
        # was never installed.
        os.replace(staged_substance, substance_db)
        os.replace(staged_canonical, canonical_db)
    except Exception:
        staged_canonical.unlink(missing_ok=True)
        staged_substance.unlink(missing_ok=True)
        staged_substance.with_name(staged_substance.name + ".tmp").unlink(missing_ok=True)
        raise

    canonical_result = canonical_stats(canonical_db)
    final_substance_result = substance_stats(substance_db)
    return {
        "canonical": canonical_result,
        "substances": final_substance_result,
        "source_import": source_result,
        "dur_bridge": bridge_result,
        "linking": link_result,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def build_integrated_databases(
    canonical_db_path: str | Path,
    substance_db_path: str | Path,
    *,
    service_key: str,
    canonical_raw_dir: str | Path,
    substance_raw_dir: str | Path,
    ingredient_raw_dir: str | Path,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    ingredient_page_size: int = 500,
    api_workers: int = 8,
    progress: bool = True,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
    ingredient_fetch_page: IngredientFetchPage | None = None,
) -> dict:
    sync_reference_sources(
        canonical_raw_dir,
        ingredient_raw_dir,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        ingredient_page_size=ingredient_page_size,
        workers=api_workers,
        progress=progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
        ingredient_fetch_page=ingredient_fetch_page,
    )
    return assemble_integrated_databases(
        canonical_db_path,
        substance_db_path,
        canonical_raw_dir,
        substance_raw_dir,
        ingredient_raw_dir,
    )


__all__ = ["assemble_integrated_databases", "build_integrated_databases"]
