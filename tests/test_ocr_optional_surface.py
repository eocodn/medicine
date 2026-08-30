import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
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
            (output / "styles.css").write_text(
                "before\n/* MEDICINE_OCR_START */\n.ocr-import-card { display: block; }\n#ocr-status { min-height: 1px; }\n/* MEDICINE_OCR_END */\nafter\n",
                encoding="utf-8",
            )
            subprocess.run(
                [node, "--experimental-strip-types", str(ROOT / "ui/build-config.ts"), str(output), "disabled"],
                cwd=ROOT,
                check=True,
                env={**os.environ},
            )
            html = (output / "index.html").read_text(encoding="utf-8")
            css = (output / "styles.css").read_text(encoding="utf-8")
            self.assertNotIn("OCR UI", html)
            self.assertNotIn("MEDICINE_OCR_START", html)
            self.assertNotIn("MEDICINE_OCR_START", css)
            self.assertNotIn("ocr-import-card", css)
            self.assertNotIn("ocr-status", css)
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

    def test_no_ocr_prepared_sources_exclude_ocr_code(self):
        node = shutil.which("node")
        if node is None:
            self.skipTest("node is unavailable")
        with tempfile.TemporaryDirectory() as tmp:
            project = Path(tmp) / "ui"
            source = project / "src"
            output = Path(tmp) / "prepared"
            source.mkdir(parents=True)
            (project / "tsconfig.json").write_text(
                '{"compilerOptions":{"target":"ES2022","module":"None"},"include":["src/**/*.ts"]}\n',
                encoding="utf-8",
            )
            (source / "app.ts").write_text(
                'const common = 1;\n// MEDICINE_OCR_START\nconst pendingParserDraft = 2;\n// MEDICINE_OCR_ELSE\nconst manualOnly = 3;\n// MEDICINE_OCR_END\n',
                encoding="utf-8",
            )
            (source / "ocr-intake.ts").write_text('this is deliberately invalid TypeScript !!!', encoding="utf-8")
            subprocess.run(
                [node, "--experimental-strip-types", str(ROOT / "ui/build-capability.ts"), "prepare", str(source), str(output), "disabled"],
                cwd=ROOT, check=True, env={**os.environ},
            )
            prepared = (output / "app.ts").read_text(encoding="utf-8")
            self.assertIn("manualOnly", prepared)
            self.assertNotIn("pendingParserDraft", prepared)
            self.assertFalse((output / "ocr-intake.ts").exists())

    def test_android_build_selects_exactly_one_ocr_native_source_set(self):
        build = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertIn('val isOcrEnabled = ocrAssetsDirectory != null', build)
        self.assertIn('src/ocr/java', build)
        self.assertIn('src/noOcr/java', build)
        self.assertIn('src/ocr/AndroidManifest.xml', build)
        self.assertNotIn('ocrFileProviderEnabled', build)
        self.assertIn('ocrEnabled.set(isOcrEnabled)', build)
        self.assertIn('buildConfigScript.set(', build)
        self.assertIn('"../ui/build-config.ts"', build)
        self.assertIn('buildConfigScript.get().asFile.absolutePath', build)

    def test_main_activity_delegates_optional_ocr_integration(self):
        activity = (ROOT / "android/app/src/main/java/com/medicine/android/MainActivity.kt").read_text(encoding="utf-8")
        self.assertIn("ProductCapabilityIntegration", activity)
        self.assertNotIn("MediaStore.ACTION_IMAGE_CAPTURE", activity)
        self.assertNotIn("FileProvider.getUriForFile", activity)
        self.assertNotIn('addPathHandler("/ocr-assets/"', activity)
        self.assertTrue((ROOT / "android/app/src/ocr/java/com/medicine/android/ProductCapabilityIntegration.java").is_file())
        self.assertTrue((ROOT / "android/app/src/noOcr/java/com/medicine/android/ProductCapabilityIntegration.java").is_file())

    def test_android_release_artifact_verifier_rejects_ocr_payloads(self):
        verifier = ROOT / "scripts" / "verify-no-ocr-android-artifact.py"
        self.assertTrue(verifier.is_file())
        with tempfile.TemporaryDirectory() as tmp:
            clean = Path(tmp) / "clean.apk"
            dirty = Path(tmp) / "dirty.apk"
            with zipfile.ZipFile(clean, "w") as archive:
                archive.writestr("base/assets/index.html", "<main>약봄</main>")
                archive.writestr("base/lib/arm64-v8a/libmedicine_core.so", b"native")
            with zipfile.ZipFile(dirty, "w") as archive:
                archive.writestr("base/assets/index.html", "<button id='ocr-import'>OCR</button>")
                archive.writestr("base/assets/ocr-intake.js", "ocr")

            accepted = subprocess.run(
                [sys.executable, str(verifier), str(clean)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )
            rejected = subprocess.run(
                [sys.executable, str(verifier), str(dirty)],
                cwd=ROOT,
                capture_output=True,
                text=True,
                check=False,
            )

        self.assertEqual(accepted.returncode, 0, accepted.stderr)
        self.assertNotEqual(rejected.returncode, 0)
        self.assertIn("OCR payload", rejected.stderr)


if __name__ == "__main__":
    unittest.main()
