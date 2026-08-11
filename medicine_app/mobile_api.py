from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .core import ConfirmationRequired, IdempotencyConflict, MedicationApp, RevisionConflict
from .ocr import OCRValidationError, split_ocr_request


_PERSON_FIELDS = {"name", "birth_date", "sex", "pregnancy_status", "lactation_status", "notes"}
_PREVIEW_FIELDS = {
    "product_ref", "product_code", "dosage_text", "dose_amount", "dose_unit",
    "frequency_per_day", "meal_relation", "administration_route", "as_needed",
    "prescription_days", "schedule_times", "start_date", "end_date", "envelope", "ocr_envelope",
}
_CREATE_FIELDS = {
    "product_ref", "product_code", "manual_name", "ingredient_name", "dosage_text", "dose_amount",
    "dose_unit", "frequency_per_day", "meal_relation", "administration_route", "as_needed",
    "prescription_days", "schedule_times", "start_date", "end_date", "request_id",
    "acknowledge_warnings", "warning_token", "source", "ocr_review_token", "ocr_origin",
}
_UPDATE_FIELDS = {
    "expected_revision", "dosage_text", "dose_amount", "dose_unit", "frequency_per_day",
    "meal_relation", "administration_route", "as_needed", "prescription_days", "schedule_times",
    "start_date", "end_date", "acknowledge_warnings", "warning_token",
}
_DOSE_FIELDS = {"status", "occurred_at", "note"}


def _json_default(value: Any) -> Any:
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    raise TypeError(f"cannot serialize {type(value).__name__}")


def _body_object(body_json: str | None) -> dict[str, Any]:
    if body_json is None or not str(body_json).strip():
        return {}
    try:
        payload = json.loads(body_json)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("request body must be valid JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("request body must be a JSON object")
    return payload


def _validated_fields(payload: Mapping[str, Any], allowed: set[str]) -> dict[str, Any]:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ValueError(f"unknown fields: {', '.join(unknown)}")
    return dict(payload)


def _bool_query(values: dict[str, list[str]], key: str, default: bool = False) -> bool:
    raw = values.get(key, [None])[-1]
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"invalid boolean query parameter: {key}")


class MobileApi:
    """HTTP-shaped dispatcher for the Android JavaScript bridge.

    The browser UI deliberately keeps its existing API contract. Android calls
    this object directly through Chaquopy instead of opening a TCP listener, so
    the same core and response/error semantics are available without an
    external or loopback web server.
    """

    def __init__(
        self,
        dur_db: str | Path,
        personal_db: str | Path,
        catalog_db: str | Path | None = None,
    ) -> None:
        self.service = MedicationApp(dur_db, personal_db, catalog_db)

    def request(self, method: str, path: str, body_json: str | None = None) -> str:
        try:
            status, body = self._dispatch(method.upper().strip(), path, body_json)
        except ConfirmationRequired as exc:
            status, body = 409, {
                "confirmation_required": True,
                "request_id": exc.request_id,
                "warning_token": exc.assessment.get("warning_token"),
                "assessment": exc.assessment,
            }
        except (RevisionConflict, IdempotencyConflict) as exc:
            status, body = 409, {"detail": str(exc)}
        except FileNotFoundError as exc:
            status, body = 503, {"detail": str(exc)}
        except KeyError as exc:
            status, body = 404, {"detail": str(exc).strip("'")}
        except (ValueError, OCRValidationError) as exc:
            status, body = 400, {"detail": str(exc)}
        except Exception:
            status, body = 500, {"detail": "unexpected server error"}
        return json.dumps(
            {"status": status, "body": body},
            ensure_ascii=False,
            separators=(",", ":"),
            default=_json_default,
        )

    def prepare_for_seal(self) -> None:
        """Merge committed WAL pages before Android encrypts the closed DB file."""
        with sqlite3.connect(self.service.personal_db, timeout=10) as con:
            con.execute("PRAGMA busy_timeout = 5000")
            row = con.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
            if row and int(row[0]) != 0:
                raise RuntimeError("personal database WAL checkpoint is busy")

    def _dispatch(self, method: str, raw_path: str, body_json: str | None) -> tuple[int, Any]:
        parsed = urlsplit(raw_path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        service = self.service

        if method == "GET" and path == "/api/health":
            return 200, {"ok": True, "full_catalog": service.products.has_full_catalog()}
        if method == "GET" and path == "/api/people":
            return 200, service.list_people()
        if method == "POST" and path == "/api/people":
            payload = _validated_fields(_body_object(body_json), _PERSON_FIELDS)
            return 201, service.create_person(**payload)
        match = re.fullmatch(r"/api/people/([^/]+)", path)
        if method == "PATCH" and match:
            payload = _validated_fields(_body_object(body_json), _PERSON_FIELDS)
            return 200, service.update_person(match.group(1), **payload)
        if method == "DELETE" and match:
            return 200, service.delete_person(match.group(1))
        if method == "GET" and path == "/api/products":
            term = (query.get("q") or [""])[-1].strip()
            if not term:
                raise ValueError("q is required")
            try:
                limit = int((query.get("limit") or ["30"])[-1])
            except ValueError as exc:
                raise ValueError("limit must be an integer") from exc
            if not 1 <= limit <= 100:
                raise ValueError("limit must be between 1 and 100")
            include_inactive = _bool_query(query, "include_inactive")
            return 200, service.search_products(term, limit, include_inactive=include_inactive)

        match = re.fullmatch(r"/api/people/([^/]+)/dashboard", path)
        if method == "GET" and match:
            person_id = match.group(1)
            target_date = (query.get("date") or [None])[-1]
            return 200, {
                "person": service.get_person(person_id),
                "medications": service.list_medications(person_id, as_of=target_date),
                "recent_logs": service.list_dose_logs(person_id, limit=20),
                "daily_plan": service.get_daily_plan(person_id, target_date),
            }

        match = re.fullmatch(r"/api/people/([^/]+)/daily-plan", path)
        if method == "GET" and match:
            target_date = (query.get("date") or [None])[-1]
            return 200, service.get_daily_plan(match.group(1), target_date)

        match = re.fullmatch(r"/api/people/([^/]+)/medications/preview", path)
        if method == "POST" and match:
            payload = _validated_fields(_body_object(body_json), _PREVIEW_FIELDS)
            envelope = payload.get("ocr_envelope") or payload.get("envelope")
            if envelope is not None:
                if not isinstance(envelope, dict):
                    raise ValueError("OCR envelope must be an object")
                return 200, service.preview_ocr(match.group(1), envelope, envelope.get("product_ref"))
            if not (payload.get("product_ref") or payload.get("product_code")):
                raise ValueError("product_ref or product_code is required")
            return 200, service.preview_medication(match.group(1), payload)

        match = re.fullmatch(r"/api/people/([^/]+)/medications/ocr-preview", path)
        if method == "POST" and match:
            envelope, product_ref = split_ocr_request(_body_object(body_json))
            return 200, service.preview_ocr(match.group(1), dict(envelope), product_ref)

        match = re.fullmatch(r"/api/people/([^/]+)/medications", path)
        if method == "POST" and match:
            payload = _validated_fields(_body_object(body_json), _CREATE_FIELDS)
            return 201, service.add_medication(match.group(1), **payload)

        match = re.fullmatch(r"/api/medications/([^/]+)", path)
        if method == "PATCH" and match:
            payload = _validated_fields(_body_object(body_json), _UPDATE_FIELDS)
            if "expected_revision" not in payload:
                raise ValueError("expected_revision is required")
            expected_revision = payload.pop("expected_revision")
            acknowledge = payload.pop("acknowledge_warnings", False)
            warning_token = payload.pop("warning_token", None)
            return 200, service.update_medication(
                match.group(1),
                expected_revision=expected_revision,
                acknowledge_warnings=acknowledge,
                warning_token=warning_token,
                **payload,
            )
        if method == "DELETE" and match:
            expected = (query.get("expected_revision") or [None])[-1]
            if expected is None:
                return 200, service.deactivate_medication(match.group(1))
            try:
                revision = int(expected)
            except ValueError as exc:
                raise ValueError("expected_revision must be an integer") from exc
            return 200, service.stop_medication(match.group(1), expected_revision=revision)

        match = re.fullmatch(r"/api/medications/([^/]+)/history", path)
        if method == "GET" and match:
            return 200, service.list_medication_revisions(match.group(1))

        match = re.fullmatch(r"/api/dose-instances/([^/]+)/completion", path)
        if method == "DELETE" and match:
            return 200, service.cancel_dose_instance(match.group(1))

        match = re.fullmatch(r"/api/dose-instances/([^/]+)", path)
        if method == "POST" and match:
            payload = _validated_fields(_body_object(body_json), _DOSE_FIELDS)
            if "status" not in payload:
                raise ValueError("status is required")
            return 200, service.record_dose_instance(
                match.group(1), payload["status"], payload.get("occurred_at"), payload.get("note")
            )

        return 404, {"detail": "route not found"}


def create_bridge(dur_db: str, personal_db: str, catalog_db: str | None = None) -> MobileApi:
    return MobileApi(dur_db, personal_db, catalog_db)


__all__ = ["MobileApi", "create_bridge"]
