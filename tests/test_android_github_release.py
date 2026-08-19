import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def release_version() -> dict[str, str]:
    return dict(
        line.split("=", 1)
        for line in (ROOT / "android" / "release.properties").read_text().splitlines()
        if line and not line.startswith("#")
    )


class AndroidGithubReleaseTest(unittest.TestCase):
    def test_release_version_is_source_controlled_and_drives_gradle_defaults(self) -> None:
        properties = ROOT / "android" / "release.properties"
        self.assertTrue(properties.is_file())
        values = release_version()
        self.assertEqual(set(values), {"versionName", "versionCode"})
        self.assertTrue(values["versionName"])
        self.assertNotRegex(values["versionName"], r"\s")
        self.assertGreater(int(values["versionCode"]), 0)

        gradle = (ROOT / "android" / "app" / "build.gradle.kts").read_text()
        self.assertIn('rootProject.file("release.properties")', gradle)
        self.assertIn("releaseVersionCode", gradle)
        self.assertIn("releaseVersionName", gradle)
        self.assertIn("versionCode = releaseEnvironment?.versionCode ?: releaseVersionCode", gradle)
        self.assertIn("versionName = releaseEnvironment?.versionName ?: releaseVersionName", gradle)
        self.assertIn("MEDICINE_ANDROID_VERSION_CODE must match android/release.properties", gradle)
        self.assertIn("MEDICINE_ANDROID_VERSION_NAME must match android/release.properties", gradle)

    def test_release_tag_must_match_android_release_version(self) -> None:
        script = ROOT / "scripts" / "verify-android-release-version.sh"
        self.assertTrue(script.is_file())
        values = release_version()
        expected_tag = f'v{values["versionName"]}'

        ok = subprocess.run(
            ["bash", str(script), expected_tag],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        mismatch = subprocess.run(
            ["bash", str(script), "v999.999.999"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        malformed = subprocess.run(
            ["bash", str(script), "0.2.0"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )

        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertIn(f"release version verified: {expected_tag}", ok.stdout)
        self.assertNotEqual(mismatch.returncode, 0)
        self.assertIn("does not match", mismatch.stderr)
        self.assertNotEqual(malformed.returncode, 0)
        self.assertIn("must start with v", malformed.stderr)

    def test_current_release_tag_is_derived_from_android_release_version(self) -> None:
        script = ROOT / "scripts" / "current-android-release-tag.sh"
        self.assertTrue(script.is_file())
        expected_tag = f'v{release_version()["versionName"]}'
        result = subprocess.run(
            ["bash", str(script)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), expected_tag)

    def test_release_check_builds_and_preserves_one_verified_debug_signed_apk_without_secrets(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "android-release-check.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_BASE64", workflow)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_PASSWORD", workflow)
        self.assertNotIn("ANDROID_RELEASE_KEY_ALIAS", workflow)
        self.assertNotIn("ANDROID_RELEASE_KEY_PASSWORD", workflow)
        self.assertIn("./scripts/current-android-release-tag.sh", workflow)
        self.assertIn("check-android-release.sh", workflow)
        self.assertIn("actions/setup-java@v4", workflow)
        self.assertIn("actions/setup-node@v4", workflow)
        self.assertIn("java-version: '17'", workflow)
        self.assertIn("node-version: '22'", workflow)
        self.assertIn('sdkmanager "platform-tools" "platforms;android-36" "build-tools;36.0.0"', workflow)
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund", workflow)
        self.assertIn("fetch_assets.mjs", workflow)
        self.assertIn("mobile/export_runtime.mjs", workflow)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", workflow)
        self.assertNotIn("docker ", workflow)
        self.assertNotIn("Dockerfile.android", workflow)
        self.assertIn("actions/cache/save@v5", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ github.run_id }}-arm64-v8a", workflow)
        self.assertIn("dist", workflow)

    def test_android_gradle_wrapper_is_pinned_and_checksum_verified(self) -> None:
        gradlew = ROOT / "android" / "gradlew"
        wrapper_jar = ROOT / "android" / "gradle" / "wrapper" / "gradle-wrapper.jar"
        wrapper_jar_checksum = ROOT / "android" / "gradle" / "wrapper" / "gradle-wrapper.jar.sha256"
        wrapper_properties = ROOT / "android" / "gradle" / "wrapper" / "gradle-wrapper.properties"

        self.assertTrue(gradlew.is_file())
        self.assertTrue(wrapper_jar.is_file())
        self.assertTrue(wrapper_jar_checksum.is_file())
        self.assertTrue(wrapper_properties.is_file())
        properties = wrapper_properties.read_text()
        self.assertIn("distributionUrl=https\\://services.gradle.org/distributions/gradle-9.4.1-bin.zip", properties)
        self.assertRegex(properties, r"(?m)^distributionSha256Sum=[0-9a-f]{64}$")
        self.assertRegex(wrapper_jar_checksum.read_text().strip(), r"^[0-9a-f]{64}  gradle-wrapper\.jar$")

        workflow = (ROOT / ".github" / "workflows" / "android-release-check.yml").read_text()
        self.assertIn("sha256sum -c gradle-wrapper.jar.sha256", workflow)

    def test_tag_release_restores_exact_validated_apk_without_rebuilding(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "android-release.yml").read_text()

        self.assertIn("push:", workflow)
        self.assertIn("tags:", workflow)
        self.assertIn("'v*'", workflow)
        self.assertIn("actions: read", workflow)
        self.assertIn("contents: write", workflow)
        self.assertIn("./scripts/verify-android-release-version.sh \"${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("actions/workflows/android-release-check.yml/runs?head_sha=${GITHUB_SHA}", workflow)
        self.assertIn("event=workflow_dispatch&status=success", workflow)
        self.assertIn("actions/cache/restore@v5", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ needs.prepare-release.outputs.run_id }}-arm64-v8a", workflow)
        self.assertIn("./scripts/ensure-release-draft.sh", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("SHA256SUMS", workflow)
        self.assertIn("./scripts/publish-release.sh", workflow)
        self.assertNotIn("android_release_build.sh", workflow)
        self.assertNotIn("check-android-release.sh", workflow)
        self.assertNotIn("docker build", workflow)

    def test_android_release_check_packages_the_verified_apk_with_stable_name(self) -> None:
        script = (ROOT / "scripts" / "check-android-release.sh").read_text()
        self.assertIn('workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)', script)
        self.assertIn('cd "${workspace}"', script)
        self.assertIn("./scripts/verify-android-release-version.sh", script)
        self.assertIn("./gradlew --no-daemon --dependency-verification strict testDebugUnitTest lintDebug assembleDebug", script)
        self.assertNotIn("\ngradle --no-daemon", script)
        self.assertNotIn("./scripts/android_release_build.sh", script)
        self.assertNotIn("MEDICINE_ANDROID_KEYSTORE", script)
        self.assertIn("medicine-${tag}-arm64-v8a.apk", script)
        self.assertIn("app-debug.apk", script)
        self.assertIn("aapt", script)
        self.assertIn("apksigner", script)
        self.assertIn("versionCode", script)
        self.assertIn("versionName", script)

    def test_release_publish_uses_draft_as_retry_boundary_and_requires_apk_and_checksum(self) -> None:
        ensure = (ROOT / "scripts" / "ensure-release-draft.sh").read_text()
        publish = (ROOT / "scripts" / "publish-release.sh").read_text()

        self.assertIn("--draft", ensure)
        self.assertIn("--verify-tag", ensure)
        self.assertIn("already published; refusing to reuse it", ensure)
        self.assertIn('apks=("${asset_dir}"/*.apk)', publish)
        self.assertIn("SHA256SUMS", publish)
        self.assertIn("--clobber", publish)
        self.assertIn("--draft=false", publish)
        self.assertIn("already published; leaving it unchanged", publish)

    def test_release_documentation_describes_cowi_style_exact_sha_handoff(self) -> None:
        docs = (ROOT / "docs" / "android-releasing.md").read_text()
        readme = (ROOT / "README.md").read_text()

        self.assertIn("Release Check", docs)
        self.assertIn("exact commit SHA", docs)
        self.assertIn("does not rebuild", docs)
        self.assertIn("debug-signed", docs)
        self.assertIn("does not require GitHub signing secrets", docs)
        self.assertIn("native GitHub-hosted Ubuntu runner", docs)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_BASE64", docs)
        self.assertIn("v0.2.0", docs)
        self.assertIn("docs/android-releasing.md", readme)


if __name__ == "__main__":
    unittest.main()
