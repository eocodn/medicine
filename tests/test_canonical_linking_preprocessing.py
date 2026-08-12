from __future__ import annotations

import sqlite3
import unittest

from medicine_canonical import linking
from medicine_canonical.schema import SCHEMA


class CanonicalLinkPreprocessingTest(unittest.TestCase):
    def setUp(self) -> None:
        self.con = sqlite3.connect(":memory:")
        self.con.executescript(SCHEMA)
        for key, family in (("permit", "mfds_permit_api"), ("mfds", "mfds_dur_item_api"), ("xlsx", "kids_mfds_xlsx")):
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
