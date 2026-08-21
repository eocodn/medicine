from __future__ import annotations

import sqlite3

from medicine_reference.product_search_text import canonical_search_text, character_ngram_document


def rebuild_product_search_index(con: sqlite3.Connection) -> int:
    """Materialize normalized orthographic product text into the trigram FTS index."""
    rows = con.execute(
        "SELECT rowid,item_seq,product_name,ingredient_text,manufacturer FROM products ORDER BY rowid"
    ).fetchall()
    con.execute("DELETE FROM product_search_fts")
    con.execute("DELETE FROM product_search_ocr_fts")
    con.executemany(
        """INSERT INTO product_search_fts(rowid,item_seq,product_name,ingredient_text,manufacturer)
           VALUES(?,?,?,?,?)""",
        (
            (
                row[0],
                row[1],
                canonical_search_text(row[2]),
                canonical_search_text(row[3]),
                canonical_search_text(row[4]),
            )
            for row in rows
        ),
    )
    con.executemany(
        """INSERT INTO product_search_ocr_fts(rowid,item_seq,product_name_bigrams)
           VALUES(?,?,?)""",
        (
            (row[0], row[1], character_ngram_document(row[2], 2))
            for row in rows
        ),
    )
    return len(rows)


__all__ = ["rebuild_product_search_index"]
