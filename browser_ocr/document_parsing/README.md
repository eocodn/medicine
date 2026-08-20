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

## Unified full-document parser data

The canonical OCR corpus can materialize parser/KIE data from the same documents used for detector and recognizer experiments. `views/parsing/samples.jsonl` exposes box text/geometry, semantic roles, association groups, and positive `same_medication` edges for learned models. `views/parsing/oracle-{train,val,test}.json` and `oracle-manifest.json` use the existing parser corpus contract for rule-based or oracle-box evaluation. Product labels such as `약명` are represented explicitly as `product_label` evidence instead of being reconstructed by text-specific fixture logic. This view is intentionally independent of whether the eventual parser is deterministic or learned.

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
## Learned-parser dataset contract

Learned parser training does not consume the legacy rule-parser corpus directly. The canonical training contract separates the **observed OCR graph** from authoritative labels and image-level medication gold:

- `observation.kind`: `oracle`, deterministic `synthetic_ocr`, or actual `runtime_ocr`;
- each observed node carries text, confidence, polygon, and zero or more matched truth-region ids;
- `label_status=labeled` carries a role/group target, while split/merge observations spanning incompatible truth roles/groups are `ambiguous` and are masked from supervised role/relation loss;
- unmatched detector boxes are explicit `other` negatives;
- relations contain both `same_medication` positives and `different_medication` hard negatives for dose, frequency, duration, instruction, and medication-associated schedule nodes;
- `gold_rows` are image-level truth and do not depend on which regions OCR happened to observe.

The strict manifest binds `samples.jsonl` by SHA-256. Dataset outputs also carry an authoritative completed/running state and exclusive writer lock: an exact rerun reuses the completed dataset, while a different seed/source/split/content profile is rejected instead of replacing it. Synthetic data may be used for train/validation/test; `real_deidentified` data is holdout-only and is rejected from `train`.

Unified corpus materialization creates these parser datasets automatically:

- `parsing/datasets/oracle/`
- `parsing/datasets/train-synthetic-ocr/`
- `parsing/datasets/val-synthetic-ocr/`
- `parsing/datasets/test-synthetic-ocr/`

The deterministic synthetic-OCR producer starts from canonical tight `natural_text_polygon` geometry, perturbs OCR observations (drop/split/merge/jitter/text/confidence/order/noise), and then labels them through the same tight-geometry alignment used for runtime OCR. It does not copy truth labels onto corrupted boxes blindly.

Use the dataset Agent Control service directly when needed:

```sh
docker compose run --rm ocr-parser-data validate \
  --manifest /workspace/path/to/parser-dataset/manifest.json --json

docker compose run --rm ocr-parser-data build-runtime \
  --truth-samples /workspace/path/to/views/parsing/samples.jsonl \
  --results-root /workspace/path/to/full-document-results \
  --output-dir /workspace/path/to/parser-runtime-val \
  --dataset-id parser-runtime-val --split val --json
```

## Real de-identified prescription photos

Private prescription photos stay outside Git. Ingestion accepts only an external `real_deidentified` source manifest whose documents are already de-identified, use pseudonymous lowercase ids, use the document id as the image filename stem, and declare `contains_patient_data=false`. Only `val` and `test` are accepted.

The GPU `ocr-parser-real` service sends every photo through the exact selected full-document detector/crop/recognizer path and writes runtime OCR results plus annotation drafts. Runtime observations require pinned detector/recognizer/config/implementation SHA-256 metadata. Parser identity alone is deliberately stripped from the observation profile: changing the parser does not invalidate OCR observations produced by unchanged detector/recognizer inputs.

```sh
docker compose run --rm \
  -v /absolute/deidentified-corpus:/real:ro \
  ocr-parser-real \
  --source-manifest /real/manifest.json \
  --baseline-result /workspace/path/to/baseline-result.json \
  --output-dir /workspace/browser_ocr/finetune/work/parser-real-holdout \
  --json
```

Human annotation assigns node roles/groups and image-level `gold_rows`. The annotation index separately binds the source manifest, source sample list, per-document runtime result, and immutable OCR observation projection by SHA-256. Rerunning either preparation command reuses the completed binding and never rewrites human labels. Finalization rechecks those hashes and fails while any OCR node remains unlabeled:

```sh
docker compose run --rm ocr-parser-data finalize-real \
  --annotations-dir /workspace/browser_ocr/finetune/work/parser-real-holdout/annotations \
  --dataset-id parser-real-holdout-v1 \
  --output-dir /workspace/browser_ocr/finetune/work/parser-real-final \
  --json
```

A missed medication can therefore remain present in `gold_rows` even when OCR produced no corresponding node. This keeps parser-only evaluation distinct from detector/recognizer end-to-end recall.
