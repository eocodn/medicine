# Unified OCR synthetic corpus

This package owns the canonical procedural full-document corpus for the OCR research pipeline:

`document image -> detector -> recognizer -> parser/KIE -> structured medication rows`

A document is generated once. Detector, recognizer, parser, and end-to-end experiments then consume deterministic views derived from that same document identity, degradation, labels, and train/validation/test split. This prevents stage-specific synthetic generators from silently testing different source distributions.

## Canonical document contract

Schema v3 keeps the existing full-page camera-like raster and authoritative text geometry, and adds a document-level split and task declaration. Every region carries text, quadrilateral geometry, a tighter natural-text polygon, semantic role, association group, critical flag, and region class. Medication-bag labels that participate in product identity use the explicit `product_label` role.

The split is assigned at the document level before any stage view is created. All recognition crops and parser nodes derived from a document inherit that exact split, so crops from one document cannot leak across train/validation/test stages.

## Generator v4 augmentation

Generator v4 keeps the schema-v3 stage contract but replaces mutually exclusive camera-failure presets with composable augmentation. The six capture profiles remain as balanced anchor strata, while each document also receives a `clean`, `medium`, or `hard` difficulty and an explicit `capture.augmentation_components` list. Medium and hard examples can combine perspective, defocus, motion blur, JPEG compression, contrast/exposure changes, glare, shadow, downscale→upsample loss, deterministic sensor-like Gaussian noise, white-balance shifts, partial crop, and foreground clutter in the same raster.

The 36-sample cycle is balanced so every capture anchor contains clean/medium/hard examples and every layout family sees all three difficulties across capture anchors. Hard samples always contain several simultaneous degradations rather than one extreme effect. Severity parameters are recorded in the manifest and bounded by the corpus contract, so a failed OCR case can be attributed to the actual transformation vector instead of only to a coarse profile name.

Layout generation is also varied within each family. Medication row/block counts, row spacing, table-column positions, medication font sizes, and selected instruction wrapping change deterministically from sample to sample. This makes parser/KIE training see real structural variation while keeping every text region and medication association explicitly annotated.

## Generator v5 drug-name holdout

Generator v5 keeps the v4 image-degradation behavior and replaces the small built-in product-name list with an explicit canonical MFDS product-name source. Generation requires a canonical SQLite database. Eligible active names are normalized and deduplicated, then close dosage-form/strength variants are grouped into deterministic drug families before any document is generated.

Families are assigned to train/validation/test drug pools with an 80/10/10 deterministic hash split. Every document samples product names only from the pool matching its parent-document split. Product regions carry `drug_family` and `drug_name_split`; documents carry `drug_name_split` plus `drug_name_exposure` (`seen` for train, `unseen` for validation/test). Validation fails closed if either an exact normalized product name or a drug family is observed in more than one pool.

The drug partition seed is intentionally independent from the document-generation seed. Keep `--drug-split-seed` fixed across training and evaluation corpora so the meaning of seen/unseen medication identity remains stable while `--seed` changes document layouts, captures, and sample identities.

The root `drug_name_policy` binds the corpus to the canonical database SHA-256, MFDS source-snapshot SHA-256, assignment seed, pool counts, family counts, and per-pool content hashes. Product typography is fitted to the declared layout slot so longer canonical names remain readable without colliding with adjacent regimen columns.

## Materialized views

`materialize` writes four views under one output root:

- `detection/`: full-page references plus region polygons, with train/val/test JSONL files and PaddleOCR detection-training labels under `detection/paddle/`.
- `recognition/`: perspective-normalized crops cut from the already-degraded full-page raster using GT region polygons. It includes the existing fine-tune dataset manifest plus a ready-to-use PaddleOCR `train.txt`, `val.txt`, and `test.txt` export.
- `parsing/`: OCR nodes with semantic roles and association groups, positive `same_medication` edges, expected structured rows, and parser-compatible oracle manifests for all/train/val/test.
- `e2e/`: full-page images plus expected structured rows and critical region ids, preserving the same document split. The existing `browser_ocr.finetune.full_document_evaluation` evaluator can score detector→recognizer→parser result directories against the same canonical manifest.

Recognition crops deliberately come from the final degraded raster rather than from pristine source text. Motion blur, JPEG compression, perspective, glare, printer degradation, and other document-level effects therefore reach recognizer training/evaluation exactly as they appear in the E2E image.

Recognition metadata also records the fixed `severe-motion-downscale-jpeg-v1` OOD signature used by recognizer research. A document is tagged `degradation-hard-ood` only when it is hard difficulty and simultaneously has motion blur radius >= 3.5, downscale factor <= 0.65, JPEG quality <= 60, and all three corresponding augmentation components. The policy object is stored in the recognition manifest so training export filters and fixed evaluation slices use the same numeric definition. Critical medication crops additionally carry `critical-medication`.

## Agent Control CLI

Use the Compose service so generation and local validation stay inside the pinned Docker environment:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_corpus \
  docker compose run --rm ocr-corpus generate \
  --output /workspace/browser_ocr/finetune/work/unified-360 \
  --canonical-db /data/canonical.sqlite \
  --drug-split-seed 161 \
  --count 360 --seed 153 --materialize --json
```

Mount the authoritative database read-only into the container, for example with `-v /absolute/path/canonical.sqlite:/data/canonical.sqlite:ro`. There is no production fallback to the former small product-name catalog.

The other commands are:

```sh
docker compose run --rm ocr-corpus validate --corpus /workspace/path/manifest.json --json
docker compose run --rm ocr-corpus materialize --corpus /workspace/path/manifest.json --output /workspace/path/views --json
docker compose run --rm ocr-corpus audit --corpus /workspace/path/manifest.json --json
```

Generation and materialization use exclusive locks, atomic state files, content hashes, resumable checkpoints, and explicit progress on stderr. Reusing an output directory with a different generator/materializer profile fails rather than mixing artifacts.

## Training and evaluation boundaries

The corpus is synthetic-only. Stage views are suitable for controlled training, regression testing, ablation, and bottleneck attribution, but they are not evidence of real-photo generalization. Release decisions still require de-identified real-photo holdouts and representative Android runtime measurements.
