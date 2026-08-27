from __future__ import annotations

import hashlib
import json
from pathlib import Path

import paddle

from browser_ocr.document_parsing.parser_v5_calibration import build_parser_v5_calibration
from browser_ocr.document_parsing.parser_v5_dataset import build_parser_v5_dataset, load_parser_v5_dataset
from browser_ocr.document_parsing.parser_v5_development_views import build_parser_v5_development_views
from browser_ocr.document_parsing.parser_v5_observation import ObservationProfile
from browser_ocr.document_parsing.parser_v5_training_paddle import ParserV5Model, ParserV5TrainingConfig
from browser_ocr.document_parsing.parser_v5_validation_protocol import freeze_parser_v5_candidate
from browser_ocr.document_parsing.parser_v5_world import ParserWorldProfile


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def build_frozen_candidate(root: Path) -> tuple[Path, Path]:
    train_manifest = build_parser_v5_dataset(
        root / "train",
        dataset_id="v5-train-sealed-eval",
        document_count=1,
        seed=4401,
        world_profile=ParserWorldProfile(medication_count=(1, 1), distractor_section_count=(1, 1)),
        observation_profile=ObservationProfile(
            text_corruption_rate=0,
            drop_rate=0,
            duplicate_rate=0,
            split_rate=0,
            merge_rate=0,
            geometry_jitter=0,
            false_positive_count=(0, 0),
            reading_order_shuffle_rate=0,
        ),
    )
    train = load_parser_v5_dataset(train_manifest)
    runtime_records = []
    for sample in train.samples:
        truth = sample["truth"]
        runtime_records.append({
            "document_id": truth["document_id"],
            "source_split": "train",
            "producer_fingerprint": "a" * 64,
            "nodes": [{
                "index": index,
                "text": span["text"],
                "detector_confidence": 0.95,
                "recognizer_confidence": 0.94,
                "polygon": span["polygon"],
            } for index, span in enumerate(truth["spans"], start=1)],
        })
    calibration = build_parser_v5_calibration(
        dataset_manifest=train_manifest,
        runtime_records=runtime_records,
        output_path=root / "calibration.json",
    )
    dev = build_parser_v5_development_views(root / "dev", documents_per_view=1, seed=4402)
    dev_ids = {
        load_parser_v5_dataset(path).dataset_id: load_parser_v5_dataset(path).samples_sha256
        for path in dev.values()
    }
    config = ParserV5TrainingConfig(
        epochs=1,
        hidden_dim=32,
        text_embedding_dim=8,
        text_conv_dim=8,
        layers=1,
        heads=1,
        assignment_hidden_dim=16,
        role_embedding_dim=4,
        device="cpu",
    )
    with paddle.utils.unique_name.guard():
        model = ParserV5Model(config)
    checkpoint = root / "model.pdparams"
    paddle.save(model.state_dict(), str(checkpoint))
    profile = {
        "schema_version": 1,
        "model_id": "parser_v5_global_structured_v1",
        "train_datasets": [{"dataset_id": train.dataset_id, "samples_sha256": train.samples_sha256}],
        "validation_datasets": [
            {"dataset_id": dataset_id, "samples_sha256": samples_sha256}
            for dataset_id, samples_sha256 in sorted(dev_ids.items())
        ],
        "config": config.__dict__,
    }
    canonical = json.dumps(profile, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    result = {
        "schema_version": 1,
        "status": "ok",
        "profile": profile,
        "profile_sha256": hashlib.sha256(canonical).hexdigest(),
        "history": [],
        "best_epoch": 1,
        "best_validation": {"views": {dataset_id: {} for dataset_id in dev_ids}},
        "best_checkpoint": checkpoint.name,
        "best_checkpoint_sha256": _sha(checkpoint),
    }
    result_path = root / "result.json"
    result_path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    freeze = freeze_parser_v5_candidate(
        training_result=result_path,
        development_manifests=list(dev.values()),
        calibration_artifact=calibration,
        output_path=root / "freeze.json",
    )
    return result_path, freeze


__all__ = ["build_frozen_candidate"]