from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from contextlib import closing
from pathlib import Path

from .substance_text import normalize_substance_name, split_top_level


PRODUCT_SEARCH_DOCUMENT_DDL = """
CREATE TABLE product_search_documents (
    item_seq TEXT PRIMARY KEY REFERENCES products(item_seq),
    normalized_product_name TEXT NOT NULL,
    normalized_manufacturer TEXT NOT NULL,
    normalized_ingredient_names TEXT NOT NULL
);
"""
PRODUCT_SEARCH_FTS_DDL = """
CREATE VIRTUAL TABLE product_search_fts USING fts5(
    searchable_text,
    tokenize='trigram',
    content=''
);
"""


def normalize_search_text(value: object) -> str:
    """Normalize lexical search text without inferring medication semantics."""
    text = unicodedata.normalize("NFKC", str(value or "")).strip().lower()
    return re.sub(r"\s+", " ", text)


def _delimited(values: set[str]) -> str:
    normalized = sorted(filter(None, (normalize_search_text(value) for value in values)))
    return "\n" + "\n".join(normalized) + "\n" if normalized else "\n"


def materialize_product_search_fts(con: sqlite3.Connection) -> int:
    """Rebuild the contentless trigram accelerator from authoritative documents."""
    con.execute("DROP TABLE IF EXISTS product_search_fts")
    con.execute(PRODUCT_SEARCH_FTS_DDL)
    con.execute(
        """INSERT INTO product_search_fts(rowid,searchable_text)
           SELECT rowid,
                  normalized_product_name || char(10) || normalized_manufacturer ||
                  normalized_ingredient_names
           FROM product_search_documents
           ORDER BY rowid"""
    )
    return int(con.execute("SELECT COUNT(*) FROM product_search_fts").fetchone()[0])


def _load_substance_aliases(
    substance_db_path: str | Path | None,
) -> tuple[dict[str, str], dict[str, set[str]]]:
    if substance_db_path is None:
        return {}, {}
    path = Path(substance_db_path)
    if not path.is_file():
        raise FileNotFoundError(f"canonical substance database not found: {path}")
    uri = f"file:{path.resolve()}?mode=ro"
    with closing(sqlite3.connect(uri, uri=True)) as con:
        name_to_substance = {
            str(normalized_name): str(substance_id)
            for normalized_name, substance_id in con.execute(
                "SELECT normalized_name,substance_id FROM substance_names"
            )
        }
        aliases: dict[str, set[str]] = defaultdict(set)
        for substance_id, name_en, name_ko in con.execute(
            "SELECT substance_id,name_en,name_ko FROM source_identities"
        ):
            for value in (name_en, name_ko):
                text = str(value or "").strip()
                if text:
                    aliases[str(substance_id)].add(text)
    return name_to_substance, aliases


def _direct_substance_ids(
    ingredient_text: object,
    name_to_substance: dict[str, str],
) -> set[str]:
    raw = str(ingredient_text or "").strip()
    if not raw or not name_to_substance:
        return set()
    normalized = normalize_substance_name(raw)
    if normalized in name_to_substance:
        return {name_to_substance[normalized]}

    parts = split_top_level(raw, frozenset({"/"}))
    if "/" not in raw or len(parts) < 2:
        return set()
    resolved: set[str] = set()
    for part in parts:
        substance_id = name_to_substance.get(normalize_substance_name(part))
        if substance_id is None:
            return set()
        resolved.add(substance_id)
    return resolved


def materialize_product_search_documents(
    con: sqlite3.Connection,
    substance_db_path: str | Path | None,
) -> dict[str, int]:
    """Build one lexical search document per product.

    Substance aliases are admitted only through an exact permit-component name
    already present in ``substance_names``. Relations between distinct
    substances are intentionally ignored here.
    """
    con.execute(PRODUCT_SEARCH_DOCUMENT_DDL.replace("CREATE TABLE ", "CREATE TABLE IF NOT EXISTS ", 1))
    con.execute("DELETE FROM product_search_documents")
    name_to_substance, aliases_by_substance = _load_substance_aliases(substance_db_path)

    rows = con.execute(
        "SELECT item_seq,product_name,manufacturer,ingredient_text FROM products ORDER BY item_seq"
    ).fetchall()
    alias_links = 0
    for item_seq, product_name, manufacturer, ingredient_text in rows:
        ingredient_aliases = {str(ingredient_text or "").strip()} if str(ingredient_text or "").strip() else set()
        for substance_id in sorted(_direct_substance_ids(ingredient_text, name_to_substance)):
            direct_aliases = aliases_by_substance.get(substance_id, set())
            ingredient_aliases.update(direct_aliases)
            alias_links += len(direct_aliases)
        con.execute(
            """INSERT INTO product_search_documents(
                   item_seq,normalized_product_name,normalized_manufacturer,
                   normalized_ingredient_names
               ) VALUES(?,?,?,?)""",
            (
                str(item_seq),
                normalize_search_text(product_name),
                normalize_search_text(manufacturer),
                _delimited(ingredient_aliases),
            ),
        )
    index_rows = materialize_product_search_fts(con)
    return {
        "documents": len(rows),
        "index_rows": index_rows,
        "direct_alias_links": alias_links,
    }


__all__ = [
    "PRODUCT_SEARCH_DOCUMENT_DDL",
    "PRODUCT_SEARCH_FTS_DDL",
    "materialize_product_search_documents",
    "materialize_product_search_fts",
    "normalize_search_text",
]