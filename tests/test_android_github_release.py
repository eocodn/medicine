import gzip
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from cryptography.hazmat.primitives import serialization

from medicine_canonical.release_signing import ReleaseSigner, encode_signed_envelope
from tests.r2_fakes import TEST_PRIVATE_KEY_PEM


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

    def test_release_check_uses_gcp_managed_durable_signing_identity(self) -> None:
        workflow = (ROOT / ".github" / "workflows" / "android-release-check.yml").read_text()

        self.assertIn("workflow_dispatch:", workflow)
        self.assertIn("contents: read", workflow)
        self.assertIn("id-token: write", workflow)
        self.assertNotIn("self-hosted", workflow)
        self.assertGreaterEqual(workflow.count("runs-on: ubuntu-latest"), 2)
        self.assertIn("build-unsigned:", workflow)
        self.assertIn("sign-and-validate:", workflow)
        self.assertIn("needs: build-unsigned", workflow)
        self.assertNotIn("secrets.", workflow)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_BASE64", workflow)
        self.assertIn("google-github-actions/auth@7c6bc770dae815cd3e89ee6cdf493a5fab2cc093", workflow)
        self.assertIn(
            "projects/173565993547/locations/global/workloadIdentityPools/github-actions/providers/medicine-android-signer",
            workflow,
        )
        self.assertIn(
            "google-github-actions/get-secretmanager-secrets@bc9c54b29fdffb8a47776820a7d26e77b379d262",
            workflow,
        )
        self.assertIn("medicine-android-release-keystore-b64", workflow)
        self.assertIn("medicine-android-release-signing-password", workflow)
        self.assertNotIn("MEDICINE_ANDROID_KEYSTORE_PATH", workflow)
        self.assertIn("MEDICINE_ANDROID_KEYSTORE_PASSWORD", workflow)
        self.assertIn("MEDICINE_ANDROID_KEY_PASSWORD", workflow)
        self.assertIn("medicine-release", workflow)
        self.assertIn("base64 --decode", workflow)
        self.assertIn("trap", workflow)
        self.assertIn("apksigner sign", workflow)
        self.assertIn("apksigner verify", workflow)
        self.assertIn("--v2-signing-enabled false", workflow)
        self.assertIn("--v3-signing-enabled true", workflow)
        self.assertIn("android-release-signing-certificate.sha256", workflow)
        self.assertIn("credentials_file_path", workflow)
        self.assertIn("rm -f", workflow)
        self.assertIn("./scripts/current-android-release-tag.sh", workflow)
        self.assertIn("check-android-release.sh", workflow)
        self.assertIn("actions/checkout@d23441a48e516b6c34aea4fa41551a30e30af803", workflow)
        self.assertIn("actions/setup-java@cf277c60eb25467037889841efdb72551f06f6c3", workflow)
        self.assertIn("dtolnay/rust-toolchain@1d1eb14921d9c2d8ea575663e5fe3a0b57868a05", workflow)
        self.assertIn("targets: aarch64-linux-android", workflow)
        self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065", workflow)
        self.assertIn("actions/setup-node@49933ea5288caeca8642d1e84afbd3f7d6820020", workflow)
        self.assertIn("node-version: '22'", workflow)
        self.assertIn("cache-dependency-path: ui/package-lock.json", workflow)
        self.assertIn("npm ci", workflow)
        self.assertIn("working-directory: ui", workflow)
        self.assertIn("cryptography==50.0.0", workflow)
        self.assertIn("java-version: '17'", workflow)
        self.assertIn("commandlinetools-linux-13114758_latest.zip", workflow)
        self.assertIn("7ec965280a073311c339e571cd5de778b9975026cfcbe79f2b1cdcb1e15317ee", workflow)
        self.assertIn("GITHUB_PATH", workflow)
        self.assertIn('sdkmanager="${sdk_root}/cmdline-tools/latest/bin/sdkmanager"', workflow)
        self.assertIn('"platform-tools"', workflow)
        self.assertIn('"platforms;android-36"', workflow)
        self.assertIn('"build-tools;36.0.0"', workflow)
        self.assertIn('"ndk;29.0.14206865"', workflow)
        self.assertIn("RUNNER_TEMP", workflow)
        self.assertIn("GITHUB_ENV", workflow)
        self.assertNotIn("browser_ocr", workflow)
        self.assertNotIn("MEDICINE_OCR_ASSETS_DIR", workflow)
        self.assertNotIn("${{ runner.temp }}", workflow)
        self.assertNotIn("docker ", workflow)
        self.assertNotIn("Dockerfile.android", workflow)
        self.assertIn("actions/cache/save@caa296126883cff596d87d8935842f9db880ef25", workflow)
        self.assertIn("actions/cache/restore@caa296126883cff596d87d8935842f9db880ef25", workflow)
        self.assertIn("android-release-unsigned-${{ github.sha }}-${{ github.run_id }}-arm64-v8a", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ github.run_id }}-arm64-v8a", workflow)
        self.assertIn("dist", workflow)
        self.assertNotRegex(workflow, r"uses:\s+[^\n]+@v\d+(?:\s|$)")

        unsigned_job, signing_job = workflow.split("\n  sign-and-validate:\n", 1)
        self.assertNotIn("google-github-actions/auth@", unsigned_job)
        self.assertNotIn("get-secretmanager-secrets@", unsigned_job)
        self.assertNotIn("MEDICINE_ANDROID_KEYSTORE_PASSWORD", unsigned_job)
        auth_index = signing_job.index("google-github-actions/auth@")
        self.assertNotIn("./scripts/", signing_job[auth_index:])

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
                    sys.executable,
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

            root_file.write_bytes(envelope(2, 1, c1))
            missing_active = subprocess.run(
                [
                    sys.executable,
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
            self.assertNotEqual(missing_active.returncode, 0)
            self.assertIn("support window", missing_active.stderr)

            root_file.write_bytes(envelope(1, 1, {**c1, "2": {}}))
            extra_active = subprocess.run(
                [
                    sys.executable,
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
            self.assertNotEqual(extra_active.returncode, 0)
            self.assertIn("support window", extra_active.stderr)

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
                    sys.executable,
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
                    sys.executable,
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

    def test_signed_reference_root_gate_verifies_full_artifact_bytes(self) -> None:
        script = ROOT / "scripts" / "verify-reference-contract-root.py"
        signer = ReleaseSigner.from_private_pem("test-2026", TEST_PRIVATE_KEY_PEM)
        public_key = serialization.load_pem_public_key(signer.public_key_pem())
        public_der_hex = public_key.public_bytes(
            serialization.Encoding.DER,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        ).hex()
        target = b"SQLite format 3\x00test-reference-payload"
        compressed = gzip.compress(target, mtime=0)
        target_sha = hashlib.sha256(target).hexdigest()
        full_sha = hashlib.sha256(compressed).hexdigest()
        full_key = f"reference/v2/contracts/1/full/{target_sha}.sqlite.gz"
        root = {
            "protocol_version": 2,
            "created_at": "2026-08-30T00:00:00Z",
            "current_contract_major": 1,
            "minimum_supported_contract_major": 1,
            "contracts": {
                "1": {
                    "dataset_id": "sha256:" + "1" * 64,
                    "target": {"sha256": target_sha, "size_bytes": len(target)},
                    "full": {
                        "key": full_key,
                        "compression": "gzip",
                        "sha256": full_sha,
                        "size_bytes": len(compressed),
                    },
                    "patches": [],
                    "history": [],
                }
            },
        }
        payload = (json.dumps(root, sort_keys=True, separators=(",", ":")) + "\n").encode()
        envelope = encode_signed_envelope(signer.sign_payload(payload, release_sequence=42))

        with tempfile.TemporaryDirectory() as temp_dir:
            root_file = Path(temp_dir) / "latest.json"
            artifact_file = Path(temp_dir) / "full.sqlite.gz"
            root_file.write_bytes(envelope)
            artifact_file.write_bytes(compressed)
            common = [
                sys.executable,
                str(script),
                "--root",
                str(root_file),
                "--contract-major",
                "1",
                "--key-id",
                "test-2026",
                "--public-key-der-hex",
                public_der_hex,
            ]
            key_result = subprocess.run(
                [*common, "--print-full-key"],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            verified = subprocess.run(
                [*common, "--full-artifact", str(artifact_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            artifact_file.write_bytes(compressed + b"tampered")
            rejected = subprocess.run(
                [*common, "--full-artifact", str(artifact_file)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(key_result.returncode, 0, key_result.stderr)
        self.assertEqual(key_result.stdout.strip(), full_key)
        self.assertEqual(verified.returncode, 0, verified.stderr)
        self.assertIn("verified signed full reference artifact", verified.stdout)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("full artifact", rejected.stderr)

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
        self.assertIn("actions/cache/restore@caa296126883cff596d87d8935842f9db880ef25", workflow)
        self.assertIn("android-release-${{ github.sha }}-${{ needs.prepare-release.outputs.run_id }}-arm64-v8a", workflow)
        self.assertIn("./scripts/ensure-release-draft.sh", workflow)
        self.assertIn("gh release upload", workflow)
        self.assertIn("SHA256SUMS", workflow)
        self.assertIn("./scripts/publish-release.sh", workflow)
        publish_job = workflow.split("\n  publish:\n", 1)[1]
        self.assertIn("actions/setup-python@a26af69be951a213d495a4c3e4e4022e16d87065", publish_job)
        self.assertIn("cryptography==50.0.0", publish_job)
        self.assertNotIn("android_release_build.sh", workflow)
        self.assertNotIn("check-android-release.sh", workflow)
        self.assertNotIn("docker build", workflow)
        self.assertNotRegex(workflow, r"uses:\s+[^\n]+@v\d+(?:\s|$)")

    def test_android_release_check_packages_the_verified_apk_with_stable_name(self) -> None:
        script = (ROOT / "scripts" / "check-android-release.sh").read_text()
        root_gate = (ROOT / "scripts" / "verify-android-reference-contract.sh").read_text()
        self.assertIn('workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)', script)
        self.assertIn('cd "${workspace}"', script)
        self.assertIn("./scripts/verify-android-release-version.sh", script)
        self.assertIn("./scripts/verify-android-reference-contract.sh", script)
        self.assertNotIn("--location", root_gate)
        self.assertNotIn(" -L", root_gate)
        self.assertIn("./gradlew --no-daemon --dependency-verification strict", script)
        self.assertIn("testDebugUnitTest lintRelease assembleRelease", script)
        self.assertIn("medicine-${tag}-arm64-v8a-unsigned.apk", script)
        self.assertIn("app-release-unsigned.apk", script)
        self.assertIn("verify-no-ocr-android-artifact.py", script)
        self.assertIn("aapt", script)
        self.assertIn("-u MEDICINE_ANDROID_KEYSTORE_PATH", script)
        self.assertNotIn("${MEDICINE_ANDROID_KEYSTORE", script)
        self.assertNotIn("apksigner", script)
        self.assertIn("versionCode", script)
        self.assertIn("versionName", script)

    def test_android_release_signing_certificate_fingerprint_is_source_controlled(self) -> None:
        fingerprint = ROOT / "deploy" / "android-release-signing-certificate.sha256"
        self.assertTrue(fingerprint.is_file())
        value = fingerprint.read_text().strip()
        self.assertRegex(value, r"^[0-9a-f]{64}$")

    def test_reference_contract_gate_uses_shared_development_url_without_release_env(self) -> None:
        script = ROOT / "scripts" / "verify-android-reference-contract.sh"
        expected_url = "https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/reference/v2/latest.json"
        expected_full = (
            "https://pub-539f06de795a469c85ab40570a8634a2.r2.dev/"
            "reference/v2/contracts/1/full/" + "2" * 64 + ".sqlite.gz"
        )
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            bin_dir = root / "bin"
            bin_dir.mkdir()
            log_path = root / "calls.log"
            curl = bin_dir / "curl"
            curl.write_text(
                "#!/bin/sh\n"
                "output=''\n"
                "url=''\n"
                "while [ $# -gt 0 ]; do\n"
                "  case \"$1\" in\n"
                "    --output) output=$2; shift 2 ;;\n"
                "    http*) url=$1; shift ;;\n"
                "    *) shift ;;\n"
                "  esac\n"
                "done\n"
                "printf 'curl:%s\\n' \"$url\" >> \"$REFERENCE_GATE_TEST_LOG\"\n"
                "printf '{}' > \"$output\"\n"
            )
            curl.chmod(0o755)
            python = bin_dir / "python"
            python.write_text(
                "#!/bin/sh\n"
                "printf 'python:%s\\n' \"$*\" >> \"$REFERENCE_GATE_TEST_LOG\"\n"
                "case \" $* \" in\n"
                "  *' --print-full-key '*) printf '%s\\n' 'reference/v2/contracts/1/full/" + "2" * 64 + ".sqlite.gz' ;;\n"
                "esac\n"
            )
            python.chmod(0o755)
            env = os.environ.copy()
            env.pop("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", None)
            env.pop("MEDICINE_PYTHON_BIN", None)
            env["REFERENCE_GATE_TEST_LOG"] = str(log_path)
            env["PATH"] = f"{bin_dir}:{env['PATH']}"
            result = subprocess.run(
                ["bash", str(script), "--verify-full-artifact"],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )
            calls = log_path.read_text().splitlines() if log_path.exists() else []

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"curl:{expected_url}", calls)
        self.assertIn(f"curl:{expected_full}", calls)
        self.assertTrue(any(call.startswith("python:./scripts/verify-reference-contract-root.py") for call in calls))
        self.assertTrue(any("--full-artifact" in call for call in calls if call.startswith("python:")))

    def test_release_publish_uses_draft_as_retry_boundary_and_requires_apk_and_checksum(self) -> None:
        ensure = (ROOT / "scripts" / "ensure-release-draft.sh").read_text()
        publish = (ROOT / "scripts" / "publish-release.sh").read_text()
        workflow = (ROOT / ".github" / "workflows" / "android-release.yml").read_text()

        self.assertIn("--draft", ensure)
        self.assertIn("--verify-tag", ensure)
        self.assertIn("already published; refusing to reuse it", ensure)
        self.assertIn('apks=("${asset_dir}"/*.apk)', publish)
        self.assertIn("SHA256SUMS", publish)
        self.assertIn("THIRD_PARTY_NOTICES.txt", publish)
        self.assertIn("android/app/src/main/assets/THIRD_PARTY_NOTICES.txt", workflow)
        self.assertIn("sha256sum ./*.apk THIRD_PARTY_NOTICES.txt > SHA256SUMS", workflow)
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
        self.assertIn("durable release signing identity", docs)
        self.assertIn("GCP Secret Manager", docs)
        self.assertIn("Workload Identity Federation", docs)
        self.assertIn("yakbom-android-signing-vault", docs)
        self.assertIn("does not require GitHub signing secrets", docs)
        self.assertNotIn("self-hosted `wsl-ci` runner", docs)
        self.assertIn("GitHub-hosted", docs)
        self.assertIn("GitHub-hosted", docs)
        self.assertIn("30-day", docs)
        self.assertIn("backup", docs)
        self.assertIn("Google-managed encryption", docs)
        self.assertNotIn("ANDROID_RELEASE_KEYSTORE_BASE64", docs)
        self.assertIn("API 28", docs)
        self.assertIn("v0.1.0", docs)
        self.assertNotIn("docs/android-releasing.md", readme)


if __name__ == "__main__":
    unittest.main()
