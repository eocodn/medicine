from __future__ import annotations

import copy
import sys
from pathlib import Path
from typing import Iterable

import numpy as np

from .dataset import DatasetError


def _resolve_character_dictionary(global_config: dict[str, object], paddleocr_root: Path) -> Path:
    raw = global_config.get("character_dict_path")
    if not isinstance(raw, str) or not raw.strip():
        path = paddleocr_root / "ppocr" / "utils" / "dict" / "ppocrv5_korean_dict.txt"
    else:
        path = Path(raw)
        if not path.is_absolute():
            path = paddleocr_root / path
    path = path.resolve()
    if not path.is_file():
        raise DatasetError(f"selected recognizer dictionary is missing: {path}")
    global_config["character_dict_path"] = str(path)
    return path


class PersistentRecognizer:
    """Keep the selected PaddleOCR recognizer loaded for repeated inference.

    This intentionally mirrors the selected model path used by tools/infer_rec.py,
    but moves model construction and checkpoint loading out of the per-document
    subprocess boundary. The current OCR pipeline is pinned to the PP-OCRv5
    Korean SVTR_LCNet/MultiHead recognizer; unsupported architectures fail
    explicitly rather than silently taking a different inference path.
    """

    def __init__(
        self,
        *,
        paddleocr_root: Path,
        config_path: Path,
        checkpoint: Path,
        use_gpu: bool,
    ) -> None:
        root = paddleocr_root.resolve()
        config_path = config_path.resolve()
        checkpoint = checkpoint.resolve()
        if not (root / "tools" / "program.py").is_file() or not (root / "ppocr").is_dir():
            raise DatasetError(f"PaddleOCR inference source is incomplete: {root}")
        if not config_path.is_file() or not checkpoint.is_file():
            raise DatasetError("persistent recognizer config/checkpoint is missing")

        root_text = str(root)
        if root_text not in sys.path:
            sys.path.insert(0, root_text)

        try:
            import paddle
            from ppocr.data import create_operators, transform
            from ppocr.modeling.architectures import build_model
            from ppocr.postprocess import build_post_process
            from ppocr.utils.save_load import load_model
            from tools import program
        except ImportError as exc:
            raise DatasetError(f"failed to import PaddleOCR recognizer runtime: {exc}") from exc

        config = copy.deepcopy(program.load_config(str(config_path)))
        architecture = config.get("Architecture")
        postprocess = config.get("PostProcess")
        if not isinstance(architecture, dict) or architecture.get("algorithm") != "SVTR_LCNet":
            raise DatasetError("persistent recognizer requires the selected SVTR_LCNet architecture")
        head = architecture.get("Head")
        if not isinstance(head, dict) or head.get("name") != "MultiHead":
            raise DatasetError("persistent recognizer requires the selected MultiHead recognizer")
        if not isinstance(postprocess, dict) or postprocess.get("name") != "CTCLabelDecode":
            raise DatasetError("persistent recognizer requires CTCLabelDecode post-processing")

        global_config = config.get("Global")
        if not isinstance(global_config, dict):
            raise DatasetError("selected recognizer config is missing Global settings")
        global_config["use_gpu"] = use_gpu
        global_config["distributed"] = False
        global_config["checkpoints"] = str(checkpoint)
        global_config["infer_mode"] = True
        _resolve_character_dictionary(global_config, root)

        program.check_device(use_gpu)
        paddle.set_device("gpu:0" if use_gpu else "cpu")
        post_process = build_post_process(postprocess, global_config)
        if not hasattr(post_process, "character"):
            raise DatasetError("selected recognizer post-process is missing its character dictionary")
        char_num = len(post_process.character)
        head["out_channels_list"] = {
            "CTCLabelDecode": char_num,
            "SARLabelDecode": char_num + 2,
            "NRTRLabelDecode": char_num + 3,
        }
        model = build_model(architecture)
        load_model(config, model, model_type="rec")
        model.eval()

        transforms = []
        eval_config = config.get("Eval")
        dataset_config = eval_config.get("dataset") if isinstance(eval_config, dict) else None
        raw_transforms = dataset_config.get("transforms") if isinstance(dataset_config, dict) else None
        if not isinstance(raw_transforms, list):
            raise DatasetError("selected recognizer Eval transforms are missing")
        for raw in raw_transforms:
            if not isinstance(raw, dict) or len(raw) != 1:
                raise DatasetError("selected recognizer Eval transform is invalid")
            op = copy.deepcopy(raw)
            op_name = next(iter(op))
            if "Label" in op_name:
                continue
            if op_name == "RecResizeImg":
                settings = op[op_name]
                if not isinstance(settings, dict):
                    raise DatasetError("selected recognizer RecResizeImg settings are invalid")
                settings["infer_mode"] = True
            elif op_name == "KeepKeys":
                settings = op[op_name]
                if not isinstance(settings, dict):
                    raise DatasetError("selected recognizer KeepKeys settings are invalid")
                settings["keep_keys"] = ["image"]
            transforms.append(op)

        self._paddle = paddle
        self._transform = transform
        self._ops = create_operators(transforms, global_config)
        self._model = model
        self._post_process = post_process

    def recognize_paths(self, paths: Iterable[str | Path]) -> dict[str, dict[str, object]]:
        ordered = [str(Path(path).resolve()) for path in paths]
        recognized: dict[str, dict[str, object]] = {}
        # Keep inference one crop at a time. Same-shape batching is faster, but it
        # raises peak VRAM and changes floating-point scores slightly. The parser
        # materializer favors bounded memory and bit-for-bit parity with the
        # historical one-image PaddleOCR inference path.
        with self._paddle.no_grad():
            for path_text in ordered:
                path = Path(path_text)
                if not path.is_file():
                    raise DatasetError(f"recognition crop does not exist: {path}")
                batch = self._transform({"image": path.read_bytes()}, self._ops)
                if batch is None or not isinstance(batch, (list, tuple)) or not batch:
                    raise DatasetError(f"recognizer preprocessing failed: {path}")
                images = self._paddle.to_tensor(np.expand_dims(batch[0], axis=0))
                post_result = self._post_process(self._model(images))
                if not isinstance(post_result, list) or len(post_result) != 1 or len(post_result[0]) < 2:
                    raise DatasetError(f"recognizer produced an invalid result: {path}")
                text, score = post_result[0][0], post_result[0][1]
                recognized[path_text] = {"text": str(text), "score": float(score)}
        return recognized


__all__ = ["PersistentRecognizer"]
