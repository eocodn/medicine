from __future__ import annotations

import unittest

from medicine_app.product_flags import build_product_flag_checks


class ProductFlagCheckTest(unittest.TestCase):
    def test_additive_caution_is_not_exposed_as_a_runtime_check(self) -> None:
        checks = build_product_flag_checks({
            "product_flags": [
                {"category": "additive_caution", "details": "legacy additive warning"},
                {"category": "split_caution", "details": "분할불가"},
            ],
        })

        self.assertEqual(len(checks), 1)
        self.assertEqual(checks[0]["category"], "split_caution")
        self.assertEqual(checks[0]["details"], "분할불가")


if __name__ == "__main__":
    unittest.main()