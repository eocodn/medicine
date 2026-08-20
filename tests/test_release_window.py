from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from medicine_canonical.release import sha256_file
from medicine_canonical.release_signing import ReleaseSigner, verify_signed_envelope
from medicine_canonical.release_window import (
    ContractReleaseCandidate,
    ROOT_KEY,
    publish_contract_window,
)
from tests.test_release_r2 import FakeS3, TEST_PRIVATE_KEY_PEM, TEST_PUBLIC_KEY_PEM


class ReferenceContractWindowPublisherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.client = FakeS3()
        self.bucket = "medicine-reference"
        self.signer = ReleaseSigner.from_private_pem("test-2026", TEST_PRIVATE_KEY_PEM)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def candidate(
        self,
        major: int,
        name: str,
        data: bytes,
        dataset_id: str,
    ) -> ContractReleaseCandidate:
        db = self.root / f"c{major}-{name}.sqlite"
        manifest = self.root / f"c{major}-{name}.manifest.json"
        db.write_bytes(data)
        manifest.write_text(
            json.dumps(
                {
                    "contract_major": major,
                    "dataset_id": dataset_id,
                    "sha256": sha256_file(db),
                    "size_bytes": db.stat().st_size,
                    "canonical_schema_version": "10",
                    "physical_policy_version": "8",
                }
            ),
            encoding="utf-8",
        )
        return ContractReleaseCandidate(major, db, manifest)

    def publish(
        self,
        candidates: list[ContractReleaseCandidate],
        *,
        current: int,
        minimum: int,
        sequence: int,
        suffix: str,
    ) -> dict:
        return publish_contract_window(
            self.client,
            self.bucket,
            candidates,
            self.root / f"dist-{suffix}",
            signer=self.signer,
            release_sequence=sequence,
            current_contract_major=current,
            minimum_supported_contract_major=minimum,
            created_at=f"2026-08-20T00:{sequence % 60:02d}:00Z",
        )

    def verified_root(self) -> dict:
        raw = self.client.objects[(self.bucket, ROOT_KEY)]["Body"]
        return verify_signed_envelope(raw, {"test-2026": TEST_PUBLIC_KEY_PEM})

    def test_first_contract_publish_uploads_immutable_full_before_signed_root(self) -> None:
        c1 = self.candidate(1, "one", b"A" * 500_000, "sha256:" + "1" * 64)
        result = self.publish([c1], current=1, minimum=1, sequence=100, suffix="first")

        self.assertEqual(result["status"], "published")
        self.assertEqual(self.client.put_order[-1], ROOT_KEY)
        verified = self.verified_root()
        root = verified["manifest"]
        self.assertEqual(root["protocol_version"], 2)
        self.assertEqual(root["current_contract_major"], 1)
        self.assertEqual(root["minimum_supported_contract_major"], 1)
        entry = root["contracts"]["1"]
        self.assertEqual(entry["dataset_id"], "sha256:" + "1" * 64)
        self.assertEqual(entry["target"]["sha256"], sha256_file(c1.database))
        self.assertTrue(entry["full"]["key"].startswith("reference/v2/contracts/1/full/"))

    def test_same_logical_dataset_with_new_physical_artifact_is_a_new_release(self) -> None:
        dataset = "sha256:" + "a" * 64
        first = self.candidate(1, "physical-a", b"A" * 500_000, dataset)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="physical-a")
        second = self.candidate(1, "physical-b", b"A" * 499_999 + b"B", dataset)

        result = self.publish([second], current=1, minimum=1, sequence=101, suffix="physical-b")

        self.assertEqual(result["status"], "published")
        self.assertEqual(self.verified_root()["release_sequence"], 101)
        self.assertEqual(
            self.verified_root()["manifest"]["contracts"]["1"]["dataset_id"],
            dataset,
        )
        self.assertEqual(
            self.verified_root()["manifest"]["contracts"]["1"]["target"]["sha256"],
            sha256_file(second.database),
        )

    def test_unchanged_exact_targets_and_window_do_not_advance_root(self) -> None:
        c1 = self.candidate(1, "same", b"A" * 500_000, "sha256:" + "b" * 64)
        self.publish([c1], current=1, minimum=1, sequence=100, suffix="same-a")
        self.client.put_order.clear()

        result = self.publish([c1], current=1, minimum=1, sequence=101, suffix="same-b")

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.verified_root()["release_sequence"], 100)
        self.assertEqual(self.client.put_order, [])

    def test_window_can_advertise_current_and_previous_contract_from_same_refresh(self) -> None:
        c1 = self.candidate(1, "window", b"A" * 500_000, "sha256:" + "1" * 64)
        c2 = self.candidate(2, "window", b"B" * 500_000, "sha256:" + "2" * 64)

        self.publish([c1, c2], current=2, minimum=1, sequence=200, suffix="window")

        root = self.verified_root()["manifest"]
        self.assertEqual(set(root["contracts"]), {"1", "2"})
        self.assertEqual(root["current_contract_major"], 2)
        self.assertEqual(root["minimum_supported_contract_major"], 1)
        self.assertTrue(root["contracts"]["1"]["full"]["key"].startswith("reference/v2/contracts/1/"))
        self.assertTrue(root["contracts"]["2"]["full"]["key"].startswith("reference/v2/contracts/2/"))

    def test_patches_are_built_only_from_history_of_the_same_contract(self) -> None:
        c1a = self.candidate(1, "base", b"A" * 2_000_000, "sha256:" + "1" * 64)
        c2a = self.candidate(2, "base", b"X" * 2_000_000, "sha256:" + "2" * 64)
        self.publish([c1a, c2a], current=2, minimum=1, sequence=200, suffix="patch-base")
        c1b = self.candidate(
            1,
            "next",
            b"A" * 1_900_000 + b"B" * 100_000,
            "sha256:" + "3" * 64,
        )
        c2b = self.candidate(
            2,
            "next",
            b"X" * 1_900_000 + b"Y" * 100_000,
            "sha256:" + "4" * 64,
        )

        self.publish([c1b, c2b], current=2, minimum=1, sequence=201, suffix="patch-next")

        root = self.verified_root()["manifest"]
        for major in (1, 2):
            entry = root["contracts"][str(major)]
            for patch in entry["patches"]:
                self.assertTrue(patch["key"].startswith(f"reference/v2/contracts/{major}/patch/"))
        self.assertEqual(
            {p["from_sha256"] for p in root["contracts"]["1"]["patches"]},
            {sha256_file(c1a.database)},
        )
        self.assertEqual(
            {p["from_sha256"] for p in root["contracts"]["2"]["patches"]},
            {sha256_file(c2a.database)},
        )

    def test_retiring_old_contract_does_not_delete_its_immutable_artifacts(self) -> None:
        c1 = self.candidate(1, "old", os.urandom(600_000), "sha256:" + "1" * 64)
        c2 = self.candidate(2, "old", os.urandom(600_000), "sha256:" + "2" * 64)
        self.publish([c1, c2], current=2, minimum=1, sequence=200, suffix="retire-base")
        c1_keys = {
            key for bucket, key in self.client.objects
            if bucket == self.bucket and key.startswith("reference/v2/contracts/1/")
        }
        c2next = self.candidate(2, "next", os.urandom(600_000), "sha256:" + "3" * 64)
        c3 = self.candidate(3, "next", os.urandom(600_000), "sha256:" + "4" * 64)

        self.publish([c2next, c3], current=3, minimum=2, sequence=201, suffix="retire-next")

        root = self.verified_root()["manifest"]
        self.assertEqual(set(root["contracts"]), {"2", "3"})
        self.assertTrue(c1_keys)
        self.assertTrue(c1_keys.issubset({key for bucket, key in self.client.objects if bucket == self.bucket}))
        self.assertFalse(any(key.startswith("reference/v2/contracts/1/") for key in self.client.delete_order))

    def test_window_rejects_more_than_current_and_previous(self) -> None:
        c1 = self.candidate(1, "invalid", b"1" * 1000, "sha256:" + "1" * 64)
        c2 = self.candidate(2, "invalid", b"2" * 1000, "sha256:" + "2" * 64)
        c3 = self.candidate(3, "invalid", b"3" * 1000, "sha256:" + "3" * 64)
        with self.assertRaisesRegex(ValueError, "N.*N-1"):
            self.publish([c1, c2, c3], current=3, minimum=1, sequence=300, suffix="invalid")

    def test_current_contract_above_one_must_keep_previous_major_supported(self) -> None:
        c2 = self.candidate(2, "missing-previous", b"2" * 1000, "sha256:" + "2" * 64)

        with self.assertRaisesRegex(ValueError, "current N and previous N-1"):
            self.publish([c2], current=2, minimum=2, sequence=300, suffix="missing-previous")

    def test_signed_support_window_cannot_regress_after_retirement(self) -> None:
        c2 = self.candidate(2, "retired-base", b"2" * 1000, "sha256:" + "2" * 64)
        c3 = self.candidate(3, "retired-base", b"3" * 1000, "sha256:" + "3" * 64)
        self.publish([c2, c3], current=3, minimum=2, sequence=300, suffix="retired-base")
        c1 = self.candidate(1, "regress", b"1" * 1000, "sha256:" + "1" * 64)
        c2next = self.candidate(2, "regress", b"4" * 1000, "sha256:" + "4" * 64)

        with self.assertRaisesRegex(ValueError, "support window cannot move backward"):
            self.publish([c1, c2next], current=2, minimum=1, sequence=301, suffix="regress")


if __name__ == "__main__":
    unittest.main()