import hashlib
import tomllib
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AndroidDirectApkDistributionTest(unittest.TestCase):
    def test_direct_apk_path_keeps_public_identity_and_ocr_verification(self) -> None:
        gradle = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        release = (ROOT / "scripts/android_release_build.sh").read_text(encoding="utf-8")
        github_release = (ROOT / "scripts/check-android-release.sh").read_text(encoding="utf-8")

        self.assertIn('applicationId = "kr.yakbom.app"', gradle)
        self.assertIn("minSdk = 28", gradle)
        self.assertIn("targetSdk = 35", gradle)
        self.assertNotIn("MEDICINE_OCR_ASSETS_DIR", gradle)
        self.assertIn("PrepareSharedUiAssets", gradle)
        self.assertIn("verify-ocr-android-artifact.py", release)
        self.assertIn("package: name='kr.yakbom.app'", release)
        self.assertNotIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", release)
        self.assertIn("app-release-unsigned.apk", github_release)
        self.assertIn("-u MEDICINE_ANDROID_KEYSTORE_PATH", github_release)
        self.assertNotIn("${MEDICINE_ANDROID_KEYSTORE", github_release)

    def test_release_apk_carries_reviewed_third_party_notices_for_locked_dependencies(self) -> None:
        notice_path = ROOT / "android/app/src/main/assets/THIRD_PARTY_NOTICES.txt"
        notice = notice_path.read_text(encoding="utf-8")
        gradle_lock = ROOT / "android/app/gradle.lockfile"
        cargo_lock = ROOT / "rust/medicine_core/Cargo.lock"

        self.assertIn(
            f"Android lock SHA-256: {hashlib.sha256(gradle_lock.read_bytes()).hexdigest()}",
            notice,
        )
        self.assertIn(
            f"Rust lock SHA-256: {hashlib.sha256(cargo_lock.read_bytes()).hexdigest()}",
            notice,
        )

        android_runtime = {
            line.split("=", 1)[0]
            for line in gradle_lock.read_text(encoding="utf-8").splitlines()
            if "releaseRuntimeClasspath" in line and not line.startswith(("#", "empty="))
        }
        for coordinate in android_runtime:
            self.assertIn(coordinate, notice)

        cargo = tomllib.loads(cargo_lock.read_text(encoding="utf-8"))
        for package in cargo["package"]:
            if package["name"] == "medicine_core":
                continue
            self.assertIn(f'{package["name"]} {package["version"]}', notice)

        self.assertIn("Apache License", notice)
        self.assertIn("MIT License", notice)
        self.assertIn("BSD", notice)
        self.assertIn("Unicode", notice)


if __name__ == "__main__":
    unittest.main()