from __future__ import annotations

import unittest

from medicine_canonical.dose_criteria import parse_daily_dose_threshold


class DoseCriteriaTest(unittest.TestCase):
    def test_parses_single_mass_threshold_into_mg(self) -> None:
        parsed = parse_daily_dose_threshold("가바펜틴 3,600mg")
        self.assertEqual(parsed, ("3600", "mg", "parsed", None))

    def test_normalizes_grams_and_micrograms(self) -> None:
        self.assertEqual(
            parse_daily_dose_threshold("아미카신 1.5g(역가)"),
            ("1500.0", "mg", "parsed", None),
        )
        self.assertEqual(
            parse_daily_dose_threshold("폴리트로핀델타 24㎍"),
            ("0.024", "mg", "parsed", None),
        )

    def test_normalizes_mfds_korean_mass_units(self) -> None:
        self.assertEqual(
            parse_daily_dose_threshold("트리아졸람 0.25밀리그램"),
            ("0.25", "mg", "parsed", None),
        )
        self.assertEqual(
            parse_daily_dose_threshold("아미카신 1.5그램"),
            ("1500.0", "mg", "parsed", None),
        )
        self.assertEqual(
            parse_daily_dose_threshold("폴리트로핀델타 24마이크로그램"),
            ("0.024", "mg", "parsed", None),
        )

    def test_rejects_multiple_or_conditional_thresholds(self) -> None:
        amount, unit, status, reason = parse_daily_dose_threshold(
            "나프록센 1,250mg 또는 나프록센나트륨 1,350mg"
        )
        self.assertIsNone(amount)
        self.assertIsNone(unit)
        self.assertEqual(status, "not_evaluable")
        self.assertEqual(reason, "dose criterion is not one unconditional numeric threshold")

        amount, unit, status, reason = parse_daily_dose_threshold("빈크리스틴황산염 주1회 2mg")
        self.assertIsNone(amount)
        self.assertIsNone(unit)
        self.assertEqual(status, "not_evaluable")
        self.assertEqual(reason, "dose criterion is not one unconditional numeric threshold")
