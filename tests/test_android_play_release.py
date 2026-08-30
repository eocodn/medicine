import os
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path


class AndroidPlayReleaseTest(unittest.TestCase):
    def test_android_play_bundle_script_builds_signed_no_ocr_aab(self) -> None:
        script = Path("scripts/android_play_bundle.sh")
        self.assertTrue(script.is_file())
        text = script.read_text()

        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", text)
        self.assertIn("MEDICINE_ANDROID_UPLOAD_CERT_SHA256", text)
        self.assertIn("MEDICINE_OCR_ASSETS_DIR", text)
        self.assertIn("bundleRelease", text)
        self.assertNotIn("assembleRelease", text)
        self.assertIn("app-release.aab", text)
        self.assertIn("verify-signed-android-bundle.sh", text)
        self.assertIn("verify-android-reference-contract.sh", text)
        self.assertIn("--verify-full-artifact", text)
        self.assertIn('validate --bundle="$aab"', text)
        self.assertIn('java -jar "$BUNDLETOOL_JAR" dump manifest', text)
        self.assertIn("/manifest/@package", text)
        self.assertIn("/manifest/@android:versionCode", text)
        self.assertIn("/manifest/@android:versionName", text)
        self.assertIn("/manifest/uses-sdk/@android:targetSdkVersion", text)
        self.assertIn("kr.yakbom.app", text)
        self.assertIn("targetSdk 36", text)
        self.assertIn("verify-no-ocr-android-artifact.py", text)
        self.assertIn("MEDICINE_RELEASE_SOURCE_COMMIT", text)
        self.assertIn("source_commit", text)
        self.assertIn("sha256sum", text)
        self.assertIn("provenance", text)

        dockerfile = Path("Dockerfile.android").read_text()
        self.assertIn("bundletool-all-1.18.3.jar", dockerfile)
        self.assertIn("a099cfa1543f55593bc2ed16a70a7c67fe54b1747bb7301f37fdfd6d91028e29", dockerfile)
        self.assertIn("BUNDLETOOL_JAR", dockerfile)
        self.assertIn("cryptography==50.0.0", dockerfile)
        self.assertIn("MEDICINE_PYTHON_BIN", dockerfile)

    def test_play_bundle_certification_writes_exact_sha_provenance(self) -> None:
        release_script = Path("scripts/android_play_bundle.sh").read_text()
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            workspace = root / "workspace"
            scripts = workspace / "scripts"
            android = workspace / "android"
            output = root / "artifacts"
            bin_dir = root / "bin"
            scripts.mkdir(parents=True)
            android.mkdir()
            bin_dir.mkdir()
            output.mkdir()
            log = root / "calls.log"

            play = scripts / "android_play_bundle.sh"
            play.write_text(release_script)
            play.chmod(0o755)
            reference_gate = scripts / "verify-android-reference-contract.sh"
            reference_gate.write_text(
                "#!/bin/sh\n"
                "printf 'reference:%s\\n' \"$*\" >> \"$PLAY_TEST_LOG\"\n"
            )
            reference_gate.chmod(0o755)
            signer_gate = scripts / "verify-signed-android-bundle.sh"
            signer_gate.write_text(
                "#!/bin/sh\n"
                "printf 'signer:%s:%s\\n' \"$1\" \"$2\" >> \"$PLAY_TEST_LOG\"\n"
            )
            signer_gate.chmod(0o755)
            (scripts / "verify-no-ocr-android-artifact.py").write_text("# stub\n")

            gradlew = android / "gradlew"
            gradlew.write_text(
                "#!/bin/sh\n"
                "printf 'gradle:%s\\n' \"$*\" >> \"$PLAY_TEST_LOG\"\n"
                "mkdir -p app/build/outputs/bundle/release\n"
                "printf 'signed-aab-fixture' > app/build/outputs/bundle/release/app-release.aab\n"
            )
            gradlew.chmod(0o755)

            java = bin_dir / "java"
            java.write_text(
                "#!/bin/sh\n"
                "printf 'java:%s\\n' \"$*\" >> \"$PLAY_TEST_LOG\"\n"
                "case \" $* \" in\n"
                "  *' validate --bundle='*) exit 0 ;;\n"
                "  *'/manifest/@package'*) printf 'kr.yakbom.app' ;;\n"
                "  *'/manifest/@android:versionCode'*) printf '23' ;;\n"
                "  *'/manifest/@android:versionName'*) printf '1.4.0' ;;\n"
                "  *'/manifest/uses-sdk/@android:targetSdkVersion'*) printf '36' ;;\n"
                "  *) exit 8 ;;\n"
                "esac\n"
            )
            java.chmod(0o755)
            for name in ("jarsigner", "keytool"):
                stub = bin_dir / name
                stub.write_text("#!/bin/sh\nexit 0\n")
                stub.chmod(0o755)
            python3 = bin_dir / "python3"
            python3.write_text(
                "#!/bin/sh\n"
                "printf 'python3:%s\\n' \"$*\" >> \"$PLAY_TEST_LOG\"\n"
            )
            python3.chmod(0o755)

            bundletool = root / "bundletool.jar"
            bundletool.write_bytes(b"bundletool")
            keystore = root / "upload.jks"
            keystore.write_bytes(b"keystore")
            source_commit = "cd" * 20
            upload_sha = "ab" * 32
            env = {
                **os.environ,
                "PATH": f"{bin_dir}:{os.environ['PATH']}",
                "PLAY_TEST_LOG": str(log),
                "ANDROID_HOME": str(root / "android-sdk"),
                "BUNDLETOOL_JAR": str(bundletool),
                "MEDICINE_ANDROID_VERSION_CODE": "23",
                "MEDICINE_ANDROID_VERSION_NAME": "1.4.0",
                "MEDICINE_ANDROID_KEYSTORE_PATH": str(keystore),
                "MEDICINE_ANDROID_KEYSTORE_PASSWORD": "store-secret",
                "MEDICINE_ANDROID_KEY_ALIAS": "upload",
                "MEDICINE_ANDROID_KEY_PASSWORD": "key-secret",
                "MEDICINE_ANDROID_UPLOAD_CERT_SHA256": upload_sha.upper(),
                "MEDICINE_RELEASE_SOURCE_COMMIT": source_commit.upper(),
                "MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL": "https://reference.yakbom.example/",
            }
            result = subprocess.run(
                ["sh", str(play), str(output)],
                cwd=workspace,
                env=env,
                capture_output=True,
                text=True,
                check=False,
            )

            artifact = output / "yakbom-v1.4.0.aab"
            checksum = output / "yakbom-v1.4.0.aab.sha256"
            provenance = output / "yakbom-v1.4.0.provenance.txt"
            calls = log.read_text().splitlines()
            artifact_sha = hashlib.sha256(artifact.read_bytes()).hexdigest() if artifact.exists() else ""
            checksum_text = checksum.read_text().strip() if checksum.exists() else ""
            provenance_text = provenance.read_text() if provenance.exists() else ""

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(artifact_sha, hashlib.sha256(b"signed-aab-fixture").hexdigest())
        self.assertEqual(checksum_text, f"{artifact_sha}  yakbom-v1.4.0.aab")
        self.assertIn(f"source_commit={source_commit}", provenance_text)
        self.assertIn(f"artifact_sha256={artifact_sha}", provenance_text)
        self.assertIn(f"upload_certificate_sha256={upload_sha}", provenance_text)
        self.assertIn("reference_base_url=https://reference.yakbom.example/", provenance_text)
        self.assertEqual(calls[0], "reference:")
        validate_index = next(index for index, call in enumerate(calls) if " validate --bundle=" in call)
        manifest_index = next(index for index, call in enumerate(calls) if "/manifest/@package" in call)
        self.assertLess(validate_index, manifest_index)
        self.assertTrue(any(call.endswith(f":{upload_sha}") for call in calls if call.startswith("signer:")))
        self.assertEqual(calls[-1], "reference:--verify-full-artifact")

    def test_android_bundle_signature_verifier_rejects_unsigned_or_wrong_certificate(self) -> None:
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
            keytool = bin_dir / "keytool"
            keytool.write_text(
                "#!/bin/sh\n"
                "printf 'Certificate fingerprints:\\n'\n"
                "printf '         SHA256: %s\\n' \"$KEYTOOL_TEST_SHA256\"\n"
            )
            keytool.chmod(0o755)
            base_env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
            expected = "ab" * 32
            rendered = ":".join(expected[index:index + 2].upper() for index in range(0, 64, 2))

            unsigned = subprocess.run(
                ["sh", str(verifier), str(bundle), expected],
                env={
                    **base_env,
                    "JARSIGNER_TEST_REPORT": "jar is unsigned.",
                    "KEYTOOL_TEST_SHA256": rendered,
                },
                capture_output=True,
                text=True,
                check=False,
            )
            partial = subprocess.run(
                ["sh", str(verifier), str(bundle), expected],
                env={
                    **base_env,
                    "KEYTOOL_TEST_SHA256": rendered,
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
            wrong_certificate = subprocess.run(
                ["sh", str(verifier), str(bundle), "cd" * 32],
                env={
                    **base_env,
                    "KEYTOOL_TEST_SHA256": rendered,
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
            signed = subprocess.run(
                ["sh", str(verifier), str(bundle), expected.upper()],
                env={
                    **base_env,
                    "KEYTOOL_TEST_SHA256": rendered,
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
        self.assertNotEqual(wrong_certificate.returncode, 0)
        self.assertIn("certificate SHA-256", wrong_certificate.stderr)
        self.assertEqual(signed.returncode, 0, signed.stderr)

    def test_android_play_release_documentation_separates_code_and_operator_gates(self) -> None:
        docs = Path("docs/android-play-releasing.md")
        self.assertTrue(docs.is_file())
        text = docs.read_text()

        self.assertIn("kr.yakbom.app", text)
        self.assertIn("targetSdk 36", text)
        self.assertIn("android_play_bundle.sh", text)
        self.assertIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", text)
        self.assertIn("MEDICINE_ANDROID_UPLOAD_CERT_SHA256", text)
        self.assertIn("Play App Signing", text)
        self.assertIn("upload key", text)
        self.assertIn("bundletool validate", text)
        self.assertIn("source commit", text)
        self.assertIn("AAB SHA-256", text)
        self.assertIn("full reference artifact", text)
        self.assertIn("개인정보처리방침", text)
        self.assertIn("Health Apps", text)
        self.assertIn("MFDS", text)
        self.assertIn("데이터 이용조건", text)


if __name__ == "__main__":
    unittest.main()