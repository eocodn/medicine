from __future__ import annotations

import unittest

from medicine_app.interaction_timing import interaction_timing_applies, parse_interaction_timing


class InteractionTimingTest(unittest.TestCase):
    def test_symmetric_hour_separation_is_structured(self) -> None:
        timing = parse_interaction_timing(
            "기능적 신부전에 의해 유산 산성증 촉진 48시간 이내 병용금기",
            "iobitridol",
            "metformin",
        )

        self.assertEqual(timing["status"], "structured")
        self.assertEqual(timing["kind"], "minimum_separation")
        self.assertEqual(timing["hours"], 48)
        self.assertEqual(timing["direction"], "symmetric")

    def test_named_post_course_washout_resolves_direction(self) -> None:
        timing = parse_interaction_timing(
            "Itraconazole 투여 중 및 종료 후 2주 간 해당 성분 투여 금기",
            "apixaban",
            "itraconazole",
        )

        self.assertEqual(timing["status"], "structured")
        self.assertEqual(timing["kind"], "washout_after")
        self.assertEqual(timing["hours"], 14 * 24)
        self.assertEqual(timing["source_side"], "right")

    def test_unresolved_post_stop_rule_is_not_silently_treated_as_overlap_only(self) -> None:
        timing = parse_interaction_timing(
            "St. John's Wort 중단한 직후에는 Nirmatrelvir/Ritonavir 시작할 수 없음",
            "nirmatrelvir+ritonavir",
            "st johns wort",
        )

        self.assertEqual(timing["status"], "not_evaluable")
        self.assertEqual(timing["kind"], "post_course_restriction")

    def test_plain_interaction_applies_only_when_courses_overlap(self) -> None:
        timing = parse_interaction_timing("함께 사용하지 않아야 함", "a", "b")
        first = {"start_date": "2026-08-01", "end_date": "2026-08-07"}
        later = {"start_date": "2026-08-20", "end_date": "2026-08-25"}
        overlap = {"start_date": "2026-08-07", "end_date": "2026-08-10"}

        self.assertFalse(interaction_timing_applies(timing, first, later, candidate_side="left"))
        self.assertTrue(interaction_timing_applies(timing, first, overlap, candidate_side="left"))

    def test_directional_washout_applies_after_source_but_not_before_it(self) -> None:
        timing = parse_interaction_timing(
            "Itraconazole 투여 중 및 종료 후 2주 간 해당 성분 투여 금기",
            "target",
            "itraconazole",
        )
        source = {"start_date": "2026-08-01", "end_date": "2026-08-07"}
        within = {"start_date": "2026-08-20", "end_date": "2026-08-22"}
        after = {"start_date": "2026-08-23", "end_date": "2026-08-25"}
        before = {"start_date": "2026-07-01", "end_date": "2026-07-10"}

        self.assertTrue(interaction_timing_applies(timing, within, source, candidate_side="left"))
        self.assertFalse(interaction_timing_applies(timing, after, source, candidate_side="left"))
        self.assertFalse(interaction_timing_applies(timing, before, source, candidate_side="left"))

    def test_date_only_hour_rule_is_conservative_at_calendar_boundary(self) -> None:
        timing = parse_interaction_timing("24시간 이내 병용금기", "a", "b")
        first = {"start_date": "2026-08-10", "end_date": "2026-08-10"}
        next_day = {"start_date": "2026-08-11", "end_date": "2026-08-11"}
        two_days_later = {"start_date": "2026-08-12", "end_date": "2026-08-12"}

        self.assertTrue(interaction_timing_applies(timing, first, next_day, candidate_side="left"))
        self.assertFalse(interaction_timing_applies(timing, first, two_days_later, candidate_side="left"))

    def test_date_only_36_hour_rule_keeps_two_day_gap_for_review(self) -> None:
        timing = parse_interaction_timing("36시간 이내 병용금기", "a", "b")
        first = {"start_date": "2026-08-10", "end_date": "2026-08-10"}
        two_days_later = {"start_date": "2026-08-12", "end_date": "2026-08-12"}
        three_days_later = {"start_date": "2026-08-13", "end_date": "2026-08-13"}

        self.assertTrue(interaction_timing_applies(timing, first, two_days_later, candidate_side="left"))
        self.assertFalse(interaction_timing_applies(timing, first, three_days_later, candidate_side="left"))


if __name__ == "__main__":
    unittest.main()
