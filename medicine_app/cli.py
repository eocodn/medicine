from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import sqlite3
import subprocess
import tempfile
import time
import urllib.request
from urllib.parse import quote, urlencode
from pathlib import Path



DEFAULT_CANONICAL_DB = Path("data/db/canonical.sqlite")
DEFAULT_PERSONAL_DB = Path("data/db/personal.sqlite")
MEDICINE_CORE_BINARY = os.environ.get("MEDICINE_CORE_BINARY", "medicine-core")
MEDICINE_CORE_WEB_BINARY = os.environ.get("MEDICINE_CORE_WEB_BINARY", "medicine-core-web")
STATIC_DIR = Path(__file__).parent / "static"


def emit(payload, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif isinstance(payload, list):
        for item in payload:
            print(json.dumps(item, ensure_ascii=False))
    else:
        print(json.dumps(payload, ensure_ascii=False))


def _snapshot_personal_database(source: Path, destination: Path) -> None:
    # Screenshot rendering is observational: never migrate or lock the user's
    # source DB, including legacy files still owned by a root-run container.
    if not source.exists():
        return
    uri = f"file:{source.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True, timeout=10) as source_con:
        with sqlite3.connect(destination) as destination_con:
            source_con.backup(destination_con)


def capture_screenshot(
    canonical_db: Path,
    personal_db: Path,
    output: Path,
    width: int,
    height: int,
    screen: str = "home",
) -> dict:
    browser = shutil.which("chromium") or shutil.which("chromium-browser") or shutil.which("google-chrome")
    if browser is None:
        raise RuntimeError("Chromium is not installed; use the compose 'ui' service")

    output = output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]

    with tempfile.TemporaryDirectory(prefix="medicine-screenshot-") as temp_dir:
        snapshot_db = Path(temp_dir) / "personal.sqlite"
        _snapshot_personal_database(personal_db, snapshot_db)
        command = [
            MEDICINE_CORE_WEB_BINARY,
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--canonical-db",
            str(canonical_db.resolve()),
            "--personal-db",
            str(snapshot_db),
            "--static-dir",
            str(STATIC_DIR.resolve()),
        ]
        ocr_assets = os.environ.get("MEDICINE_OCR_ASSETS_DIR")
        if ocr_assets:
            command.extend(["--ocr-assets-dir", ocr_assets])
        server = subprocess.Popen(
            command,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        base_url = f"http://127.0.0.1:{port}"
        url = f"{base_url}/?screen={screen}"
        try:
            deadline = time.monotonic() + 10
            while True:
                if server.poll() is not None:
                    raise RuntimeError("temporary Rust web server exited before screenshot")
                try:
                    with urllib.request.urlopen(f"{base_url}/api/health", timeout=0.5) as response:
                        if response.status == 200:
                            break
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise RuntimeError("temporary Rust web server did not become ready")
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

    return {"path": str(output), "width": width, "height": height, "screen": screen, "size_bytes": output.stat().st_size}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="medicine-app", description="Headless control CLI for the medication app")
    parser.add_argument("--canonical-db", type=Path, default=DEFAULT_CANONICAL_DB)
    parser.add_argument("--personal-db", type=Path, default=DEFAULT_PERSONAL_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    people = sub.add_parser("people")
    people.add_argument("--json", action="store_true")

    person_add = sub.add_parser("person-add")
    person_add.add_argument("--name", required=True)
    person_add.add_argument("--birth-date", required=True)
    person_add.add_argument("--sex", choices=["female", "male"], required=True)
    person_add.add_argument("--pregnancy-status", default="unknown")
    person_add.add_argument("--lactation-status", default="unknown")
    person_add.add_argument("--json", action="store_true")

    person_update = sub.add_parser("person-update")
    person_update.add_argument("--person", required=True)
    person_update.add_argument("--name", required=True)
    person_update.add_argument("--birth-date", required=True)
    person_update.add_argument("--sex", required=True)
    person_update.add_argument("--pregnancy-status", required=True)
    person_update.add_argument("--lactation-status", required=True)
    person_update.add_argument("--json", action="store_true")

    person_delete = sub.add_parser("person-delete")
    person_delete.add_argument("--person", required=True)
    person_delete.add_argument("--json", action="store_true")

    search = sub.add_parser("drug-search")
    search.add_argument("term")
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--include-inactive", action="store_true")
    search.add_argument("--explain-matches", action="store_true")
    search.add_argument("--json", action="store_true")

    meds = sub.add_parser("meds")
    meds.add_argument("--person", required=True)
    meds.add_argument("--date")
    meds.add_argument("--json", action="store_true")

    preview = sub.add_parser("risk-preview")
    preview.add_argument("--person", required=True)
    preview.add_argument("--product-ref", "--product-code", dest="product_ref", required=True)
    preview.add_argument("--dose-amount", type=float)
    preview.add_argument("--dose-unit")
    preview.add_argument("--frequency", type=int)
    preview.add_argument("--meal-relation", default="unspecified")
    preview.add_argument("--route", default="unknown")
    preview.add_argument("--prn", action="store_true")
    preview.add_argument("--prn-max", type=int)
    preview.add_argument("--days", type=int)
    preview.add_argument("--long-term", action="store_true")
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
    add.add_argument("--route", default="unknown")
    add.add_argument("--prn", action="store_true")
    add.add_argument("--prn-max", type=int)
    add.add_argument("--days", type=int)
    add.add_argument("--long-term", action="store_true")
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
    update.add_argument("--prn-max", type=int)
    update.add_argument("--days", type=int)
    long_term = update.add_mutually_exclusive_group()
    long_term.add_argument("--long-term", dest="long_term", action="store_true")
    long_term.add_argument("--bounded", dest="long_term", action="store_false")
    update.set_defaults(long_term=None)
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

    instance_cancel = sub.add_parser("dose-instance-cancel")
    instance_cancel.add_argument("--instance", required=True)
    instance_cancel.add_argument("--json", action="store_true")

    prn_intake = sub.add_parser("prn-intake")
    prn_intake.add_argument("--medication", required=True)
    prn_intake.add_argument("--at")
    prn_intake.add_argument("--note")
    prn_intake.add_argument("--request-id", required=True)
    prn_intake.add_argument("--json", action="store_true")

    screenshot = sub.add_parser("screenshot")
    screenshot.add_argument("--output", type=Path, default=Path("data/debug/mobile.png"))
    screenshot.add_argument("--width", type=int, default=390)
    screenshot.add_argument("--height", type=int, default=844)
    screenshot.add_argument("--screen", choices=["home", "meds", "search", "people"], default="home")
    screenshot.add_argument("--json", action="store_true")


    return parser


def _native_json(arguments: list[str]) -> dict:
    command = [MEDICINE_CORE_BINARY, *arguments, "--json"]
    completed = subprocess.run(
        command,
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.strip() or completed.stdout.strip() or "medicine-core failed"
        raise RuntimeError(detail)
    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        raise RuntimeError("medicine-core returned invalid JSON") from exc
    if not isinstance(payload, dict) or not isinstance(payload.get("status"), int) or "body" not in payload:
        raise RuntimeError("medicine-core returned an invalid response envelope")
    return payload


def _initialize_personal_database(personal_db: Path) -> None:
    personal_db.parent.mkdir(parents=True, exist_ok=True)
    response = _native_json(["personal-schema", "--personal-db", str(personal_db)])
    if not 200 <= response["status"] < 300:
        raise RuntimeError(str(response["body"].get("detail", "personal database initialization failed")))


def _native_request(
    args,
    method: str,
    path: str,
    body: dict | None = None,
) -> dict:
    command = [
        "request",
        method,
        path,
        "--canonical-db",
        str(args.canonical_db),
        "--personal-db",
        str(args.personal_db),
    ]
    if body is not None:
        command.extend(["--body", json.dumps(body, ensure_ascii=False, separators=(",", ":"))])
    return _native_json(command)


def _optional_values(values: dict) -> dict:
    return {key: value for key, value in values.items() if value is not None}


def _dispatch_native(args) -> tuple[dict, bool]:
    _initialize_personal_database(args.personal_db)

    if args.command == "people":
        return _native_request(args, "GET", "/api/people"), False
    if args.command == "person-add":
        return _native_request(args, "POST", "/api/people", {
            "name": args.name,
            "birth_date": args.birth_date,
            "sex": args.sex,
            "pregnancy_status": args.pregnancy_status,
            "lactation_status": args.lactation_status,
        }), False
    if args.command == "person-update":
        person = quote(args.person, safe="")
        return _native_request(args, "PATCH", f"/api/people/{person}", {
            "name": args.name,
            "birth_date": args.birth_date,
            "sex": args.sex,
            "pregnancy_status": args.pregnancy_status,
            "lactation_status": args.lactation_status,
        }), False
    if args.command == "person-delete":
        person = quote(args.person, safe="")
        return _native_request(args, "DELETE", f"/api/people/{person}"), False
    if args.command == "drug-search":
        query = urlencode({
            "q": args.term,
            "limit": args.limit,
            "include_inactive": "true" if args.include_inactive else "false",
        })
        return _native_request(args, "GET", f"/api/products?{query}"), False
    if args.command == "meds":
        person = quote(args.person, safe="")
        query = "" if args.date is None else f"?{urlencode({'date': args.date})}"
        return _native_request(args, "GET", f"/api/people/{person}/medications{query}"), False
    if args.command == "risk-preview":
        person = quote(args.person, safe="")
        payload = _optional_values({
            "product_ref": args.product_ref,
            "dose_amount": args.dose_amount,
            "dose_unit": args.dose_unit,
            "frequency_per_day": args.frequency,
            "meal_relation": args.meal_relation,
            "administration_route": args.route,
            "as_needed": args.prn,
            "prn_max_per_day": args.prn_max,
            "prescription_days": args.days,
            "long_term": args.long_term,
            "schedule_times": args.time,
            "start_date": args.start_date,
            "end_date": args.end_date,
        })
        return _native_request(args, "POST", f"/api/people/{person}/medications/preview", payload), False
    if args.command == "med-add":
        person = quote(args.person, safe="")
        payload = _optional_values({
            "product_ref": args.product_ref,
            "dosage_text": args.dose,
            "dose_amount": args.dose_amount,
            "dose_unit": args.dose_unit,
            "frequency_per_day": args.frequency,
            "meal_relation": args.meal_relation,
            "administration_route": args.route,
            "as_needed": args.prn,
            "prn_max_per_day": args.prn_max,
            "prescription_days": args.days,
            "long_term": args.long_term,
            "schedule_times": args.time,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "request_id": args.request_id,
            "acknowledge_warnings": args.acknowledge_warnings,
            "warning_token": args.warning_token,
        })
        return _native_request(args, "POST", f"/api/people/{person}/medications", payload), False
    if args.command == "med-update":
        medication = quote(args.medication, safe="")
        payload = _optional_values({
            "expected_revision": args.expected_revision,
            "dosage_text": args.dose,
            "dose_amount": args.dose_amount,
            "dose_unit": args.dose_unit,
            "frequency_per_day": args.frequency,
            "meal_relation": args.meal_relation,
            "administration_route": args.route,
            "as_needed": args.as_needed,
            "prn_max_per_day": args.prn_max,
            "prescription_days": args.days,
            "long_term": args.long_term,
            "schedule_times": args.time,
            "start_date": args.start_date,
            "end_date": args.end_date,
            "acknowledge_warnings": args.acknowledge_warnings,
            "warning_token": args.warning_token,
        })
        return _native_request(args, "PATCH", f"/api/medications/{medication}", payload), False
    if args.command == "med-history":
        medication = quote(args.medication, safe="")
        return _native_request(args, "GET", f"/api/medications/{medication}/history"), False
    if args.command == "med-stop":
        medication = quote(args.medication, safe="")
        query = urlencode({"expected_revision": args.expected_revision})
        return _native_request(args, "DELETE", f"/api/medications/{medication}?{query}"), False
    if args.command == "daily-plan":
        person = quote(args.person, safe="")
        query = "" if args.date is None else f"?{urlencode({'date': args.date})}"
        return _native_request(args, "GET", f"/api/people/{person}/daily-plan{query}"), False
    if args.command == "dose-instance":
        instance = quote(args.instance, safe="")
        payload = {"status": args.status}
        if args.at is not None:
            payload["occurred_at"] = args.at
        return _native_request(args, "POST", f"/api/dose-instances/{instance}", payload), False
    if args.command == "dose-instance-cancel":
        instance = quote(args.instance, safe="")
        return _native_request(args, "DELETE", f"/api/dose-instances/{instance}/completion"), False
    if args.command == "prn-intake":
        medication = quote(args.medication, safe="")
        payload = _optional_values({
            "request_id": args.request_id,
            "occurred_at": args.at,
            "note": args.note,
        })
        return _native_request(args, "POST", f"/api/medications/{medication}/prn-intakes", payload), False
    raise AssertionError(args.command)


def _exit_code(envelope: dict) -> int:
    status = envelope["status"]
    body = envelope["body"]
    if 200 <= status < 300:
        return 0
    if status == 409 and isinstance(body, dict) and body.get("confirmation_required") is True:
        return 2
    if status == 503 and isinstance(body, dict) and body.get("detail") == "product search engine is not implemented":
        return 3
    return 1


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "screenshot":
        if args.width < 320 or args.height < 480:
            raise SystemExit("screenshot dimensions are too small")
        payload = capture_screenshot(
            args.canonical_db,
            args.personal_db,
            args.output,
            args.width,
            args.height,
            args.screen,
        )
        emit(payload, args.json)
        return 0

    envelope, medications_only = _dispatch_native(args)
    payload = envelope["body"]
    if medications_only and 200 <= envelope["status"] < 300:
        payload = payload["medications"]
    emit(payload, args.json)
    return _exit_code(envelope)


if __name__ == "__main__":
    raise SystemExit(main())
