from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .parser_v5_dataset import build_parser_v5_dataset
from .parser_v5_observation import ObservationProfile
from .parser_v5_world import ParserWorldProfile


@dataclass(frozen=True)
class ParserV5TrainingView:
    name: str
    sample_multiplier: int
    world_profile: ParserWorldProfile
    observation_profile: ObservationProfile

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("Parser v5 training view name is required")
        if isinstance(self.sample_multiplier, bool) or not isinstance(self.sample_multiplier, int) or self.sample_multiplier <= 0:
            raise ValueError("Parser v5 training view sample_multiplier must be positive")
        if self.world_profile.product_vocabulary != "train" or self.world_profile.wording_vocabulary != "train":
            raise ValueError("Parser v5 training views must use train-only vocabulary partitions")


TRAINING_VIEWS = (
    ParserV5TrainingView("baseline", 4, ParserWorldProfile(), ObservationProfile()),
    ParserV5TrainingView(
        "zero_medication",
        1,
        ParserWorldProfile(medication_count=(0, 0), distractor_section_count=(4, 8)),
        ObservationProfile(false_positive_count=(1, 5)),
    ),
    ParserV5TrainingView(
        "many_medication",
        1,
        ParserWorldProfile(medication_count=(5, 7), distractor_section_count=(2, 5)),
        ObservationProfile(),
    ),
    ParserV5TrainingView(
        "high_distractor",
        1,
        ParserWorldProfile(medication_count=(0, 4), distractor_section_count=(8, 12)),
        ObservationProfile(false_positive_count=(2, 7)),
    ),
    ParserV5TrainingView(
        "counterfactual_context",
        2,
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(2, 6),
            counterfactual_context_rate=1.0,
        ),
        ObservationProfile(),
    ),
    ParserV5TrainingView(
        "geometry_scramble",
        1,
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(2, 6),
            geometry_scramble_rate=1.0,
        ),
        ObservationProfile(),
    ),
    ParserV5TrainingView(
        "ocr_corruption",
        2,
        ParserWorldProfile(),
        ObservationProfile(
            text_corruption_rate=0.30,
            drop_rate=0.12,
            duplicate_rate=0.08,
            split_rate=0.14,
            merge_rate=0.14,
            geometry_jitter=0.015,
            false_positive_count=(2, 7),
            reading_order_shuffle_rate=0.16,
        ),
    ),
    ParserV5TrainingView(
        "merged_regions",
        2,
        ParserWorldProfile(medication_count=(1, 5), distractor_section_count=(1, 5)),
        ObservationProfile(
            text_corruption_rate=0.08,
            drop_rate=0.02,
            duplicate_rate=0.01,
            split_rate=0.02,
            merge_rate=0.60,
            geometry_jitter=0.004,
            false_positive_count=(0, 3),
            reading_order_shuffle_rate=0.04,
        ),
    ),
)


def build_parser_v5_training_views(
    output_root: str | Path,
    *,
    documents_per_unit: int,
    seed: int,
) -> dict[str, Path]:
    if isinstance(documents_per_unit, bool) or not isinstance(documents_per_unit, int) or documents_per_unit <= 0:
        raise ValueError("Parser v5 documents_per_unit must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Parser v5 training seed must be an integer")
    root = Path(output_root).resolve()
    manifests: dict[str, Path] = {}
    for index, view in enumerate(TRAINING_VIEWS):
        manifests[view.name] = build_parser_v5_dataset(
            root / view.name,
            dataset_id=f"train-{view.name.replace('_', '-')}",
            document_count=documents_per_unit * view.sample_multiplier,
            seed=seed + index * 20_011,
            world_profile=view.world_profile,
            observation_profile=view.observation_profile,
        )
    return manifests


__all__ = [
    "TRAINING_VIEWS",
    "ParserV5TrainingView",
    "build_parser_v5_training_views",
]