from __future__ import annotations

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_canonical.dur_bridge import materialize_dur_ingredient_bridge
from medicine_canonical.linking import materialize_product_criterion_links
from medicine_canonical.schema import SCHEMA
from medicine_canonical.substance_schema import SUBSTANCE_SCHEMA


class DurConceptBridgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.substance_db = self.root / "substances.sqlite"
        with closing(sqlite3.connect(self.substance_db)) as con:
            con.executescript(SUBSTANCE_SCHEMA)
            con.executemany(
                "INSERT INTO substances(substance_id,canonical_name,identity_status) VALUES(?,?,?)",
                [
                    ("SUB_ALPHA", "Alpha", "resolved_external_exact"),
                    ("SUB_ALPHA_HCL", "Alpha Hydrochloride", "resolved_external_exact"),
                ],
            )
            con.executemany(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                [
                    ("alpha", "SUB_ALPHA", "Alpha"),
                    ("alpha hydrochloride", "SUB_ALPHA_HCL", "Alpha Hydrochloride"),
                ],
            )
            con.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _canonical(self) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.executescript(SCHEMA)
        for key, family in (("mfds", "mfds_dur_item_api"), ("mfds_ing", "mfds_dur_ingredient_api")):
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?)""",
                (key, family, key, key, 1, "0" * 64, "{}"),
            )
        con.execute(
            """INSERT INTO products(item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key)
               VALUES('P1',1,'알파염산염정','Alpha Hydrochloride','active','mfds')"""
        )
        return con

    def test_one_dur_concept_can_contain_distinct_precise_substances(self) -> None:
        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO products(item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key)
                   VALUES('P2',2,'알파정','Alpha','active','mfds')"""
            )
            con.executemany(
                """INSERT INTO product_rules(
                       source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name,ingredient_name_en
                   ) VALUES('mfds',?,'dose_caution',?,?,?,?)""",
                [
                    (1, "P1", "D-ALPHA", "알파염산염", "Alpha Hydrochloride"),
                    (2, "P2", "D-ALPHA", "알파", "Alpha"),
                ],
            )
            result = materialize_dur_ingredient_bridge(con, self.substance_db)

            self.assertEqual(result["dur_ingredient_concepts"], 1)
            members = {
                row[0]
                for row in con.execute(
                    """SELECT substance_id FROM dur_concept_substances
                       WHERE category='dose_caution' AND ingredient_code='D-ALPHA'"""
                )
            }
            self.assertEqual(members, {"SUB_ALPHA", "SUB_ALPHA_HCL"})
            with closing(sqlite3.connect(self.substance_db)) as substances:
                self.assertEqual(
                    substances.execute("SELECT COUNT(*) FROM substance_relations").fetchone()[0],
                    0,
                )
        finally:
            con.close()

    def test_linker_consumes_materialized_mfds_code_bridge(self) -> None:
        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO product_rules(
                       source_dataset_key,source_row,category,item_seq,ingredient_code,
                       ingredient_name,ingredient_name_en
                   ) VALUES('mfds',1,'dose_caution','P1','D-ALPHA','알파염산염','Alpha Hydrochloride')"""
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                       source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,rule_value
                   ) VALUES('mfds_ing',1,'dose_caution','Alpha','알파','알파 100mg')"""
            )
            criterion_id = int(con.execute("SELECT id FROM ingredient_rules").fetchone()[0])
            con.execute(
                """INSERT INTO ingredient_rule_codes(
                       criterion_rule_id,ingredient_code,mixture_type,
                       mixture_ingredient_codes_json,mixture_ingredient_names_json
                   ) VALUES(?, 'D-ALPHA', '단일', '[]', '[]')""",
                (criterion_id,),
            )
            bridge = materialize_dur_ingredient_bridge(con, self.substance_db)
            self.assertEqual(bridge["criterion_signatures"], 1)

            result = materialize_product_criterion_links(con)

            self.assertEqual(result["linked_product_rules"], 1)
            self.assertEqual(
                con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0],
                "mfds_ingredient_code",
            )
            self.assertEqual(
                con.execute(
                    "SELECT evidence_kind FROM dur_criterion_signatures WHERE criterion_rule_id=1"
                ).fetchone()[0],
                "mfds_criterion_composition",
            )
        finally:
            con.close()

    def test_composition_membership_uses_only_complete_atomic_components(self) -> None:
        with closing(sqlite3.connect(self.substance_db)) as substances:
            substances.executemany(
                "INSERT INTO substances(substance_id,canonical_name,identity_status) VALUES(?,?,?)",
                [
                    ("SUB_BETA", "Beta", "resolved_external_exact"),
                    ("SUB_ALPHA_BETA", "Alpha/Beta", "local_exact_unsolved"),
                ],
            )
            substances.executemany(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                [
                    ("beta", "SUB_BETA", "Beta"),
                    ("alpha/beta", "SUB_ALPHA_BETA", "Alpha/Beta"),
                ],
            )
            substances.commit()

        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO product_rules(
                       source_dataset_key,source_row,category,item_seq,ingredient_code,
                       ingredient_name,ingredient_name_en
                   ) VALUES('mfds',1,'pregnancy_contraindication','P1','D-COMBO','알파/베타','Alpha/Beta')"""
            )
            materialize_dur_ingredient_bridge(con, self.substance_db)

            members = {
                row[0]
                for row in con.execute(
                    """SELECT substance_id FROM dur_concept_substances
                       WHERE category='pregnancy_contraindication' AND ingredient_code='D-COMBO'"""
                )
            }
            self.assertEqual(members, {"SUB_ALPHA", "SUB_BETA"})
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()