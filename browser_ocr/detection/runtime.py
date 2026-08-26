from __future__ import annotations

import hashlib
import json
import tarfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort

from .detector_benchmark import _prepare_input, _probability_map, db_postprocess, validate_official_config


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _archive_member_sha256(archive_path: Path, member_relative: str) -> str:
    target = member_relative.lstrip("./")
    with tarfile.open(archive_path, "r:*") as archive:
        member = next((item for item in archive.getmembers() if item.name.lstrip("./") == target), None)
        if member is None or not member.isfile():
            raise ValueError(f"detector archive is missing expected member: {member_relative}")
        stream = archive.extractfile(member)
        if stream is None:
            raise ValueError(f"detector archive member is unreadable: {member_relative}")
        digest = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
        return digest.hexdigest()


def _verify_extracted_assets(archive_path: Path, archive_root: str, onnx_path: Path, config_path: Path) -> tuple[str, str]:
    onnx_sha256 = _sha256_file(onnx_path)
    config_sha256 = _sha256_file(config_path)
    root = archive_root.rstrip("/")
    expected_onnx_sha256 = _archive_member_sha256(archive_path, f"{root}/{onnx_path.name}")
    expected_config_sha256 = _archive_member_sha256(archive_path, f"{root}/{config_path.name}")
    if onnx_sha256 != expected_onnx_sha256 or config_sha256 != expected_config_sha256:
        raise ValueError("extracted detector assets differ from the pinned archive")
    return onnx_sha256, config_sha256


def _require_sha256(value: object, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError(f"detector {label} must be a lowercase SHA-256")
    return value


def _verify_runtime_assets(root: Path, model_name: str, model: dict[str, Any]) -> dict[str, object]:
    archive_root = model.get("archive_root")
    onnx_file = model.get("onnx_file")
    config_file = model.get("config_file")
    if not all(isinstance(value, str) and value for value in (archive_root, onnx_file, config_file)):
        raise ValueError(f"detector model {model_name} is missing extracted asset paths")
    extracted = root / str(archive_root)
    onnx_path = extracted / str(onnx_file)
    config_path = extracted / str(config_file)
    for label, path in (("ONNX", onnx_path), ("config", config_path)):
        if not path.is_file():
            raise ValueError(f"detector {label} asset is missing: {path}")

    archive_value = model.get("archive")
    if archive_value is not None:
        if not isinstance(archive_value, str) or not archive_value:
            raise ValueError(f"detector model {model_name} archive must be a non-empty string")
        archive_path = root / archive_value
        if not archive_path.is_file():
            raise ValueError(f"detector archive asset is missing: {archive_path}")
        expected_archive_sha = _require_sha256(model.get("sha256"), "archive sha256")
        archive_sha = _sha256_file(archive_path)
        if archive_sha != expected_archive_sha:
            raise ValueError(f"detector archive SHA-256 mismatch: {archive_path}")
        onnx_sha, config_sha = _verify_extracted_assets(archive_path, str(archive_root), onnx_path, config_path)
        return {
            "asset_sha256": archive_sha,
            "onnx_sha256": onnx_sha,
            "config_sha256": config_sha,
            "onnx_path": onnx_path,
            "config_path": config_path,
        }

    expected_onnx_sha = _require_sha256(model.get("onnx_sha256"), "onnx_sha256")
    expected_config_sha = _require_sha256(model.get("config_sha256"), "config_sha256")
    asset_sha = _require_sha256(model.get("sha256"), "asset sha256")
    if asset_sha != expected_onnx_sha:
        raise ValueError("trained detector candidate asset SHA-256 must equal its ONNX SHA-256")
    onnx_sha = _sha256_file(onnx_path)
    if onnx_sha != expected_onnx_sha:
        raise ValueError(f"detector ONNX SHA-256 mismatch: {onnx_path}")
    config_sha = _sha256_file(config_path)
    if config_sha != expected_config_sha:
        raise ValueError(f"detector config SHA-256 mismatch: {config_path}")
    return {
        "asset_sha256": asset_sha,
        "onnx_sha256": onnx_sha,
        "config_sha256": config_sha,
        "onnx_path": onnx_path,
        "config_path": config_path,
    }


@dataclass(frozen=True)
class DetectorRuntime:
    model_name: str
    detector_edge: int
    archive_sha256: str
    onnx_sha256: str
    config_sha256: str
    model_bytes: int
    postprocess: dict[str, Any]
    preprocess: dict[str, Any]
    session: ort.InferenceSession
    input_name: str
    output_name: str

    def predict(self, image: np.ndarray) -> list[dict[str, Any]]:
        if image is None or image.ndim != 3 or image.shape[2] != 3:
            raise ValueError("detector input must be a BGR image")
        source_height, source_width = image.shape[:2]
        tensor = _prepare_input(image, self.detector_edge, self.preprocess)
        output = self.session.run([self.output_name], {self.input_name: tensor})[0]
        probability = _probability_map(output)
        return db_postprocess(
            probability,
            source_width=source_width,
            source_height=source_height,
            threshold=self.postprocess["threshold"],
            box_threshold=self.postprocess["box_threshold"],
            max_candidates=self.postprocess["max_candidates"],
            unclip_ratio=self.postprocess["unclip_ratio"],
        )


def load_detector_runtime(
    *,
    model_manifest_path: str | Path,
    model_root: str | Path,
    model_name: str,
    detector_edge: int,
    threads: int = 1,
) -> DetectorRuntime:
    if detector_edge <= 0:
        raise ValueError("detector edge must be positive")
    if threads <= 0:
        raise ValueError("detector threads must be positive")

    manifest_path = Path(model_manifest_path).resolve()
    root = Path(model_root).resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    model = manifest.get("models", {}).get(model_name)
    if not isinstance(model, dict):
        raise ValueError(f"unknown detector model {model_name}")

    verified = _verify_runtime_assets(root, model_name, model)
    onnx_path = Path(verified["onnx_path"])
    config_path = Path(verified["config_path"])
    validate_official_config(config_path, model_name, model)
    archive_sha256 = str(verified["asset_sha256"])
    onnx_sha256 = str(verified["onnx_sha256"])
    config_sha256 = str(verified["config_sha256"])

    cv2.setNumThreads(1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    inputs = session.get_inputs()
    outputs = session.get_outputs()
    if len(inputs) != 1 or len(outputs) != 1:
        raise ValueError(f"unexpected detector IO contract for {model_name}")

    return DetectorRuntime(
        model_name=model_name,
        detector_edge=detector_edge,
        archive_sha256=archive_sha256,
        onnx_sha256=onnx_sha256,
        config_sha256=config_sha256,
        model_bytes=onnx_path.stat().st_size,
        postprocess=dict(model["postprocess"]),
        preprocess=dict(model["preprocess"]),
        session=session,
        input_name=inputs[0].name,
        output_name=outputs[0].name,
    )
