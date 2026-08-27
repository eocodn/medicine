from __future__ import annotations

import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from .build import populate_canonical_source_tables, sync_reference_sources
from .canonical_job import canonical_build_stage
from .dur_bridge import materialize_dur_ingredient_bridge
from .integrated_job import integrated_build_input_fingerprint
from .inspection import canonical_stats, verify_canonical_database
from .job_lifecycle import JobLifecycle, sqlite_heartbeat
from .linking import materialize_product_criterion_links
from .schema import SCHEMA, SCHEMA_VERSION
from .source_policy import CANONICAL_SOURCE_POLICY
from .mfds_ingredient import IngredientFetchPage
from .product_search_documents import materialize_product_search_documents
from .source_layout import MfdsSourceLayout
from .snapshot_io import sha256_file
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
    source_layout: MfdsSourceLayout,
    substance_raw_dir: str | Path,
    *,
    progress=None,
    checkpoint_path: str | Path | None = None,
) -> dict:
    """Build source → substance → DUR bridge → product links in that order."""
    canonical_db = Path(canonical_db_path)
    substance_db = Path(substance_db_path)
    substance_raw = Path(substance_raw_dir)
    canonical_db.parent.mkdir(parents=True, exist_ok=True)
    substance_db.parent.mkdir(parents=True, exist_ok=True)
    staged_canonical = canonical_db.with_name(canonical_db.name + ".integrated.tmp")
    staged_substance = substance_db.with_name(substance_db.name + ".integrated.stage")
    checkpoint = (
        Path(checkpoint_path)
        if checkpoint_path is not None
        else canonical_db.with_name(canonical_db.name + ".integrated.checkpoint.json")
    )
    input_fingerprint = integrated_build_input_fingerprint(source_layout, substance_raw)
    lifecycle = JobLifecycle(
        "integrated-build",
        checkpoint,
        input_fingerprint=input_fingerprint,
        progress=progress,
        total_steps=5,
    )
    started = time.monotonic()
    current_phase = "startup"
    lifecycle.started()

    try:
        phase = lifecycle.completed_phase
        allowed_phases = {None, "source_staged", "substance_staged", "linked", "verified"}
        if phase not in allowed_phases:
            lifecycle.discard(f"unknown completed phase {phase!r}")
        if phase is not None:
            if lifecycle.artifacts.get("staged_canonical") != str(staged_canonical):
                lifecycle.discard("staged canonical database path changed")
            if lifecycle.artifacts.get("staged_substance") != str(staged_substance):
                lifecycle.discard("staged substance database path changed")
            source_result = lifecycle.artifacts.get("source_result")
            if not isinstance(source_result, dict):
                lifecycle.discard("integrated checkpoint source_result is missing or invalid")

        if phase is None:
            staged_canonical.unlink(missing_ok=True)
            staged_substance.unlink(missing_ok=True)
            staged_substance.with_name(staged_substance.name + ".tmp").unlink(missing_ok=True)
            staged_substance.with_name(
                staged_substance.name + ".build.checkpoint.json"
            ).unlink(missing_ok=True)
            current_phase = "source_staged"
            lifecycle.step_started(current_phase, 1)
            with closing(sqlite3.connect(staged_canonical)) as con, sqlite_heartbeat(
                con, lifecycle, current_phase
            ):
                con.executescript(SCHEMA)
                con.execute("BEGIN")
                source_result = populate_canonical_source_tables(con, source_layout)
                _insert_source_stage_meta(con)
                con.commit()
            canonical_sha256 = sha256_file(staged_canonical)
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_canonical": str(staged_canonical),
                    "staged_substance": str(staged_substance),
                    "canonical_sha256": canonical_sha256,
                    "source_result": source_result,
                },
            )
            lifecycle.step_completed(current_phase, 1)
            phase = lifecycle.completed_phase
        else:
            canonical_sha256 = lifecycle.artifacts.get("canonical_sha256")
            if not isinstance(canonical_sha256, str) or not canonical_sha256:
                lifecycle.discard("integrated checkpoint canonical sha256 is missing")
            if phase != "verified":
                if not staged_canonical.exists() or sha256_file(staged_canonical) != canonical_sha256:
                    lifecycle.discard("checkpointed staged canonical database bytes changed")

        if phase == "source_staged":
            if canonical_build_stage(staged_canonical) != "source":
                lifecycle.discard("authoritative integrated canonical database is not at source stage")
            current_phase = "substance_staged"
            lifecycle.step_started(current_phase, 2)
            substance_result = assemble_substance_database(
                staged_substance,
                staged_canonical,
                substance_raw,
                progress=progress,
            )
            canonical_sha256 = sha256_file(staged_canonical)
            substance_sha256 = sha256_file(staged_substance)
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_canonical": str(staged_canonical),
                    "staged_substance": str(staged_substance),
                    "canonical_sha256": canonical_sha256,
                    "substance_sha256": substance_sha256,
                    "source_result": source_result,
                    "substance_result": substance_result,
                },
            )
            lifecycle.step_completed(current_phase, 2)
            phase = lifecycle.completed_phase
        else:
            substance_result = lifecycle.artifacts.get("substance_result")
            if phase in {"substance_staged", "linked", "verified"} and not isinstance(
                substance_result, dict
            ):
                lifecycle.discard("integrated checkpoint substance_result is missing or invalid")
            if phase == "linked":
                substance_sha256 = lifecycle.artifacts.get("substance_sha256")
                if not isinstance(substance_sha256, str) or not substance_sha256:
                    lifecycle.discard("integrated checkpoint substance sha256 is missing")
                if not staged_substance.exists() or sha256_file(staged_substance) != substance_sha256:
                    lifecycle.discard("checkpointed linked substance database bytes changed")
                if canonical_build_stage(staged_canonical) != "complete":
                    lifecycle.discard("checkpointed integrated canonical database is not linked")

        if phase == "substance_staged":
            substance_sha256 = lifecycle.artifacts.get("substance_sha256")
            if not isinstance(substance_sha256, str) or not substance_sha256:
                lifecycle.discard("integrated checkpoint substance sha256 is missing")
            if not staged_substance.exists() or sha256_file(staged_substance) != substance_sha256:
                lifecycle.discard("checkpointed staged substance database bytes changed")
            current_phase = "linked"
            lifecycle.step_started(current_phase, 3)
            with closing(sqlite3.connect(staged_canonical)) as con, sqlite_heartbeat(
                con, lifecycle, current_phase
            ):
                con.execute("BEGIN")
                search_result = materialize_product_search_documents(con, staged_substance)
                bridge_result = materialize_dur_ingredient_bridge(con, staged_substance)
                link_result = materialize_product_criterion_links(con)
                built_at = datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
                con.execute(
                    "DELETE FROM canonical_meta WHERE key IN "
                    "('built_at','build_stage','dur_bridge_substance_schema_version',"
                    "'dur_bridge_substance_source_fingerprint')"
                )
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
            canonical_sha256 = sha256_file(staged_canonical)
            substance_sha256 = sha256_file(staged_substance)
            lifecycle.checkpoint(
                current_phase,
                {
                    "staged_canonical": str(staged_canonical),
                    "staged_substance": str(staged_substance),
                    "canonical_sha256": canonical_sha256,
                    "substance_sha256": substance_sha256,
                    "source_result": source_result,
                    "substance_result": substance_result,
                    "search_result": search_result,
                    "bridge_result": bridge_result,
                    "link_result": link_result,
                },
            )
            lifecycle.step_completed(current_phase, 3)
            phase = lifecycle.completed_phase
        else:
            search_result = lifecycle.artifacts.get("search_result")
            bridge_result = lifecycle.artifacts.get("bridge_result")
            link_result = lifecycle.artifacts.get("link_result")
            if phase in {"linked", "verified"} and not all(
                isinstance(result, dict) for result in (search_result, bridge_result, link_result)
            ):
                lifecycle.discard("integrated checkpoint linking results are missing or invalid")

        if phase == "linked":
            current_phase = "verified"
            lifecycle.step_started(current_phase, 4)
            lifecycle.heartbeat(current_phase, force=True)
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
            canonical_sha256 = sha256_file(staged_canonical)
            substance_sha256 = sha256_file(staged_substance)
            lifecycle.checkpoint(
                current_phase,
                {
                    **lifecycle.artifacts,
                    "canonical_sha256": canonical_sha256,
                    "substance_sha256": substance_sha256,
                },
            )
            lifecycle.step_completed(current_phase, 4)
            phase = lifecycle.completed_phase

        if phase != "verified":
            lifecycle.discard(f"cannot commit from completed phase {phase!r}")
        canonical_sha256 = lifecycle.artifacts.get("canonical_sha256")
        substance_sha256 = lifecycle.artifacts.get("substance_sha256")
        if not isinstance(canonical_sha256, str) or not isinstance(substance_sha256, str):
            lifecycle.discard("verified integrated checkpoint is missing artifact sha256")

        current_phase = "commit"
        lifecycle.step_started(current_phase, 5)
        # Product canonical is self-contained after link materialization. Replace
        # the substance DB first and canonical DB last. On restart, exact verified
        # hashes distinguish a partially committed generation from unrelated state.
        if staged_substance.exists():
            if sha256_file(staged_substance) != substance_sha256:
                lifecycle.discard("verified staged substance database bytes changed")
            os.replace(staged_substance, substance_db)
        elif not substance_db.exists() or sha256_file(substance_db) != substance_sha256:
            lifecycle.discard("committed substance database does not match verified sha256")
        if staged_canonical.exists():
            if sha256_file(staged_canonical) != canonical_sha256:
                lifecycle.discard("verified staged canonical database bytes changed")
            os.replace(staged_canonical, canonical_db)
        elif not canonical_db.exists() or sha256_file(canonical_db) != canonical_sha256:
            lifecycle.discard("committed canonical database does not match verified sha256")
        lifecycle.step_completed(current_phase, 5)
        lifecycle.completed()
    except Exception as exc:
        lifecycle.failed(current_phase, exc)
        if lifecycle.completed_phase is None:
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
        "product_search": search_result,
        "linking": link_result,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def build_integrated_databases(
    canonical_db_path: str | Path,
    substance_db_path: str | Path,
    *,
    service_key: str,
    source_layout: MfdsSourceLayout,
    substance_raw_dir: str | Path,
    permit_page_size: int = 500,
    dur_page_size: int = 500,
    ingredient_page_size: int = 500,
    api_workers: int = 8,
    progress: bool = True,
    job_progress=None,
    permit_fetch_page: PermitFetchPage | None = None,
    dur_fetch_page: DurFetchPage | None = None,
    ingredient_fetch_page: IngredientFetchPage | None = None,
) -> dict:
    sync_reference_sources(
        source_layout,
        service_key=service_key,
        permit_page_size=permit_page_size,
        dur_page_size=dur_page_size,
        ingredient_page_size=ingredient_page_size,
        workers=api_workers,
        progress=progress,
        job_progress=job_progress,
        permit_fetch_page=permit_fetch_page,
        dur_fetch_page=dur_fetch_page,
        ingredient_fetch_page=ingredient_fetch_page,
    )
    return assemble_integrated_databases(
        canonical_db_path,
        substance_db_path,
        source_layout,
        substance_raw_dir,
        progress=job_progress,
    )


__all__ = ["assemble_integrated_databases", "build_integrated_databases"]
