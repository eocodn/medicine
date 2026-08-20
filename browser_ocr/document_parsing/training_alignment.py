from __future__ import annotations

from typing import Any, Mapping, Sequence

from .region_alignment import match_region_candidates, observed_region_id

MODEL_ROLES = {"product", "product_label", "dose", "frequency", "duration", "instruction", "header", "other"}
_FIELD_ROLES = {"dose", "frequency", "duration", "instruction"}


def normalize_semantic_role(value: object) -> str:
    role = str(value or "").strip()
    return role if role in MODEL_ROLES else "other"


def _polygon(value: object) -> list[list[float]]:
    if not isinstance(value, list) or len(value) != 4:
        raise ValueError("observed region polygon must contain four points")
    points: list[list[float]] = []
    for point in value:
        if not isinstance(point, list) or len(point) != 2:
            raise ValueError("observed region polygon point must contain x/y")
        points.append([float(point[0]), float(point[1])])
    return points


def align_observation_nodes(
    truth_regions: Sequence[Mapping[str, Any]],
    observed_regions: Sequence[Mapping[str, Any]],
) -> list[dict[str, object]]:
    indexed = list(enumerate(observed_regions, start=1))
    indexed.sort(key=lambda item: observed_region_id(item[1], item[0]))
    nodes: list[dict[str, object]] = []
    for fallback_index, observed in indexed:
        node_id = observed_region_id(observed, fallback_index)
        score = observed.get("recognition_score", observed.get("confidence", 0.0))
        if isinstance(score, bool) or not isinstance(score, (int, float)):
            raise ValueError(f"{node_id} recognition confidence must be numeric")
        confidence = float(score)
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"{node_id} recognition confidence must be between 0 and 1")
        matches = match_region_candidates(truth_regions, observed)
        target_ids = sorted({str(truth.get("region_id") or "") for truth, _ in matches if truth.get("region_id")})
        signatures = {
            (
                normalize_semantic_role(truth.get("semantic_role")),
                str(truth.get("association_group") or "") or None,
            )
            for truth, _ in matches
        }
        if not matches:
            label_status = "labeled"
            semantic_role: str | None = "other"
            association_group: str | None = None
        elif len(signatures) == 1:
            label_status = "labeled"
            semantic_role, association_group = next(iter(signatures))
            if semantic_role == "other":
                association_group = None
        else:
            label_status = "ambiguous"
            semantic_role = None
            association_group = None
        nodes.append(
            {
                "node_id": node_id,
                "text": str(observed.get("text") or ""),
                "confidence": confidence,
                "polygon": _polygon(observed.get("polygon")),
                "target_region_ids": target_ids,
                "label_status": label_status,
                "semantic_role": semantic_role,
                "association_group": association_group,
            }
        )
    return nodes


def build_relation_labels(nodes: Sequence[Mapping[str, Any]]) -> list[dict[str, str]]:
    products = [
        node for node in nodes
        if node.get("label_status") == "labeled"
        and node.get("semantic_role") == "product"
        and node.get("association_group")
    ]
    fields = [
        node for node in nodes
        if node.get("label_status") == "labeled"
        and node.get("semantic_role") in _FIELD_ROLES
        and node.get("association_group")
    ]
    relations: list[dict[str, str]] = []
    for product in products:
        for field in fields:
            relations.append(
                {
                    "product_node_id": str(product["node_id"]),
                    "field_node_id": str(field["node_id"]),
                    "label": (
                        "same_medication"
                        if product["association_group"] == field["association_group"]
                        else "different_medication"
                    ),
                }
            )
    relations.sort(key=lambda item: (item["product_node_id"], item["field_node_id"], item["label"]))
    return relations


__all__ = ["MODEL_ROLES", "align_observation_nodes", "build_relation_labels", "normalize_semantic_role"]
