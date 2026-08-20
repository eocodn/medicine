from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .canonical_runtime import category_resolution_issues, linked_categories, product_flags
from .dosage_forms import infer_administration_route


class ProductRepository:
    """Read product identity and safety metadata directly from canonical.sqlite.

    ITEM_SEQ is the only runtime product identity. EDI values remain searchable
    identifiers for humans, but never participate in safety classification.
    """

    def __init__(self, canonical_db: Path | str):
        self.canonical_db = Path(canonical_db)

    @contextmanager
    def _canonical(self) -> Iterator[sqlite3.Connection]:
        if not self.canonical_db.exists():
            raise FileNotFoundError(f"canonical database not found: {self.canonical_db}")
        uri = f"file:{self.canonical_db.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        try:
            yield con
        finally:
            con.close()

    def has_full_catalog(self) -> bool:
        try:
            with self._canonical() as con:
                return con.execute("SELECT 1 FROM products LIMIT 1").fetchone() is not None
        except (FileNotFoundError, sqlite3.DatabaseError):
            return False

    @staticmethod
    def _decorate_product(row: sqlite3.Row, con: sqlite3.Connection) -> dict:
        target_item_seq = str(row["item_seq"])
        canonical_dosage_forms = {
            str(value[0]).strip()
            for value in con.execute(
                """SELECT dosage_form FROM product_rules WHERE item_seq=? AND dosage_form IS NOT NULL
                   UNION
                   SELECT dosage_form FROM product_flags WHERE item_seq=? AND dosage_form IS NOT NULL""",
                (target_item_seq, target_item_seq),
            ).fetchall()
            if value[0] and str(value[0]).strip()
        }
        if row["dosage_form"] and str(row["dosage_form"]).strip():
            canonical_dosage_forms.add(str(row["dosage_form"]).strip())
        suggested_route = infer_administration_route(canonical_dosage_forms)
        edi_codes = [
            str(value[0]) for value in con.execute(
                """SELECT value FROM product_identifiers
                   WHERE item_seq=? AND system='EDI' ORDER BY value""",
                (target_item_seq,),
            ).fetchall()
            if value[0]
        ]
        linked = linked_categories(con, target_item_seq)
        issues = category_resolution_issues(con, target_item_seq)
        coverage_status = "partial" if issues else "complete" if linked else "limited"
        return {
            "product_ref": target_item_seq,
            "catalog_item_seq": target_item_seq,
            # Kept as an API/storage alias; the value is now ITEM_SEQ, not EDI/HIRA.
            "product_code": target_item_seq,
            "edi_codes": edi_codes,
            "matched_product_codes": [target_item_seq],
            "product_mapping_status": "matched",
            "product_mapping_method": "item_seq_exact",
            "product_identity_status": "matched",
            "product_identity_method": "item_seq_exact",
            "bridge_product_codes": [],
            "product_flags": product_flags(con, target_item_seq),
            "product_name": row["product_name"],
            "ingredient_code": None,
            "ingredient_name": row["ingredient_text"],
            "safety_ingredients": [],
            "ingredient_mapping_status": "not_required",
            "ingredient_mapping_method": "canonical_applicability",
            "ingredient_mapping_reason": None,
            "unmapped_ingredients": [],
            "manufacturer": row["manufacturer"],
            "dosage_form": row["dosage_form"],
            "canonical_dosage_forms": sorted(canonical_dosage_forms),
            "suggested_administration_route": suggested_route,
            "permit_date": row["permit_date"],
            "cancel_date": row["cancel_date"],
            "permit_status_name": row["cancel_name"],
            "permit_status": row["permit_status"],
            "catalog_source": "canonical",
            "dur_match": bool(linked),
            "dur_coverage_status": coverage_status,
            "canonical_linked_categories": sorted(linked),
            "canonical_resolution_issues": {key: len(value) for key, value in sorted(issues.items())},
        }

    def search(self, term: str, limit: int = 30, include_inactive: bool = False) -> list[dict]:
        term = term.strip()
        if not term:
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        like = f"%{term}%"
        prefix = f"{term}%"
        status_sql = "" if include_inactive else "AND p.permit_status='active'"
        with self._canonical() as con:
            rows = con.execute(
                f"""SELECT p.*
                    FROM products p
                    WHERE (
                        p.product_name LIKE ? OR p.ingredient_text LIKE ? OR p.manufacturer LIKE ?
                        OR p.item_seq LIKE ?
                        OR EXISTS (
                            SELECT 1 FROM product_identifiers i
                            WHERE i.item_seq=p.item_seq AND i.system='EDI' AND i.value LIKE ?
                        )
                    ) {status_sql}
                    ORDER BY CASE WHEN p.permit_status='active' THEN 0 ELSE 1 END,
                             CASE WHEN p.product_name LIKE ? THEN 0 ELSE 1 END,
                             p.product_name,p.item_seq
                    LIMIT ?""",
                (like, like, like, prefix, prefix, prefix, limit),
            ).fetchall()
            return [self._decorate_product(row, con) for row in rows]

    def get(self, product_ref: str) -> dict:
        product_ref = product_ref.strip()
        if not product_ref:
            raise ValueError("product_ref is required")
        with self._canonical() as con:
            return self.get_from_connection(con, product_ref)

    def get_from_connection(self, con: sqlite3.Connection, product_ref: str) -> dict:
        product_ref = product_ref.strip()
        if not product_ref:
            raise ValueError("product_ref is required")
        row = con.execute("SELECT * FROM products WHERE item_seq=?", (product_ref,)).fetchone()
        if row is None:
            raise KeyError("product not found")
        return self._decorate_product(row, con)
