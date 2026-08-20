from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from browser_ocr.document_parsing.tests.test_training_dataset import _doc, _rewrite_samples, _runtime_profile
from browser_ocr.document_parsing.training_dataset import ParserDatasetError, load_parser_dataset, write_parser_dataset


class ParserSourceBindingTest(unittest.TestCase):
    def test_builder_provenance_must_match_document_source_binding_on_write_and_load(self) -> None:
        runtime_document = _doc(split="val")
        runtime_profile = _runtime_profile()
        runtime_document["observation"]["kind"] = "runtime_ocr"
        runtime_document["observation"]["profile"] = runtime_profile
        runtime_producer = json.loads(json.dumps(runtime_profile))
        runtime_producer.pop("image_sha256")
        runtime_metadata = {
            "builder": "parser_runtime_builder_v2",
            "truth_samples_sha256": "9" * 64,
            "observation_kind": "runtime_ocr",
            "split": "val",
            "ocr_producer": runtime_producer,
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "truth_samples_sha256|source binding"):
                write_parser_dataset(
                    root / "runtime-forged",
                    dataset_id="runtime-forged-truth",
                    documents=[runtime_document],
                    metadata={**runtime_metadata, "truth_samples_sha256": "f" * 64},
                )
            manifest = write_parser_dataset(
                root / "runtime-load",
                dataset_id="runtime-bound-truth",
                documents=[runtime_document],
                metadata=runtime_metadata,
            )
            persisted = list(load_parser_dataset(manifest).documents)
            persisted[0]["source_binding"]["truth_samples_sha256"] = "f" * 64
            _rewrite_samples(manifest, persisted)
            with self.assertRaisesRegex(ParserDatasetError, "truth_samples_sha256|source binding"):
                load_parser_dataset(manifest)

        real_document = _doc(source_kind="real_deidentified", split="val")
        real_document["observation"]["kind"] = "runtime_ocr"
        real_document["observation"]["profile"] = _runtime_profile()
        real_metadata = {
            "builder": "real_annotation_finalize_v1",
            "source_dataset_id": "real-fixture",
            "source_manifest_sha256": "c" * 64,
            "source_samples_sha256": "d" * 64,
        }
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            with self.assertRaisesRegex(ParserDatasetError, "source identity|source binding"):
                write_parser_dataset(
                    root / "real-forged",
                    dataset_id="real-forged-source",
                    documents=[real_document],
                    metadata={
                        **real_metadata,
                        "source_dataset_id": "totally-unrelated-source",
                        "source_manifest_sha256": "e" * 64,
                        "source_samples_sha256": "f" * 64,
                    },
                )
            manifest = write_parser_dataset(
                root / "real-load",
                dataset_id="real-bound-source",
                documents=[real_document],
                metadata=real_metadata,
            )
            persisted = list(load_parser_dataset(manifest).documents)
            persisted[0]["source_binding"]["source_dataset_id"] = "totally-unrelated-source"
            _rewrite_samples(manifest, persisted)
            with self.assertRaisesRegex(ParserDatasetError, "source identity|source binding"):
                load_parser_dataset(manifest)


if __name__ == "__main__":
    unittest.main()
