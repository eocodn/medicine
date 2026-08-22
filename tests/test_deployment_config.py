import base64
import hashlib
import importlib.util
import json
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
        self.assertIn("REFERENCE_SIGNING_TRUSTED_KEYS_FILE", workflow)
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
        self.assertIn("canonical reference-build-publish-r2", workflow)
        self.assertIn("--output-dir artifacts/reference-release", workflow)
        self.assertIn("--allow-retired-previous-failure", workflow)
        self.assertIn("r2-public-audit", workflow)
        self.assertIn("--contract-dir data/db/reference-contracts", workflow)
        self.assertIn("retire_previous_contract", workflow)
        self.assertIn("--retire-previous-contract", workflow)
        self.assertIn("RETIRE_PREVIOUS_CONTRACT", workflow)
        self.assertLess(
            workflow.index("r2-public-audit"),
            workflow.index("reference-build-publish-r2"),
        )

    def test_release_trust_configuration_matches_android_production_keys(self) -> None:
        trust = json.loads(Path("deploy/reference-signing-trusted-keys.json").read_text())
        spec = importlib.util.spec_from_file_location(
            "verify_reference_contract_root", "scripts/verify-reference-contract-root.py"
        )
        assert spec is not None and spec.loader is not None
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        android = module._android_trust()
        workflow = Path(".github/workflows/reference-publish.yml").read_text()
        workflow_key_id = re.search(r"REFERENCE_SIGNING_KEY_ID:\s*([A-Za-z0-9._-]+)", workflow).group(1)
        self.assertEqual(trust["active_key_id"], workflow_key_id)
        self.assertIn(trust["active_key_id"], trust["keys"])
        self.assertEqual(set(trust["keys"]), set(android))
        for key_id, pem in trust["keys"].items():
            der = base64.b64decode(
                pem.replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replace("\n", ""),
                validate=True,
            )
            spki_hex, fingerprint = android[key_id]
            self.assertEqual(der.hex(), spki_hex)
            self.assertEqual(hashlib.sha256(der).hexdigest(), fingerprint)

    def test_scheduled_reference_publish_manages_one_github_failure_incident(self) -> None:
        workflow = Path(".github/workflows/reference-publish.yml").read_text()
        incident = workflow.split("\n  incident:\n", 1)[1]

        self.assertIn("always()", incident)
        self.assertIn("github.event_name == 'schedule'", incident)
        self.assertIn("REFERENCE_PUBLISH_SCHEDULE_ENABLED == 'true'", incident)
        self.assertIn("needs: [sources, publish]", incident)
        self.assertIn("issues: write", incident)
        self.assertIn("contents: read", incident)
        self.assertNotIn("id-token: write", incident)
        self.assertNotIn("R2_ACCESS_KEY_ID", incident)
        self.assertNotIn("REFERENCE_SIGNING_KMS_KEY_VERSION", incident)
        self.assertNotIn("secrets.", incident)
        self.assertIn("GH_TOKEN: ${{ github.token }}", incident)
        self.assertIn("needs.sources.result", incident)
        self.assertIn("needs.publish.result", incident)
        self.assertIn("<!-- reference-publish-scheduled-incident -->", incident)
        self.assertIn("[automation] Reference DB scheduled publish failure", incident)
        self.assertIn("gh issue list", incident)
        self.assertIn("gh issue create", incident)
        self.assertIn("gh issue comment", incident)
        self.assertIn("gh issue close", incident)
        self.assertIn("--label bug", incident)
        self.assertIn("actions/runs/${GITHUB_RUN_ID}", incident)

    def test_android_debug_and_release_default_to_r2_dev_reference_bootstrap(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        compose = Path("compose.yaml").read_text()

        self.assertIn("https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/", gradle)
        self.assertIn("REFERENCE_UPDATE_BASE_URL", gradle)
        self.assertIn("releaseReferenceUpdateBaseUrl", gradle)
        self.assertIn("effectiveReferenceUpdateBaseUrl", gradle)
        self.assertIn("debug", gradle)
        self.assertIn("release", gradle)
        self.assertIn("r2.dev", gradle)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_BASE_URL", compose)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", compose)

    def test_android_release_enables_code_and_resource_shrinking(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        release = gradle.split('getByName("release") {', 1)[1].split("\n        }", 1)[0]
        debug = gradle.split('getByName("debug") {', 1)[1].split("\n        }", 1)[0]

        self.assertIn("isMinifyEnabled = true", release)
        self.assertIn("isShrinkResources = true", release)
        self.assertIn('getDefaultProguardFile("proguard-android-optimize.txt")', release)
        self.assertIn('"proguard-rules.pro"', release)
        self.assertNotIn("isMinifyEnabled = true", debug)
        self.assertNotIn("isShrinkResources = true", debug)

        proguard = Path("android/app/proguard-rules.pro").read_text()
        self.assertIn("@android.webkit.JavascriptInterface", proguard)

    def test_android_release_requires_explicit_version_and_signing_inputs(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()

        for name in (
            "MEDICINE_ANDROID_VERSION_CODE",
            "MEDICINE_ANDROID_VERSION_NAME",
            "MEDICINE_ANDROID_KEYSTORE_PATH",
            "MEDICINE_ANDROID_KEYSTORE_PASSWORD",
            "MEDICINE_ANDROID_KEY_ALIAS",
            "MEDICINE_ANDROID_KEY_PASSWORD",
        ):
            self.assertIn(name, gradle)

        self.assertIn("signingConfigs", gradle)
        release = gradle.split('getByName("release") {', 1)[1].split("\n        }", 1)[0]
        self.assertIn("signingConfig", release)
        self.assertIn("requireReleaseEnvironment", gradle)
        self.assertIn('tasks.register("verifyReleaseEnvironment")', gradle)

    def test_android_aggregate_bundle_requires_release_environment(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertIn("tasks.configureEach", gradle)
        self.assertIn('name.contains("Release", ignoreCase = true)', gradle)
        self.assertIn("dependsOn(verifyReleaseEnvironment)", gradle)

    def test_android_release_guard_uses_resolved_tasks_not_raw_cli_names(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()

        self.assertNotIn("gradle.startParameter.taskNames", gradle)
        self.assertIn('tasks.register("verifyReleaseEnvironment")', gradle)
        self.assertIn('name.contains("Release", ignoreCase = true)', gradle)
        self.assertIn("dependsOn(verifyReleaseEnvironment)", gradle)

    def test_android_release_keystores_are_ignored_recursively(self) -> None:
        gitignore = Path(".gitignore").read_text()

        self.assertIn("android/**/*.jks", gitignore)
        self.assertIn("android/**/*.keystore", gitignore)

    def test_android_release_keystores_are_excluded_from_docker_context(self) -> None:
        dockerignore = Path(".dockerignore").read_text()

        self.assertIn("android/**/*.jks", dockerignore)
        self.assertIn("android/**/*.keystore", dockerignore)

    def test_android_release_passwords_are_prompted_without_literal_secret_exports(self) -> None:
        readme = Path("README.md").read_text()
        release_section = readme.split("release 변형은", 1)[1].split("## 의료 정보 주의", 1)[0]

        self.assertNotIn("export MEDICINE_ANDROID_KEYSTORE_PASSWORD='...'", release_section)
        self.assertNotIn("export MEDICINE_ANDROID_KEY_PASSWORD='...'", release_section)
        self.assertIn("read -r -s MEDICINE_ANDROID_KEYSTORE_PASSWORD", release_section)
        self.assertIn("read -r -s MEDICINE_ANDROID_KEY_PASSWORD", release_section)
        self.assertIn("export MEDICINE_ANDROID_KEYSTORE_PASSWORD", release_section)
        self.assertIn("export MEDICINE_ANDROID_KEY_PASSWORD", release_section)

    def test_android_dependencies_are_locked_and_checksum_verified(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertIn("dependencyLocking", gradle)
        self.assertIn("lockAllConfigurations()", gradle)

        lockfile = Path("android/app/gradle.lockfile")
        verification = Path("android/gradle/verification-metadata.xml")
        self.assertTrue(lockfile.is_file())
        self.assertTrue(verification.is_file())
        self.assertIn("sha256", verification.read_text())

        dockerfile = Path("Dockerfile.android").read_text()
        self.assertIn("COPY android/gradle ./gradle", dockerfile)

    def test_android_sdk_archive_is_checksum_verified(self) -> None:
        dockerfile = Path("Dockerfile.android").read_text()
        self.assertIn(
            "gradle:9.4.1-jdk17@sha256:549ab76a04fc532f37945d689207a3fb2642350b1235901eb652c93dae218dd0",
            dockerfile,
        )
        self.assertIn("commandlinetools-linux-13114758_latest.zip", dockerfile)
        self.assertIn(
            "7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee",
            dockerfile,
        )
        self.assertIn("sha256sum -c", dockerfile)

    def test_android_release_script_rejects_missing_release_configuration_before_gradle(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        script = repo_root / "scripts" / "android_release_build.sh"
        self.assertTrue(script.is_file())

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            log_path = Path(temp_dir) / "calls.log"
            gradle_stub = bin_dir / "gradle"
            gradle_stub.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$ANDROID_RELEASE_TEST_LOG\"\n"
            )
            gradle_stub.chmod(0o755)

            env = os.environ.copy()
            for name in (
                "MEDICINE_ANDROID_VERSION_CODE",
                "MEDICINE_ANDROID_VERSION_NAME",
                "MEDICINE_ANDROID_KEYSTORE_PATH",
                "MEDICINE_ANDROID_KEYSTORE_PASSWORD",
                "MEDICINE_ANDROID_KEY_ALIAS",
                "MEDICINE_ANDROID_KEY_PASSWORD",
            ):
                env.pop(name, None)
            env["ANDROID_RELEASE_TEST_LOG"] = str(log_path)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["sh", str(script)],
                cwd=repo_root,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            gradle_calls = log_path.read_text() if log_path.exists() else ""

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("MEDICINE_ANDROID_VERSION_CODE", result.stderr)
        self.assertEqual(gradle_calls, "")

    def test_android_release_script_builds_and_verifies_signed_versioned_apk(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        release_script = (repo_root / "scripts" / "android_release_build.sh").read_text()

        with tempfile.TemporaryDirectory() as temp_dir:
            workspace = Path(temp_dir) / "workspace"
            scripts_dir = workspace / "scripts"
            android_dir = workspace / "android"
            scripts_dir.mkdir(parents=True)
            android_dir.mkdir()
            script = scripts_dir / "android_release_build.sh"
            script.write_text(release_script)

            keystore = Path(temp_dir) / "release.jks"
            keystore.write_bytes(b"test keystore placeholder")
            log_path = Path(temp_dir) / "calls.log"
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            gradle_stub = bin_dir / "gradle"
            gradle_stub.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$ANDROID_RELEASE_TEST_LOG\"\n"
                "mkdir -p \"$ANDROID_RELEASE_TEST_WORKSPACE/android/app/build/outputs/apk/release\"\n"
                ": > \"$ANDROID_RELEASE_TEST_WORKSPACE/android/app/build/outputs/apk/release/app-release.apk\"\n"
            )
            gradle_stub.chmod(0o755)

            android_home = Path(temp_dir) / "android-sdk"
            build_tools = android_home / "build-tools" / "36.0.0"
            build_tools.mkdir(parents=True)
            aapt_stub = build_tools / "aapt"
            aapt_stub.write_text(
                "#!/bin/sh\n"
                "printf 'aapt:%s\\n' \"$*\" >> \"$ANDROID_RELEASE_TEST_LOG\"\n"
                "printf \"package: name='com.medicine.android' versionCode='23' versionName='1.4.0'\\n\"\n"
            )
            aapt_stub.chmod(0o755)
            apksigner_stub = build_tools / "apksigner"
            apksigner_stub.write_text(
                "#!/bin/sh\n"
                "printf 'apksigner:%s\\n' \"$*\" >> \"$ANDROID_RELEASE_TEST_LOG\"\n"
            )
            apksigner_stub.chmod(0o755)

            env = os.environ.copy()
            env.update(
                {
                    "ANDROID_HOME": str(android_home),
                    "ANDROID_RELEASE_TEST_LOG": str(log_path),
                    "ANDROID_RELEASE_TEST_WORKSPACE": str(workspace),
                    "MEDICINE_ANDROID_VERSION_CODE": "23",
                    "MEDICINE_ANDROID_VERSION_NAME": "1.4.0",
                    "MEDICINE_ANDROID_KEYSTORE_PATH": str(keystore),
                    "MEDICINE_ANDROID_KEYSTORE_PASSWORD": "store-secret",
                    "MEDICINE_ANDROID_KEY_ALIAS": "medicine-release",
                    "MEDICINE_ANDROID_KEY_PASSWORD": "key-secret",
                    "PATH": f"{bin_dir}:{env['PATH']}",
                }
            )
            result = subprocess.run(
                ["sh", str(script)],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = log_path.read_text().splitlines()

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            calls[0],
            "gradle:--no-daemon --dependency-verification strict testDebugUnitTest lintRelease assembleRelease",
        )
        self.assertTrue(any(call.startswith("aapt:dump badging ") for call in calls))
        self.assertTrue(any(call.startswith("apksigner:verify --verbose --print-certs ") for call in calls))

    def test_android_package_excludes_python_runtime_but_repo_cli_remains(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        compose = Path("compose.yaml").read_text()
        rust_build = Path("scripts/build_android_rust.sh").read_text()

        self.assertNotIn("chaquopy", gradle.lower())
        self.assertNotIn('include("medicine_app/**/*.py")', gradle)
        self.assertNotIn("medicine_canonical", gradle)
        self.assertTrue(Path("medicine_app/cli.py").is_file())
        self.assertTrue(Path("rust/medicine_core/src/bin/medicine_core.rs").is_file())
        self.assertIn("--lib", rust_build)
        app_service = compose.split("\n  app:\n", 1)[1].split("\n  web:\n", 1)[0]
        app_dockerfile = Path("Dockerfile.app").read_text()
        ui_dockerfile = Path("Dockerfile.ui").read_text()
        self.assertIn("dockerfile: Dockerfile.app", app_service)
        self.assertIn('entrypoint: ["python", "-m", "medicine_app.cli"]', app_service)
        self.assertIn("cargo build --locked --release --bin medicine-core", app_dockerfile)
        self.assertIn("COPY --from=rust-cli", app_dockerfile)
        self.assertIn("medicine-core", app_dockerfile)
        self.assertIn("COPY medicine_app/__init__.py medicine_app/cli.py ./medicine_app/", app_dockerfile)
        self.assertNotIn("COPY medicine_app ./medicine_app", app_dockerfile)
        self.assertIn("cargo build --locked --release --features web --bin medicine-core --bin medicine-core-web", ui_dockerfile)
        self.assertIn("medicine-core-web", ui_dockerfile)
        self.assertIn("COPY medicine_app/__init__.py medicine_app/cli.py ./medicine_app/", ui_dockerfile)
        self.assertIn("COPY medicine_app/static ./medicine_app/static", ui_dockerfile)
        self.assertNotIn("COPY medicine_app ./medicine_app", ui_dockerfile)

    def test_android_bootstrap_contract_matches_embedded_runtime_contract(self) -> None:
        kotlin = Path(
            "android/app/src/main/java/com/medicine/android/ReferenceRuntimeAdapters.kt"
        ).read_text()
        python_runtime = Path("medicine_app/reference_contracts/v1.py").read_text()

        android_contract = re.search(r'const val CONTRACT_MAJOR = ([0-9]+)', kotlin)
        runtime_contract = re.search(r'REFERENCE_CONTRACT_MAJOR = ([0-9]+)', python_runtime)
        self.assertIsNotNone(android_contract)
        self.assertIsNotNone(runtime_contract)
        self.assertEqual(android_contract.group(1), runtime_contract.group(1))

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

    def test_android_excludes_mfds_remark_registry_and_requires_materialized_semantics(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        verifier = Path("medicine_app/reference_contracts/v1.py").read_text()
        self.assertNotIn('include("medicine_reference/**/*.py")', gradle)
        self.assertNotIn("mfds_remark_registry.tsv", gradle)
        self.assertIn('"reference_criterion_semantics"', verifier)
        self.assertIn("_verify_schema(con)", verifier)

    def test_android_build_does_not_generate_or_package_reference_snapshot(self) -> None:
        compose = Path("compose.yaml").read_text()
        build_script = Path("scripts/android_compose_build.sh").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        self.assertNotIn("build_mobile_database", build_script)
        self.assertNotIn("mobile.sqlite", build_script)
        self.assertNotIn("mobile.manifest.json", build_script)
        self.assertNotIn("PrepareMobileAssets", gradle)
        self.assertNotIn("MEDICINE_MOBILE_DB", gradle)
        self.assertNotIn("MEDICINE_MOBILE_MANIFEST", gradle)
        self.assertIn('command: ["sh", "/workspace/scripts/android_compose_build.sh"]', compose)
        self.assertIn("androidComponents", gradle)
        self.assertIn("addGeneratedSourceDirectory", gradle)
        self.assertNotIn("assets.srcDirs", gradle)
        self.assertNotIn("project.copy", gradle)
        self.assertNotIn('include("medicine_app/**/*.py")', gradle)

    def test_ui_service_runs_as_the_host_user_for_bind_mounted_screenshots(self) -> None:
        compose = Path("compose.yaml").read_text()
        ui_service = compose.split("\n  ui:\n", 1)[1].split("\n  test:\n", 1)[0]

        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', ui_service)

    def test_writable_bind_mount_services_run_as_the_host_user(self) -> None:
        compose = Path("compose.yaml").read_text()
        service_names = (
            "canonical",
            "app",
            "web",
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

    def test_android_has_no_prebuilt_reference_asset_inputs(self) -> None:
        compose = Path("compose.yaml").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        build_script = Path("scripts/android_compose_build.sh").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]

        self.assertNotIn("MEDICINE_MOBILE_DB", gradle)
        self.assertNotIn("MEDICINE_MOBILE_MANIFEST", gradle)
        self.assertNotIn("MEDICINE_MOBILE_DB", android_service)
        self.assertNotIn("MEDICINE_MOBILE_MANIFEST", android_service)
        self.assertNotIn("mobile.sqlite", build_script)
        self.assertNotIn("mobile.manifest.json", build_script)

    def test_android_default_compose_build_runs_gradle_without_mobile_builder(self) -> None:
        repo_root = Path(__file__).resolve().parents[1]
        compose = (repo_root / "compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]
        build_script = repo_root / "scripts" / "android_compose_build.sh"

        self.assertIn('command: ["sh", "/workspace/scripts/android_compose_build.sh"]', android_service)

        with tempfile.TemporaryDirectory() as temp_dir:
            bin_dir = Path(temp_dir) / "bin"
            bin_dir.mkdir()
            log_path = Path(temp_dir) / "calls.log"
            gradle_stub = bin_dir / "gradle"
            gradle_stub.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$ANDROID_BUILD_TEST_LOG\"\n"
            )
            gradle_stub.chmod(0o755)

            env = os.environ.copy()
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

        self.assertEqual(
            calls,
            ["gradle:--no-daemon --dependency-verification strict testDebugUnitTest assembleDebug"],
        )

    def test_local_web_packages_rust_runtime_and_approved_on_device_ocr_runtime(self) -> None:
        compose = Path("compose.yaml").read_text()
        web_service = compose.split("\n  web:\n", 1)[1].split("\n  ui:\n", 1)[0]
        dockerfile = Path("Dockerfile.web").read_text()

        self.assertIn("dockerfile: Dockerfile.web", web_service)
        self.assertIn('user: "${LOCAL_UID:-1000}:${LOCAL_GID:-1000}"', web_service)
        self.assertIn("HOME: /tmp", web_service)
        self.assertNotIn("PYTHONDONTWRITEBYTECODE", web_service)
        self.assertIn("AS ocr-assets", dockerfile)
        self.assertIn("AS rust-web", dockerfile)
        self.assertIn("cargo build --locked --release --features web --bin medicine-core-web", dockerfile)
        self.assertIn("mobile/export_runtime.mjs /downloads /out", dockerfile)
        self.assertIn("COPY --from=ocr-assets /out /opt/medicine-ocr-assets", dockerfile)
        self.assertIn("chmod -R a+rX /opt/medicine-static /opt/medicine-ocr-assets", dockerfile)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR=/opt/medicine-ocr-assets", dockerfile)
        self.assertIn("COPY medicine_app/static /opt/medicine-static", dockerfile)
        self.assertIn("COPY --from=rust-web", dockerfile)
        self.assertIn('ENTRYPOINT ["/usr/local/bin/medicine-core-web"]', dockerfile)
        self.assertNotIn("python", dockerfile.lower())

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
