from __future__ import annotations

from medicine_reference.reference_update import REFERENCE_CONTRACT_MAJOR as RUNTIME_CONTRACT_MAJOR
from medicine_canonical import linking
from medicine_canonical.mobile import REFERENCE_CONTRACT_MAJOR as BUILDER_CONTRACT_MAJOR
from medicine_canonical.preprocessing import IdentityResolver
from medicine_canonical.schema import SCHEMA_VERSION
from tests.canonical_linking_test_support import CanonicalLinkingFixture


class MfdsFalseNegativeRecoveryTest(CanonicalLinkingFixture):
    def test_permit_locant_slash_is_one_identity_but_composition_slash_still_splits(self) -> None:
        resolver = IdentityResolver()
        resolver.add(
            "therapeutic_duplication_caution",
            "Methyl-N,S-Diacetylcysteine",
            "D000968",
        )
        resolver.add("therapeutic_duplication_caution", "Pancreatic Protease", "D009999")
        resolver.add("therapeutic_duplication_caution", "Alpha", "D000001")
        resolver.add("therapeutic_duplication_caution", "Beta", "D000002")

        self.assertEqual(
            resolver.resolve_permit_composition(
                "Methyl-N/S-Diacetylcysteine", "therapeutic_duplication_caution"
            ),
            frozenset({"D000968"}),
        )
        self.assertEqual(
            resolver.resolve_permit_composition(
                "Methyl-N/S-Diacetylcysteine/Pancreatic Protease",
                "therapeutic_duplication_caution",
            ),
            frozenset({"D000968", "D009999"}),
        )
        self.assertEqual(
            resolver.resolve_permit_composition("Alpha/Beta", "therapeutic_duplication_caution"),
            frozenset({"D000001", "D000002"}),
        )

    def test_exact_meaningful_details_can_disambiguate_same_code_criterion(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        self.con.execute(
            "UPDATE product_rules SET details=? WHERE source_row=1",
            ("  생후   4주 미만  ",),
        )
        criterion_id = self.mfds_criterion(
            1,
            "age_contraindication",
            "Example",
            "D000111",
            mixture_type="복합",
            mixture_codes=("D000222",),
            mixture_names=("Other",),
            rule_value="4주 미만",
            dosage_form="주사제",
            details="생후 4주 미만",
        )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(
            self.con.execute(
                "SELECT criterion_rule_id,match_method FROM product_criterion_links"
            ).fetchall(),
            [(criterion_id, "mfds_details_exact")],
        )

    def test_placeholder_details_do_not_become_identity_evidence(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        self.con.execute("UPDATE product_rules SET details='-' WHERE source_row=1")
        self.mfds_criterion(
            1,
            "age_contraindication",
            "Example",
            "D000111",
            mixture_type="복합",
            mixture_codes=("D000222",),
            mixture_names=("Other",),
            rule_value="4주 미만",
            dosage_form="주사제",
            details="-",
        )
        self.mfds_criterion(
            2,
            "age_contraindication",
            "Example",
            "D000111",
            mixture_type="복합",
            mixture_codes=("D000333",),
            mixture_names=("Other 2",),
            rule_value="18세 미만",
            dosage_form="캡슐제",
            details="_",
        )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product_criterion_links").fetchone()[0], 0)

    def test_conflicting_exact_detail_values_remain_unlinked(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        self.con.execute("UPDATE product_rules SET details='같은 상세' WHERE source_row=1")
        for row, mixture_code, rule_value in (
            (1, "D000222", "4주 미만"),
            (2, "D000333", "18세 미만"),
        ):
            self.mfds_criterion(
                row,
                "age_contraindication",
                "Example",
                "D000111",
                mixture_type="복합",
                mixture_codes=(mixture_code,),
                mixture_names=(f"Other {row}",),
                rule_value=rule_value,
                dosage_form="주사제",
                details="같은   상세",
            )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product_criterion_links").fetchone()[0], 0)

    def test_unanimous_same_code_value_survives_unresolved_form_and_composition(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        self.con.execute("UPDATE product_rules SET details='품목 상세' WHERE source_row=1")
        ids = []
        for row, mixture_code, form in (
            (1, "D000222", "주사제"),
            (2, "D000333", "캡슐제"),
        ):
            ids.append(
                self.mfds_criterion(
                    row,
                    "age_contraindication",
                    "Example",
                    "D000111",
                    mixture_type="복합",
                    mixture_codes=(mixture_code,),
                    mixture_names=(f"Other {row}",),
                    rule_value="18세 미만",
                    dosage_form=form,
                    details=f"기준 {row}",
                )
            )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(
            self.con.execute(
                "SELECT criterion_rule_id,match_method FROM product_criterion_links ORDER BY criterion_rule_id"
            ).fetchall(),
            [(ids[0], "mfds_unanimous_value"), (ids[1], "mfds_unanimous_value")],
        )

    def test_conflicting_same_code_values_remain_unlinked(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        for row, mixture_code, rule_value in (
            (1, "D000222", "4주 미만"),
            (2, "D000333", "18세 미만"),
        ):
            self.mfds_criterion(
                row,
                "age_contraindication",
                "Example",
                "D000111",
                mixture_type="복합",
                mixture_codes=(mixture_code,),
                mixture_names=(f"Other {row}",),
                rule_value=rule_value,
                dosage_form="주사제",
                details=f"기준 {row}",
            )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(self.con.execute("SELECT COUNT(*) FROM product_criterion_links").fetchone()[0], 0)

    def test_unanimous_fallback_preserves_qualifier_for_contract_materialization(self) -> None:
        self.product("P1", "Example", "정제")
        self.product_rule(1, "age_contraindication", "P1", "D000111", "Example", dosage_form="정제")
        for row, mixture_code, qualifier in (
            (1, "D000222", "점안제(1%)"),
            (2, "D000333", None),
        ):
            self.mfds_criterion(
                row,
                "age_contraindication",
                "Example",
                "D000111",
                mixture_type="복합",
                mixture_codes=(mixture_code,),
                mixture_names=(f"Other {row}",),
                rule_value="12세 미만",
                dosage_form="주사제",
                details=f"기준 {row}",
                qualifier_note=qualifier,
            )
        actual_dataset_key = "mfds_dur_ingredient:getSpcifyAgrdeTabooInfoList02"
        self.con.execute(
            "INSERT INTO source_snapshots(dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
            (actual_dataset_key, "mfds_dur_ingredient_api", "age", "age", 2, "1" * 64, "{}"),
        )
        self.con.execute(
            "UPDATE ingredient_rules SET source_dataset_key=? WHERE category='age_contraindication'",
            (actual_dataset_key,),
        )
        linking.materialize_product_criterion_links(self.con)

        linked = self.con.execute(
            """SELECT l.match_method,i.qualifier_note
               FROM product_criterion_links l
               JOIN ingredient_rules i ON i.id=l.criterion_rule_id
               ORDER BY i.source_row"""
        ).fetchall()
        self.assertEqual(
            linked,
            [("mfds_unanimous_value", "점안제(1%)"), ("mfds_unanimous_value", None)],
        )

    def test_explicit_concentration_scope_still_blocks_unreviewed_item_fallback(self) -> None:
        self.product("202600107", "Atropine Sulfate", "점안용액제")
        self.product_rule(
            1,
            "age_contraindication",
            "202600107",
            "D000656",
            "Atropine Sulfate",
            dosage_form="점안용액제",
        )
        for row, mixture_code in ((1, "D000222"), (2, "D000333")):
            self.mfds_criterion(
                row,
                "age_contraindication",
                "Atropine",
                "D000656",
                mixture_type="복합",
                mixture_codes=(mixture_code,),
                mixture_names=(f"Other {row}",),
                rule_value="4세 미만",
                dosage_form="주사제",
                details=f"기준 {row}",
            )

        linking.materialize_product_criterion_links(self.con)

        self.assertEqual(
            self.con.execute("SELECT COUNT(*) FROM product_criterion_links").fetchone()[0],
            0,
        )

    def test_semantic_link_change_advances_persisted_generations(self) -> None:
        self.assertEqual(SCHEMA_VERSION, "11")
        self.assertEqual(BUILDER_CONTRACT_MAJOR, 1)
        self.assertEqual(RUNTIME_CONTRACT_MAJOR, 1)
