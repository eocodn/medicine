# OCR recognizer research pipeline

This directory contains the current PaddleOCR recognizer research/runtime tooling used with the unified v6 document corpus. It is not an application release bundle. Android/Web consume OCR only through an explicitly supplied runtime asset directory; model promotion is a separate step.

## Current pipeline

The active recognizer path is:

1. generate/materialize the unified v6 corpus under `browser_ocr/corpus`;
2. derive the recognition training view with the authoritative document-level train/val/test split;
3. verify the pinned PaddleOCR source/config/dictionary/pretrained weights;
4. train with `v6-train`, optimizing on train and selecting on val only;
5. use the completed recognizer result with the full-document OCR runtime to generate parser observations;
6. train/export the sparse graph parser under `browser_ocr/document_parsing`.

## Reproducibility and privacy

`upstream.json` pins the PaddleOCR source commit, recognizer configuration, dictionary, pretrained checkpoint identity, and verified CUDA/Paddle runtime. Persistent training outputs belong under `/artifacts/ocr/`, backed by host `~/dev/.artifacts/medicine`; they are not checked into the repository.

The recognition dataset contract forbids patient data. Real de-identified prescription images, when used for parser validation, remain outside the repository and are holdout-only. The synthetic v6 corpus is suitable for controlled training and regression work, not by itself for product-safety claims.

`results/selected-100k-training-drug-exposure.json` is lineage metadata required by the current v6 corpus split policy so the materialized r10 corpus remains reproducible.

## Docker tests

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-test
```

GPU/runtime smoke verification uses the training image:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train probe --json
```

## Prepare the v6 recognition training view

The unified corpus materializer produces the source recognition manifest/split. Derive the filtered training view with:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train prepare-training-view \
  --manifest /artifacts/ocr/corpora/<corpus>/views-v16/recognition/manifest.json \
  --split /artifacts/ocr/corpora/<corpus>/views-v16/recognition/split.json \
  --output-dir /artifacts/ocr/recognition/<training-view> \
  --json
```

The training view preserves the authoritative document split, excludes samples incompatible with the pinned recognizer contract, and reserves the configured severe OOD signature from training.

## Recognizer preflight and training

The selected pretrained Korean PP-OCRv5 mobile recognizer checkpoint is stored in the durable artifact tree and verified against `upstream.json`.

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train v6-preflight \
  --manifest /artifacts/ocr/recognition/<training-view>/manifest.json \
  --export-dir /artifacts/ocr/recognition/<training-view>/paddle \
  --run-dir /artifacts/ocr/training/<run> \
  --json
```

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-finetune-train v6-train \
  --manifest /artifacts/ocr/recognition/<training-view>/manifest.json \
  --export-dir /artifacts/ocr/recognition/<training-view>/paddle \
  --run-dir /artifacts/ocr/training/<run> \
  --epochs 4 \
  --batch-size 32 \
  --learning-rate 0.00005 \
  --warmup-epochs 1 \
  --json
```

`v6-train` is strict and resumable. It validates the exact dataset/export/upstream profile, resumes only from a complete epoch checkpoint, records authoritative state/result hashes, and rejects test labels in the optimization command. Test remains a promotion-only split.

## Full-document OCR for parser data

`ocr-full-document`, `ocr-parser-synthetic`, and `ocr-parser-real` use a completed recognizer result plus an explicitly selected detector artifact. Their runtime producer identity binds the detector asset/config, recognizer result/checkpoint, PaddleOCR source/runtime, orientation/crop implementations, and environment fingerprints.

Synthetic parser materialization:

```sh
COMPOSE_PROJECT_NAME=medicine_ocr_finetuning \
  docker compose run --rm ocr-parser-synthetic \
  --manifest /artifacts/ocr/corpora/<corpus>/views-v16/parsing/manifest.json \
  --recognizer-result /artifacts/ocr/training/<run>/result.json \
  --output-dir /artifacts/parser/<runtime-batch> \
  --json
```

The parser data and graph training/export contracts are documented in `browser_ocr/document_parsing/README.md`.

## Application boundary

Research code in this directory is not imported by the Android/Web application. The shared UI only knows the `/ocr-assets/` runtime boundary. Product builds do not bundle a checked-in OCR model. An OCR-enabled build must be given a separately produced, verified runtime directory through `MEDICINE_OCR_ASSETS_DIR`; otherwise the image-import control remains unavailable.
