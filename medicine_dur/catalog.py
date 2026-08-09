from __future__ import annotations

import sqlite3


def build_product_catalog(conn: sqlite3.Connection) -> int:
    """Build one canonical searchable product row per normalized DUR product code."""
    conn.execute("DELETE FROM product_catalog")
    conn.execute(
        """
        INSERT INTO product_catalog(product_code, product_name, ingredient_code, ingredient_name)
        WITH candidates AS (
            SELECT
                product_code,
                product_name,
                ingredient_code,
                ingredient_name,
                CASE category
                    WHEN 'age_contraindication' THEN 1
                    WHEN 'pregnancy_contraindication' THEN 2
                    WHEN 'dose_caution' THEN 3
                    WHEN 'duration_caution' THEN 4
                    WHEN 'elderly_caution' THEN 5
                    WHEN 'combination_contraindication' THEN 6
                    WHEN 'therapeutic_duplication_caution' THEN 9
                    ELSE 8
                END AS source_rank
            FROM product_dur
            WHERE product_code IS NOT NULL AND product_name IS NOT NULL
            UNION ALL
            SELECT
                paired_product_code,
                paired_product_name,
                paired_ingredient_code,
                paired_ingredient_name,
                7
            FROM product_dur
            WHERE paired_product_code IS NOT NULL AND paired_product_name IS NOT NULL
        ), ranked AS (
            SELECT
                product_code,
                product_name,
                ingredient_code,
                ingredient_name,
                ROW_NUMBER() OVER (
                    PARTITION BY product_code
                    ORDER BY source_rank, product_name, ingredient_name
                ) AS rn
            FROM candidates
        )
        SELECT product_code, product_name, ingredient_code, ingredient_name
        FROM ranked
        WHERE rn=1
        """
    )
    return conn.execute("SELECT COUNT(*) FROM product_catalog").fetchone()[0]
