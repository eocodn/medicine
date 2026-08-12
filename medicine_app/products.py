from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from medicine_dur.verification import dataset_manifest

from .coverage import ingredient_index, resolve_safety_mapping
from .ingredient_aliases import (
    load_materialized_ingredient_aliases,
    load_materialized_multi_ingredient_aliases,
)


class ProductRepository:
    def __init__(self, dur_db: Path | str, catalog_db: Path | str | None = None):
        self.dur_db = Path(dur_db)
        self.catalog_db = Path(catalog_db) if catalog_db else None
        self._ingredient_aliases = self._load_ingredient_aliases()
        self._ingredient_multi_aliases = self._load_ingredient_multi_aliases()

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

    def _load_ingredient_aliases(self) -> dict[str, str]:
        if not self.catalog_db or not self.catalog_db.exists() or not self.dur_db.exists():
            return {}
        with self._dur() as dur_con, self._catalog() as catalog_con:
            dataset_id = dataset_manifest(dur_con).get("dataset_id")
            return load_materialized_ingredient_aliases(
                catalog_con, dur_dataset_id=dataset_id
            )

    def _load_ingredient_multi_aliases(self) -> dict[str, tuple[str, ...]]:
        if not self.catalog_db or not self.catalog_db.exists() or not self.dur_db.exists():
            return {}
        with self._dur() as dur_con, self._catalog() as catalog_con:
            dataset_id = dataset_manifest(dur_con).get("dataset_id")
            return load_materialized_multi_ingredient_aliases(
                catalog_con, dur_dataset_id=dataset_id
            )

    def has_full_catalog(self) -> bool:
        if not self.catalog_db or not self.catalog_db.exists():
            return False
        try:
            with self._catalog() as con:
                return con.execute("SELECT 1 FROM products LIMIT 1").fetchone() is not None
        except sqlite3.DatabaseError:
            return False

    @staticmethod
    def _decorate_product(
        row: sqlite3.Row,
        dur_con: sqlite3.Connection,
        known_ingredients: set[str] | None = None,
        ingredient_aliases: dict[str, str] | None = None,
        ingredient_multi_aliases: dict[str, tuple[str, ...]] | None = None,
    ) -> dict:
        mapping = resolve_safety_mapping(
            dur_con,
            catalog_item_seq=row["item_seq"],
            edi_value=row["edi_code"],
            catalog_product_name=row["product_name"],
            catalog_ingredient=row["ingredient_name"],
            known_ingredients=known_ingredients,
            ingredient_aliases=ingredient_aliases,
            ingredient_multi_aliases=ingredient_multi_aliases,
        )
        return {
            "product_ref": row["item_seq"],
            "catalog_item_seq": row["item_seq"],
            "product_code": mapping["product_code"],
            "edi_codes": mapping["edi_codes"],
            "matched_product_codes": mapping["matched_product_codes"],
            "product_mapping_status": mapping["product_status"],
            "product_mapping_method": mapping["product_mapping_method"],
            "product_identity_status": mapping["product_identity_status"],
            "product_identity_method": mapping["product_identity_method"],
            "product_flags": mapping["product_flags"],
            "product_name": row["product_name"],
            "ingredient_code": mapping["ingredient_code"],
            "ingredient_name": row["ingredient_name"],
            "safety_ingredients": mapping["ingredients"],
            "ingredient_mapping_status": mapping["ingredient_status"],
            "ingredient_mapping_method": mapping["ingredient_mapping_method"],
            "ingredient_mapping_reason": mapping["ingredient_reason"],
            "unmapped_ingredients": mapping["unmapped_ingredients"],
            "manufacturer": row["manufacturer"],
            "dosage_form": row["dosage_form"],
            "permit_date": row["permit_date"],
            "cancel_date": row["cancel_date"],
            "permit_status_name": row["cancel_name"],
            "permit_status": row["permit_status"],
            "catalog_source": row["source"] or "mfds",
            "dur_match": mapping["product_status"] == "matched",
        }

    def search(self, term: str, limit: int = 30, include_inactive: bool = False) -> list[dict]:
        term = term.strip()
        if not term:
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")

        like = f"%{term}%"
        prefix = f"{term}%"
        permit_filter = "" if include_inactive else "AND permit_status='active'"
        with self._catalog() as con:
            rows = con.execute(
                f"""
                SELECT item_seq, product_name, manufacturer, ingredient_name,
                       dosage_form, edi_code, permit_date, cancel_date,
                       cancel_name, permit_status, source
                FROM products
                WHERE (product_name LIKE ? OR ingredient_name LIKE ? OR manufacturer LIKE ?
                       OR item_seq LIKE ? OR edi_code LIKE ?)
                  {permit_filter}
                ORDER BY CASE WHEN permit_status='active' THEN 0 ELSE 1 END,
                         CASE WHEN product_name LIKE ? THEN 0 ELSE 1 END,
                         product_name, item_seq
                LIMIT ?
                """,
                (like, like, like, prefix, prefix, prefix, limit),
            ).fetchall()
        with self._dur() as dur_con:
            known_ingredients = ingredient_index(dur_con)
            return [
                self._decorate_product(
                    row, dur_con, known_ingredients, self._ingredient_aliases,
                    self._ingredient_multi_aliases
                )
                for row in rows
            ]

    def get(self, product_ref: str) -> dict:
        product_ref = product_ref.strip()
        if not product_ref:
            raise ValueError("product_ref is required")

        with self._catalog() as con:
            row = con.execute(
                """
                SELECT item_seq, product_name, manufacturer, ingredient_name,
                       dosage_form, edi_code, permit_date, cancel_date,
                       cancel_name, permit_status, source
                FROM products WHERE item_seq=? OR edi_code=?
                ORDER BY CASE WHEN item_seq=? THEN 0 ELSE 1 END
                LIMIT 1
                """,
                (product_ref, product_ref, product_ref),
            ).fetchone()
        if row is None:
            raise KeyError("product not found")
        with self._dur() as dur_con:
            return self._decorate_product(
                row, dur_con, ingredient_index(dur_con), self._ingredient_aliases,
                self._ingredient_multi_aliases
            )
