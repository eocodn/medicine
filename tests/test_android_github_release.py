import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from medicine_canonical.release_signing import ReleaseSigner, encode_signed_envelope
from tests.test_release_r2 import TEST_PRIVATE_KEY_PEM


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
        self.assertIn("actions/setup-python@v5", workflow)
        self.assertIn("cryptography==50.0.0", workflow)
        self.assertIn("java-version: '17'", workflow)
        self.assertIn("node-version: '22'", workflow)
        self.assertIn("commandlinetools-linux-13114758_latest.zip", workflow)
        self.assertIn("7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee", workflow)
        self.assertIn("GITHUB_PATH", workflow)
        self.assertIn('sdkmanager="${sdk_root}/cmdline-tools/latest/bin/sdkmanager"', workflow)
        self.assertIn('"${sdkmanager}" "platform-tools" "platforms;android-36" "build-tools;36.0.0"', workflow)
        self.assertIn("npm ci --ignore-scripts --no-audit --no-fund", workflow)
        self.assertIn("fetch_assets.mjs", workflow)
        self.assertIn("mobile/export_runtime.mjs", workflow)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", workflow)
        self.assertIn("RUNNER_TEMP", workflow)
        self.assertIn("GITHUB_ENV", workflow)
        self.assertNotIn("${{ runner.temp }}", workflow)
        self.assertNotIn("docker ", workflow)
        self.assertNotIn("Dockerfile.android", workflow)
        self.assertIn("actions/cache/save@v5", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ github.run_id }}-arm64-v8a", workflow)
        self.assertIn("dist", workflow)

    def test_signed_reference_root_gate_requires_android_contract_before_release(self) -> None:
        script = ROOT / "scripts" / "verify-reference-contract-root.py"
        signer = ReleaseSigner.from_private_pem("test-2026", TEST_PRIVATE_KEY_PEM)
        public_key = serialization.load_pem_public_key(signer.public_key_pem())
        public_der_hex = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).hex()

        def envelope(current: int, minimum: int, contracts: dict) -> bytes:
            root = {
                "protocol_version": 2,
                "created_at": "2026-08-20T00:00:00Z",
                "current_contract_major": current,
                "minimum_supported_contract_major": minimum,
                "contracts": contracts,
            }
            payload = (json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n").encode()
            return encode_signed_envelope(signer.sign_payload(payload, release_sequence=42))

        c1 = {
            "1": {
                "dataset_id": "sha256:" + "1" * 64,
                "target": {"sha256": "2" * 64, "size_bytes": 10},
                "full": {
                    "key": "reference/v2/contracts/1/full/" + "2" * 64 + ".sqlite.gz",
                    "compression": "gzip",
                    "sha256": "3" * 64,
                    "size_bytes": 5,
                },
                "patches": [],
                "history": [],
            }
        }
        with tempfile.TemporaryDirectory() as td:
            root_file = Path(td) / "latest.json"
            root_file.write_bytes(envelope(1, 1, c1))
            ok = subprocess.run(
                [
                    "python",
                    str(script),
                    "--root",
                    str(root_file),
                    "--contract-major",
                    "1",
                    "--key-id",
                    "test-2026",
                    "--public-key-der-hex",
                    public_der_hex,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(ok.returncode, 0, ok.stderr)
            self.assertIn("supports Android contract 1", ok.stdout)

            malformed = {
                "1": {
                    "dataset_id": "NOT-A-DATASET-ID",
                    "target": {"sha256": "2" * 64, "size_bytes": 10},
                    "full": {"compression": "gzip"},
                    "patches": [],
                    "history": [],
                }
            }
            root_file.write_bytes(envelope(1, 1, malformed))
            rejected = subprocess.run(
                [
                    "python",
                    str(script),
                    "--root",
                    str(root_file),
                    "--contract-major",
                    "1",
                    "--key-id",
                    "test-2026",
                    "--public-key-der-hex",
                    public_der_hex,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(rejected.returncode, 0)

            root_file.write_bytes(envelope(2, 2, {"2": {}}))
            retired = subprocess.run(
                [
                    "python",
                    str(script),
                    "--root",
                    str(root_file),
                    "--contract-major",
                    "1",
                    "--key-id",
                    "test-2026",
                    "--public-key-der-hex",
                    public_der_hex,
                ],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(retired.returncode, 0)
            self.assertIn("not supported", retired.stderr)

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
        self.assertIn("verify-android-reference-contract.sh", workflow)
        self.assertIn("cryptography==50.0.0", workflow)
        self.assertIn("./scripts/verify-android-release-version.sh \"${GITHUB_REF_NAME}\"", workflow)
        self.assertIn("actions/workflows/android-release-check.yml/runs?head_sha=${GITHUB_SHA}", workflow)
        self.assertIn("event=workflow_dispatch&status=success", workflow)
        self.assertIn("actions/cache/restore@v5", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ needs.prepare-release.outputs.run_id }}-arm64-v8a", workflow)
        self.assertIn("./scripts/ensure-release-draft.sh", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("SHA256SUMS", workflow)
        self.assertIn("./scripts/publish-release.sh", workflow)
        publish_job = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn("actions/setup-python@v5", publish_job)
        self.assertIn("cryptography==50.0.0", publish_job)
        self.assertNotIn("android_release_build.sh", workflow)
        self.assertNotIn("check-android-release.sh", workflow)
        self.assertNotIn("docker build", workflow)

    def test_android_release_check_packages_the_verified_apk_with_stable_name(self) -> None:
        script = (ROOT / "scripts" / "check-android-release.sh").read_text()
        root_gate = (ROOT / "scripts" / "verify-android-reference-contract.sh").read_text()
        self.assertIn('workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)', script)
        self.assertIn('cd "${workspace}"', script)
        self.assertIn("./scripts/verify-android-release-version.sh", script)
        self.assertIn("./scripts/verify-android-reference-contract.sh", script)
        self.assertNotIn("--location", root_gate)
        self.assertNotIn(" -L", root_gate)
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
        self.assertIn("verify-android-reference-contract.sh", publish)
        self.assertIn("--draft=false", publish)
        self.assertLess(
            publish.index("--clobber"),
            publish.index("verify-android-reference-contract.sh"),
        )
        self.assertLess(
            publish.index("verify-android-reference-contract.sh"),
            publish.index("--draft=false"),
        )
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
