from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
COMPOSE = ROOT / "compose.yaml"
OCR_DOCS = (
    ROOT / "browser_ocr" / "corpus" / "README.md",
    ROOT / "browser_ocr" / "detection" / "README.md",
    ROOT / "browser_ocr" / "finetune" / "README.md",
    ROOT / "browser_ocr" / "document_parsing" / "README.md",
)
WRITABLE_ML_SERVICES = (
    "ocr-detection-benchmark",
    "ocr-detector-train",
    "ocr-detector-export-paddle",
    "ocr-detector-convert",
    "ocr-detector-candidate-eval",
    "ocr-corpus",
    "ocr-parser-data",
    "ocr-parser-train",
    "ocr-parser-eval-model",
    "ocr-parser-export-model",
    "ocr-finetune",
    "ocr-finetune-train",
    "ocr-full-document",
    "ocr-parser-real",
    "ocr-parser-synthetic-runtime",
)


def compose_service_block(text: str, service: str) -> str:
    match = re.search(
        rf"(?ms)^  {re.escape(service)}:\n(.*?)(?=^  [a-zA-Z0-9_-]+:\n|\Z)",
        text,
    )
    if match is None:
        raise AssertionError(f"missing Compose service: {service}")
    return match.group(0)


class OcrArtifactStoragePolicyTest(unittest.TestCase):
    def test_writable_ml_services_mount_durable_artifact_root(self) -> None:
        compose = COMPOSE.read_text(encoding="utf-8")
        expected_source = (
            "source: ${MEDICINE_ARTIFACTS_DIR:-${HOME}/dev/.artifacts/medicine}"
        )

        for service in WRITABLE_ML_SERVICES:
            with self.subTest(service=service):
                block = compose_service_block(compose, service)
                self.assertIn(expected_source, block)
                self.assertIn("target: /artifacts", block)
                self.assertIn("create_host_path: false", block)

    def test_ml_pipeline_docs_document_durable_artifact_mount(self) -> None:
        for path in OCR_DOCS:
            text = path.read_text(encoding="utf-8")
            with self.subTest(path=path.relative_to(ROOT)):
                self.assertIn("/artifacts", text)
                self.assertIn("~/dev/.artifacts/medicine", text)

    def test_detector_benchmark_default_is_durable(self) -> None:
        cli = (ROOT / "browser_ocr" / "detection" / "cli.mjs").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            'option(args, "--output", "/artifacts/ocr/evaluations/detection/zero-shot")',
            cli,
        )

    def test_training_view_docs_keep_hardlink_source_on_artifact_mount(self) -> None:
        readme = (ROOT / "browser_ocr" / "finetune" / "README.md").read_text(
            encoding="utf-8"
        )
        self.assertIn(
            "--manifest /artifacts/ocr/corpora/unified-360/views/recognition/manifest.json",
            readme,
        )
        self.assertIn(
            "--split /artifacts/ocr/corpora/unified-360/views/recognition/document-split.json",
            readme,
        )


if __name__ == "__main__":
    unittest.main()