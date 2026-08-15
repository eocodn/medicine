# Unified OCR synthetic corpus

This package owns the canonical procedural full-document corpus for the OCR research pipeline:

`document image -> detector -> recognizer -> parser/KIE -> structured medication rows`

A document is generated once. Detector, recognizer, parser, and end-to-end experiments then consume deterministic views derived from that same document identity, degradation, labels, and train/validation/test split. This prevents stage-specific synthetic generators from silently testing different source distributions.

## Canonical document contract

Schema v3 keeps the existing full-page camera-like raster and authoritative text geometry, and adds a document-level split and task declaration. Every region carries text, quadrilateral geometry, a tighter natural-text polygon, semantic role, association group, critical flag, and region class. Medication-bag labels that participate in product identity use the explicit `product_label` role.

The split is assigned at the document level before any stage view is created. All recognition crops and parser nodes derived from a document inherit that exact split, so crops from one document cannot leak across train/validation/test stages.

## Materialized views

`materialize` writes four views under one output root:

- `detection/`: full-page references plus region polygons, with train/val/test JSONL files and PaddleOCR detection-training labels under `detection/paddle/`.
- `recognition/`: perspective-normalized crops cut from the already-degraded full-page raster using GT region polygons. It includes the existing fine-tune dataset manifest plus a ready-to-use PaddleOCR `train.txt`, `val.txt`, and `test.txt` export.
- `parsing/`: OCR nodes with semantic roles and association groups, positive `same_medication` edges, expected structured rows, and parser-compatible oracle manifests for all/train/val/test.
- `e2e/`: full-page images plus expected structured rows and critical region ids, preserving the same document split. The existing `browser_ocr.finetune.full_document_evaluation` evaluator can score detector→recognizer→parser result directories against the same canonical manifest.

Recognition crops deliberately come from the final degraded raster rather than from pristine source text. Motion blur, JPEG compression, perspective, glare, printer degradation, and other document-level effects therefore reach recognizer training/evaluation exactly as they appear in the E2E image.

## Agent Control CLI

Use the Compose service so generation and local validation stay inside the pinned Docker environment:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_corpus \
  docker compose run --rm ocr-corpus generate \
  --output /workspace/browser_ocr/finetune/work/unified-360 \
  --count 360 --seed 153 --materialize --json
```

The other commands are:

```sh
docker compose run --rm ocr-corpus validate --corpus /workspace/path/manifest.json --json
docker compose run --rm ocr-corpus materialize --corpus /workspace/path/manifest.json --output /workspace/path/views --json
docker compose run --rm ocr-corpus audit --corpus /workspace/path/manifest.json --json
```

Generation and materialization use exclusive locks, atomic state files, content hashes, resumable checkpoints, and explicit progress on stderr. Reusing an output directory with a different generator/materializer profile fails rather than mixing artifacts.

## Training and evaluation boundaries

The corpus is synthetic-only. Stage views are suitable for controlled training, regression testing, ablation, and bottleneck attribution, but they are not evidence of real-photo generalization. Release decisions still require de-identified real-photo holdouts and representative Android runtime measurements.
