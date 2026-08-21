from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .canonical_runtime import category_resolution_issues, linked_categories, product_flags
from .dosage_forms import infer_administration_route
from .product_search import (
    ProductSearchMatch,
    ProductSearchQuery,
    fts_candidate_terms,
    match_product_fields,
    ocr_candidate_bigrams,
    parse_product_search_query,
)


_FUZZY_CANDIDATE_LIMIT_MANUAL = 240
_FUZZY_CANDIDATE_LIMIT_OCR = 360
_PRODUCT_FETCH_BATCH = 500


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

    @staticmethod
    def _identifier_search_rows(
        con: sqlite3.Connection,
        term: str,
        include_inactive: bool,
    ) -> list[sqlite3.Row]:
        prefix = f"{term}%"
        status_sql = "" if include_inactive else "AND p.permit_status='active'"
        return con.execute(
            f"""SELECT p.*,
                       CASE WHEN lower(p.item_seq)=lower(?) OR EXISTS (
                           SELECT 1 FROM product_identifiers exact_i
                           WHERE exact_i.item_seq=p.item_seq AND exact_i.system='EDI'
                             AND lower(exact_i.value)=lower(?)
                       ) THEN 1 ELSE 0 END AS identifier_exact
                FROM products p
                WHERE (
                    p.item_seq LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM product_identifiers i
                        WHERE i.item_seq=p.item_seq AND i.system='EDI' AND i.value LIKE ?
                    )
                ) {status_sql}
                ORDER BY identifier_exact DESC,
                         CASE WHEN p.permit_status='active' THEN 0 ELSE 1 END,
                         p.product_name,p.item_seq""",
            (term, term, prefix, prefix),
        ).fetchall()

    @staticmethod
    def _fts_candidate_rows(
        con: sqlite3.Connection,
        query: ProductSearchQuery,
        include_inactive: bool,
        *,
        fuzzy_candidate_limit: int,
    ) -> list[sqlite3.Row]:
        """Retrieve candidates without making semantic or field-priority decisions.

        Complete normalized substring hits are always retained. Character-similarity
        candidates are bounded per field, then all candidates are deduplicated and
        passed to the single authoritative Python matcher/ranker.
        """
        def fts_ids(table: str, match_query: str, limit: int | None = None) -> list[str]:
            limit_sql = "" if limit is None else " LIMIT ?"
            params: tuple[object, ...] = (match_query,) if limit is None else (match_query, limit)
            rows = con.execute(
                f"""SELECT item_seq, MIN(rank) AS best_rank
                    FROM {table}
                    WHERE {table} MATCH ?
                    GROUP BY item_seq
                    ORDER BY best_rank, item_seq{limit_sql}""",
                params,
            ).fetchall()
            return [str(row[0]) for row in rows]

        # Exact normalized substring candidates are complete. FTS5 trigram can
        # answer length >= 3 directly; shorter queries scan the same canonical
        # search representation rather than falling back to raw catalog text.
        if len(query.normalized) >= 3:
            item_seq = fts_ids("product_search_fts", f'"{query.normalized}"')
        else:
            like = f"%{query.normalized}%"
            item_seq = [
                str(row[0])
                for row in con.execute(
                    """SELECT item_seq FROM product_search_fts
                       WHERE product_name LIKE ? OR ingredient_text LIKE ? OR manufacturer LIKE ?
                       ORDER BY item_seq""",
                    (like, like, like),
                ).fetchall()
            ]

        # Similarity recall is field-independent: no hit in one field may stop
        # another field from contributing candidates. The cap applies only to
        # fuzzy recall, never to complete exact-substring candidates.
        terms = fts_candidate_terms(query)
        if terms:
            term_query = " OR ".join(f'"{term}"' for term in terms)
            for field in ("product_name", "ingredient_text", "manufacturer"):
                item_seq.extend(
                    fts_ids(
                        "product_search_fts",
                        f"{field} : ({term_query})",
                        fuzzy_candidate_limit,
                    )
                )

        if query.mode == "ocr":
            bigrams = ocr_candidate_bigrams(query)
            if bigrams:
                bigram_query = " OR ".join(f'"{term}"' for term in bigrams)
                item_seq.extend(
                    fts_ids("product_search_ocr_fts", bigram_query, fuzzy_candidate_limit)
                )

        item_seq = list(dict.fromkeys(item_seq))
        if not item_seq:
            return []

        status_sql = "" if include_inactive else "AND permit_status='active'"
        by_id: dict[str, sqlite3.Row] = {}
        for offset in range(0, len(item_seq), _PRODUCT_FETCH_BATCH):
            batch = item_seq[offset:offset + _PRODUCT_FETCH_BATCH]
            placeholders = ",".join("?" for _ in batch)
            for row in con.execute(
                f"SELECT * FROM products WHERE item_seq IN ({placeholders}) {status_sql}",
                batch,
            ).fetchall():
                by_id[str(row["item_seq"])] = row
        return [by_id[value] for value in item_seq if value in by_id]

    @staticmethod
    def _search_sort_key(
        row: sqlite3.Row,
        match: ProductSearchMatch,
    ) -> tuple[object, ...]:
        status_rank = 0 if row["permit_status"] == "active" else 1
        if match.field == "identifier":
            exact_rank = 0 if match.tier == "identifier_exact" else 1
            return (
                0, exact_rank, status_rank,
                str(row["product_name"]), str(row["item_seq"]),
            )
        return (
            1, status_rank, *match.sort_key,
            str(row["product_name"]), str(row["item_seq"]),
        )

    @staticmethod
    def _rank_rows(
        rows: list[sqlite3.Row],
        query: ProductSearchQuery,
    ) -> list[tuple[tuple[object, ...], sqlite3.Row, ProductSearchMatch]]:
        ranked = []
        for row in rows:
            match = match_product_fields(
                query,
                product_name=row["product_name"],
                ingredient_text=row["ingredient_text"],
                manufacturer=row["manufacturer"],
            )
            if match is None:
                continue
            ranked.append((
                ProductRepository._search_sort_key(row, match),
                row,
                match,
            ))
        return ranked

    def search(
        self,
        term: str,
        limit: int = 30,
        include_inactive: bool = False,
        *,
        mode: str = "manual",
        explain: bool = False,
    ) -> list[dict]:
        term = term.strip()
        if not term:
            return []
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        query = parse_product_search_query(term, mode=mode)
        if not query.normalized:
            return []

        with self._canonical() as con:
            identifier_rows = (
                self._identifier_search_rows(con, term, include_inactive)
                if query.identifier_like
                else []
            )
            ranked = []
            for row in identifier_rows:
                exact = bool(row["identifier_exact"])
                identifier_match = ProductSearchMatch(
                    field="identifier",
                    tier="identifier_exact" if exact else "identifier_prefix",
                    fuzzy=False,
                    similarity=1.0,
                    sort_key=(-1, 0 if exact else 1, 0, 0, 0),
                )
                ranked.append((
                    self._search_sort_key(row, identifier_match),
                    row,
                    identifier_match,
                ))
            fuzzy_candidate_limit = (
                _FUZZY_CANDIDATE_LIMIT_OCR if query.mode == "ocr"
                else _FUZZY_CANDIDATE_LIMIT_MANUAL
            )
            ranked.extend(self._rank_rows(
                self._fts_candidate_rows(
                    con, query, include_inactive, fuzzy_candidate_limit=fuzzy_candidate_limit
                ),
                query,
            ))
            ranked.sort(key=lambda item: item[0])

            results = []
            seen_item_seq: set[str] = set()
            for _sort_key, row, match in ranked:
                item_seq = str(row["item_seq"])
                if item_seq in seen_item_seq:
                    continue
                seen_item_seq.add(item_seq)
                decorated = self._decorate_product(row, con)
                if explain:
                    decorated["search_match"] = match.explanation()
                results.append(decorated)
                if len(results) >= limit:
                    break
            return results

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
