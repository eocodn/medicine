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