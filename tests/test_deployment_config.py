import base64
import hashlib
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
    def test_release_trust_manifest_is_the_android_and_publisher_source_of_truth(self) -> None:
        trust = json.loads(Path("deploy/reference-signing-trusted-keys.json").read_text())
        workflow = Path(".github/workflows/reference-publish.yml").read_text()
        gradle = Path("android/app/build.gradle.kts").read_text()
        android_trust = Path(
            "android/app/src/main/java/com/medicine/android/ReferenceTrust.kt"
        ).read_text()
        workflow_key_id = re.search(r"REFERENCE_SIGNING_KEY_ID:\s*([A-Za-z0-9._-]+)", workflow).group(1)
        entries = {entry["key_id"]: entry for entry in trust["keys"]}

        self.assertEqual(trust["active_key_id"], workflow_key_id)
        self.assertIn(trust["active_key_id"], entries)
        self.assertIn("../deploy/reference-signing-trusted-keys.json", gradle)
        self.assertIn("REFERENCE_TRUSTED_KEYS_JSON", gradle)
        self.assertIn("BuildConfig.REFERENCE_TRUSTED_KEYS_JSON", android_trust)
        self.assertNotIn("reference-prod-2026-01", android_trust)
        for key_id, entry in entries.items():
            pem = entry["public_key_pem"]
            der = base64.b64decode(
                pem.replace("-----BEGIN PUBLIC KEY-----", "")
                .replace("-----END PUBLIC KEY-----", "")
                .replace("\n", ""),
                validate=True,
            )
            self.assertEqual(hashlib.sha256(der).hexdigest(), entry["spki_sha256"], key_id)
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
    def test_android_public_identity_targets_api_36(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        activity = Path("android/app/src/main/java/com/medicine/android/MainActivity.kt").read_text()
        html = Path("ui/public/index.html").read_text()
        css = Path("ui/public/styles.css").read_text()

        self.assertIn('namespace = "com.medicine.android"', gradle)
        self.assertIn('applicationId = "kr.yakbom.app"', gradle)
        self.assertNotIn('applicationId = "com.medicine.android"', gradle)
        self.assertIn("compileSdk = 36", gradle)
        self.assertIn("targetSdk = 36", gradle)
        self.assertNotIn("WindowInsetsCompat", activity)
        self.assertNotIn("ViewCompat.setOnApplyWindowInsetsListener", activity)
        self.assertIn("viewport-fit=cover", html)
        self.assertIn("env(safe-area-inset-top)", css)
        self.assertIn("env(safe-area-inset-bottom)", css)

    def test_android_debug_defaults_to_r2_dev_but_release_requires_production_reference_url(self) -> None:
        gradle = Path("android/app/build.gradle.kts").read_text()
        compose = Path("compose.yaml").read_text()
        reference_properties = Path("android/reference.properties").read_text()
        reference_gate = Path("scripts/verify-android-reference-contract.sh").read_text()

        self.assertIn("developmentBaseUrl=https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/", reference_properties)
        self.assertIn('rootProject.file("reference.properties")', gradle)
        self.assertIn("REFERENCE_UPDATE_BASE_URL", gradle)
        self.assertIn("developmentReferenceUpdateBaseUrl", gradle)
        self.assertIn("debugReferenceUpdateBaseUrl", gradle)
        self.assertIn("productionReferenceUpdateBaseUrl", gradle)
        self.assertIn('val name = "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL"', gradle)
        self.assertIn('error("$name is required for Android release tasks")', gradle)
        self.assertIn("must not use the development r2.dev endpoint", gradle)
        self.assertIn("lowercase(Locale.ROOT)", gradle)
        self.assertIn("trimEnd('.')", gradle)
        self.assertIn("android/reference.properties", reference_gate)
        self.assertNotIn("defaultReleaseReferenceUpdateBaseUrl", reference_gate)
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

        compose = Path("compose.yaml").read_text()
        android_service = compose.split("\n  android:\n", 1)[1]
        dockerfile = Path("Dockerfile.android").read_text()
        self.assertIn("- .:/workspace", android_service)
        self.assertNotIn("COPY android/gradle", dockerfile)
        self.assertNotIn("COPY android/app", dockerfile)
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
                "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL",
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
            reference_gate = scripts_dir / "verify-android-reference-contract.sh"
            reference_gate.write_text(
                "#!/bin/sh\n"
                "printf 'reference-gate:%s\\n' \"$MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL\" >> \"$ANDROID_RELEASE_TEST_LOG\"\n"
            )
            reference_gate.chmod(0o755)
            no_ocr_verifier = scripts_dir / "verify-no-ocr-android-artifact.py"
            no_ocr_verifier.write_text(
                "import os, sys\n"
                "with open(os.environ['ANDROID_RELEASE_TEST_LOG'], 'a') as handle:\n"
                "    handle.write(f'no-ocr:{sys.argv[1]}\\n')\n"
            )

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
                "printf \"package: name='kr.yakbom.app' versionCode='23' versionName='1.4.0'\\n\"\n"
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
                    "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL": "https://reference.yakbom.example/",
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
        self.assertEqual(calls[0], "reference-gate:https://reference.yakbom.example/")
        self.assertIn(
            "gradle:--no-daemon --dependency-verification strict testDebugUnitTest lintRelease assembleRelease",
            calls,
        )
        self.assertTrue(any(call.startswith("aapt:dump badging ") for call in calls))
        self.assertTrue(any(call.startswith("apksigner:verify --verbose --print-certs ") for call in calls))
        self.assertTrue(any(call.startswith("no-ocr:") for call in calls))
        self.assertEqual(calls[-1], "reference-gate:https://reference.yakbom.example/")

    def test_android_play_bundle_script_builds_signed_no_ocr_aab(self) -> None:
        script = Path("scripts/android_play_bundle.sh")
        self.assertTrue(script.is_file())
        text = script.read_text()

        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", text)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", text)
        self.assertIn("bundleRelease", text)
        self.assertNotIn("assembleRelease", text)
        self.assertIn("app-release.aab", text)
        self.assertIn("verify-signed-android-bundle.sh", text)
        self.assertIn("verify-android-reference-contract.sh", text)
        self.assertIn('java -jar "$BUNDLETOOL_JAR" dump manifest', text)
        self.assertIn("/manifest/@package", text)
        self.assertIn("/manifest/@android:versionCode", text)
        self.assertIn("/manifest/@android:versionName", text)
        self.assertIn("/manifest/uses-sdk/@android:targetSdkVersion", text)
        self.assertIn("kr.yakbom.app", text)
        self.assertIn("targetSdk 36", text)
        self.assertIn("verify-no-ocr-android-artifact.py", text)

        dockerfile = Path("Dockerfile.android").read_text()
        self.assertIn("bundletool-all-1.18.3.jar", dockerfile)
        self.assertIn("a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29", dockerfile)
        self.assertIn("BUNDLETOOL_JAR", dockerfile)
        self.assertIn("cryptography==50.0.0", dockerfile)
        self.assertIn("MEDICINE_PYTHON_BIN", dockerfile)

    def test_android_signed_release_build_checks_exact_production_reference_channel(self) -> None:
        script = Path("scripts/android_release_build.sh").read_text()
        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", script)
        self.assertIn("verify-android-reference-contract.sh", script)
        self.assertLess(script.index("apksigner"), script.rindex("verify-android-reference-contract.sh"))

    def test_android_bundle_signature_verifier_rejects_unsigned_jarsigner_success(self) -> None:
        verifier = Path("scripts/verify-signed-android-bundle.sh")
        self.assertTrue(verifier.is_file())
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bundle = root / "app.aab"
            bundle.write_bytes(b"placeholder")
            bin_dir = root / "bin"
            bin_dir.mkdir()
            jarsigner = bin_dir / "jarsigner"
            jarsigner.write_text(
                "#!/bin/sh\n"
                "printf '%b\\n' \"$JARSIGNER_TEST_REPORT\"\n"
                "exit ${JARSIGNER_TEST_RC:-0}\n"
            )
            jarsigner.chmod(0o755)
            base_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}

            unsigned = subprocess.run(
                ["sh", str(verifier), str(bundle)],
                env={**base_env, "JARSIGNER_TEST_REPORT": "jar is unsigned."},
                capture_output=True,
                text=True,
                check=False,
            )
            partial = subprocess.run(
                ["sh", str(verifier), str(bundle)],
                env={
                    **base_env,
                    "JARSIGNER_TEST_REPORT": (
                        "sm 123 base/assets/index.html\\n"
                        "?  4 base/assets/extra.txt\\n"
                        "jar verified.\\n"
                        "This jar contains unsigned entries which have not been integrity-checked."
                    ),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            signed = subprocess.run(
                ["sh", str(verifier), str(bundle)],
                env={
                    **base_env,
                    "JARSIGNER_TEST_REPORT": (
                        "sm 123 base/assets/index.html\\n"
                        ">>> Signer\\n"
                        "jar verified."
                    ),
                },
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertNotEqual(unsigned.returncode, 0)
        self.assertNotEqual(partial.returncode, 0)
        self.assertEqual(signed.returncode, 0, signed.stderr)

    def test_android_play_release_documentation_separates_code_and_operator_gates(self) -> None:
        docs = Path("docs/android-play-releasing.md")
        self.assertTrue(docs.is_file())
        text = docs.read_text()

        self.assertIn("kr.yakbom.app", text)
        self.assertIn("targetSdk 36", text)
        self.assertIn("android_play_bundle.sh", text)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", text)
        self.assertIn("Play App Signing", text)
        self.assertIn("upload key", text)
        self.assertIn("개인정보처리방침", text)
        self.assertIn("Health Apps", text)
        self.assertIn("MFDS", text)
        self.assertIn("데이터 이용조건", text)
