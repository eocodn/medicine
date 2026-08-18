from __future__ import annotations

import csv
import json
from dataclasses import dataclass
from importlib.resources import files


_RESOURCE = "data/mfds_remark_registry.tsv"
_SUPPORTED_CATEGORIES = (
    "combination_contraindication",
    "age_contraindication",
    "pregnancy_contraindication",
    "dose_caution",
    "duration_caution",
    "elderly_caution",
    "therapeutic_duplication_caution",
)
_ALLOWED_MODES = {
    "informational", "review_required", "interaction_window", "form_exclusion", "composition_scope"
}


@dataclass(frozen=True)
class ReviewedMfdsRemark:
    category: str
    remark: str
    mode: str
    qualifier_type: str
    display_text: str
    value: str | None
    rationale: str

    @property
    def requires_review(self) -> bool:
        return self.mode == "review_required"

    def payload(self) -> dict[str, object]:
        result: dict[str, object] = {
            "type": self.qualifier_type,
            "text": self.display_text,
            "source_remark": self.remark,
            "mode": self.mode,
            "requires_review": self.requires_review,
        }
        if self.value:
            result["value"] = self.value
        return result


def _load_registry() -> dict[tuple[str, str], ReviewedMfdsRemark]:
    resource = files("medicine_reference").joinpath(_RESOURCE)
    result: dict[tuple[str, str], ReviewedMfdsRemark] = {}
    with resource.open("r", encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="\t")
        expected = {
            "category", "remark", "mode", "qualifier_type", "display_text", "value", "rationale"
        }
        if set(reader.fieldnames or ()) != expected:
            raise RuntimeError("invalid MFDS REMARK registry header")
        for line_number, row in enumerate(reader, start=2):
            category = str(row["category"] or "").strip()
            raw_remark = str(row["remark"] or "")
            if raw_remark.startswith("@json:"):
                try:
                    decoded_remark = json.loads(raw_remark[len("@json:") :])
                except json.JSONDecodeError as exc:
                    raise RuntimeError(
                        f"invalid JSON-escaped MFDS REMARK at row {line_number}"
                    ) from exc
                if not isinstance(decoded_remark, str):
                    raise RuntimeError(
                        f"invalid JSON-escaped MFDS REMARK at row {line_number}"
                    )
                remark = decoded_remark.strip()
            else:
                remark = raw_remark.strip()
            mode = str(row["mode"] or "").strip()
            qualifier_type = str(row["qualifier_type"] or "").strip()
            display_text = str(row["display_text"] or "").strip()
            value = str(row["value"] or "").strip() or None
            rationale = str(row["rationale"] or "").strip()
            if category not in _SUPPORTED_CATEGORIES:
                raise RuntimeError(f"invalid MFDS REMARK category at row {line_number}: {category!r}")
            if not remark or not qualifier_type or not display_text or not rationale:
                raise RuntimeError(f"invalid MFDS REMARK registry row {line_number}: empty field")
            if mode not in _ALLOWED_MODES:
                raise RuntimeError(f"invalid MFDS REMARK mode at row {line_number}: {mode!r}")
            if mode == "interaction_window":
                try:
                    if int(value or "") <= 0:
                        raise ValueError
                except ValueError as exc:
                    raise RuntimeError(
                        f"invalid MFDS REMARK interaction window at row {line_number}"
                    ) from exc
            elif mode == "form_exclusion" and value != "topical":
                raise RuntimeError(f"invalid MFDS REMARK form exclusion at row {line_number}")
            elif mode == "composition_scope" and value != "all":
                raise RuntimeError(f"invalid MFDS REMARK composition scope at row {line_number}")
            elif mode in {"informational", "review_required"} and value is not None:
                raise RuntimeError(f"unexpected MFDS REMARK value at row {line_number}")
            key = (category, remark)
            if key in result:
                raise RuntimeError(f"duplicate MFDS REMARK registry row {line_number}: {key!r}")
            result[key] = ReviewedMfdsRemark(
                category=category,
                remark=remark,
                mode=mode,
                qualifier_type=qualifier_type,
                display_text=display_text,
                value=value,
                rationale=rationale,
            )
    return result


_REGISTRY = _load_registry()


def reviewed_mfds_remark(category: object, remark: object) -> ReviewedMfdsRemark | None:
    category_text = str(category or "").strip()
    remark_text = str(remark or "").strip()
    if not remark_text:
        return None
    result = _REGISTRY.get((category_text, remark_text))
    if result is None:
        raise ValueError(
            f"unreviewed MFDS REMARK for {category_text or '<missing-category>'}: {remark_text!r}"
        )
    return result


def reviewed_mfds_remark_count() -> int:
    return len(_REGISTRY)


def reviewed_mfds_remark_counts_by_category() -> dict[str, int]:
    counts = {category: 0 for category in _SUPPORTED_CATEGORIES}
    for category, _remark in _REGISTRY:
        counts[category] += 1
    return dict(sorted(counts.items()))


__all__ = [
    "ReviewedMfdsRemark",
    "reviewed_mfds_remark",
    "reviewed_mfds_remark_count",
    "reviewed_mfds_remark_counts_by_category",
]
