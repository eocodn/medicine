from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.web import create_web_app
from tests.test_app_core import make_catalog_db, make_dur_db


class WebApiTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.dur_db = root / "dur.sqlite"
        self.personal_db = root / "personal.sqlite"
        self.catalog_db = root / "catalog.sqlite"
        make_dur_db(self.dur_db)
        make_catalog_db(self.catalog_db)
        self.client = TestClient(create_web_app(self.dur_db, self.personal_db))

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_mobile_shell_is_served(self) -> None:
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn('name="viewport"', response.text)
        self.assertIn('class="bottom-nav"', response.text)
        self.assertIn("복용", response.text)
        self.assertIn("약 검색", response.text)
        self.assertIn('id="include-inactive"', response.text)
        self.assertIn('src="/static/native-api.js?v=20260811k"', response.text)
        self.assertIn('src="/static/people.js?v=20260811k"', response.text)
        self.assertIn('href="/static/styles.css?v=20260812a"', response.text)
        self.assertIn('src="/static/timeline.js?v=20260811b"', response.text)
        self.assertIn('src="/static/prescription.js?v=20260811e"', response.text)
        self.assertIn('src="/static/app.js?v=20260811v"', response.text)
        self.assertNotIn("로컬 우선", response.text)
        self.assertNotIn("personal.sqlite", response.text)
        self.assertIn("Content-Security-Policy", response.text)
        self.assertIn('name="lactation_status"', response.text)
        self.assertIn('name="notes" type="hidden"', response.text)
        self.assertIn('class="screen people-screen"', response.text)
        script = self.client.get("/static/app.js")
        self.assertEqual(script.status_code, 200)
        self.assertIn("MedicineLocalApi", script.text)
        self.assertIn("formatDoseText", script.text)
        self.assertIn("mealRelationLabel(item.meal_relation)", script.text)
        self.assertIn("한 번에", script.text)
        self.assertIn("하루", script.text)
        self.assertIn("식사 전", script.text)
        self.assertIn("먹는 약", script.text)
        self.assertIn("일 복용", script.text)
        self.assertIn("매일", script.text)
        self.assertIn("med.dur_alert", script.text)
        self.assertIn('class="dur-alert-badge"', script.text)
        self.assertIn("현재 DUR 주의 항목 보기", script.text)
        self.assertIn('data-dur-alert="${med.id}"', script.text)
        self.assertIn("medication.current_assessment", script.text)
        self.assertIn("[data-dur-alert]", script.text)
        self.assertNotIn("risks: [], coverage: null", script.text)
        self.assertIn("알레르기", script.text)
        self.assertIn("신장·간 기능", script.text)
        self.assertIn("일반약·건강기능식품", script.text)
        styles = self.client.get("/static/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertIn(".course-progress-track", styles.text)
        self.assertIn(".dur-alert-badge", styles.text)
        self.assertIn(".dur-finding { margin: 0; padding: 0; border-top: 0; }", styles.text)
        self.assertIn(".dur-finding + .dur-finding", styles.text)
        self.assertIn("width: 100%", styles.text)
        self.assertIn("warning_token", script.text)
        self.assertIn("openMedicationEdit", script.text)
        self.assertNotIn("변경 이력", script.text)
        self.assertIn("data-instance-cancel", script.text)
        self.assertIn("cancelDoseInstance", script.text)
        self.assertNotIn("data-taken=", script.text)
        self.assertNotIn("function logDose", script.text)
        self.assertIn('type="button">삭제</button>', script.text)
        self.assertIn("기존 복용 기록은 남습니다", script.text)
        self.assertNotIn("복용 목록에서 종료", script.text)
        self.assertNotIn("개인 기록은 로컬 DB에 저장 중", script.text)
        self.assertIn('data-product-select=', script.text)
        self.assertIn("selectProductResult", script.text)
        self.assertIn('name="birth_date" type="date" min="1000-01-01"', response.text)
        people_script = self.client.get("/static/people.js")
        self.assertEqual(people_script.status_code, 200)
        self.assertNotIn("변경 이력", people_script.text)
        self.assertIn("const formElement = event.currentTarget", people_script.text)
        self.assertIn("formElement.reset()", people_script.text)
        self.assertNotIn("event.currentTarget.reset()", people_script.text)
        self.assertIn("birthInput.max = todayInKorea()", people_script.text)
        self.assertIn("deletePerson", people_script.text)
        self.assertIn("not_applicable", people_script.text)
        self.assertIn('form.elements.notes.value = person.notes || ""', people_script.text)
        self.assertIn('class="person-select" role="button" tabindex="0"', people_script.text)
        self.assertNotIn('<button class="person-select"', people_script.text)
        styles = self.client.get("/static/styles.css")
        self.assertEqual(styles.status_code, 200)
        self.assertNotIn("--bottom-nav-clearance", styles.text)
        self.assertIn("grid-template-rows: auto minmax(0, 1fr) auto", styles.text)
        self.assertIn("height: 100dvh", styles.text)
        self.assertIn("overflow: hidden", styles.text)
        self.assertIn(".main-content", styles.text)
        self.assertIn("min-height: 0", styles.text)
        self.assertIn(".screen.active", styles.text)
        self.assertIn(".people-screen", styles.text)
        self.assertIn("#people-list", styles.text)
        self.assertIn("align-content: start", styles.text)
        self.assertIn("grid-auto-rows: max-content", styles.text)
        self.assertIn("overflow-y: auto", styles.text)
        self.assertIn("scrollbar-width: none", styles.text)
        self.assertIn(".screen.active::-webkit-scrollbar", styles.text)
        self.assertIn("#people-list::-webkit-scrollbar", styles.text)
        self.assertIn(".bottom-sheet::-webkit-scrollbar", styles.text)
        self.assertIn("overflow-wrap: anywhere", styles.text)
        self.assertIn("position: static", styles.text)
        self.assertNotIn("position: fixed;\n  z-index: 20;\n  left: 50%;", styles.text)
        prescription_script = self.client.get("/static/prescription.js")
        self.assertEqual(prescription_script.status_code, 200)
        self.assertIn("reviewPrescriptionDraft", prescription_script.text)
        self.assertIn("hasClearDurCoverage", prescription_script.text)
        self.assertIn("durStatusHtml", prescription_script.text)
        self.assertIn("durChecks.length === 8", prescription_script.text)
        self.assertNotIn("coverageLimitHtml", prescription_script.text)
        self.assertNotIn("제품 단위 DUR 매핑 실패", prescription_script.text)
        self.assertNotIn("자동 확인이 제한된 항목", prescription_script.text)
        self.assertIn("확인된 DUR 경고가 없어 바로 저장합니다", prescription_script.text)
        app_script = self.client.get("/static/app.js")
        self.assertIn("clearDurCoverage", app_script.text)
        self.assertIn("if (reviewRequired) return", app_script.text)
        timeline_script = self.client.get("/static/timeline.js")
        self.assertEqual(timeline_script.status_code, 200)
        self.assertIn("medicationCourseHtml", timeline_script.text)
        self.assertIn("/static/timeline.js", response.text)
        self.assertIn("하루 복용 횟수와 입력한 복용 시간 개수가 같아야 해요", prescription_script.text)
        self.assertNotIn("<details", prescription_script.text)
        native_api = self.client.get("/static/native-api.js")
        self.assertEqual(native_api.status_code, 200)
        self.assertIn("MedicineNative.request", native_api.text)
        self.assertNotIn("요청 실패 (", native_api.text)
        self.assertNotIn("요청 실패 (", script.text)

    def test_ocr_review_contract_is_exposed_without_raw_artifacts(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn('id="ocr-scan-button"', page.text)
        self.assertIn('id="ocr-status"', page.text)
        self.assertIn("브라우저 안에서 사진을 인식할 수 있어요. 사진은 서버로 전송되지 않아요.", page.text)
        self.assertNotIn("기기 내 OCR 기능을 확인하는 중", page.text)
        self.assertIn('src="/static/ocr.js?v=20260811k"', page.text)

        ocr = self.client.get("/static/ocr.js")
        self.assertEqual(ocr.status_code, 200)
        for command in ("get_capabilities", "start_scan", "cancel_scan", "finish_scan"):
            self.assertIn(command, ocr.text)
        for state in ("accepted", "scanner_ready", "scanning", "recognizing", "review_required", "cancelled", "failed", "expired"):
            self.assertIn(state, ocr.text)
        self.assertIn("schema_version", ocr.text)
        self.assertIn("operation_id", ocr.text)
        self.assertIn("sequence", ocr.text)
        self.assertNotIn("rawText", ocr.text)
        self.assertNotIn("imageUri", ocr.text)
        self.assertNotIn("pdfUri", ocr.text)
        self.assertNotIn("localFilePath", ocr.text)
        self.assertNotIn("localStorage", ocr.text)
        self.assertNotIn("fetch(", ocr.text)
        self.assertIn("ambiguity_codes", ocr.text)
        self.assertIn("unsupported_codes", ocr.text)
        self.assertIn("ocr-issue", ocr.text)
        self.assertIn("UNSUPPORTED_ROUTE", ocr.text)
        self.assertIn("UNSUPPORTED_PRN", ocr.text)
        self.assertIn("onClear", ocr.text)
        self.assertIn("state.active.issues = null", ocr.text)
        for transient_copy in (
            "처방전 스캔을 시작했어요",
            "카메라를 준비했어요",
            "처방전을 촬영하는 중",
            "처방전 내용을 기기에서 인식하는 중",
            "스캔을 취소하는 중",
            "스캔을 취소했어요",
        ):
            self.assertNotIn(transient_copy, ocr.text)

        app = self.client.get("/static/app.js")
        self.assertEqual(app.status_code, 200)
        self.assertIn("MedicineOcr", app.text)
        self.assertIn("MedicineOcr.getReview", app.text)
        self.assertIn("MedicineOcr.clearReviewToken", app.text)
        self.assertIn("사진에서 불러온 처방 확인 시간이 지났어요", app.text)
        self.assertIn("ocr-preview", app.text)
        self.assertIn("ocr_review_token", app.text)
        self.assertIn("reviewPrescriptionDraft", app.text)
        self.assertIn("renderReview", app.text)
        self.assertIn("reviewToken = null", ocr.text)
        self.assertIn("여러 약명 인식", ocr.text)
        self.assertIn("품목별 확인", ocr.text)
        for transient_copy in (
            "오늘 복용 완료로 기록했어요",
            "복용 완료를 취소했어요",
            "복용약 목록에서 삭제했어요",
            "처방 정보를 수정했어요",
            "복용약에 추가했어요",
            "전체 허가 의약품에서 찾는 중",
            "전체 허가 의약품 카탈로그를 확인하는 중",
            "식약처 허가상태 + DUR 연결",
            "초기화 실패:",
        ):
            self.assertNotIn(transient_copy, app.text)
        people = self.client.get("/static/people.js")
        for transient_copy in (
            "님으로 전환했어요",
            "님 정보를 수정했어요",
            "님 프로필을 만들었어요",
            "님의 관리 데이터를 삭제했어요",
        ):
            self.assertNotIn(transient_copy, people.text)
        self.assertLessEqual(len(app.text.splitlines()), 600)

    def test_browser_ocr_is_self_hosted_and_keeps_images_out_of_app_requests(self) -> None:
        page = self.client.get("/")
        self.assertEqual(page.status_code, 200)
        policy = page.headers.get("content-security-policy", "")
        self.assertIn("connect-src 'self'", policy)
        self.assertIn("worker-src 'self' blob:", policy)
        self.assertIn("script-src 'self' 'wasm-unsafe-eval'", policy)
        self.assertNotIn("'unsafe-eval'", policy)
        self.assertNotIn("https:", policy)
        self.assertNotIn("content-security-policy", self.client.get("/api/health").headers)
        self.assertIn('id="ocr-image-input"', page.text)
        self.assertIn('accept="image/*"', page.text)
        self.assertIn('capture="environment"', page.text)
        for source in (
            "/static/browser-ocr-parser.js",
        ):
            self.assertIn(f'src="{source}"', page.text)
        self.assertIn('src="/static/browser-ocr.js?v=20260811k"', page.text)
        self.assertNotIn("tesseract.min.js", page.text)

        for asset in (
            "/ocr-assets/direct/ocr-worker.js",
            "/ocr-assets/ort/ort-wasm-simd-threaded.mjs",
            "/ocr-assets/ort/ort-wasm-simd-threaded.wasm",
            "/ocr-assets/models/detection.onnx",
            "/ocr-assets/models/korean-recognition.onnx",
            "/ocr-assets/models/korean-recognition-dictionary.json",
        ):
            response = self.client.get(asset)
            self.assertEqual(response.status_code, 200, asset)
            self.assertTrue(response.content, asset)
        browser = self.client.get("/static/browser-ocr.js")
        self.assertEqual(browser.status_code, 200)
        for contract in (
            "MedicineBrowserOcr", "direct-onnx-wasm-cpu",
            "korean_PP-OCRv5_mobile_rec", "120_000", "worker.terminate()",
        ):
            self.assertIn(contract, browser.text)
        for forbidden in ("FormData", "XMLHttpRequest", "sendBeacon", "localStorage", "sessionStorage"):
            self.assertNotIn(forbidden, browser.text)
        self.assertIn('backend: "wasm"', browser.text)
        self.assertNotIn("PaddleOCR", browser.text)
        self.assertNotIn("OpenCV", browser.text)
        self.assertNotIn("Tesseract", browser.text)

        bridge = self.client.get("/static/ocr.js")
        self.assertIn("MedicineBrowserOcr", bridge.text)

    def test_product_search_can_include_inactive_permit_records(self) -> None:
        default = self.client.get("/api/products", params={"q": "과거취하약"})
        self.assertEqual(default.status_code, 200)
        self.assertEqual(default.json(), [])

        response = self.client.get(
            "/api/products",
            params={"q": "과거취하약", "include_inactive": "true"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()[0]["permit_status"], "withdrawn")
        self.assertEqual(response.json()[0]["permit_status_name"], "취하")

    def test_web_app_requires_canonical_reference_database(self) -> None:
        missing = self.dur_db.with_name("missing-canonical.sqlite")
        with self.assertRaisesRegex(FileNotFoundError, "canonical database not found"):
            create_web_app(missing, self.personal_db.with_name("other.sqlite"))

    def test_person_search_preview_add_and_log_flow(self) -> None:
        person_response = self.client.post(
            "/api/people",
            json={
                "name": "테스트",
                "birth_date": "2010-01-10",
                "sex": "female",
                "pregnancy_status": "pregnant",
            },
        )
        self.assertEqual(person_response.status_code, 201)
        person = person_response.json()

        search = self.client.get("/api/products", params={"q": "약B"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["product_code"], "MFDS-B")

        current_warning = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={"product_code": "MFDS-A", "schedule_times": ["08:00"]},
        )
        self.assertEqual(current_warning.status_code, 409)
        current_added = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={
                "product_code": "MFDS-A", "schedule_times": ["08:00"],
                "acknowledge_warnings": True,
                "warning_token": current_warning.json()["warning_token"],
            },
        )
        self.assertEqual(current_added.status_code, 201)
        preview = self.client.post(
            f"/api/people/{person['id']}/medications/preview",
            json={"product_code": "MFDS-B"},
        )
        self.assertEqual(preview.status_code, 200)
        self.assertIn("combination_contraindication", {r["type"] for r in preview.json()["risks"]})

        added = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={"product_code": "MFDS-B", "dosage_text": "1정", "schedule_times": ["20:00"]},
        )
        self.assertEqual(added.status_code, 409)
        warning = added.json()
        acknowledged = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={
                "product_code": "MFDS-B", "dosage_text": "1정", "schedule_times": ["20:00"],
                "acknowledge_warnings": True, "warning_token": warning["warning_token"],
            },
        )
        self.assertEqual(acknowledged.status_code, 201)
        medication = acknowledged.json()

        plan = self.client.get(f"/api/people/{person['id']}/daily-plan").json()
        instance = next(
            dose for dose in plan["doses"] if dose["medication_id"] == medication["id"]
        )
        logged = self.client.post(
            f"/api/dose-instances/{instance['id']}",
            json={"status": "taken"},
        )
        self.assertEqual(logged.status_code, 200)

        legacy_log = self.client.post(
            f"/api/medications/{medication['id']}/logs",
            json={"status": "taken"},
        )
        self.assertEqual(legacy_log.status_code, 404)

        dashboard = self.client.get(f"/api/people/{person['id']}/dashboard")
        self.assertEqual(dashboard.status_code, 200)
        body = dashboard.json()
        self.assertEqual(len(body["medications"]), 2)
        self.assertEqual(len(body["recent_logs"]), 1)

    def test_person_profile_can_update_lactation_and_be_deleted(self) -> None:
        created = self.client.post(
            "/api/people",
            json={
                "name": "프로필",
                "birth_date": "1990-01-01",
                "sex": "female",
                "pregnancy_status": "not_pregnant",
                "lactation_status": "unknown",
            },
        )
        self.assertEqual(created.status_code, 201)
        person = created.json()

        updated = self.client.patch(
            f"/api/people/{person['id']}",
            json={
                "name": "프로필",
                "birth_date": "1990-01-01",
                "sex": "female",
                "pregnancy_status": "not_pregnant",
                "lactation_status": "breastfeeding",
            },
        )
        self.assertEqual(updated.status_code, 200)
        self.assertEqual(updated.json()["lactation_status"], "breastfeeding")

        deleted = self.client.delete(f"/api/people/{person['id']}")
        self.assertEqual(deleted.status_code, 200)
        self.assertEqual(deleted.json(), {"id": person["id"], "deleted": True})
        self.assertEqual(self.client.get("/api/people").json(), [])

    def test_structured_prescription_and_daily_plan_api(self) -> None:
        person = self.client.post(
            "/api/people",
            json={"name": "일정", "birth_date": "1990-01-01", "sex": "female", "pregnancy_status": "not_pregnant"},
        ).json()

        search = self.client.get("/api/products", params={"q": "전체카탈로그약B"})
        self.assertEqual(search.status_code, 200)
        self.assertEqual(search.json()[0]["product_ref"], "MFDS-B")

        added = self.client.post(
            f"/api/people/{person['id']}/medications",
            json={
                "product_ref": "MFDS-B",
                "dose_amount": 1,
                "dose_unit": "정",
                "frequency_per_day": 2,
                "meal_relation": "after_meal",
                "administration_route": "oral",
                "prescription_days": 2,
                "start_date": "2026-08-10",
                "schedule_times": ["08:00", "20:00"],
            },
        )
        self.assertEqual(added.status_code, 201)
        self.assertEqual(added.json()["end_date"], "2026-08-11")

        plan = self.client.get(f"/api/people/{person['id']}/daily-plan", params={"date": "2026-08-10"})
        self.assertEqual(plan.status_code, 200)
        self.assertEqual(len(plan.json()["doses"]), 2)
        self.assertEqual(plan.json()["doses"][0]["dose_text"], "1정")
        self.assertEqual(plan.json()["doses"][0]["meal_relation"], "after_meal")

        instance_id = plan.json()["doses"][0]["id"]
        completed = self.client.post(
            f"/api/dose-instances/{instance_id}",
            json={"status": "taken", "occurred_at": "2026-08-10T08:01:00+09:00"},
        )
        self.assertEqual(completed.status_code, 200)
        self.assertEqual(completed.json()["status"], "taken")

        dashboard = self.client.get(f"/api/people/{person['id']}/dashboard", params={"date": "2026-08-10"})
        self.assertEqual(dashboard.json()["daily_plan"]["summary"]["taken"], 1)
        self.assertEqual(dashboard.json()["medications"][0]["course_progress"]["remaining_days"], 1)


if __name__ == "__main__":
    unittest.main()
