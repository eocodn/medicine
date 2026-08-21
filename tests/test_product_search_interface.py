from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from medicine_app.mobile_api import MobileApi
from medicine_app.products import ProductRepository, ProductSearchUnavailable
from medicine_app.web import create_web_app
from tests.test_app_core import make_canonical_db


class ProductSearchInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.canonical_db = root / "canonical.sqlite"
        self.personal_db = root / "personal.sqlite"
        make_canonical_db(self.canonical_db)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def test_repository_keeps_search_signature_but_has_no_engine(self) -> None:
        repo = ProductRepository(self.canonical_db)
        for mode in ("manual", "ocr", " OCR "):
            with self.assertRaisesRegex(ProductSearchUnavailable, "not implemented"):
                repo.search("약A", limit=10, include_inactive=True, mode=mode, explain=True)

        with self.assertRaisesRegex(ValueError, "search mode"):
            repo.search("약A", mode="guess")

    def test_web_search_route_remains_and_reports_engine_unavailable(self) -> None:
        client = TestClient(create_web_app(self.canonical_db, self.personal_db))

        for mode in ("manual", "ocr"):
            response = client.get("/api/products", params={"q": "약A", "mode": mode})
            self.assertEqual(response.status_code, 503)
            self.assertIn("not implemented", response.json()["detail"])

    def test_mobile_search_route_remains_and_reports_engine_unavailable(self) -> None:
        api = MobileApi(self.canonical_db, self.personal_db)
        response = json.loads(api.request("GET", "/api/products?q=%EC%95%BDA&mode=ocr"))

        self.assertEqual(response["status"], 503)
        self.assertIn("not implemented", response["body"]["detail"])


if __name__ == "__main__":
    unittest.main()