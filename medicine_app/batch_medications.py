"""Atomic, review-bound creation of multiple medication rows.

This module is deliberately separate from ``core.py``: multi-row OCR has a different
state boundary from a single medication create.  Every row is normalized before any
write, all rows are assessed against both stored medications and their batch peers,
and the transaction commits all rows or none.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from typing import Any, Mapping

from .assessment import assess_medication, bind_warning_token, requires_acknowledgement
from .core import ConfirmationRequired, IdempotencyConflict
from .prescriptions import draft_hash, normalize_draft


_MAX_BATCH_ROWS = 24
_ROW_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_BATCH_SUBJECT = "__ocr_medication_batch__"
_BATCH_ROW_FIELDS = {
    "row_id", "product_ref", "product_code", "dosage_text", "dose_amount", "dose_unit",
    "frequency_per_day", "meal_relation", "administration_route", "as_needed",
    "prescription_days", "schedule_times", "start_date", "end_date",
}


def _normalize_rows(app: Any, rows: Any) -> list[dict[str, Any]]:
    if not isinstance(rows, list) or not 1 <= len(rows) <= _MAX_BATCH_ROWS:
        raise ValueError(f"rows must contain between 1 and {_MAX_BATCH_ROWS} medications")
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for index, value in enumerate(rows):
        if not isinstance(value, Mapping):
            raise ValueError(f"batch row {index + 1} must be an object")
        unknown = sorted(set(value) - _BATCH_ROW_FIELDS)
        if unknown:
            raise ValueError(f"unsupported batch row fields: {', '.join(unknown)}")
        row_id = str(value.get("row_id") or "").strip()
        if not _ROW_ID_RE.fullmatch(row_id):
            raise ValueError("row_id must use 1-64 ASCII letters, digits, '_' or '-'")
        if row_id in seen_ids:
            raise ValueError("row_id must be unique within a batch")
        seen_ids.add(row_id)
        product_ref = str(value.get("product_ref") or value.get("product_code") or "").strip()
        if not product_ref:
            raise ValueError(f"product_ref is required for row {row_id}")
        product = app.get_product(product_ref)
        if product.get("permit_status") != "active":
            raise ValueError(f"an active catalog product is required for row {row_id}")
        draft_values = {key: item for key, item in value.items() if key not in {"row_id", "product_ref", "product_code"}}
        try:
            draft = normalize_draft(draft_values)
        except ValueError as exc:
            raise ValueError(f"row {row_id}: {exc}") from exc
        normalized.append({
            "row_id": row_id,
            "product": {**product, "med_source": "ocr"},
            "draft": draft,
            "payload_hash": draft_hash("__pending_person__", product, draft),
        })
    return normalized


def _bind_person(rows: list[dict[str, Any]], person_id: str) -> None:
    for row in rows:
        row["payload_hash"] = draft_hash(person_id, row["product"], row["draft"])


def _batch_fingerprint(person_id: str, rows: list[dict[str, Any]]) -> str:
    payload = {
        "person_id": person_id,
        "rows": [
            {
                "row_id": row["row_id"],
                "product_ref": row["product"].get("product_ref"),
                "draft": row["draft"],
            }
            for row in rows
        ],
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _peer(row: Mapping[str, Any]) -> dict[str, Any]:
    product = dict(row["product"])
    draft = dict(row["draft"])
    return {
        **product,
        **draft,
        "id": f"batch:{row['row_id']}",
        "catalog_item_seq": product.get("catalog_item_seq") or product.get("product_ref"),
        "product_code": product.get("product_code") or product.get("product_ref"),
        "product_name": product.get("product_name"),
        "active": True,
        "source": "ocr",
    }


def _assess_rows(app: Any, con: sqlite3.Connection, person: dict, rows: list[dict[str, Any]], acknowledged: bool) -> list[dict[str, Any]]:
    peers = [_peer(row) for row in rows]
    assessed: list[dict[str, Any]] = []
    for index, row in enumerate(rows):
        additional = [peer for peer_index, peer in enumerate(peers) if peer_index != index]
        assessment = assess_medication(
            app,
            con,
            person,
            row["product"],
            row["draft"],
            acknowledged,
            additional_current=additional,
        )
        bind_warning_token(assessment, row["payload_hash"])
        assessed.append({**row, "assessment": assessment})
    return assessed


def _batch_warning_token(batch_fingerprint: str, rows: list[dict[str, Any]]) -> str | None:
    if not any(requires_acknowledgement(row["assessment"]) for row in rows):
        return None
    context = [
        {
            "row_id": row["row_id"],
            "product_ref": row["product"].get("product_ref"),
            "warning_token": row["assessment"].get("warning_token"),
        }
        for row in rows
    ]
    encoded = json.dumps(context, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{batch_fingerprint}\0{encoded}".encode("utf-8")).hexdigest()


def _preview_result(
    rows: list[dict[str, Any]],
    *,
    batch_fingerprint: str,
    warning_token: str | None,
    ocr_review_token: str | None,
    operation_id: str | None,
) -> dict[str, Any]:
    return {
        "operation_id": operation_id,
        "rows": [
            {
                "row_id": row["row_id"],
                "product": row["product"],
                "draft": row["draft"],
                "assessment": row["assessment"],
            }
            for row in rows
        ],
        "requires_review": warning_token is not None,
        "warning_token": warning_token,
        "ocr_review_token": ocr_review_token,
        "batch_fingerprint": batch_fingerprint,
    }


def preview_medication_batch(
    app: Any,
    person_id: str,
    rows: Any,
    *,
    operation_id: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_rows(app, rows)
    _bind_person(normalized, person_id)
    operation_id = str(operation_id or "").strip() or None
    if operation_id is None:
        raise ValueError("operation_id is required for OCR batch review")
    with app._personal() as con:
        person = app._get_person_from_connection(con, person_id)
        assessed = _assess_rows(app, con, person, normalized, False)
    fingerprint = _batch_fingerprint(person_id, normalized)
    warning_token = _batch_warning_token(fingerprint, assessed)
    review_token = app.ocr_reviews.issue(person_id, _BATCH_SUBJECT, fingerprint)
    return _preview_result(
        assessed,
        batch_fingerprint=fingerprint,
        warning_token=warning_token,
        ocr_review_token=review_token,
        operation_id=operation_id,
    )


def _derived_request_id(request_id: str, row_id: str) -> str:
    return f"{request_id}:{row_id}"


def _insert_row(
    app: Any,
    con: sqlite3.Connection,
    person_id: str,
    row: dict[str, Any],
    assessment: dict[str, Any],
    request_id: str,
    acknowledged: bool,
    medication_id: str,
) -> dict[str, Any]:
    product, draft = row["product"], row["draft"]
    con.execute(
        """
        INSERT INTO medications(
            id,person_id,catalog_item_seq,product_code,product_name,ingredient_code,ingredient_name,
            manufacturer,catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,
            meal_relation,administration_route,as_needed,prescription_days,
            start_date,end_date,active,source,revision
        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?,1)
        """,
        (
            medication_id, person_id, product["catalog_item_seq"], product["product_code"],
            product["product_name"], product.get("ingredient_code"), product.get("ingredient_name"),
            product.get("manufacturer"), product["catalog_source"], draft["dosage_text"],
            draft["dose_amount"], draft["dose_unit"], draft["frequency_per_day"],
            draft["meal_relation"], draft["administration_route"], int(draft["as_needed"]),
            draft["prescription_days"], draft["start_date"], draft["end_date"], "ocr",
        ),
    )
    app._replace_schedules(con, medication_id, draft["schedule_times"], draft["dosage_text"])
    medication = app._get_medication_from_connection(con, medication_id)
    app._append_revision(
        con, medication, "create", assessment, acknowledged, request_id, row["payload_hash"]
    )
    con.execute(
        "INSERT INTO medication_requests(request_id,person_id,payload_hash,medication_id) VALUES(?,?,?,?)",
        (request_id, person_id, row["payload_hash"], medication_id),
    )
    medication["assessment"] = assessment
    return medication


def _replace_peer_ids(value: Any, medication_ids: Mapping[str, str]) -> Any:
    if isinstance(value, list):
        return [_replace_peer_ids(item, medication_ids) for item in value]
    if isinstance(value, dict):
        result = {key: _replace_peer_ids(item, medication_ids) for key, item in value.items()}
        related = result.get("related_medication_id")
        if isinstance(related, str) and related.startswith("batch:"):
            row_id = related.removeprefix("batch:")
            if row_id in medication_ids:
                result["related_medication_id"] = medication_ids[row_id]
        return result
    return value


def add_medication_batch(
    app: Any,
    person_id: str,
    rows: Any,
    *,
    request_id: str,
    ocr_review_token: str | None,
    acknowledge_warnings: bool = False,
    warning_token: str | None = None,
) -> dict[str, Any]:
    normalized = _normalize_rows(app, rows)
    _bind_person(normalized, person_id)
    request_id = str(request_id or "").strip()
    if not request_id:
        raise ValueError("batch request_id is required")
    fingerprint = _batch_fingerprint(person_id, normalized)

    created: list[dict[str, Any]] | None = None
    with app._personal(write_lock=True) as con:
        existing: list[sqlite3.Row | None] = []
        for row in normalized:
            derived = _derived_request_id(request_id, row["row_id"])
            existing.append(con.execute(
                "SELECT person_id,payload_hash,medication_id FROM medication_requests WHERE request_id=?",
                (derived,),
            ).fetchone())
        if all(record is not None for record in existing):
            for row, record in zip(normalized, existing, strict=True):
                assert record is not None
                if record["person_id"] != person_id or record["payload_hash"] != row["payload_hash"]:
                    raise IdempotencyConflict("batch request_id was already used with a different payload")
            created = [
                app._get_medication_from_connection(con, record["medication_id"])
                for record in existing if record is not None
            ]
        elif any(record is not None for record in existing):
            raise IdempotencyConflict("batch request_id is only partially present")
        else:
            if not app.ocr_reviews.verify(ocr_review_token, person_id, _BATCH_SUBJECT, fingerprint):
                raise ValueError("ocr_review_token does not match the reviewed medication batch")
            person = app._get_person_from_connection(con, person_id)
            assessed = _assess_rows(app, con, person, normalized, acknowledge_warnings)
            expected_warning = _batch_warning_token(fingerprint, assessed)
            if expected_warning is not None and (
                not acknowledge_warnings or warning_token != expected_warning
            ):
                raise ConfirmationRequired(
                    request_id,
                    _preview_result(
                        assessed,
                        batch_fingerprint=fingerprint,
                        warning_token=expected_warning,
                        ocr_review_token=ocr_review_token,
                        operation_id=None,
                    ),
                )
            medication_ids = {row["row_id"]: app._new_id() for row in assessed}
            created = []
            for row in assessed:
                derived = _derived_request_id(request_id, row["row_id"])
                authoritative_assessment = _replace_peer_ids(row["assessment"], medication_ids)
                created.append(_insert_row(
                    app, con, person_id, row, authoritative_assessment, derived, acknowledge_warnings,
                    medication_ids[row["row_id"]],
                ))

    assert created is not None
    app.ocr_reviews.invalidate(ocr_review_token)
    return {"request_id": request_id, "medications": created}


__all__ = ["add_medication_batch", "preview_medication_batch"]
