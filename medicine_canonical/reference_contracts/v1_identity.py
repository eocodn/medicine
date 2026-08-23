from __future__ import annotations

import hashlib
import json
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager


# Contract-v1 identity is frozen.  The oracle SQL below is the specification;
# the physical-policy-v8 executor may change execution strategy only when it
# emits the exact same ordered logical row transcript.
REFERENCE_CONTRACT_MAJOR = 1
Progress = Callable[[dict[str, object]], None]


_LOGICAL_PROJECTIONS: tuple[tuple[str, str], ...] = (
    (
        "products",
        """SELECT item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                  permit_date,cancel_date,cancel_name,permit_status
           FROM products
           ORDER BY item_seq,product_name,manufacturer,ingredient_text,dosage_form,
                    permit_date,cancel_date,cancel_name,permit_status""",
    ),
    (
        "product_identifiers",
        """SELECT item_seq,system,value FROM product_identifiers
           ORDER BY item_seq,system,value""",
    ),
    (
        "product_flags",
        """SELECT item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,details,change_date
           FROM product_flags
           ORDER BY item_seq,category,flag_code,flag_name,ingredient_name,dosage_form,details,change_date""",
    ),
    (
        "product_rules",
        """SELECT category,item_seq,ingredient_code,ingredient_name,ingredient_name_en,
                  paired_item_seq,paired_ingredient_code,paired_ingredient_name,
                  paired_ingredient_name_en,effect_name,dosage_form,details,
                  notification_date,change_date
           FROM product_rules
           ORDER BY category,item_seq,ingredient_code,ingredient_name,ingredient_name_en,
                    paired_item_seq,paired_ingredient_code,paired_ingredient_name,
                    paired_ingredient_name_en,effect_name,dosage_form,details,
                    notification_date,change_date""",
    ),
    (
        "ingredient_rules",
        """SELECT category,sequence_text,ingredient_name,ingredient_name_ko,
                  paired_ingredient_name,rule_value,dosage_form,note,details
           FROM ingredient_rules
           ORDER BY category,sequence_text,ingredient_name,ingredient_name_ko,
                    paired_ingredient_name,rule_value,dosage_form,note,details""",
    ),
    (
        "dose_criteria",
        """SELECT i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason
           FROM dose_criteria d JOIN ingredient_rules i ON i.id=d.criterion_rule_id
           ORDER BY i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                    i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                    d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason""",
    ),
    (
        "product_criterion_links",
        """SELECT
                  p.category,p.item_seq,p.ingredient_code,p.ingredient_name,p.ingredient_name_en,
                  p.paired_item_seq,p.paired_ingredient_code,p.paired_ingredient_name,
                  p.paired_ingredient_name_en,p.effect_name,p.dosage_form,p.details,
                  p.notification_date,p.change_date,
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  l.match_method,l.pair_orientation
           FROM product_criterion_links l
           JOIN product_rules p ON p.id=l.product_rule_id
           JOIN ingredient_rules i ON i.id=l.criterion_rule_id
           ORDER BY
                  p.category,p.item_seq,p.ingredient_code,p.ingredient_name,p.ingredient_name_en,
                  p.paired_item_seq,p.paired_ingredient_code,p.paired_ingredient_name,
                  p.paired_ingredient_name_en,p.effect_name,p.dosage_form,p.details,
                  p.notification_date,p.change_date,
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  l.match_method,l.pair_orientation""",
    ),
    (
        "runtime_product_rule_criteria",
        """SELECT
                  p.category,p.item_seq,p.ingredient_name,p.paired_item_seq,
                  p.paired_ingredient_name,p.effect_name,p.product_dosage_form,p.product_details,
                  p.criterion_sequence_text,p.criterion_ingredient_name,p.criterion_ingredient_name_ko,
                  p.criterion_paired_ingredient_name,p.criterion_rule_value,p.criterion_dosage_form,
                  p.criterion_note,p.criterion_details,
                  p.criterion_maximum_daily_amount,p.criterion_maximum_daily_unit,
                  p.criterion_dose_parse_status,p.criterion_dose_parse_reason,
                  p.match_method,p.pair_orientation,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,s.fallback_action,
                  s.qualifier_type,s.display_text,s.structured_payload_json
           FROM product_rule_criteria p
           LEFT JOIN reference_criterion_semantics s
             ON s.criterion_rule_id=p.criterion_rule_id
           ORDER BY
                  p.category,p.item_seq,p.ingredient_name,p.paired_item_seq,
                  p.paired_ingredient_name,p.effect_name,p.product_dosage_form,p.product_details,
                  p.criterion_sequence_text,p.criterion_ingredient_name,p.criterion_ingredient_name_ko,
                  p.criterion_paired_ingredient_name,p.criterion_rule_value,p.criterion_dosage_form,
                  p.criterion_note,p.criterion_details,
                  p.criterion_maximum_daily_amount,p.criterion_maximum_daily_unit,
                  p.criterion_dose_parse_status,p.criterion_dose_parse_reason,
                  p.match_method,p.pair_orientation,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,s.fallback_action,
                  s.qualifier_type,s.display_text,s.structured_payload_json""",
    ),
    (
        "criterion_semantics",
        """SELECT
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,
                  s.fallback_action,s.qualifier_type,s.display_text,s.structured_payload_json
           FROM reference_criterion_semantics s
           JOIN ingredient_rules i ON i.id=s.criterion_rule_id
           ORDER BY
                  i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
                  i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
                  s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,
                  s.fallback_action,s.qualifier_type,s.display_text,s.structured_payload_json""",
    ),
)


_MATCH_METHOD_FROM_CODE = {
    0: "mfds_ingredient_code",
    1: "permit_composition",
    2: "mfds_details_exact",
    3: "mfds_unanimous_value",
}
_PAIR_ORIENTATION_FROM_CODE = {0: "forward", 1: "reverse"}


def _notify(progress: Progress | None, **event: object) -> None:
    if progress is not None:
        progress(event)


@contextmanager
def _sqlite_heartbeat(
    database: sqlite3.Connection,
    progress: Progress | None,
    *,
    phase: str,
    section: str | None = None,
):
    if progress is None:
        yield
        return
    last = time.monotonic()

    def heartbeat() -> int:
        nonlocal last
        now = time.monotonic()
        if now - last >= 10:
            event: dict[str, object] = {"phase": phase, "status": "heartbeat"}
            if section is not None:
                event["section"] = section
            _notify(progress, **event)
            last = now
        return 0

    database.set_progress_handler(heartbeat, 100_000)
    try:
        yield
    finally:
        database.set_progress_handler(None, 0)


def _hash_rows(
    digest: "hashlib._Hash",
    label: str,
    rows: Iterator[tuple] | sqlite3.Cursor,
    *,
    progress: Progress | None,
) -> None:
    digest.update(f"section\0{label}\n".encode("utf-8"))
    _notify(progress, phase="logical_identity", section=label, status="started")
    count = 0
    for count, row in enumerate(rows, start=1):
        encoded = json.dumps(
            list(row),
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        digest.update(encoded)
        digest.update(b"\n")
        if count % 100_000 == 0:
            _notify(
                progress,
                phase="logical_identity",
                section=label,
                status="progress",
                rows=count,
            )
    _notify(
        progress,
        phase="logical_identity",
        section=label,
        status="completed",
        rows=count,
    )


def logical_dataset_id_oracle(
    database: sqlite3.Connection,
    *,
    progress: Progress | None = None,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"reference-contract\0{REFERENCE_CONTRACT_MAJOR}\n".encode("utf-8"))
    for label, query in _LOGICAL_PROJECTIONS:
        with _sqlite_heartbeat(
            database,
            progress,
            phase="logical_identity",
            section=label,
        ):
            _hash_rows(digest, label, database.execute(query), progress=progress)
    return f"sha256:{digest.hexdigest()}"


def _fast_layout_is_valid(database: sqlite3.Connection) -> bool:
    required = {
        "mobile_product_rules",
        "mobile_product_criterion_links",
        "mobile_rule_texts",
    }
    names = {
        str(row[0])
        for row in database.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name IN (?,?,?)",
            tuple(sorted(required)),
        )
    }
    if names != required:
        return False
    # Compact mobile layouts assign dictionary IDs in SQLite BINARY text order.
    # Verify that invariant directly before using integer IDs as exact text sort
    # keys instead of coupling the executor to a physical-policy version number.
    mismatch = database.execute(
        """SELECT 1 FROM (
               SELECT id,ROW_NUMBER() OVER (ORDER BY value) AS expected_id
               FROM mobile_rule_texts
           ) WHERE id<>expected_id LIMIT 1"""
    ).fetchone()
    return mismatch is None


def _text_dictionary(database: sqlite3.Connection) -> dict[int, str]:
    return {int(row[0]): str(row[1]) for row in database.execute("SELECT id,value FROM mobile_rule_texts")}


def _decode(texts: dict[int, str], value: int | None) -> str | None:
    return None if value is None else texts[int(value)]


# Valid compact layouts insert mobile_rule_texts in SQLite BINARY order, so each
# non-NULL dictionary ID is already the exact sort rank of its decoded text.
# Keep the fast executor read-only: logical_dataset_id() is valid inside caller-owned
# transactions and on PRAGMA query_only connections, so rank TEMP tables (and
# especially executescript(), which commits pending work) are forbidden here.
def _fast_product_rules(database: sqlite3.Connection, texts: dict[int, str]) -> Iterator[tuple]:
    rows = database.execute(
        """SELECT
               r.category_text_id,r.item_seq,r.ingredient_code_text_id,r.ingredient_name_text_id,
               r.ingredient_name_en_text_id,r.paired_item_seq,r.paired_ingredient_code_text_id,
               r.paired_ingredient_name_text_id,r.paired_ingredient_name_en_text_id,
               r.effect_name_text_id,r.dosage_form_text_id,r.details_text_id,
               r.notification_date_text_id,r.change_date_text_id
           FROM mobile_product_rules r
           ORDER BY
               r.category_text_id,r.item_seq,r.ingredient_code_text_id,r.ingredient_name_text_id,
               r.ingredient_name_en_text_id,r.paired_item_seq,r.paired_ingredient_code_text_id,
               r.paired_ingredient_name_text_id,r.paired_ingredient_name_en_text_id,
               r.effect_name_text_id,r.dosage_form_text_id,r.details_text_id,
               r.notification_date_text_id,r.change_date_text_id"""
    )
    for row in rows:
        yield (
            _decode(texts, row[0]), row[1], _decode(texts, row[2]), _decode(texts, row[3]),
            _decode(texts, row[4]), row[5], _decode(texts, row[6]), _decode(texts, row[7]),
            _decode(texts, row[8]), _decode(texts, row[9]), _decode(texts, row[10]),
            _decode(texts, row[11]), _decode(texts, row[12]), _decode(texts, row[13]),
        )


def _fast_product_criterion_links(
    database: sqlite3.Connection,
    texts: dict[int, str],
) -> Iterator[tuple]:
    rows = database.execute(
        """SELECT
               p.category_text_id,p.item_seq,p.ingredient_code_text_id,p.ingredient_name_text_id,
               p.ingredient_name_en_text_id,p.paired_item_seq,p.paired_ingredient_code_text_id,
               p.paired_ingredient_name_text_id,p.paired_ingredient_name_en_text_id,
               p.effect_name_text_id,p.dosage_form_text_id,p.details_text_id,
               p.notification_date_text_id,p.change_date_text_id,
               i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
               i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
               l.match_method_code,l.pair_orientation_code
           FROM mobile_product_criterion_links l
           JOIN mobile_product_rules p ON p.id=l.product_rule_id
           JOIN ingredient_rules i ON i.id=l.criterion_rule_id
           ORDER BY
               p.category_text_id,p.item_seq,p.ingredient_code_text_id,p.ingredient_name_text_id,
               p.ingredient_name_en_text_id,p.paired_item_seq,p.paired_ingredient_code_text_id,
               p.paired_ingredient_name_text_id,p.paired_ingredient_name_en_text_id,
               p.effect_name_text_id,p.dosage_form_text_id,p.details_text_id,
               p.notification_date_text_id,p.change_date_text_id,
               i.category,i.sequence_text,i.ingredient_name,i.ingredient_name_ko,
               i.paired_ingredient_name,i.rule_value,i.dosage_form,i.note,i.details,
               CASE l.match_method_code WHEN 2 THEN 0 WHEN 0 THEN 1 WHEN 3 THEN 2 WHEN 1 THEN 3 END,
               l.pair_orientation_code"""
    )
    for row in rows:
        yield (
            _decode(texts, row[0]), row[1], _decode(texts, row[2]), _decode(texts, row[3]),
            _decode(texts, row[4]), row[5], _decode(texts, row[6]), _decode(texts, row[7]),
            _decode(texts, row[8]), _decode(texts, row[9]), _decode(texts, row[10]),
            _decode(texts, row[11]), _decode(texts, row[12]), _decode(texts, row[13]),
            *row[14:23],
            _MATCH_METHOD_FROM_CODE[int(row[23])],
            None if row[24] is None else _PAIR_ORIENTATION_FROM_CODE[int(row[24])],
        )


def _fast_runtime_product_rule_criteria(
    database: sqlite3.Connection,
    texts: dict[int, str],
) -> Iterator[tuple]:
    rows = database.execute(
        """SELECT
               p.category_text_id,p.item_seq,p.ingredient_name_text_id,p.paired_item_seq,
               p.paired_ingredient_name_text_id,p.effect_name_text_id,p.dosage_form_text_id,
               p.details_text_id,
               i.sequence_text,i.ingredient_name,i.ingredient_name_ko,i.paired_ingredient_name,
               i.rule_value,i.dosage_form,i.note,i.details,
               d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason,
               l.match_method_code,l.pair_orientation_code,
               s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,s.fallback_action,
               s.qualifier_type,s.display_text,s.structured_payload_json
           FROM mobile_product_criterion_links l
           JOIN mobile_product_rules p ON p.id=l.product_rule_id
           JOIN ingredient_rules i ON i.id=l.criterion_rule_id
           LEFT JOIN dose_criteria d ON d.criterion_rule_id=i.id
           LEFT JOIN reference_criterion_semantics s ON s.criterion_rule_id=i.id
           ORDER BY
               p.category_text_id,p.item_seq,p.ingredient_name_text_id,p.paired_item_seq,
               p.paired_ingredient_name_text_id,p.effect_name_text_id,p.dosage_form_text_id,
               p.details_text_id,
               i.sequence_text,i.ingredient_name,i.ingredient_name_ko,i.paired_ingredient_name,
               i.rule_value,i.dosage_form,i.note,i.details,
               d.maximum_daily_amount,d.maximum_daily_unit,d.parse_status,d.parse_reason,
               CASE l.match_method_code WHEN 2 THEN 0 WHEN 0 THEN 1 WHEN 3 THEN 2 WHEN 1 THEN 3 END,
               l.pair_orientation_code,
               s.ordinal,s.semantic_role,s.evaluation_mode,s.evaluator_kind,s.fallback_action,
               s.qualifier_type,s.display_text,s.structured_payload_json"""
    )
    for row in rows:
        yield (
            _decode(texts, row[0]), row[1], _decode(texts, row[2]), row[3],
            _decode(texts, row[4]), _decode(texts, row[5]), _decode(texts, row[6]),
            _decode(texts, row[7]),
            *row[8:20],
            _MATCH_METHOD_FROM_CODE[int(row[20])],
            None if row[21] is None else _PAIR_ORIENTATION_FROM_CODE[int(row[21])],
            *row[22:30],
        )


def logical_dataset_id_fast(
    database: sqlite3.Connection,
    *,
    progress: Progress | None = None,
) -> str:
    _notify(progress, phase="logical_identity_fast_setup", status="started")
    texts = _text_dictionary(database)
    _notify(progress, phase="logical_identity_fast_setup", status="completed")

    digest = hashlib.sha256()
    digest.update(f"reference-contract\0{REFERENCE_CONTRACT_MAJOR}\n".encode("utf-8"))
    for label, query in _LOGICAL_PROJECTIONS:
        if label == "product_rules":
            rows = _fast_product_rules(database, texts)
        elif label == "product_criterion_links":
            rows = _fast_product_criterion_links(database, texts)
        elif label == "runtime_product_rule_criteria":
            rows = _fast_runtime_product_rule_criteria(database, texts)
        else:
            rows = database.execute(query)
        with _sqlite_heartbeat(
            database,
            progress,
            phase="logical_identity",
            section=label,
        ):
            _hash_rows(digest, label, rows, progress=progress)
    return f"sha256:{digest.hexdigest()}"


def logical_dataset_id(
    database: sqlite3.Connection,
    *,
    physical_policy_version: str | None = None,
    progress: Progress | None = None,
) -> str:
    # physical_policy_version remains part of the exporter/build diagnostic API,
    # but executor safety is a property of the database layout itself. A new
    # policy may add unrelated physical objects without invalidating this layout.
    _ = physical_policy_version
    if _fast_layout_is_valid(database):
        return logical_dataset_id_fast(database, progress=progress)
    return logical_dataset_id_oracle(database, progress=progress)


__all__ = [
    "logical_dataset_id",
    "logical_dataset_id_fast",
    "logical_dataset_id_oracle",
]
