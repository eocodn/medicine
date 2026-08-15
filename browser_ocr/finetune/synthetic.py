from __future__ import annotations

import fcntl
import hashlib
import json
import math
import os
import random
import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from PIL import Image, ImageDraw, ImageFont

from .dataset import DatasetError, dataset_stats, load_dataset


class GenerationError(DatasetError):
    pass


_GENERATOR_VERSION = "4"
_LICENSE_ID = "data-go-kr-unrestricted-use"
_MAX_PRODUCT_LENGTH = 18
_SOURCE_DATASET_KEY = "mfds_permit:products"


@dataclass(frozen=True)
class Product:
    item_seq: str
    product_name: str
    ingredient_text: str | None
    dosage_form: str | None


@dataclass(frozen=True)
class SourceSnapshot:
    dataset_key: str
    source_family: str
    source_locator: str
    sha256: str


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _clean_text(value: object, *, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    text = unicodedata.normalize("NFC", unicodedata.normalize("NFKC", value)).strip()
    if not text or len(text) > limit or any(char in text for char in "\t\r\n\x00"):
        return None
    return text


def load_product_lexicon(canonical_db: str | Path) -> tuple[tuple[Product, ...], SourceSnapshot]:
    path = Path(canonical_db).resolve()
    if not path.is_file():
        raise GenerationError(f"canonical database does not exist: {path}")
    try:
        con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
        con.row_factory = sqlite3.Row
        source_row = con.execute(
            "select dataset_key, source_family, source_locator, sha256 "
            "from source_snapshots where dataset_key = ?",
            (_SOURCE_DATASET_KEY,),
        ).fetchone()
        if source_row is None:
            raise GenerationError(f"canonical database is missing source snapshot {_SOURCE_DATASET_KEY}")
        products: list[Product] = []
        for row in con.execute(
            "select item_seq, product_name, ingredient_text, dosage_form "
            "from products where source_dataset_key = ? "
            "and permit_status not in ('canceled', 'withdrawn', 'expired', 'business_closed') "
            "order by item_seq",
            (_SOURCE_DATASET_KEY,),
        ):
            product_name = _clean_text(row["product_name"], limit=_MAX_PRODUCT_LENGTH)
            if product_name is None:
                continue
            item_seq = _clean_text(row["item_seq"], limit=64)
            if item_seq is None:
                continue
            products.append(Product(
                item_seq=item_seq,
                product_name=product_name,
                ingredient_text=_clean_text(row["ingredient_text"], limit=40),
                dosage_form=_clean_text(row["dosage_form"], limit=24),
            ))
    except sqlite3.Error as exc:
        raise GenerationError(f"could not read canonical database: {exc}") from exc
    finally:
        if "con" in locals():
            con.close()
    if not products:
        raise GenerationError("canonical database has no eligible active medicine products")
    snapshot = SourceSnapshot(
        dataset_key=str(source_row["dataset_key"]),
        source_family=str(source_row["source_family"]),
        source_locator=str(source_row["source_locator"]),
        sha256=str(source_row["sha256"]),
    )
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot.sha256):
        raise GenerationError("canonical source snapshot has an invalid SHA-256")
    return tuple(products), snapshot


def _index_rng(seed: int, index: int) -> random.Random:
    material = hashlib.sha256(f"medicine-ocr-synthetic-v3\0content\0{seed}\0{index}".encode()).digest()
    return random.Random(int.from_bytes(material[:8], "big"))


def _assignment_bucket(*, seed: int, index: int, namespace: str, buckets: int) -> int:
    material = hashlib.sha256(
        f"medicine-ocr-synthetic-v3\0assignment\0{namespace}\0{seed}\0{index}".encode()
    ).digest()
    return int.from_bytes(material[:8], "big") % buckets


def synthetic_assignments(*, seed: int, index: int) -> dict[str, object]:
    """Return deterministic, independently keyed experimental assignments.

    The namespace separation is intentional: holdout family, semantic case, document
    type, and capture artifacts must not share an index-period relationship. Otherwise
    a held-out layout/source can accidentally become a proxy for a particular label or
    image-degradation stratum and invalidate the generalization experiment.
    """
    capture_tags = [
        tag
        for tag, denominator in (
            ("small_print", 5),
            ("low_contrast", 7),
            ("rotation", 11),
            ("plastic_reflection", 13),
        )
        if _assignment_bucket(
            seed=seed,
            index=index,
            namespace=f"capture:{tag}",
            buckets=denominator,
        ) == 0
    ]
    text_case_offset = _assignment_bucket(seed=seed, index=0, namespace="text-case-offset", buckets=14)
    return {
        "text_case": (index + text_case_offset) % 14,
        "document_type": (
            "prescription"
            if _assignment_bucket(seed=seed, index=index, namespace="document-type", buckets=2) == 0
            else "medication_bag"
        ),
        "layout_family": f"layout-{_assignment_bucket(seed=seed, index=index, namespace='layout', buckets=24):02d}",
        "source_family": f"synthetic-source-{_assignment_bucket(seed=seed, index=index, namespace='source', buckets=17):02d}",
        "capture_tags": capture_tags,
    }


def _slug_hash(value: str, length: int = 12) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()[:length]


def _text_case(case: int, product: Product, rng: random.Random) -> tuple[str, list[str], list[str]]:
    if case == 0:
        return product.product_name, ["product"], []
    if case == 1:
        strength = rng.choice(["5mg", "10mg", "100mg", "500mg", "0.5mg", "20mL"])
        return f"{product.product_name} {strength}", ["product", "strength"], ["exact_numeric", "mixed_script"]
    if case == 2:
        return f"{product.product_name} 1정", ["product", "dose"], ["exact_numeric"]
    if case == 3:
        return rng.choice(["1일 3회", "하루 2회", "매 8시간"]), ["frequency"], ["exact_numeric"]
    if case == 4:
        return rng.choice(["3일분", "5일간", "7일분", "14일간"]), ["duration"], ["exact_numeric"]
    if case == 5:
        return rng.choice(["아침 식후 30분", "저녁 식후 30분", "취침 전 1회", "08:00 복용"]), ["schedule"], ["exact_numeric"]
    if case == 6:
        return rng.choice(["진료시간 09:00~18:00", "점심시간 12:30~13:30"]), ["clinic_hours"], ["hard_negative", "exact_numeric", "ambiguous_range"]
    if case == 7:
        return f"문의 02-{rng.randrange(1000, 10000):04d}-{rng.randrange(1000, 10000):04d}", ["phone"], ["hard_negative", "exact_numeric"]
    if case == 8:
        year = rng.choice([2025, 2026, 2027])
        month = rng.randrange(1, 13)
        day = rng.randrange(1, 29)
        return f"조제일 {year:04d}-{month:02d}-{day:02d}", ["date"], ["hard_negative", "exact_numeric"]
    if case == 9:
        return f"처방번호 RX-{rng.randrange(100000, 1000000)}", ["identifier"], ["hard_negative", "exact_numeric", "mixed_script"]
    if case == 10:
        return f"{product.product_name} TAB", ["product"], ["mixed_script"]
    if case == 11:
        return "1/2정 복용", ["dose"], ["exact_numeric", "fraction"]
    if case == 12:
        return "0.5정 복용", ["dose"], ["exact_numeric", "decimal"]
    return "1~2정 복용", ["dose"], ["exact_numeric", "ambiguous_range"]


def _render_line(
    text: str,
    target: Path,
    *,
    font_path: Path,
    capture_tags: list[str],
    rng: random.Random,
) -> None:
    font_size = 20 if "small_print" in capture_tags else rng.randrange(24, 35)
    font = ImageFont.truetype(str(font_path), font_size)
    probe = Image.new("L", (8, 8), 255)
    draw = ImageDraw.Draw(probe)
    left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
    text_width = max(1, right - left)
    text_height = max(1, bottom - top)
    margin_x = rng.randrange(10, 19)
    margin_y = rng.randrange(8, 15)
    width = text_width + margin_x * 2
    height = text_height + margin_y * 2
    if width > 1280:
        scale = 1280 / width
        font_size = max(16, math.floor(font_size * scale))
        font = ImageFont.truetype(str(font_path), font_size)
        left, top, right, bottom = draw.textbbox((0, 0), text, font=font)
        text_width = max(1, right - left)
        text_height = max(1, bottom - top)
        width = text_width + margin_x * 2
        height = text_height + margin_y * 2
    background = rng.randrange(244, 256)
    foreground = rng.randrange(20, 61)
    if "low_contrast" in capture_tags:
        background = rng.randrange(235, 247)
        foreground = rng.randrange(105, 145)
    image = Image.new("L", (max(96, width), max(40, height)), background)
    draw = ImageDraw.Draw(image)
    draw.text((margin_x - left, margin_y - top), text, fill=foreground, font=font)
    if "plastic_reflection" in capture_tags:
        stripe_x = max(1, image.width // 3)
        draw.polygon(
            [(stripe_x, 0), (stripe_x + 10, 0), (stripe_x + 40, image.height), (stripe_x + 28, image.height)],
            fill=min(252, background + 7),
        )
    if "rotation" in capture_tags:
        angle = rng.choice([-2.0, -1.25, 1.25, 2.0])
        image = image.rotate(angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor=background)
    target.parent.mkdir(parents=True, exist_ok=True)
    image.save(target, format="PNG", optimize=False, compress_level=6)


def _config(*, canonical_sha: str, font_sha: str, count: int, seed: int) -> dict:
    return {
        "schema_version": 1,
        "generator_version": _GENERATOR_VERSION,
        "canonical_db_sha256": canonical_sha,
        "font_sha256": font_sha,
        "count": count,
        "seed": seed,
    }


def _read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise GenerationError(f"could not read generation metadata {path.name}: {exc}") from exc
    if not isinstance(value, dict):
        raise GenerationError(f"generation metadata {path.name} must be an object")
    return value


def _write_json_atomic(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def _checkpoint(path: Path, config: dict, completed: int) -> None:
    _write_json_atomic(path, {"schema_version": 1, "config": config, "completed": completed})


def _validate_partial(output: Path, completed: int) -> None:
    partial = output / "samples.partial.jsonl"
    if completed == 0:
        if partial.exists() and partial.stat().st_size:
            raise GenerationError("partial sample file exists but checkpoint says zero completed")
        return
    if not partial.is_file():
        raise GenerationError("checkpoint exists but partial sample file is missing")
    with partial.open("r", encoding="utf-8") as handle:
        line_count = sum(1 for _ in handle)
    if line_count != completed:
        raise GenerationError(f"checkpoint/sample mismatch: completed={completed}, lines={line_count}")
    first = output / "images" / "sample-000000.png"
    last = output / "images" / f"sample-{completed - 1:06d}.png"
    if not first.is_file() or not last.is_file():
        raise GenerationError("checkpoint exists but generated image files are incomplete")


def generate_dataset(
    canonical_db: str | Path,
    output_dir: str | Path,
    *,
    count: int,
    seed: int,
    font_path: str | Path,
    progress: Callable[[int, int], None] | None = None,
    progress_interval: int = 100,
) -> dict:
    if not isinstance(count, int) or count <= 0:
        raise GenerationError("count must be a positive integer")
    if not isinstance(seed, int):
        raise GenerationError("seed must be an integer")
    if not isinstance(progress_interval, int) or progress_interval <= 0:
        raise GenerationError("progress_interval must be a positive integer")
    canonical = Path(canonical_db).resolve()
    font = Path(font_path).resolve()
    if not font.is_file():
        raise GenerationError(f"font file does not exist: {font}")
    canonical_sha = _sha256_file(canonical) if canonical.is_file() else ""
    if not canonical_sha:
        raise GenerationError(f"canonical database does not exist: {canonical}")
    font_sha = _sha256_file(font)
    config = _config(canonical_sha=canonical_sha, font_sha=font_sha, count=count, seed=seed)
    products, source = load_product_lexicon(canonical)

    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=True)
    lock_path = output / ".generation.lock"
    with lock_path.open("a+b") as lock:
        try:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GenerationError(f"generation is already active for {output}") from exc

        report_path = output / "generation-report.json"
        state_path = output / ".generation-state.json"
        if report_path.is_file() and not state_path.exists():
            report = _read_json(report_path)
            if report.get("config") != config:
                raise GenerationError("completed dataset configuration does not match requested configuration")
            dataset = load_dataset(output / "manifest.json")
            if dataset.fingerprint != report.get("dataset_fingerprint"):
                raise GenerationError("completed dataset fingerprint no longer matches generation report")
            return report

        if state_path.exists():
            state = _read_json(state_path)
            if state.get("config") != config:
                raise GenerationError("partial dataset configuration does not match requested configuration")
            completed = state.get("completed")
            if not isinstance(completed, int) or completed < 0 or completed > count:
                raise GenerationError("generation checkpoint has invalid completed count")
        else:
            unexpected = [name for name in ("manifest.json", "samples.jsonl", "samples.partial.jsonl", "generation-report.json") if (output / name).exists()]
            if unexpected:
                raise GenerationError(f"output directory contains generation artifacts without checkpoint: {', '.join(unexpected)}")
            completed = 0
            _checkpoint(state_path, config, completed)
        _validate_partial(output, completed)

        partial_path = output / "samples.partial.jsonl"
        mode = "a" if completed else "w"
        with partial_path.open(mode, encoding="utf-8", newline="\n") as samples_file:
            for index in range(completed, count):
                rng = _index_rng(seed, index)
                assignment = synthetic_assignments(seed=seed, index=index)
                product = products[rng.randrange(len(products))]
                text, semantic_tags, risk_tags = _text_case(int(assignment["text_case"]), product, rng)
                capture_tags = list(assignment["capture_tags"])
                risk_tags = list(dict.fromkeys([*risk_tags, *capture_tags]))
                image_rel = f"images/sample-{index:06d}.png"
                image_path = output / image_rel
                _render_line(text, image_path, font_path=font, capture_tags=capture_tags, rng=rng)
                image_sha = _sha256_file(image_path)
                sample = {
                    "id": f"sample-{index:06d}",
                    "image": image_rel,
                    "image_sha256": image_sha,
                    "text": text,
                    "origin": "synthetic",
                    "document_type": assignment["document_type"],
                    "document_id": f"doc-{index:06d}",
                    "groups": {
                        "layout_family": assignment["layout_family"],
                        "source_family": assignment["source_family"],
                        "drug_family": f"drug-{_slug_hash(product.item_seq)}",
                    },
                    "semantic_tags": semantic_tags,
                    "risk_tags": risk_tags,
                    "privacy": {"contains_patient_data": False, "deidentified": True},
                    "provenance": {
                        "source_id": source.dataset_key,
                        "license_id": _LICENSE_ID,
                        "generator_version": _GENERATOR_VERSION,
                        "source_revision": source.sha256,
                    },
                }
                samples_file.write(_canonical_json(sample) + "\n")
                samples_file.flush()
                completed = index + 1
                _checkpoint(state_path, config, completed)
                if progress and (completed == count or completed % progress_interval == 0):
                    progress(completed, count)

        os.replace(partial_path, output / "samples.jsonl")
        manifest = {
            "schema_version": 1,
            "dataset_id": f"medicine-synth-rec-v1-s{seed}-n{count}",
            "task": "text_recognition",
            "patient_data_policy": "forbid",
            "samples_file": "samples.jsonl",
            "description": "Deterministic medicine-domain synthetic OCR recognition crops",
            "metadata": {
                "generator_version": _GENERATOR_VERSION,
                "canonical_db_sha256": canonical_sha,
                "font_sha256": font_sha,
                "source_dataset_key": source.dataset_key,
                "source_family": source.source_family,
                "source_locator": source.source_locator,
                "source_revision": source.sha256,
            },
        }
        _write_json_atomic(output / "manifest.json", manifest)
        dataset = load_dataset(output / "manifest.json")
        stats = dataset_stats(dataset)
        report = {
            "schema_version": 1,
            "config": config,
            "dataset_id": dataset.manifest["dataset_id"],
            "dataset_fingerprint": dataset.fingerprint,
            "sample_count": len(dataset.samples),
            "canonical_db_sha256": canonical_sha,
            "source_dataset_key": source.dataset_key,
            "source_revision": source.sha256,
            "stats": stats,
        }
        _write_json_atomic(report_path, report)
        state_path.unlink()
        return report
