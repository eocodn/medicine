from __future__ import annotations

import unittest

from medicine_canonical.mfds_ingredient import MFDS_INGREDIENT_ENDPOINTS
from medicine_canonical.schema import CORE_SOURCE_FAMILIES
from medicine_canonical.source_policy import EXPECTED_CANONICAL_SOURCE_FAMILIES
from medicine_canonical.sources import DUR_ENDPOINTS, PERMIT_DATASET_KEY


class MfdsOnlySourcePolicyTest(unittest.TestCase):
    def test_authoritative_source_policy_is_mfds_only(self) -> None:
        expected = {
            PERMIT_DATASET_KEY: "mfds_permit_api",
            **{
                f"mfds_dur:{operation}": "mfds_dur_item_api"
                for operation in DUR_ENDPOINTS
            },
            **{
                f"mfds_dur_ingredient:{operation}": "mfds_dur_ingredient_api"
                for operation in MFDS_INGREDIENT_ENDPOINTS
            },
        }
        self.assertEqual(EXPECTED_CANONICAL_SOURCE_FAMILIES, expected)
        self.assertEqual(
            CORE_SOURCE_FAMILIES,
            frozenset(
                {"mfds_permit_api", "mfds_dur_item_api", "mfds_dur_ingredient_api"}
            ),
        )



if __name__ == "__main__":
    unittest.main()
