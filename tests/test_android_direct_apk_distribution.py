import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class AndroidDirectApkDistributionTest(unittest.TestCase):
    def test_direct_apk_path_keeps_public_identity_and_no_ocr_verification(self) -> None:
        gradle = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        release = (ROOT / "scripts/android_release_build.sh").read_text(encoding="utf-8")
        github_release = (ROOT / "scripts/check-android-release.sh").read_text(encoding="utf-8")

        self.assertIn('applicationId = "kr.yakbom.app"', gradle)
        self.assertIn("minSdk = 28", gradle)
        self.assertIn("targetSdk = 35", gradle)
        self.assertIn("buildConfigScript.set(", gradle)
        self.assertIn("buildConfigScript.get().asFile.absolutePath", gradle)
        self.assertIn("verify-no-ocr-android-artifact.py", release)
        self.assertIn("package: name='kr.yakbom.app'", release)
        self.assertNotIn("MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL", release)
        self.assertIn("./scripts/android_release_build.sh", github_release)


if __name__ == "__main__":
    unittest.main()