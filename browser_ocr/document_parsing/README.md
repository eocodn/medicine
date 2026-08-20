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
- each observed node carries text, confidence, a non-degenerate four-point polygon bounded by the declared image dimensions, and zero or more matched truth-region ids;
- `label_status=labeled` carries a role/group target, while split/merge observations spanning incompatible truth roles/groups are `ambiguous` and are masked from supervised role/relation loss;
- unmatched detector boxes are explicit `other` negatives;
- relations contain both `same_medication` positives and `different_medication` hard negatives for dose, frequency, duration, instruction, and medication-associated schedule nodes;
- `gold_rows` are image-level truth and do not depend on which regions OCR happened to observe. Learned-parser synthetic rows additionally carry deterministic, evidence-backed schedule/meal/route/PRN semantics when the associated instruction text proves them; the legacy deterministic-parser oracle/E2E rows intentionally retain their narrower core contract.
- `gold_rows_reviewed` separates an explicitly reviewed empty image-level gold set from the default unfinished annotation state.
- real documents preserve a pseudonymous lowercase-ASCII `source_id` plus the allowlisted de-identified-source `license_id` (`private-deidentified`) in the finalized parser sample; provenance fields are identifiers, not free-form text.

Parser dataset schema v2 binds both `samples.jsonl` and manifest metadata by SHA-256 and emits strict standards-compliant JSON. Gold draft fields are type/domain checked (finite positive dose values, bounded integer frequency/duration, enum meal/route values, boolean PRN, HH:MM schedules, and ISO dates) and must also satisfy product cross-field invariants: PRN cannot carry a fixed frequency/schedule, an explicit frequency must match explicit schedule count, and explicit date/duration combinations cannot contradict each other. Complete samples must contain the exact relation matrix implied by every labeled product/field pair, and every positively labeled medication association group must have a corresponding `gold_row_id`; extra image-level gold rows remain allowed for medications that OCR missed. Real-deidentified image SHA-256 values must remain unique even at the final parser-artifact contract, not only in the source manifest, so val/test leakage cannot be introduced by an alternate builder. Runtime observations are revalidated by the dataset artifact contract itself rather than trusted merely because a builder produced them. Dataset outputs also carry an authoritative completed/running state and exclusive writer lock: an exact rerun reuses the completed dataset, while a different seed/source/split/content profile is rejected instead of replacing it. Synthetic data may be used for train/validation/test; `real_deidentified` data is holdout-only and is rejected from `train`.

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

Private prescription photos stay outside Git. Ingestion accepts only an external `real_deidentified` source manifest whose documents are already de-identified, use pseudonymous lowercase ids, use the document id as the image filename stem, and declare `contains_patient_data=false`. Only `val` and `test` are accepted, and image SHA-256 values must be unique across the source manifest so the same photo cannot leak between validation and test under different pseudonyms.

The GPU `ocr-parser-real` service sends every photo through the exact selected full-document detector/crop/recognizer path and writes runtime OCR results plus annotation drafts. Runtime observations use an exact metadata allowlist and require pinned detector/recognizer/config/implementation SHA-256 metadata plus hashes of the exact loaded detector ONNX/config bytes, the actual PaddleOCR inference Python source tree, the dictionary selected by the recognizer config, and a canonical fingerprint of the Python inference runtime environment. GPU profiles additionally bind the runtime-visible device selector plus Paddle-reported CUDA/cuDNN/device/capability identity and available NVIDIA driver identity. Model identifiers are bounded ASCII ids rather than free-form metadata. Arbitrary runtime-profile fields are rejected instead of being copied into de-identified artifacts. The detector runtime additionally verifies that extracted ONNX/config bytes match the pinned archive before inference. A batch checkpoint binds one normalized OCR producer identity, so an interrupted batch cannot resume with a different detector/recognizer/PaddleOCR/runtime environment and mix producers. The standalone `build-runtime` path enforces the same one-producer-per-dataset invariant. Parser identity alone is deliberately stripped from the observation profile: changing the parser does not invalidate OCR observations produced by unchanged detector/recognizer inputs.

```sh
docker compose run --rm \
  -v /absolute/deidentified-corpus:/real:ro \
  ocr-parser-real \
  --source-manifest /real/manifest.json \
  --baseline-result /workspace/path/to/baseline-result.json \
  --output-dir /workspace/browser_ocr/finetune/work/parser-real-holdout \
  --json
```

Human annotation assigns node roles/groups and image-level `gold_rows`, then explicitly sets `gold_rows_reviewed=true` even when the reviewed image contains zero medication rows. A reviewed-empty gold set is accepted only when no OCR node has been positively assigned to a medication association group; each observed medication group must use a matching `gold_row_id`, while OCR-missed medications may still appear as extra gold rows. Annotation index schema v3 binds the exact source document set, source manifest/sample hashes, one homogeneous OCR producer identity, each per-document runtime result, and the immutable OCR/source projection by SHA-256. `ocr-parser-real`, `prepare-real`, and `finalize-real` share one output lock so they cannot concurrently mutate/read the same annotation snapshot. Rerunning either preparation command adopts only matching crash-window drafts and never rewrites human labels. Finalization repeats the exact source-set/path checks instead of trusting the index, preserves source/license provenance plus exact source manifest/sample hashes, and fails while any OCR node remains unlabeled or image-level gold remains unreviewed:

```sh
docker compose run --rm ocr-parser-data finalize-real \
  --annotations-dir /workspace/browser_ocr/finetune/work/parser-real-holdout/annotations \
  --dataset-id parser-real-holdout-v1 \
  --output-dir /workspace/browser_ocr/finetune/work/parser-real-final \
  --json
```

A missed medication can therefore remain present in `gold_rows` even when OCR produced no corresponding node. This keeps parser-only evaluation distinct from detector/recognizer end-to-end recall.
