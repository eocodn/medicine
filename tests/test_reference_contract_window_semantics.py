from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest import mock

from medicine_canonical.cli import main as canonical_main
from medicine_canonical.mobile import MOBILE_PHYSICAL_POLICY_VERSION, build_mobile_database
from medicine_canonical.product_search_documents import materialize_product_search_documents
from medicine_canonical.schema import SCHEMA_VERSION
from medicine_canonical.reference_contracts.v1 import (
    REFERENCE_CONTRACT_MAJOR,
    export_reference_database,
    logical_dataset_id,
    logical_dataset_id_oracle,
    semantic_facts_for_reviewed_remark,
    verify_reference_database,
)
from medicine_canonical.reference_contracts.registry import (
    ReferenceContractImplementation,
    build_supported_contract_window,
    supported_contract_majors,
)
from medicine_reference.mfds_remark_registry import reviewed_mfds_remark
from tests.canonical_fixture_support import add_linked_rule, add_product
from tests.canonical_fixture_support import make_canonical_db

from tests.test_reference_contract_semantics import ReferenceContractSemanticsTestFixture


class ReferenceContractWindowSemanticsTest(ReferenceContractSemanticsTestFixture):
    def test_supported_contract_window_builder_emits_every_registered_major(self) -> None:
        output = self.mobile.parent / "contract-window"

        result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(supported_contract_majors(), (1,))
        self.assertEqual(result["current_contract_major"], 1)
        self.assertEqual(result["minimum_supported_contract_major"], 1)
        self.assertEqual([entry["contract_major"] for entry in result["contracts"]], [1])
        self.assertTrue(Path(result["contracts"][0]["database"]).is_file())
        self.assertTrue(Path(result["contracts"][0]["manifest"]).is_file())
    def test_supported_contract_window_preserves_exporter_checkpoint_for_resume(self) -> None:
        output = self.mobile.parent / "resumable-contract-window"
        database = output / "contract-1.sqlite"
        manifest = output / "contract-1.manifest.json"
        checkpoint = database.with_name(database.name + ".build.checkpoint.json")

        with mock.patch(
            "medicine_canonical.mobile.write_manifest_atomic",
            side_effect=RuntimeError("manifest interrupted"),
        ):
            with self.assertRaisesRegex(RuntimeError, "manifest interrupted"):
                build_supported_contract_window(self.canonical, output)

        self.assertTrue(database.is_file())
        self.assertFalse(manifest.exists())
        self.assertTrue(checkpoint.is_file())

        with mock.patch(
            "medicine_canonical.mobile.verify_canonical_database",
            side_effect=AssertionError("resumed exporter must not restart verification"),
        ):
            result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(result["contracts"][0]["database"], str(database))
        self.assertTrue(manifest.is_file())
        self.assertFalse(checkpoint.exists())
    def test_supported_contract_window_does_not_repeat_strict_identity_verification(self) -> None:
        output = self.mobile.parent / "single-identity-pass-window"
        database = output / "contract-1.sqlite"
        manifest = output / "contract-1.manifest.json"

        def export(_canonical, target, *, manifest_path, **_kwargs):
            Path(target).parent.mkdir(parents=True, exist_ok=True)
            data = b"verified-contract"
            Path(target).write_bytes(data)
            payload = {
                "contract_major": 1,
                "dataset_id": "sha256:" + "1" * 64,
                "sha256": __import__("hashlib").sha256(data).hexdigest(),
                "size_bytes": len(data),
            }
            Path(manifest_path).write_text(json.dumps(payload), encoding="utf-8")
            return payload

        strict = mock.Mock(side_effect=AssertionError("strict verifier repeated logical identity"))
        built = mock.Mock(return_value={"status": "verified"})
        implementation = ReferenceContractImplementation(
            1,
            export,
            strict,
            verify_built=built,
        )
        with (
            mock.patch(
                "medicine_canonical.reference_contracts.registry.supported_contract_majors",
                return_value=(1,),
            ),
            mock.patch(
                "medicine_canonical.reference_contracts.registry.implementation_for",
                return_value=implementation,
            ),
        ):
            result = build_supported_contract_window(self.canonical, output)

        self.assertEqual(result["contracts"][0]["database"], str(database))
        self.assertEqual(result["contracts"][0]["manifest"], str(manifest))
        built.assert_called_once_with(database, 1, "sha256:" + "1" * 64)
        strict.assert_not_called()
    def test_publication_build_can_surface_unbuildable_previous_contract_without_hiding_it(self) -> None:
        output = self.mobile.parent / "retired-build-window"

        def fail_previous(*_args, **_kwargs):
            raise ValueError("contract 1 cannot express current semantics")

        def export_current(_canonical, database, *, manifest_path, **_kwargs):
            Path(database).write_bytes(b"contract-2")
            Path(manifest_path).write_text("{}", encoding="utf-8")
            return {
                "contract_major": 2,
                "dataset_id": "sha256:" + "2" * 64,
                "sha256": "3" * 64,
                "size_bytes": 10,
            }

        implementations = {
            1: ReferenceContractImplementation(1, fail_previous, lambda *_args: {}),
            2: ReferenceContractImplementation(2, export_current, lambda *_args: {}),
        }
        with (
            mock.patch(
                "medicine_canonical.reference_contracts.registry.supported_contract_majors",
                return_value=(1, 2),
            ),
            mock.patch(
                "medicine_canonical.reference_contracts.registry.implementation_for",
                side_effect=lambda major: implementations[major],
            ),
        ):
            with self.assertRaisesRegex(ValueError, "cannot express"):
                build_supported_contract_window(self.canonical, output)

            result = build_supported_contract_window(
                self.canonical,
                output,
                allow_previous_failure=True,
            )

        self.assertEqual([entry["contract_major"] for entry in result["contracts"]], [2])
        self.assertEqual(result["failed_previous_contract"]["contract_major"], 1)
        self.assertEqual(result["failed_previous_contract"]["error"], "ValueError")
        self.assertFalse((output / "contract-1.sqlite").exists())
        self.assertFalse((output / "contract-1.manifest.json").exists())
        self.assertTrue((output / "contract-2.sqlite").is_file())
    def test_reference_window_build_cli_uses_registered_contract_set(self) -> None:
        output = self.mobile.parent / "cli-contract-window"
        stdout = StringIO()

        with redirect_stdout(stdout):
            code = canonical_main([
                "reference-window-build",
                "--db", str(self.canonical),
                "--output-dir", str(output),
                "--json",
            ])

        self.assertEqual(code, 0)
        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["current_contract_major"], 1)
        self.assertEqual([entry["contract_major"] for entry in payload["contracts"]], [1])
    def test_review_required_remark_is_materialized_as_opaque_condition(self) -> None:
        row = self._semantic_for_remark(
            "다만, 다른 약을 사용할 수 없거나 효과가 없는 경우에만 8세 이상 신중투여"
        )
        self.assertEqual(row["semantic_role"], "applicability_condition")
        self.assertEqual(row["evaluation_mode"], "review_required")
        self.assertEqual(row["evaluator_kind"], "opaque_condition")
        self.assertEqual(row["fallback_action"], "review_required")
        self.assertEqual(json.loads(row["structured_payload_json"]), {})
    def test_interaction_window_is_materialized_as_runtime_evaluator(self) -> None:
        row = self._semantic_for_remark("24시간 이내 병용금기")
        self.assertEqual(row["evaluation_mode"], "runtime_evaluable")
        self.assertEqual(row["evaluator_kind"], "minimum_separation")
        self.assertEqual(row["fallback_action"], "review_required")
        self.assertEqual(
            json.loads(row["structured_payload_json"]),
            {"direction": "symmetric", "hours": 24},
        )
    def test_form_exclusion_is_materialized_as_runtime_evaluator(self) -> None:
        row = self._semantic_for_remark("외용제는 제외")
        self.assertEqual(row["evaluator_kind"], "excluded_route")
        self.assertEqual(json.loads(row["structured_payload_json"]), {"route": "topical"})
    def test_build_scope_review_produces_no_runtime_fact(self) -> None:
        reviewed = reviewed_mfds_remark("dose_caution", "단일제·복합제 포함")
        self.assertIsNotNone(reviewed)
        assert reviewed is not None
        self.assertEqual(semantic_facts_for_reviewed_remark(reviewed), ())
