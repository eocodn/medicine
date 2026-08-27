from __future__ import annotations

import io
import hashlib
import json
import sqlite3
import tempfile
import time
import unittest
import zipfile
from contextlib import closing, redirect_stdout
from pathlib import Path
from unittest import mock

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.substance_build import (
    assemble_substance_database,
    rebuild_substance_database,
)
from medicine_canonical.substance_inspection import (
    substance_stats,
    verify_substance_database,
)
from medicine_canonical.substance_sources import (
    FDA_GSRS_UNII_NAMES_FILENAME,
    OPENFDA_UNII_FILENAME,
    iter_gsrs_unii_names,
    sync_fda_gsrs_unii_names,
    sync_openfda_unii,
)


def _zip_unii(records: list[dict[str, str]], *, last_updated: str = "2026-08-12") -> bytes:
    payload = {
        "meta": {
            "license": "https://open.fda.gov/license/",
            "last_updated": last_updated,
            "results": {"skip": 0, "limit": len(records), "total": len(records)},
        },
        "results": records,
    }
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("other-unii-0001-of-0001.json", json.dumps(payload))
    return buffer.getvalue()


def _zip_gsrs_names(
    rows: list[tuple[str, str, str, str]],
    *,
    date_token: str = "26Feb2026",
    header: str = "Name\tTYPE\tUNII\tDisplay Name",
) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        lines = [header]
        lines.extend("\t".join(row) for row in rows)
        archive.writestr(f"UNII_Names_{date_token}.txt", "\n".join(lines) + "\n")
        archive.writestr("READ ME UNII Lists.txt", "UNII Names fixture\n")
    return buffer.getvalue()

from tests.test_canonical_substances import CanonicalSubstanceTestFixture


class CanonicalSubstanceSyncTest(CanonicalSubstanceTestFixture):
    def test_sync_openfda_unii_is_atomic_and_preserves_provenance(self) -> None:
        records = [
            {"substance_name": "ALPHA", "unii": "UNIIALPHA1"},
            {"substance_name": "BETA", "unii": "UNIIBETA01"},
        ]
        archive = _zip_unii(records)
        manifest = {
            "meta": {"last_updated": "2026-08-12"},
            "results": {
                "other": {
                    "unii": {
                        "export_date": "2026-08-12",
                        "partitions": [{
                            "file": "https://download.open.fda.gov/other/unii/test.json.zip",
                            "records": 2,
                        }],
                        "total_records": 2,
                    }
                }
            },
        }
        result = sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest,
            partition_fetcher=lambda _: archive,
        )
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["effective_date"], "2026-08-12")
        self.assertTrue((self.raw_dir / OPENFDA_UNII_FILENAME).exists())
        self.assertEqual(result["source_family"], "openfda_unii")
    def test_sync_openfda_reuses_verified_snapshot_for_same_current_manifest(self) -> None:
        records = [
            {"substance_name": "ALPHA", "unii": "UNIIALPHA1"},
            {"substance_name": "BETA", "unii": "UNIIBETA01"},
        ]
        archive = _zip_unii(records)
        manifest = {
            "meta": {"last_updated": "2026-08-12"},
            "results": {
                "other": {
                    "unii": {
                        "export_date": "2026-08-12",
                        "partitions": [{
                            "file": "https://download.open.fda.gov/other/unii/test.json.zip",
                            "records": 2,
                        }],
                        "total_records": 2,
                    }
                }
            },
        }
        first = sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest,
            partition_fetcher=lambda _: archive,
        )
        events: list[dict[str, object]] = []

        second = sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest,
            partition_fetcher=lambda _: (_ for _ in ()).throw(
                AssertionError("verified current snapshot must not redownload")
            ),
            job_progress=events.append,
        )

        self.assertEqual(second, first)
        self.assertTrue(events[0]["resumed"])
        self.assertEqual(events[0]["status"], "started")
        self.assertTrue(any(event.get("status") == "checkpoint" for event in events))
        self.assertEqual(events[-1]["status"], "completed")
    def test_sync_openfda_redownloads_when_cached_archive_hash_is_invalid(self) -> None:
        records = [{"substance_name": "ALPHA", "unii": "UNIIALPHA1"}]
        archive = _zip_unii(records)
        manifest = {
            "meta": {"last_updated": "2026-08-12"},
            "results": {
                "other": {
                    "unii": {
                        "export_date": "2026-08-12",
                        "partitions": [{
                            "file": "https://download.open.fda.gov/other/unii/test.json.zip",
                            "records": 1,
                        }],
                        "total_records": 1,
                    }
                }
            },
        }
        sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest,
            partition_fetcher=lambda _: archive,
        )
        (self.raw_dir / OPENFDA_UNII_FILENAME).write_bytes(b"corrupt")
        downloads: list[str] = []

        result = sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest,
            partition_fetcher=lambda url: downloads.append(url) or archive,
        )

        self.assertEqual(downloads, ["https://download.open.fda.gov/other/unii/test.json.zip"])
        self.assertEqual(result["sha256"], hashlib.sha256(archive).hexdigest())
    def test_sync_openfda_redownloads_when_current_manifest_identity_changes(self) -> None:
        first_archive = _zip_unii(
            [{"substance_name": "ALPHA", "unii": "UNIIALPHA1"}],
            last_updated="2026-08-12",
        )
        second_archive = _zip_unii(
            [{"substance_name": "BETA", "unii": "UNIIBETA01"}],
            last_updated="2026-08-13",
        )

        def manifest(day: str, url: str) -> dict:
            return {
                "meta": {"last_updated": day},
                "results": {
                    "other": {
                        "unii": {
                            "export_date": day,
                            "partitions": [{"file": url, "records": 1}],
                            "total_records": 1,
                        }
                    }
                },
            }

        sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest("2026-08-12", "https://example.test/one.zip"),
            partition_fetcher=lambda _url: first_archive,
        )
        downloads: list[str] = []
        result = sync_openfda_unii(
            self.raw_dir,
            manifest_fetcher=lambda: manifest("2026-08-13", "https://example.test/two.zip"),
            partition_fetcher=lambda url: downloads.append(url) or second_archive,
        )

        self.assertEqual(downloads, ["https://example.test/two.zip"])
        self.assertEqual(result["effective_date"], "2026-08-13")
        self.assertEqual(result["source_locator"], "https://example.test/two.zip")
    def test_slow_gsrs_download_emits_heartbeat_while_waiting(self) -> None:
        archive = _zip_gsrs_names([
            ("RIFAMPICIN", "of", "UNIIRIFAMP", "RIFAMPIN"),
        ])
        events: list[dict[str, object]] = []

        def slow_fetch(_url: str) -> bytes:
            time.sleep(0.04)
            return archive

        with mock.patch(
            "medicine_canonical.substance_sources.SUBSTANCE_HEARTBEAT_INTERVAL_SECONDS",
            0.01,
        ):
            sync_fda_gsrs_unii_names(
                self.raw_dir,
                archive_fetcher=slow_fetch,
                job_progress=events.append,
            )

        self.assertTrue(
            any(
                event.get("status") == "heartbeat"
                and event.get("phase") == "download"
                for event in events
            )
        )
        self.assertTrue(any(event.get("status") == "progress" and "bar" in event for event in events))
    def test_substance_sync_reports_metadata_write_failure(self) -> None:
        archive = _zip_gsrs_names([
            ("RIFAMPICIN", "of", "UNIIRIFAMP", "RIFAMPIN"),
        ])
        events: list[dict[str, object]] = []

        with (
            mock.patch(
                "medicine_canonical.substance_sources._write_json_atomic",
                side_effect=OSError("metadata disk failure"),
            ),
            self.assertRaisesRegex(OSError, "metadata disk failure"),
        ):
            sync_fda_gsrs_unii_names(
                self.raw_dir,
                archive_fetcher=lambda _url: archive,
                job_progress=events.append,
            )

        self.assertEqual(events[-1]["status"], "failed")
        self.assertEqual(events[-1]["error"], "OSError")
        self.assertIn("metadata disk failure", str(events[-1]["detail"]))
    def test_sync_fda_gsrs_names_is_atomic_and_preserves_name_types(self) -> None:
        archive = _zip_gsrs_names([
            ("RIFAMPICIN", "of", "UNIIRIFAMP", "RIFAMPIN"),
            ("ALPHA BRAND", "bn", "UNIIALPHA0", "ALPHA"),
        ])
        result = sync_fda_gsrs_unii_names(
            self.raw_dir,
            archive_fetcher=lambda _: archive,
        )
        self.assertEqual(result["row_count"], 2)
        self.assertEqual(result["effective_date"], "2026-02-26")
        self.assertEqual(result["source_family"], "fda_gsrs_unii_names")
        self.assertTrue((self.raw_dir / FDA_GSRS_UNII_NAMES_FILENAME).exists())
    def test_current_fda_gsrs_names_header_is_accepted_without_relaxing_columns(self) -> None:
        archive = _zip_gsrs_names(
            [("RIFAMPICIN", "of", "UNIIRIFAMP", "RIFAMPIN")],
            header="NAME\tTYPE\tUNII\tDISPLAY_NAME",
        )
        rows = list(iter_gsrs_unii_names(archive))
        self.assertEqual(
            rows,
            [{
                "name": "RIFAMPICIN",
                "name_type": "of",
                "unii": "UNIIRIFAMP",
                "display_name": "RIFAMPIN",
            }],
        )
    def test_gsrs_exact_names_can_release_only_fully_known_permit_composition(self) -> None:
        self._write_unii_snapshot()
        self._write_gsrs_names_snapshot([
            ("GAMMA", "cn", "UNIIGAMMA1", "GAMMA PREFERRED"),
            ("DELTA", "sys", "UNIIDELTA1", "DELTA PREFERRED"),
        ])
        with closing(sqlite3.connect(self.canonical_db)) as con:
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P3',3,'감마델타정','Gamma/Delta','active','mfds_permit:products')"""
            )
            con.commit()

        assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)
        with closing(sqlite3.connect(self.substance_db)) as con:
            self.assertIsNone(
                con.execute(
                    "SELECT 1 FROM source_unparsed_expressions WHERE raw_text='Gamma/Delta'"
                ).fetchone()
            )
            delta = con.execute(
                """SELECT i.value,i.evidence_source_dataset_key
                   FROM substance_identifiers i
                   JOIN substance_names n ON n.substance_id=i.substance_id
                   WHERE n.normalized_name='delta' AND i.system='UNII'"""
            ).fetchone()
            self.assertEqual(delta, ("UNIIDELTA1", "fda_gsrs_unii_names:all"))
    def test_sync_rejects_manifest_and_archive_count_mismatch(self) -> None:
        archive = _zip_unii([{"substance_name": "ALPHA", "unii": "UNIIALPHA1"}])
        manifest = {
            "meta": {"last_updated": "2026-08-12"},
            "results": {
                "other": {
                    "unii": {
                        "export_date": "2026-08-12",
                        "partitions": [{"file": "https://example.test/unii.zip", "records": 2}],
                        "total_records": 2,
                    }
                }
            },
        }
        with self.assertRaisesRegex(RuntimeError, "row-count mismatch"):
            sync_openfda_unii(
                self.raw_dir,
                manifest_fetcher=lambda: manifest,
                partition_fetcher=lambda _: archive,
            )
    def test_rebuild_from_same_snapshots_keeps_substance_identity_stable(self) -> None:
        self._write_unii_snapshot()
        first = assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)
        with closing(sqlite3.connect(self.substance_db)) as con:
            first_names = con.execute(
                "SELECT normalized_name,substance_id FROM substance_names ORDER BY normalized_name"
            ).fetchall()
            first_ids = con.execute(
                "SELECT substance_id,system,value FROM substance_identifiers ORDER BY substance_id,system"
            ).fetchall()

        second = assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)
        with closing(sqlite3.connect(self.substance_db)) as con:
            second_names = con.execute(
                "SELECT normalized_name,substance_id FROM substance_names ORDER BY normalized_name"
            ).fetchall()
            second_ids = con.execute(
                "SELECT substance_id,system,value FROM substance_identifiers ORDER BY substance_id,system"
            ).fetchall()

        self.assertEqual(first["substances"], second["substances"])
        self.assertEqual(first["canonical_source_fingerprint"], second["canonical_source_fingerprint"])
        self.assertEqual(first_names, second_names)
        self.assertEqual(first_ids, second_ids)
    def test_substance_cli_exposes_stats_verify_and_unsolved(self) -> None:
        self._write_unii_snapshot()
        assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)
        for args in (
            ["substance-stats", "--db", str(self.substance_db), "--json"],
            ["substance-verify", "--db", str(self.substance_db), "--json"],
            ["substance-unsolved", "--db", str(self.substance_db), "--json"],
            ["substance-unparsed", "--db", str(self.substance_db), "--json"],
        ):
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = canonical_main(args)
            self.assertEqual(code, 0)
            self.assertIn('"db_path"', buf.getvalue())

        self.assertEqual(substance_stats(self.substance_db)["substances"], 13)
    def test_substance_sync_and_rebuild_thread_structured_progress(self) -> None:
        sync_payload = {
            "openfda_unii": {"row_count": 1},
            "fda_gsrs_unii_names": {"row_count": 1},
        }
        with (
            mock.patch(
                "medicine_canonical.cli.sync_substance_identity_sources",
                return_value=sync_payload,
            ) as cli_sync,
            redirect_stdout(io.StringIO()),
        ):
            self.assertEqual(
                canonical_main(
                    ["substance-sync", "--raw-dir", str(self.raw_dir), "--json"]
                ),
                0,
            )
        self.assertTrue(callable(cli_sync.call_args.kwargs["job_progress"]))

        progress = mock.Mock()
        with (
            mock.patch(
                "medicine_canonical.substance_build.sync_substance_identity_sources"
            ) as rebuild_sync,
            mock.patch(
                "medicine_canonical.substance_build.assemble_substance_database",
                return_value={"db_path": str(self.substance_db)},
            ) as assemble,
        ):
            rebuild_substance_database(
                self.substance_db,
                self.canonical_db,
                self.raw_dir,
                progress=progress,
            )
        self.assertIs(rebuild_sync.call_args.kwargs["job_progress"], progress)
        self.assertIs(assemble.call_args.kwargs["progress"], progress)
