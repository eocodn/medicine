from __future__ import annotations

import io
import inspect
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

from medicine_canonical.mfds_sync import request_json, sync_paginated_jsonl


class MfdsSyncEngineTest(unittest.TestCase):
    def test_shared_sync_engine_owns_transport_and_pagination_mechanics(self) -> None:
        self.assertIn("url", inspect.signature(request_json).parameters)
        self.assertIn("output", inspect.signature(sync_paginated_jsonl).parameters)

        product_source = Path("medicine_canonical/sources.py").read_text(encoding="utf-8")
        ingredient_source = Path("medicine_canonical/mfds_ingredient.py").read_text(
            encoding="utf-8"
        )

        self.assertNotIn("def _request_json(", product_source)
        self.assertNotIn("def _sync_paginated_jsonl(", product_source)
        self.assertNotIn("from .sources import _request_json", ingredient_source)
        self.assertNotIn("from .sources import _sync_paginated_jsonl", ingredient_source)
        self.assertIn("from .mfds_sync import request_json, sync_paginated_jsonl", product_source)
        self.assertIn("from .mfds_sync import request_json, sync_paginated_jsonl", ingredient_source)

    def test_nonretryable_http_error_preserves_malformed_envelope_failure(self) -> None:
        error = urllib.error.HTTPError(
            "https://example.invalid",
            400,
            "Bad Request",
            hdrs=None,
            fp=io.BytesIO(b'{"response":[]}'),
        )
        with (
            mock.patch("medicine_canonical.mfds_sync.urllib.request.urlopen", side_effect=error),
            self.assertRaisesRegex(RuntimeError, "returned an invalid response envelope"),
        ):
            request_json("https://example.invalid", label="MFDS permit API", attempts=1)


if __name__ == "__main__":
    unittest.main()
