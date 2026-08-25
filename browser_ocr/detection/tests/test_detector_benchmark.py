import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path

import numpy as np

from browser_ocr.detection.runtime import _verify_extracted_assets, _verify_runtime_assets
from browser_ocr.detection.detector_benchmark import (
    _verify_optional_onnx_sha,
    db_postprocess,
    rectify_text_crop,
    resize_dimensions,
    validate_official_config,
)


class DetectorBenchmarkCoreTest(unittest.TestCase):
    def test_resize_dimensions_preserve_aspect_and_multiple_of_32(self):
        self.assertEqual(resize_dimensions(1280, 1600, 640), (512, 640))
        self.assertEqual(resize_dimensions(1280, 1600, 960), (768, 960))
        self.assertEqual(resize_dimensions(1280, 1600, 1280), (1024, 1280))
        width, height = resize_dimensions(641, 120, 640)
        self.assertEqual(width % 32, 0)
        self.assertEqual(height % 32, 0)
        self.assertLessEqual(max(width, height), 640 + 16)

    def test_db_postprocess_extracts_one_box_from_probability_map(self):
        probability = np.zeros((64, 64), dtype=np.float32)
        probability[20:44, 14:50] = 0.95
        boxes = db_postprocess(
            probability,
            source_width=640,
            source_height=640,
            threshold=0.3,
            box_threshold=0.6,
            max_candidates=1000,
            unclip_ratio=1.5,
        )
        self.assertEqual(len(boxes), 1)
        self.assertGreater(boxes[0]["score"], 0.9)
        self.assertEqual(len(boxes[0]["polygon"]), 4)
        for x, y in boxes[0]["polygon"]:
            self.assertGreaterEqual(x, 0)
            self.assertGreaterEqual(y, 0)
            self.assertLessEqual(x, 640)
            self.assertLessEqual(y, 640)

    def test_rectify_text_crop_warps_quad_to_horizontal_recognition_crop(self):
        image = np.zeros((120, 220, 3), dtype=np.uint8)
        image[20:80, 20:200] = 255
        crop = rectify_text_crop(
            image,
            [[24, 32], [194, 20], [198, 64], [28, 78]],
        )
        self.assertEqual(crop.ndim, 3)
        self.assertEqual(crop.shape[2], 3)
        self.assertGreater(crop.shape[1], crop.shape[0])
        self.assertGreater(crop.shape[1], 140)
        self.assertGreater(crop.mean(), 180)

    def test_rectify_text_crop_rotates_tall_text_region_for_recognizer(self):
        image = np.full((220, 120, 3), 200, dtype=np.uint8)
        crop = rectify_text_crop(
            image,
            [[36, 18], [76, 18], [76, 202], [36, 202]],
        )
        self.assertGreater(crop.shape[1], crop.shape[0])

    def test_extracted_detector_assets_must_match_pinned_archive_members(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            archive = root / "model.tar"
            extracted = root / "model"
            extracted.mkdir()
            onnx = extracted / "inference.onnx"
            config = extracted / "inference.yml"
            onnx.write_bytes(b"onnx-bytes")
            config.write_bytes(b"config-bytes")
            with tarfile.open(archive, "w") as stream:
                for name, content in (("model/inference.onnx", onnx.read_bytes()), ("model/inference.yml", config.read_bytes())):
                    info = tarfile.TarInfo(name)
                    info.size = len(content)
                    stream.addfile(info, io.BytesIO(content))
            onnx_sha, config_sha = _verify_extracted_assets(archive, "model", onnx, config)
            self.assertEqual(len(onnx_sha), 64)
            self.assertEqual(len(config_sha), 64)
            onnx.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "pinned archive"):
                _verify_extracted_assets(archive, "model", onnx, config)

    def test_trained_candidate_runtime_assets_are_hash_pinned_without_archive(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            onnx = root / "inference.onnx"
            config = root / "inference.yml"
            onnx.write_bytes(b"candidate-onnx")
            config.write_bytes(b"candidate-config")
            import hashlib

            onnx_sha = hashlib.sha256(onnx.read_bytes()).hexdigest()
            config_sha = hashlib.sha256(config.read_bytes()).hexdigest()
            model = {
                "archive_root": ".",
                "onnx_file": "inference.onnx",
                "config_file": "inference.yml",
                "sha256": onnx_sha,
                "onnx_sha256": onnx_sha,
                "config_sha256": config_sha,
            }
            verified = _verify_runtime_assets(root, "candidate", model)
            self.assertEqual(verified["asset_sha256"], onnx_sha)
            self.assertEqual(verified["onnx_sha256"], onnx_sha)
            self.assertEqual(verified["config_sha256"], config_sha)

            config.write_bytes(b"tampered")
            with self.assertRaisesRegex(ValueError, "config SHA-256"):
                _verify_runtime_assets(root, "candidate", model)

    def test_trained_candidate_runtime_requires_explicit_config_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "inference.onnx").write_bytes(b"candidate-onnx")
            (root / "inference.yml").write_bytes(b"candidate-config")
            import hashlib

            onnx_sha = hashlib.sha256((root / "inference.onnx").read_bytes()).hexdigest()
            model = {
                "archive_root": ".",
                "onnx_file": "inference.onnx",
                "config_file": "inference.yml",
                "sha256": onnx_sha,
                "onnx_sha256": onnx_sha,
            }
            with self.assertRaisesRegex(ValueError, "config_sha256"):
                _verify_runtime_assets(root, "candidate", model)

    def test_official_inference_yaml_must_match_pinned_db_settings(self):
        pinned = {
            "preprocess": {
                "color_mode": "BGR",
                "mean": [0.485, 0.456, 0.406],
                "std": [0.229, 0.224, 0.225],
            },
            "postprocess": {
                "threshold": 0.2,
                "box_threshold": 0.45,
                "max_candidates": 3000,
                "unclip_ratio": 1.4,
            },
        }
        config = """Global:\n  model_name: PP-OCRv6_small_det\nPreProcess:\n  transform_ops:\n  - DecodeImage:\n      channel_first: false\n      img_mode: BGR\n  - NormalizeImage:\n      mean: [0.485, 0.456, 0.406]\n      order: hwc\n      scale: 1./255.\n      std: [0.229, 0.224, 0.225]\nPostProcess:\n  name: DBPostProcess\n  thresh: 0.2\n  box_thresh: 0.45\n  max_candidates: 3000\n  unclip_ratio: 1.4\n"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference.yml"
            path.write_text(config, encoding="utf-8")
            validate_official_config(path, "PP-OCRv6_small_det", pinned)
            broken = json.loads(json.dumps(pinned))
            broken["postprocess"]["box_threshold"] = 0.6
            with self.assertRaisesRegex(ValueError, "box_threshold"):
                validate_official_config(path, "PP-OCRv6_small_det", broken)

    def test_candidate_runtime_key_can_bind_original_config_model_name(self):
        pinned = {
            "config_model_name": "PP-OCRv5_mobile_det",
            "preprocess": {
                "color_mode": "BGR",
                "mean": [0.485, 0.456, 0.406],
                "std": [0.229, 0.224, 0.225],
            },
            "postprocess": {
                "threshold": 0.3,
                "box_threshold": 0.6,
                "max_candidates": 1000,
                "unclip_ratio": 1.5,
            },
        }
        config = """Global:\n  model_name: PP-OCRv5_mobile_det\nPreProcess:\n  transform_ops:\n  - DecodeImage:\n      img_mode: BGR\n  - NormalizeImage:\n      mean: [0.485, 0.456, 0.406]\n      std: [0.229, 0.224, 0.225]\nPostProcess:\n  name: DBPostProcess\n  thresh: 0.3\n  box_thresh: 0.6\n  max_candidates: 1000\n  unclip_ratio: 1.5\n"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference.yml"
            path.write_text(config, encoding="utf-8")
            validate_official_config(path, "PP-OCRv5_mobile_det_candidate_deadbeef", pinned)

    def test_candidate_onnx_sha_is_verified_when_manifest_provides_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "inference.onnx"
            path.write_bytes(b"candidate-onnx")
            import hashlib

            expected = hashlib.sha256(path.read_bytes()).hexdigest()
            _verify_optional_onnx_sha(path, {"onnx_sha256": expected})
            with self.assertRaisesRegex(ValueError, "ONNX SHA-256"):
                _verify_optional_onnx_sha(path, {"onnx_sha256": "0" * 64})


if __name__ == "__main__":
    unittest.main()