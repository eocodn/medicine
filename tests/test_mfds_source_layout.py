from __future__ import annotations

import inspect
import unittest
from dataclasses import replace
from pathlib import Path

from medicine_canonical import build as canonical_build
from medicine_canonical import integrated_build
from medicine_canonical.cli import (
    DEFAULT_MFDS_INGREDIENT_RAW,
    DEFAULT_RAW,
    build_parser,
)
from medicine_canonical.source_layout import MfdsSourceLayout
from medicine_canonical.mfds_ingredient import (
    import_mfds_ingredient_snapshots,
    sync_mfds_ingredient_sources,
)
from medicine_canonical.sources import sync_canonical_api_sources
from medicine_reference.mfds_sources import (
    MFDS_DUR_INGREDIENT_SOURCE_FAMILY,
    MFDS_SOURCE_MANIFEST,
    PERMIT_SOURCE,
)


class MfdsSourceLayoutTest(unittest.TestCase):
    def test_layout_resolves_all_17_manifest_paths_by_source_family(self) -> None:
        layout = MfdsSourceLayout.from_roots("/tmp/product", "/tmp/ingredient")

        resolved = {source.dataset_key: layout.path_for(source) for source in MFDS_SOURCE_MANIFEST}

        self.assertEqual(len(resolved), 17)
        for source in MFDS_SOURCE_MANIFEST:
            expected_root = (
                Path("/tmp/ingredient")
                if source.source_family == MFDS_DUR_INGREDIENT_SOURCE_FAMILY
                else Path("/tmp/product")
            )
            self.assertEqual(resolved[source.dataset_key], expected_root / source.filename)

    def test_layout_preserves_existing_default_relationships(self) -> None:
        direct = MfdsSourceLayout.for_database(Path("/tmp/db/canonical.sqlite"))
        self.assertEqual(direct.product_dir, Path("/tmp/db/canonical.sources"))
        self.assertEqual(direct.ingredient_dir, Path("/tmp/db/mfds_ingredient"))

        cli = MfdsSourceLayout.from_roots(DEFAULT_RAW, DEFAULT_MFDS_INGREDIENT_RAW)
        self.assertEqual(cli.product_dir, Path("data/canonical/raw"))
        self.assertEqual(cli.ingredient_dir, Path("data/canonical/mfds_ingredient"))

    def test_layout_rejects_manifest_source_with_unknown_family(self) -> None:
        layout = MfdsSourceLayout.from_roots("/tmp/product", "/tmp/ingredient")
        unknown = replace(PERMIT_SOURCE, source_family="unexpected_source_family")

        with self.assertRaisesRegex(ValueError, "unsupported MFDS source family"):
            layout.path_for(unknown)

    def test_build_layers_accept_one_mfds_layout_instead_of_parallel_roots(self) -> None:
        sync_params = inspect.signature(canonical_build.sync_reference_sources).parameters
        populate_params = inspect.signature(canonical_build.populate_canonical_source_tables).parameters
        assemble_params = inspect.signature(canonical_build.assemble_canonical_database).parameters
        build_params = inspect.signature(canonical_build.build_canonical_database).parameters
        integrated_assemble = inspect.signature(
            integrated_build.assemble_integrated_databases
        ).parameters
        integrated_build_params = inspect.signature(
            integrated_build.build_integrated_databases
        ).parameters
        product_sync_params = inspect.signature(sync_canonical_api_sources).parameters
        ingredient_sync_params = inspect.signature(sync_mfds_ingredient_sources).parameters
        ingredient_import_params = inspect.signature(import_mfds_ingredient_snapshots).parameters

        self.assertIn("source_layout", sync_params)
        self.assertNotIn("raw_dir", sync_params)
        self.assertNotIn("ingredient_raw_dir", sync_params)

        self.assertIn("source_layout", populate_params)
        self.assertNotIn("raw_dir", populate_params)
        self.assertNotIn("ingredient_raw_dir", populate_params)

        self.assertIn("source_layout", assemble_params)
        self.assertNotIn("raw_dir", assemble_params)
        self.assertNotIn("ingredient_raw_dir", assemble_params)

        self.assertIn("source_layout", build_params)
        self.assertNotIn("raw_dir", build_params)
        self.assertNotIn("ingredient_raw_dir", build_params)

        self.assertIn("source_layout", integrated_assemble)
        self.assertNotIn("canonical_raw_dir", integrated_assemble)
        self.assertNotIn("ingredient_raw_dir", integrated_assemble)

        self.assertIn("source_layout", integrated_build_params)
        self.assertNotIn("canonical_raw_dir", integrated_build_params)
        self.assertNotIn("ingredient_raw_dir", integrated_build_params)

        for params in (product_sync_params, ingredient_sync_params, ingredient_import_params):
            self.assertIn("source_layout", params)
            self.assertNotIn("raw_dir", params)

    def test_cli_keeps_existing_raw_directory_flags_and_defaults(self) -> None:
        parser = build_parser()

        sync = parser.parse_args(["sync"])
        self.assertEqual(sync.raw_dir, DEFAULT_RAW)
        self.assertEqual(sync.ingredient_raw_dir, DEFAULT_MFDS_INGREDIENT_RAW)

        build = parser.parse_args(["build"])
        self.assertEqual(build.raw_dir, DEFAULT_RAW)
        self.assertEqual(build.ingredient_raw_dir, DEFAULT_MFDS_INGREDIENT_RAW)


if __name__ == "__main__":
    unittest.main()