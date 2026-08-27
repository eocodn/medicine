from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .substance_text import normalize_substance_name, split_top_level, text_or_none


@dataclass
class SourceIdentity:
    dataset_key: str
    scope: str
    source_row: int
    ingredient_code: str | None
    name_en: str | None
    name_ko: str | None
    normalized_name: str
    occurrence_count: int


def _aggregate_identity(
    bucket: dict[tuple, SourceIdentity],
    *,
    dataset_key: object,
    scope: str,
    source_row: object,
    occurrence_count: int,
    ingredient_code: object = None,
    name_en: object = None,
    name_ko: object = None,
) -> None:
    english = text_or_none(name_en)
    korean = text_or_none(name_ko)
    normalized = normalize_substance_name(english or korean)
    if not normalized:
        return
    key = (
        str(dataset_key),
        scope,
        text_or_none(ingredient_code),
        english,
        korean,
        normalized,
    )
    row_number = int(source_row or 0)
    existing = bucket.get(key)
    if existing is None:
        bucket[key] = SourceIdentity(
            dataset_key=str(dataset_key),
            scope=scope,
            source_row=row_number,
            ingredient_code=text_or_none(ingredient_code),
            name_en=english,
            name_ko=korean,
            normalized_name=normalized,
            occurrence_count=int(occurrence_count),
        )
        return
    existing.occurrence_count += int(occurrence_count)
    existing.source_row = min(existing.source_row, row_number)


def extract_domestic_identities(
    con: sqlite3.Connection,
    external_names: set[str],
) -> tuple[list[SourceIdentity], list[tuple[str, str, int, str, str]]]:
    con.row_factory = sqlite3.Row
    bucket: dict[tuple, SourceIdentity] = {}

    for scope, prefix in (("dur_rule_primary", ""), ("dur_rule_paired", "paired_")):
        rows = con.execute(
            f"""SELECT source_dataset_key,MIN(source_row) AS first_row,COUNT(*) AS n,
                       {prefix}ingredient_code AS ingredient_code,
                       {prefix}ingredient_name_en AS name_en,
                       {prefix}ingredient_name AS name_ko
                FROM product_rules
                WHERE ({prefix}ingredient_name_en IS NOT NULL AND TRIM({prefix}ingredient_name_en)<>'')
                   OR ({prefix}ingredient_name IS NOT NULL AND TRIM({prefix}ingredient_name)<>'')
                GROUP BY source_dataset_key,{prefix}ingredient_code,
                         {prefix}ingredient_name_en,{prefix}ingredient_name"""
        ).fetchall()
        for row in rows:
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope=scope,
                source_row=row["first_row"],
                occurrence_count=row["n"],
                ingredient_code=row["ingredient_code"],
                name_en=row["name_en"],
                name_ko=row["name_ko"],
            )

    ingredient_rows = con.execute(
        """SELECT source_dataset_key,source_row,ingredient_name,ingredient_name_ko,
                  paired_ingredient_name
           FROM ingredient_rules"""
    ).fetchall()
    for row in ingredient_rows:
        primary = split_top_level(row["ingredient_name"], frozenset({"/", "+"}))
        for component in dict.fromkeys(primary):
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="ingredient_rule_primary",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
                name_ko=row["ingredient_name_ko"] if len(primary) == 1 else None,
            )
        paired = split_top_level(row["paired_ingredient_name"], frozenset({"/", "+"}))
        for component in dict.fromkeys(paired):
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="ingredient_rule_paired",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
            )

    trusted_atomic_names = {item.normalized_name for item in bucket.values()} | external_names
    unparsed: list[tuple[str, str, int, str, str]] = []
    for row in con.execute(
        """SELECT source_dataset_key,source_row,ingredient_text
           FROM products
           WHERE ingredient_text IS NOT NULL AND TRIM(ingredient_text)<>''"""
    ):
        raw_text = str(row["ingredient_text"]).strip()
        parts = split_top_level(raw_text, frozenset({"/"}))
        # Slash is overloaded in MFDS permit text: it separates ingredients, but
        # is also used in ratios and biological strain designations. Split only
        # when every resulting atom is independently known.
        if "/" in raw_text and (
            len(parts) < 2
            or any(normalize_substance_name(part) not in trusted_atomic_names for part in parts)
        ):
            unparsed.append(
                (
                    str(row["source_dataset_key"]),
                    "permit_composition",
                    int(row["source_row"]),
                    raw_text,
                    "ambiguous_composition_delimiter",
                )
            )
            continue
        seen: set[str] = set()
        for component in parts or [raw_text]:
            normalized = normalize_substance_name(component)
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            _aggregate_identity(
                bucket,
                dataset_key=row["source_dataset_key"],
                scope="permit_component",
                source_row=row["source_row"],
                occurrence_count=1,
                name_en=component,
            )

    identities = sorted(
        bucket.values(),
        key=lambda item: (
            item.normalized_name,
            item.dataset_key,
            item.scope,
            item.ingredient_code or "",
            item.name_en or "",
            item.name_ko or "",
        ),
    )
    return identities, unparsed


def representative_name(observations: list[SourceIdentity]) -> str:
    english = sorted(
        {row.name_en for row in observations if row.name_en},
        key=lambda value: (len(value), value.casefold(), value),
    )
    if english:
        return english[0]
    korean = sorted(
        {row.name_ko for row in observations if row.name_ko},
        key=lambda value: (len(value), value),
    )
    if not korean:
        raise RuntimeError("substance identity has no representative source name")
    return korean[0]


__all__ = ["SourceIdentity", "extract_domestic_identities", "representative_name"]