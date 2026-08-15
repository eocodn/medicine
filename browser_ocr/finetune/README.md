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
  --output /workspace/path/to/layout-split.json --json

COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  export-paddle --manifest /workspace/path/to/manifest.json \
  --split /workspace/path/to/layout-split.json \
  --output-dir /workspace/path/to/paddle-layout --json
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
  --pretrained-model /workspace/browser_ocr/finetune/work/upstream/korean_PP-OCRv5_mobile_rec_pretrained.pdparams \
  --manifest /workspace/browser_ocr/finetune/work/synth-5k/manifest.json \
  --export-dir /workspace/browser_ocr/finetune/work/synth-5k/paddle-drug \
  --run-dir /workspace/browser_ocr/finetune/work/training/smoke \
  --train-samples 128 --val-samples 64 --batch-size 16 --json
```

Generated datasets, downloaded weights, training logs, and checkpoints stay under the ignored `browser_ocr/finetune/work/` tree. Do not generate a dictionary from only the training labels and silently replace the model dictionary; `audit-model` must remain green against the pinned upstream Korean dictionary before training.

## Deterministic synthetic recognition crops

`generate-synthetic` builds patient-data-free recognition crops from the canonical MFDS product-name corpus plus deterministic medicine-domain dosage and hard-negative templates. The canonical database is an input only and should be mounted read-only. Generated corpora belong under the ignored `browser_ocr/finetune/work/` tree, not in Git.

The renderer uses the Noto CJK font installed in the dedicated fine-tune image. The generation report pins the canonical database SHA-256, canonical source-snapshot SHA-256, font SHA-256, generator version, seed, and sample count. Re-running the same completed configuration validates and returns the existing dataset; changing any pinned input fails rather than silently overwriting it. Interrupted generation keeps `.generation-state.json` plus `samples.partial.jsonl` and resumes from its checkpoint.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm \
  -v /absolute/path/to/canonical.sqlite:/reference/canonical.sqlite:ro \
  ocr-finetune generate-synthetic \
  --canonical-db /reference/canonical.sqlite \
  --output-dir /workspace/browser_ocr/finetune/work/synth-5k \
  --count 5000 --seed 112 --json
```

The generator covers the research-plan semantic strata (`product`, `strength`, `dose`, `frequency`, `duration`, `schedule`) and hard negatives (`clinic_hours`, `phone`, `date`, `identifier`). It also creates numeric adversarial cases (`0.5정`, `1/2정`, `1~2정`) and capture-risk tags for small print, low contrast, rotation, and plastic reflection.

Run the plan gate after generation. A non-zero exit code means at least one required document type, script, semantic stratum, or risk stratum is below the requested minimum.

```bash
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning docker compose run --rm ocr-finetune \
  audit-coverage \
  --manifest /workspace/browser_ocr/finetune/work/synth-5k/manifest.json \
  --minimum-per-stratum 300 --json
```

The canonical product source recorded by this project is the Korean MFDS `DrugPrdtPrmsnInfoService07` public API. The source portal currently reports no restriction on the permitted-use range; generated provenance records this as the internal identifier `data-go-kr-unrestricted-use`. Re-check source terms before publishing or redistributing a corpus outside this research workspace.

## First 5k learning-curve baseline

The first complete baseline uses the synthetic 5k corpus with a `drug_family` graph holdout (`4000/500/500`) and batch size 32 for 10 epochs. The runner evaluates the pinned pretrained model on the fixed test split before training, checkpoints every epoch, resumes only from a complete `iter_epoch_N` checkpoint, selects `best_accuracy` by validation, and evaluates that checkpoint on the same test split. A completed run is idempotent and re-validates the best checkpoint SHA before returning the cached result.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train baseline \
  --pretrained-model /workspace/browser_ocr/finetune/work/upstream/korean_PP-OCRv5_mobile_rec_pretrained.pdparams \
  --manifest /workspace/browser_ocr/finetune/work/synth-5k/manifest.json \
  --export-dir /workspace/browser_ocr/finetune/work/synth-5k/paddle-drug \
  --run-dir /workspace/browser_ocr/finetune/work/training/baseline-5k-e10-b32 \
  --epochs 10 --batch-size 32 --json
```

The recorded synthetic-only result is in `results/synth-5k-drug-baseline.json`: pretrained exact accuracy `0.6080` versus validation-best test accuracy `0.9740`, with normalized edit distance improving from `0.9535` to `0.9975`. Validation peaked at epoch 2 and later epochs oscillated, so the 10-epoch final checkpoint is preserved for reproducibility but is not treated as the selected model. These figures are **not** evidence of real-photo or end-to-end prescription safety; real deidentified holdouts and the other layout/source holdouts remain required.

## Unified full-document recognition data

New recognition training/evaluation data should be derived from the canonical full-document corpus rather than a separate line-image generator when the goal is E2E robustness. `ocr-corpus generate --materialize` writes `views/recognition/manifest.json` plus a Paddle-ready `views/recognition/paddle/` export. Each crop is rectified from the final degraded document raster and inherits its parent document train/validation/test split, semantic role, association group, layout, capture anchor, augmentation difficulty/components, and risk tags. Generator v4 composes camera failures, so one crop can simultaneously carry motion blur, JPEG loss, downscale, noise, exposure/color drift, perspective, and glare/shadow rather than belonging to only one degradation bucket. This makes recognition failures directly comparable with detector and E2E results from the same source documents. The older standalone 100k recognition corpus remains a retained historical training artifact, not the canonical source for future full-document robustness experiments.

## Full-document detector → fine-tuned recognizer research path

`ocr-full-document` composes the mobile detector research pipeline with a completed fine-tune baseline. The default detector is the selected `PP-OCRv5_mobile_det` candidate at edge 640. Its official ONNX archive is verified against `browser_ocr/detection/detector-models.json`; the recognizer is loaded directly from the `best_checkpoint` recorded by the supplied baseline result and its SHA-256 is verified before inference.

The command performs full-document detection, perspective-normalizes each quadrilateral into a recognition crop, runs the selected Korean recognizer, then passes the recognized quadrilaterals through the evidence-backed `geometry_rule_v2` document parser. The schema-v2 result contains both the raw `regions` and structured `medications`; every exact medication field carries the originating `region-NNNN` evidence ids so repeated products and cross-medication associations remain distinguishable even when their printed values are equal.

The parser does not maintain OCR-typo substitution tables. Table rows are recovered from geometry plus typed medication values when headers are damaged, repeated medication-bag blocks are accepted only when each block proves its own regimen structure, and ambiguous associations remain unresolved. The output profile pins the hashes of the detector, recognizer, parser, parser contract, and full-document implementation, so changing pipeline code cannot silently reuse a result produced by an older implementation.

Structured parsing also has a document-level OCR quality gate. Empty recognitions are retained in the raw region output but excluded from parser tokens, and the parser abstains entirely when more than 20% of detected regions have recognition confidence below 0.8 (empty text counts as low confidence). This is a fail-closed guard against broad blur/compression failures; it does not rewrite low-confidence text or guess medication values.

Detector assets must already be present under the detection cache (the detection pipeline's `assets` command populates that cache). Generated crops, logs, state, and results should remain under ignored `browser_ocr/finetune/work/` paths.

```bash
LOCAL_UID=$(id -u) LOCAL_GID=$(id -g) \
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-full-document \
  --image /workspace/browser_ocr/detection/corpus/images/synthetic-000001.jpg \
  --baseline-result /workspace/browser_ocr/finetune/work/training/baseline-v5-100k-source-stable-e10-b32-lr1e4-w1/baseline-result.json \
  --output-dir /workspace/browser_ocr/finetune/work/full-document/synthetic-000001 \
  --json
```

The output directory is stateful and strict. Re-running exactly the same image/model/implementation profile returns the completed result; changing an input, model, or implementation hash for the same directory fails rather than mixing artifacts. A crashed/failed run can resume by re-running the identical profile after the process lock is released. No-detection is reported explicitly as `skipped_no_detections` for recognition and `skipped_no_regions` for parsing rather than silently substituting another OCR or parser path.
