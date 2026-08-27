from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import urllib.error
from contextlib import closing, redirect_stderr, redirect_stdout
from pathlib import Path
from unittest import mock

from medicine_canonical import linking as canonical_linking
from medicine_canonical.build import (
    assemble_canonical_database,
    build_canonical_database,
    canonical_stats,
    sync_reference_sources,
    verify_canonical_database,
)
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mfds_ingredient import (
    MFDS_INGREDIENT_ENDPOINTS,
    MFDS_INGREDIENT_PAGE_SIZE_MAX,
)
from medicine_canonical.mfds_sync import request_json, sync_paginated_jsonl
from medicine_canonical.schema import SCHEMA
from medicine_canonical.source_layout import MfdsSourceLayout
from medicine_canonical.source_policy import (
    CANONICAL_SOURCE_POLICY,
    EXPECTED_CANONICAL_SOURCE_FAMILIES,
    EXPECTED_CANONICAL_SOURCE_KEYS,
)
from medicine_canonical.sources import (
    DUR_ENDPOINTS,
    PERMIT_DATASET_KEY,
    PERMIT_PAGE_SIZE_MAX,
    sync_canonical_api_sources,
)

from tests.test_canonical_database import CanonicalDatabaseTestFixture


class CanonicalSyncTest(CanonicalDatabaseTestFixture):
    def test_mfds_api_page_limits_are_enforced(self) -> None:
        self.assertEqual(PERMIT_PAGE_SIZE_MAX, 500)
        self.assertEqual(MFDS_INGREDIENT_PAGE_SIZE_MAX, 500)
        with self.assertRaisesRegex(ValueError, "permit_page_size"):
            sync_canonical_api_sources(
                MfdsSourceLayout.from_roots(
                    self.root / "raw-limit", self.root / "ingredient-limit"
                ),
                service_key="test-key",
                permit_page_size=501,
                progress=False,
                permit_fetch_page=self._permit_fetch,
                dur_fetch_page=self._dur_fetch,
            )
    def test_mfds_request_retry_is_observable_without_leaking_service_key(self) -> None:
        stderr = io.StringIO()
        with (
            mock.patch(
                "medicine_canonical.mfds_sync.urllib.request.urlopen",
                side_effect=urllib.error.URLError("timed out"),
            ),
            mock.patch("medicine_canonical.mfds_sync.time.sleep"),
            redirect_stderr(stderr),
            self.assertRaisesRegex(RuntimeError, "MFDS permit API failed after 2 attempts"),
        ):
            request_json(
                "https://apis.data.go.kr/example?serviceKey=do-not-log-me",
                label="MFDS permit API",
                attempts=2,
            )
        output = stderr.getvalue()
        self.assertIn("MFDS permit API: retry 2/2 after URLError", output)
        self.assertNotIn("do-not-log-me", output)
    def test_paginated_sync_reports_first_page_before_network_fetch(self) -> None:
        stderr = io.StringIO()
        with redirect_stderr(stderr):
            sync_paginated_jsonl(
                self.root / "progress.jsonl",
                dataset_key="mfds_permit:products",
                source_family="mfds_permit_api",
                source_locator="https://apis.data.go.kr/example",
                page_size=500,
                workers=1,
                fetch_page=lambda page, size: ([{"ITEM_SEQ": "P1"}], 1),
                progress=True,
            )
        self.assertIn("[canonical-sync] mfds_permit:products: fetch first page", stderr.getvalue())
    def test_reference_sync_threads_structured_progress_to_every_source(self) -> None:
        events: list[dict[str, object]] = []
        result = sync_reference_sources(
            MfdsSourceLayout.from_roots(
                self.root / "structured-product",
                self.root / "structured-ingredient",
            ),
            service_key="test-key",
            workers=2,
            progress=False,
            job_progress=events.append,
            permit_fetch_page=self._permit_fetch,
            dur_fetch_page=self._dur_fetch,
            ingredient_fetch_page=self._ingredient_fetch,
        )

        started = [event for event in events if event.get("status") == "started"]
        completed = [event for event in events if event.get("status") == "completed"]
        expected_sources = 1 + len(DUR_ENDPOINTS) + len(MFDS_INGREDIENT_ENDPOINTS)
        self.assertEqual(len(started), expected_sources)
        self.assertEqual(len(completed), expected_sources)
        self.assertEqual(result["source_rows"], sum(int(event["row_count"]) for event in completed))
        self.assertTrue(all(event.get("job") == "mfds-source-sync" for event in events))
    def test_sync_cli_enables_structured_progress_unless_quiet(self) -> None:
        payload = {"source_rows": 0, "product_sources": {}, "ingredient_sources": {}}
        for quiet in (False, True):
            args = ["sync", "--service-key", "test-key", "--json"]
            if quiet:
                args.append("--quiet")
            with (
                mock.patch("medicine_canonical.cli.sync_reference_sources", return_value=payload) as sync,
                redirect_stdout(io.StringIO()),
            ):
                self.assertEqual(canonical_main(args), 0)
            kwargs = sync.call_args.kwargs
            self.assertEqual(kwargs["progress"], not quiet)
            if quiet:
                self.assertIsNone(kwargs["job_progress"])
            else:
                self.assertTrue(callable(kwargs["job_progress"]))
    def test_link_code_preprocessing_has_only_reviewed_explicit_equivalences(self) -> None:
        cases = {
            "D001289": "D000274",
            "D000195": "D000982",
            "D000983": "D000309",
            "D000904": "D000719",
        }
        for raw_code, expected in cases.items():
            self.assertEqual(
                canonical_linking.canonicalize_link_ingredient_code(raw_code), expected
            )
        self.assertEqual(
            canonical_linking.canonicalize_link_ingredient_code("D999999"), "D999999"
        )
    def test_linker_rejects_ingredient_criterion_without_mfds_code_payload(self) -> None:
        con = sqlite3.connect(":memory:")
        try:
            con.executescript(SCHEMA)
            for key, family in (
                ("mfds", "mfds_dur_item_api"),
                ("mfds_ing", "mfds_dur_ingredient_api"),
            ):
                con.execute(
                    """INSERT INTO source_snapshots(
                           dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (key, family, key, key, 1, "0" * 64, "{}"),
                )
            con.execute(
                """INSERT INTO product_rules(
                       source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name_en
                   ) VALUES('mfds',1,'age_contraindication','P1','D-ALPHA','Alpha')"""
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                       source_dataset_key,source_row,category,ingredient_name,rule_value
                   ) VALUES('mfds_ing',1,'age_contraindication','Alpha','12세 미만')"""
            )
            with self.assertRaisesRegex(ValueError, "authoritative ingredient code"):
                canonical_linking.materialize_product_criterion_links(con)
        finally:
            con.close()
    def test_release_source_policy_matches_declared_mfds_sources(self) -> None:
        expected = {PERMIT_DATASET_KEY}
        expected.update(f"mfds_dur:{operation}" for operation in DUR_ENDPOINTS)
        expected.update(
            f"mfds_dur_ingredient:{operation}" for operation in MFDS_INGREDIENT_ENDPOINTS
        )
        self.assertEqual(EXPECTED_CANONICAL_SOURCE_KEYS, expected)
        self.assertEqual(len(EXPECTED_CANONICAL_SOURCE_FAMILIES), 17)
    def test_verify_rejects_source_key_family_mismatch(self) -> None:
        self._build()
        with closing(sqlite3.connect(self.db)) as con:
            con.execute(
                """UPDATE source_snapshots SET source_family='mfds_dur_item_api'
                   WHERE dataset_key='mfds_dur_ingredient:getCpctyAtentInfoList02'"""
            )
            con.commit()
        verification = verify_canonical_database(self.db)
        self.assertEqual(verification["status"], "invalid")
        self.assertIn("source snapshot family mismatch", " ".join(verification["errors"]))
    def test_declares_all_expected_live_dur_endpoints(self) -> None:
        self.assertEqual(len(DUR_ENDPOINTS), 9)
        self.assertEqual(len(MFDS_INGREDIENT_ENDPOINTS), 7)
        self.assertEqual(
            {spec.category for spec in MFDS_INGREDIENT_ENDPOINTS.values()},
            {
                "combination_contraindication",
                "age_contraindication",
                "pregnancy_contraindication",
                "dose_caution",
                "duration_caution",
                "elderly_caution",
                "therapeutic_duplication_caution",
            },
        )
