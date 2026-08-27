from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

from medicine_canonical.integrated_build import assemble_integrated_databases
from medicine_canonical.schema import SCHEMA
from medicine_canonical.source_layout import MfdsSourceLayout


class IntegratedBuildLifecycleTest(unittest.TestCase):
    def test_resumes_after_substance_stage_without_reimporting_sources(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            canonical = root / "canonical.sqlite"
            substances = root / "substances.sqlite"
            raw = root / "raw"
            layout = MfdsSourceLayout.from_roots(root / "products", root / "ingredients")
            events: list[dict[str, object]] = []

            def populate(_database: sqlite3.Connection, _layout: MfdsSourceLayout) -> dict:
                return {"permit_source_rows": 1}

            def build_substance(path, *_args, **_kwargs) -> dict:
                path = Path(path)
                with closing(sqlite3.connect(path)) as database:
                    database.execute("CREATE TABLE marker(value TEXT)")
                    database.execute("INSERT INTO marker(value) VALUES('ready')")
                    database.commit()
                return {
                    "schema_version": "3",
                    "canonical_source_fingerprint": "source-fingerprint",
                }

            common = [
                mock.patch(
                    "medicine_canonical.integrated_build.integrated_build_input_fingerprint",
                    return_value="sha256:fixture",
                ),
                mock.patch(
                    "medicine_canonical.integrated_build.populate_canonical_source_tables",
                    side_effect=populate,
                ),
                mock.patch(
                    "medicine_canonical.integrated_build.assemble_substance_database",
                    side_effect=build_substance,
                ),
            ]
            with common[0], common[1], common[2], mock.patch(
                "medicine_canonical.integrated_build.materialize_product_search_documents",
                side_effect=RuntimeError("linking interrupted"),
            ):
                with self.assertRaisesRegex(RuntimeError, "linking interrupted"):
                    assemble_integrated_databases(
                        canonical,
                        substances,
                        layout,
                        raw,
                        progress=events.append,
                    )

            checkpoint = canonical.with_name(canonical.name + ".integrated.checkpoint.json")
            self.assertTrue(checkpoint.is_file())
            self.assertTrue(any(event["status"] == "checkpoint" for event in events))

            with mock.patch(
                "medicine_canonical.integrated_build.integrated_build_input_fingerprint",
                return_value="sha256:fixture",
            ), mock.patch(
                "medicine_canonical.integrated_build.populate_canonical_source_tables",
                side_effect=AssertionError("source import must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.assemble_substance_database",
                side_effect=AssertionError("substance build must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_product_search_documents",
                return_value={"documents": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_dur_ingredient_bridge",
                return_value={"bridges": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_product_criterion_links",
                return_value={"links": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_canonical_database",
                return_value={"status": "verified", "errors": []},
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_substance_database",
                return_value={"status": "verified", "errors": []},
            ), mock.patch(
                "medicine_canonical.integrated_build.canonical_stats",
                return_value={"products": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.substance_stats",
                return_value={"substances": 1},
            ):
                result = assemble_integrated_databases(
                    canonical,
                    substances,
                    layout,
                    raw,
                    progress=events.append,
                )

            self.assertEqual(result["canonical"], {"products": 1})
            self.assertEqual(result["substances"], {"substances": 1})
            self.assertFalse(checkpoint.exists())
            self.assertTrue(canonical.is_file())
            self.assertTrue(substances.is_file())

    def test_verified_partial_commit_resumes_from_authoritative_hashes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            canonical = root / "canonical.sqlite"
            substances = root / "substances.sqlite"
            raw = root / "raw"
            layout = MfdsSourceLayout.from_roots(root / "products", root / "ingredients")
            staged_canonical = canonical.with_name(canonical.name + ".integrated.tmp")
            staged_substance = substances.with_name(substances.name + ".integrated.stage")
            real_replace = os.replace

            def populate(_database: sqlite3.Connection, _layout: MfdsSourceLayout) -> dict:
                return {"permit_source_rows": 1}

            def build_substance(path, *_args, **_kwargs) -> dict:
                with closing(sqlite3.connect(path)) as database:
                    database.execute("CREATE TABLE marker(value TEXT)")
                    database.execute("INSERT INTO marker(value) VALUES('ready')")
                    database.commit()
                return {
                    "schema_version": "3",
                    "canonical_source_fingerprint": "source-fingerprint",
                }

            def interrupt_canonical_commit(source, target) -> None:
                if Path(source) == staged_canonical:
                    raise RuntimeError("canonical commit interrupted")
                real_replace(source, target)

            with mock.patch(
                "medicine_canonical.integrated_build.integrated_build_input_fingerprint",
                return_value="sha256:fixture",
            ), mock.patch(
                "medicine_canonical.integrated_build.populate_canonical_source_tables",
                side_effect=populate,
            ), mock.patch(
                "medicine_canonical.integrated_build.assemble_substance_database",
                side_effect=build_substance,
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_product_search_documents",
                return_value={"documents": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_dur_ingredient_bridge",
                return_value={"bridges": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_product_criterion_links",
                return_value={"links": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_canonical_database",
                return_value={"status": "verified", "errors": []},
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_substance_database",
                return_value={"status": "verified", "errors": []},
            ), mock.patch(
                "medicine_canonical.integrated_build.os.replace",
                side_effect=interrupt_canonical_commit,
            ):
                with self.assertRaisesRegex(RuntimeError, "canonical commit interrupted"):
                    assemble_integrated_databases(canonical, substances, layout, raw)

            checkpoint = canonical.with_name(canonical.name + ".integrated.checkpoint.json")
            self.assertTrue(checkpoint.is_file())
            self.assertTrue(substances.is_file())
            self.assertFalse(staged_substance.exists())
            self.assertTrue(staged_canonical.is_file())
            self.assertFalse(canonical.exists())

            with mock.patch(
                "medicine_canonical.integrated_build.integrated_build_input_fingerprint",
                return_value="sha256:fixture",
            ), mock.patch(
                "medicine_canonical.integrated_build.populate_canonical_source_tables",
                side_effect=AssertionError("source import must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.assemble_substance_database",
                side_effect=AssertionError("substance build must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.materialize_product_search_documents",
                side_effect=AssertionError("linking must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_canonical_database",
                side_effect=AssertionError("verification must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.verify_substance_database",
                side_effect=AssertionError("verification must not rerun"),
            ), mock.patch(
                "medicine_canonical.integrated_build.canonical_stats",
                return_value={"products": 1},
            ), mock.patch(
                "medicine_canonical.integrated_build.substance_stats",
                return_value={"substances": 1},
            ):
                result = assemble_integrated_databases(canonical, substances, layout, raw)

            self.assertEqual(result["canonical"], {"products": 1})
            self.assertEqual(result["substances"], {"substances": 1})
            self.assertFalse(checkpoint.exists())
            self.assertTrue(canonical.is_file())
            self.assertTrue(substances.is_file())


if __name__ == "__main__":
    unittest.main()