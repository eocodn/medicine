from __future__ import annotations

import io
import hashlib
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
    FDA_GSRS_UNII_NAMES_FILENAME,
    OPENFDA_UNII_FILENAME,
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


def _zip_gsrs_names(rows: list[tuple[str, str, str, str]], *, date_token: str = "26Feb2026") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        lines = ["Name\tTYPE\tUNII\tDisplay Name"]
        lines.extend("\t".join(row) for row in rows)
        archive.writestr(f"UNII_Names_{date_token}.txt", "\n".join(lines) + "\n")
        archive.writestr("READ ME UNII Lists.txt", "UNII Names fixture\n")
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
                    (5, "Gamma(주사제)", "감마주사제"),
                    (6, "Gamma 함유제제", "감마함유제제"),
                    (7, "Gamma (분류번호 : 123)", "감마분류"),
                    (8, "Gamma (Gamma Synonym)", "감마동의어"),
                    (9, "Follitropin δ", "폴리트로핀델타"),
                    (10, "Gamma Hydrate", "감마수화물"),
                    (11, "Gamma Micronized", "감마미분화"),
                    (12, "Gamma (Beta)", "감마베타"),
                    (13, "Florbetaben(18F)", "플로르베타벤"),
                    (14, "St. John’s Wort", "세인트존스워트"),
                    (15, "Gamma Solid Dispersions", "감마고체분산체"),
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
        self._write_gsrs_names_snapshot()

    def _write_gsrs_names_snapshot(
        self,
        rows: list[tuple[str, str, str, str]] | None = None,
    ) -> None:
        rows = rows or [
            ("GAMMA", "cn", "UNIIGAMMA1", "GAMMA PREFERRED"),
            ("GAMMA SYNONYM", "cn", "UNIIGAMMA1", "GAMMA PREFERRED"),
            ("FOLLITROPIN DELTA", "of", "UNIIFOLLID", "FOLLITROPIN DELTA"),
            ("FLORBETABEN (18F)", "of", "UNIIFLOR18", "FLORBETABEN F18"),
            ("ST JOHN'S WORT", "cn", "UNIISTJOHN", "ST JOHN'S WORT"),
            ("ALPHA", "bn", "UNIIALPHA0", "ALPHA PREFERRED"),
        ]
        archive = _zip_gsrs_names(rows)
        self.raw_dir.mkdir(parents=True, exist_ok=True)
        path = self.raw_dir / FDA_GSRS_UNII_NAMES_FILENAME
        path.write_bytes(archive)
        meta = {
            "dataset_key": "fda_gsrs_unii_names:all",
            "source_family": "fda_gsrs_unii_names",
            "source_locator": "https://precision.fda.gov/uniisearch/archive/latest/UNIIs.zip",
            "effective_date": "2026-02-26",
            "fetched_at": "2026-08-12T23:00:00+09:00",
            "row_count": len(rows),
            "sha256": hashlib.sha256(archive).hexdigest(),
        }
        path.with_suffix(path.suffix + ".meta.json").write_text(json.dumps(meta), encoding="utf-8")

    def test_builds_exact_substance_layer_and_keeps_unsolved_visible(self) -> None:
        self._write_unii_snapshot()
        result = assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)

        self.assertEqual(result["substances"], 13)
        self.assertEqual(result["local_exact_names"], 18)
        self.assertEqual(result["resolved_external_exact"], 4)
        self.assertEqual(result["resolved_external_structured"], 3)
        self.assertEqual(result["resolved_source_relation"], 2)
        self.assertEqual(result["unsolved_substances"], 4)
        self.assertEqual(result["unparsed_source_expressions"], 1)
        self.assertEqual(
            result["unsolved_reasons"],
            {"external_exact_multiple_matches": 1, "external_exact_no_match": 3},
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
            gamma = con.execute(
                """SELECT i.value,i.evidence_source_dataset_key
                   FROM substance_identifiers i
                   JOIN substance_names n ON n.substance_id=i.substance_id
                   WHERE n.normalized_name='gamma' AND i.system='UNII'"""
            ).fetchone()
            self.assertEqual(gamma, ("UNIIGAMMA1", "fda_gsrs_unii_names:all"))
            structured_methods = dict(
                con.execute(
                    """SELECT normalized_name,match_method FROM substance_match_candidates
                       WHERE normalized_name IN (
                           'gamma(주사제)','gamma 함유제제','gamma (분류번호 : 123)',
                           'gamma (gamma synonym)','follitropin δ','florbetaben(18f)',
                           'st. john’s wort'
                       )"""
                )
            )
            self.assertEqual(structured_methods["gamma(주사제)"], "source_wrapper_exact")
            self.assertEqual(structured_methods["gamma 함유제제"], "source_wrapper_exact")
            self.assertEqual(structured_methods["gamma (분류번호 : 123)"], "source_wrapper_exact")
            self.assertEqual(structured_methods["gamma (gamma synonym)"], "source_declared_alias")
            self.assertEqual(structured_methods["follitropin δ"], "typography_greek")
            self.assertEqual(structured_methods["florbetaben(18f)"], "typography_isotope")
            self.assertEqual(structured_methods["st. john’s wort"], "typography_apostrophe")
            for unresolved in ("gamma hydrate", "gamma (beta)"):
                self.assertIsNone(
                    con.execute(
                        "SELECT 1 FROM substance_match_candidates WHERE normalized_name=?",
                        (unresolved,),
                    ).fetchone()
                )
            relations = {
                row[0]: row[1:]
                for row in con.execute(
                    """SELECT n.normalized_name,r.relation_type,b.normalized_name
                       FROM substance_relations r
                       JOIN substance_names n ON n.substance_id=r.subject_substance_id
                       JOIN substance_names b ON b.substance_id=r.object_substance_id
                       WHERE n.normalized_name IN ('gamma micronized','gamma solid dispersions')
                         AND b.normalized_name='gamma'"""
                )
            }
            self.assertEqual(relations["gamma micronized"], ("physical_form_of", "gamma"))
            self.assertEqual(relations["gamma solid dispersions"], ("formulation_of", "gamma"))
            self.assertIsNone(
                con.execute(
                    """SELECT value FROM substance_identifiers i
                       JOIN substances s ON s.substance_id=i.substance_id
                       JOIN substance_names n ON n.substance_id=s.substance_id
                       WHERE n.normalized_name='alpha'"""
                ).fetchone()
            )
            self.assertEqual(
                con.execute(
                    """SELECT reason FROM substance_unsolved u
                       JOIN substance_names n ON n.substance_id=u.substance_id
                       WHERE n.normalized_name='alpha'"""
                ).fetchone()[0],
                "external_exact_no_match",
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
            self.assertEqual(con.execute("SELECT COUNT(*) FROM substance_relations").fetchone()[0], 2)
            self.assertEqual(
                con.execute(
                    "SELECT COUNT(*) FROM source_snapshots WHERE source_family='fda_gsrs_unii_names'"
                ).fetchone()[0],
                1,
            )

        verification = verify_substance_database(self.substance_db)
        self.assertEqual(verification["status"], "verified")

    def test_build_applies_only_active_reviewed_nomenclature_alias(self) -> None:
        with closing(sqlite3.connect(self.canonical_db)) as con:
            con.execute(
                """INSERT INTO ingredient_rules(
                       source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko
                   ) VALUES('kids_mfds_xlsx:age',16,'age_contraindication',?,?)""",
                ("Atorvastatin Calcium Hydrate", "아토르바스타틴칼슘수화물"),
            )
            con.commit()
        self._write_unii_snapshot()
        self._write_gsrs_names_snapshot(
            [
                ("GAMMA", "cn", "UNIIGAMMA1", "GAMMA PREFERRED"),
                ("GAMMA SYNONYM", "cn", "UNIIGAMMA1", "GAMMA PREFERRED"),
                ("FOLLITROPIN DELTA", "of", "UNIIFOLLID", "FOLLITROPIN DELTA"),
                ("FLORBETABEN (18F)", "of", "UNIIFLOR18", "FLORBETABEN F18"),
                ("ST JOHN'S WORT", "cn", "UNIISTJOHN", "ST JOHN'S WORT"),
                ("ALPHA", "bn", "UNIIALPHA0", "ALPHA PREFERRED"),
                (
                    "ATORVASTATIN CALCIUM HYDRATE [JAN]",
                    "cn",
                    "48A5M73Z4Q",
                    "ATORVASTATIN CALCIUM TRIHYDRATE",
                ),
            ]
        )

        result = assemble_substance_database(self.substance_db, self.canonical_db, self.raw_dir)
        self.assertEqual(result["approved_nomenclature_alias_matches"], 1)
        self.assertEqual(result["active_approved_nomenclature_rows"], 1)
        with closing(sqlite3.connect(self.substance_db)) as con:
            row = con.execute(
                """SELECT c.value,c.external_name,c.match_method
                   FROM substance_match_candidates c
                   WHERE c.normalized_name='atorvastatin calcium hydrate'"""
            ).fetchone()
        self.assertEqual(
            row,
            (
                "48A5M73Z4Q",
                "ATORVASTATIN CALCIUM HYDRATE [JAN]",
                "approved_nomenclature_alias",
            ),
        )

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


if __name__ == "__main__":
    unittest.main()