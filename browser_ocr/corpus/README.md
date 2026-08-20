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

Generator v5 binds the drug holdout to the **actual selected recognizer training exposure**, not merely to a new random partition. A historical exposure artifact is derived from the exact source-train split used by the selected checkpoint. Every family observed by that checkpoint is forced into the drug `train` pool; every historically unseen family is assigned only to `val` or `test`, balanced deterministically by product count. The document split remains 80/10/10 independently. This makes `drug_name_exposure=seen` a real family-level statement about the selected checkpoint and makes validation/test families truly unseen to it. Validation fails closed on exact-name/family leakage.

The drug partition seed is intentionally independent from the document-generation seed. Keep `--drug-split-seed` and `--historical-drug-exposure` fixed across training and evaluation corpora so the unseen-family val/test assignment remains stable while `--seed` changes document layouts, captures, and sample identities.

The root `drug_name_policy` binds the corpus to the canonical database SHA-256, MFDS source-snapshot SHA-256, assignment seed, selected checkpoint SHA-256, historical source dataset fingerprint, historical train-split SHA-256, historical vocabulary/family hashes, pool counts, and per-pool content hashes. Product typography is fitted to the declared layout slot so longer canonical names remain readable without colliding with adjacent regimen columns.

## Parser structure holdout

Generator v5 revision 8 adds parser-specific structural recipes before rendering. Training documents cover complete and partial medication rows, product-only rows, missing/partial headers, numeric cells, regimen-shaped distractors, and association-spacing stress. Recipe assignment advances by the document's ordinal within its actual split rather than raw sample index, preventing the 10-way document split from permanently masking train recipes. Validation/test use disjoint held-out recipe names, including short generic headers, fraction doses, no-header combinations, and header-only negatives. Product-only/header-only strata remove medication-associated instruction and schedule regions as well as numeric regimen values. The selected `parser_structure_variant` is stored on every document and the generator fingerprint binds the structure-recipe revision. This changes the source document itself rather than post-processing parser labels, so detector/recognizer/runtime-parser experiments see the same rendered structure.

## Materialized views

`materialize` writes four views under one output root:

- `detection/`: full-page references plus region polygons, with train/val/test JSONL files and PaddleOCR detection-training labels under `detection/paddle/`.
- `recognition/`: perspective-normalized crops cut from the already-degraded full-page raster using GT region polygons. It includes the existing fine-tune dataset manifest plus a ready-to-use PaddleOCR `train.txt`, `val.txt`, and `test.txt` export.
- `parsing/`: authoritative document truth plus oracle manifests and strict learned-parser datasets. Materialization emits oracle and deterministic synthetic-OCR train/val/test manifests whose observed boxes may be dropped, split, merged, jittered or corrupted before geometry-based labeling. Relations include `same_medication` positives and cross-medication hard negatives.
- `e2e/`: full-page images plus expected structured rows and critical region ids, preserving the same document split. The existing `browser_ocr.finetune.full_document_evaluation` evaluator can score detector→recognizer→parser result directories against the same canonical manifest.

Recognition crops deliberately come from the final degraded raster rather than from pristine source text. Motion blur, JPEG compression, perspective, glare, printer degradation, and other document-level effects therefore reach recognizer training/evaluation exactly as they appear in the E2E image. Crop checkpoints bind both source-image bytes and a rolling SHA-256 chain of completed crop outputs, so a same-path source change or mutated completed crop is rejected instead of being silently reused.

Recognition metadata also records the fixed `severe-motion-downscale-jpeg-v1` OOD signature used by recognizer research. A document is tagged `degradation-hard-ood` only when it is hard difficulty and simultaneously has motion blur radius >= 3.5, downscale factor <= 0.65, JPEG quality <= 60, and all three corresponding augmentation components. The policy object is stored in the recognition manifest so training export filters and fixed evaluation slices use the same numeric definition. Critical medication crops additionally carry `critical-medication`.

## Agent Control CLI

Use the Compose service so generation and local validation stay inside the pinned Docker environment:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_corpus \
  docker compose run --rm ocr-corpus generate \
  --output /workspace/browser_ocr/finetune/work/unified-360 \
  --canonical-db /data/canonical.sqlite \
  --historical-drug-exposure /workspace/browser_ocr/finetune/results/selected-100k-training-drug-exposure.json \
  --drug-split-seed 161 \
  --count 360 --seed 153 --materialize --json
```

Mount the authoritative database read-only into the container, for example with `-v /absolute/path/canonical.sqlite:/data/canonical.sqlite:ro`. There is no production fallback to the former small product-name catalog.

The historical exposure artifact is reproducible from the selected recognizer's authoritative training dataset and split:

```sh
docker compose run --rm ocr-corpus historical-exposure \
  --manifest /workspace/path/to/historical/manifest.json \
  --split /workspace/path/to/historical/paddle-source-stable-v1/split.json \
  --checkpoint-sha256 <selected-checkpoint-sha256> \
  --output /workspace/browser_ocr/finetune/results/selected-100k-training-drug-exposure.json \
  --json
```

The builder reads product-tagged samples from the **train membership only**, removes only the known standalone-generator product decorations, records the source dataset fingerprint and split-file SHA-256, and writes a content-hashed family exposure set. Reusing an output path with different authoritative content fails rather than overwriting it.

The other commands are:

```sh
docker compose run --rm ocr-corpus validate --corpus /workspace/path/manifest.json --json
docker compose run --rm ocr-corpus materialize --corpus /workspace/path/manifest.json --output /workspace/path/views --json
docker compose run --rm ocr-corpus audit --corpus /workspace/path/manifest.json --json
```

Generation and materialization use exclusive locks, atomic state files, content hashes, resumable checkpoints, and explicit progress on stderr. Materializer v12 uses a kernel advisory lock held by a helper process, so a dead process cannot strand an existence-based lock file; the lock file itself may persist harmlessly. Completed reuse revalidates the report SHA, a fixed set of stage/parser artifact hashes, recognition image hashes through the recognition dataset core, and all emitted parser datasets before returning success. Reusing an output directory with a different generator/materializer profile fails rather than mixing artifacts.

## Training and evaluation boundaries

The corpus is synthetic-only. Stage views are suitable for controlled training, regression testing, ablation, and bottleneck attribution, but they are not evidence of real-photo generalization. Release decisions still require de-identified real-photo holdouts and representative Android runtime measurements.
