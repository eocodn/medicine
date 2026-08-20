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

    archive_path = root / model["archive"]
    extracted = root / model["archive_root"]
    onnx_path = extracted / model["onnx_file"]
    config_path = extracted / model["config_file"]
    for label, path in (("archive", archive_path), ("ONNX", onnx_path), ("config", config_path)):
        if not path.is_file():
            raise ValueError(f"detector {label} asset is missing: {path}")

    archive_sha256 = _sha256_file(archive_path)
    if archive_sha256 != model["sha256"]:
        raise ValueError(f"detector archive SHA-256 mismatch: {archive_path}")
    validate_official_config(config_path, model_name, model)
    onnx_sha256, config_sha256 = _verify_extracted_assets(
        archive_path, str(model["archive_root"]), onnx_path, config_path
    )

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
