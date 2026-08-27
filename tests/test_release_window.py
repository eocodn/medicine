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
    publish_contract_directory_from_env,
    publish_contract_window,
    publish_verified_contract_window,
)
from medicine_canonical.release_window_artifacts import load_candidate, prepare_contract
from tests.r2_fakes import FakeS3, TEST_PRIVATE_KEY_PEM, TEST_PUBLIC_KEY_PEM

class ReferenceContractWindowPublisherTestFixture(unittest.TestCase):
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
        progress=None,
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
            trusted_public_keys={"test-2026": TEST_PUBLIC_KEY_PEM},
            created_at=f"2026-08-20T00:{sequence % 60:02d}:00Z",
            allow_early_retirement=allow_early_retirement,
            progress=progress,
        )
    def verified_root(self) -> dict:
        raw = self.client.objects[(self.bucket, ROOT_KEY)]["Body"]
        return verify_signed_envelope(raw, {"test-2026": TEST_PUBLIC_KEY_PEM})

class ReferenceContractWindowPublisherTest(ReferenceContractWindowPublisherTestFixture):
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
    def test_contract_window_publish_threads_artifact_lifecycle_progress(self) -> None:
        c1 = self.candidate(1, "progress", b"P" * 500_000, "sha256:" + "1" * 64)
        events: list[dict[str, object]] = []

        result = publish_contract_window(
            self.client,
            self.bucket,
            [c1],
            self.root / "dist-progress",
            signer=self.signer,
            release_sequence=100,
            current_contract_major=1,
            minimum_supported_contract_major=1,
            trusted_public_keys={"test-2026": TEST_PUBLIC_KEY_PEM},
            created_at="2026-08-20T00:00:00Z",
            progress=events.append,
        )

        self.assertEqual(result["status"], "published")
        contract_events = [
            event for event in events if event.get("job") == "contract-release-prepare-1"
        ]
        self.assertEqual(contract_events[0]["status"], "started")
        self.assertTrue(any(event.get("status") == "checkpoint" for event in contract_events))
        self.assertEqual(contract_events[-1]["status"], "completed")
        transfer_events = [
            event for event in events if event.get("job") == "reference-publish-transfer"
        ]
        self.assertEqual(transfer_events[0]["status"], "started")
        self.assertTrue(
            any(
                event.get("status") == "progress"
                and str(event.get("phase", "")).endswith("_upload")
                and event.get("current") == event.get("total")
                and isinstance(event.get("bar"), str)
                for event in transfer_events
            )
        )
        self.assertTrue(any(event.get("status") == "checkpoint" for event in transfer_events))
        self.assertEqual(transfer_events[-1]["status"], "completed")
    def test_contract_artifact_preparation_resumes_full_without_recompression(self) -> None:
        first = self.candidate(1, "resume-first", b"A" * 500_000, "sha256:" + "1" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="resume-seed")
        previous = self.verified_root()["manifest"]["contracts"]["1"]
        second = self.candidate(
            1,
            "resume-second",
            b"A" * 450_000 + b"B" * 50_000,
            "sha256:" + "2" * 64,
        )
        metadata = load_candidate(second)
        output = self.root / "dist-resume-contract"
        temporary = self.root / "tmp-resume-contract"
        temporary.mkdir()
        events: list[dict[str, object]] = []

        with mock.patch(
            "medicine_canonical.release_window_artifacts.create_chunk_patch",
            side_effect=RuntimeError("historical patch interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "historical patch interrupted"):
                prepare_contract(
                    self.client,
                    self.bucket,
                    metadata,
                    previous,
                    output,
                    temporary,
                    progress=events.append,
                )

        checkpoint = output / "reference/v2/contracts/1/.prepare.checkpoint.json"
        full = output / (
            f"reference/v2/contracts/1/full/{metadata.target_sha256}.sqlite.gz"
        )
        self.assertTrue(checkpoint.is_file())
        self.assertTrue(full.is_file())
        self.assertTrue(
            any(
                event.get("job") == "contract-release-prepare-1"
                and event.get("status") == "checkpoint"
                and event.get("phase") == "full"
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.get("status") == "progress"
                and event.get("phase") == "base_download_1"
                and event.get("current") == event.get("total")
                and isinstance(event.get("bar"), str)
                for event in events
            )
        )
        self.assertTrue(
            any(
                event.get("status") == "progress"
                and event.get("phase") == "base_decompress_1"
                and event.get("current") == event.get("total")
                and isinstance(event.get("bar"), str)
                for event in events
            )
        )

        retry_tmp = self.root / "tmp-resume-contract-retry"
        retry_tmp.mkdir()
        with mock.patch(
            "medicine_canonical.release_window_artifacts.compress_snapshot",
            side_effect=AssertionError("completed v2 full must not be recompressed"),
        ):
            prepared = prepare_contract(
                self.client,
                self.bucket,
                metadata,
                previous,
                output,
                retry_tmp,
                progress=events.append,
            )

        self.assertFalse(checkpoint.exists())
        self.assertEqual(prepared.entry["dataset_id"], "sha256:" + "2" * 64)
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
    def test_verified_candidate_rebinds_exact_bytes_without_repeating_frozen_verifier(self) -> None:
        candidate = self.candidate(
            1,
            "verified-in-process",
            b"verified" * 80_000,
            "sha256:" + "8" * 64,
            verifier=mock.Mock(side_effect=AssertionError("verifier must not repeat")),
        )
        artifact = VerifiedContractArtifact(
            contract_major=1,
            database=candidate.database,
            manifest=candidate.manifest,
            dataset_id="sha256:" + "8" * 64,
            sha256=sha256_file(candidate.database),
            size_bytes=candidate.database.stat().st_size,
        )

        result = publish_verified_contract_window(
            self.client,
            self.bucket,
            [artifact],
            self.root / "dist-verified-in-process",
            signer=self.signer,
            release_sequence=100,
            current_contract_major=1,
            minimum_supported_contract_major=1,
            trusted_public_keys={"test-2026": TEST_PUBLIC_KEY_PEM},
            created_at="2026-08-20T00:00:00Z",
        )

        self.assertEqual(result["status"], "published")
        self.assertEqual(self.client.put_order[-1], ROOT_KEY)
    def test_verified_candidate_rejects_bytes_changed_after_build(self) -> None:
        candidate = self.candidate(
            1,
            "verified-mutated",
            b"verified" * 80_000,
            "sha256:" + "7" * 64,
        )
        artifact = VerifiedContractArtifact(
            contract_major=1,
            database=candidate.database,
            manifest=candidate.manifest,
            dataset_id="sha256:" + "7" * 64,
            sha256=sha256_file(candidate.database),
            size_bytes=candidate.database.stat().st_size,
        )
        candidate.database.write_bytes(candidate.database.read_bytes() + b"tampered")

        with self.assertRaisesRegex(ValueError, "changed after contract verification"):
            publish_verified_contract_window(
                self.client,
                self.bucket,
                [artifact],
                self.root / "dist-verified-mutated",
                signer=self.signer,
                release_sequence=100,
                current_contract_major=1,
                minimum_supported_contract_major=1,
                trusted_public_keys={"test-2026": TEST_PUBLIC_KEY_PEM},
                created_at="2026-08-20T00:00:00Z",
            )
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
    def test_window_rotation_reads_previous_root_with_explicit_overlap_trust_set(self) -> None:
        first = self.candidate(1, "rotation-old", b"A" * 500_000, "sha256:" + "a" * 64)
        self.publish([first], current=1, minimum=1, sequence=100, suffix="rotation-old")

        new_private = ec.generate_private_key(ec.SECP256R1())
        new_private_pem = new_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        new_signer = ReleaseSigner.from_private_pem("test-2027", new_private_pem)
        second = self.candidate(1, "rotation-new", b"B" * 500_000, "sha256:" + "b" * 64)

        result = publish_contract_window(
            self.client,
            self.bucket,
            [second],
            self.root / "dist-rotation-new",
            signer=new_signer,
            release_sequence=101,
            current_contract_major=1,
            minimum_supported_contract_major=1,
            trusted_public_keys={
                "test-2026": TEST_PUBLIC_KEY_PEM,
                "test-2027": new_signer.public_key_pem(),
            },
        )

        self.assertEqual(result["status"], "published")
        verified = verify_signed_envelope(
            self.client.objects[(self.bucket, ROOT_KEY)]["Body"],
            {"test-2027": new_signer.public_key_pem()},
        )
        self.assertEqual(verified["key_id"], "test-2027")
    def test_window_rotation_resigns_unchanged_root_with_new_signer(self) -> None:
        candidate = self.candidate(1, "rotation-same", b"A" * 500_000, "sha256:" + "a" * 64)
        self.publish([candidate], current=1, minimum=1, sequence=100, suffix="rotation-same-old")
        previous = self.verified_root()

        new_private = ec.generate_private_key(ec.SECP256R1())
        new_private_pem = new_private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        new_signer = ReleaseSigner.from_private_pem("test-2027", new_private_pem)
        overlap = {
            "test-2026": TEST_PUBLIC_KEY_PEM,
            "test-2027": new_signer.public_key_pem(),
        }

        with self.assertRaisesRegex(ValueError, "release_sequence"):
            publish_contract_window(
                self.client,
                self.bucket,
                [candidate],
                self.root / "dist-rotation-same-stale",
                signer=new_signer,
                release_sequence=100,
                trusted_public_keys=overlap,
                current_contract_major=1,
                minimum_supported_contract_major=1,
            )

        self.client.put_order.clear()
        result = publish_contract_window(
            self.client,
            self.bucket,
            [candidate],
            self.root / "dist-rotation-same-new",
            signer=new_signer,
            release_sequence=101,
            trusted_public_keys=overlap,
            current_contract_major=1,
            minimum_supported_contract_major=1,
        )

        verified = verify_signed_envelope(
            self.client.objects[(self.bucket, ROOT_KEY)]["Body"],
            {"test-2027": new_signer.public_key_pem()},
        )
        self.assertEqual(result["status"], "resigned")
        self.assertEqual(verified["key_id"], "test-2027")
        self.assertEqual(verified["release_sequence"], 101)
        self.assertEqual(verified["manifest"], previous["manifest"])
        self.assertEqual(self.client.put_order, [ROOT_KEY])
