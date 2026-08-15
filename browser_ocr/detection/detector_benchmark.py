#!/usr/bin/env python3
"""Zero-shot DB text-detector benchmark over the medicine full-document corpus."""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import onnxruntime as ort
import psutil
import pyclipper
import yaml


def resize_dimensions(width: int, height: int, longest_edge: int) -> tuple[int, int]:
    if width <= 0 or height <= 0 or longest_edge <= 0:
        raise ValueError("image dimensions and longest_edge must be positive")
    scale = longest_edge / max(width, height)
    resized_width = max(32, int(round(width * scale / 32.0)) * 32)
    resized_height = max(32, int(round(height * scale / 32.0)) * 32)
    return resized_width, resized_height


def _find_transform(config: dict[str, Any], name: str) -> Any:
    for operation in config.get("PreProcess", {}).get("transform_ops", []):
        if isinstance(operation, dict) and name in operation:
            return operation[name]
    raise ValueError(f"official inference config missing {name}")


def _close_sequence(left: list[float], right: list[float], tolerance: float = 1e-8) -> bool:
    return len(left) == len(right) and all(abs(float(a) - float(b)) <= tolerance for a, b in zip(left, right))


def validate_official_config(config_path: Path, model_name: str, pinned: dict[str, Any]) -> dict[str, Any]:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    if config.get("Global", {}).get("model_name") != model_name:
        raise ValueError(f"official model_name mismatch for {model_name}")
    decode = _find_transform(config, "DecodeImage")
    normalize = _find_transform(config, "NormalizeImage")
    if decode.get("img_mode") != pinned["preprocess"]["color_mode"]:
        raise ValueError(f"{model_name} color_mode differs from pinned manifest")
    for key in ("mean", "std"):
        if not _close_sequence(normalize.get(key, []), pinned["preprocess"][key]):
            raise ValueError(f"{model_name} preprocess {key} differs from pinned manifest")

    official = config.get("PostProcess", {})
    if official.get("name") != "DBPostProcess":
        raise ValueError(f"{model_name} postprocess is not DBPostProcess")
    mapping = {
        "threshold": "thresh",
        "box_threshold": "box_thresh",
        "max_candidates": "max_candidates",
        "unclip_ratio": "unclip_ratio",
    }
    for pinned_key, official_key in mapping.items():
        left = pinned["postprocess"][pinned_key]
        right = official.get(official_key)
        if isinstance(left, int):
            equal = left == right
        else:
            equal = right is not None and abs(float(left) - float(right)) <= 1e-8
        if not equal:
            raise ValueError(f"{model_name} postprocess {pinned_key} differs from pinned manifest")
    return config


def _order_box_points(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float32).reshape(4, 2)
    x_order = points[np.argsort(points[:, 0])]
    left = x_order[:2]
    right = x_order[2:]
    left = left[np.argsort(left[:, 1])]
    right = right[np.argsort(right[:, 1])]
    return np.array([left[0], right[0], right[1], left[1]], dtype=np.float32)


def _mini_box(contour: np.ndarray) -> tuple[np.ndarray, float]:
    rect = cv2.minAreaRect(contour.astype(np.float32))
    box = _order_box_points(cv2.boxPoints(rect))
    return box, min(rect[1])


def _box_score(probability: np.ndarray, box: np.ndarray) -> float:
    height, width = probability.shape
    xmin = max(0, min(width - 1, int(math.floor(np.min(box[:, 0])))))
    xmax = max(0, min(width - 1, int(math.ceil(np.max(box[:, 0])))))
    ymin = max(0, min(height - 1, int(math.floor(np.min(box[:, 1])))))
    ymax = max(0, min(height - 1, int(math.ceil(np.max(box[:, 1])))))
    if xmax < xmin or ymax < ymin:
        return 0.0
    local = box.copy()
    local[:, 0] -= xmin
    local[:, 1] -= ymin
    mask = np.zeros((ymax - ymin + 1, xmax - xmin + 1), dtype=np.uint8)
    cv2.fillPoly(mask, [np.rint(local).astype(np.int32)], 1)
    return float(cv2.mean(probability[ymin : ymax + 1, xmin : xmax + 1], mask=mask)[0])


def _unclip(box: np.ndarray, ratio: float) -> np.ndarray | None:
    contour = np.asarray(box, dtype=np.float32)
    area = abs(float(cv2.contourArea(contour)))
    perimeter = float(cv2.arcLength(contour, True))
    if area <= 0 or perimeter <= 0:
        return None
    distance = area * ratio / perimeter
    offset = pyclipper.PyclipperOffset()
    scale = 1024.0
    path = [(int(round(x * scale)), int(round(y * scale))) for x, y in contour]
    offset.AddPath(path, pyclipper.JT_ROUND, pyclipper.ET_CLOSEDPOLYGON)
    expanded = offset.Execute(distance * scale)
    if not expanded:
        return None
    largest = max(expanded, key=lambda item: abs(pyclipper.Area(item)))
    return np.asarray(largest, dtype=np.float32).reshape(-1, 2) / scale


def db_postprocess(
    probability: np.ndarray,
    *,
    source_width: int,
    source_height: int,
    threshold: float,
    box_threshold: float,
    max_candidates: int,
    unclip_ratio: float,
) -> list[dict[str, Any]]:
    if probability.ndim != 2:
        raise ValueError("DB probability map must be two-dimensional")
    bitmap = (probability > threshold).astype(np.uint8) * 255
    contours, _ = cv2.findContours(bitmap, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    map_height, map_width = probability.shape
    predictions: list[dict[str, Any]] = []
    for contour in contours[:max_candidates]:
        if len(contour) < 3:
            continue
        box, short_side = _mini_box(contour)
        if short_side < 3:
            continue
        score = _box_score(probability, box)
        if score < box_threshold:
            continue
        expanded = _unclip(box, unclip_ratio)
        if expanded is None or len(expanded) < 3:
            continue
        box, short_side = _mini_box(expanded)
        if short_side < 5:
            continue
        polygon = []
        for x, y in box:
            source_x = max(0.0, min(float(source_width), float(x) * source_width / map_width))
            source_y = max(0.0, min(float(source_height), float(y) * source_height / map_height))
            polygon.append([round(source_x, 3), round(source_y, 3)])
        predictions.append({"polygon": polygon, "score": score})
    predictions.sort(key=lambda item: (item["polygon"][0][1], item["polygon"][0][0]))
    return predictions


def _prepare_input(image: np.ndarray, edge: int, preprocess: dict[str, Any]) -> np.ndarray:
    height, width = image.shape[:2]
    resized_width, resized_height = resize_dimensions(width, height, edge)
    resized = cv2.resize(image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR)
    normalized = resized.astype(np.float32) / 255.0
    mean = np.asarray(preprocess["mean"], dtype=np.float32)
    std = np.asarray(preprocess["std"], dtype=np.float32)
    normalized = (normalized - mean) / std
    return np.transpose(normalized, (2, 0, 1))[None, :, :, :].astype(np.float32, copy=False)


def _probability_map(output: np.ndarray) -> np.ndarray:
    array = np.asarray(output)
    while array.ndim > 2:
        if array.shape[0] != 1:
            raise ValueError(f"unexpected detector output shape {array.shape}")
        array = array[0]
    if array.ndim != 2:
        raise ValueError(f"unexpected detector output shape {array.shape}")
    return array.astype(np.float32, copy=False)


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    index = (len(ordered) - 1) * percentile
    lower = math.floor(index)
    upper = math.ceil(index)
    if lower == upper:
        return ordered[lower]
    fraction = index - lower
    return ordered[lower] * (1 - fraction) + ordered[upper] * fraction


def _latency_summary(values: list[float]) -> dict[str, float]:
    return {
        "mean": round(statistics.fmean(values), 3) if values else 0.0,
        "p50": round(_percentile(values, 0.50), 3),
        "p95": round(_percentile(values, 0.95), 3),
        "max": round(max(values), 3) if values else 0.0,
    }


def run_benchmark(
    *,
    corpus_path: Path,
    model_manifest_path: Path,
    model_root: Path,
    model_name: str,
    detector_edge: int,
    threads: int = 1,
) -> dict[str, Any]:
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    manifest = json.loads(model_manifest_path.read_text(encoding="utf-8"))
    model = manifest.get("models", {}).get(model_name)
    if model is None:
        raise ValueError(f"unknown detector model {model_name}")
    extracted = model_root / model["archive_root"]
    onnx_path = extracted / model["onnx_file"]
    config_path = extracted / model["config_file"]
    if not onnx_path.is_file() or not config_path.is_file():
        raise ValueError(f"detector assets are incomplete for {model_name}")
    validate_official_config(config_path, model_name, model)

    cv2.setNumThreads(1)
    options = ort.SessionOptions()
    options.intra_op_num_threads = threads
    options.inter_op_num_threads = 1
    options.execution_mode = ort.ExecutionMode.ORT_SEQUENTIAL
    options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
    process = psutil.Process()
    base_rss = process.memory_info().rss
    session_start = time.perf_counter()
    session = ort.InferenceSession(str(onnx_path), sess_options=options, providers=["CPUExecutionProvider"])
    session_create_ms = (time.perf_counter() - session_start) * 1000.0
    peak_rss = max(base_rss, process.memory_info().rss)
    input_name = session.get_inputs()[0].name
    output_name = session.get_outputs()[0].name
    corpus_root = corpus_path.parent

    if not corpus.get("samples"):
        raise ValueError("corpus contains no samples")
    warmup_sample = corpus["samples"][0]
    warmup_image = cv2.imread(str(corpus_root / warmup_sample["image"]), cv2.IMREAD_COLOR)
    if warmup_image is None:
        raise ValueError(f"failed to read {warmup_sample['image']}")
    warmup_input = _prepare_input(warmup_image, detector_edge, model["preprocess"])
    warmup_start = time.perf_counter()
    session.run([output_name], {input_name: warmup_input})
    warmup_ms = (time.perf_counter() - warmup_start) * 1000.0
    peak_rss = max(peak_rss, process.memory_info().rss)

    total_latencies: list[float] = []
    inference_latencies: list[float] = []
    prediction_samples: list[dict[str, Any]] = []
    for index, sample in enumerate(corpus["samples"], start=1):
        started = time.perf_counter()
        image = cv2.imread(str(corpus_root / sample["image"]), cv2.IMREAD_COLOR)
        if image is None:
            raise ValueError(f"failed to read {sample['image']}")
        source_height, source_width = image.shape[:2]
        tensor = _prepare_input(image, detector_edge, model["preprocess"])
        inference_started = time.perf_counter()
        output = session.run([output_name], {input_name: tensor})[0]
        inference_latencies.append((time.perf_counter() - inference_started) * 1000.0)
        probability = _probability_map(output)
        predictions = db_postprocess(
            probability,
            source_width=source_width,
            source_height=source_height,
            threshold=model["postprocess"]["threshold"],
            box_threshold=model["postprocess"]["box_threshold"],
            max_candidates=model["postprocess"]["max_candidates"],
            unclip_ratio=model["postprocess"]["unclip_ratio"],
        )
        total_latencies.append((time.perf_counter() - started) * 1000.0)
        peak_rss = max(peak_rss, process.memory_info().rss)
        prediction_samples.append({"id": sample["id"], "predictions": predictions})
        if index == 1 or index == len(corpus["samples"]) or index % max(1, len(corpus["samples"]) // 10) == 0:
            print(
                f"[det-bench] {model_name}@{detector_edge} {index}/{len(corpus['samples'])}",
                file=sys.stderr,
                flush=True,
            )

    return {
        "schema_version": 1,
        "corpus_id": corpus["corpus_id"],
        "model": model_name,
        "detector_edge": detector_edge,
        "asset_sha256": model["sha256"],
        "model_bytes": onnx_path.stat().st_size,
        "postprocess": model["postprocess"],
        "runtime": {
            "engine": "onnxruntime-cpu",
            "onnxruntime": ort.__version__,
            "threads": threads,
            "providers": session.get_providers(),
            "timing_scope": "development_cpu_proxy_not_android_release_gate",
        },
        "performance": {
            "samples": len(prediction_samples),
            "session_create_ms": round(session_create_ms, 3),
            "warmup_inference_ms": round(warmup_ms, 3),
            "latency_ms": _latency_summary(total_latencies),
            "inference_ms": _latency_summary(inference_latencies),
            "base_rss_bytes": base_rss,
            "peak_rss_bytes": peak_rss,
            "incremental_peak_rss_bytes": max(0, peak_rss - base_rss),
        },
        "predictions": {
            "schema_version": 1,
            "corpus_id": corpus["corpus_id"],
            "samples": prediction_samples,
        },
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", required=True, type=Path)
    parser.add_argument("--model-manifest", required=True, type=Path)
    parser.add_argument("--model-root", required=True, type=Path)
    parser.add_argument("--model", required=True)
    parser.add_argument("--edge", required=True, type=int)
    parser.add_argument("--threads", type=int, default=1)
    parser.add_argument("--output", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    result = run_benchmark(
        corpus_path=args.corpus.resolve(),
        model_manifest_path=args.model_manifest.resolve(),
        model_root=args.model_root.resolve(),
        model_name=args.model,
        detector_edge=args.edge,
        threads=args.threads,
    )
    encoded = json.dumps(result, ensure_ascii=False, separators=(",", ":")) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        temporary = args.output.with_suffix(args.output.suffix + ".partial")
        temporary.write_text(encoded, encoding="utf-8")
        temporary.replace(args.output)
    else:
        print(encoded, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())