from __future__ import annotations

import unittest

from medicine_app.mobile_request_policy import classify_mobile_request


class MobileRequestPolicyTest(unittest.TestCase):
    def assert_policy(
        self,
        method: str,
        path: str,
        *,
        access: str,
        requires_reference: bool,
    ) -> None:
        policy = classify_mobile_request(method, path)
        self.assertEqual(policy.access, access)
        self.assertEqual(policy.requires_reference, requires_reference)

    def test_reference_and_personal_access_are_classified_in_one_policy(self) -> None:
        self.assert_policy("GET", "/api/health", access="reference", requires_reference=False)
        self.assert_policy("GET", "/api/products?q=test", access="reference", requires_reference=True)
        self.assert_policy("GET", "/api/people", access="personal_read", requires_reference=False)
        self.assert_policy(
            "GET",
            "/api/medications/m1/history",
            access="personal_read",
            requires_reference=False,
        )
        self.assert_policy(
            "POST",
            "/api/people/p1/medications/preview",
            access="personal_read",
            requires_reference=True,
        )

    def test_reference_requirement_is_independent_from_personal_write_access(self) -> None:
        self.assert_policy(
            "GET",
            "/api/people/p1/dashboard?date=2026-08-20",
            access="personal_write",
            requires_reference=False,
        )
        self.assert_policy(
            "POST",
            "/api/people/p1/medications",
            access="personal_write",
            requires_reference=True,
        )
        self.assert_policy(
            "PATCH",
            "/api/medications/m1",
            access="personal_write",
            requires_reference=True,
        )
        self.assert_policy(
            "DELETE",
            "/api/medications/m1?expected_revision=2",
            access="personal_write",
            requires_reference=False,
        )
        self.assert_policy(
            "POST",
            "/api/dose-instances/d1",
            access="personal_write",
            requires_reference=False,
        )

    def test_unknown_routes_fail_safe_to_personal_write_without_inventing_reference_need(self) -> None:
        self.assert_policy("GET", "/api/unknown", access="personal_write", requires_reference=False)


if __name__ == "__main__":
    unittest.main()