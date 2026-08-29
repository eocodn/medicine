import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class OcrOptionalSurfaceTest(unittest.TestCase):
    def test_ui_build_filter_removes_ocr_surface_and_script(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "index.html").write_text(
                "before\n<!-- MEDICINE_OCR_START -->\nOCR UI\n<!-- MEDICINE_OCR_END -->\nafter\n",
                encoding="utf-8",
            )
            (output / "ocr-intake.js").write_text("ocr", encoding="utf-8")
            subprocess.run(
                [node, "--experimental-strip-types", str(ROOT / "ui/build-config.ts"), str(output), "disabled"],
                cwd=ROOT,
                check=True,
                env={**os.environ},
            )
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertNotIn("OCR UI", html)
            self.assertNotIn("MEDICINE_OCR_START", html)
            self.assertFalse((output / "ocr-intake.js").exists())

    def test_ui_build_filter_preserves_ocr_surface_when_enabled(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp)
            (output / "index.html").write_text(
                "before\n<!-- MEDICINE_OCR_START -->\nOCR UI\n<!-- MEDICINE_OCR_END -->\nafter\n",
                encoding="utf-8",
            )
            (output / "ocr-intake.js").write_text("ocr", encoding="utf-8")
            subprocess.run(
                [node, "--experimental-strip-types", str(ROOT / "ui/build-config.ts"), str(output), "enabled"],
                cwd=ROOT,
                check=True,
                env={**os.environ},
            )
            html = (output / "index.html").read_text(encoding="utf-8")
            self.assertIn("OCR UI", html)
            self.assertNotIn("MEDICINE_OCR_START", html)
            self.assertTrue((output / "ocr-intake.js").exists())

    def test_android_build_selects_exactly_one_ocr_native_source_set(self):
        build = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn('val isOcrEnabled = ocrAssetsDirectory != null', build)
        self.assertIn('src/ocr/java', build)
        self.assertIn('src/noOcr/java', build)
        self.assertIn('manifestPlaceholders["ocrFileProviderEnabled"]', build)
        self.assertIn('ocrEnabled.set(isOcrEnabled)', build)

    def test_main_activity_delegates_optional_ocr_integration(self):
        activity = (ROOT / "android/app/src/main/java/com/medicine/android/MainActivity.kt").read_text(encoding="utf-8")
        self.assertIn("OcrIntegration", activity)
        self.assertNotIn("MediaStore.ACTION_IMAGE_CAPTURE", activity)
        self.assertNotIn("FileProvider.getUriForFile", activity)
        self.assertNotIn('addPathHandler("/ocr-assets/"', activity)
        self.assertTrue((ROOT / "android/app/src/ocr/java/com/medicine/android/OcrIntegration.java").is_file())
        self.assertTrue((ROOT / "android/app/src/noOcr/java/com/medicine/android/OcrIntegration.java").is_file())


if __name__ == "__main__":
    unittest.main()
