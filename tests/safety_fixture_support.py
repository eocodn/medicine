from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from medicine_dur.verification import REQUIRED_HEADERS, REQUIRED_SOURCE_KEYS


def make_dur_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE source_files (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL UNIQUE,
            source_kind TEXT NOT NULL,
            category TEXT NOT NULL,
            source_path TEXT NOT NULL,
            sha256 TEXT NOT NULL,
            size_bytes INTEGER NOT NULL,
            row_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            metadata_json TEXT NOT NULL
        );
        CREATE TABLE product_dur (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            ingredient_name TEXT,
            ingredient_code TEXT,
            product_name TEXT,
            product_code TEXT,
            paired_ingredient_name TEXT,
            paired_ingredient_code TEXT,
            paired_product_name TEXT,
            paired_product_code TEXT,
            rule_value TEXT,
            details TEXT,
            notice_no TEXT,
            notice_date TEXT
        );
        CREATE TABLE product_catalog (
            product_code TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            ingredient_code TEXT,
            ingredient_name TEXT
        );
        CREATE TABLE ingredient_dur (
            id INTEGER PRIMARY KEY,
            dataset_key TEXT NOT NULL,
            source_row INTEGER NOT NULL,
            category TEXT NOT NULL,
            ingredient_name TEXT,
            ingredient_name_ko TEXT,
            paired_ingredient_name TEXT,
            rule_value TEXT,
            dosage_form TEXT,
            note TEXT,
            details TEXT,
            sequence_text TEXT
        );
        CREATE TABLE product_item_flags (
            dataset_key TEXT NOT NULL,
            item_seq TEXT NOT NULL,
            product_name TEXT NOT NULL,
            edi_code TEXT,
            category TEXT NOT NULL,
            flag_code TEXT NOT NULL,
            flag_name TEXT NOT NULL,
            dosage_form TEXT,
            ingredient_name TEXT,
            details TEXT,
            change_date TEXT,
            PRIMARY KEY(item_seq, category)
        );
        """
    )
    for index, key in enumerate(REQUIRED_SOURCE_KEYS, 1):
        kind, category = key.split(":", 1)
        con.execute(
            """INSERT INTO source_files(
                dataset_key,source_kind,category,source_path,sha256,size_bytes,row_count,metadata_json
            ) VALUES(?,?,?,?,?,?,?,?)""",
            (
                key, kind, category, f"fixture/{key}", f"{index:064x}", 1, 1,
                json.dumps({"title": "fixture 2026.08.01", "header": sorted(REQUIRED_HEADERS[key])}, ensure_ascii=False),
            ),
        )
    con.execute(
        """INSERT INTO ingredient_dur(
            dataset_key,source_row,category,ingredient_name,paired_ingredient_name,
            rule_value,dosage_form,note,details,sequence_text
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        ("ingredient:duration_caution", 1, "duration_caution", "Zolpidem", None, "28일", "정제", None, None, "1"),
    )
    con.execute(
        """INSERT INTO ingredient_dur(
            dataset_key,source_row,category,ingredient_name,paired_ingredient_name,
            rule_value,dosage_form,note,details,sequence_text
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "ingredient:combination_contraindication", 1, "combination_contraindication",
            "Alprazolam", "Itraconazole", None, None, "24시간 이내 병용금기",
            "두 성분을 함께 사용하지 않아야 함", "1",
        ),
    )
    con.execute(
        """INSERT INTO ingredient_dur(
            dataset_key,source_row,category,ingredient_name,paired_ingredient_name,
            rule_value,dosage_form,note,details,sequence_text
        ) VALUES(?,?,?,?,?,?,?,?,?,?)""",
        (
            "ingredient:lactation_caution", 1, "lactation_caution",
            "Zolpidem", None, None, "정제", None,
            "수유 중 투여 시 주의가 필요한 성분입니다.", "1",
        ),
    )
    con.execute(
        "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
        ("P-LINK", "다중EDI졸피뎀", "ING-Z", "zolpidem"),
    )
    con.execute(
        "INSERT INTO product_catalog(product_code,product_name,ingredient_code,ingredient_name) VALUES(?,?,?,?)",
        ("P-SALT", "졸피뎀염제품", "ING-ZS", "zolpidem tartrate"),
    )
    con.commit()
    con.close()


def make_catalog_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.executescript(
        """
        CREATE TABLE products (
            item_seq TEXT PRIMARY KEY,
            product_name TEXT NOT NULL,
            manufacturer TEXT,
            ingredient_name TEXT,
            dosage_form TEXT,
            edi_code TEXT,
            permit_date TEXT,
            cancel_date TEXT,
            cancel_name TEXT,
            permit_status TEXT NOT NULL,
            source TEXT NOT NULL,
            raw_json TEXT NOT NULL
        );
        """
    )
    con.executemany(
        """INSERT INTO products(
            item_seq,product_name,manufacturer,ingredient_name,dosage_form,edi_code,
            permit_date,cancel_date,cancel_name,permit_status,source,raw_json
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
        [
            ("MFDS-Z", "졸피뎀제품", "제약", "Zolpidem", "정제", None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-ZU", "졸피뎀제형미상제품", "제약", "Zolpidem", None, None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-I", "이트라코나졸제품", "제약", "Itraconazole", "캡슐제", None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-A", "알프라졸람제품", "제약", "Alprazolam", "정제", None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-X", "미확인성분제품", "제약", "Mystery Salt", "정제", None, "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-M", "다중EDI졸피뎀", "제약", "Zolpidem", "정제", "P-NONE,P-LINK", "2026-01-01", None, "정상", "active", "fixture", "{}"),
            ("MFDS-S", "졸피뎀염제품", "제약", "Zolpidem Tartrate", "정제", "P-SALT", "2026-01-01", None, "정상", "active", "fixture", "{}"),
        ],
    )
    con.commit()
    con.close()
