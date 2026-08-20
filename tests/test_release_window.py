from __future__ import annotations

import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from medicine_canonical.release import sha256_file
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.release_signing import ReleaseSigner, verify_signed_envelope
from medicine_canonical.release_window import (
    ContractReleaseCandidate,
    ROOT_KEY,
    publish_contract_directory_from_env,
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
        verifier=None,
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
        return ContractReleaseCandidate(
            major,
            db,
            manifest,
            verifier=verifier or (lambda _database, _major, _dataset_id: None),
        )

    def publish(
        self,
        candidates: list[ContractReleaseCandidate],
        *,
        current: int,
        minimum: int,
        sequence: int,
        suffix: str,
        allow_early_retirement: bool = False,
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
            allow_early_retirement=allow_early_retirement,
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

    def test_candidate_must_pass_frozen_contract_verifier_before_any_upload(self) -> None:
        def reject(_database, _major, _dataset_id):
            raise ValueError("frozen contract verification failed")

        candidate = self.candidate(
            1,
            "invalid-contract",
            b"not-a-contract-db",
            "sha256:" + "9" * 64,
            verifier=reject,
        )

        with self.assertRaisesRegex(ValueError, "frozen contract verification failed"):
            self.publish([candidate], current=1, minimum=1, sequence=100, suffix="invalid-contract")
        self.assertEqual(self.client.put_order, [])

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

    def test_unchanged_publish_repairs_missing_current_full_without_advancing_root(self) -> None:
        c1 = self.candidate(1, "repair", b"R" * 500_000, "sha256:" + "c" * 64)
        self.publish([c1], current=1, minimum=1, sequence=100, suffix="repair-a")
        root = self.verified_root()["manifest"]
        full_key = root["contracts"]["1"]["full"]["key"]
        self.client.objects.pop((self.bucket, full_key))
        self.client.put_order.clear()

        result = self.publish([c1], current=1, minimum=1, sequence=101, suffix="repair-b")

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.verified_root()["release_sequence"], 100)
        self.assertIn((self.bucket, full_key), self.client.objects)
        self.assertEqual(self.client.put_order, [full_key])

    def test_missing_historical_full_does_not_block_changed_target_publish(self) -> None:
        first = self.candidate(1, "missing-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="missing-base")
        previous_root = self.verified_root()["manifest"]
        previous_full = previous_root["contracts"]["1"]["full"]["key"]
        self.client.objects.pop((self.bucket, previous_full))
        second = self.candidate(1, "missing-next", b"B" * 500_000, "sha256:" + "2" * 64)

        result = self.publish([second], current=1, minimum=1, sequence=101, suffix="missing-next")

        self.assertEqual(result["status"], "published")
        verified = self.verified_root()
        self.assertEqual(verified["release_sequence"], 101)
        entry = verified["manifest"]["contracts"]["1"]
        self.assertEqual(entry["target"]["sha256"], sha256_file(second.database))
        self.assertEqual(entry["patches"], [])
        self.assertEqual(entry["history"], [])
        self.assertIn((self.bucket, entry["full"]["key"]), self.client.objects)
        self.assertEqual(
            result["skipped_patch_bases"]["1"],
            [{"key": previous_full, "error": "FakeNotFound"}],
        )

    def test_transient_historical_full_read_failure_aborts_without_deleting_history(self) -> None:
        first = self.candidate(1, "timeout-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="timeout-base")
        previous_root = self.verified_root()["manifest"]
        previous_full = previous_root["contracts"]["1"]["full"]["key"]
        second = self.candidate(1, "timeout-next", b"B" * 500_000, "sha256:" + "2" * 64)
        original_get = self.client.get_object
        failed = False

        def timeout_old_full(**kwargs):
            nonlocal failed
            if kwargs["Key"] == previous_full and not failed:
                failed = True
                raise TimeoutError("simulated transient historical read timeout")
            return original_get(**kwargs)

        self.client.get_object = timeout_old_full
        try:
            with self.assertRaisesRegex(TimeoutError, "transient historical read timeout"):
                self.publish([second], current=1, minimum=1, sequence=101, suffix="timeout-next")
        finally:
            self.client.get_object = original_get

        self.assertEqual(self.verified_root()["release_sequence"], 100)
        self.assertIn((self.bucket, previous_full), self.client.objects)
        self.assertEqual(self.client.delete_order, [])

    def test_corrupt_historical_full_is_skipped_as_conclusively_unusable(self) -> None:
        first = self.candidate(1, "corrupt-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="corrupt-base")
        previous_root = self.verified_root()["manifest"]
        previous_full = previous_root["contracts"]["1"]["full"]["key"]
        self.client.objects[(self.bucket, previous_full)]["Body"] = b"corrupt historical full"
        second = self.candidate(1, "corrupt-next", b"B" * 500_000, "sha256:" + "2" * 64)

        result = self.publish([second], current=1, minimum=1, sequence=101, suffix="corrupt-next")

        self.assertEqual(result["status"], "published")
        self.assertEqual(self.verified_root()["release_sequence"], 101)
        self.assertEqual(self.verified_root()["manifest"]["contracts"]["1"]["history"], [])
        self.assertEqual(
            result["skipped_patch_bases"]["1"],
            [{"key": previous_full, "error": "HistoricalBaseIntegrityError"}],
        )

    def test_immutable_upload_failure_does_not_advance_signed_root(self) -> None:
        first = self.candidate(1, "upload-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="upload-base")
        second = self.candidate(1, "upload-next", b"B" * 500_000, "sha256:" + "2" * 64)
        original_put = self.client.put_object

        def fail_full_once(**kwargs):
            if kwargs["Key"].endswith(f"/{sha256_file(second.database)}.sqlite.gz"):
                self.client.put_object = original_put
                raise RuntimeError("simulated immutable upload failure")
            return original_put(**kwargs)

        self.client.put_object = fail_full_once

        with self.assertRaisesRegex(RuntimeError, "simulated immutable upload failure"):
            self.publish([second], current=1, minimum=1, sequence=101, suffix="upload-next")
        self.assertEqual(self.verified_root()["release_sequence"], 100)

    def test_competing_root_change_prevents_v2_root_commit(self) -> None:
        first = self.candidate(1, "race-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="race-base")
        second = self.candidate(1, "race-next", b"B" * 500_000, "sha256:" + "2" * 64)

        def mutate_root() -> None:
            key = (self.bucket, ROOT_KEY)
            current = dict(self.client.objects[key])
            current["Body"] = current["Body"] + b" "
            self.client.objects[key] = current

        self.client.before_latest_put = mutate_root

        with self.assertRaisesRegex(RuntimeError, "root changed"):
            self.publish([second], current=1, minimum=1, sequence=101, suffix="race-next")
        self.assertEqual(self.client.delete_order, [])

    def test_post_commit_cleanup_failure_is_repaired_by_idempotent_retry(self) -> None:
        first = self.candidate(1, "cleanup-base", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="cleanup-base")
        stale = "reference/v2/contracts/1/patch/" + "a" * 64 + "-" + "b" * 64 + ".mpatch"
        self.client.objects[(self.bucket, stale)] = {
            "Body": b"stale",
            "Metadata": {"sha256": "0" * 64},
            "ContentType": "application/octet-stream",
            "CacheControl": "public, max-age=31536000, immutable",
        }
        self.client.fail_delete_once = stale
        second = self.candidate(1, "cleanup-next", b"B" * 500_000, "sha256:" + "2" * 64)

        with self.assertRaisesRegex(RuntimeError, "retention cleanup failed"):
            self.publish([second], current=1, minimum=1, sequence=101, suffix="cleanup-next")

        self.assertEqual(self.verified_root()["release_sequence"], 101)
        self.assertIn((self.bucket, stale), self.client.objects)

        retried = self.publish([second], current=1, minimum=1, sequence=102, suffix="cleanup-retry")
        self.assertEqual(retried["status"], "unchanged")
        self.assertNotIn((self.bucket, stale), self.client.objects)

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

    def test_early_previous_contract_retirement_requires_explicit_mode(self) -> None:
        c1 = self.candidate(1, "retirement", b"A" * 500_000, "sha256:" + "1" * 64)
        c2 = self.candidate(2, "retirement", b"B" * 500_000, "sha256:" + "2" * 64)
        self.publish([c1, c2], current=2, minimum=1, sequence=200, suffix="retirement-base")

        with self.assertRaisesRegex(ValueError, "explicit retirement"):
            self.publish([c2], current=2, minimum=2, sequence=201, suffix="retirement-implicit")

        result = self.publish(
            [c2],
            current=2,
            minimum=2,
            sequence=201,
            suffix="retirement-explicit",
            allow_early_retirement=True,
        )

        self.assertEqual(result["status"], "published")
        root = self.verified_root()["manifest"]
        self.assertEqual(root["current_contract_major"], 2)
        self.assertEqual(root["minimum_supported_contract_major"], 2)
        self.assertEqual(set(root["contracts"]), {"2"})

        c3 = self.candidate(3, "retirement-next", b"C" * 500_000, "sha256:" + "3" * 64)
        next_result = self.publish(
            [c2, c3], current=3, minimum=2, sequence=202, suffix="retirement-next"
        )
        self.assertEqual(next_result["status"], "published")
        self.assertEqual(self.verified_root()["manifest"]["minimum_supported_contract_major"], 2)

    def test_directory_retirement_mode_selects_only_current_contract_candidate(self) -> None:
        with (
            mock.patch.dict(os.environ, {"R2_BUCKET": self.bucket}),
            mock.patch("medicine_canonical.release_window.supported_contract_majors", return_value=(1, 2)),
            mock.patch(
                "medicine_canonical.release_window.implementation_for",
                return_value=SimpleNamespace(verify=lambda *_args: None),
            ),
            mock.patch("medicine_canonical.release_window.client_from_env", return_value=self.client),
            mock.patch("medicine_canonical.release_window.release_signer_from_env", return_value=self.signer),
            mock.patch("medicine_canonical.release_window.release_sequence_from_env", return_value=201),
            mock.patch("medicine_canonical.release_window.publish_contract_window", return_value={"status": "published"}) as publish,
        ):
            publish_contract_directory_from_env(
                self.root / "contracts",
                self.root / "dist-retirement",
                retire_previous_contract=True,
            )

        candidates = publish.call_args.args[2]
        self.assertEqual([candidate.contract_major for candidate in candidates], [2])
        self.assertEqual(publish.call_args.kwargs["current_contract_major"], 2)
        self.assertEqual(publish.call_args.kwargs["minimum_supported_contract_major"], 2)
        self.assertTrue(publish.call_args.kwargs["allow_early_retirement"])

    def test_directory_publisher_preserves_already_signed_retirement_on_later_runs(self) -> None:
        published_root = {
            "protocol_version": 2,
            "current_contract_major": 2,
            "minimum_supported_contract_major": 2,
            "contracts": {"2": {}},
        }
        with (
            mock.patch.dict(os.environ, {"R2_BUCKET": self.bucket}),
            mock.patch("medicine_canonical.release_window.supported_contract_majors", return_value=(1, 2)),
            mock.patch(
                "medicine_canonical.release_window.implementation_for",
                return_value=SimpleNamespace(verify=lambda *_args: None),
            ),
            mock.patch("medicine_canonical.release_window.client_from_env", return_value=self.client),
            mock.patch("medicine_canonical.release_window.release_signer_from_env", return_value=self.signer),
            mock.patch("medicine_canonical.release_window.release_sequence_from_env", return_value=202),
            mock.patch(
                "medicine_canonical.release_window._read_root",
                return_value=(b"root", '"etag"', published_root, 201),
            ),
            mock.patch("medicine_canonical.release_window.publish_contract_window", return_value={"status": "published"}) as publish,
        ):
            publish_contract_directory_from_env(
                self.root / "contracts",
                self.root / "dist-retirement-followup",
            )

        candidates = publish.call_args.args[2]
        self.assertEqual([candidate.contract_major for candidate in candidates], [2])
        self.assertEqual(publish.call_args.kwargs["minimum_supported_contract_major"], 2)
        self.assertTrue(publish.call_args.kwargs["allow_early_retirement"])

    def test_cli_passes_explicit_retirement_only_for_contract_directory_publish(self) -> None:
        stdout = StringIO()
        with mock.patch(
            "medicine_canonical.cli.publish_contract_directory_from_env",
            return_value={"status": "published"},
        ) as publish:
            with redirect_stdout(stdout):
                code = canonical_main([
                    "release-publish-r2",
                    "--contract-dir", str(self.root / "contracts"),
                    "--retire-previous-contract",
                    "--json",
                ])
        self.assertEqual(code, 0)
        self.assertTrue(publish.call_args.kwargs["retire_previous_contract"])

        with self.assertRaisesRegex(ValueError, "requires --contract-dir"):
            canonical_main(["release-publish-r2", "--retire-previous-contract", "--json"])

    def test_changed_window_repairs_reused_contract_full_before_advancing_root(self) -> None:
        c1 = self.candidate(1, "reuse-repair", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([c1], current=1, minimum=1, sequence=100, suffix="reuse-repair-base")
        previous = self.verified_root()["manifest"]
        c1_full = previous["contracts"]["1"]["full"]["key"]
        self.client.objects.pop((self.bucket, c1_full))
        c2 = self.candidate(2, "reuse-repair", b"B" * 500_000, "sha256:" + "2" * 64)

        result = self.publish(
            [c1, c2],
            current=2,
            minimum=1,
            sequence=200,
            suffix="reuse-repair-next",
        )

        self.assertEqual(result["status"], "published")
        self.assertEqual(self.verified_root()["release_sequence"], 200)
        self.assertIn((self.bucket, c1_full), self.client.objects)

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