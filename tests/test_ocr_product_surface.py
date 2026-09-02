import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RUNTIME = ROOT / "android/app/src/main/assets/ocr-assets"


class OcrProductSurfaceTest(unittest.TestCase):
    def test_ocr_is_unconditional_product_surface(self):
        build = (ROOT / "android/app/build.gradle.kts").read_text(encoding="utf-8")
        self.assertNotIn("MEDICINE_OCR_ASSETS_DIR", build)
        self.assertTrue((ROOT / "android/app/src/main/java/com/medicine/android/ProductCapabilityIntegration.java").is_file())

    def test_packaged_runtime_matches_its_manifest(self):
        manifest = json.loads((RUNTIME / "runtime-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(1, manifest["schema_version"])
        self.assertTrue(manifest["files"])
        for relative, expected in manifest["files"].items():
            path = RUNTIME / relative
            self.assertTrue(path.is_file(), relative)
            payload = path.read_bytes()
            self.assertEqual(expected["size_bytes"], len(payload), relative)
            self.assertEqual(expected["sha256"], hashlib.sha256(payload).hexdigest(), relative)

    def test_release_verifier_requires_packaged_runtime(self):
        verifier = ROOT / "scripts/verify-ocr-android-artifact.py"
        manifest = (RUNTIME / "runtime-manifest.json").read_bytes()
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "bad.apk"
            with zipfile.ZipFile(bad, "w") as archive:
                archive.writestr("assets/ocr-intake.js", b"ok")
            rejected = subprocess.run([sys.executable, str(verifier), str(bad)], text=True, capture_output=True)
            self.assertNotEqual(0, rejected.returncode)
            self.assertIn("OCR runtime manifest is missing", rejected.stderr)

            good = Path(tmp) / "good.apk"
            runtime_manifest = json.loads(manifest)
            with zipfile.ZipFile(good, "w") as archive:
                archive.writestr("assets/ocr-intake.js", b"ok")
                archive.writestr("assets/ocr-assets/runtime-manifest.json", manifest)
                for relative in runtime_manifest["files"]:
                    archive.writestr(f"assets/ocr-assets/{relative}", (RUNTIME / relative).read_bytes())
            accepted = subprocess.run([sys.executable, str(verifier), str(good)], text=True, capture_output=True)
            self.assertEqual(0, accepted.returncode, accepted.stderr)


if __name__ == "__main__":
    unittest.main()
