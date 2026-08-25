from __future__ import annotations

import argparse
import shutil
import time
from pathlib import Path

from .crop_refinement import refine_prediction_crops
from .dataset import DatasetError
from .full_document import build_document_regions, sort_text_predictions
from .full_document_cli import (
    _exclusive_lock,
    _read_json_object,
    _sha256_file,
    _write_json_atomic,
    build_ocr_producer_profile,
    load_selected_recognizer,
)
from .orientation_runtime import resolve_page_orientation
from .recognizer_runtime import PersistentRecognizer


def _orientation_probe_quality(
    recognized: dict[str, dict[str, object]],
    paths: list[str],
) -> float:
    if not paths:
        return 0.0
    quality = 0.0
    for path in paths:
        item = recognized[path]
        text = "".join(str(item.get("text") or "").split())
        score = float(item.get("score") or 0.0)
        content = min(len(text) / 3.0, 1.0)
        quality += score * content
    return max(0.0, min(1.0, quality / len(paths)))


class FullDocumentRuntime:
    """Reusable detector + recognizer runtime for one producer profile."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.args = args
        self.recognizer = load_selected_recognizer(args.baseline_result)
        self.producer = build_ocr_producer_profile(args, self.recognizer)

        from browser_ocr.detection.runtime import load_detector_runtime

        self.detector = load_detector_runtime(
            model_manifest_path=Path(args.detector_manifest),
            model_root=Path(args.detector_root),
            model_name=args.detector_model,
            detector_edge=args.detector_edge,
            threads=args.detector_threads,
        )
        if (
            self.detector.onnx_sha256 != self.producer["detector_onnx_sha256"]
            or self.detector.config_sha256 != self.producer["detector_config_sha256"]
        ):
            raise DatasetError("loaded detector assets changed after OCR producer profile resolution")

        self.recognition = PersistentRecognizer(
            paddleocr_root=Path(args.paddleocr_root),
            config_path=Path(self.recognizer["config"]),
            checkpoint=Path(self.recognizer["checkpoint"]),
            use_gpu=args.recognizer_device == "gpu",
        )
        if build_ocr_producer_profile(args, self.recognizer) != self.producer:
            raise DatasetError("OCR producer profile changed while persistent runtime was initialized")

    def _recognize(self, crop_paths: list[str], *, output_path: Path, log_path: Path) -> dict[str, dict[str, object]]:
        recognized = self.recognition.recognize_paths(crop_paths)
        output_path.write_text(
            "".join(
                f"{path}\t{recognized[path]['text']}\t{recognized[path]['score']}\n"
                for path in crop_paths
            ),
            encoding="utf-8",
        )
        log_path.write_text(
            f"persistent recognizer reused one loaded model for {len(crop_paths)} crops\n",
            encoding="utf-8",
        )
        return recognized

    def _recognize_orientation_probes(self, probes: dict[int, list[object]], *, output_dir: Path) -> dict[int, float]:
        import cv2

        probe_dir = output_dir / "orientation-probes"
        shutil.rmtree(probe_dir, ignore_errors=True)
        probe_dir.mkdir(parents=True)
        paths_by_rotation: dict[int, list[str]] = {rotation: [] for rotation in probes}
        crop_paths: list[str] = []
        counter = 0
        for rotation in probes:
            for crop in probes[rotation]:
                counter += 1
                path = probe_dir / f"region-{counter:04d}.png"
                if not cv2.imwrite(str(path), crop):
                    raise DatasetError(f"failed to write orientation probe crop: {path}")
                resolved = str(path.resolve())
                paths_by_rotation[rotation].append(resolved)
                crop_paths.append(resolved)
        if not crop_paths:
            return {rotation: 0.0 for rotation in probes}
        recognized = self._recognize(
            crop_paths,
            output_path=output_dir / "orientation-recognition.txt",
            log_path=output_dir / "orientation-recognition.log",
        )
        return {
            rotation: _orientation_probe_quality(recognized, paths_by_rotation[rotation])
            for rotation in probes
        }

    def run(self, *, image_path: str | Path, output_dir: str | Path) -> dict[str, object]:
        image_path = Path(image_path).resolve()
        if not image_path.is_file():
            raise DatasetError(f"input image does not exist: {image_path}")
        output_dir = Path(output_dir).resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        state_path = output_dir / "state.json"
        result_path = output_dir / "result.json"
        profile = {**self.producer, "image_sha256": _sha256_file(image_path)}

        with _exclusive_lock(output_dir / ".pipeline.lock"):
            if state_path.is_file():
                state = _read_json_object(state_path, "full-document state")
                if state.get("profile") != profile:
                    raise DatasetError("full-document output profile differs from the requested inputs/models")
                if state.get("status") == "completed":
                    expected_result_sha = state.get("result_sha256")
                    if not isinstance(expected_result_sha, str) or len(expected_result_sha) != 64:
                        raise DatasetError("completed full-document state is missing result SHA-256")
                    if not result_path.is_file() or _sha256_file(result_path) != expected_result_sha:
                        raise DatasetError("completed full-document result SHA-256 mismatch")
                    result = _read_json_object(result_path, "full-document result")
                    if result.get("profile") != profile or result.get("status") != "ok":
                        raise DatasetError("completed full-document state/result disagree")
                    return result
            elif any(path.name != ".pipeline.lock" for path in output_dir.iterdir()):
                raise DatasetError("full-document output directory is non-empty without authoritative state")

            _write_json_atomic(state_path, {"schema_version": 2, "status": "running", "profile": profile})
            crop_dir = output_dir / "crops"
            shutil.rmtree(crop_dir, ignore_errors=True)
            crop_dir.mkdir(parents=True)
            for stale in (
                output_dir / "recognition.txt",
                output_dir / "recognition.log",
                output_dir / "orientation-recognition.txt",
                output_dir / "orientation-recognition.log",
                result_path,
            ):
                stale.unlink(missing_ok=True)

            try:
                import cv2

                image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                if image is None:
                    raise DatasetError(f"failed to decode input image: {image_path}")
                source_height, source_width = image.shape[:2]

                detector_started = time.perf_counter()
                predictions = sort_text_predictions(self.detector.predict(image))
                detector_ms = (time.perf_counter() - detector_started) * 1000.0

                orientation_started = time.perf_counter()
                image, predictions, orientation = resolve_page_orientation(
                    image,
                    predictions,
                    probe_scorer=lambda probes: self._recognize_orientation_probes(dict(probes), output_dir=output_dir),
                )
                orientation_ms = (time.perf_counter() - orientation_started) * 1000.0

                prediction_crops = refine_prediction_crops(image, predictions)
                predictions = [prediction for prediction, _ in prediction_crops]
                crop_paths: list[str] = []
                for index, (_, crop) in enumerate(prediction_crops, start=1):
                    crop_path = crop_dir / f"region-{index:04d}.png"
                    if not cv2.imwrite(str(crop_path), crop):
                        raise DatasetError(f"failed to write recognition crop: {crop_path}")
                    crop_paths.append(str(crop_path.resolve()))

                recognition_ms = 0.0
                if crop_paths:
                    recognition_started = time.perf_counter()
                    recognized = self._recognize(
                        crop_paths,
                        output_path=output_dir / "recognition.txt",
                        log_path=output_dir / "recognition.log",
                    )
                    recognition_ms = (time.perf_counter() - recognition_started) * 1000.0
                    regions = build_document_regions(predictions, crop_paths, recognized)
                    recognition_status = "ok"
                else:
                    regions = []
                    recognition_status = "skipped_no_detections"

                height, width = image.shape[:2]
                result = {
                    "schema_version": 2,
                    "status": "ok",
                    "profile": profile,
                    "image": {
                        "path": str(image_path),
                        "sha256": profile["image_sha256"],
                        "width": width,
                        "height": height,
                        "source_width": source_width,
                        "source_height": source_height,
                    },
                    "stages": {
                        "detection": {
                            "status": "ok",
                            "model": self.detector.model_name,
                            "detector_edge": self.detector.detector_edge,
                            "boxes": len(predictions),
                            "latency_ms": round(detector_ms, 3),
                            "model_bytes": self.detector.model_bytes,
                        },
                        "orientation": {"status": "ok", **orientation, "latency_ms": round(orientation_ms, 3)},
                        "recognition": {
                            "status": recognition_status,
                            "model": "korean_PP-OCRv5_mobile_rec",
                            "checkpoint_sha256": self.recognizer["checkpoint_sha256"],
                            "regions": len(regions),
                            "latency_ms": round(recognition_ms, 3),
                            "device": self.args.recognizer_device,
                        },
                    },
                    "regions": regions,
                    "text_lines": [region["text"] for region in regions],
                }
                _write_json_atomic(result_path, result)
                _write_json_atomic(
                    state_path,
                    {
                        "schema_version": 2,
                        "status": "completed",
                        "profile": profile,
                        "result_sha256": _sha256_file(result_path),
                    },
                )
                return result
            except Exception as exc:
                _write_json_atomic(
                    state_path,
                    {"schema_version": 2, "status": "failed", "profile": profile, "error": str(exc)},
                )
                raise


__all__ = ["FullDocumentRuntime"]
