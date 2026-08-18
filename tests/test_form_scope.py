from __future__ import annotations

import unittest

from medicine_canonical.form_scope import mfds_form_scope_applies


class MfdsFormScopeTest(unittest.TestCase):
    def test_comma_qualified_inhaler_forms_are_not_interchangeable(self) -> None:
        self.assertTrue(
            mfds_form_scope_applies("정량흡입제, 분말제", "정량흡입제, 분말제")
        )
        self.assertFalse(
            mfds_form_scope_applies("정량흡입제, 분말제", "정량흡입제, 용액제")
        )

    def test_immediate_tablet_label_does_not_include_modified_release_tablet(self) -> None:
        self.assertTrue(mfds_form_scope_applies("정제", "필름코팅정"))
        self.assertTrue(mfds_form_scope_applies("정제", "나정"))
        self.assertFalse(mfds_form_scope_applies("정제", "서방정"))
        self.assertFalse(mfds_form_scope_applies("정제", "장용정"))

    def test_capsule_with_powder_contents_matches_capsule_not_powder(self) -> None:
        self.assertTrue(mfds_form_scope_applies("캡슐", "경질캡슐제, 산제"))
        self.assertFalse(mfds_form_scope_applies("산제", "경질캡슐제, 산제"))


if __name__ == "__main__":
    unittest.main()
