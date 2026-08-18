from __future__ import annotations

import json
import sqlite3
import unittest

from medicine_canonical.schema import SCHEMA


class CanonicalLinkingFixture(unittest.TestCase):
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



__all__ = ["CanonicalLinkingFixture"]
