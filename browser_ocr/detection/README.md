# Mobile prescription text-detection research

This package isolates the first stage of the proposed document pipeline:

`full document -> text detection -> text recognition -> document parsing`

Detection remains independently benchmarkable, but its synthetic documents now come from the shared `browser_ocr/corpus` generator so recognition, parsing, and E2E experiments can derive aligned views from exactly the same sources.

## What is fixed by this foundation

- A full-document corpus is a whole image plus per-text-region quadrilateral ground truth.
- Regions carry `association_group` and `semantic_role` metadata so a detector can be penalized for merging text across medication rows or blocks.
- Critical medication fields are tagged separately from headers and instructions.
- Detection recall/precision use one-to-one matching at >=80% visible-text core coverage. This avoids treating model-specific DB unclip padding as a miss while separate merge/split metrics still penalize over-broad crops.
- Evaluation reports recall/precision/Hmean, mean core coverage/IoU, plus medicine-specific `critical_box_recall`, `merge_errors`, `cross_association_merges`, and `split_errors`.
- The tracked seed corpus is synthetic only. It is a harness fixture, not evidence of real-photo generalization.

## Full-document synthetic pipeline

The canonical generator is unified OCR corpus generator v4. It is designed to scale beyond the original six-document harness while keeping the ground truth authoritative and resumable. Detection consumes the full-page view; recognizer, parser, and E2E views are materialized from the same documents by `browser_ocr/corpus`.

- Six procedural layout families: general prescription table, dense administrative prescription form, legacy blue preprinted medication bag, classic medication bag, counseling/information-dense medication bag, and pharmacy information sheet.
- Six balanced capture anchor profiles: flat scan, phone perspective, low-contrast defocus, glare/shadow, motion-blur + JPEG compression, and partial-crop + foreground clutter. Generator v4 no longer makes those effects mutually exclusive: medium/hard documents compose multiple camera failures in the same raster.
- Three explicit augmentation difficulties (`clean`, `medium`, `hard`). The manifest records the exact component vector and bounded parameters for perspective, defocus, motion blur, JPEG, exposure/contrast, glare/shadow, downscale→upsample loss, deterministic sensor noise, white-balance shift, crop, and clutter.
- Layout families vary medication density, row spacing, column positions, typography, and selected instruction wrapping instead of emitting one fixed geometry per family.
- Three material profiles (plain paper, folded paper, wrinkled plastic), three printer profiles (clean laser, low toner, ink bleed), and three scene-background profiles.
- Every visible synthetic text item is annotated. Medication fields, contextual fields, and distractor text are separate `region_class` values.
- Each text region stores an annotation polygon plus a tighter natural text-core polygon, both before and after homography. Layout slots are kept separately and are not used as text GT.
- Final corpus images are JPEG raster captures produced by ImageMagick with librsvg. Both rasterizer and SVG-delegate versions are fingerprinted into the generator configuration so renderer changes cannot silently reuse old output.
- Generation is deterministic per sample index, uses an exclusive output lock, atomically writes images/checkpoints/manifests, verifies checkpoint image hashes on resume, and rejects seed/count/renderer/config drift. The generator revision is part of the fingerprint so implementation changes cannot silently reuse an older completed corpus.
- Long runs emit progress on stderr while JSON results remain clean on stdout.
- Coverage auditing fails closed if required layout, capture anchor, augmentation difficulty/component, material, printer, background, risk, or critical medication-field strata disappear.

The tracked seed is intentionally small and synthetic-only. It is a detector stress harness, not evidence that a model generalizes to real patient photos. Real-photo holdouts remain necessary before any release decision.

For detector fine-tuning, unified materialization also writes PaddleOCR-compatible `detection/paddle/{train,val,test}.txt` labels from the same final document polygons. The export keeps the canonical corpus root as `data_dir`, so detector training and detector evaluation share document identities and degradation profiles instead of using a separate synthetic source.

## Agent Control CLI

All commands support machine-readable JSON output.

```sh
docker compose run --rm ocr-corpus generate --output /tmp/ocr-corpus --count 360 --seed 153 --materialize --json
node browser_ocr/detection/cli.mjs validate --corpus browser_ocr/detection/corpus/manifest.json --json
node browser_ocr/detection/cli.mjs audit --corpus browser_ocr/detection/corpus/manifest.json --json
node browser_ocr/detection/cli.mjs matrix --json
node browser_ocr/detection/cli.mjs assets --output browser_ocr/detection/.cache/models --json
node browser_ocr/detection/cli.mjs benchmark --output browser_ocr/detection/results/zero-shot --threads 1 --json
node browser_ocr/detection/cli.mjs evaluate --corpus /path/to/manifest.json --predictions /path/to/predictions.json --json
```

The benchmark matrix is deliberately small: official `PP-OCRv5_mobile_det`, `PP-OCRv6_tiny_det`, and `PP-OCRv6_small_det` ONNX models at detector longest edges 640, 960, and 1280. Archive URLs, SHA-256 digests, official preprocessing, and model-specific DB postprocess settings are pinned in `detector-models.json`. Downloads are resumable and hash-verified; benchmark runs are checkpointed and write per-run prediction artifacts plus a ranked summary.

The checked-in `reports/zero-shot-synthetic-36-r8.json` is a historical detector result produced against the previous v2/revision-8 manifest. It remains immutable evidence for that exact corpus identity; new experiments should use the unified v4 generator and record a new result rather than relabeling the old report. Historical detector numbers from earlier corpora are not assumed to carry over to the stronger composable augmentation. CPU latency/RSS numbers remain development proxies only; representative Android devices and a de-identified real-photo holdout remain release gates.

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
