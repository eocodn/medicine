import os
import re
import subprocess
import tempfile
import tomllib
import unittest
from pathlib import Path


class DeploymentConfigTest(unittest.TestCase):
    def test_reference_publish_workflow_builds_verified_mobile_release_before_r2_publish(self) -> None:
        workflow = Path(".github/workflows/reference-publish.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("schedule:", workflow)
        self.assertIn("cron: '17 3 * * *'", workflow)
        self.assertIn('timezone: "Asia/Seoul"', workflow)
        self.assertIn("REFERENCE_PUBLISH_SCHEDULE_ENABLED", workflow)
        self.assertIn('"$EVENT_NAME" == "schedule"', workflow)
        self.assertIn("concurrency:", workflow)
        self.assertIn("DATA_GO_KR_SERVICE_KEY", workflow)
        self.assertIn("R2_ACCESS_KEY_ID", workflow)
        self.assertIn("R2_SECRET_ACCESS_KEY", workflow)
        self.assertIn("R2_ENDPOINT", workflow)
        self.assertIn("R2_BUCKET", workflow)
        self.assertNotIn("REFERENCE_SIGNING_PRIVATE_KEY_PEM", workflow)
        self.assertIn("REFERENCE_SIGNING_KEY_ID", workflow)
        self.assertIn("REFERENCE_SIGNING_KMS_KEY_VERSION", workflow)
        self.assertIn("REFERENCE_RELEASE_SEQUENCE", workflow)
        self.assertIn("github.run_number", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertIn("google-github-actions/auth@v3", workflow)
        self.assertIn("medicine-505813", workflow)
        self.assertIn(
            "projects/173565993547/locations/global/workloadIdentityPools/github-actions/providers/medicine-reference-publisher",
            workflow,
        )
        self.assertIn(
            "projects/medicine-505813/locations/global/keyRings/medicine-release/cryptoKeys/reference-release-signing/cryptoKeyVersions/1",
            workflow,
        )
        self.assertIn("google-cloud-kms", workflow)
        self.assertIn("refresh_sources", workflow)
        self.assertIn("actions/cache/restore@v5", workflow)
        self.assertIn("actions/cache/save@v5", workflow)
        self.assertIn("data/canonical/raw", workflow)
        self.assertIn("data/canonical/substances", workflow)
        self.assertIn("data/canonical/mfds_ingredient", workflow)
        self.assertIn("cache-matched-key", workflow)
        self.assertIn("substance-sync", workflow)
        self.assertIn("medicine-canonical sync", workflow)
        self.assertIn("MFDS sync attempt ${attempt}/3", workflow)
        self.assertIn("for attempt in 1 2 3", workflow)
        self.assertIn("integrated-build", workflow)
        self.assertNotIn("integrated-rebuild", workflow)
        self.assertLess(workflow.index("substance-sync"), workflow.index("medicine-canonical sync"))
        self.assertLess(workflow.index("medicine-canonical sync"), workflow.index("integrated-build"))
        self.assertIn("canonical verify", workflow)
        self.assertIn("canonical substance-verify", workflow)
        self.assertIn("canonical mobile-build", workflow)
        self.assertIn("r2-public-audit", workflow)
        self.assertIn("release-publish-r2", workflow)
        self.assertLess(workflow.index("r2-public-audit"), workflow.index("release-publish-r2"))

    def test_android_release_defaults_to_r2_dev_reference_updates_while_debug_stays_off(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        compose = Path("compose.yaml").read_text()

        self.assertIn("REFERENCE_UPDATE_BASE_URL", gradle)
        self.assertIn("releaseReferenceUpdateBaseUrl", gradle)
        self.assertIn("debug", gradle)
        self.assertIn("release", gradle)
        self.assertIn("r2.dev", gradle)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_BASE_URL", compose)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", compose)

    def test_canonical_reviewed_corpora_are_included_in_built_package(self) -> None:
        config = tomllib.loads(Path("pyproject.toml").read_text())
        package_data = config["tool"]["setuptools"]["package-data"]
        self.assertIn("medicine_canonical", package_data)
        self.assertIn("data/*.tsv", package_data["medicine_canonical"])

    def test_r2_smoke_workflow_verifies_private_bucket_write_path(self) -> None:
        workflow = Path(".github/workflows/r2-smoke.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn("python-version: '3.13'", workflow)
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
        self.assertIn("r2-public-audit", workflow)

    def test_android_packages_mfds_remark_runtime_registry(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertIn('include("medicine_reference/**/*.py")', gradle)
        self.assertIn('include("medicine_reference/data/mfds_remark_registry.tsv")', gradle)
        self.assertNotIn('include("medicine_canonical/mfds_remark_registry.py")', gradle)
        self.assertNotIn('include("medicine_canonical/data/mfds_remark_registry.tsv")', gradle)

    def test_android_build_packages_canonical_snapshot_without_alias_refresh(self) -> None:
        compose = Path("compose.yaml").read_text()
        build_script = Path("scripts/android_compose_build.sh").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertIn("from medicine_canonical.mobile import build_mobile_database", build_script)
        self.assertIn('build_mobile_database("data/db/canonical.sqlite", "data/db/mobile.sqlite")', build_script)
        self.assertNotIn("ingredient-aliases --write", build_script)
        self.assertNotIn("data/db/dur.sqlite", build_script)
        self.assertIn('command: ["sh", "/workspace/scripts/android_compose_build.sh"]', compose)
        self.assertIn("androidComponents", gradle)
        self.assertIn("addGeneratedSourceDirectory", gradle)
        self.assertNotIn("assets.srcDirs", gradle)
        self.assertNotIn("project.copy", gradle)
        runtime = Path("medicine_app/canonical_runtime.py").read_text()
        self.assertIn('include("medicine_app/**/*.py")', gradle)
        self.assertNotIn("from medicine_canonical", runtime)
        self.assertNotIn("import medicine_canonical", runtime)

    def test_ui_service_runs_as_the_host_user_for_bind_mounted_screenshots(self) -> None:
        compose = Path("compose.yaml").read_text()
        ui_service = compose.split("\n  ui:\n", 1)[1].split("\n  test:\n", 1)[0]

        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', ui_service)

    def test_writable_bind_mount_services_run_as_the_host_user(self) -> None:
        compose = Path("compose.yaml").read_text()
        service_names = (
            "canonical",
            "app",
            "ui",
            "test",
            "browser-test",
            "ocr-detection-test",
            "ocr-detection-benchmark",
            "ocr-corpus",
            "ocr-finetune",
            "ocr-finetune-train",
            "ocr-full-document",
            "android",
        )
        service_starts = [match.start() for match in re.finditer(r"(?m)^  [a-z0-9-]+:\n", compose)]

        for name in service_names:
            start = compose.index(f"  {name}:\n")
            later_starts = [candidate for candidate in service_starts if candidate > start]
            end = min(later_starts, default=len(compose))
            service = compose[start:end]
            self.assertIn(
                'user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"',
                service,
                msg=f"{name} must not write to the repository bind mount as root",
            )

        self.assertNotIn("${MEDICINE_UID:-1000}:${MEDICINE_GID:-1000}", compose)

    def test_android_uses_per_worktree_host_owned_gradle_state(self) -> None:
        compose = Path("compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]

        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', android_service)
        self.assertIn("HOME: /tmp", android_service)
        self.assertIn("GRADLE_USER_HOME: /workspace/.android-gradle-cache", android_service)
        self.assertNotIn("android-gradle-cache:/opt/gradle-cache", android_service)
        self.assertNotIn("\nvolumes:\n  android-gradle-cache:\n", compose)

    def test_android_accepts_prebuilt_reference_inputs_outside_workspace(self) -> None:
        compose = Path("compose.yaml").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        build_script = Path("scripts/android_compose_build.sh").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]

        self.assertIn("MEDICINE_MOBILE_DB", gradle)
        self.assertIn("MEDICINE_MOBILE_MANIFEST", gradle)
        self.assertIn("MEDICINE_MOBILE_DB", android_service)
        self.assertIn("MEDICINE_MOBILE_MANIFEST", android_service)
        self.assertIn("Both MEDICINE_MOBILE_DB and MEDICINE_MOBILE_MANIFEST must be set together", build_script)
        self.assertIn("Skipping mobile database build; using prebuilt reference inputs", build_script)

    def test_android_default_compose_build_executes_mobile_builder_before_gradle(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        compose = (repo_root / "compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]
        build_script = repo_root / "scripts" / "android_compose_build.sh"

        self.assertIn('command: ["sh", "/workspace/scripts/android_compose_build.sh"]', android_service)

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            log_path = Path(temp_dir) / "calls.log"
            python_stub = bin_dir / "python3.12"
            python_stub.write_text(
                "#!/bin/sh\n"
                "printf 'python-argc:%s\\n' \"$#\" >> \"$ANDROID_BUILD_TEST_LOG\"\n"
                "index=1\n"
                "for arg in \"$@\"; do\n"
                "  printf 'python-arg%s:%s\\n' \"$index\" \"$arg\" >> \"$ANDROID_BUILD_TEST_LOG\"\n"
                "  index=$((index + 1))\n"
                "done\n"
            )
            python_stub.chmod(0o755)
            gradle_stub = bin_dir / "gradle"
            gradle_stub.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$ANDROID_BUILD_TEST_LOG\"\n"
            )
            gradle_stub.chmod(0o755)

            env = os.environ.copy()
            env.pop("MEDICINE_MOBILE_DB", None)
            env.pop("MEDICINE_MOBILE_MANIFEST", None)
            env["ANDROID_BUILD_TEST_LOG"] = str(log_path)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["sh", str(build_script)],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            calls = log_path.read_text().splitlines()

        self.assertEqual(calls[0], "python-argc:2")
        self.assertEqual(calls[1], "python-arg1:-c")
        self.assertIn("from medicine_canonical.mobile import build_mobile_database", calls[2])
        self.assertIn(
            'build_mobile_database("data/db/canonical.sqlite", "data/db/mobile.sqlite")',
            calls[2],
        )
        self.assertEqual(calls[3], "gradle:--no-daemon testDebugUnitTest assembleDebug")

    def test_local_web_packages_the_approved_on_device_ocr_runtime(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1].split("\n  ui:\n", 1)[0]
        dockerfile = Path("Dockerfile.web").read_text()
        entrypoint = Path("medicine_app/web_entrypoint.py").read_text()

        self.assertIn("dockerfile: Dockerfile.web", web_service)
        self.assertNotIn("\n    user:", web_service)
        self.assertIn('LOCAL_UID: "${LOCAL_UID:-1000}"', web_service)
        self.assertIn('LOCAL_GID: "${LOCAL_GID:-1000}"', web_service)
        self.assertIn("HOME: /tmp", web_service)
        self.assertIn('PYTHONDONTWRITEBYTECODE: "1"', web_service)
        self.assertIn("AS ocr-assets", dockerfile)
        self.assertIn("mobile/export_runtime.mjs /downloads /out", dockerfile)
        self.assertIn("COPY --from=ocr-assets /out /opt/medicine-ocr-assets", dockerfile)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR=/opt/medicine-ocr-assets", dockerfile)
        self.assertIn('ENTRYPOINT ["python", "-m", "medicine_app.web_entrypoint"]', dockerfile)
        self.assertIn("os.chown", entrypoint)
        self.assertIn("os.setgid", entrypoint)
        self.assertIn("os.setuid", entrypoint)

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
