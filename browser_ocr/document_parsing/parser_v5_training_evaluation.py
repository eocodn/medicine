from __future__ import annotations

from typing import Any, Mapping, Sequence

import paddle
import paddle.nn.functional as F

from .parser_v5_dataset import ParserV5Dataset
from .parser_v5_decode import ParserV5DecodeConfig
from .parser_v5_encoder_paddle import parser_v5_tensors
from .parser_v5_evaluation import evaluate_parser_v5_rows
from .parser_v5_heads_paddle import parser_v5_head_targets
from .parser_v5_inference_paddle import run_parser_v5_inference
from .parser_v5_model_input import build_parser_v5_model_input, build_parser_v5_runtime_input
from .parser_v5_structured_targets import build_parser_v5_structured_targets


def prepare_parser_v5_sample(sample: Mapping[str, Any], config: Any):
    truth = sample["truth"]
    observation = sample["observation"]
    model_input = build_parser_v5_model_input(truth, observation, max_text_bytes=config.max_text_bytes)
    if not model_input.node_ids:
        return None
    tensors = parser_v5_tensors(model_input)
    structured = build_parser_v5_structured_targets(truth, observation)
    targets = parser_v5_head_targets(structured, node_count=len(model_input.node_ids))
    return tensors, targets


def _binary_counts(
    logits: paddle.Tensor,
    targets: paddle.Tensor,
    mask: paddle.Tensor,
) -> tuple[int, int, int, int]:
    predicted = (F.sigmoid(logits) >= 0.5).astype("int64").numpy().reshape([-1]).tolist()
    actual = targets.astype("int64").numpy().reshape([-1]).tolist()
    active = mask.numpy().reshape([-1]).tolist()
    tp = fp = fn = correct = 0
    for guess, truth, enabled in zip(predicted, actual, active, strict=True):
        if float(enabled) <= 0:
            continue
        correct += int(int(guess) == int(truth))
        tp += int(int(guess) == 1 and int(truth) == 1)
        fp += int(int(guess) == 1 and int(truth) == 0)
        fn += int(int(guess) == 0 and int(truth) == 1)
    return tp, fp, fn, correct


def _categorical_accuracy(
    guess: paddle.Tensor,
    truth: paddle.Tensor,
    mask: paddle.Tensor,
) -> tuple[int, int]:
    active = mask > 0
    correct = int(((guess == truth) & active).astype("int64").sum().item())
    total = int(active.astype("int64").sum().item())
    return correct, total


@paddle.no_grad()
def evaluate_parser_v5(
    model: Any,
    datasets: Sequence[ParserV5Dataset],
    config: Any,
) -> dict[str, float | int]:
    model.eval()
    role_tp = role_fp = role_fn = role_active = role_correct = 0
    candidate_correct = candidate_total = 0
    assignment_correct = assignment_total = 0
    assignment_none_correct = assignment_none_total = 0
    documents = 0
    decoded_counts = {
        "gold_rows": 0,
        "predicted_rows": 0,
        "product_tp": 0,
        "product_fp": 0,
        "product_fn": 0,
        "field_total": 0,
        "field_exact": 0,
        "field_false_exact": 0,
        "field_unresolved": 0,
        "cross_medication_associations": 0,
        "invented_values": 0,
        "zero_medication_false_rows": 0,
    }
    for dataset in datasets:
        for sample in dataset.samples:
            prepared = prepare_parser_v5_sample(sample, config)
            if prepared is None:
                continue
            tensors, targets = prepared
            hidden, role_logits = model.encoder(tensors)
            candidate_logits, assignment_logits = model.heads(hidden, tensors.relation_features, targets)
            tp, fp, fn, correct = _binary_counts(role_logits, tensors.role_targets, tensors.role_mask)
            role_tp += tp
            role_fp += fp
            role_fn += fn
            role_correct += correct
            role_active += int(tensors.role_mask.sum().item())

            candidate_guess = (F.sigmoid(candidate_logits) >= 0.5).astype("int64")
            candidate_truth = targets.candidate_targets.astype("int64")
            candidate_active = targets.candidate_mask > 0
            candidate_correct += int(
                ((candidate_guess == candidate_truth) & candidate_active).astype("int64").sum().item()
            )
            candidate_total += int(candidate_active.astype("int64").sum().item())

            if targets.assignment_targets.shape[0] > 0:
                assignment_guess = paddle.argmax(assignment_logits, axis=1)
                correct_count, total_count = _categorical_accuracy(
                    assignment_guess,
                    targets.assignment_targets,
                    targets.assignment_positive_mask,
                )
                assignment_correct += correct_count
                assignment_total += total_count
                none_correct, none_total = _categorical_accuracy(
                    assignment_guess,
                    targets.assignment_targets,
                    targets.assignment_none_mask,
                )
                assignment_none_correct += none_correct
                assignment_none_total += none_total

            truth = sample["truth"]
            observation = sample["observation"]
            runtime_nodes = [
                {
                    "node_id": node["node_id"],
                    "text": node["text"],
                    "detector_confidence": node["detector_confidence"],
                    "recognizer_confidence": node["recognizer_confidence"],
                    "polygon": node["polygon"],
                }
                for node in observation["nodes"]
            ]
            if runtime_nodes:
                runtime_input = build_parser_v5_runtime_input(
                    document_id=str(truth["document_id"]),
                    width=truth["width"],
                    height=truth["height"],
                    nodes=runtime_nodes,
                    max_text_bytes=config.max_text_bytes,
                )
                inference = run_parser_v5_inference(
                    encoder=model.encoder,
                    heads=model.heads,
                    model_input=runtime_input,
                    nodes=runtime_nodes,
                    config=ParserV5DecodeConfig(),
                )
                rows = inference.rows
            else:
                rows = ()
            decoded = evaluate_parser_v5_rows(truth, observation, rows)
            for key in decoded_counts:
                decoded_counts[key] += int(decoded[key])
            documents += 1

    precision = role_tp / max(role_tp + role_fp, 1)
    recall = role_tp / max(role_tp + role_fn, 1)
    role_f1 = 2 * precision * recall / max(precision + recall, 1e-12)
    product_precision = decoded_counts["product_tp"] / max(
        decoded_counts["product_tp"] + decoded_counts["product_fp"], 1
    )
    product_recall = decoded_counts["product_tp"] / max(
        decoded_counts["product_tp"] + decoded_counts["product_fn"], 1
    )
    field_exact_rate = decoded_counts["field_exact"] / max(decoded_counts["field_total"], 1)
    field_unresolved_rate = decoded_counts["field_unresolved"] / max(decoded_counts["field_total"], 1)
    return {
        "documents": documents,
        "role_micro_f1": role_f1,
        "role_element_accuracy": role_correct / max(role_active, 1),
        "candidate_accuracy": candidate_correct / max(candidate_total, 1),
        # Preserve the historical meaning: this is accuracy on true medication
        # field -> product assignments, not a majority-weighted mixture with NONE.
        "assignment_accuracy": assignment_correct / max(assignment_total, 1),
        "assignment_supervised": assignment_total,
        "assignment_none_accuracy": assignment_none_correct / max(assignment_none_total, 1),
        "assignment_none_supervised": assignment_none_total,
        **decoded_counts,
        "decoded_product_precision": product_precision,
        "decoded_product_recall": product_recall,
        "decoded_field_exact_rate": field_exact_rate,
        "decoded_field_unresolved_rate": field_unresolved_rate,
    }


def evaluate_parser_v5_views(
    model: Any,
    datasets: Sequence[ParserV5Dataset],
    config: Any,
) -> dict[str, Any]:
    overall = evaluate_parser_v5(model, datasets, config)
    views: dict[str, dict[str, float | int]] = {}
    scores: list[float] = []
    for dataset in datasets:
        metrics = evaluate_parser_v5(model, [dataset], config)
        views[dataset.dataset_id] = metrics
        applicable = [float(metrics["role_micro_f1"]), float(metrics["candidate_accuracy"])]
        if int(metrics["assignment_supervised"]) > 0:
            applicable.append(float(metrics["assignment_accuracy"]))
        if int(metrics["assignment_none_supervised"]) > 0:
            applicable.append(float(metrics["assignment_none_accuracy"]))
        if int(metrics["gold_rows"]) == 0:
            decoded_score = 1.0 if int(metrics["zero_medication_false_rows"]) == 0 else 0.0
        else:
            decoded_score = (
                float(metrics["decoded_product_precision"])
                + float(metrics["decoded_product_recall"])
                + float(metrics["decoded_field_exact_rate"])
            ) / 3.0
            safety_errors = (
                int(metrics["field_false_exact"])
                + int(metrics["cross_medication_associations"])
                + int(metrics["invented_values"])
            )
            safety_denominator = max(int(metrics["field_total"]) + int(metrics["gold_rows"]), 1)
            decoded_score = max(0.0, decoded_score - safety_errors / safety_denominator)
        applicable.append(decoded_score)
        scores.append(sum(applicable) / len(applicable))
    if not scores:
        raise ValueError("Parser v5 validation produced no development views")
    return {
        **overall,
        "views": views,
        "worst_view_score": min(scores),
    }


def parser_v5_selection_score(metrics: Mapping[str, Any]) -> float:
    return float(metrics["worst_view_score"])


__all__ = [
    "evaluate_parser_v5",
    "evaluate_parser_v5_views",
    "parser_v5_selection_score",
    "prepare_parser_v5_sample",
]