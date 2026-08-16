from pathlib import Path
import tomllib
import unittest


class DeploymentConfigTest(unittest.TestCase):
    def test_reference_publish_workflow_builds_verified_mobile_release_before_r2_publish(self) -> None:
        workflow = Path(".github/workflows/reference-publish.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertNotIn("schedule:", workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("DATA_GO_KR_SERVICE_KEY", workflow)
        self.assertIn("R2_ACCESS_KEY_ID", workflow)
        self.assertIn("R2_SECRET_ACCESS_KEY", workflow)
        self.assertIn("R2_ENDPOINT", workflow)
        self.assertIn("R2_BUCKET", workflow)
        self.assertIn("refresh_sources", workflow)
        self.assertIn("actions/cache/restore@v5", workflow)
        self.assertIn("actions/cache/save@v5", workflow)
        self.assertIn("data/canonical/raw", workflow)
        self.assertIn("data/canonical/substances", workflow)
        self.assertIn("data/kids", workflow)
        self.assertIn("cache-matched-key", workflow)
        self.assertIn("kids-sync", workflow)
        self.assertIn("substance-sync", workflow)
        self.assertIn("medicine-canonical sync", workflow)
        self.assertIn("integrated-build", workflow)
        self.assertNotIn("integrated-rebuild", workflow)
        self.assertLess(workflow.index("kids-sync"), workflow.index("substance-sync"))
        self.assertLess(workflow.index("substance-sync"), workflow.index("medicine-canonical sync"))
        self.assertLess(workflow.index("medicine-canonical sync"), workflow.index("integrated-build"))
        self.assertIn("canonical verify", workflow)
        self.assertIn("canonical substance-verify", workflow)
        self.assertIn("canonical mobile-build", workflow)
        self.assertIn("release-publish-r2", workflow)
        self.assertNotIn("reference-source/kids/current.zip", workflow)
        self.assertNotIn("kids_source_key", workflow)
        self.assertNotIn("kids_source_sha256", workflow)
        self.assertNotIn("kids-extract", workflow)

    def test_canonical_reviewed_corpora_are_included_in_built_package(self) -> None:
        config = tomllib.loads(Path("pyproject.toml").read_text())
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertIn("medicine_canonical", package_data)
        self.assertIn("data/*.tsv", package_data["medicine_canonical"])

    def test_r2_smoke_workflow_verifies_private_bucket_write_path(self) -> None:
        workflow = Path(".github/workflows/r2-smoke.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        for secret in (
            "R2_ACCESS_KEY_ID",
            "R2_SECRET_ACCESS_KEY",
            "R2_ENDPOINT",
            "R2_BUCKET",
        ):
            self.assertIn(f"secrets.{secret}", workflow)
        self.assertIn("put_object", workflow)
        self.assertIn("head_object", workflow)
        self.assertIn("delete_object", workflow)
        self.assertIn("medicine-r2-smoke/", workflow)

    def test_android_build_packages_canonical_snapshot_without_alias_refresh(self) -> None:
        compose = Path("compose.yaml").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertIn("from medicine_canonical.mobile import build_mobile_database", compose)
        self.assertIn('build_mobile_database("data/db/canonical.sqlite", "data/db/mobile.sqlite")', compose)
        self.assertNotIn("ingredient-aliases --write", compose)
        self.assertNotIn("data/db/dur.sqlite", compose.split("android:", 1)[1])
        self.assertIn("androidComponents", gradle)
        self.assertIn("addGeneratedSourceDirectory", gradle)
        self.assertNotIn("assets.srcDirs", gradle)
        self.assertNotIn("project.copy", gradle)
        runtime = Path("medicine_app/canonical_runtime.py").read_text()
        self.assertIn('include("medicine_app/**/*.py")', gradle)
        self.assertNotIn("from medicine_canonical", runtime)
        self.assertNotIn("import medicine_canonical", runtime)

    def test_retired_legacy_etl_is_not_packaged_or_exposed(self) -> None:
        compose = Path("compose.yaml").read_text()
        dockerfile = Path("Dockerfile").read_text()
        ui_dockerfile = Path("Dockerfile.ui").read_text()
        pyproject = Path("pyproject.toml").read_text()

        self.assertNotIn("  dur:\n", compose)
        self.assertNotIn("  catalog:\n", compose)
        self.assertNotIn("COPY medicine_dur", dockerfile)
        self.assertNotIn("COPY medicine_catalog", dockerfile)
        self.assertNotIn("COPY medicine_dur", ui_dockerfile)
        self.assertNotIn("COPY medicine_catalog", ui_dockerfile)
        self.assertNotIn('medicine-dur =', pyproject)
        self.assertNotIn('name = "medicine-dur"', pyproject)
        self.assertNotIn('medicine-catalog =', pyproject)
        self.assertNotIn('medicine_dur*', pyproject)
        self.assertNotIn('medicine_catalog*', pyproject)


if __name__ == "__main__":
    unittest.main()
