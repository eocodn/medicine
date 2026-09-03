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

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from medicine_canonical.release import sha256_file
from medicine_canonical.cli import main as canonical_main
from medicine_canonical.reference_contracts.registry import VerifiedContractArtifact
from medicine_canonical.release_signing import ReleaseSigner, verify_signed_envelope
from medicine_canonical.release_window import (
    ContractReleaseCandidate,
    ROOT_KEY,
    build_and_publish_contract_window_from_env,
    publish_contract_window,
    publish_verified_contract_window,
)
from medicine_canonical.release_window_artifacts import load_candidate, prepare_contract
from tests.r2_fakes import FakeS3, TEST_PRIVATE_KEY_PEM, TEST_PUBLIC_KEY_PEM

from tests.test_release_window import ReferenceContractWindowPublisherTestFixture


class ReferenceContractWindowRecoveryTest(ReferenceContractWindowPublisherTestFixture):
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
        events: list[dict[str, object]] = []

        result = self.publish(
            [c1],
            current=1,
            minimum=1,
            sequence=101,
            suffix="repair-b",
            progress=events.append,
        )

        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.verified_root()["release_sequence"], 100)
        self.assertIn((self.bucket, full_key), self.client.objects)
        self.assertEqual(self.client.put_order, [full_key])
        repair_events = [
            event for event in events if event.get("job") == "contract-full-repair-1"
        ]
        self.assertEqual(repair_events[0]["status"], "started")
        self.assertTrue(any(event.get("status") == "checkpoint" for event in repair_events))
        self.assertTrue(
            any(
                event.get("status") == "progress"
                and str(event.get("phase", "")).endswith("_upload")
                and event.get("current") == event.get("total")
                for event in repair_events
            )
        )
        self.assertEqual(repair_events[-1]["status"], "completed")
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
    def test_cli_integrated_reference_publish_keeps_verified_build_in_process(self) -> None:
        stdout = StringIO()
        with mock.patch(
            "medicine_canonical.cli.build_and_publish_contract_window_from_env",
            return_value={"status": "published"},
        ) as publish:
            with redirect_stdout(stdout):
                code = canonical_main([
                    "reference-build-publish-r2",
                    "--db", str(self.root / "canonical.sqlite"),
                    "--contract-dir", str(self.root / "contracts"),
                    "--output-dir", str(self.root / "dist-integrated"),
                    "--retire-previous-contract",
                    "--allow-retired-previous-failure",
                    "--json",
                ])

        self.assertEqual(code, 0)
        self.assertEqual(publish.call_args.args[0], self.root / "canonical.sqlite")
        self.assertEqual(publish.call_args.args[1], self.root / "contracts")
        self.assertTrue(publish.call_args.kwargs["retire_previous_contract"])
        self.assertTrue(publish.call_args.kwargs["allow_previous_failure"])
        self.assertTrue(callable(publish.call_args.kwargs["progress"]))
    def test_integrated_reference_publish_passes_exact_built_artifact_to_verified_path(self) -> None:
        database = self.root / "integrated.sqlite"
        manifest = self.root / "integrated.manifest.json"
        database.write_bytes(b"integrated-contract")
        manifest.write_text("{}", encoding="utf-8")
        artifact = VerifiedContractArtifact(
            contract_major=1,
            database=database,
            manifest=manifest,
            dataset_id="sha256:" + "6" * 64,
            sha256=sha256_file(database),
            size_bytes=database.stat().st_size,
        )
        build_payload = {
            "current_contract_major": 1,
            "minimum_supported_contract_major": 1,
            "contracts": [],
        }

        with (
            mock.patch(
                "medicine_canonical.release_window._publication_context_from_env",
                return_value=(self.bucket, (1,), self.client, self.signer, False, (1,), 1),
            ),
            mock.patch(
                "medicine_canonical.release_window.build_supported_contract_artifacts",
                return_value=(build_payload, [artifact]),
            ) as build,
            mock.patch(
                "medicine_canonical.release_window.release_sequence_from_env",
                return_value=123,
            ),
            mock.patch(
                "medicine_canonical.release_window.trusted_public_keys_from_env",
                return_value={"test-2026": TEST_PUBLIC_KEY_PEM},
            ),
            mock.patch(
                "medicine_canonical.release_window.publish_verified_contract_window",
                return_value={"status": "published", "release_sequence": 123},
            ) as publish,
        ):
            result = build_and_publish_contract_window_from_env(
                self.root / "canonical.sqlite",
                self.root / "contracts",
                self.root / "dist-integrated-direct",
                allow_previous_failure=True,
            )

        self.assertIs(publish.call_args.args[2][0], artifact)
        self.assertEqual(publish.call_args.kwargs["release_sequence"], 123)
        self.assertTrue(build.call_args.kwargs["allow_previous_failure"])
        self.assertIs(result["build"], build_payload)
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
