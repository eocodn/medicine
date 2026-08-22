from __future__ import annotations

import io
import inspect
import json
import tempfile
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

    def test_total_count_change_discards_partial_checkpoint_for_clean_retry(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dur.jsonl"

            def changing_fetch(page: int, _page_size: int) -> tuple[list[dict], int]:
                if page == 1:
                    return [{"id": 1}, {"id": 2}], 4
                return [{"id": 3}], 3

            with self.assertRaisesRegex(
                RuntimeError,
                "totalCount changed during sync: 4 -> 3",
            ):
                sync_paginated_jsonl(
                    output,
                    dataset_key="mfds_dur:test",
                    source_family="mfds_dur_item_api",
                    source_locator="https://apis.data.go.kr/example",
                    page_size=2,
                    workers=1,
                    fetch_page=changing_fetch,
                    progress=False,
                )

            self.assertFalse(output.with_name(output.name + ".pages").exists())

            retry_pages: list[int] = []

            def stable_fetch(page: int, _page_size: int) -> tuple[list[dict], int]:
                retry_pages.append(page)
                if page == 1:
                    return [{"id": 1}, {"id": 2}], 3
                return [{"id": 3}], 3

            metadata = sync_paginated_jsonl(
                output,
                dataset_key="mfds_dur:test",
                source_family="mfds_dur_item_api",
                source_locator="https://apis.data.go.kr/example",
                page_size=2,
                workers=1,
                fetch_page=stable_fetch,
                progress=False,
            )

            self.assertEqual(retry_pages, [1, 2])
            self.assertEqual(metadata["row_count"], 3)
            self.assertEqual(
                [json.loads(line)["id"] for line in output.read_text(encoding="utf-8").splitlines()],
                [1, 2, 3],
            )

    def test_transient_page_failure_keeps_checkpoint_for_resume(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            output = Path(temp_dir) / "dur.jsonl"

            def interrupted_fetch(page: int, _page_size: int) -> tuple[list[dict], int]:
                if page == 1:
                    return [{"id": 1}, {"id": 2}], 4
                raise RuntimeError("temporary network failure")

            with self.assertRaisesRegex(RuntimeError, "temporary network failure"):
                sync_paginated_jsonl(
                    output,
                    dataset_key="mfds_dur:test",
                    source_family="mfds_dur_item_api",
                    source_locator="https://apis.data.go.kr/example",
                    page_size=2,
                    workers=1,
                    fetch_page=interrupted_fetch,
                    progress=False,
                )

            self.assertTrue(output.with_name(output.name + ".pages").exists())
            resumed_pages: list[int] = []

            def resumed_fetch(page: int, _page_size: int) -> tuple[list[dict], int]:
                resumed_pages.append(page)
                if page == 1:
                    raise AssertionError("completed page 1 must not be refetched")
                return [{"id": 3}, {"id": 4}], 4

            metadata = sync_paginated_jsonl(
                output,
                dataset_key="mfds_dur:test",
                source_family="mfds_dur_item_api",
                source_locator="https://apis.data.go.kr/example",
                page_size=2,
                workers=1,
                fetch_page=resumed_fetch,
                progress=False,
            )

            self.assertEqual(resumed_pages, [2])
            self.assertEqual(metadata["row_count"], 4)


if __name__ == "__main__":
    unittest.main()
