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

## Training gate

`upstream.json` intentionally has `training_enabled=false`. The official pretrained model URL is recorded, but a partial download is never treated as a pin. Before the first actual training run we must:

1. download the complete pretrained weights with resumable/checkpointed tooling and record SHA-256;
2. pin the PaddleOCR training source/revision and config;
3. select the actual training hardware profile (CUDA/Paddle build, GPU memory, batch-size envelope);
4. compare the upstream model and each learning-curve checkpoint on the exact same holdouts.

Do not generate a dictionary from only the training labels and silently replace the model dictionary. `observed-characters.txt` is an audit artifact; dictionary compatibility with the upstream Korean model must be checked explicitly before training.

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
