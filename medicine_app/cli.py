from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

from .core import ConfirmationRequired, MedicationApp
from .ocr import OCRValidationError, inspect_envelope


DEFAULT_DUR_DB = Path("data/db/dur.sqlite")
DEFAULT_PERSONAL_DB = Path("data/db/personal.sqlite")
DEFAULT_CATALOG_DB = Path("data/db/catalog.sqlite")


def emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def capture_screenshot(
    dur_db: Path,
    personal_db: Path,
    catalog_db: Path,
    output: Path,
    width: int,
    height: int,
) -> dict:
    browser = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if browser is None:
        raise RuntimeError("Chromium is not installed; use the compose 'ui' service")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    env = os.environ.copy()
    env["MEDICINE_DUR_DB"] = str(dur_db.resolve())
    env["MEDICINE_PERSONAL_DB"] = str(personal_db.resolve())
    env["MEDICINE_CATALOG_DB"] = str(catalog_db.resolve())
    server = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "uvicorn",
            "medicine_app.web:app",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--log-level",
            "warning",
        ],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + 10
        while True:
            if server.poll() is not None:
                raise RuntimeError("temporary web server exited before screenshot")
            try:
                with urllib.request.urlopen(f"{url}/api/health", timeout=0.5) as response:
                    if response.status == 200:
                        break
            except OSError:
                pass
            if time.monotonic() >= deadline:
                raise RuntimeError("temporary web server did not become ready")
            time.sleep(0.1)

        subprocess.run(
            [
                browser,
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--virtual-time-budget=2000",
                f"--window-size={width},{height}",
                f"--screenshot={output}",
                url,
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait(timeout=5)

    return {"path": str(output), "width": width, "height": height, "size_bytes": output.stat().st_size}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medicine-app", description="Headless control CLI for the medication app")
    parser.add_argument("--dur-db", type=Path, default=DEFAULT_DUR_DB)
    parser.add_argument("--personal-db", type=Path, default=DEFAULT_PERSONAL_DB)
    parser.add_argument("--catalog-db", type=Path, default=DEFAULT_CATALOG_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    people = sub.add_parser("people")
    people.add_argument("--json", action="store_true")

    person_add = sub.add_parser("person-add")
    person_add.add_argument("--name", required=True)
    person_add.add_argument("--birth-date", required=True)
    person_add.add_argument("--sex", default="unknown")
    person_add.add_argument("--pregnancy-status", default="unknown")
    person_add.add_argument("--json", action="store_true")

    search = sub.add_parser("drug-search")
    search.add_argument("term")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--include-inactive", action="store_true")
    search.add_argument("--json", action="store_true")

    meds = sub.add_parser("meds")
    meds.add_argument("--person", required=True)
    meds.add_argument("--json", action="store_true")

    preview = sub.add_parser("risk-preview")
    preview.add_argument("--person", required=True)
    preview.add_argument("--product-ref", "--product-code", dest="product_ref", required=True)
    preview.add_argument("--dose-amount", type=float)
    preview.add_argument("--dose-unit")
    preview.add_argument("--frequency", type=int)
    preview.add_argument("--meal-relation", default="unspecified")
    preview.add_argument("--route", default="oral")
    preview.add_argument("--prn", action="store_true")
    preview.add_argument("--days", type=int)
    preview.add_argument("--start-date")
    preview.add_argument("--end-date")
    preview.add_argument("--time", action="append", default=[])
    preview.add_argument("--json", action="store_true")

    add = sub.add_parser("med-add")
    add.add_argument("--person", required=True)
    add.add_argument("--product-ref", "--product-code", dest="product_ref", required=True)
    add.add_argument("--dose")
    add.add_argument("--dose-amount", type=float)
    add.add_argument("--dose-unit")
    add.add_argument("--frequency", type=int)
    add.add_argument("--meal-relation", default="unspecified")
    add.add_argument("--route", default="oral")
    add.add_argument("--prn", action="store_true")
    add.add_argument("--days", type=int)
    add.add_argument("--start-date")
    add.add_argument("--end-date")
    add.add_argument("--time", action="append", default=[])
    add.add_argument("--request-id")
    add.add_argument("--acknowledge-warnings", action="store_true")
    add.add_argument("--warning-token")
    add.add_argument("--json", action="store_true")

    update = sub.add_parser("med-update")
    update.add_argument("--medication", required=True)
    update.add_argument("--expected-revision", type=int, required=True)
    update.add_argument("--dose")
    update.add_argument("--dose-amount", type=float)
    update.add_argument("--dose-unit")
    update.add_argument("--frequency", type=int)
    update.add_argument("--meal-relation")
    update.add_argument("--route")
    prn = update.add_mutually_exclusive_group()
    prn.add_argument("--prn", dest="as_needed", action="store_true")
    prn.add_argument("--scheduled", dest="as_needed", action="store_false")
    update.set_defaults(as_needed=None)
    update.add_argument("--days", type=int)
    update.add_argument("--start-date")
    update.add_argument("--end-date")
    update.add_argument("--time", action="append")
    update.add_argument("--acknowledge-warnings", action="store_true")
    update.add_argument("--warning-token")
    update.add_argument("--json", action="store_true")

    history = sub.add_parser("med-history")
    history.add_argument("--medication", required=True)
    history.add_argument("--json", action="store_true")

    stop = sub.add_parser("med-stop")
    stop.add_argument("--medication", required=True)
    stop.add_argument("--expected-revision", type=int, required=True)
    stop.add_argument("--json", action="store_true")

    plan = sub.add_parser("daily-plan")
    plan.add_argument("--person", required=True)
    plan.add_argument("--date")
    plan.add_argument("--json", action="store_true")

    instance = sub.add_parser("dose-instance")
    instance.add_argument("--instance", required=True)
    instance.add_argument("--status", choices=["taken", "skipped"], required=True)
    instance.add_argument("--at")
    instance.add_argument("--json", action="store_true")

    log = sub.add_parser("dose-log")
    log.add_argument("--medication", required=True)
    log.add_argument("--status", choices=["taken", "skipped"], required=True)
    log.add_argument("--at")
    log.add_argument("--json", action="store_true")

    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("--output", type=Path, default=Path("data/debug/mobile.png"))
    screenshot.add_argument("--width", type=int, default=390)
    screenshot.add_argument("--height", type=int, default=844)
    screenshot.add_argument("--json", action="store_true")

    ocr = sub.add_parser("ocr-inspect")
    ocr.add_argument("--input", required=True, metavar="FILE|-", type=Path)
    ocr.add_argument("--json", action="store_true")

    return parser


def _dispatch(args, app: MedicationApp):
    if args.command == "people":
        payload = app.list_people()
    elif args.command == "person-add":
        payload = app.create_person(args.name, args.birth_date, args.sex, args.pregnancy_status)
    elif args.command == "drug-search":
        payload = app.search_products(args.term, args.limit, include_inactive=args.include_inactive)
    elif args.command == "meds":
        payload = app.list_medications(args.person)
    elif args.command == "risk-preview":
        payload = app.preview_medication(args.person, {
            "product_ref": args.product_ref, "dose_amount": args.dose_amount,
            "dose_unit": args.dose_unit, "frequency_per_day": args.frequency,
            "meal_relation": args.meal_relation, "administration_route": args.route,
            "as_needed": args.prn, "prescription_days": args.days,
            "start_date": args.start_date, "end_date": args.end_date,
            "schedule_times": args.time,
        })
    elif args.command == "med-add":
        payload = app.add_medication(
            args.person,
            product_ref=args.product_ref,
            dosage_text=args.dose,
            dose_amount=args.dose_amount,
            dose_unit=args.dose_unit,
            frequency_per_day=args.frequency,
            meal_relation=args.meal_relation,
            administration_route=args.route,
            as_needed=args.prn,
            prescription_days=args.days,
            start_date=args.start_date,
            end_date=args.end_date,
            schedule_times=args.time,
            request_id=args.request_id,
            acknowledge_warnings=args.acknowledge_warnings,
            warning_token=args.warning_token,
        )
    elif args.command == "med-update":
        mapping = {
            "dosage_text": args.dose, "dose_amount": args.dose_amount,
            "dose_unit": args.dose_unit, "frequency_per_day": args.frequency,
            "meal_relation": args.meal_relation, "administration_route": args.route,
            "as_needed": args.as_needed, "prescription_days": args.days,
            "start_date": args.start_date, "end_date": args.end_date,
            "schedule_times": args.time,
        }
        payload = app.update_medication(
            args.medication, expected_revision=args.expected_revision,
            acknowledge_warnings=args.acknowledge_warnings, warning_token=args.warning_token,
            **{key: value for key, value in mapping.items() if value is not None},
        )
    elif args.command == "med-history":
        payload = app.list_medication_revisions(args.medication)
    elif args.command == "med-stop":
        payload = app.stop_medication(args.medication, expected_revision=args.expected_revision)
    elif args.command == "daily-plan":
        payload = app.get_daily_plan(args.person, args.date)
    elif args.command == "dose-instance":
        payload = app.record_dose_instance(args.instance, args.status, args.at)
    elif args.command == "dose-log":
        payload = app.record_dose(args.medication, args.status, args.at)
    elif args.command == "screenshot":
        if args.width < 320 or args.height < 480:
            raise SystemExit("screenshot dimensions are too small")
        payload = capture_screenshot(args.dur_db, args.personal_db, args.catalog_db, args.output, args.width, args.height)
    else:
        raise AssertionError(args.command)

    return payload


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "ocr-inspect":
        try:
            raw = sys.stdin.read() if str(args.input) == "-" else args.input.read_text(encoding="utf-8")
            payload = inspect_envelope(json.loads(raw))
            if not payload.get("valid", False):
                emit(payload, args.json)
                return 2
        except (OSError, UnicodeError, json.JSONDecodeError, OCRValidationError) as exc:
            payload = {"valid": False, "error": str(exc), "issues": getattr(exc, "issues", [])}
            emit(payload, args.json)
            return 2
        emit(payload, args.json)
        return 0
    app = MedicationApp(args.dur_db, args.personal_db, args.catalog_db)
    try:
        payload = _dispatch(args, app)
    except ConfirmationRequired as exc:
        emit({
            "confirmation_required": True,
            "request_id": exc.request_id,
            "warning_token": exc.assessment.get("warning_token"),
            "assessment": exc.assessment,
        }, getattr(args, "json", False))
        return 2

    emit(payload, args.json)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
