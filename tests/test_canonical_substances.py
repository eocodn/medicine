from __future__ import annotations

import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from contextlib import closing, redirect_stdout
from pathlib import Path

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.substance_build import (
    assemble_substance_database,
)
from medicine_canonical.substance_inspection import (
    substance_stats,
    verify_substance_database,
)
from medicine_canonical.substance_sources import (
    OPENFDA_UNII_FILENAME,
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


class CanonicalSubstanceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.canonical_db = self.root / "canonical.sqlite"
        self.substance_db = self.root / "canonical_substances.sqlite"
        self.raw_dir = self.root / "substances"
        self._write_canonical_fixture()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _write_canonical_fixture(self) -> None:
        with closing(sqlite3.connect(self.canonical_db)) as con:
            con.executescript(SCHEMA)
            snapshots = [
                ("mfds_permit:products", "mfds_permit_api"),
                ("mfds_dur:age", "mfds_dur_item_api"),
                ("kids_mfds_xlsx:age", "kids_mfds_xlsx"),
            ]
            for key, family in snapshots:
                con.execute(
                    """INSERT INTO source_snapshots(
                           dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                       ) VALUES(?,?,?,?,?,?,?)""",
                    (key, family, key, key, 1, "0" * 64, "{}"),
                )
            con.execute(
                "INSERT INTO canonical_meta(key,value) VALUES('schema_version',?)",
                (SCHEMA_VERSION,),
            )
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P1',1,'알파복합정','Alpha Hydrochloride/Beta/Beta','active','mfds_permit:products')"""
            )
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P2',2,'전분액','Hydroxyethyl Starch 130/0.42/Sodium Chloride','active','mfds_permit:products')"""
            )
            con.executemany(
                """INSERT INTO product_rules(
                       source_dataset_key,source_row,category,item_seq,ingredient_code,
                       ingredient_name,ingredient_name_en
                   ) VALUES('mfds_dur:age',?,?,?,?,?,?)""",
                [
                    (1, "age_contraindication", "P1", "D-ALPHA", "알파염산염", "Alpha Hydrochloride"),
                    (2, "age_contraindication", "P1", "D-GAMMA", "감마", "Gamma"),
                ],
            )
            con.executemany(
                """INSERT INTO ingredient_rules(
                       source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko
                   ) VALUES('kids_mfds_xlsx:age',?,'age_contraindication',?,?)""",
                [
                    (1, "Alpha", "알파"),
                    (2, "Ambiguous", "모호"),
                    (3, "Ethinyl Estradiol", "에티닐에스트라디올"),
                    (4, "Ethinylestradiol", "에티닐에스트라디올"),
                ],
            )
            con.commit()

    def _write_unii_snapshot(self) -> None:
        records = [
            {"substance_name": "ALPHA HYDROCHLORIDE", "unii": "UNIIALPHA1"},
            {"substance_name": "BETA", "unii": "UNIIBETA01"},
            {"substance_name": "AMBIGUOUS", "unii": "UNIIAMBIG1"},
            {"substance_name": " ambiguous ", "unii": "UNIIAMBIG2"},
            {"substance_name": "ETHINYL ESTRADIOL", "unii": "UNIIEE0001"},
            {"substance_name": "ETHINYLESTRADIOL", "unii": "UNIIEE0001"},
        ]
        archive = _zip_unii(records)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / OPENFDA_UNII_FILENAME
        path.write_bytes(archive)
        import hashlib

        meta = {
            "dataset_key": "openfda_unii:all",
            "source_family": "openfda_unii",
            "source_locator": "https://download.open.fda.gov/other/unii/test.json.zip",
            "effective_date": "2026-08-12",
            "fetched_at": "2026-08-12T22:00:00+09:00",
            "row_count": len(records),
            "sha256": hashlib.sha256(archive).hexdigest(),
        }
        path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_builds_exact_substance_layer_and_keeps_unsolved_visible(self) -> None:
        self._write_unii_snapshot()
        result = assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)

        self.assertEqual(result["substances"], 6)
        self.assertEqual(result["local_exact_names"], 7)
        self.assertEqual(result["resolved_external_exact"], 3)
        self.assertEqual(result["unsolved_substances"], 3)
        self.assertEqual(result["unparsed_source_expressions"], 1)
        self.assertEqual(
            result["unsolved_reasons"],
            {"external_exact_multiple_matches": 1, "external_exact_no_match": 2},
        )

        with closing(sqlite3.connect(self.substance_db)) as con:
            alpha_hcl = con.execute(
                """SELECT s.canonical_name,i.value
                   FROM substances s
                   JOIN substance_identifiers i ON i.substance_id=s.substance_id
                   JOIN substance_names n ON n.substance_id=s.substance_id
                   WHERE n.normalized_name='alpha hydrochloride' AND i.system='UNII'"""
            ).fetchone()
            self.assertEqual(alpha_hcl, ("Alpha Hydrochloride", "UNIIALPHA1"))
            self.assertIsNone(
                con.execute(
                    """SELECT value FROM substance_identifiers i
                       JOIN substances s ON s.substance_id=i.substance_id
                       JOIN substance_names n ON n.substance_id=s.substance_id
                       WHERE n.normalized_name='alpha'"""
                ).fetchone()
            )
            beta_permit = con.execute(
                """SELECT occurrence_count FROM source_identities
                   WHERE source_scope='permit_component' AND normalized_name='beta'"""
            ).fetchone()[0]
            self.assertEqual(beta_permit, 1)
            self.assertIsNone(
                con.execute(
                    "SELECT substance_id FROM substance_names WHERE normalized_name='0.42'"
                ).fetchone()
            )
            unparsed = con.execute(
                """SELECT raw_text,reason FROM source_unparsed_expressions
                   WHERE source_scope='permit_composition'"""
            ).fetchone()
            self.assertEqual(
                unparsed,
                (
                    "Hydroxyethyl Starch 130/0.42/Sodium Chloride",
                    "ambiguous_composition_delimiter",
                ),
            )
            ambiguous = set(
                row[0]
                for row in con.execute(
                    """SELECT c.value
                       FROM substance_match_candidates c
                       WHERE c.normalized_name='ambiguous'"""
                )
            )
            self.assertEqual(ambiguous, {"UNIIAMBIG1", "UNIIAMBIG2"})
            aliases = con.execute(
                """SELECT COUNT(DISTINCT substance_id),MIN(substance_id),MAX(substance_id)
                   FROM substance_names
                   WHERE normalized_name IN ('ethinyl estradiol','ethinylestradiol')"""
            ).fetchone()
            self.assertEqual(aliases[0], 1)
            self.assertEqual(aliases[1], aliases[2])
            self.assertEqual(
                con.execute(
                    "SELECT value FROM substance_identifiers WHERE substance_id=? AND system='UNII'",
                    (aliases[1],),
                ).fetchone()[0],
                "UNIIEE0001",
            )
            self.assertEqual(con.execute("SELECT COUNT(*) FROM substance_relations").fetchone()[0], 0)

        verification = verify_substance_database(self.substance_db)
        self.assertEqual(verification["status"], "verified")

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

        self.assertEqual(substance_stats(self.substance_db)["substances"], 6)


if __name__ == "__main__":
    unittest.main()