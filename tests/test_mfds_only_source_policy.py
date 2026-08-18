from __future__ import annotations

import inspect
from pathlib import Path
import unittest

from medicine_app.canonical_runtime import _RUNTIME_SOURCE_FAMILIES
from medicine_canonical.build import assemble_canonical_database, build_canonical_database
from medicine_canonical.cli import build_parser
from medicine_canonical import mfds_ingredient
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
        self.assertEqual(_RUNTIME_SOURCE_FAMILIES, expected)
        self.assertEqual(
            CORE_SOURCE_FAMILIES,
            frozenset(
                {"mfds_permit_api", "mfds_dur_item_api", "mfds_dur_ingredient_api"}
            ),
        )

    def test_canonical_build_api_has_no_kids_input(self) -> None:
        self.assertNotIn("kids_dir", inspect.signature(assemble_canonical_database).parameters)
        self.assertNotIn("kids_dir", inspect.signature(build_canonical_database).parameters)

        parser = build_parser()
        commands = parser._subparsers._group_actions[0].choices
        self.assertNotIn("kids-sync", commands)
        for command in ("build", "rebuild", "integrated-build", "integrated-rebuild"):
            option_strings = {
                option
                for action in commands[command]._actions
                for option in action.option_strings
            }
            self.assertNotIn("--kids-dir", option_strings)

    def test_kids_importer_modules_are_removed(self) -> None:
        self.assertFalse(Path("medicine_canonical/kids_sources.py").exists())
        self.assertFalse(Path("medicine_canonical/xlsx.py").exists())
        self.assertFalse(Path("tests/test_kids_sources.py").exists())

    def test_obsolete_mfds_ingredient_preview_surface_is_removed(self) -> None:
        commands = build_parser()._subparsers._group_actions[0].choices
        for command in ("sync", "build", "integrated-build", "criteria", "stats", "verify"):
            self.assertIn(command, commands)
        for command in (
            "mfds-ingredient-sync",
            "mfds-ingredient-build",
            "mfds-ingredient-rebuild",
        ):
            self.assertNotIn(command, commands)

        for name in (
            "assemble_mfds_ingredient_preview",
            "build_mfds_ingredient_preview",
            "verify_mfds_ingredient_preview",
        ):
            self.assertFalse(hasattr(mfds_ingredient, name))

    def test_reference_publish_workflow_has_no_kids_source_path(self) -> None:
        workflow = Path(".github/workflows/reference-publish.yml").read_text(encoding="utf-8")
        for legacy in (
            "data/kids",
            "kids-sync",
            "KIDS",
            "reference-source/kids/current.zip",
            "kids_source_key",
            "kids_source_sha256",
            "kids-extract",
        ):
            self.assertNotIn(legacy, workflow)
        self.assertIn("data/canonical/mfds_ingredient", workflow)


if __name__ == "__main__":
    unittest.main()
