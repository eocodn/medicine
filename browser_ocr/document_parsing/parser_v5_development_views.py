from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .parser_v5_dataset import build_parser_v5_dataset
from .parser_v5_observation import ObservationProfile
from .parser_v5_world import ParserWorldProfile


@dataclass(frozen=True)
class ParserV5DevelopmentView:
    name: str
    world_profile: ParserWorldProfile
    observation_profile: ObservationProfile


DEVELOPMENT_VIEWS = (
    ParserV5DevelopmentView("baseline", ParserWorldProfile(), ObservationProfile()),
    ParserV5DevelopmentView(
        "zero_medication",
        ParserWorldProfile(medication_count=(0, 0), distractor_section_count=(4, 8)),
        ObservationProfile(false_positive_count=(1, 5)),
    ),
    ParserV5DevelopmentView(
        "many_medication",
        ParserWorldProfile(medication_count=(5, 7), distractor_section_count=(2, 5)),
        ObservationProfile(),
    ),
    ParserV5DevelopmentView(
        "high_distractor",
        ParserWorldProfile(medication_count=(0, 4), distractor_section_count=(8, 12)),
        ObservationProfile(false_positive_count=(2, 7)),
    ),
    ParserV5DevelopmentView(
        "counterfactual_context",
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(2, 6),
            counterfactual_context_rate=1.0,
        ),
        ObservationProfile(),
    ),
    ParserV5DevelopmentView(
        "geometry_scramble",
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(2, 6),
            geometry_scramble_rate=1.0,
        ),
        ObservationProfile(),
    ),
    ParserV5DevelopmentView(
        "ocr_corruption",
        ParserWorldProfile(),
        ObservationProfile(
            text_corruption_rate=0.35,
            drop_rate=0.14,
            duplicate_rate=0.10,
            split_rate=0.16,
            merge_rate=0.16,
            geometry_jitter=0.018,
            false_positive_count=(2, 8),
            reading_order_shuffle_rate=0.20,
        ),
    ),
    ParserV5DevelopmentView(
        "merged_regions",
        ParserWorldProfile(medication_count=(1, 5), distractor_section_count=(1, 5)),
        ObservationProfile(
            text_corruption_rate=0.08,
            drop_rate=0.02,
            duplicate_rate=0.01,
            split_rate=0.01,
            merge_rate=0.75,
            geometry_jitter=0.004,
            false_positive_count=(0, 3),
            reading_order_shuffle_rate=0.04,
        ),
    ),
    ParserV5DevelopmentView(
        "unseen_product_names",
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(1, 5),
            product_vocabulary="unseen",
            wording_vocabulary="train",
        ),
        ObservationProfile(),
    ),
    ParserV5DevelopmentView(
        "unseen_wording",
        ParserWorldProfile(
            medication_count=(1, 5),
            distractor_section_count=(2, 6),
            product_vocabulary="train",
            wording_vocabulary="unseen",
        ),
        ObservationProfile(),
    ),
)


def build_parser_v5_development_views(
    output_root: str | Path,
    *,
    documents_per_view: int,
    seed: int,
) -> dict[str, Path]:
    if isinstance(documents_per_view, bool) or not isinstance(documents_per_view, int) or documents_per_view <= 0:
        raise ValueError("Parser v5 documents_per_view must be a positive integer")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("Parser v5 development seed must be an integer")
    root = Path(output_root).resolve()
    manifests: dict[str, Path] = {}
    for index, view in enumerate(DEVELOPMENT_VIEWS):
        manifests[view.name] = build_parser_v5_dataset(
            root / view.name,
            dataset_id=f"dev-{view.name.replace('_', '-')}",
            document_count=documents_per_view,
            seed=seed + index * 10_007,
            world_profile=view.world_profile,
            observation_profile=view.observation_profile,
        )
    return manifests


__all__ = [
    "DEVELOPMENT_VIEWS",
    "ParserV5DevelopmentView",
    "build_parser_v5_development_views",
]