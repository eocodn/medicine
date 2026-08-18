from __future__ import annotations

import json
import sqlite3
import unittest

from medicine_canonical import linking
from medicine_canonical.schema import SCHEMA


class CanonicalLinkPreprocessingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        for key, family in (
            ("permit", "mfds_permit_api"),
            ("mfds", "mfds_dur_item_api"),
            ("xlsx", "kids_mfds_xlsx"),
            ("mfds_ing", "mfds_dur_ingredient_api"),
        ):
            self.con.execute(
                "INSERT INTO source_snapshots(dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json) VALUES(?,?,?,?,?,?,?)",
                (key, family, key, key, 1, "0" * 64, "{}"),
            )

    def tearDown(self) -> None:
        self.con.close()

    def product(self, item_seq: str, ingredient_text: str, dosage_form: str = "필름코팅정") -> None:
        self.con.execute(
            """INSERT INTO products(
                item_seq,source_row,product_name,ingredient_text,dosage_form,permit_status,source_dataset_key
            ) VALUES(?,?,?,?,?,'active','permit')""",
            (item_seq, len(list(self.con.execute("SELECT 1 FROM products"))) + 1, item_seq, ingredient_text, dosage_form),
        )

    def product_rule(
        self,
        row: int,
        category: str,
        item_seq: str,
        code: str,
        name_en: str,
        *,
        name_ko: str | None = None,
        paired_item_seq: str | None = None,
        paired_code: str | None = None,
        paired_name_en: str | None = None,
        paired_name_ko: str | None = None,
        dosage_form: str | None = None,
        effect_name: str | None = None,
    ) -> None:
        self.con.execute(
            """INSERT INTO product_rules(
                source_dataset_key,source_row,category,item_seq,ingredient_code,ingredient_name,ingredient_name_en,
                paired_item_seq,paired_ingredient_code,paired_ingredient_name,paired_ingredient_name_en,
                dosage_form,effect_name
            ) VALUES('mfds',?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                row, category, item_seq, code, name_ko, name_en,
                paired_item_seq, paired_code, paired_name_ko, paired_name_en,
                dosage_form, effect_name,
            ),
        )

    def criterion(
        self,
        row: int,
        category: str,
        ingredient: str,
        *,
        paired: str | None = None,
        ingredient_ko: str | None = None,
        rule_value: str | None = None,
    ) -> None:
        self.con.execute(
            """INSERT INTO ingredient_rules(
                source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,paired_ingredient_name,rule_value
            ) VALUES('xlsx',?,?,?,?,?,?)""",
            (row, category, ingredient, ingredient_ko, paired, rule_value),
        )

    def mfds_criterion(
        self,
        row: int,
        category: str,
        ingredient: str,
        code: str,
        *,
        paired: str | None = None,
        paired_code: str | None = None,
        mixture_type: str = "단일",
        mixture_codes: tuple[str, ...] = (),
        mixture_names: tuple[str, ...] = (),
        rule_value: str | None = None,
        dosage_form: str | None = None,
        details: str | None = None,
        note: str | None = None,
    ) -> int:
        cur = self.con.execute(
            """INSERT INTO ingredient_rules(
                source_dataset_key,source_row,category,ingredient_name,paired_ingredient_name,
                rule_value,dosage_form,note,details
            ) VALUES('mfds_ing',?,?,?,?,?,?,?,?)""",
            (row, category, ingredient, paired, rule_value, dosage_form, note, details),
        )
        criterion_id = int(cur.lastrowid)
        self.con.execute(
            """INSERT INTO ingredient_rule_codes(
                criterion_rule_id,ingredient_code,paired_ingredient_code,
                mixture_type,mixture_ingredient_codes_json,mixture_ingredient_names_json
            ) VALUES(?,?,?,?,?,?)""",
            (
                criterion_id,
                code,
                paired_code,
                mixture_type,
                json.dumps(list(mixture_codes)),
                json.dumps(list(mixture_names)),
            ),
        )
        return criterion_id

    def test_category_scoped_active_moiety_links_salt_name(self) -> None:
        self.product("P1", "Escitalopram Oxalate")
        self.product("P2", "Domperidone")
        self.product_rule(1, "combination_contraindication", "P1", "D-ESC", "Escitalopram Oxalate",
                          paired_item_seq="P2", paired_code="D-DOM", paired_name_en="Domperidone")
        self.criterion(1, "combination_contraindication", "escitalopram", paired="domperidone")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(
            self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0],
            "ingredient_preprocessed",
        )

    def test_exact_permit_composition_links_combination_criterion(self) -> None:
        self.product("P1", "Ezetimibe/Simvastatin")
        self.product("P2", "Itraconazole")
        self.product_rule(1, "combination_contraindication", "P1", "D-EZE", "Ezetimibe",
                          paired_item_seq="P2", paired_code="D-ITRA", paired_name_en="Itraconazole")
        # Official names that make the permit composition independently resolvable.
        self.product_rule(2, "dose_caution", "P1", "D-SIM", "Simvastatin")
        self.criterion(1, "combination_contraindication", "ezetimibe + simvastatin", paired="itraconazole")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(
            self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0],
            "permit_composition",
        )

    def test_duplicate_permit_ingredients_are_collapsed_for_composition(self) -> None:
        self.product("P1", "Alpha/Alpha/Beta/Beta")
        self.product("P2", "Gamma")
        self.product_rule(1, "combination_contraindication", "P1", "D-A", "Alpha",
                          paired_item_seq="P2", paired_code="D-G", paired_name_en="Gamma")
        self.product_rule(2, "dose_caution", "P1", "D-B", "Beta")
        self.criterion(1, "combination_contraindication", "alpha + beta", paired="gamma")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0], "permit_composition")

    def test_parenthetical_alias_is_local_to_the_xlsx_criterion(self) -> None:
        self.product("P1", "Pethidine Hydrochloride")
        self.product("P2", "Rasagiline Mesylate")
        self.product_rule(1, "combination_contraindication", "P1", "D-PETH", "Pethidine Hydrochloride",
                          paired_item_seq="P2", paired_code="D-RASA", paired_name_en="Rasagiline Mesylate")
        self.criterion(1, "combination_contraindication", "pethidine(meperidine)", paired="rasagiline")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0], "ingredient_preprocessed")

    def test_parenthetical_dosage_qualifier_only_links_matching_product_form(self) -> None:
        self.product("PINJ", "Risperidone", "용액주사제")
        self.product("PTAB", "Risperidone", "필름코팅정")
        self.product("PDOM", "Domperidone", "필름코팅정")
        self.product_rule(1, "combination_contraindication", "PDOM", "D-DOM", "Domperidone",
                          paired_item_seq="PINJ", paired_code="D-RIS", paired_name_en="Risperidone")
        self.product_rule(2, "combination_contraindication", "PDOM", "D-DOM", "Domperidone",
                          paired_item_seq="PTAB", paired_code="D-RIS", paired_name_en="Risperidone")
        self.criterion(1, "combination_contraindication", "domperidone", paired="risperidone (주사제)")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        linked_rows = self.con.execute(
            """SELECT r.paired_item_seq FROM product_criterion_links l
               JOIN product_rules r ON r.id=l.product_rule_id"""
        ).fetchall()
        self.assertEqual(linked_rows, [("PINJ",)])

    def test_permit_composition_can_use_exact_name_token_when_component_has_no_dur_code(self) -> None:
        self.product("PVAL", "Sodium Valproate")
        self.product("PIMI", "Cilastatin Sodium/Imipenem Hydrate", "용액주사제")
        self.product_rule(1, "combination_contraindication", "PVAL", "D-VAL", "Sodium Valproate",
                          paired_item_seq="PIMI", paired_code="D-IMI", paired_name_en="Imipenem Hydrate")
        self.product_rule(2, "pregnancy_contraindication", "PVAL", "D-VAL", "Valproic Acid")
        self.product("POTHER", "Sodium Valproate")
        self.product_rule(3, "dose_caution", "POTHER", "D-OTHER", "Sodium Valproate")
        self.criterion(1, "combination_contraindication", "valproic acid(valproate)", paired="imipenem + cilastatin")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0], "permit_composition")

    def test_paired_qualifier_can_use_dur_form_from_another_rule(self) -> None:
        self.product("PDOM", "Domperidone")
        self.product("PRIS", "Risperidone")
        self.con.execute("UPDATE products SET dosage_form=NULL WHERE item_seq='PRIS'")
        self.product_rule(1, "combination_contraindication", "PDOM", "D-DOM", "Domperidone",
                          paired_item_seq="PRIS", paired_code="D-RIS", paired_name_en="Risperidone")
        self.product_rule(2, "pregnancy_contraindication", "PRIS", "D-RIS", "Risperidone", dosage_form="용액주사제")
        self.criterion(1, "combination_contraindication", "domperidone", paired="risperidone (주사제)")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)

    def test_controlled_dilute_descriptor_maps_nitroglycerin(self) -> None:
        self.product("PTAD", "Tadalafil")
        self.product("PNIT", "Dilute Nitroglycerin Solution")
        self.product_rule(1, "combination_contraindication", "PTAD", "D-TAD", "Tadalafil",
                          paired_item_seq="PNIT", paired_code="D-NIT", paired_name_en="Dilute Nitroglycerin Solution")
        self.criterion(1, "combination_contraindication", "tadalafil", paired="nitroglycerin")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0], "ingredient_preprocessed")

    def test_parenthetical_inclusion_can_name_multiple_explicit_identities(self) -> None:
        self.product("P1", "Atenolol")
        self.product("P2", "S-Atenolol")
        self.product_rule(1, "pregnancy_contraindication", "P1", "D-AT", "Atenolol")
        self.product_rule(2, "pregnancy_contraindication", "P2", "D-SAT", "S-Atenolol")
        self.criterion(1, "pregnancy_contraindication", "Atenolol(S-atenolol 포함)")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 2)

    def test_dose_rule_value_korean_identity_can_prove_exact_salt(self) -> None:
        self.product("P1", "Alpha Special Salt")
        self.product_rule(1, "dose_caution", "P1", "D-A", "Alpha Special Salt", name_ko="알파특수염")
        self.criterion(1, "dose_caution", "Alpha", ingredient_ko="알파", rule_value="알파특수염 10mg")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 1)
        self.assertEqual(self.con.execute("SELECT match_method FROM product_criterion_links").fetchone()[0], "rule_value_identity")

    def test_new_active_moiety_ambiguity_is_reported_not_guessed(self) -> None:
        self.product("P1", "FutureDrug Hydrochloride")
        self.product("P2", "FutureDrug Succinate")
        self.product_rule(1, "dose_caution", "P1", "D-F1", "FutureDrug Hydrochloride")
        self.product_rule(2, "dose_caution", "P2", "D-F2", "FutureDrug Succinate")
        self.criterion(1, "dose_caution", "FutureDrug")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["product_criterion_links"], 0)
        self.assertEqual(result["unresolved_link_identity_count"], 1)
        self.assertEqual(result["unresolved_link_identities"][0]["ingredient_name"], "FutureDrug")
        self.assertEqual(result["unresolved_link_identities"][0]["candidate_codes"], ["D-F1", "D-F2"])


    def test_dose_rule_value_or_clause_links_each_explicit_ingredient_form(self) -> None:
        self.product("P1", "Naproxen")
        self.product("P2", "Naproxen Sodium")
        self.product_rule(1, "dose_caution", "P1", "D-NAP", "Naproxen", name_ko="나프록센")
        self.product_rule(2, "dose_caution", "P2", "D-NAS", "Naproxen Sodium", name_ko="나프록센나트륨")
        self.criterion(1, "dose_caution", "Naproxen", ingredient_ko="나프록센",
                       rule_value="나프록센 1,250mg 또는 나프록센나트륨 1,350mg")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 2)
        methods = dict(self.con.execute("""SELECT r.item_seq,l.match_method FROM product_criterion_links l
                                           JOIN product_rules r ON r.id=l.product_rule_id""").fetchall())
        self.assertEqual(methods["P1"], "english_exact")
        self.assertEqual(methods["P2"], "rule_value_identity")

    def test_dose_variant_can_be_proven_by_exact_product_detail_to_xlsx_form(self) -> None:
        self.product("P1", "Lidocaine")
        self.product("P2", "Lidocaine Hydrochloride Hydrate", "용액주사제")
        self.product_rule(1, "dose_caution", "P1", "D-LID", "Lidocaine", name_ko="리도카인", dosage_form="피부크림제")
        self.product_rule(2, "dose_caution", "P2", "D-LIDH", "Lidocaine Hydrochloride Hydrate",
                          name_ko="리도카인염산염수화물", dosage_form="용액주사제")
        self.con.execute("UPDATE product_rules SET details=? WHERE source_row=2", ("주사제, 외용액제(2%, 4%)",))
        self.criterion(1, "dose_caution", "Lidocaine", ingredient_ko="리도카인",
                       rule_value="리도카인염산염 300mg")
        self.con.execute("UPDATE ingredient_rules SET dosage_form=? WHERE source_row=1", ("주사제, 외용액제(2%, 4%)",))

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 2)
        method = self.con.execute("""SELECT l.match_method FROM product_criterion_links l
                                     JOIN product_rules r ON r.id=l.product_rule_id WHERE r.item_seq='P2'""").fetchone()[0]
        self.assertEqual(method, "product_detail_evidence")

    def test_dose_variant_can_be_proven_by_equivalent_active_moiety_detail(self) -> None:
        self.product("P1", "Piroxicam")
        self.product("P2", "Piroxicam Potassium", "용액주사제")
        self.product_rule(1, "dose_caution", "P1", "D-PIR", "Piroxicam", name_ko="피록시캄")
        self.product_rule(2, "dose_caution", "P2", "D-PIRK", "Piroxicam Potassium",
                          name_ko="피록시캄칼륨", dosage_form="용액주사제")
        self.con.execute("UPDATE product_rules SET details=? WHERE source_row=2", ("피록시캄으로서 20mg",))
        self.criterion(1, "dose_caution", "Piroxicam", ingredient_ko="피록시캄", rule_value="피록시캄 20mg")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["linked_product_rules"], 2)
        method = self.con.execute("""SELECT l.match_method FROM product_criterion_links l
                                     JOIN product_rules r ON r.id=l.product_rule_id WHERE r.item_seq='P2'""").fetchone()[0]
        self.assertEqual(method, "product_detail_evidence")

    def test_exact_dose_name_with_conflicting_product_detail_stays_unlinked_without_identity_error(self) -> None:
        self.product("P1", "Lidocaine")
        self.product_rule(1, "dose_caution", "P1", "D-LID", "Lidocaine", name_ko="리도카인", dosage_form="피부크림제")
        self.con.execute("UPDATE product_rules SET details=? WHERE source_row=1", ("크림제(4%) 리도카인 해당 제형으로 25g",))
        self.product_rule(2, "dose_caution", "P2", "D-LIDH", "Lidocaine Hydrochloride", name_ko="리도카인염산염")
        self.product("P2", "Lidocaine Hydrochloride")
        self.criterion(1, "dose_caution", "Lidocaine", ingredient_ko="리도카인", rule_value="리도카인염산염 300mg")

        result = linking.materialize_product_criterion_links(self.con)

        linked_items = {row[0] for row in self.con.execute("""SELECT r.item_seq FROM product_criterion_links l
                                                              JOIN product_rules r ON r.id=l.product_rule_id""")}
        self.assertEqual(linked_items, {"P2"})
        self.assertEqual(result["unresolved_link_identity_count"], 0)

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
            note="단일제·복합제 포함",
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

    def test_criterion_ambiguity_without_a_blocked_product_rule_is_not_reported(self) -> None:
        self.product("P1", "OtherDrug")
        self.product_rule(1, "dose_caution", "P1", "D-O", "OtherDrug")
        self.product_rule(2, "age_contraindication", "P1", "D-F1", "FutureDrug Hydrochloride")
        self.product_rule(3, "pregnancy_contraindication", "P1", "D-F2", "FutureDrug Succinate")
        self.criterion(1, "dose_caution", "FutureDrug")

        result = linking.materialize_product_criterion_links(self.con)

        self.assertEqual(result["unresolved_link_identity_count"], 0)


if __name__ == "__main__":
    unittest.main()
