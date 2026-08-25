# OCR fine-tuning research

This directory is the research boundary for Chronicle #112. It is intentionally independent from the medication product intake path.

## Scope

Phase 1 fine-tunes **text recognition** for Korean prescription and medication-bag crops. It does not yet train the detector or a KIE/document model. The current detector remains a baseline until recognition learning curves show where the remaining error comes from.

The starting model is `korean_PP-OCRv5_mobile_rec`, which supports Korean, English and numeric recognition. PaddleOCR `SimpleDataSet` recognition training consumes one UTF-8 label per line in the form `relative/image/path<TAB>ground truth text`; `export-paddle` emits exactly that contract.

## Data contract

A dataset has `manifest.json` plus a JSONL sample file. Every sample must include:

- a raster crop and SHA-256;
- NFC-normalized ground-truth text with no tab/newline;
- synthetic/public/real-deidentified provenance;
- a pseudonymous `document_id`;
- `layout_family`, `source_family`, and `drug_family` group IDs;
- semantic and risk tags used for coverage/error slicing;
- an explicit privacy declaration. `contains_patient_data=true` is rejected.

Do not put private real-photo corpora in this repository. Keep them outside Git and point the CLI at their manifest. For real samples, identifiers in `document_id`, source groups, and provenance must be pseudonymous and the image must already be de-identified before ingestion.

## Real-photo parser holdout path

The learned-parser data pipeline has a separate full-document path for already de-identified prescription photos. Private images stay outside this repository and are mounted read-only. `ocr-parser-real` reuses the selected `ocr-full-document` detector/crop/recognizer implementation, stores per-document runtime OCR snapshots under the durable `/artifacts/parser/` tree, and emits human-annotation drafts whose immutable source/OCR content is SHA-256-bound separately from editable labels. The batch checkpoint pins one resolved OCR producer identity including exact detector ONNX/config bytes, the actual PaddleOCR inference source tree, the dictionary selected by the recognizer config, the installed Python environment, installed Debian package versions/native shared libraries, and the actual native payload bytes installed by inference-critical Python wheels (PaddlePaddle, ONNX Runtime, OpenCV, NumPy, and NVIDIA runtime packages when present). GPU runs additionally bind the runtime-visible GPU selector and Paddle-reported CUDA/cuDNN/device/capability identity, with NVIDIA driver identity when exposed by the runtime. Matching crash-window artifacts are adopted instead of overwritten. The detector runtime rejects extracted assets that no longer match the pinned archive, and completed full-document state binds `result.json` by SHA-256 before cache reuse. Finalized parser samples retain a lowercase-ASCII pseudonymous source id and the allowlisted real-source license id `private-deidentified`, use strict allowlisted runtime and dataset-manifest metadata, and require explicitly reviewed, type-valid image-level `gold_rows`. Real parser sources are val/test-only; training on real patient-derived images is intentionally not enabled by this contract. See `browser_ocr/document_parsing/README.md` for the source manifest and finalization workflow.

## Why split is graph-based

A random crop split leaks information. Crops from the same photographed document share layout, print quality, camera conditions and often drug names. The splitter therefore joins each `document_id` to the selected holdout family and assigns the resulting connected components as a unit. This guarantees that neither the document nor the chosen family crosses train/validation/test.

Run three separate experiments:

- `layout_family`: unseen layout generalization;
- `source_family`: unseen hospital/pharmacy/source generalization;
- `drug_family`: unseen medication-name family generalization.

## Commands

Use the dedicated Docker service; no host Python installation is required.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune-test

COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  validate --manifest /workspace/path/to/manifest.json --json

COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  stats --manifest /workspace/path/to/manifest.json --json

COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  split --manifest /workspace/path/to/manifest.json \
  --group-by layout_family --seed 112 \
  --output /artifacts/ocr/recognition/layout-split.json --json

COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  export-paddle --manifest /workspace/path/to/manifest.json \
  --split /artifacts/ocr/recognition/layout-split.json \
  --output-dir /artifacts/ocr/recognition/paddle-layout --json
```

The Paddle export contains `train.txt`, `val.txt`, `test.txt`, `split.json`, `export.json`, and `observed-characters.txt`. `data_dir` remains the original dataset root; images are not duplicated.

For learning-curve comparisons across corpus sizes, add `--stable-across-scales`. This assigns each connected holdout component from the selected family IDs rather than its current sample membership, so an existing layout/source/drug family does not move between train, validation, and test merely because more samples were generated. The command fails closed if the deterministic family hash leaves any split empty. Keep the same seed and ratios at every learning-curve point.

## Training runtime

`upstream.json` now records a complete source/model/runtime pin and has `training_enabled=true`. Training is still research-only and does not change the product OCR boundary. The pinned training image uses PaddleOCR v3.7.0, PaddlePaddle GPU 3.2.0 with CUDA 12.6, exact OCR/runtime dependency locks, and the upstream Korean dictionary.

Before any training command, validate that the current container runtime actually exposes a working CUDA device. This is intentionally a runtime check: NVIDIA driver libraries are injected when the Compose service starts and are not available while the Docker image is being built.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train probe --json
```

The bounded smoke profile uses 128 training samples, 64 validation samples, batch size 16, one epoch, and the `drug_family` holdout export. It verifies the exact config/dictionary/weight SHA-256 values, dataset fingerprint and model text contract before invoking PaddleOCR. The run streams Paddle progress, writes a state file and log, and treats a persisted checkpoint as success only after verifying its SHA-256.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train smoke \
  --pretrained-model /artifacts/ocr/checkpoints/upstream/korean_PP-OCRv5_mobile_rec_pretrained.pdparams \
  --manifest /artifacts/ocr/recognition/synth-5k/manifest.json \
  --export-dir /artifacts/ocr/recognition/synth-5k/paddle-drug \
  --run-dir /artifacts/ocr/training/smoke \
  --train-samples 128 --val-samples 64 --batch-size 16 --json
```

Generated datasets, downloaded weights, training logs, checkpoints, and large evaluation artifacts belong under `/artifacts`, which the writable OCR Compose services bind to host `~/dev/.artifacts/medicine` by default. Create that host directory as your normal user before the first run (`mkdir -p ~/dev/.artifacts/medicine`); Compose refuses to auto-create it so Docker cannot leave a root-owned artifact directory. Override the host location with `MEDICINE_ARTIFACTS_DIR=/absolute/path` when needed; the in-container path remains `/artifacts`. Do not generate a dictionary from only the training labels and silently replace the model dictionary; `audit-model` must remain green against the pinned upstream Korean dictionary before training.

## Deterministic synthetic recognition crops

`generate-synthetic` builds patient-data-free recognition crops from the canonical MFDS product-name corpus plus deterministic medicine-domain dosage and hard-negative templates. The canonical database is an input only and should be mounted read-only. Generated corpora belong under `/artifacts/ocr/recognition/`, not in the Git worktree.

The renderer uses the Noto CJK font installed in the dedicated fine-tune image. The generation report pins the canonical database SHA-256, canonical source-snapshot SHA-256, font SHA-256, generator version, seed, and sample count. Re-running the same completed configuration validates and returns the existing dataset; changing any pinned input fails rather than silently overwriting it. Interrupted generation keeps `.generation-state.json` plus `samples.partial.jsonl` and resumes from its checkpoint.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm \
  -v /absolute/path/to/canonical.sqlite:/reference/canonical.sqlite:ro \
  ocr-finetune generate-synthetic \
  --canonical-db /reference/canonical.sqlite \
  --output-dir /artifacts/ocr/recognition/synth-5k \
  --count 5000 --seed 112 --json
```

The generator covers the research-plan semantic strata (`product`, `strength`, `dose`, `frequency`, `duration`, `schedule`) and hard negatives (`clinic_hours`, `phone`, `date`, `identifier`). It also creates numeric adversarial cases (`0.5정`, `1/2정`, `1~2정`) and capture-risk tags for small print, low contrast, rotation, and plastic reflection.

Run the plan gate after generation. A non-zero exit code means at least one required document type, script, semantic stratum, or risk stratum is below the requested minimum.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  audit-coverage \
  --manifest /artifacts/ocr/recognition/synth-5k/manifest.json \
  --minimum-per-stratum 300 --json
```

The canonical product source recorded by this project is the Korean MFDS `DrugPrdtPrmsnInfoService07` public API. The source portal currently reports no restriction on the permitted-use range; generated provenance records this as the internal identifier `data-go-kr-unrestricted-use`. Re-check source terms before publishing or redistributing a corpus outside this research workspace.

## First 5k learning-curve baseline

The first complete baseline uses the synthetic 5k corpus with a `drug_family` graph holdout (`4000/500/500`) and batch size 32 for 10 epochs. The runner evaluates the pinned pretrained model on the fixed test split before training, checkpoints every epoch, resumes only from a complete `iter_epoch_N` checkpoint, selects `best_accuracy` by validation, and evaluates that checkpoint on the same test split. A completed run is idempotent and re-validates the best checkpoint SHA before returning the cached result.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train baseline \
  --pretrained-model /artifacts/ocr/checkpoints/upstream/korean_PP-OCRv5_mobile_rec_pretrained.pdparams \
  --manifest /artifacts/ocr/recognition/synth-5k/manifest.json \
  --export-dir /artifacts/ocr/recognition/synth-5k/paddle-drug \
  --run-dir /artifacts/ocr/training/baseline-5k-e10-b32 \
  --epochs 10 --batch-size 32 --json
```

The recorded synthetic-only result is in `results/synth-5k-drug-baseline.json`: pretrained exact accuracy `0.6080` versus validation-best test accuracy `0.9740`, with normalized edit distance improving from `0.9535` to `0.9975`. Validation peaked at epoch 2 and later epochs oscillated, so the 10-epoch final checkpoint is preserved for reproducibility but is not treated as the selected model. These figures are **not** evidence of real-photo or end-to-end prescription safety; real deidentified holdouts and the other layout/source holdouts remain required.

## Unified full-document recognition data

New recognition training/evaluation data should be derived from the canonical full-document corpus rather than a separate line-image generator when the goal is E2E robustness. `ocr-corpus generate --materialize` writes `views/recognition/manifest.json` plus a Paddle-ready `views/recognition/paddle/` export. Each crop is rectified from the final degraded document raster and inherits its parent document train/validation/test split, semantic role, association group, layout, capture anchor, augmentation difficulty/components, and risk tags. Generator v4 composes camera failures, so one crop can simultaneously carry motion blur, JPEG loss, downscale, noise, exposure/color drift, perspective, and glare/shadow rather than belonging to only one degradation bucket. This makes recognition failures directly comparable with detector and E2E results from the same source documents. The older standalone 100k recognition corpus remains a retained historical training artifact, not the canonical source for future full-document robustness experiments.

## Fixed full-document recognizer evaluation

The tracked `results/selected-100k-training-drug-exposure.json` binds the current canonical family holdout to the exact historical train split used by the selected 100k checkpoint. New fixed-eval and full-document training corpora must use that artifact (or a cryptographically equivalent regenerated artifact) so `drug-unseen` means unseen to the model being fine-tuned, not merely held out from the new corpus.

`ocr-finetune-train fixed-eval` evaluates one pinned recognizer checkpoint against a materialized unified recognition dataset with a single `tools/infer_rec.py` pass, then derives all metric slices from the immutable prediction artifact. Fixed-eval policy v2 reports overall and critical exact accuracy plus normalized edit similarity, product/dose/frequency/duration, clean/medium/hard, augmentation components/combinations, critical drug seen/unseen, explicit `product-seen`/`product-unseen`, and the cross-slices `seen-drug-unseen-image`, `unseen-drug-familiar-degradation`, `unseen-drug-hard-in-domain`, and `unseen-drug-hard-ood`. The runner binds dataset/checkpoint/config/source hashes, policy id, the canonical plan hash, and evaluator-core hash in state. Any changed slice contract therefore invalidates an older completed cache instead of silently returning metrics produced under a previous definition.

The evaluation path allows model-incompatible **noncritical** context references to remain in the overall metric so model limitations are visible, but critical medication references must satisfy the pinned recognizer dictionary and maximum text length or evaluation fails closed. This exception applies only to direct fixed inference; training compatibility checks remain strict for every training sample.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_fixed_eval \
  docker compose run --rm ocr-finetune-train fixed-eval \
  --baseline-result /workspace/path/to/baseline-result.json \
  --expected-checkpoint-sha256 <sha256> \
  --manifest /workspace/path/to/views/recognition/manifest.json \
  --run-dir /artifacts/ocr/evaluations/recognition/fixed-eval/run-1 \
  --minimum-required-count 32 --device gpu --json
```

## Selected-checkpoint full-document training views

Full-document fine-tuning does not train directly from the raw materialized recognition view. `prepare-training-view` first derives a model-compatible, immutable training view using the pinned recognizer dictionary and `max_text_length`. Model-incompatible references are excluded from every split, while the explicit `degradation-hard-ood` signature is excluded from **train only** and may remain in validation/test for diagnostics. Train/validation/test membership is inherited from the parent document split and is never re-randomized. Retained images are hard-linked when the source and derived view share the durable artifact filesystem, and the derived manifest records the source fingerprint, source split SHA-256, dictionary/model contract, filtering policy, exclusion counts, and resulting dataset fingerprint.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_training_view \
  docker compose run --rm ocr-finetune-train prepare-training-view \
  --manifest /artifacts/ocr/corpora/unified-360/views/recognition/manifest.json \
  --split /artifacts/ocr/corpora/unified-360/views/recognition/document-split.json \
  --output-dir /artifacts/ocr/recognition/training-view \
  --json
```

The full-document-only experiment uses `selected-finetune`, which initializes `Global.pretrained_model` from the cryptographically selected 100k `best_accuracy` checkpoint rather than from the original Paddle pretrained model. Resume checkpoints are accepted only from the new run. This runner intentionally does not inherit the old standalone baseline's required semantic/risk slice list; model promotion is decided later on the immutable fixed evaluation corpus. It retains the same strict state, checkpoint, resume, best-validation, and SHA verification behavior.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_selected_finetune \
  docker compose run --rm \
  -v /absolute/path/to/historical/training:/historical-training:ro \
  ocr-finetune-train selected-finetune \
  --initial-baseline-result /historical-training/baseline-v5-100k-source-stable-e10-b32-lr1e4-w1/baseline-result.json \
  --expected-initial-checkpoint-sha256 <selected-100k-sha256> \
  --manifest /artifacts/ocr/recognition/training-view/manifest.json \
  --export-dir /artifacts/ocr/recognition/training-view/paddle \
  --run-dir /artifacts/ocr/training/full-document-only \
  --epochs 4 --batch-size 32 --learning-rate 0.00005 --warmup-epochs 1 --json
```

For the mixed experiment, `prepare-mixed-training-view` adds **only the exact historical 100k train split** to the new full-document train split. Validation and test remain exclusively from the new full-document corpus. The command fails unless the new corpus's `historical_exposure` metadata matches the supplied historical dataset fingerprint, train-split SHA-256, and train count. Historical images are copied because the authoritative corpus is normally a read-only Docker bind mount on a separate mount where hard-linking is not possible; new unified images are hard-linked within the durable artifact filesystem. All ids, documents, and grouping keys are namespaced before combination.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_mixed_view \
  docker compose run --rm \
  -v /absolute/path/to/synth-100k-v5:/historical:ro \
  ocr-finetune-train prepare-mixed-training-view \
  --historical-manifest /historical/manifest.json \
  --historical-split /historical/paddle-source-stable-v1/split.json \
  --unified-manifest /artifacts/ocr/recognition/training-view/manifest.json \
  --unified-export-dir /artifacts/ocr/recognition/training-view/paddle \
  --output-dir /artifacts/ocr/recognition/mixed-training-view \
  --json
```

The mixed output is passed to the same `selected-finetune` runner. This keeps the only experimental difference in the training data composition while evaluation, model initialization, checkpointing, and fixed-eval selection remain identical.

## V6 unified recognizer training boundary

The current v6 rebuild does not depend on the historical selected-checkpoint file being present. `v6-preflight`/`v6-train` start from the pinned official `korean_PP-OCRv5_mobile_rec` pretrained weight and require a model-compatible unified recognition training view produced by `prepare-training-view`. Preflight verifies the pinned PaddleOCR config/dictionary/pretrain hashes, every source crop hash through the dataset loader, exact document-split membership and label-file content, and the training-view policy before constructing the Paddle command. Only `train` is optimized and only `val` is used for Paddle checkpoint selection; `test.txt` is bound into provenance but is forbidden from the optimization command.

```bash
docker compose run --rm ocr-finetune-train v6-preflight \
  --manifest /artifacts/ocr/recognition/v6-training/manifest.json \
  --export-dir /artifacts/ocr/recognition/v6-training/paddle \
  --run-dir /artifacts/ocr/training/recognizer-v6 \
  --epochs 4 --batch-size 32 --learning-rate 0.00005 --warmup-epochs 1 --json
```

Replacing `v6-preflight` with `v6-train` starts the explicitly approved optimization run. The runner is single-writer, persists atomic state/result files, emits 30-second heartbeats, checkpoints each epoch, and resumes only from the latest complete epoch. A completed `best_accuracy.pdparams` remains `pending_project_safety_evaluation`; held-out synthetic test evaluation, frozen full-document OCR evaluation, real-photo validation and Android runtime gates happen later and are not replaced by Paddle validation accuracy.

## Full-document detector → fine-tuned recognizer observation path

`ocr-full-document` composes the mobile detector research pipeline with a completed fine-tune baseline. The default detector is the selected `PP-OCRv5_mobile_det` candidate at edge 640. Its official ONNX archive is verified against `browser_ocr/detection/detector-models.json`; the recognizer is loaded directly from the `best_checkpoint` recorded by the supplied baseline result and its SHA-256 is verified before inference.

The command runs the detector once on the input pixels, then canonicalizes metadata-free 0/90/180/270-degree page orientation before parser-visible OCR geometry is produced. Detector-box aspect ratios reduce the common search to one 180-degree pair; a bounded set of strong text boxes is recognized under the remaining candidates and the selected recognizer score resolves upright vs upside-down. The full page is then rotated once and the original detector polygons are transformed into the same canonical coordinate frame, avoiding a second detector pass. The browser/Android ONNX worker uses the same detector-axis + recognizer-probe policy. The result keeps the original file SHA-256 plus `source_width`/`source_height` for provenance, while `image.width`/`image.height` and every `regions[].polygon` describe the canonical upright frame. The orientation decision and probe scores are observable under `stages.orientation`.

After orientation, the command perspective-normalizes each quadrilateral into a recognition crop and runs the selected Korean recognizer. Batch materializers construct one persistent full-document runtime: detector and recognizer weights are loaded once per batch process and reused across documents and across orientation/final-recognition passes. Recognition remains deliberately one crop at a time instead of GPU-batching crops, bounding peak VRAM and preserving the exact single-image PaddleOCR scores used by the earlier subprocess path. Per-document state/result SHA-256 checkpoints and crash-window restart behavior are unchanged. The schema-v2 result contains raw OCR `regions` and `text_lines` only. Structured medication rows are the responsibility of the learned parser model, which is deliberately outside this OCR producer. The output profile pins detector, recognizer, orientation code, cropper, persistent-runtime code, runtime environment, and orchestration implementation hashes so parser experiments can reuse an unchanged OCR observation set without coupling to a parser implementation.

Detector assets must already be present under the detection cache (the detection pipeline's `assets` command populates that cache). Generated crops, logs, state, and results belong under `/artifacts/ocr/`, outside the Git worktree.

The synthetic parser runtime materializer has no document-count limit by default and keeps one persistent detector/recognizer runtime for the full invocation. `--max-new-documents N` remains available only when an explicitly bounded invocation is desired. Whether bounded or unbounded, per-document result and batch checkpoints are made durable before advancing, and this execution policy is not part of OCR producer identity.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-full-document \
  --image /workspace/browser_ocr/detection/corpus/images/synthetic-000001.jpg \
  --baseline-result /artifacts/ocr/training/baseline-v5-100k-source-stable-e10-b32-lr1e4-w1/baseline-result.json \
  --output-dir /artifacts/ocr/runtime/full-document/synthetic-000001 \
  --json
```

The output directory is stateful and strict. Re-running exactly the same image/model/implementation profile returns the completed result; changing an input, model, or implementation hash for the same directory fails rather than mixing artifacts. A crashed/failed run can resume by re-running the identical profile after the process lock is released. No-detection is reported explicitly as `skipped_no_detections` for recognition rather than silently substituting another OCR path.
