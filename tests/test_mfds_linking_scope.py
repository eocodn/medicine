from __future__ import annotations

from medicine_canonical import linking
from tests.canonical_linking_test_support import CanonicalLinkingFixture


class MfdsLinkingScopeTest(CanonicalLinkingFixture):
    def test_mfds_criterion_code_links_same_regulatory_code_despite_name_variant(self) -> None:
        self.product("P1", "Amikacin Sulfate", "용액주사제")
        self.product_rule(
            1,
            "dose_caution",
            "P1",
            "D000550",
            "Amikacin Sulfate",
            name_ko="아미카신황산염",
            dosage_form="용액주사제",
        )
        self.con.execute(
            "UPDATE product_rules SET details=? WHERE source_row=1",
            ("아미카신 1.5g(역가)",),
        )
        self.mfds_criterion(
            1,
            "dose_caution",
            "Amikacin",
            "D000550",
            rule_value="아미카신 1.5그램",
            dosage_form="용액주사제/용액용분말주사제",
            details="아미카신 1.5g(역가)",
        )

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 1)
        self.assertEqual(
            self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0],
            "mfds_ingredient_code",
        )

    def test_mfds_criterion_form_scope_filters_same_code_rows(self) -> None:
        self.product("P1", "Clobetasol Propionate", "피부크림제")
        self.product_rule(
            1,
            "duration_caution",
            "P1",
            "D000479",
            "Clobetasol Propionate",
            dosage_form="피부크림제",
        )
        cream_id = self.mfds_criterion(
            1,
            "duration_caution",
            "Clobetasol Propionate",
            "D000479",
            rule_value="14일",
            dosage_form="피부크림제/피부연고제/피부현탁액제",
        )
        self.mfds_criterion(
            2,
            "duration_caution",
            "Clobetasol Propionate",
            "D000479",
            rule_value="28일",
            dosage_form="피부로션제/샴푸/일반액상분무제",
        )

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 1)
        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {cream_id})

    def test_atropine_age_concentration_exceptions_are_item_scoped(self) -> None:
        for item_seq in ("199403253", "202007499", "202600107"):
            self.product(item_seq, "Atropine Sulfate", "점안용액제")
        self.product_rule(1, "age_contraindication", "199403253", "D000656", "Atropine Sulfate", dosage_form="점안용액제")
        self.product_rule(2, "age_contraindication", "202007499", "D000656", "Atropine Sulfate", dosage_form="점안용액제")
        self.product_rule(3, "age_contraindication", "202600107", "D000656", "Atropine Sulfate", dosage_form="점안용액제")
        self.mfds_criterion(
            1, "age_contraindication", "Atropine", "D000656",
            rule_value="12세 미만", dosage_form="점안용액제", qualifier_note="점안제(1%)",
        )
        self.mfds_criterion(
            2, "age_contraindication", "Atropine", "D000656",
            rule_value="4세 미만", dosage_form="점안용액제", qualifier_note="점안제(0.125%)",
        )

        linking.materialize_product_criterion_links(self.con)

        linked = self.con.execute(
            """SELECT r.item_seq,i.rule_value
               FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               JOIN ingredient_rules i ON i.id=l.criterion_rule_id
               ORDER BY r.item_seq,i.rule_value"""
        ).fetchall()
        self.assertEqual(linked, [("199403253", "12세 미만"), ("202007499", "4세 미만")])

    def test_propofol_age_concentration_exceptions_are_item_scoped(self) -> None:
        for item_seq in ("201101016", "201100954", "201100959"):
            self.product(item_seq, "Propofol", "유화주사제")
        self.product_rule(1, "age_contraindication", "201101016", "D001068", "Propofol", dosage_form="유화주사제")
        self.product_rule(2, "age_contraindication", "201100954", "D001068", "Propofol", dosage_form="유화주사제")
        self.product_rule(3, "age_contraindication", "201100959", "D001068", "Propofol", dosage_form="유화주사제")
        self.mfds_criterion(
            1, "age_contraindication", "Propofol", "D001068",
            rule_value="1개월 이하", dosage_form="유화주사제",
            qualifier_note="1개월 이하(1%) (전신마취), 36개월 미만(2%) (전신마취)",
        )
        self.mfds_criterion(
            2, "age_contraindication", "Propofol", "D001068",
            rule_value="36개월 미만", dosage_form="유화주사제",
            qualifier_note="1개월 이하(1%) (전신마취), 36개월 미만(2%) (전신마취)",
        )

        linking.materialize_product_criterion_links(self.con)

        linked = self.con.execute(
            """SELECT r.item_seq,i.rule_value
               FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id
               JOIN ingredient_rules i ON i.id=l.criterion_rule_id
               ORDER BY r.item_seq,i.rule_value"""
        ).fetchall()
        self.assertEqual(linked, [("201100954", "36개월 미만"), ("201101016", "1개월 이하")])

    def test_mfds_tablet_age_rule_does_not_apply_to_injection_of_same_code(self) -> None:
        self.product("P1", "Diclofenac Sodium", "용액주사제")
        self.product_rule(
            1,
            "age_contraindication",
            "P1",
            "D000506",
            "Diclofenac Sodium",
            dosage_form="용액주사제",
        )
        injection_id = self.mfds_criterion(
            1,
            "age_contraindication",
            "Diclofenac",
            "D000506",
            rule_value="4주 미만",
            dosage_form="용액주사제/유화주사제",
        )
        self.mfds_criterion(
            2,
            "age_contraindication",
            "Diclofenac",
            "D000506",
            rule_value="18세 미만",
            dosage_form="정제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {injection_id})

    def test_mfds_generic_form_label_matches_authoritative_product_subtype(self) -> None:
        self.product("P1", "Meloxicam", "경질캡슐제, 산제")
        self.product_rule(
            1,
            "dose_caution",
            "P1",
            "D000427",
            "Meloxicam",
            dosage_form="경질캡슐제, 산제",
        )
        criterion_id = self.mfds_criterion(
            1,
            "dose_caution",
            "Meloxicam",
            "D000427",
            rule_value="멜록시캄 15밀리그램",
            dosage_form="정제/캡슐",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {criterion_id})

    def test_mfds_same_code_combination_criterion_requires_exact_product_composition(self) -> None:
        self.product("P1", "Metformin", "필름코팅정")
        self.product_rule(
            1,
            "dose_caution",
            "P1",
            "D000718",
            "Metformin",
            dosage_form="필름코팅정",
        )
        single_id = self.mfds_criterion(
            1,
            "dose_caution",
            "Metformin",
            "D000718",
            mixture_type="단일",
            rule_value="메트포르민염산염 2,550밀리그램",
            dosage_form="나정/필름코팅정",
        )
        self.mfds_criterion(
            2,
            "dose_caution",
            "Metformin",
            "D000718",
            mixture_type="복합",
            mixture_codes=("D000244",),
            mixture_names=("Sitagliptin",),
            rule_value="메트포르민 2,000밀리그램/시타글립틴 100밀리그램",
            dosage_form="정제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {single_id})

    def test_mfds_combination_scope_uses_authoritative_mixture_name_code_alias(self) -> None:
        self.product("P1", "Brimonidine Tartrate/Timolol Maleate", "점안용액제")
        self.product_rule(
            1,
            "age_contraindication",
            "P1",
            "D000641",
            "Brimonidine Tartrate",
            dosage_form="점안용액제",
        )
        criterion_id = self.mfds_criterion(
            1,
            "age_contraindication",
            "Brimonidine",
            "D000641",
            mixture_type="복합",
            mixture_codes=("D000684",),
            mixture_names=("Timolol",),
            rule_value="2세 미만",
            dosage_form="점안용액제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {criterion_id})

    def test_mfds_single_scope_uses_product_rule_code_when_permit_has_one_component(self) -> None:
        self.product("P1", "Sodium Alendronate Hydrate", "나정")
        self.product_rule(
            1,
            "pregnancy_contraindication",
            "P1",
            "D000658",
            "Alendronate",
            dosage_form="나정",
        )
        criterion_id = self.mfds_criterion(
            1,
            "pregnancy_contraindication",
            "Alendronate",
            "D000658",
            mixture_type="단일",
            rule_value="2등급",
            dosage_form="정제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {criterion_id})

    def test_mfds_explicit_all_forms_and_single_combo_remark_overrides_narrow_scope(self) -> None:
        self.product("P1", "Acetaminophen", "용액주사제")
        self.product("P2", "Acetaminophen/Pseudoephedrine Hydrochloride", "나정")
        self.product_rule(
            1,
            "dose_caution",
            "P1",
            "D000147",
            "Acetaminophen",
            dosage_form="용액주사제",
        )
        self.product_rule(
            2,
            "dose_caution",
            "P2",
            "D000147",
            "Acetaminophen",
            dosage_form="나정",
        )
        criterion_id = self.mfds_criterion(
            1,
            "dose_caution",
            "Acetaminophen",
            "D000147",
            mixture_type="단일",
            rule_value="아세트아미노펜 4,000밀리그램",
            dosage_form="고형제/반고형제/액제",
            qualifier_note="단일제·복합제 포함",
            details="모든 제형",
        )

        linking.materialize_product_criterion_links(self.con)

        linked = {
            (row[0], row[1])
            for row in self.con.execute(
                "SELECT product_rule_id,criterion_rule_id FROM product_criterion_links"
            )
        }
        self.assertEqual(linked, {(1, criterion_id), (2, criterion_id)})

    def test_mfds_product_composition_is_resolved_within_dur_category(self) -> None:
        self.product("P1", "Shared Ingredient", "필름코팅정")
        self.product("P2", "Shared Ingredient", "필름코팅정")
        self.product_rule(
            1,
            "dose_caution",
            "P1",
            "D000111",
            "Shared Ingredient",
            dosage_form="필름코팅정",
        )
        self.product_rule(
            2,
            "pregnancy_contraindication",
            "P2",
            "D000222",
            "Shared Ingredient",
            dosage_form="필름코팅정",
        )
        criterion_id = self.mfds_criterion(
            1,
            "dose_caution",
            "Shared Ingredient",
            "D000111",
            mixture_type="단일",
            rule_value="10밀리그램",
            dosage_form="정제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {criterion_id})
        category_signature = self.con.execute(
            """SELECT signature_key,evidence_kind
               FROM dur_product_category_signatures
               WHERE item_seq='P1' AND category='dose_caution'"""
        ).fetchone()
        self.assertEqual(category_signature, ('["D000111"]', "category_permit_composition"))

    def test_mfds_single_criterion_accepts_permit_salt_of_category_active_moiety(self) -> None:
        self.product("P1", "Amlodipine Maleate", "필름코팅정")
        self.product_rule(
            1,
            "pregnancy_contraindication",
            "P1",
            "D000152",
            "Amlodipine",
            dosage_form="필름코팅정",
        )
        criterion_id = self.mfds_criterion(
            1,
            "pregnancy_contraindication",
            "Amlodipine",
            "D000152",
            mixture_type="단일",
            rule_value="2등급",
            dosage_form="정제",
        )

        linking.materialize_product_criterion_links(self.con)

        linked_ids = {
            row[0] for row in self.con.execute("SELECT criterion_rule_id FROM product_criterion_links")
        }
        self.assertEqual(linked_ids, {criterion_id})
        category_signature = self.con.execute(
            """SELECT signature_key,evidence_kind
               FROM dur_product_category_signatures
               WHERE item_seq='P1' AND category='pregnancy_contraindication'"""
        ).fetchone()
        self.assertEqual(category_signature, ('["D000152"]', "category_permit_composition"))



if __name__ == "__main__":
    unittest.main()
