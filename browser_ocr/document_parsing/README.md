# Medication document parsing research

This package isolates document-structure parsing from OCR recognition quality.
It is research-only and does not reconnect OCR to the product.

## Boundary

Input fixtures contain only synthetic OCR boxes:

- recognized text
- recognition confidence
- four-point polygon geometry

Expected output is a set of medication rows shaped like the dormant medication
intake boundary: `product_query`, structured draft fields, and explicit
`uncertainty_codes`. Schema v2 also requires source-box `evidence` for the
product query and every non-null structured field. The row id is the first
product-evidence box id, so repeated identical product text remains representable
as distinct document rows. Canonical product identity remains outside this layer.

The first benchmark is intentionally model-free. The tracked
`geometry_rule_v2` baseline deskews OCR polygons, groups them into lines,
reconstructs split table headers and medication labels, parses table columns and
repeated medication-bag blocks, and only propagates an explicitly labeled common
regimen to a contiguous group whose scope is structurally proven. Unassociated
regimen text stays unresolved rather than being copied to a nearby medication.

This baseline is deliberately small. Passing the seed corpus is a harness sanity
check, not evidence that rules generalize to unseen pharmacy layouts. Learned
document/KIE models should only be considered after broader layout perturbation
and held-out document experiments show a measured need.

## Safety metrics

The evaluator separates three outcomes that must not be conflated:

- exact structured fields that match the expected medication
- unresolved fields left empty for later review
- false exact fields, especially values copied from another medication row

`cross_medication_associations` is the critical association metric and is based
on source-box provenance, not value inequality. This means a wrong association
is still visible when two medications happen to print the same dose or
frequency. `invented_fields` separately catches exact structured claims for
fields the expected row intentionally leaves unresolved. A parser that cannot
prove a field association should leave that field unresolved rather than
borrowing a plausible exact value from another medication.

## Agent Control CLI

Validate the tracked synthetic corpus:

```sh
docker compose run --rm ocr-document-parse \
  validate-corpus \
  --corpus browser_ocr/document_parsing/corpus/manifest.json \
  --json
```

Evaluate a prediction file:

```sh
docker compose run --rm ocr-document-parse \
  evaluate \
  --corpus browser_ocr/document_parsing/corpus/manifest.json \
  --predictions /workspace/path/to/predictions.json \
  --json
```

Prediction files use schema version 2 and contain one `case_id` plus `rows` per
evaluated case. An empty `rows` list is valid fail-closed output.

Run the tracked model-free baseline directly against the oracle OCR boxes:

```sh
docker compose run --rm ocr-document-parse \
  run-baseline \
  --corpus browser_ocr/document_parsing/corpus/manifest.json \
  --json
```

The command returns both the generated prediction envelope and the benchmark
evaluation so later baselines can be compared under exactly the same contract.

## Learned layout research baseline

`hashed_layout_linear_v2` is a deliberately small learned comparison model. OCR
regions are nodes. A multiclass node head predicts `product`, `dose`,
`frequency`, `duration`, or `other` from bounded hashed text features, OCR
confidence, and normalized box geometry. A separate learned edge head predicts
whether a structured field belongs to a candidate product using relative
geometry plus local product-neighborhood features. The decoder retains the same
source-region evidence contract as `geometry_rule_v2` and leaves ambiguous edges
unresolved rather than selecting the nearest medication blindly.

The node head can be initialized from a balanced deterministic sample of the
retained 100k recognition corpus, while layout edges are trained only on the
full-document records supplied to the current fold. The research CLI runs both
capture-profile and whole-layout-family cross-validation and compares learned
output with `geometry_rule_v2` using the same evidence-based medication safety
metrics:

```sh
docker compose run --rm \
  -v /path/to/full-document-results:/results:ro \
  -v /path/to/synth-100k-v5:/semantic:ro \
  ocr-document-learned benchmark \
  --corpus /workspace/browser_ocr/detection/corpus/manifest.json \
  --results-root /results \
  --output-dir /workspace/browser_ocr/finetune/work/learned-layout \
  --semantic-samples /semantic/samples.jsonl \
  --semantic-per-role 2500 --semantic-epochs 12 \
  --epochs 60 --seed 112 --json
```

The tracked `results/learned-layout-v2-summary.json` records the current outcome.
On the 36-document synthetic benchmark, capture-profile holdout ties
`geometry_rule_v2` exactly: 580/672 critical fields, 145/168 rows, 36/36 safety
passes, zero false exact fields, and learned edge precision/recall/F1 of 1.0.
Whole-layout-family holdout is worse: 570/672 critical fields and 31/36 safety
passes versus 580/672 and 36/36 for the rule baseline. All five false exact
fields are from the unseen `legacy_preprinted_medication_bag` family, where the
edge head remains perfect but the node head cannot distinguish standalone
printed labels such as `1일`/`1회` from values and has never learned the
`1포(정)` dose form in context.

Therefore this learned model is **not promoted** into the full-document OCR
pipeline. The next learned experiment should broaden contextual node training
coverage or introduce a small contextual node encoder; threshold tuning or
hard-coded OCR substitutions are not justified by this result. These numbers
remain synthetic-only research evidence and are not a real-photo or production
release result.
## Contextual learned layout v3

`hashed_layout_context_v3` keeps the small learned relation model but replaces
single-box role classification with a two-stage contextual node encoder. Stage 1
is the semantic-pretrained local role head. Stage 2 applies a learned residual
correction using the six nearest OCR boxes, relative `dx`/`dy`/distance, visual
row/column overlap, directional neighborhood role distributions, and document
role context. The contextual features are built only from OCR text/confidence,
box geometry, and Stage-1 predictions; GT roles are never fed to inference.

The training/evaluation boundary is now seed-disjoint. A separate 360-document
full-document corpus (`seed=911`) supplies contextual training labels, with 60
samples for every layout family and 60 for every capture profile (13,140 text
regions / 6,000 critical medication regions). The original 36-document corpus
(`seed=153`) remains evaluation-only and is passed through the actual
`PP-OCRv5_mobile_det@640` detector plus the selected 100k recognizer before the
learned parser sees it. The semantic Stage-1 initializer still uses the balanced
12,500-example sample from the retained 100k recognition corpus.

The tracked model is
`browser_ocr/document_parsing/models/hashed-layout-context-v3.json`. It is 38,140
bytes with SHA-256
`742f2f7004a8561d3e93fd4d6c136309c04dc9bc3508c9ffb48f33549ce74a05`.
Two independent runs with the same seed/corpora produced byte-identical model
JSON. On the separate 36-document detector+OCR holdout it exactly matches
`geometry_rule_v2`: 145/168 medication rows, 580/672 critical fields, 31/36
quality-pass documents, 36/36 safety-pass documents, zero false exact fields,
zero cross-medication associations, and relation precision/recall/F1 of 1.0.
All five non-quality documents are the already gated severe motion/JPEG cases;
all other capture profiles are 6/6 exact. The legacy preprinted medication-bag
family is 5/6 exact, with its motion/JPEG sample failing closed, so the earlier
`1일`/`1회` label confusion is no longer present.

A representative research run is:

```sh
docker compose run --rm \
  -v /path/to/ocr-finetune-work:/source-work:ro \
  ocr-document-learned benchmark \
  --corpus browser_ocr/detection/corpus/manifest.json \
  --results-root /source-work/full-document-e2e-v2 \
  --output-dir browser_ocr/finetune/work/learned-layout-context-v3 \
  --semantic-samples /source-work/synth-100k-v5/samples.jsonl \
  --semantic-per-role 2500 --semantic-epochs 12 \
  --context-train-corpus browser_ocr/finetune/work/layout-context-train-360/manifest.json \
  --epochs 8 --seed 112 --skip-cross-validation --json
```

`results/learned-layout-context-v3-summary.json` is the tracked comparison.
This is now a viable synthetic learned-parser candidate, but it is deliberately
**not** made the default parser yet: no real deidentified full-document corpus or
Android handset benchmark has been evaluated. A host-Python parser-only probe
was about 14 ms/document and the model is only ~38 KiB, so OCR remains the likely
mobile bottleneck, but handset measurements are still required before product
integration.
