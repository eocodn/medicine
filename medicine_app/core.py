from __future__ import annotations

import calendar
import re
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

from .persistence import ensure_personal_schema
from .planning import materialize_daily_plan, record_instance
from .products import ProductRepository

SEX_VALUES = {"female", "male", "other", "unknown"}
PREGNANCY_VALUES = {"pregnant", "not_pregnant", "unknown", "not_applicable"}
DOSE_STATUS_VALUES = {"taken", "skipped"}
MEAL_RELATION_VALUES = {"unspecified", "before_meal", "after_meal", "with_meal", "empty_stomach", "regardless"}
ADMINISTRATION_ROUTE_VALUES = {"oral", "topical", "inhaled", "ophthalmic", "otic", "nasal", "injection", "other", "unknown"}
APP_TIMEZONE = ZoneInfo("Asia/Seoul")


def _uuid() -> str:
    return str(uuid.uuid4())


def _row(row: sqlite3.Row | None) -> dict | None:
    return dict(row) if row is not None else None


def _parse_birth_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise ValueError("birth_date must be YYYY-MM-DD") from exc


def _add_months(value: date, months: int) -> date:
    month_index = value.month - 1 + months
    year = value.year + month_index // 12
    month = month_index % 12 + 1
    day = min(value.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _add_years(value: date, years: int) -> date:
    try:
        return value.replace(year=value.year + years)
    except ValueError:
        return value.replace(month=2, day=28, year=value.year + years)


def age_years(birth_date: str, as_of: date | None = None) -> int:
    birth = _parse_birth_date(birth_date)
    today = as_of or datetime.now(APP_TIMEZONE).date()
    years = today.year - birth.year
    if (today.month, today.day) < (birth.month, birth.day):
        years -= 1
    return max(years, 0)


AGE_RULE_RE = re.compile(r"(?P<n>\d+)\s*(?P<unit>세|개월|주)\s*(?P<op>미만|이하|이상|초과)")


def age_rule_matches(birth_date: str, rule: str | None, as_of: date | None = None) -> bool:
    if not rule:
        return False
    match = AGE_RULE_RE.search(rule)
    if not match:
        return False
    birth = _parse_birth_date(birth_date)
    today = as_of or datetime.now(APP_TIMEZONE).date()
    amount = int(match.group("n"))
    unit = match.group("unit")
    op = match.group("op")

    if unit == "세":
        threshold = _add_years(birth, amount)
        next_threshold = _add_years(birth, amount + 1)
    elif unit == "개월":
        threshold = _add_months(birth, amount)
        next_threshold = _add_months(birth, amount + 1)
    else:
        threshold = birth + timedelta(weeks=amount)
        next_threshold = birth + timedelta(weeks=amount + 1)

    if op == "미만":
        return today < threshold
    if op == "이하":
        return today < next_threshold
    if op == "이상":
        return today >= threshold
    if op == "초과":
        return today >= next_threshold
    return False


class MedicationApp:
    def __init__(
        self,
        dur_db: Path | str,
        personal_db: Path | str,
        catalog_db: Path | str | None = None,
    ):
        self.dur_db = Path(dur_db)
        self.personal_db = Path(personal_db)
        self.catalog_db = Path(catalog_db) if catalog_db else None
        if not self.dur_db.exists():
            raise FileNotFoundError(f"DUR database not found: {self.dur_db}")
        self.personal_db.parent.mkdir(parents=True, exist_ok=True)
        self.products = ProductRepository(self.dur_db, self.catalog_db)
        with self._personal() as con:
            ensure_personal_schema(con)

    @contextmanager
    def _personal(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.personal_db, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys = ON")
        con.execute("PRAGMA busy_timeout = 5000")
        try:
            yield con
            con.commit()
        except Exception:
            con.rollback()
            raise
        finally:
            con.close()

    @contextmanager
    def _dur(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self.dur_db.resolve()}?mode=ro"
        con = sqlite3.connect(uri, uri=True, timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")
        try:
            yield con
        finally:
            con.close()

    def create_person(
        self,
        name: str,
        birth_date: str,
        sex: str = "unknown",
        pregnancy_status: str = "unknown",
        notes: str | None = None,
    ) -> dict:
        name = name.strip()
        if not name:
            raise ValueError("name is required")
        _parse_birth_date(birth_date)
        if sex not in SEX_VALUES:
            raise ValueError(f"invalid sex: {sex}")
        if pregnancy_status not in PREGNANCY_VALUES:
            raise ValueError(f"invalid pregnancy_status: {pregnancy_status}")
        person_id = _uuid()
        with self._personal() as con:
            con.execute(
                "INSERT INTO people(id,name,birth_date,sex,pregnancy_status,notes) VALUES(?,?,?,?,?,?)",
                (person_id, name, birth_date, sex, pregnancy_status, notes),
            )
            row = con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        return self._person_dict(row)

    def list_people(self) -> list[dict]:
        with self._personal() as con:
            rows = con.execute("SELECT * FROM people ORDER BY rowid").fetchall()
        return [self._person_dict(row) for row in rows]

    def get_person(self, person_id: str) -> dict:
        with self._personal() as con:
            row = con.execute("SELECT * FROM people WHERE id=?", (person_id,)).fetchone()
        if row is None:
            raise KeyError("person not found")
        return self._person_dict(row)

    def _person_dict(self, row: sqlite3.Row) -> dict:
        data = dict(row)
        data["age"] = age_years(data["birth_date"])
        return data

    def search_products(self, term: str, limit: int = 30) -> list[dict]:
        return self.products.search(term, limit)

    def get_product(self, product_ref: str) -> dict:
        return self.products.get(product_ref)

    def add_medication(
        self,
        person_id: str,
        *,
        product_ref: str | None = None,
        product_code: str | None = None,
        manual_name: str | None = None,
        ingredient_name: str | None = None,
        dosage_text: str | None = None,
        dose_amount: float | None = None,
        dose_unit: str | None = None,
        frequency_per_day: int | None = None,
        meal_relation: str = "unspecified",
        administration_route: str = "oral",
        as_needed: bool = False,
        prescription_days: int | None = None,
        schedule_times: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        source: str | None = None,
    ) -> dict:
        self.get_person(person_id)
        resolved_ref = product_ref or product_code
        if resolved_ref:
            product = self.get_product(resolved_ref)
            product_code = product["product_code"]
            product_name = product["product_name"] or resolved_ref
            ingredient_code = product["ingredient_code"]
            resolved_ingredient = product["ingredient_name"]
            catalog_item_seq = product["catalog_item_seq"]
            manufacturer = product["manufacturer"]
            catalog_source = product["catalog_source"]
            med_source = source or "catalog_search"
        else:
            product_name = (manual_name or "").strip()
            if not product_name:
                raise ValueError("product_ref, product_code or manual_name is required")
            ingredient_code = None
            resolved_ingredient = ingredient_name
            catalog_item_seq = None
            manufacturer = None
            catalog_source = "manual"
            med_source = source or "manual"

        schedule_times = schedule_times or []
        for value in schedule_times:
            self._validate_time(value)
        if frequency_per_day is None and schedule_times:
            frequency_per_day = len(schedule_times)
        if dose_amount is not None:
            dose_amount = float(dose_amount)
            if dose_amount <= 0:
                raise ValueError("dose_amount must be > 0")
        dose_unit = (dose_unit or "").strip() or None
        if frequency_per_day is not None:
            frequency_per_day = int(frequency_per_day)
            if frequency_per_day < 1 or frequency_per_day > 24:
                raise ValueError("frequency_per_day must be between 1 and 24")
            if schedule_times and frequency_per_day != len(schedule_times):
                raise ValueError("frequency_per_day must match the number of schedule_times")
        if meal_relation not in MEAL_RELATION_VALUES:
            raise ValueError(f"invalid meal_relation: {meal_relation}")
        if administration_route not in ADMINISTRATION_ROUTE_VALUES:
            raise ValueError(f"invalid administration_route: {administration_route}")
        if prescription_days is not None:
            prescription_days = int(prescription_days)
            if prescription_days < 1 or prescription_days > 3650:
                raise ValueError("prescription_days must be between 1 and 3650")

        start = date.fromisoformat(start_date) if start_date else datetime.now(APP_TIMEZONE).date()
        finish = date.fromisoformat(end_date) if end_date else None
        if prescription_days is not None:
            computed_finish = start + timedelta(days=prescription_days - 1)
            if finish is not None and finish != computed_finish:
                raise ValueError("end_date conflicts with start_date and prescription_days")
            finish = computed_finish
        if start is not None and finish is not None and finish < start:
            raise ValueError("end_date must be on or after start_date")
        start_date = start.isoformat() if start else None
        end_date = finish.isoformat() if finish else None

        if dosage_text is None and dose_amount is not None:
            amount_text = f"{dose_amount:g}"
            dosage_text = f"{amount_text}{dose_unit or ''}"

        medication_id = _uuid()
        with self._personal() as con:
            con.execute(
                """
                INSERT INTO medications(
                    id,person_id,catalog_item_seq,product_code,product_name,ingredient_code,ingredient_name,
                    manufacturer,catalog_source,dosage_text,dose_amount,dose_unit,frequency_per_day,
                    meal_relation,administration_route,as_needed,prescription_days,
                    start_date,end_date,active,source
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,1,?)
                """,
                (
                    medication_id, person_id, catalog_item_seq, product_code, product_name, ingredient_code,
                    resolved_ingredient, manufacturer, catalog_source, dosage_text, dose_amount, dose_unit,
                    frequency_per_day, meal_relation, administration_route, int(bool(as_needed)),
                    prescription_days, start_date, end_date, med_source,
                ),
            )
            for time_of_day in schedule_times:
                con.execute(
                    "INSERT INTO medication_schedules(id,medication_id,time_of_day,dose_text) VALUES(?,?,?,?)",
                    (_uuid(), medication_id, time_of_day, dosage_text),
                )
        return self.get_medication(medication_id)

    @staticmethod
    def _validate_time(value: str) -> None:
        try:
            datetime.strptime(value, "%H:%M")
        except ValueError as exc:
            raise ValueError("schedule time must be HH:MM") from exc

    def get_medication(self, medication_id: str) -> dict:
        with self._personal() as con:
            row = con.execute("SELECT * FROM medications WHERE id=?", (medication_id,)).fetchone()
            if row is None:
                raise KeyError("medication not found")
            schedules = con.execute(
                "SELECT time_of_day,dose_text FROM medication_schedules WHERE medication_id=? ORDER BY time_of_day",
                (medication_id,),
            ).fetchall()
        data = dict(row)
        data["active"] = bool(data["active"])
        data["as_needed"] = bool(data.get("as_needed") or 0)
        data["meal_relation"] = data.get("meal_relation") or "unspecified"
        data["administration_route"] = data.get("administration_route") or "oral"
        data["schedules"] = [dict(item) for item in schedules]
        return data

    def list_medications(self, person_id: str, active_only: bool = True) -> list[dict]:
        self.get_person(person_id)
        where = "WHERE person_id=?"
        params: tuple = (person_id,)
        if active_only:
            where += " AND active=1"
        with self._personal() as con:
            rows = con.execute(f"SELECT id FROM medications {where} ORDER BY rowid", params).fetchall()
        return [self.get_medication(row["id"]) for row in rows]

    def get_daily_plan(self, person_id: str, target_date: str | date | None = None) -> dict:
        self.get_person(person_id)
        if target_date is None:
            target = datetime.now(APP_TIMEZONE).date()
        elif isinstance(target_date, date):
            target = target_date
        else:
            target = date.fromisoformat(target_date)
        medications = self.list_medications(person_id, active_only=True)
        with self._personal() as con:
            return materialize_daily_plan(con, person_id, medications, target, _uuid)

    def record_dose_instance(
        self,
        instance_id: str,
        status: str,
        occurred_at: str | None = None,
        note: str | None = None,
    ) -> dict:
        if status not in DOSE_STATUS_VALUES:
            raise ValueError("status must be taken or skipped")
        when = occurred_at or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
        datetime.fromisoformat(when)
        with self._personal() as con:
            return record_instance(con, instance_id, status, when, note, _uuid)

    def deactivate_medication(self, medication_id: str) -> dict:
        with self._personal() as con:
            result = con.execute("UPDATE medications SET active=0 WHERE id=?", (medication_id,))
            if result.rowcount == 0:
                raise KeyError("medication not found")
        return self.get_medication(medication_id)

    def record_dose(self, medication_id: str, status: str, occurred_at: str | None = None, note: str | None = None) -> dict:
        if status not in DOSE_STATUS_VALUES:
            raise ValueError("status must be taken or skipped")
        medication = self.get_medication(medication_id)
        when = occurred_at or datetime.now(APP_TIMEZONE).isoformat(timespec="seconds")
        datetime.fromisoformat(when)
        log_id = _uuid()
        with self._personal() as con:
            con.execute(
                "INSERT INTO dose_logs(id,medication_id,person_id,status,occurred_at,note) VALUES(?,?,?,?,?,?)",
                (log_id, medication_id, medication["person_id"], status, when, note),
            )
            row = con.execute("SELECT * FROM dose_logs WHERE id=?", (log_id,)).fetchone()
        return dict(row)

    def list_dose_logs(self, person_id: str, limit: int = 50) -> list[dict]:
        self.get_person(person_id)
        with self._personal() as con:
            rows = con.execute(
                """
                SELECT l.*, m.product_name, m.dosage_text
                FROM dose_logs l JOIN medications m ON m.id=l.medication_id
                WHERE l.person_id=? ORDER BY l.occurred_at DESC, l.rowid DESC LIMIT ?
                """,
                (person_id, limit),
            ).fetchall()
        return [dict(row) for row in rows]

    def preview_medication(self, person_id: str, product_code: str, as_of: date | None = None) -> dict:
        person = self.get_person(person_id)
        product = self.get_product(product_code)
        current = self.list_medications(person_id)
        risks: list[dict] = []
        if product.get("dur_match"):
            with self._dur() as con:
                risks.extend(self._combination_risks(con, product, current))
                risks.extend(self._person_specific_risks(con, person, product, as_of))
                risks.extend(self._duplication_risks(con, product, current))
                risks.extend(self._rule_presence_risks(con, product))
        return {
            "person": person,
            "product": product,
            "current_medication_count": len(current),
            "risks": self._dedupe_risks(risks),
            "coverage": {
                "dur_match": bool(product.get("dur_match")),
                "message": (
                    "DUR 제품코드와 연결되어 개인별 금기·주의를 확인했습니다."
                    if product.get("dur_match")
                    else "전체 의약품 카탈로그에는 있지만 DUR 제품코드와 연결되지 않아 자동 위험 확인 범위가 제한됩니다."
                ),
            },
        }

    def _combination_risks(self, con: sqlite3.Connection, product: dict, current: list[dict]) -> list[dict]:
        risks: list[dict] = []
        for medication in current:
            if not medication.get("product_code"):
                continue
            rows = con.execute(
                """
                SELECT details, notice_no, notice_date, product_name, paired_product_name
                FROM product_dur
                WHERE category='combination_contraindication'
                  AND ((product_code=? AND paired_product_code=?) OR (product_code=? AND paired_product_code=?))
                LIMIT 10
                """,
                (product["product_code"], medication["product_code"], medication["product_code"], product["product_code"]),
            ).fetchall()
            for row in rows:
                risks.append(
                    {
                        "type": "combination_contraindication",
                        "severity": "danger",
                        "title": f"{medication['product_name']}와 병용금기",
                        "details": row["details"] or "DUR 병용금기 조합에 해당합니다.",
                        "related_medication_id": medication["id"],
                        "notice_no": row["notice_no"],
                        "notice_date": row["notice_date"],
                    }
                )
        return risks

    def _person_specific_risks(
        self, con: sqlite3.Connection, person: dict, product: dict, as_of: date | None
    ) -> list[dict]:
        risks: list[dict] = []
        rows = con.execute(
            """
            SELECT category, rule_value, details, notice_no, notice_date
            FROM product_dur
            WHERE product_code=? AND category IN ('age_contraindication','pregnancy_contraindication','elderly_caution')
            """,
            (product["product_code"],),
        ).fetchall()
        current_age = age_years(person["birth_date"], as_of)
        for row in rows:
            category = row["category"]
            if category == "age_contraindication":
                if not age_rule_matches(person["birth_date"], row["rule_value"], as_of):
                    continue
                title = f"연령금기 · {row['rule_value']}"
                severity = "danger"
            elif category == "pregnancy_contraindication":
                if person["pregnancy_status"] != "pregnant":
                    continue
                title = f"임부금기 · {row['rule_value'] or '등급 미표기'}"
                severity = "danger"
            else:
                if current_age < 65:
                    continue
                title = "노인주의 대상"
                severity = "warning"
            risks.append(
                {
                    "type": category,
                    "severity": severity,
                    "title": title,
                    "details": row["details"],
                    "notice_no": row["notice_no"],
                    "notice_date": row["notice_date"],
                }
            )
        return risks

    def _duplication_risks(self, con: sqlite3.Connection, product: dict, current: list[dict]) -> list[dict]:
        new_groups = {
            row["rule_value"]
            for row in con.execute(
                "SELECT DISTINCT rule_value FROM product_dur WHERE category='therapeutic_duplication_caution' AND product_code=?",
                (product["product_code"],),
            ).fetchall()
            if row["rule_value"]
        }
        if not new_groups:
            return []
        risks: list[dict] = []
        for medication in current:
            code = medication.get("product_code")
            if not code:
                continue
            groups = {
                row["rule_value"]
                for row in con.execute(
                    "SELECT DISTINCT rule_value FROM product_dur WHERE category='therapeutic_duplication_caution' AND product_code=?",
                    (code,),
                ).fetchall()
                if row["rule_value"]
            }
            for group in sorted(new_groups & groups):
                risks.append(
                    {
                        "type": "therapeutic_duplication_caution",
                        "severity": "warning",
                        "title": f"효능군 중복주의 · {group}",
                        "details": f"현재 복용 중인 {medication['product_name']}와 같은 효능군입니다.",
                        "related_medication_id": medication["id"],
                    }
                )
        return risks

    def _rule_presence_risks(self, con: sqlite3.Connection, product: dict) -> list[dict]:
        labels = {
            "dose_caution": "용량주의 대상",
            "duration_caution": "투여기간주의 대상",
        }
        rows = con.execute(
            """
            SELECT category, rule_value, details, notice_no, notice_date
            FROM product_dur
            WHERE product_code=? AND category IN ('dose_caution','duration_caution')
            """,
            (product["product_code"],),
        ).fetchall()
        risks = []
        for row in rows:
            value = row["rule_value"]
            detail = row["details"]
            if value:
                detail = f"기준: {value}. " + (detail or "입력한 복용량/기간과의 자동 비교는 아직 지원하지 않습니다.")
            risks.append(
                {
                    "type": row["category"],
                    "severity": "info",
                    "title": labels[row["category"]],
                    "details": detail,
                    "notice_no": row["notice_no"],
                    "notice_date": row["notice_date"],
                }
            )
        return risks

    @staticmethod
    def _dedupe_risks(risks: list[dict]) -> list[dict]:
        seen: set[tuple] = set()
        result: list[dict] = []
        for risk in risks:
            key = (risk.get("type"), risk.get("title"), risk.get("details"), risk.get("related_medication_id"))
            if key in seen:
                continue
            seen.add(key)
            result.append(risk)
        order = {"danger": 0, "warning": 1, "info": 2}
        result.sort(key=lambda item: (order.get(item.get("severity"), 9), item.get("title") or ""))
        return result
