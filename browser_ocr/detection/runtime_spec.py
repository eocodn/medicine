from __future__ import annotations

from pathlib import Path
from typing import Mapping

import yaml


class DetectorRuntimeSpecError(ValueError):
    pass


def _transform(config: Mapping[str, object], name: str) -> Mapping[str, object]:
    preprocess = config.get("PreProcess")
    if not isinstance(preprocess, Mapping):
        raise DetectorRuntimeSpecError("detector inference config is missing PreProcess")
    transforms = preprocess.get("transform_ops")
    if not isinstance(transforms, list):
        raise DetectorRuntimeSpecError("detector inference config transform_ops is invalid")
    for item in transforms:
        if isinstance(item, Mapping) and name in item:
            value = item[name]
            if value is None:
                return {}
            if isinstance(value, Mapping):
                return value
            raise DetectorRuntimeSpecError(f"detector inference transform {name} must be an object")
    raise DetectorRuntimeSpecError(f"detector inference config is missing transform {name}")


def load_detector_runtime_spec(path: str | Path) -> dict[str, object]:
    path = Path(path)
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DetectorRuntimeSpecError(f"could not read detector inference config {path}: {exc}") from exc
    if not isinstance(config, Mapping):
        raise DetectorRuntimeSpecError("detector inference config must be a YAML object")
    global_config = config.get("Global")
    if not isinstance(global_config, Mapping):
        raise DetectorRuntimeSpecError("detector inference config is missing Global")
    model_name = global_config.get("model_name")
    if not isinstance(model_name, str) or not model_name:
        raise DetectorRuntimeSpecError("detector inference config model_name is missing")

    decode = _transform(config, "DecodeImage")
    normalize = _transform(config, "NormalizeImage")
    color_mode = decode.get("img_mode")
    mean = normalize.get("mean")
    std = normalize.get("std")
    if color_mode not in {"BGR", "RGB"}:
        raise DetectorRuntimeSpecError("detector inference DecodeImage img_mode is unsupported")
    if not isinstance(mean, list) or len(mean) != 3 or not all(isinstance(value, (int, float)) for value in mean):
        raise DetectorRuntimeSpecError("detector inference normalization mean is invalid")
    if not isinstance(std, list) or len(std) != 3 or not all(isinstance(value, (int, float)) for value in std):
        raise DetectorRuntimeSpecError("detector inference normalization std is invalid")

    post = config.get("PostProcess")
    if not isinstance(post, Mapping) or post.get("name") != "DBPostProcess":
        raise DetectorRuntimeSpecError("detector inference postprocess must be DBPostProcess")
    required = {
        "threshold": post.get("thresh"),
        "box_threshold": post.get("box_thresh"),
        "max_candidates": post.get("max_candidates"),
        "unclip_ratio": post.get("unclip_ratio"),
    }
    if not isinstance(required["max_candidates"], int) or required["max_candidates"] <= 0:
        raise DetectorRuntimeSpecError("detector inference max_candidates is invalid")
    for key in ("threshold", "box_threshold", "unclip_ratio"):
        value = required[key]
        if not isinstance(value, (int, float)):
            raise DetectorRuntimeSpecError(f"detector inference {key} is invalid")
    return {
        "model_name": model_name,
        "preprocess": {
            "color_mode": color_mode,
            "mean": [float(value) for value in mean],
            "std": [float(value) for value in std],
        },
        "postprocess": {
            "threshold": float(required["threshold"]),
            "box_threshold": float(required["box_threshold"]),
            "max_candidates": required["max_candidates"],
            "unclip_ratio": float(required["unclip_ratio"]),
        },
    }


__all__ = ["DetectorRuntimeSpecError", "load_detector_runtime_spec"]