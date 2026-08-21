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
    fuzzy_candidate_fragments,
    match_product_fields,
    parse_product_search_query,
    raw_candidate_variants,
)
from .product_search_candidate_text import text_candidate_anchor_patterns
from .product_search_numeric import raw_numeric_compat_glob


# Candidate SQL is only a bounded superset prefilter. Final matching validates
# every token/strength, so dropping excess predicates can broaden candidates
# but cannot turn a valid match into a miss or overflow SQLite expression depth.
_MAX_CANDIDATE_TEXT_TOKENS = 3
_MAX_CANDIDATE_NUMBERS = 3


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
    def _legacy_search_rows(
        con: sqlite3.Connection,
        term: str,
        limit: int,
        include_inactive: bool,
    ) -> list[sqlite3.Row]:
        like = f"%{term}%"
        prefix = f"{term}%"
        status_sql = "" if include_inactive else "AND p.permit_status='active'"
        return con.execute(
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

    @staticmethod
    def _legacy_match(
        con: sqlite3.Connection,
        row: sqlite3.Row,
        term: str,
    ) -> ProductSearchMatch:
        needle = term.casefold()
        for field, field_rank in (
            ("product_name", 0),
            ("ingredient_text", 1),
            ("manufacturer", 2),
        ):
            value = str(row[field] or "")
            compact = value.casefold()
            position = compact.find(needle)
            if position < 0:
                continue
            prefix = position == 0
            return ProductSearchMatch(
                field=field,
                tier="legacy_prefix" if prefix else "legacy_substring",
                fuzzy=False,
                sort_key=(field_rank, 0, 0 if prefix else 1, max(0, len(compact) - len(needle))),
            )
        if str(row["item_seq"]).casefold().startswith(needle):
            return ProductSearchMatch(
                field="identifier",
                tier="identifier_prefix",
                fuzzy=False,
                sort_key=(-1, 0, 0, 0),
            )
        edi_match = con.execute(
            """SELECT 1 FROM product_identifiers
               WHERE item_seq=? AND system='EDI' AND lower(value) LIKE lower(?) LIMIT 1""",
            (row["item_seq"], f"{term}%"),
        ).fetchone()
        if edi_match is not None:
            return ProductSearchMatch(
                field="identifier",
                tier="identifier_prefix",
                fuzzy=False,
                sort_key=(-1, 0, 0, 0),
            )
        return ProductSearchMatch(
            field="legacy",
            tier="legacy_like",
            fuzzy=False,
            sort_key=(3, 0, 1, 0),
        )

    def _legacy_results(
        self,
        con: sqlite3.Connection,
        term: str,
        limit: int,
        include_inactive: bool,
        *,
        explain: bool,
    ) -> list[dict]:
        rows = self._legacy_search_rows(con, term, limit, include_inactive)
        results = []
        for row in rows:
            decorated = self._decorate_product(row, con)
            if explain:
                decorated["search_match"] = self._legacy_match(con, row, term).explanation()
            results.append(decorated)
        return results

    @staticmethod
    def _number_candidate_patterns(
        number: str,
    ) -> tuple[tuple[str, str], ...]:
        # Candidate SQL is allowed to broaden, never to reject a final match.
        # Very long numeric tokens can exceed SQLite LIKE/GLOB pattern limits;
        # omit that prefilter and let the authoritative matcher validate it.
        if len(number) > 128:
            return ()
        normalized_variants = [number]
        integer, dot, fraction = number.partition(".")
        if integer == "0" and dot and fraction:
            normalized_variants.append(f".{fraction}")
        if integer.isdigit() and len(integer) > 3:
            first_group = len(integer) % 3 or 3
            grouped = ",".join(
                (integer[:first_group], *(
                    integer[index:index + 3]
                    for index in range(first_group, len(integer), 3)
                ))
            )
            if dot:
                grouped = f"{grouped}.{fraction}"
            if grouped not in normalized_variants:
                normalized_variants.append(grouped)
        patterns: list[tuple[str, str]] = []
        for normalized in normalized_variants:
            for raw in raw_candidate_variants(normalized, include_fullwidth=True):
                candidate = ("LIKE", f"%{raw}%")
                if candidate not in patterns:
                    patterns.append(candidate)
            compatibility = raw_numeric_compat_glob(normalized)
            candidate = ("GLOB", compatibility) if compatibility else None
            if candidate and candidate not in patterns:
                patterns.append(candidate)
        return tuple(patterns)

    @staticmethod
    def _structured_candidate_rows(
        con: sqlite3.Connection,
        query: ProductSearchQuery,
        include_inactive: bool,
        *,
        fragments: tuple[str, ...] = (),
    ) -> list[sqlite3.Row]:
        status_sql = "" if include_inactive else "AND p.permit_status='active'"
        if not query.text_tokens:
            return []

        def field_clause(
            field: str,
            text_tokens: tuple[str, ...],
            *,
            fragment_mode: bool,
        ) -> tuple[str, list[object]]:
            raw_text_tokens = tuple(dict.fromkeys(text_tokens))
            text_limit = 6 if fragment_mode else _MAX_CANDIDATE_TEXT_TOKENS
            token_pattern_candidates = [
                (index, token, text_candidate_anchor_patterns(token))
                for index, token in enumerate(raw_text_tokens)
            ]
            fragment_has_unbounded_anchor = bool(
                fragment_mode
                and (
                    len(raw_text_tokens) > text_limit
                    or any(
                        not patterns
                        for _index, _token, patterns in token_pattern_candidates
                    )
                )
            )
            text_token_patterns = tuple(
                (token, patterns)
                for _index, token, patterns in sorted(
                    (candidate for candidate in token_pattern_candidates if candidate[2]),
                    key=lambda item: (-len(item[1]), item[0]),
                )[:text_limit]
            )
            clauses: list[str] = []
            params: list[object] = []
            token_clauses: list[str] = []
            text_params: list[object] = []
            for _token, anchor_groups in text_token_patterns:
                anchor_clauses: list[str] = []
                for patterns in anchor_groups:
                    variant_clauses = []
                    for operator, pattern in patterns:
                        variant_clauses.append(f"p.{field} {operator} ?")
                        text_params.append(pattern)
                    anchor_clauses.append("(" + " OR ".join(variant_clauses) + ")")
                token_clauses.append("(" + " AND ".join(anchor_clauses) + ")")
            if token_clauses and not (fragment_mode and fragment_has_unbounded_anchor):
                text_joiner = " OR " if fragment_mode else " AND "
                clauses.append("(" + text_joiner.join(token_clauses) + ")")
                params.extend(text_params)
            numbers = tuple(dict.fromkeys(query.number_tokens))
            for number in numbers[:_MAX_CANDIDATE_NUMBERS]:
                variant_clauses = []
                for operator, pattern in ProductRepository._number_candidate_patterns(number):
                    variant_clauses.append(f"p.{field} {operator} ?")
                    params.append(pattern)
                if variant_clauses:
                    clauses.append("(" + " OR ".join(variant_clauses) + ")")
            if not clauses:
                return "(1=1)", params
            return "(" + " AND ".join(clauses) + ")", params

        # Final matching is field-local. Candidate SQL uses only bounded safe
        # anchors from that same field, so it can broaden retrieval but never
        # borrow a text qualifier from one field and a strength from another.
        # OCR fragments are an additional product-name-only candidate branch,
        # not a fallback query, so exact and fuzzy candidates share one ranking.
        candidate_parts: list[str] = []
        params: list[object] = []
        for field in ("product_name", "ingredient_text", "manufacturer"):
            clause, clause_params = field_clause(
                field,
                query.text_tokens,
                fragment_mode=False,
            )
            candidate_parts.append(clause)
            params.extend(clause_params)
        if fragments:
            clause, clause_params = field_clause(
                "product_name",
                fragments,
                fragment_mode=True,
            )
            candidate_parts.append(clause)
            params.extend(clause_params)
        candidate_clause = " OR ".join(candidate_parts)
        return con.execute(
            f"""SELECT p.*
                FROM products p
                WHERE ({candidate_clause}) {status_sql}
                """,
            params,
        ).fetchall()

    @staticmethod
    def _identifier_search_rows(
        con: sqlite3.Connection,
        term: str,
        include_inactive: bool,
    ) -> list[sqlite3.Row]:
        prefix = f"{term}%"
        status_sql = "" if include_inactive else "AND p.permit_status='active'"
        return con.execute(
            f"""SELECT p.*
                FROM products p
                WHERE (
                    p.item_seq LIKE ?
                    OR EXISTS (
                        SELECT 1 FROM product_identifiers i
                        WHERE i.item_seq=p.item_seq AND i.system='EDI' AND i.value LIKE ?
                    )
                ) {status_sql}
                ORDER BY CASE WHEN p.permit_status='active' THEN 0 ELSE 1 END,
                         p.product_name,p.item_seq
                LIMIT 1000""",
            (prefix, prefix),
        ).fetchall()

    @staticmethod
    def _rank_structured_rows(
        rows: list[sqlite3.Row],
        query: ProductSearchQuery,
    ) -> list[tuple[tuple[object, ...], sqlite3.Row, object]]:
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
                (
                    0 if row["permit_status"] == "active" else 1,
                    *match.sort_key,
                    str(row["product_name"]),
                    str(row["item_seq"]),
                ),
                row,
                match,
            ))
        ranked.sort(key=lambda item: item[0])
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
        with self._canonical() as con:
            if query.mode == "manual" and not query.structured:
                return self._legacy_results(
                    con, term, limit, include_inactive, explain=explain
                )
            if not query.text_tokens:
                return self._legacy_results(
                    con, term, limit, include_inactive, explain=explain
                )
            identifier_match = ProductSearchMatch(
                field="identifier",
                tier="identifier_prefix",
                fuzzy=False,
                sort_key=(-1, 0, 0, 0),
            )
            identifier_rows = (
                self._identifier_search_rows(con, term, include_inactive)
                if query.identifier_like
                else []
            )
            ranked = [
                (
                    (
                        0 if row["permit_status"] == "active" else 1,
                        *identifier_match.sort_key,
                        str(row["product_name"]),
                        str(row["item_seq"]),
                    ),
                    row,
                    identifier_match,
                )
                for row in identifier_rows
            ]
            rows = self._structured_candidate_rows(con, query, include_inactive)
            ranked.extend(self._rank_structured_rows(rows, query))
            if (
                query.mode == "ocr"
                and not any(
                    match.field == "product_name" and row["permit_status"] == "active"
                    for _key, row, match in ranked
                )
            ):
                fragments = fuzzy_candidate_fragments(query)
                if fragments:
                    rows = self._structured_candidate_rows(
                        con,
                        query,
                        include_inactive,
                        fragments=fragments,
                    )
                    ranked.extend(self._rank_structured_rows(rows, query))
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
