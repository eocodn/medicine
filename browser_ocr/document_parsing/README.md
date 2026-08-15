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
`geometry_rule_v1` baseline deskews OCR polygons, groups them into lines,
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