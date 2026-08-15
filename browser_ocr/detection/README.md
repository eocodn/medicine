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

## Full-document synthetic pipeline

Generator v2 is designed to scale beyond the original six-document harness while keeping the ground truth authoritative and resumable.

- Six procedural layout families: general prescription table, dense administrative prescription form, legacy blue preprinted medication bag, classic medication bag, counseling/information-dense medication bag, and pharmacy information sheet.
- Six raster capture profiles: flat scan, true projective phone perspective, low-contrast defocus, glare/shadow, motion-blur + JPEG compression, and partial-crop + foreground clutter.
- Three material profiles (plain paper, folded paper, wrinkled plastic), three printer profiles (clean laser, low toner, ink bleed), and three scene-background profiles.
- Every visible synthetic text item is annotated. Medication fields, contextual fields, and distractor text are separate `region_class` values.
- Each text region stores both its local `source_polygon` and the final homography-transformed `polygon`, so projective geometry stays authoritative after camera simulation.
- Final corpus images are JPEG raster captures produced by ImageMagick with librsvg. Both rasterizer and SVG-delegate versions are fingerprinted into the generator configuration so renderer changes cannot silently reuse old output.
- Generation is deterministic per sample index, uses an exclusive output lock, atomically writes images/checkpoints/manifests, verifies checkpoint image hashes on resume, and rejects seed/count/renderer/config drift. The generator revision is part of the fingerprint so implementation changes cannot silently reuse an older completed corpus.
- Long runs emit progress on stderr while JSON results remain clean on stdout.
- Coverage auditing fails closed if required layout, capture, material, printer, background, risk, or critical medication-field strata disappear.

The tracked seed is intentionally small and synthetic-only. It is a detector stress harness, not evidence that a model generalizes to real patient photos. Real-photo holdouts remain necessary before any release decision.

## Agent Control CLI

All commands support machine-readable JSON output.

```sh
node browser_ocr/detection/cli.mjs generate --output /tmp/detection-corpus --count 360 --seed 153 --json
node browser_ocr/detection/cli.mjs validate --corpus browser_ocr/detection/corpus/manifest.json --json
node browser_ocr/detection/cli.mjs audit --corpus browser_ocr/detection/corpus/manifest.json --json
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
