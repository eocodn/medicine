from __future__ import annotations

import re
from typing import Any, Iterable, Sequence

from .contract import SCHEMA_VERSION, Corpus, OcrBox
from .evaluation import evaluate_corpus
from .geometry import _Line, _Token, _document_lines


BASELINE_ID = "geometry_rule_v2"
_HEADER_PRODUCT = re.compile(r"^(?:약품명|의약품명|제품명|처방의약품명|약명)$")
_HEADER_DOSE = re.compile(r"(?:1회.*(?:투약|투여|복용).*(?:량|용량)|1회량)")
_HEADER_FREQUENCY = re.compile(r"(?:1일.*(?:투약|투여|복용).*(?:횟수|회수)|1일횟수)")
_HEADER_DAYS = re.compile(r"(?:총.*(?:투약|투여|복용).*일수|(?:투약|투여|복용)일수)")
_PRODUCT_PREFIX = re.compile(r"^(?:약명|제품명|약품명|의약품명)[:：](.+)$")
_PRODUCT_LABEL = re.compile(r"^(?:약명|제품명|약품명|의약품명)[:：]?$")
_COMMON_REGIMEN = re.compile(r"^공통(?:복용법|용법|복약방법)[:：]")
_EXPLICIT_REGIMEN = re.compile(
    r"(?:복용법|용법|복약방법|복용량|투약량|투여량|복용횟수|투약횟수|투여횟수|복용일수|투약일수|투여일수)[:：]"
)
_DOSE = re.compile(r"(?:^|\s)(\d+(?:\.\d+)?)\s*(정|tablet|캡슐|capsule|포|mL|ml)(?=\s|$)", re.I)
_FREQUENCY = re.compile(r"(?:1\s*일|하루)\s*(\d+)\s*회")
_DURATION = re.compile(r"(\d+)\s*일(?!\s*\d+\s*회)")
_TABLE_DOSE = re.compile(r"^(\d+(?:\.\d+)?)\s*(정|tablet|캡슐|capsule|포|mL|ml)$", re.I)
_TABLE_FREQUENCY = re.compile(r"^(\d+)\s*회$")
_TABLE_DAYS = re.compile(r"^(\d+)\s*일(?:분)?$")
_AMBIGUOUS_PACKET_TABLET_DOSE = re.compile(r"^(\d+(?:\.\d+)?)\s*포\(정\)$")


def _header_key(value: str) -> str | None:
    text = value.replace(" ", "")
    if _HEADER_PRODUCT.fullmatch(text):
        return "product"
    if _HEADER_DOSE.search(text):
        return "dose"
    if _HEADER_FREQUENCY.search(text):
        return "frequency"
    if _HEADER_DAYS.search(text):
        return "days"
    return None


def _header_anchors(line: _Line) -> list[tuple[str, float]]:
    anchors: list[tuple[str, float]] = []
    index = 0
    while index < len(line.items):
        match: tuple[str, float, int] | None = None
        for width in range(1, min(3, len(line.items) - index) + 1):
            group = line.items[index : index + width]
            key = _header_key("".join(item.text for item in group))
            if key is not None:
                match = (key, (group[0].x1 + group[-1].x2) / 2, width)
                break
        if match is None:
            index += 1
            continue
        anchors.append((match[0], match[1]))
        index += match[2]
    return anchors


def _unit(value: str) -> str:
    lowered = value.lower()
    if lowered in {"정", "tablet"}:
        return "tablet"
    if lowered in {"캡슐", "capsule"}:
        return "capsule"
    if lowered == "포":
        return "packet"
    if lowered == "ml":
        return "mL"
    return value


def _parse_regimen(value: str) -> dict[str, Any]:
    text = value.strip()
    result: dict[str, Any] = {}
    dose = _DOSE.search(text)
    if dose:
        amount = float(dose.group(1))
        result["dose_amount"] = int(amount) if amount.is_integer() else amount
        result["dose_unit"] = _unit(dose.group(2))
    frequency = _FREQUENCY.search(text)
    if frequency:
        result["frequency_per_day"] = int(frequency.group(1))
    duration_matches = list(_DURATION.finditer(text))
    if duration_matches:
        result["prescription_days"] = int(duration_matches[-1].group(1))
    return result


def _table_value(key: str, value: str) -> dict[str, Any]:
    compact = value.replace(" ", "")
    if key == "dose" and (match := _TABLE_DOSE.fullmatch(compact)):
        amount = float(match.group(1))
        return {
            "dose_amount": int(amount) if amount.is_integer() else amount,
            "dose_unit": _unit(match.group(2)),
        }
    if key == "frequency" and (match := _TABLE_FREQUENCY.fullmatch(compact)):
        return {"frequency_per_day": int(match.group(1))}
    if key == "days" and (match := _TABLE_DAYS.fullmatch(compact)):
        return {"prescription_days": int(match.group(1))}
    return {}


def _typed_item(item: _Token) -> tuple[str, dict[str, Any]] | None:
    compact = item.text.replace(" ", "")
    if match := _AMBIGUOUS_PACKET_TABLET_DOSE.fullmatch(compact):
        amount = float(match.group(1))
        return "dose", {
            "dose_amount": int(amount) if amount.is_integer() else amount,
            "dosage_text": compact,
        }
    for key in ("dose", "frequency", "days"):
        parsed = _table_value(key, item.text)
        if parsed:
            return key, parsed
    return None


def _structural_table_row(line: _Line) -> dict[str, Any] | None:
    typed: list[tuple[_Token, str, dict[str, Any]]] = []
    for item in line.items:
        classified = _typed_item(item)
        if classified is not None:
            typed.append((item, classified[0], classified[1]))
    if len({key for _, key, _ in typed}) < 2:
        return None

    first_structured_x = min(item.x1 for item, _, _ in typed)
    product_items = [item for item in line.items if item.x2 < first_structured_x and _typed_item(item) is None]
    product = _clean_product("".join(item.text for item in product_items))
    if not product:
        return None

    row = _new_row(product, [item.box_id for item in product_items])
    seen_keys: set[str] = set()
    for item, key, parsed in typed:
        if key in seen_keys:
            return None
        seen_keys.add(key)
        row["draft"].update(parsed)
        for field in parsed:
            row["evidence"][field] = [item.box_id]
    return row


def _structural_table_rows(lines: Sequence[_Line], header_index: int) -> tuple[list[dict[str, Any]], set[int]]:
    # Header OCR may be imperfect, but medication rows expose strongly typed values
    # (dose/frequency/duration) in stable columns. Infer rows from those observed
    # value types instead of maintaining typo-specific substitutions.
    rows: list[dict[str, Any]] = []
    consumed = {header_index}
    last_row_index = header_index
    for line_index in range(header_index + 1, len(lines)):
        line = lines[line_index]
        if _header_anchors(line):
            break
        row = _structural_table_row(line)
        if row is None:
            if rows and line.cy - lines[last_row_index].cy > max(90.0, line.height * 3.5):
                break
            continue
        rows.append(row)
        consumed.add(line_index)
        last_row_index = line_index
    return rows, consumed


def _unheaded_table_rows(lines: Sequence[_Line]) -> tuple[list[dict[str, Any]], set[int]]:
    candidates: list[tuple[int, dict[str, Any]]] = []
    for line_index, line in enumerate(lines):
        row = _structural_table_row(line)
        if row is not None:
            candidates.append((line_index, row))
    # Without a header, require a repeated row pattern before treating arbitrary
    # document text as a medication table. A single numeric-looking line is not
    # enough evidence to create a medication record.
    if len(candidates) < 2:
        return [], set()
    return [row for _, row in candidates], {index for index, _ in candidates}


def _clean_product(value: str) -> str:
    compact = value.replace(" ", "").strip()
    compact = re.sub(r"^(?:약명|제품명|약품명|의약품명)[:：]", "", compact)
    compact = re.sub(r"^[0-9]+[.)]", "", compact)
    return compact.strip()


def _new_row(product_query: str, product_evidence: Sequence[str]) -> dict[str, Any]:
    if not product_evidence:
        raise ValueError("product evidence is required")
    return {
        "row_id": product_evidence[0],
        "product_query": product_query,
        "draft": {},
        "uncertainty_codes": [],
        "evidence": {"product_query": list(product_evidence)},
    }


def _table_rows(lines: Sequence[_Line]) -> tuple[list[dict[str, Any]], set[int]]:
    for header_index, header in enumerate(lines):
        anchors = _header_anchors(header)
        keys = {key for key, _ in anchors}
        if "product" not in keys:
            continue
        if not {"product", "dose", "frequency", "days"}.issubset(keys):
            rows, consumed = _structural_table_rows(lines, header_index)
            if rows:
                return rows, consumed
            continue
        anchors.sort(key=lambda item: item[1])
        boundaries = [(anchors[index][1] + anchors[index + 1][1]) / 2 for index in range(len(anchors) - 1)]
        rows: list[dict[str, Any]] = []
        consumed = {header_index}
        for line_index in range(header_index + 1, len(lines)):
            line = lines[line_index]
            if _header_anchors(line):
                break
            cells: dict[str, list[_Token]] = {}
            for item in line.items:
                column = next((index for index, boundary in enumerate(boundaries) if item.cx < boundary), len(anchors) - 1)
                key = anchors[column][0]
                cells.setdefault(key, []).append(item)
            product_items = cells.get("product", [])
            product = _clean_product("".join(item.text for item in product_items))
            if not product or not any(key in cells for key in ("dose", "frequency", "days")):
                continue
            row = _new_row(product, [item.box_id for item in product_items])
            for key in ("dose", "frequency", "days"):
                if key in cells:
                    items = cells[key]
                    parsed = _table_value(key, "".join(item.text for item in items))
                    row["draft"].update(parsed)
                    evidence = [item.box_id for item in items]
                    for field in parsed:
                        row["evidence"][field] = evidence
            rows.append(row)
            consumed.add(line_index)
        if rows:
            return rows, consumed
    return [], set()


def _labeled_product(line: _Line) -> tuple[str, list[str]] | None:
    match = _PRODUCT_PREFIX.fullmatch(line.compact)
    if match is not None:
        product = _clean_product(match.group(1))
        return (product, [item.box_id for item in line.items]) if product else None

    # Real detector output often separates the printed field label and value into
    # distinct boxes. Reconstruct the label from the left edge of the line and
    # bind only the boxes to its right as the product value.
    for width in range(1, min(3, len(line.items)) + 1):
        label = "".join(item.text.replace(" ", "") for item in line.items[:width])
        if _PRODUCT_LABEL.fullmatch(label) is None:
            continue
        product_items = line.items[width:]
        product = _clean_product("".join(item.text for item in product_items))
        if product:
            return product, [item.box_id for item in line.items[:width] + product_items]
    return None


def _is_common_regimen(line: _Line) -> bool:
    return _COMMON_REGIMEN.match(line.compact) is not None


def _is_explicit_regimen(line: _Line) -> bool:
    return _EXPLICIT_REGIMEN.search(line.compact) is not None and not _is_common_regimen(line)


def _preprinted_regimen_fields(line: _Line) -> tuple[dict[str, Any], dict[str, list[str]]] | None:
    items = line.items
    fields: dict[str, Any] = {}
    evidence: dict[str, list[str]] = {}
    paired_labels = 0
    consumed_values: set[int] = set()

    for index in range(len(items) - 1):
        label = items[index].text.replace(" ", "")
        value_item = items[index + 1]
        if label == "1일":
            parsed = _table_value("frequency", value_item.text)
        elif label == "1회":
            classified = _typed_item(value_item)
            parsed = classified[1] if classified is not None and classified[0] == "dose" else {}
        else:
            continue
        if not parsed:
            continue
        paired_labels += 1
        consumed_values.add(index + 1)
        for field, value in parsed.items():
            fields[field] = value
            evidence[field] = [items[index].box_id, value_item.box_id]

    if paired_labels == 0:
        return None

    # Printed duration labels are especially vulnerable to OCR errors (and vary
    # by pharmacy), so the value token is identified by its numeric form rather
    # than by maintaining a vocabulary of label spellings.
    duration_candidates: list[tuple[int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        if index in consumed_values:
            continue
        parsed = _table_value("days", item.text)
        if parsed:
            duration_candidates.append((index, parsed))
    if duration_candidates:
        index, parsed = duration_candidates[-1]
        for field, value in parsed.items():
            fields[field] = value
            evidence[field] = [items[index].box_id]
    return fields, evidence


def _line_structured_fields(line: _Line) -> tuple[dict[str, Any], dict[str, list[str]]] | None:
    preprinted = _preprinted_regimen_fields(line)
    if preprinted is not None:
        return preprinted
    fields = _parse_regimen(line.text)
    evidence: dict[str, list[str]] = {}
    if fields:
        line_ids = [item.box_id for item in line.items]
        for field in fields:
            evidence[field] = line_ids

    for item in line.items:
        classified = _typed_item(item)
        if classified is None:
            continue
        _, parsed = classified
        for field, value in parsed.items():
            if field in fields and fields[field] != value:
                return None
            fields[field] = value
            evidence[field] = [item.box_id]
    return fields, evidence


def _block_fields(
    lines: Sequence[_Line],
    product_line_index: int,
    end_index: int,
    median_height: float,
    common_indexes: set[int],
) -> tuple[dict[str, Any], dict[str, list[str]], set[int]] | None:
    fields: dict[str, Any] = {}
    evidence: dict[str, list[str]] = {}
    consumed: set[int] = set()
    previous_cy = lines[product_line_index].cy
    for candidate_index in range(product_line_index + 1, end_index):
        if candidate_index in common_indexes:
            break
        line = lines[candidate_index]
        if line.cy - previous_cy > max(90.0, median_height * 3.2):
            break
        previous_cy = line.cy
        parsed = _line_structured_fields(line)
        if parsed is None:
            return None
        line_fields, line_evidence = parsed
        if not line_fields:
            continue
        for field, value in line_fields.items():
            if field in fields and fields[field] != value:
                return None
            fields[field] = value
            evidence[field] = line_evidence[field]
        consumed.add(candidate_index)
    families = {
        name
        for name, members in {
            "dose": {"dose_amount", "dose_unit"},
            "frequency": {"frequency_per_day"},
            "days": {"prescription_days"},
        }.items()
        if members & fields.keys()
    }
    if len(families) < 2:
        return None
    return fields, evidence, consumed


def _labeled_rows(lines: Sequence[_Line], median_height: float) -> tuple[list[dict[str, Any]], set[int]]:
    product_lines = [
        (index, product, evidence)
        for index, line in enumerate(lines)
        if (found := _labeled_product(line)) is not None
        for product, evidence in [found]
    ]
    if not product_lines:
        return [], set()
    rows = [_new_row(product, evidence) for _, product, evidence in product_lines]
    consumed: set[int] = {line_index for line_index, _, _ in product_lines}
    common_indexes = {index for index, line in enumerate(lines) if _is_common_regimen(line)}

    structural_blocks = []
    for row_index, (line_index, _, _) in enumerate(product_lines):
        next_product = product_lines[row_index + 1][0] if row_index + 1 < len(product_lines) else len(lines)
        structural_blocks.append(
            _block_fields(lines, line_index, next_product, median_height, common_indexes)
        )
    # A repeated bag pattern is only trusted when every medication block proves
    # its own structured regimen. This prevents a lone trailing regimen from
    # being attached to the nearest of several products by proximity alone.
    if structural_blocks and all(block is not None for block in structural_blocks):
        for row, block in zip(rows, structural_blocks, strict=True):
            assert block is not None
            fields, evidence, block_consumed = block
            row["draft"].update(fields)
            row["evidence"].update(evidence)
            consumed.update(block_consumed)
    elif len(product_lines) == 1:
        product_line_index = product_lines[0][0]
        prior_candidates: list[tuple[int, dict[str, Any], dict[str, list[str]]]] = []
        for candidate_index in range(product_line_index - 1, -1, -1):
            line = lines[candidate_index]
            if lines[product_line_index].cy - line.cy > max(420.0, median_height * 10.0):
                break
            parsed = _line_structured_fields(line)
            if parsed is None:
                continue
            fields, evidence = parsed
            families = {
                name
                for name, members in {
                    "dose": {"dose_amount", "dose_unit", "dosage_text"},
                    "frequency": {"frequency_per_day"},
                    "days": {"prescription_days"},
                }.items()
                if members & fields.keys()
            }
            if len(families) >= 2:
                prior_candidates.append((candidate_index, fields, evidence))
        # A sole product cannot suffer cross-medication association. Still
        # require exactly one structurally valid prior regimen rather than
        # selecting among multiple plausible instructions.
        if len(prior_candidates) == 1:
            candidate_index, fields, evidence = prior_candidates[0]
            rows[0]["draft"].update(fields)
            rows[0]["evidence"].update(evidence)
            consumed.add(candidate_index)

    for row_index, (line_index, _, _) in enumerate(product_lines):
        next_product = product_lines[row_index + 1][0] if row_index + 1 < len(product_lines) else len(lines)
        previous_cy = lines[line_index].cy
        for candidate_index in range(line_index + 1, next_product):
            if candidate_index in common_indexes:
                break
            if candidate_index in consumed:
                continue
            line = lines[candidate_index]
            if line.cy - previous_cy > max(55.0, median_height * 2.8):
                break
            previous_cy = line.cy
            if not _is_explicit_regimen(line):
                continue
            fields = _parse_regimen(line.text)
            if fields:
                rows[row_index]["draft"].update(fields)
                evidence = [item.box_id for item in line.items]
                for field in fields:
                    rows[row_index]["evidence"][field] = evidence
                consumed.add(candidate_index)

    for common_index in sorted(common_indexes):
        fields = _parse_regimen(lines[common_index].text)
        if not fields:
            continue
        members: list[int] = []
        cursor = common_index - 1
        lower_cy = lines[common_index].cy
        while cursor >= 0:
            line = lines[cursor]
            if lower_cy - line.cy > max(55.0, median_height * 2.8):
                break
            marker = next(
                (index for index, (product_index, _, _) in enumerate(product_lines) if product_index == cursor),
                None,
            )
            if marker is None:
                break
            members.insert(0, marker)
            lower_cy = line.cy
            cursor -= 1
        if not members:
            continue
        common_evidence = [item.box_id for item in lines[common_index].items]
        for row_index in members:
            for key, value in fields.items():
                if key not in rows[row_index]["draft"]:
                    rows[row_index]["draft"][key] = value
                    rows[row_index]["evidence"][key] = common_evidence
        consumed.add(common_index)

    if len(rows) > 1:
        unassociated = False
        for index, line in enumerate(lines):
            if index in consumed or _labeled_product(line) is not None or _is_common_regimen(line):
                continue
            if _parse_regimen(line.text):
                unassociated = True
                break
        if unassociated:
            for row in rows:
                row["uncertainty_codes"].append("UNRESOLVED_REGIMEN_ASSOCIATION")
    return rows, consumed


def parse_boxes(boxes: Iterable[OcrBox]) -> list[dict[str, Any]]:
    lines, median_height, low_confidence = _document_lines(boxes)
    rows, _ = _table_rows(lines)
    if not rows:
        rows, _ = _unheaded_table_rows(lines)
    if not rows:
        rows, _ = _labeled_rows(lines, median_height)
    if low_confidence:
        for row in rows:
            if "LOW_CONFIDENCE_OCR" not in row["uncertainty_codes"]:
                row["uncertainty_codes"].append("LOW_CONFIDENCE_OCR")
    return rows[:24]


def run_baseline(corpus: Corpus) -> dict[str, Any]:
    predictions = {
        "schema_version": SCHEMA_VERSION,
        "predictions": [
            {"case_id": case.case_id, "rows": parse_boxes(case.boxes)}
            for case in corpus.cases
        ],
    }
    return {
        "status": "ok",
        "baseline": BASELINE_ID,
        "predictions": predictions,
        "evaluation": evaluate_corpus(corpus, predictions),
    }


__all__ = ["BASELINE_ID", "parse_boxes", "run_baseline"]