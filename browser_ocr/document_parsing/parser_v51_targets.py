from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

from .parser_v5_contract import validate_parser_v5_pair


ROW_FIELD_ROLES = ("product", "dose", "frequency", "duration", "instruction", "schedule")


@dataclass(frozen=True)
class ParserV51SpanPieceTarget:
    node_index: int
    node_id: str
    source_span_id: str
    operation: str
    start_char: int
    end_char: int
    start_byte: int
    end_byte: int


@dataclass(frozen=True)
class ParserV51FieldTarget:
    semantic_role: str
    pieces: tuple[ParserV51SpanPieceTarget, ...]


@dataclass(frozen=True)
class ParserV51MedicationRowTarget:
    medication_id: str
    fields: tuple[ParserV51FieldTarget, ...]

    def field(self, semantic_role: str) -> ParserV51FieldTarget:
        for value in self.fields:
            if value.semantic_role == semantic_role:
                return value
        raise KeyError(semantic_role)


@dataclass(frozen=True)
class ParserV51RowTargets:
    rows: tuple[ParserV51MedicationRowTarget, ...]


def _char_byte_offsets(text: str) -> tuple[int, ...]:
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + len(char.encode("utf-8")))
    return tuple(offsets)


def _piece(
    *,
    node_index: int,
    node_id: str,
    source_span_id: str,
    operation: str,
    text: str,
    start_char: int,
    end_char: int,
) -> ParserV51SpanPieceTarget:
    offsets = _char_byte_offsets(text)
    return ParserV51SpanPieceTarget(
        node_index=node_index,
        node_id=node_id,
        source_span_id=source_span_id,
        operation=operation,
        start_char=start_char,
        end_char=end_char,
        start_byte=offsets[start_char],
        end_byte=offsets[end_char],
    )


def build_parser_v51_row_targets(
    document: Mapping[str, object],
    observation: Mapping[str, object],
) -> ParserV51RowTargets:
    """Build direct medication-row supervision from visible OCR source spans.

    Header/context/other labels are intentionally irrelevant here. The direct
    decoder is trained only on text evidence that belongs to an observable
    medication row. OCR text that is not selected by any row/field target is
    therefore learned as unused evidence rather than as a hand-maintained
    semantic taxonomy.
    """

    validate_parser_v5_pair(document, observation)  # type: ignore[arg-type]
    raw_medications = document["medications"]
    raw_spans = document["spans"]
    raw_nodes = observation["nodes"]
    if not isinstance(raw_medications, list) or not isinstance(raw_spans, list) or not isinstance(raw_nodes, list):
        raise ValueError("Parser v5.1 row targets require medication, span and observation lists")

    medication_ids = [str(medication["medication_id"]) for medication in raw_medications]
    medication_set = set(medication_ids)
    span_truth = {str(span["span_id"]): span for span in raw_spans}
    collected: dict[str, dict[str, list[ParserV51SpanPieceTarget]]] = {
        medication_id: {role: [] for role in ROW_FIELD_ROLES}
        for medication_id in medication_ids
    }

    for node_index, raw_node in enumerate(raw_nodes):
        if not isinstance(raw_node, Mapping):
            raise ValueError("Parser v5.1 observation node must be an object")
        node_id = str(raw_node["node_id"])
        operation = str(raw_node["operation"])
        text = str(raw_node["text"])
        targets = raw_node["targets"]
        segments = raw_node["source_segments"]
        if not isinstance(targets, list) or not isinstance(segments, list):
            raise ValueError("Parser v5.1 observation provenance must use lists")
        label_status = {
            str(target["source_span_id"]): str(target["label_status"])
            for target in targets
            if isinstance(target, Mapping)
        }
        for segment in segments:
            if not isinstance(segment, Mapping):
                raise ValueError("Parser v5.1 source segment must be an object")
            source_span_id = str(segment["source_span_id"])
            if label_status.get(source_span_id) != "labeled":
                continue
            truth = span_truth[source_span_id]
            semantic_role = str(truth["semantic_role"])
            association_group = truth.get("association_group")
            if semantic_role not in ROW_FIELD_ROLES or association_group not in medication_set:
                continue
            start_char = int(segment["start_char"])
            end_char = int(segment["end_char"])
            collected[str(association_group)][semantic_role].append(
                _piece(
                    node_index=node_index,
                    node_id=node_id,
                    source_span_id=source_span_id,
                    operation=operation,
                    text=text,
                    start_char=start_char,
                    end_char=end_char,
                )
            )

    rows: list[ParserV51MedicationRowTarget] = []
    for medication_id in medication_ids:
        fields = collected[medication_id]
        # A row without observable product text cannot produce a safe product
        # query at runtime. Do not ask the decoder to hallucinate one from
        # regimen/context evidence alone.
        if not fields["product"]:
            continue
        row_fields = []
        for role in ROW_FIELD_ROLES:
            pieces = sorted(
                fields[role],
                key=lambda item: (item.node_index, item.start_char, item.end_char, item.source_span_id),
            )
            row_fields.append(ParserV51FieldTarget(semantic_role=role, pieces=tuple(pieces)))
        rows.append(ParserV51MedicationRowTarget(medication_id=medication_id, fields=tuple(row_fields)))
    return ParserV51RowTargets(rows=tuple(rows))


def observed_piece_text(node_text: str, piece: ParserV51SpanPieceTarget) -> str:
    return node_text[piece.start_char : piece.end_char]


def required_field_pieces(field: ParserV51FieldTarget) -> tuple[ParserV51SpanPieceTarget, ...]:
    """Choose the source pieces required to reconstruct one observed field.

    Split OCR pieces are complementary and all non-duplicate fragments are
    required. Multiple complete observations created by duplication are
    alternatives, so one canonical non-duplicate piece is sufficient. This is
    training-only provenance logic; runtime inference receives no operation
    labels and must learn the corresponding membership pattern from text and
    document geometry.
    """

    if not field.pieces:
        return ()
    pieces = tuple(
        sorted(
            field.pieces,
            key=lambda item: (item.node_index, item.start_char, item.end_char, item.source_span_id),
        )
    )
    if any(piece.operation == "split" for piece in pieces):
        required = tuple(piece for piece in pieces if piece.operation != "duplicate")
        return required or (pieces[0],)
    preferred = tuple(piece for piece in pieces if piece.operation != "duplicate")
    return (preferred[0] if preferred else pieces[0],)


__all__ = [
    "ROW_FIELD_ROLES",
    "ParserV51FieldTarget",
    "ParserV51MedicationRowTarget",
    "ParserV51RowTargets",
    "ParserV51SpanPieceTarget",
    "build_parser_v51_row_targets",
    "observed_piece_text",
    "required_field_pieces",
]