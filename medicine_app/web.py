from __future__ import annotations

import os
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .core import MedicationApp


DEFAULT_DUR_DB = Path("data/db/dur.sqlite")
DEFAULT_PERSONAL_DB = Path("data/db/personal.sqlite")
DEFAULT_CATALOG_DB = Path("data/db/catalog.sqlite")
STATIC_DIR = Path(__file__).parent / "static"


class PersonCreate(BaseModel):
    name: str
    birth_date: str
    sex: str = "unknown"
    pregnancy_status: str = "unknown"
    notes: str | None = None


class MedicationPreviewRequest(BaseModel):
    product_ref: str | None = None
    product_code: str | None = None


class MedicationCreate(BaseModel):
    product_ref: str | None = None
    product_code: str | None = None
    manual_name: str | None = None
    ingredient_name: str | None = None
    dosage_text: str | None = None
    dose_amount: float | None = None
    dose_unit: str | None = None
    frequency_per_day: int | None = None
    meal_relation: str = "unspecified"
    administration_route: str = "oral"
    as_needed: bool = False
    prescription_days: int | None = None
    schedule_times: list[str] = Field(default_factory=list)
    start_date: str | None = None
    end_date: str | None = None


class DoseLogCreate(BaseModel):
    status: str
    occurred_at: str | None = None
    note: str | None = None


class DoseInstanceUpdate(BaseModel):
    status: str
    occurred_at: str | None = None
    note: str | None = None


def _translate_error(exc: Exception) -> HTTPException:
    if isinstance(exc, KeyError):
        return HTTPException(status_code=404, detail=str(exc).strip("'"))
    if isinstance(exc, ValueError):
        return HTTPException(status_code=400, detail=str(exc))
    return HTTPException(status_code=500, detail="unexpected server error")


def create_web_app(
    dur_db: Path | str = DEFAULT_DUR_DB,
    personal_db: Path | str = DEFAULT_PERSONAL_DB,
    catalog_db: Path | str | None = DEFAULT_CATALOG_DB,
) -> FastAPI:
    service = MedicationApp(dur_db, personal_db, catalog_db)
    app = FastAPI(title="Medicine", version="0.1.0")
    app.state.service = service
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return (STATIC_DIR / "index.html").read_text(encoding="utf-8")

    @app.get("/api/health")
    def health() -> dict:
        return {"ok": True, "full_catalog": service.products.has_full_catalog()}

    @app.get("/api/people")
    def list_people() -> list[dict]:
        return service.list_people()

    @app.post("/api/people", status_code=201)
    def create_person(payload: PersonCreate) -> dict:
        try:
            return service.create_person(**payload.model_dump())
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.get("/api/products")
    def search_products(q: str = Query(min_length=1), limit: int = Query(default=30, ge=1, le=100)) -> list[dict]:
        try:
            return service.search_products(q, limit)
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.get("/api/people/{person_id}/dashboard")
    def dashboard(person_id: str, date: str | None = None) -> dict:
        try:
            return {
                "person": service.get_person(person_id),
                "medications": service.list_medications(person_id),
                "recent_logs": service.list_dose_logs(person_id, limit=20),
                "daily_plan": service.get_daily_plan(person_id, date),
            }
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.get("/api/people/{person_id}/daily-plan")
    def daily_plan(person_id: str, date: str | None = None) -> dict:
        try:
            return service.get_daily_plan(person_id, date)
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/api/people/{person_id}/medications/preview")
    def preview_medication(person_id: str, payload: MedicationPreviewRequest) -> dict:
        try:
            product_ref = payload.product_ref or payload.product_code
            if not product_ref:
                raise ValueError("product_ref or product_code is required")
            return service.preview_medication(person_id, product_ref)
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/api/people/{person_id}/medications", status_code=201)
    def add_medication(person_id: str, payload: MedicationCreate) -> dict:
        try:
            return service.add_medication(person_id, **payload.model_dump())
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.delete("/api/medications/{medication_id}")
    def stop_medication(medication_id: str) -> dict:
        try:
            return service.deactivate_medication(medication_id)
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/api/medications/{medication_id}/logs", status_code=201)
    def log_dose(medication_id: str, payload: DoseLogCreate) -> dict:
        try:
            return service.record_dose(medication_id, payload.status, payload.occurred_at, payload.note)
        except Exception as exc:
            raise _translate_error(exc) from exc

    @app.post("/api/dose-instances/{instance_id}")
    def update_dose_instance(instance_id: str, payload: DoseInstanceUpdate) -> dict:
        try:
            return service.record_dose_instance(instance_id, payload.status, payload.occurred_at, payload.note)
        except Exception as exc:
            raise _translate_error(exc) from exc

    return app


app = create_web_app(
    os.environ.get("MEDICINE_DUR_DB", str(DEFAULT_DUR_DB)),
    os.environ.get("MEDICINE_PERSONAL_DB", str(DEFAULT_PERSONAL_DB)),
    os.environ.get("MEDICINE_CATALOG_DB", str(DEFAULT_CATALOG_DB)),
)
