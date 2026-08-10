from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator


class ProductRepository:
    def __init__(self, dur_db: Path | str, catalog_db: Path | str | None = None):
        self.dur_db = Path(dur_db)
        self.catalog_db = Path(catalog_db) if catalog_db else None

    @contextmanager
    def _dur(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self.dur_db.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        try:
            yield con
        finally:
            con.close()

    @contextmanager
    def _catalog(self) -> Iterator[sqlite3.Connection]:
        if not self.catalog_db or not self.catalog_db.exists():
            raise FileNotFoundError("catalog database not available")
        uri = f"file:{self.catalog_db.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        try:
            yield con
        finally:
            con.close()

    def has_full_catalog(self) -> bool:
        if not self.catalog_db or not self.catalog_db.exists():
            return False
        try:
            with self._catalog() as con:
                return con.execute("SELECT 1 FROM products LIMIT 1").fetchone() is not None
        except sqlite3.DatabaseError:
            return False

    def _dur_codes(self, codes: list[str]) -> set[str]:
        codes = [code for code in codes if code]
        if not codes:
            return set()
        placeholders = ",".join("?" for _ in codes)
        with self._dur() as con:
            rows = con.execute(
                f"SELECT product_code FROM product_catalog WHERE product_code IN ({placeholders})",
                codes,
            ).fetchall()
        return {row["product_code"] for row in rows}

    def search(self, term: str, limit: int = 30) -> list[dict]:
        term = term.strip()
        if not term:
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        results: list[dict] = []
        seen_dur_codes: set[str] = set()
        if self.has_full_catalog():
            like = f"%{term}%"
            prefix = f"{term}%"
            with self._catalog() as con:
                rows = con.execute(
                    """
                    SELECT item_seq, product_name, manufacturer, ingredient_name,
                           dosage_form, edi_code, permit_date, cancel_date, source
                    FROM products
                    WHERE (cancel_date IS NULL OR TRIM(cancel_date)='')
                      AND (product_name LIKE ? OR ingredient_name LIKE ? OR manufacturer LIKE ?
                           OR item_seq LIKE ? OR edi_code LIKE ?)
                    ORDER BY CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END, product_name, item_seq
                    LIMIT ?
                    """,
                    (like, like, like, prefix, prefix, prefix, limit),
                ).fetchall()
            dur_codes = self._dur_codes([row["edi_code"] for row in rows if row["edi_code"]])
            for row in rows:
                data = dict(row)
                edi_code = data.pop("edi_code")
                if edi_code:
                    seen_dur_codes.add(edi_code)
                results.append(
                    {
                        "product_ref": data["item_seq"],
                        "catalog_item_seq": data["item_seq"],
                        "product_code": edi_code,
                        "product_name": data["product_name"],
                        "ingredient_code": None,
                        "ingredient_name": data["ingredient_name"],
                        "manufacturer": data["manufacturer"],
                        "dosage_form": data["dosage_form"],
                        "permit_date": data["permit_date"],
                        "catalog_source": data["source"] or "mfds",
                        "dur_match": bool(edi_code and edi_code in dur_codes),
                    }
                )

        remaining = limit - len(results)
        if remaining <= 0:
            return results

        like = f"%{term}%"
        prefix = f"{term}%"
        with self._dur() as con:
            rows = con.execute(
                """
                SELECT product_code, product_name, ingredient_code, ingredient_name
                FROM product_catalog
                WHERE product_name LIKE ? OR ingredient_name LIKE ? OR product_code LIKE ?
                ORDER BY CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END, product_name
                LIMIT ?
                """,
                (like, like, prefix, prefix, remaining + len(seen_dur_codes)),
            ).fetchall()
        for row in rows:
            if row["product_code"] in seen_dur_codes:
                continue
            results.append(
                {
                    "product_ref": row["product_code"],
                    "catalog_item_seq": None,
                    "product_code": row["product_code"],
                    "product_name": row["product_name"],
                    "ingredient_code": row["ingredient_code"],
                    "ingredient_name": row["ingredient_name"],
                    "manufacturer": None,
                    "dosage_form": None,
                    "permit_date": None,
                    "catalog_source": "dur",
                    "dur_match": True,
                }
            )
            if len(results) >= limit:
                break
        return results

    def get(self, product_ref: str) -> dict:
        product_ref = product_ref.strip()
        if not product_ref:
            raise ValueError("product_ref is required")

        if self.has_full_catalog():
            with self._catalog() as con:
                row = con.execute(
                    """
                    SELECT item_seq, product_name, manufacturer, ingredient_name,
                           dosage_form, edi_code, permit_date, cancel_date, source
                    FROM products WHERE item_seq=? OR edi_code=?
                    ORDER BY CASE WHEN item_seq=? THEN 0 ELSE 1 END
                    LIMIT 1
                    """,
                    (product_ref, product_ref, product_ref),
                ).fetchone()
            if row is not None:
                edi_code = row["edi_code"]
                dur_match = bool(edi_code and edi_code in self._dur_codes([edi_code]))
                return {
                    "product_ref": row["item_seq"],
                    "catalog_item_seq": row["item_seq"],
                    "product_code": edi_code,
                    "product_name": row["product_name"],
                    "ingredient_code": None,
                    "ingredient_name": row["ingredient_name"],
                    "manufacturer": row["manufacturer"],
                    "dosage_form": row["dosage_form"],
                    "permit_date": row["permit_date"],
                    "catalog_source": row["source"] or "mfds",
                    "dur_match": dur_match,
                }

        with self._dur() as con:
            row = con.execute(
                """
                SELECT product_code, product_name, ingredient_code, ingredient_name
                FROM product_catalog WHERE product_code=?
                """,
                (product_ref,),
            ).fetchone()
        if row is None:
            raise KeyError("product not found")
        return {
            "product_ref": row["product_code"],
            "catalog_item_seq": None,
            "product_code": row["product_code"],
            "product_name": row["product_name"],
            "ingredient_code": row["ingredient_code"],
            "ingredient_name": row["ingredient_name"],
            "manufacturer": None,
            "dosage_form": None,
            "permit_date": None,
            "catalog_source": "dur",
            "dur_match": True,
        }
