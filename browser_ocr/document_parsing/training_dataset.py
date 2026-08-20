from __future__ import annotations

import hashlib
import fcntl
import json
import math
import os
import re
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from .artifact_invariants import normalized_polygon, normalized_provenance, validate_parser_document_set
from .dataset_metadata import normalize_parser_metadata
from .draft_contract import normalize_parser_draft
from .training_alignment import MODEL_ROLES, build_relation_labels


SCHEMA_VERSION = 2
TASK = "medication_document_parser"
SOURCE_POLICY = "synthetic_train_real_holdout_v1"
_STATE_FILE = ".dataset-state.json"
_LOCK_FILE = ".dataset.lock"
_ALLOWED_SPLITS = {"train", "val", "test"}
_ALLOWED_SOURCES = {"synthetic", "real_deidentified"}
_ALLOWED_OBSERVATIONS = {"oracle", "synthetic_ocr", "runtime_ocr"}
_ALLOWED_LABEL_STATUS = {"labeled", "ambiguous", "unlabeled"}
_ALLOWED_RELATIONS = {"same_medication", "different_medication"}
_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_TAG_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{0,63}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_MANIFEST_FIELDS = {
    "schema_version",
    "dataset_id",
    "task",
    "source_policy",
    "samples_file",
    "samples_sha256",
    "metadata_sha256",
    "document_count",
    "metadata",
}
_DOCUMENT_FIELDS = {
    "document_id", "split", "source_kind", "image_sha256", "width", "height", "layout_family",
    "scenario_tags", "risk_tags", "privacy", "provenance", "observation", "relations", "gold_rows",
    "gold_rows_reviewed", "annotation_status",
}
_NODE_FIELDS = {
    "node_id",
    "text",
    "confidence",
    "polygon",
    "target_region_ids",
    "label_status",
    "semantic_role",
    "association_group",
}
_RELATION_FIELDS = {"product_node_id", "field_node_id", "label"}
_GOLD_ROW_FIELDS = {"gold_row_id", "product_query", "draft"}


class ParserDatasetError(ValueError):
    pass


@dataclass(frozen=True)
class ParserDataset:
    root: Path
    manifest_path: Path
    dataset_id: str
    metadata: Mapping[str, Any]
    documents: tuple[dict[str, Any], ...]
    fingerprint: str


def _canonical_json(value: object) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ParserDatasetError(f"value is not strict JSON: {exc}") from exc


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _require_mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ParserDatasetError(f"{label} must be an object")
    return value


def _reject_unknown(value: Mapping[str, Any], allowed: set[str], label: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ParserDatasetError(f"unsupported {label} fields: {', '.join(map(str, unknown))}")


def _require_id(value: object, label: str) -> str:
    text = str(value or "").strip()
    if not _ID_RE.fullmatch(text):
        raise ParserDatasetError(f"{label} must be a 1-128 character ASCII id")
    return text


def _require_tags(value: object, label: str) -> list[str]:
    if not isinstance(value, list):
        raise ParserDatasetError(f"{label} must be a list")
    tags: list[str] = []
    for raw in value:
        tag = str(raw or "").strip()
        if not _TAG_RE.fullmatch(tag):
            raise ParserDatasetError(f"{label} values must be lowercase ASCII tags")
        if tag in tags:
            raise ParserDatasetError(f"{label} values must be unique")
        tags.append(tag)
    return tags


def _normalize_gold_rows(value: object, label: str) -> list[dict[str, Any]]:
    if not isinstance(value, list) or len(value) > 24:
        raise ParserDatasetError(f"{label} must be a list with at most 24 rows")
    rows: list[dict[str, Any]] = []
    ids: set[str] = set()
    for index, raw in enumerate(value):
        data = _require_mapping(raw, f"{label}[{index}]")
        _reject_unknown(data, _GOLD_ROW_FIELDS, f"{label}[{index}]")
        row_id = _require_id(data.get("gold_row_id"), f"{label}[{index}].gold_row_id")
        if row_id in ids:
            raise ParserDatasetError(f"duplicate gold_row_id in {label}: {row_id}")
        ids.add(row_id)
        product_query = str(data.get("product_query") or "").strip()
        if not product_query or len(product_query) > 256:
            raise ParserDatasetError(f"{label}[{index}].product_query must contain 1-256 characters")
        draft = _require_mapping(data.get("draft"), f"{label}[{index}].draft")
        try:
            normalized_draft = normalize_parser_draft(draft)
        except ValueError as exc:
            raise ParserDatasetError(f"invalid {label}[{index}].draft: {exc}") from exc
        rows.append({"gold_row_id": row_id, "product_query": product_query, "draft": normalized_draft})
    return rows


def _normalize_node(value: object, document_id: str, index: int, *, width: int, height: int) -> dict[str, Any]:
    data = _require_mapping(value, f"{document_id}.observation.nodes[{index}]")
    _reject_unknown(data, _NODE_FIELDS, f"{document_id}.observation.nodes[{index}]")
    node_id = _require_id(data.get("node_id"), f"{document_id}.node_id")
    text = data.get("text")
    if not isinstance(text, str) or len(text) > 512 or any(char in text for char in "\r\n\x00"):
        raise ParserDatasetError(f"{document_id}/{node_id}.text must be a <=512 character single-line string")
    confidence = data.get("confidence")
    if isinstance(confidence, bool) or not isinstance(confidence, (int, float)):
        raise ParserDatasetError(f"{document_id}/{node_id}.confidence must be numeric")
    confidence_number = float(confidence)
    if not math.isfinite(confidence_number) or not 0.0 <= confidence_number <= 1.0:
        raise ParserDatasetError(f"{document_id}/{node_id}.confidence must be between 0 and 1")
    targets_raw = data.get("target_region_ids")
    if not isinstance(targets_raw, list) or len(targets_raw) > 32:
        raise ParserDatasetError(f"{document_id}/{node_id}.target_region_ids must be a list with at most 32 ids")
    targets: list[str] = []
    for raw_target in targets_raw:
        target = _require_id(raw_target, f"{document_id}/{node_id}.target_region_ids")
        if target not in targets:
            targets.append(target)
    status = str(data.get("label_status") or "")
    if status not in _ALLOWED_LABEL_STATUS:
        raise ParserDatasetError(f"{document_id}/{node_id}.label_status is unsupported")
    role_raw = data.get("semantic_role")
    group_raw = data.get("association_group")
    if status in {"ambiguous", "unlabeled"}:
        if role_raw is not None or group_raw is not None:
            raise ParserDatasetError(f"{document_id}/{node_id} {status} nodes must not carry role/group labels")
        role = None
        group = None
    else:
        role = str(role_raw or "")
        if role not in MODEL_ROLES:
            raise ParserDatasetError(f"{document_id}/{node_id}.semantic_role is unsupported")
        group = None if group_raw is None else _require_id(group_raw, f"{document_id}/{node_id}.association_group")
        if role in {"product", "product_label", "dose", "frequency", "duration"} and group is None:
            raise ParserDatasetError(f"{document_id}/{node_id} medication role requires association_group")
        if role in {"product", "product_label", "dose", "frequency", "duration"} and group == "document":
            raise ParserDatasetError(f"{document_id}/{node_id} medication association_group cannot use reserved document group")
        if role == "other":
            group = None
    try:
        polygon = normalized_polygon(
            data.get("polygon"),
            label=f"{document_id}/{node_id}.polygon",
            width=width,
            height=height,
        )
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc
    return {
        "node_id": node_id,
        "text": text,
        "confidence": confidence_number,
        "polygon": polygon,
        "target_region_ids": targets,
        "label_status": status,
        "semantic_role": role,
        "association_group": group,
    }


def _normalize_document(value: object) -> dict[str, Any]:
    data = _require_mapping(value, "parser document")
    _reject_unknown(data, _DOCUMENT_FIELDS, "parser document")
    document_id = _require_id(data.get("document_id"), "document_id")
    split = str(data.get("split") or "")
    if split not in _ALLOWED_SPLITS:
        raise ParserDatasetError(f"{document_id}.split must be train, val or test")
    source_kind = str(data.get("source_kind") or "")
    if source_kind not in _ALLOWED_SOURCES:
        raise ParserDatasetError(f"{document_id}.source_kind is unsupported")
    if source_kind == "real_deidentified" and split == "train":
        raise ParserDatasetError(f"{document_id} real_deidentified documents cannot use train split")
    image_sha = str(data.get("image_sha256") or "")
    if not _SHA256_RE.fullmatch(image_sha):
        raise ParserDatasetError(f"{document_id}.image_sha256 must be lowercase SHA-256")
    width = data.get("width")
    height = data.get("height")
    if isinstance(width, bool) or not isinstance(width, int) or width <= 0:
        raise ParserDatasetError(f"{document_id}.width must be a positive integer")
    if isinstance(height, bool) or not isinstance(height, int) or height <= 0:
        raise ParserDatasetError(f"{document_id}.height must be a positive integer")
    layout_family = _require_id(data.get("layout_family"), f"{document_id}.layout_family")
    privacy = _require_mapping(data.get("privacy"), f"{document_id}.privacy")
    if set(privacy) != {"contains_patient_data", "deidentified"}:
        raise ParserDatasetError(f"{document_id}.privacy must contain contains_patient_data and deidentified")
    if privacy.get("contains_patient_data") is not False:
        raise ParserDatasetError(f"{document_id} contains patient data")
    if not isinstance(privacy.get("deidentified"), bool):
        raise ParserDatasetError(f"{document_id}.privacy.deidentified must be boolean")
    if source_kind == "real_deidentified" and privacy.get("deidentified") is not True:
        raise ParserDatasetError(f"{document_id} real data must be explicitly deidentified")

    try:
        provenance = normalized_provenance(
            data.get("provenance"),
            source_kind=source_kind,
            label=f"{document_id}.provenance",
        )
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc

    observation = _require_mapping(data.get("observation"), f"{document_id}.observation")
    if set(observation) != {"kind", "profile", "nodes"}:
        raise ParserDatasetError(f"{document_id}.observation must contain kind, profile and nodes")
    observation_kind = str(observation.get("kind") or "")
    if observation_kind not in _ALLOWED_OBSERVATIONS:
        raise ParserDatasetError(f"{document_id}.observation.kind is unsupported")
    if source_kind == "real_deidentified" and observation_kind != "runtime_ocr":
        raise ParserDatasetError(f"{document_id} real_deidentified observations must use runtime_ocr")
    profile = _require_mapping(observation.get("profile"), f"{document_id}.observation.profile")
    if observation_kind == "runtime_ocr":
        from .observation_profile import runtime_observation_profile

        normalized_profile = runtime_observation_profile(profile, expected_image_sha256=image_sha)
        if dict(profile) != normalized_profile:
            raise ParserDatasetError(f"{document_id}.observation.profile must use canonical runtime OCR fields")
        profile = normalized_profile
    raw_nodes = observation.get("nodes")
    if not isinstance(raw_nodes, list):
        raise ParserDatasetError(f"{document_id}.observation.nodes must be a list")
    nodes = [
        _normalize_node(raw, document_id, index, width=width, height=height)
        for index, raw in enumerate(raw_nodes)
    ]
    node_ids = [node["node_id"] for node in nodes]
    if len(set(node_ids)) != len(node_ids):
        raise ParserDatasetError(f"{document_id}.observation.node_id values must be unique")
    node_by_id = {node["node_id"]: node for node in nodes}

    raw_relations = data.get("relations")
    if not isinstance(raw_relations, list):
        raise ParserDatasetError(f"{document_id}.relations must be a list")
    relations: list[dict[str, str]] = []
    relation_keys: set[tuple[str, str]] = set()
    for index, raw in enumerate(raw_relations):
        relation = _require_mapping(raw, f"{document_id}.relations[{index}]")
        _reject_unknown(relation, _RELATION_FIELDS, f"{document_id}.relations[{index}]")
        product_id = _require_id(relation.get("product_node_id"), f"{document_id}.relations[{index}].product_node_id")
        field_id = _require_id(relation.get("field_node_id"), f"{document_id}.relations[{index}].field_node_id")
        label = str(relation.get("label") or "")
        if label not in _ALLOWED_RELATIONS:
            raise ParserDatasetError(f"{document_id}.relations[{index}].label is unsupported")
        if product_id not in node_by_id or field_id not in node_by_id:
            raise ParserDatasetError(f"{document_id}.relations[{index}] references unknown node")
        if node_by_id[product_id]["label_status"] != "labeled" or node_by_id[field_id]["label_status"] != "labeled":
            raise ParserDatasetError(f"{document_id}.relations[{index}] cannot reference ambiguous/unlabeled nodes")
        if node_by_id[product_id]["semantic_role"] != "product":
            raise ParserDatasetError(f"{document_id}.relations[{index}] product node is not labeled product")
        if node_by_id[field_id]["semantic_role"] not in {"dose", "frequency", "duration", "instruction", "schedule"}:
            raise ParserDatasetError(f"{document_id}.relations[{index}] field node has unsupported role")
        product_group = node_by_id[product_id]["association_group"]
        field_group = node_by_id[field_id]["association_group"]
        if product_group is None or field_group is None:
            raise ParserDatasetError(f"{document_id}.relations[{index}] endpoints require association_group")
        expected_label = "same_medication" if product_group == field_group else "different_medication"
        if label != expected_label:
            raise ParserDatasetError(
                f"{document_id}.relations[{index}] label contradicts endpoint association_group values"
            )
        key = (product_id, field_id)
        if key in relation_keys:
            raise ParserDatasetError(f"{document_id}.relations contains duplicate product/field pair")
        relation_keys.add(key)
        relations.append({"product_node_id": product_id, "field_node_id": field_id, "label": label})

    gold_rows = _normalize_gold_rows(data.get("gold_rows"), f"{document_id}.gold_rows")
    gold_rows_reviewed = data.get("gold_rows_reviewed")
    if not isinstance(gold_rows_reviewed, bool):
        raise ParserDatasetError(f"{document_id}.gold_rows_reviewed must be boolean")
    annotation_status = str(data.get("annotation_status") or "complete")
    if annotation_status not in {"draft", "complete"}:
        raise ParserDatasetError(f"{document_id}.annotation_status must be draft or complete")
    if annotation_status == "complete" and any(node["label_status"] == "unlabeled" for node in nodes):
        raise ParserDatasetError(f"{document_id} complete annotation contains unlabeled nodes")
    if annotation_status == "complete" and gold_rows_reviewed is not True:
        raise ParserDatasetError(f"{document_id} complete annotation requires image gold review")
    if annotation_status == "complete":
        expected_relations = build_relation_labels(nodes)
        relations.sort(key=lambda item: (item["product_node_id"], item["field_node_id"], item["label"]))
        if relations != expected_relations:
            raise ParserDatasetError(f"{document_id} complete annotation relation supervision is incomplete")
        medication_roles = {"product", "product_label", "dose", "frequency", "duration", "instruction", "schedule"}
        labeled_groups = {
            str(node["association_group"])
            for node in nodes
            if node["label_status"] == "labeled"
            and node["semantic_role"] in medication_roles
            and node["association_group"] is not None
            and node["association_group"] != "document"
        }
        gold_group_ids = {row["gold_row_id"] for row in gold_rows}
        missing_gold_groups = sorted(labeled_groups - gold_group_ids)
        if missing_gold_groups:
            raise ParserDatasetError(
                f"{document_id} labeled medication groups are missing from image gold: {', '.join(missing_gold_groups)}"
            )

    return {
        "document_id": document_id,
        "split": split,
        "source_kind": source_kind,
        "image_sha256": image_sha,
        "width": width,
        "height": height,
        "layout_family": layout_family,
        "scenario_tags": _require_tags(data.get("scenario_tags"), f"{document_id}.scenario_tags"),
        "risk_tags": _require_tags(data.get("risk_tags"), f"{document_id}.risk_tags"),
        "privacy": {"contains_patient_data": False, "deidentified": bool(privacy["deidentified"])},
        "provenance": provenance,
        "observation": {"kind": observation_kind, "profile": dict(profile), "nodes": nodes},
        "relations": relations,
        "gold_rows": gold_rows,
        "gold_rows_reviewed": gold_rows_reviewed,
        "annotation_status": annotation_status,
    }


def normalize_parser_documents(documents: Iterable[object]) -> list[dict[str, Any]]:
    normalized = [_normalize_document(document) for document in documents]
    ids = [document["document_id"] for document in normalized]
    if len(set(ids)) != len(ids):
        raise ParserDatasetError("document_id values must be unique")
    try:
        validate_parser_document_set(normalized)
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc
    return normalized


def _atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(content)
    os.replace(temporary, path)


@contextmanager
def _exclusive_output_lock(root: Path):
    lock_path = root / _LOCK_FILE
    stream = lock_path.open("a+")
    try:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise ParserDatasetError(f"parser dataset build is already active in {root}") from exc
        yield
    finally:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        finally:
            stream.close()


def _read_state(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ParserDatasetError("parser dataset output state is invalid JSON") from exc
    if not isinstance(value, Mapping):
        raise ParserDatasetError("parser dataset output state must be an object")
    return value


def write_parser_dataset(
    output_dir: str | Path,
    *,
    dataset_id: str,
    documents: Iterable[object],
    metadata: Mapping[str, Any] | None = None,
) -> Path:
    root = Path(output_dir).resolve()
    root.mkdir(parents=True, exist_ok=True)
    normalized_id = _require_id(dataset_id, "dataset_id")
    normalized = normalize_parser_documents(documents)
    samples_bytes = b"".join(_canonical_json(document) + b"\n" for document in normalized)
    samples_path = root / "samples.jsonl"
    samples_sha = _sha256_bytes(samples_bytes)
    try:
        normalized_metadata = normalize_parser_metadata(metadata or {})
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc
    metadata_sha = _sha256_bytes(_canonical_json(normalized_metadata))
    profile = {
        "schema_version": 1,
        "dataset_id": normalized_id,
        "samples_sha256": samples_sha,
        "metadata_sha256": metadata_sha,
    }
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "dataset_id": normalized_id,
        "task": TASK,
        "source_policy": SOURCE_POLICY,
        "samples_file": "samples.jsonl",
        "samples_sha256": samples_sha,
        "metadata_sha256": metadata_sha,
        "document_count": len(normalized),
        "metadata": normalized_metadata,
    }
    manifest_path = root / "manifest.json"
    state_path = root / _STATE_FILE
    with _exclusive_output_lock(root):
        if state_path.is_file():
            state = _read_state(state_path)
            if state.get("profile") != profile:
                raise ParserDatasetError("parser dataset output profile differs from requested dataset")
            if state.get("status") == "completed":
                dataset = load_parser_dataset(manifest_path, allow_draft=True)
                if dataset.dataset_id != normalized_id or _sha256_file(dataset.root / "samples.jsonl") != samples_sha:
                    raise ParserDatasetError("completed parser dataset state disagrees with persisted dataset")
                if _sha256_bytes(_canonical_json(dataset.metadata)) != profile["metadata_sha256"]:
                    raise ParserDatasetError("completed parser dataset metadata disagrees with persisted state")
                return manifest_path
            if state.get("status") != "running":
                raise ParserDatasetError("parser dataset output state has unsupported status")
        else:
            unexpected = [
                path.name for path in root.iterdir()
                if path.name != _LOCK_FILE
            ]
            if unexpected:
                raise ParserDatasetError("parser dataset output is non-empty without authoritative state")
        _atomic_write(
            state_path,
            json.dumps({"schema_version": 1, "status": "running", "profile": profile}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        _atomic_write(samples_path, samples_bytes)
        _atomic_write(manifest_path, json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n")
        _atomic_write(
            state_path,
            json.dumps({"schema_version": 1, "status": "completed", "profile": profile}, ensure_ascii=False, indent=2, sort_keys=True).encode("utf-8") + b"\n",
        )
        return manifest_path


def load_parser_dataset(manifest_path: str | Path, *, allow_draft: bool = False) -> ParserDataset:
    path = Path(manifest_path).resolve()
    if not path.is_file():
        raise ParserDatasetError(f"parser dataset manifest does not exist: {path}")
    try:
        manifest = _require_mapping(json.loads(path.read_text(encoding="utf-8")), "parser dataset manifest")
    except json.JSONDecodeError as exc:
        raise ParserDatasetError(f"invalid parser dataset manifest JSON: {exc}") from exc
    _reject_unknown(manifest, _MANIFEST_FIELDS, "parser dataset manifest")
    if set(manifest) != _MANIFEST_FIELDS:
        missing = sorted(_MANIFEST_FIELDS - set(manifest))
        raise ParserDatasetError(f"parser dataset manifest is missing fields: {', '.join(missing)}")
    if manifest.get("schema_version") != SCHEMA_VERSION:
        raise ParserDatasetError("unsupported parser dataset schema_version")
    dataset_id = _require_id(manifest.get("dataset_id"), "dataset_id")
    if manifest.get("task") != TASK:
        raise ParserDatasetError(f"task must be {TASK}")
    if manifest.get("source_policy") != SOURCE_POLICY:
        raise ParserDatasetError(f"source_policy must be {SOURCE_POLICY}")
    samples_file = str(manifest.get("samples_file") or "")
    relative = Path(samples_file)
    if relative.is_absolute() or ".." in relative.parts:
        raise ParserDatasetError("samples_file must stay inside the dataset root")
    samples_path = (path.parent / relative).resolve()
    if path.parent != samples_path.parent and path.parent not in samples_path.parents:
        raise ParserDatasetError("samples_file escapes the dataset root")
    if not samples_path.is_file():
        raise ParserDatasetError(f"parser dataset samples_file does not exist: {samples_file}")
    expected_samples_sha = str(manifest.get("samples_sha256") or "")
    if not _SHA256_RE.fullmatch(expected_samples_sha):
        raise ParserDatasetError("samples_sha256 must be lowercase SHA-256")
    actual_samples_sha = _sha256_file(samples_path)
    if actual_samples_sha != expected_samples_sha:
        raise ParserDatasetError("parser dataset samples SHA-256 mismatch")
    metadata = _require_mapping(manifest.get("metadata"), "metadata")
    expected_metadata_sha = str(manifest.get("metadata_sha256") or "")
    if not _SHA256_RE.fullmatch(expected_metadata_sha):
        raise ParserDatasetError("metadata_sha256 must be lowercase SHA-256")
    actual_metadata_sha = _sha256_bytes(_canonical_json(metadata))
    if actual_metadata_sha != expected_metadata_sha:
        raise ParserDatasetError("parser dataset metadata SHA-256 mismatch")
    try:
        normalized_metadata = normalize_parser_metadata(metadata)
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc
    if dict(metadata) != normalized_metadata:
        raise ParserDatasetError("parser dataset metadata must use canonical fields")
    documents: list[dict[str, Any]] = []
    for line_number, raw_line in enumerate(samples_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not raw_line.strip():
            continue
        try:
            documents.append(_normalize_document(json.loads(raw_line)))
        except json.JSONDecodeError as exc:
            raise ParserDatasetError(f"samples line {line_number} is invalid JSON") from exc
    if len(documents) != manifest.get("document_count"):
        raise ParserDatasetError("document_count does not match samples_file")
    if len({document["document_id"] for document in documents}) != len(documents):
        raise ParserDatasetError("document_id values must be unique")
    try:
        validate_parser_document_set(documents)
    except ValueError as exc:
        raise ParserDatasetError(str(exc)) from exc
    if not allow_draft and any(document["annotation_status"] != "complete" for document in documents):
        raise ParserDatasetError("parser dataset contains draft annotations")
    fingerprint = _sha256_bytes(
        _canonical_json({
            "schema_version": SCHEMA_VERSION,
            "dataset_id": dataset_id,
            "samples_sha256": actual_samples_sha,
            "metadata": normalized_metadata,
        })
    )
    return ParserDataset(
        root=path.parent,
        manifest_path=path,
        dataset_id=dataset_id,
        metadata=normalized_metadata,
        documents=tuple(documents),
        fingerprint=fingerprint,
    )


__all__ = [
    "ParserDataset",
    "ParserDatasetError",
    "SOURCE_POLICY",
    "TASK",
    "load_parser_dataset",
    "normalize_parser_documents",
    "write_parser_dataset",
]
