from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from medicine_canonical.product_ingredient_applicability import (
    materialize_product_ingredient_criterion_links,
)
from medicine_canonical.schema import SCHEMA
from medicine_canonical.substance_schema import SUBSTANCE_SCHEMA


class ProductIngredientApplicabilityTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.substance_db = self.root / "substances.sqlite"
        with closing(sqlite3.connect(self.substance_db)) as con:
            con.executescript(SUBSTANCE_SCHEMA)
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                   ) VALUES('reviewed','external_identity','reviewed','reviewed',1,?, '{}')""",
                ("0" * 64,),
            )
            con.executemany(
                "INSERT INTO substances(substance_id,canonical_name,identity_status) VALUES(?,?,?)",
                [
                    ("SUB_ALPHA", "Alpha", "resolved_external_exact"),
                    ("SUB_ALPHA_HCL", "Alpha Hydrochloride", "resolved_external_exact"),
                    ("SUB_ALPHA_MIC", "Alpha Micronized", "resolved_external_exact"),
                    ("SUB_ALPHA_PHOS", "Alpha Phosphate", "resolved_external_exact"),
                    ("SUB_BETA", "Beta", "resolved_external_exact"),
                ],
            )
            con.executemany(
                "INSERT INTO substance_names(normalized_name,substance_id,representative_name) VALUES(?,?,?)",
                [
                    ("alpha", "SUB_ALPHA", "Alpha"),
                    ("alpha hydrochloride", "SUB_ALPHA_HCL", "Alpha Hydrochloride"),
                    ("alpha micronized", "SUB_ALPHA_MIC", "Alpha Micronized"),
                    ("alpha phosphate", "SUB_ALPHA_PHOS", "Alpha Phosphate"),
                    ("beta", "SUB_BETA", "Beta"),
                ],
            )
            con.execute(
                """INSERT INTO substance_relations(
                       subject_substance_id,relation_type,object_substance_id,
                       evidence_source_dataset_key,evidence_json
                   ) VALUES('SUB_ALPHA_MIC','physical_form_of','SUB_ALPHA','reviewed','{}')"""
            )
            con.commit()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _canonical(self) -> sqlite3.Connection:
        con = sqlite3.connect(":memory:")
        con.row_factory = sqlite3.Row
        con.executescript(SCHEMA)
        for key, family in (("permit", "mfds_permit_api"), ("mfds", "mfds_dur_item_api"), ("xlsx", "kids_mfds_xlsx")):
            con.execute(
                """INSERT INTO source_snapshots(
                       dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
                   ) VALUES(?,?,?,?,?,?,?)""",
                (key, family, key, key, 1, "0" * 64, "{}"),
            )
        products = [
            ("P-SIG", 1, "시그정", "Beta"),
            ("P-EXACT", 2, "알파정", "Alpha"),
            ("P-REL", 3, "알파미분화정", "Alpha Micronized"),
            ("P-SCOPE", 4, "알파염산염정", "Alpha Hydrochloride"),
            ("P-UNKNOWN", 5, "알파인산염정", "Alpha Phosphate"),
        ]
        con.executemany(
            """INSERT INTO products(
                   item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
               ) VALUES(?,?,?,?,'active','permit')""",
            products,
        )
        con.execute(
            """INSERT INTO ingredient_rules(
                   id,source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,note
               ) VALUES(1,'xlsx',1,'lactation_caution','Alpha','알파','수유 주의')"""
        )
        con.execute(
            """INSERT INTO product_rules(
                   id,source_dataset_key,source_row,category,item_seq,ingredient_code,
                   ingredient_name,ingredient_name_en
               ) VALUES(1,'mfds',1,'pregnancy_contraindication','P-SCOPE','D-SCOPE','알파','Alpha')"""
        )
        con.execute(
            """INSERT INTO dur_product_item_signatures(
                   item_seq,signature_type,signature_key,component_count,match_method,evidence_kind
               ) VALUES('P-SIG','code','[\"D-ALPHA\"]',1,'mfds_ingredient_code','mfds_code_scope')"""
        )
        con.execute(
            """INSERT INTO dur_criterion_signatures(
                   criterion_rule_id,category,effect_key,signature_type,signature_key,
                   qualifier,match_method,evidence_kind
               ) VALUES(1,'lactation_caution','','code','[\"D-ALPHA\"]',NULL,
                        'mfds_ingredient_code','mfds_code_scope')"""
        )
        return con

    def test_materializes_only_authoritative_lactation_applicability(self) -> None:
        con = self._canonical()
        try:
            result = materialize_product_ingredient_criterion_links(con, self.substance_db)
            self.assertEqual(result["product_ingredient_criterion_links"], 4)
            self.assertEqual(result["unresolved_product_ingredient_criteria"], 1)
            self.assertEqual(
                dict(con.execute(
                    """SELECT item_seq,match_method FROM product_ingredient_criterion_links
                       ORDER BY item_seq"""
                ).fetchall()),
                {
                    "P-EXACT": "precise_substance_exact",
                    "P-REL": "reviewed_substance_relation",
                    "P-SCOPE": "same_item_official_dur_name",
                    "P-SIG": "dur_scope_signature",
                },
            )
            unresolved = con.execute(
                """SELECT item_seq,reason,evidence_json
                   FROM product_ingredient_criterion_unresolved"""
            ).fetchone()
            self.assertEqual(unresolved["item_seq"], "P-UNKNOWN")
            self.assertEqual(unresolved["reason"], "scope_relation_unproven")
            evidence = json.loads(unresolved["evidence_json"])
            self.assertEqual(evidence["product_component"], "alpha phosphate")
            self.assertEqual(evidence["criterion_name"], "alpha")
        finally:
            con.close()

    def test_scope_name_candidate_never_becomes_positive_identity(self) -> None:
        con = self._canonical()
        try:
            materialize_product_ingredient_criterion_links(con, self.substance_db)
            self.assertIsNone(
                con.execute(
                    "SELECT 1 FROM product_ingredient_criterion_links WHERE item_seq='P-UNKNOWN'"
                ).fetchone()
            )
            with closing(sqlite3.connect(self.substance_db)) as substances:
                self.assertEqual(
                    substances.execute(
                        """SELECT COUNT(*) FROM substance_relations
                           WHERE subject_substance_id='SUB_ALPHA_PHOS' AND object_substance_id='SUB_ALPHA'"""
                    ).fetchone()[0],
                    0,
                )
        finally:
            con.close()

    def test_incomplete_composition_fails_closed_instead_of_partial_exact_match(self) -> None:
        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P-COMBO',6,'복합정','Alpha/Unknown Component','active','permit')"""
            )
            materialize_product_ingredient_criterion_links(con, self.substance_db)
            self.assertIsNone(
                con.execute(
                    "SELECT 1 FROM product_ingredient_criterion_links WHERE item_seq='P-COMBO'"
                ).fetchone()
            )
            row = con.execute(
                """SELECT reason FROM product_ingredient_criterion_unresolved
                   WHERE item_seq='P-COMBO' AND criterion_rule_id=1"""
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row[0], "product_composition_unresolved")
        finally:
            con.close()

    def test_exact_multi_component_criterion_requires_complete_composition(self) -> None:
        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P-ALPHA-BETA',7,'알파베타정','Alpha/Beta','active','permit')"""
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                       id,source_dataset_key,source_row,category,ingredient_name,note
                   ) VALUES(2,'xlsx',2,'lactation_caution','Alpha/Beta','복합 수유 주의')"""
            )
            materialize_product_ingredient_criterion_links(con, self.substance_db)
            row = con.execute(
                """SELECT match_method,evidence_json
                   FROM product_ingredient_criterion_links
                   WHERE item_seq='P-ALPHA-BETA' AND criterion_rule_id=2"""
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["match_method"], "precise_substance_exact")
            self.assertEqual(
                set(json.loads(row["evidence_json"])["substance_ids"]),
                {"SUB_ALPHA", "SUB_BETA"},
            )
        finally:
            con.close()

    def test_typographic_scope_key_equivalence_is_unresolved_not_clear(self) -> None:
        con = self._canonical()
        try:
            con.execute(
                """INSERT INTO products(
                       item_seq,source_row,product_name,ingredient_text,permit_status,source_dataset_key
                   ) VALUES('P-TYPO',8,'알파표기정','Alpha-Phosphate','active','permit')"""
            )
            con.execute(
                """INSERT INTO ingredient_rules(
                       id,source_dataset_key,source_row,category,ingredient_name,note
                   ) VALUES(3,'xlsx',3,'lactation_caution','Alpha Phosphate','표기 검토')"""
            )
            materialize_product_ingredient_criterion_links(con, self.substance_db)
            row = con.execute(
                """SELECT reason,evidence_json
                   FROM product_ingredient_criterion_unresolved
                   WHERE item_seq='P-TYPO' AND criterion_rule_id=3"""
            ).fetchone()
            self.assertIsNotNone(row)
            self.assertEqual(row["reason"], "product_component_identity_unresolved")
            self.assertEqual(
                json.loads(row["evidence_json"])["candidate_kind"],
                "normalized_scope_key_equivalent",
            )
        finally:
            con.close()

    def test_materialization_is_idempotent(self) -> None:
        con = self._canonical()
        try:
            first = materialize_product_ingredient_criterion_links(con, self.substance_db)
            second = materialize_product_ingredient_criterion_links(con, self.substance_db)
            self.assertEqual(first, second)
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM product_ingredient_criterion_links").fetchone()[0],
                4,
            )
            self.assertEqual(
                con.execute("SELECT COUNT(*) FROM product_ingredient_criterion_unresolved").fetchone()[0],
                1,
            )
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
