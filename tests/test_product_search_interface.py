from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from medicine_app.products import ProductRepository, ProductSearchUnavailable
from tests.test_app_core import make_canonical_db


class ProductSearchInterfaceTest(unittest.TestCase):
    def test_repository_keeps_search_signature_but_has_no_engine(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            canonical_db = Path(temp_dir) / "canonical.sqlite"
            make_canonical_db(canonical_db)
            repo = ProductRepository(canonical_db)
            with self.assertRaisesRegex(ProductSearchUnavailable, "not implemented"):
                repo.search("약A", limit=10, include_inactive=True, explain=True)


if __name__ == "__main__":
    unittest.main()
