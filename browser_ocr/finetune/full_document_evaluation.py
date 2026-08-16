from __future__ import annotations

import argparse
import json
import math
import re
from collections import OrderedDict, defaultdict
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


_MATCH_CORE_COVERAGE = 0.8
_CRITICAL_ROLES = {"product", "dose", "frequency", "duration"}
_FIELD_EVIDENCE = {
    "product_query": "product",
    "dose_amount": "dose",
    "dose_unit": "dose",
    "dosage_text": "dose",
    "frequency_per_day": "frequency",
    "prescription_days": "duration",
}


def _polygon_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        total += float(point[0]) * float(next_point[1]) - float(next_point[0]) * float(point[1])
    return abs(total) / 2.0


def _signed_area(polygon: Sequence[Sequence[float]]) -> float:
    total = 0.0
    for index, point in enumerate(polygon):
        next_point = polygon[(index + 1) % len(polygon)]
        total += float(point[0]) * float(next_point[1]) - float(next_point[0]) * float(point[1])
    return total / 2.0


def _line_intersection(a: Sequence[float], b: Sequence[float], c: Sequence[float], d: Sequence[float]) -> list[float]:
    denominator = (a[0] - b[0]) * (c[1] - d[1]) - (a[1] - b[1]) * (c[0] - d[0])
    if abs(denominator) < 1e-9:
        return [float(b[0]), float(b[1])]
    determinant_a = a[0] * b[1] - a[1] * b[0]
    determinant_b = c[0] * d[1] - c[1] * d[0]
    return [
        (determinant_a * (c[0] - d[0]) - (a[0] - b[0]) * determinant_b) / denominator,
        (determinant_a * (c[1] - d[1]) - (a[1] - b[1]) * determinant_b) / denominator,
    ]


def _clip_convex(subject: Sequence[Sequence[float]], clip: Sequence[Sequence[float]]) -> list[list[float]]:
    output = [[float(point[0]), float(point[1])] for point in subject]
    clockwise = _signed_area(clip) < 0

    def inside(point: Sequence[float], edge_start: Sequence[float], edge_end: Sequence[float]) -> bool:
        cross = (edge_end[0] - edge_start[0]) * (point[1] - edge_start[1]) - (
            edge_end[1] - edge_start[1]
        ) * (point[0] - edge_start[0])
        return cross <= 1e-9 if clockwise else cross >= -1e-9

    for edge_index, edge_start in enumerate(clip):
        edge_end = clip[(edge_index + 1) % len(clip)]
        input_points = output
        output = []
        if not input_points:
            break
        previous = input_points[-1]
        for current in input_points:
            current_inside = inside(current, edge_start, edge_end)
            previous_inside = inside(previous, edge_start, edge_end)
            if current_inside:
                if not previous_inside:
                    output.append(_line_intersection(previous, current, edge_start, edge_end))
                output.append(current)
            elif previous_inside:
                output.append(_line_intersection(previous, current, edge_start, edge_end))
            previous = current
    return output


def _overlap(gt_polygon: Sequence[Sequence[float]], pred_polygon: Sequence[Sequence[float]]) -> tuple[float, float, float]:
    gt_area = _polygon_area(gt_polygon)
    pred_area = _polygon_area(pred_polygon)
    intersection_polygon = _clip_convex(gt_polygon, pred_polygon)
    intersection = _polygon_area(intersection_polygon) if len(intersection_polygon) >= 3 else 0.0
    union = gt_area + pred_area - intersection
    core_coverage = intersection / gt_area if gt_area > 0 else 0.0
    prediction_coverage = intersection / pred_area if pred_area > 0 else 0.0
    iou = intersection / union if union > 0 else 0.0
    return core_coverage, prediction_coverage, iou


def _core_polygon(region: Mapping[str, Any]) -> Sequence[Sequence[float]]:
    return region.get("natural_text_polygon") or region["polygon"]


def _match_regions(gt_regions: Sequence[Mapping[str, Any]], predicted_regions: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    candidates: list[tuple[float, float, float, int, int]] = []
    for gt_index, gt in enumerate(gt_regions):
        for pred_index, pred in enumerate(predicted_regions):
            core, pred_coverage, iou = _overlap(_core_polygon(gt), pred["polygon"])
            if core >= _MATCH_CORE_COVERAGE:
                candidates.append((core, pred_coverage, iou, gt_index, pred_index))
    candidates.sort(reverse=True)
    gt_used: set[int] = set()
    pred_used: set[int] = set()
    mapping: dict[str, Mapping[str, Any]] = {}
    for _, _, _, gt_index, pred_index in candidates:
        if gt_index in gt_used or pred_index in pred_used:
            continue
        gt_used.add(gt_index)
        pred_used.add(pred_index)
        pred = predicted_regions[pred_index]
        raw_index = pred.get("index", pred_index + 1)
        mapping[f"region-{int(raw_index):04d}"] = gt_regions[gt_index]
    return mapping


def _number(text: str) -> float | None:
    match = re.search(r"\d+(?:\.\d+)?", text)
    if match is None:
        return None
    value = float(match.group(0))
    return int(value) if value.is_integer() else value


def _expected_rows(sample: Mapping[str, Any]) -> OrderedDict[str, dict[str, Any]]:
    rows: OrderedDict[str, dict[str, Any]] = OrderedDict()
    for region in sample["regions"]:
        role = region.get("semantic_role")
        group = region.get("association_group")
        if role not in _CRITICAL_ROLES or group == "document":
            continue
        row = rows.setdefault(str(group), {})
        row[role] = region
    return OrderedDict((group, row) for group, row in rows.items() if "product" in row)


def _row_group(row: Mapping[str, Any], region_map: Mapping[str, Mapping[str, Any]]) -> tuple[str | None, bool]:
    evidence = row.get("evidence", {}).get("product_query", [])
    mapped = [region_map.get(str(box_id)) for box_id in evidence]
    mapped = [region for region in mapped if region is not None]
    product_regions = [region for region in mapped if region.get("semantic_role") == "product"]
    groups = {str(region.get("association_group")) for region in product_regions}
    if len(groups) == 1:
        return next(iter(groups)), False
    return None, True


def _field_exact(expected: Mapping[str, Any], row: Mapping[str, Any], field: str) -> bool:
    if field == "product_query":
        return str(row.get("product_query") or "") == str(expected["product"]["text"])
    role = {"dose_amount": "dose", "frequency_per_day": "frequency", "prescription_days": "duration"}[field]
    expected_number = _number(str(expected[role]["text"])) if role in expected else None
    actual = row.get("draft", {}).get(field)
    if expected_number is None:
        return actual is None
    if isinstance(actual, bool) or not isinstance(actual, (int, float)):
        return False
    return math.isclose(float(actual), float(expected_number), rel_tol=0.0, abs_tol=1e-9)


def _association_counts(
    row: Mapping[str, Any],
    group: str,
    region_map: Mapping[str, Mapping[str, Any]],
) -> tuple[int, int]:
    cross = 0
    unproven = 0
    evidence = row.get("evidence", {})
    draft = row.get("draft", {})
    fields = ["product_query", *[field for field, value in draft.items() if value is not None]]
    for field in fields:
        if field not in _FIELD_EVIDENCE:
            continue
        ids = evidence.get(field)
        if not isinstance(ids, list) or not ids:
            unproven += 1
            continue
        mapped = [region_map.get(str(box_id)) for box_id in ids]
        if any(region is None for region in mapped):
            unproven += 1
            continue
        groups = {
            str(region.get("association_group"))
            for region in mapped
            if region is not None and region.get("association_group") != "document"
        }
        if not groups:
            unproven += 1
        elif groups != {group}:
            cross += 1
    return cross, unproven


def evaluate_sample(sample: Mapping[str, Any], result: Mapping[str, Any]) -> dict[str, Any]:
    predicted_regions = list(result.get("regions") or [])
    medication_rows = list(result.get("medications") or [])
    region_map = _match_regions(list(sample["regions"]), predicted_regions)
    expected = _expected_rows(sample)

    critical_gt = [region for region in sample["regions"] if region.get("critical")]
    critical_matched = sum(1 for region in critical_gt if any(mapped is region for mapped in region_map.values()))

    rows_by_group: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    unproven_rows = 0
    for row in medication_rows:
        group, unproven = _row_group(row, region_map)
        if group is None:
            unproven_rows += 1 if unproven else 0
            continue
        rows_by_group[group].append(row)

    matched_rows = 0
    missing_rows = 0
    unexpected_rows = unproven_rows
    critical_field_exact = 0
    critical_field_total = len(expected) * 4
    false_exact_fields = 0
    unresolved_fields = 0
    cross_associations = 0
    unproven_associations = 0
    row_results: list[dict[str, Any]] = []

    for group, expected_row in expected.items():
        predictions = rows_by_group.pop(group, [])
        if not predictions:
            missing_rows += 1
            row_results.append({"group": group, "status": "missing"})
            continue
        row = predictions[0]
        unexpected_rows += max(0, len(predictions) - 1)
        matched_rows += 1
        field_exact = {
            field: _field_exact(expected_row, row, field)
            for field in ("product_query", "dose_amount", "frequency_per_day", "prescription_days")
        }
        critical_field_exact += sum(field_exact.values())
        for field, exact in field_exact.items():
            if exact:
                continue
            if field == "product_query":
                false_exact_fields += 1
            elif row.get("draft", {}).get(field) is None:
                unresolved_fields += 1
            else:
                false_exact_fields += 1
        cross, unproven = _association_counts(row, group, region_map)
        cross_associations += cross
        unproven_associations += unproven
        row_results.append(
            {
                "group": group,
                "status": "matched",
                "product_query": row.get("product_query"),
                "field_exact": field_exact,
                "uncertainty_codes": list(row.get("uncertainty_codes") or []),
            }
        )

    unexpected_rows += sum(len(rows) for rows in rows_by_group.values())
    safety_pass = (
        cross_associations == 0
        and unproven_associations == 0
        and false_exact_fields == 0
        and unexpected_rows == 0
    )
    quality_pass = (
        missing_rows == 0
        and unexpected_rows == 0
        and critical_field_exact == critical_field_total
        and critical_matched == len(critical_gt)
    )
    return {
        "id": sample.get("id"),
        "layout_family": sample.get("layout_family"),
        "capture_profile": sample.get("capture_profile"),
        "expected_rows": len(expected),
        "predicted_rows": len(medication_rows),
        "matched_rows": matched_rows,
        "missing_rows": missing_rows,
        "unexpected_rows": unexpected_rows,
        "critical_field_exact": critical_field_exact,
        "critical_field_total": critical_field_total,
        "false_exact_fields": false_exact_fields,
        "unresolved_fields": unresolved_fields,
        "critical_detection_matched": critical_matched,
        "critical_detection_total": len(critical_gt),
        "cross_medication_associations": cross_associations,
        "unproven_associations": unproven_associations,
        "safety_pass": safety_pass,
        "quality_pass": quality_pass,
        "rows": row_results,
    }


def _ratio(numerator: int, denominator: int) -> float:
    return numerator / denominator if denominator else 1.0


def evaluate_outputs(corpus: Mapping[str, Any], outputs_root: str | Path) -> dict[str, Any]:
    root = Path(outputs_root)
    samples: list[dict[str, Any]] = []
    for sample in corpus["samples"]:
        result_path = root / str(sample["id"]) / "result.json"
        if not result_path.is_file():
            raise FileNotFoundError(f"missing full-document result: {result_path}")
        result = json.loads(result_path.read_text(encoding="utf-8"))
        samples.append(evaluate_sample(sample, result))

    totals = {
        key: sum(int(sample[key]) for sample in samples)
        for key in (
            "expected_rows",
            "predicted_rows",
            "matched_rows",
            "missing_rows",
            "unexpected_rows",
            "critical_field_exact",
            "critical_field_total",
            "false_exact_fields",
            "unresolved_fields",
            "critical_detection_matched",
            "critical_detection_total",
            "cross_medication_associations",
            "unproven_associations",
        )
    }
    by_capture: dict[str, dict[str, int]] = {}
    by_layout: dict[str, dict[str, int]] = {}
    for label, target in (("capture_profile", by_capture), ("layout_family", by_layout)):
        for value in sorted({str(sample[label]) for sample in samples}):
            subset = [sample for sample in samples if str(sample[label]) == value]
            target[value] = {
                "samples": len(subset),
                "quality_pass": sum(bool(sample["quality_pass"]) for sample in subset),
                "safety_pass": sum(bool(sample["safety_pass"]) for sample in subset),
                "critical_field_exact": sum(sample["critical_field_exact"] for sample in subset),
                "critical_field_total": sum(sample["critical_field_total"] for sample in subset),
            }

    pass_all = all(sample["quality_pass"] and sample["safety_pass"] for sample in samples)
    return {
        "schema_version": 1,
        "status": "pass" if pass_all else "fail",
        "samples": samples,
        "totals": totals,
        "metrics": {
            "row_recall": _ratio(totals["matched_rows"], totals["expected_rows"]),
            "critical_field_exact_accuracy": _ratio(totals["critical_field_exact"], totals["critical_field_total"]),
            "critical_detection_recall": _ratio(
                totals["critical_detection_matched"], totals["critical_detection_total"]
            ),
        },
        "by_capture_profile": by_capture,
        "by_layout_family": by_layout,
    }


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="ocr-full-document-evaluate")
    parser.add_argument("--corpus", required=True)
    parser.add_argument("--outputs", required=True)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(list(argv) if argv is not None else None)
    corpus = json.loads(Path(args.corpus).read_text(encoding="utf-8"))
    result = evaluate_outputs(corpus, args.outputs)
    print(json.dumps(result, ensure_ascii=False, sort_keys=True if args.json else False, indent=None if args.json else 2))
    return 0 if result["status"] == "pass" else 3


if __name__ == "__main__":
    raise SystemExit(main())
