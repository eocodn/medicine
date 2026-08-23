from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any, Iterable

from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_SOURCES_BY_OPERATION as MFDS_INGREDIENT_ENDPOINTS,
)

from medicine_canonical.dose_criteria import parse_daily_dose_threshold
from medicine_canonical.product_search_documents import materialize_product_search_documents
from medicine_canonical.schema import SCHEMA, SCHEMA_VERSION
from medicine_canonical.source_policy import CANONICAL_SOURCE_POLICY
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_DATASET_KEY


PERMIT_SOURCE = PERMIT_DATASET_KEY
DUR_SOURCE = "mfds_dur:getUsjntTabooInfoList03"
CRITERION_SOURCE_BY_CATEGORY = {
    spec.category: f"mfds_dur_ingredient:{operation}"
    for operation, spec in MFDS_INGREDIENT_ENDPOINTS.items()
}


def expected_source_snapshots() -> list[tuple[str, str]]:
    return (
        [(PERMIT_DATASET_KEY, "mfds_permit_api")]
        + [(f"mfds_dur:{operation}", "mfds_dur_item_api") for operation in DUR_ENDPOINTS]
        + [(f"mfds_dur_ingredient:{operation}", "mfds_dur_ingredient_api") for operation in MFDS_INGREDIENT_ENDPOINTS]
    )


def create_canonical_fixture(path: Path) -> sqlite3.Connection:
    con = sqlite3.connect(path)
    con.executescript(SCHEMA)
    for ordinal, (key, family) in enumerate(expected_source_snapshots(), 1):
        con.execute(
            """INSERT INTO source_snapshots(
                   dataset_key,source_family,source_locator,snapshot_path,row_count,sha256,metadata_json
               ) VALUES(?,?,?,?,?,?,?)""",
            (key, family, key, key, 1, f"{ordinal:064x}", "{}"),
        )
    con.executemany(
        "INSERT INTO canonical_meta(key,value) VALUES(?,?)",
        [
            ("schema_version", SCHEMA_VERSION),
            ("build_stage", "complete"),
            ("built_at", "2026-08-13T15:00:00+09:00"),
            ("source_policy", CANONICAL_SOURCE_POLICY),
        ],
    )
    return con


def add_product(
    con: sqlite3.Connection,
    item_seq: str,
    name: str,
    ingredient: str,
    *,
    manufacturer: str = "제약",
    dosage_form: str | None = "정제",
    permit_status: str = "active",
    permit_date: str = "2020-01-01",
    cancel_date: str | None = None,
    cancel_name: str | None = "정상",
    edi: str | None = None,
) -> None:
    source_row = con.execute("SELECT COUNT(*)+1 FROM products").fetchone()[0]
    con.execute(
        """INSERT INTO products(
               item_seq,source_row,product_name,manufacturer,ingredient_text,dosage_form,
               permit_date,cancel_date,cancel_name,permit_status,source_dataset_key
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (item_seq, source_row, name, manufacturer, ingredient, dosage_form,
         permit_date, cancel_date, cancel_name, permit_status, PERMIT_SOURCE),
    )
    con.execute(
        "INSERT INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
        (item_seq, "MFDS_ITEM_SEQ", item_seq, PERMIT_SOURCE),
    )
    if edi:
        con.execute(
            "INSERT INTO product_identifiers(item_seq,system,value,source_dataset_key) VALUES(?,?,?,?)",
            (item_seq, "EDI", edi, PERMIT_SOURCE),
        )

def add_linked_rule(
    con: sqlite3.Connection,
    *,
    category: str,
    item_seq: str,
    ingredient: str,
    rule_value: str | None = None,
    details: str | None = None,
    paired_item_seq: str | None = None,
    paired_ingredient: str | None = None,
    dosage_form: str | None = None,
    product_dosage_form: str | None = None,
    criterion_dosage_form: str | None = None,
    effect_name: str | None = None,
    criterion_qualifier_note: str | None = None,
) -> tuple[int, int]:
    product_dosage_form = dosage_form if product_dosage_form is None else product_dosage_form
    criterion_dosage_form = dosage_form if criterion_dosage_form is None else criterion_dosage_form
    product_source_row = con.execute("SELECT COUNT(*)+1 FROM product_rules").fetchone()[0]
    ingredient_code = f"D-{item_seq}"
    paired_code = f"D-{paired_item_seq}" if paired_item_seq else None
    cur = con.execute(
        """INSERT INTO product_rules(
               source_dataset_key,source_row,category,item_seq,ingredient_code,
               ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
               paired_ingredient_name,paired_ingredient_name_en,effect_name,dosage_form,details
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, product_source_row, category, item_seq, ingredient_code,
         ingredient, ingredient, paired_item_seq, paired_code,
         paired_ingredient, paired_ingredient, effect_name, product_dosage_form, details),
    )
    product_rule_id = int(cur.lastrowid)
    criterion_source_row = con.execute("SELECT COUNT(*)+1 FROM ingredient_rules").fetchone()[0]
    cur = con.execute(
        """INSERT INTO ingredient_rules(
               source_dataset_key,source_row,category,ingredient_name,ingredient_name_ko,
               paired_ingredient_name,rule_value,dosage_form,qualifier_note,details,sequence_text
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
        (CRITERION_SOURCE_BY_CATEGORY[category], criterion_source_row, category, ingredient, ingredient,
         paired_ingredient, rule_value or effect_name, criterion_dosage_form, criterion_qualifier_note, details,
         str(criterion_source_row)),
    )
    criterion_rule_id = int(cur.lastrowid)
    con.execute(
        """INSERT INTO ingredient_rule_codes(
               criterion_rule_id,ingredient_code,paired_ingredient_code,
               mixture_type,mixture_ingredient_codes_json,mixture_ingredient_names_json
           ) VALUES(?,?,?,?,?,?)""",
        (
            criterion_rule_id,
            ingredient_code,
            paired_code,
            None if category == "combination_contraindication" else "단일",
            "[]",
            "[]",
        ),
    )
    if category == "dose_caution":
        amount, unit, status, reason = parse_daily_dose_threshold(rule_value or effect_name)
        con.execute(
            """INSERT INTO dose_criteria(
                   criterion_rule_id,maximum_daily_amount,maximum_daily_unit,parse_status,parse_reason
               ) VALUES(?,?,?,?,?)""",
            (criterion_rule_id, amount, unit, status, reason),
        )
    con.execute(
        """INSERT INTO product_criterion_links(
               product_rule_id,criterion_rule_id,match_method,pair_orientation
           ) VALUES(?,?,?,?)""",
        (product_rule_id, criterion_rule_id, "mfds_ingredient_code",
         "forward" if paired_item_seq else None),
    )
    return product_rule_id, criterion_rule_id


def add_unlinked_rule(
    con: sqlite3.Connection,
    *,
    category: str,
    item_seq: str,
    ingredient: str,
    paired_item_seq: str | None = None,
    paired_ingredient: str | None = None,
    details: str | None = None,
) -> int:
    source_row = con.execute("SELECT COUNT(*)+1 FROM product_rules").fetchone()[0]
    cur = con.execute(
        """INSERT INTO product_rules(
               source_dataset_key,source_row,category,item_seq,ingredient_code,
               ingredient_name,ingredient_name_en,paired_item_seq,paired_ingredient_code,
               paired_ingredient_name,paired_ingredient_name_en,details
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, source_row, category, item_seq, f"D-{item_seq}", ingredient, ingredient,
         paired_item_seq, f"D-{paired_item_seq}" if paired_item_seq else None,
         paired_ingredient, paired_ingredient, details),
    )
    return int(cur.lastrowid)

def add_flag(
    con: sqlite3.Connection,
    *,
    item_seq: str,
    category: str,
    details: str | None = None,
) -> None:
    source_row = con.execute("SELECT COUNT(*)+1 FROM product_flags").fetchone()[0]
    con.execute(
        """INSERT INTO product_flags(
               source_dataset_key,source_row,flag_ordinal,item_seq,category,flag_code,flag_name,details
           ) VALUES(?,?,?,?,?,?,?,?)""",
        (DUR_SOURCE, source_row, 0, item_seq, category, category, category, details),
    )


def make_canonical_db(path: Path) -> None:
    con = create_canonical_fixture(path)
    add_product(con, "MFDS-Z", "졸피뎀제품", "Zolpidem", dosage_form="정제")
    add_product(con, "MFDS-ZU", "졸피뎀제형미상제품", "Zolpidem", dosage_form=None)
    add_product(con, "MFDS-I", "이트라코나졸제품", "Itraconazole", dosage_form="캡슐제")
    add_product(con, "MFDS-A", "알프라졸람제품", "Alprazolam", dosage_form="정제")
    add_product(con, "MFDS-X", "규칙없는제품", "Mystery Salt", dosage_form="정제")
    add_product(con, "MFDS-U", "연결불완전제품", "FutureDrug Salt", dosage_form="정제")
    add_product(con, "MFDS-LU", "수유범위미확정제품", "Osimertinib Mesylate", dosage_form="정제")
    add_product(con, "MFDS-M", "정량비교졸피뎀", "Zolpidem", dosage_form="정제", edi="P-LINK")
    add_product(con, "MFDS-AGE-T", "세티리진정", "Cetirizine", dosage_form=None)
    add_product(con, "MFDS-AGE-U", "세티리진제형미상", "Cetirizine", dosage_form=None)
    add_product(con, "MFDS-AGE-N", "비고연령금기제품", "DoxyLike", dosage_form="정제")
    add_product(con, "MFDS-P1", "임부1등급제품", "PregnancyGradeOne", dosage_form="정제")
    add_product(con, "MFDS-P2", "임부2등급제품", "PregnancyGradeTwo", dosage_form="정제")
    add_product(con, "MFDS-PC", "조건부임부금기제품", "PregnancyConditional", dosage_form="정제")
    add_product(con, "MFDS-PN", "비고조건임부금기제품", "PregnancyNoteConditional", dosage_form="정제")
    add_product(con, "MFDS-PA", "임부등급충돌제품", "PregnancyAmbiguous", dosage_form="정제")
    add_product(con, "MFDS-CW", "중단조건약", "ConditionalWashout", dosage_form="정제")
    add_product(con, "MFDS-CT", "중단조건대상약", "ConditionalTarget", dosage_form="정제")
    add_product(con, "MFDS-CN-A", "용량조건병용약", "ConditionalDoseA", dosage_form="정제")
    add_product(con, "MFDS-CN-B", "용량조건대상약", "ConditionalDoseB", dosage_form="정제")
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-Z", ingredient="Zolpidem",
        rule_value="28일", details="최대 투여기간 28일", dosage_form="정제",
    )
    add_linked_rule(
        con, category="duration_caution", item_seq="MFDS-M", ingredient="Zolpidem",
        rule_value="28일", details="최대 투여기간 28일", dosage_form="정제",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-A", ingredient="Alprazolam",
        paired_item_seq="MFDS-I", paired_ingredient="Itraconazole", details="병용금기",
    )
    add_linked_rule(
        con,
        category="age_contraindication",
        item_seq="MFDS-AGE-T",
        ingredient="Cetirizine",
        rule_value="액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        product_dosage_form="필름코팅정",
        criterion_dosage_form="액제, 정제, 캡슐제",
    )
    add_linked_rule(
        con,
        category="age_contraindication",
        item_seq="MFDS-AGE-U",
        ingredient="Cetirizine",
        rule_value="액제: 2세 미만, 정제, 캡슐제: 6세 미만",
        product_dosage_form=None,
        criterion_dosage_form="액제, 정제, 캡슐제",
    )
    add_linked_rule(
        con, category="age_contraindication", item_seq="MFDS-AGE-N",
        ingredient="DoxyLike", rule_value="12세 미만", criterion_qualifier_note="다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여",
        details="12세 미만 소아 주의", dosage_form="정제",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-P1",
        ingredient="PregnancyGradeOne", rule_value="1등급", details="임부금기 1등급",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-P2",
        ingredient="PregnancyGradeTwo", rule_value="2", details="임부금기 2등급",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PC",
        ingredient="PregnancyConditional", rule_value="2등급(말라리아 치료시 제외)",
        details="말라리아 치료 목적이면 예외가 될 수 있음",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PN",
        ingredient="PregnancyNoteConditional", rule_value="2등급",
        criterion_qualifier_note="단, 강심제로 사용시 제외",
        details="강심제 사용 여부에 따라 예외가 있음",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PA",
        ingredient="PregnancyAmbiguous", rule_value="1등급", details="적응증 A",
    )
    add_linked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-PA",
        ingredient="PregnancyAmbiguous", rule_value="2등급", details="적응증 B",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-CW",
        ingredient="ConditionalWashout", paired_item_seq="MFDS-CT",
        paired_ingredient="ConditionalTarget",
        details="ConditionalWashout 중단한 직후에는 ConditionalTarget 시작할 수 없음",
    )
    add_linked_rule(
        con, category="combination_contraindication", item_seq="MFDS-CN-A",
        ingredient="ConditionalDoseA", paired_item_seq="MFDS-CN-B",
        paired_ingredient="ConditionalDoseB", details="혈액학적 독성 증가",
        criterion_qualifier_note="methotrexate(1週에 20mg 이상 투여시)",
    )
    add_unlinked_rule(
        con, category="duration_caution", item_seq="MFDS-ZU", ingredient="Zolpidem",
        details="제형 적용범위를 확정하지 못함",
    )
    add_unlinked_rule(
        con, category="pregnancy_contraindication", item_seq="MFDS-U", ingredient="FutureDrug Salt",
    )
    # The mobile release gate verifies bridge materialization, so this synthetic
    # canonical fixture carries representative bridge rows instead of bypassing
    # release verification in tests.
    criterion_id = con.execute("SELECT id FROM ingredient_rules ORDER BY id LIMIT 1").fetchone()[0]
    con.execute(
        "INSERT INTO dur_ingredient_concepts(concept_id,category,ingredient_code) VALUES('fixture:concept','duration_caution','D-MFDS-Z')"
    )
    con.execute(
        """INSERT INTO dur_product_item_signatures(
               item_seq,signature_type,signature_key,component_count,match_method,evidence_kind
           ) VALUES('MFDS-Z','code','D-MFDS-Z',1,'mfds_ingredient_code','fixture')"""
    )
    con.execute(
        """INSERT INTO dur_criterion_signatures(
               criterion_rule_id,category,effect_key,signature_key,match_method,evidence_kind
           ) VALUES(?,'duration_caution','','D-MFDS-Z','mfds_ingredient_code','fixture')""",
        (criterion_id,),
    )
    materialize_product_search_documents(con, None)
    con.commit()
    con.close()


__all__ = [
    "create_canonical_fixture", "make_canonical_db", "add_product", "add_linked_rule", "add_unlinked_rule",
    "add_flag", "PERMIT_SOURCE", "DUR_SOURCE", "CRITERION_SOURCE_BY_CATEGORY",
]
