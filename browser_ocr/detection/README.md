# Mobile prescription text-detection research

This package isolates the first stage of the proposed document pipeline:

`full document -> text detection -> text recognition -> document parsing`

It intentionally does not depend on the recognition fine-tuning or document-parsing research branches.

## What is fixed by this foundation

- A full-document corpus is a whole image plus per-text-region quadrilateral ground truth.
- Regions carry `association_group` and `semantic_role` metadata so a detector can be penalized for merging text across medication rows or blocks.
- Critical medication fields are tagged separately from headers and instructions.
- Evaluation reports conventional recall/precision/Hmean, plus medicine-specific `critical_box_recall`, `merge_errors`, `cross_association_merges`, and `split_errors`.
- The tracked seed corpus is synthetic only. It is a harness fixture, not evidence of real-photo generalization.

## Agent Control CLI

All commands support machine-readable JSON output.

```sh
node browser_ocr/detection/cli.mjs generate --output /tmp/detection-corpus --count 30 --seed 153 --json
node browser_ocr/detection/cli.mjs validate --corpus browser_ocr/detection/corpus/manifest.json --json
node browser_ocr/detection/cli.mjs matrix --json
node browser_ocr/detection/cli.mjs evaluate --corpus /path/to/manifest.json --predictions /path/to/predictions.json --json
```

The initial benchmark matrix is deliberately small: current `PP-OCRv5_mobile_det`, `PP-OCRv6_tiny_det`, and `PP-OCRv6_small_det` at detector longest edges 640, 960, and 1280. Model assets and inference adapters are intentionally not pinned in this first slice.

## Prediction format

```json
{
  "schema_version": 1,
  "corpus_id": "...",
  "samples": [
    {
      "id": "synthetic-0001",
      "predictions": [
        {"polygon": [[10, 10], [100, 10], [100, 40], [10, 40]], "score": 0.98}
      ]
    }
  ]
}
```

Every corpus sample must be represented exactly once. Missing samples are a contract error rather than an implicit empty prediction, so interrupted benchmark runs cannot silently become poor model results.
