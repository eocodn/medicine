from __future__ import annotations

import json
import re
import sqlite3
from contextlib import closing
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import parse_qs, urlsplit

from .core import ConfirmationRequired, IdempotencyConflict, MedicationApp, RevisionConflict
from .mobile_request_policy import classify_mobile_request


_PERSON_FIELDS = {"name", "birth_date", "sex", "pregnancy_status", "lactation_status", "notes"}
_PREVIEW_FIELDS = {
    "product_ref", "product_code", "dosage_text", "dose_amount", "dose_unit",
    "frequency_per_day", "meal_relation", "administration_route", "as_needed", "prn_max_per_day",
    "prescription_days", "long_term", "schedule_times", "start_date", "end_date",
}
_CREATE_FIELDS = {
    "product_ref", "product_code", "manual_name", "ingredient_name", "dosage_text", "dose_amount",
    "dose_unit", "frequency_per_day", "meal_relation", "administration_route", "as_needed", "prn_max_per_day",
    "prescription_days", "long_term", "schedule_times", "start_date", "end_date", "request_id",
    "acknowledge_warnings", "warning_token",
}
_UPDATE_FIELDS = {
    "expected_revision", "dosage_text", "dose_amount", "dose_unit", "frequency_per_day",
    "meal_relation", "administration_route", "as_needed", "prn_max_per_day", "prescription_days", "long_term",
    "schedule_times", "start_date", "end_date", "acknowledge_warnings", "warning_token",
}
_DOSE_FIELDS = {"status", "occurred_at", "note"}


class ReferenceUnavailable(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


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
        canonical_db: str | Path | None,
        personal_db: str | Path,
        *,
        reference_unavailable_reason: str | None = None,
    ) -> None:
        self.service = MedicationApp(canonical_db, personal_db)
        self.reference_available = canonical_db is not None
        self.reference_unavailable_reason = (
            None if self.reference_available else (reference_unavailable_reason or "unavailable")
        )

    def set_reference_available(self, available: bool, reason: str | None = None) -> None:
        if available and self.service.products is None:
            raise ValueError("reference database is unavailable")
        self.reference_available = bool(available)
        self.reference_unavailable_reason = None if self.reference_available else (reason or "unavailable")

    def _require_reference(self) -> None:
        if not self.reference_available:
            raise ReferenceUnavailable(self.reference_unavailable_reason or "unavailable")

    def request_access(self, method: str, raw_path: str) -> str:
        return classify_mobile_request(method, raw_path).access

    def request(self, method: str, path: str, body_json: str | None = None) -> str:
        try:
            normalized_method = method.upper().strip()
            policy = classify_mobile_request(normalized_method, path)
            if policy.requires_reference:
                self._require_reference()
            if policy.access == "personal_read":
                with self.service.personal_read_only():
                    status, body = self._dispatch(normalized_method, path, body_json)
            else:
                status, body = self._dispatch(normalized_method, path, body_json)
        except ConfirmationRequired as exc:
            status, body = 409, {
                "confirmation_required": True,
                "request_id": exc.request_id,
                "warning_token": exc.assessment.get("warning_token"),
                "assessment": exc.assessment,
            }
        except (RevisionConflict, IdempotencyConflict) as exc:
            status, body = 409, {"detail": str(exc)}
        except ReferenceUnavailable as exc:
            status, body = 503, {
                "detail": "reference data unavailable; app update required",
                "reference_status": exc.reason,
            }
        except FileNotFoundError as exc:
            status, body = 503, {"detail": str(exc)}
        except KeyError as exc:
            status, body = 404, {"detail": str(exc).strip("'")}
        except ValueError as exc:
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
        with closing(sqlite3.connect(self.service.personal_db, timeout=10)) as con:
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
            return 200, {
                "ok": True,
                "full_catalog": service.products.has_full_catalog() if self.reference_available else False,
                "reference_available": self.reference_available,
                "reference_status": self.reference_unavailable_reason,
            }
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
            self._require_reference()
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
            return 200, service.get_dashboard(
                person_id,
                target_date,
                include_current_assessment=self.reference_available,
            )

        match = re.fullmatch(r"/api/people/([^/]+)/daily-plan", path)
        if method == "GET" and match:
            target_date = (query.get("date") or [None])[-1]
            return 200, service.get_daily_plan(match.group(1), target_date)

        match = re.fullmatch(r"/api/people/([^/]+)/medications/preview", path)
        if method == "POST" and match:
            self._require_reference()
            payload = _validated_fields(_body_object(body_json), _PREVIEW_FIELDS)
            if not (payload.get("product_ref") or payload.get("product_code")):
                raise ValueError("product_ref or product_code is required")
            return 200, service.preview_medication(match.group(1), payload)

        match = re.fullmatch(r"/api/people/([^/]+)/medications", path)
        if method == "POST" and match:
            self._require_reference()
            payload = _validated_fields(_body_object(body_json), _CREATE_FIELDS)
            return 201, service.add_medication(match.group(1), **payload)

        match = re.fullmatch(r"/api/medications/([^/]+)", path)
        if method == "PATCH" and match:
            self._require_reference()
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

        match = re.fullmatch(r"/api/medications/([^/]+)/prn-intakes", path)
        if method == "POST" and match:
            payload = _validated_fields(_body_object(body_json), {"occurred_at", "note", "request_id"})
            request_id = str(payload.get("request_id") or "").strip()
            if not request_id:
                raise ValueError("request_id is required")
            dose = service.record_prn_dose(
                match.group(1),
                payload.get("occurred_at"),
                payload.get("note"),
                request_id=request_id,
            )
            return 201, service.with_recent_dose_logs(dose)

        match = re.fullmatch(r"/api/medications/([^/]+)/history", path)
        if method == "GET" and match:
            return 200, service.list_medication_revisions(match.group(1))

        match = re.fullmatch(r"/api/dose-instances/([^/]+)/completion", path)
        if method == "DELETE" and match:
            return 200, service.with_recent_dose_logs(service.cancel_dose_instance(match.group(1)))

        match = re.fullmatch(r"/api/dose-instances/([^/]+)", path)
        if method == "POST" and match:
            payload = _validated_fields(_body_object(body_json), _DOSE_FIELDS)
            if "status" not in payload:
                raise ValueError("status is required")
            metadata = {}
            if "occurred_at" in payload:
                metadata["occurred_at"] = payload["occurred_at"]
            if "note" in payload:
                metadata["note"] = payload["note"]
            dose = service.record_dose_instance(
                match.group(1), payload["status"], **metadata
            )
            return 200, service.with_recent_dose_logs(dose)

        return 404, {"detail": "route not found"}


def create_bridge(canonical_db: str | None, personal_db: str) -> MobileApi:
    return MobileApi(canonical_db, personal_db)


__all__ = ["MobileApi", "create_bridge"]
