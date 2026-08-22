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

The runtime parser is intentionally not implemented here. The production direction
is a learned document/KIE model trained and evaluated against the contracts in this
package. OCR observations remain independent inputs so parser model changes do not
change detector/recognizer provenance.

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

The canonical OCR corpus can materialize parser/KIE data from the same documents used for detector and recognizer experiments. `views/parsing/samples.jsonl` exposes box text/geometry, semantic roles, association groups, and positive `same_medication` edges for learned models. `views/parsing/oracle-{train,val,test}.json` and `oracle-manifest.json` use the parser corpus contract for oracle-box evaluation. Product labels such as `약명` are represented explicitly as `product_label` evidence instead of being reconstructed by text-specific fixture logic. This view is intentionally independent of whether the eventual parser is deterministic or learned.

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

## Learned-parser dataset contract

Learned parser training uses a canonical contract that separates the **observed OCR graph** from authoritative labels and image-level medication gold:

- `observation.kind`: `oracle`, deterministic `synthetic_ocr`, or actual `runtime_ocr`;
- each observed node carries text, confidence, an ordered convex four-point polygon bounded by the declared image dimensions, and zero or more matched truth-region ids;
- `label_status=labeled` carries a role/group target, while split/merge observations spanning incompatible truth roles/groups are `ambiguous` and are masked from supervised role/relation loss;
- unmatched detector boxes are explicit `other` negatives;
- relations contain both `same_medication` positives and `different_medication` hard negatives for dose, frequency, duration, instruction, and medication-associated schedule nodes;
- `gold_rows` are image-level truth and do not depend on which regions OCR happened to observe. Synthetic rows carry deterministic, evidence-backed schedule/meal/route/PRN semantics when the associated instruction text proves them.
- `gold_rows_reviewed` separates an explicitly reviewed empty image-level gold set from the default unfinished annotation state.
- real documents preserve a pseudonymous lowercase-ASCII `source_id` plus the allowlisted de-identified-source `license_id` (`private-deidentified`) in the finalized parser sample; provenance fields are identifiers, not free-form text.

Parser dataset schema v3 binds both `samples.jsonl` and manifest metadata by SHA-256 and emits/accepts only strict standards-compliant JSON. One shared artifact-validation boundary is used by both writer and loader: completed datasets must be non-empty, every document carries a typed `source_binding` for its synthetic truth or real source dataset, manifest metadata must agree with the actual document split/observation/source contract, and builder metadata such as truth/source hashes, seeds, and runtime OCR producer identity must agree with those document-bound identities. Manifest metadata itself is schema-constrained: only builder/runtime/source identity fields are accepted, builder ids are allowlisted, and arbitrary notes or patient-identifying fields are rejected on both write and load. Observation profiles are also typed by kind: oracle and deterministic synthetic OCR profiles have exact producer/hash/seed fields, while runtime OCR retains its exact pinned runtime profile, so none of these profiles can act as an arbitrary free-text side channel. Gold draft fields are type/domain checked (finite positive dose values, bounded integer frequency/duration, enum meal/route values, boolean PRN, HH:MM schedules, and ISO dates) and must also satisfy product cross-field invariants: PRN cannot carry a fixed frequency/schedule, an explicit frequency must match explicit schedule count, and explicit date/duration combinations cannot contradict each other. Complete samples must contain the exact relation matrix implied by every labeled product/field pair, and every positively labeled medication association group must have a corresponding `gold_row_id`; medication product/product-label/dose/frequency/duration nodes cannot use the reserved document-level association group. Extra image-level gold rows remain allowed for medications that OCR missed. Real-deidentified image SHA-256 values must remain unique even when an existing parser artifact is loaded, not only during source ingestion or writing, so val/test leakage cannot be reintroduced by repackaging. Runtime observations are revalidated by the dataset artifact contract itself and every `runtime_ocr` document in one dataset must resolve to the same image-independent OCR producer identity. Dataset outputs also carry an authoritative completed/running state and exclusive writer lock: normal loading requires the authoritative state to be `completed` and bound to the persisted dataset hashes; an exact rerun reuses the completed dataset, while a different seed/source/split/content profile is rejected instead of replacing it. Synthetic data may be used for train/validation/test; `real_deidentified` data is holdout-only and is rejected from `train`.

Unified corpus materialization creates these parser datasets automatically:

- `parsing/datasets/oracle/`
- `parsing/datasets/train-oracle/`
- `parsing/datasets/train-synthetic-ocr/`
- `parsing/datasets/val-synthetic-ocr/`
- `parsing/datasets/test-synthetic-ocr/`

The deterministic synthetic-OCR producer starts from canonical tight `natural_text_polygon` geometry, perturbs OCR observations (drop/split/merge/jitter/text/confidence/order/noise), and then labels them through the same tight-geometry alignment used for runtime OCR. It does not copy truth labels onto corrupted boxes blindly.

After a detector/recognizer candidate is explicitly frozen, use `ocr-parser-synthetic-runtime` to create the primary noisy parser view. This heavy batch is intentionally separate from ordinary corpus materialization: it runs every unified synthetic page through the exact selected full-document detector/orientation/crop/recognizer producer, checkpoints each persisted `result.json`, verifies image/source/producer hashes on resume, and then builds GT-aligned `runtime_ocr` datasets independently for every present train/val/test split. Runtime OCR preserves the original image hash and raw dimensions for provenance but exposes upright canonical dimensions/polygons to the parser; synthetic truth polygons are transformed into that same coordinate frame before alignment. OCR text and boxes are observations only; authoritative semantic roles, associations, and `gold_rows` still come from the synthetic truth file, so OCR mistakes are not promoted to labels.

```sh
docker compose run --rm ocr-parser-synthetic-runtime \
  --corpus-manifest /artifacts/ocr/corpora/unified-v6/manifest.json \
  --truth-samples /artifacts/ocr/corpora/unified-v6/views/parsing/samples.jsonl \
  --baseline-result /artifacts/ocr/training/selected/baseline-result.json \
  --output-dir /artifacts/parser/synthetic-runtime-v6 \
  --json
```

The output keeps raw runtime OCR snapshots under `runtime/<document-id>/` and strict parser datasets under `datasets/{train,val,test}/`. Reusing the output with a changed corpus, truth file, detector/recognizer producer, or mutated completed runtime result fails rather than mixing observation distributions.

## Sparse document graph for the learned parser

`document_graph.py` converts any strict parser document into the model-facing full-document graph without introducing document-template rules. Every OCR node keeps its recognized text, confidence and polygon; the input feature vector combines bounded normalized geometry/character composition with a fixed-size signed hash of Korean/ASCII character 1–3-grams, so new drug names do not require a vocabulary lookup. Each OCR node receives at most `K` nearest spatial neighbors with relative `dx/dy`, distance, row/column overlap and relative-size edge features. A dedicated page token is connected in both directions to every OCR node so the subsequent encoder can exchange global context without quadratic all-node attention. Ambiguous OCR merges remain graph nodes and therefore remain available as context, but their node targets are masked; unmatched OCR boxes remain supervised `other` hard negatives.

The initial mobile encoder design budget is explicit in `GraphEncoderSpec`: hidden size 96, two message-passing layers, 12 local neighbors and a 64-unit pair head. `graph_encoder_parameter_count()` counts the planned input projection, shared self/neighbor/edge message projections, role head and product↔field association head. The default design is intentionally far below one million learned parameters; model size is allowed to grow only after held-out evidence demonstrates a need. Original-page pixel features are deliberately outside this graph contract and remain the separate visual-feature research axis.

`graph_encoder_paddle.py` implements that contract as a trainable sparse message-passing network. Each layer projects the target state, neighboring node state and relative edge feature separately, mean-aggregates incoming sparse messages (including the page-token links), and produces a contextual hidden state. Separate heads predict the nine parser node roles and `same_medication` product↔field association logits. The default 96×2 configuration has 61,930 learned parameters by both the static budget formula and the Paddle parameter inventory. A dedicated Paddle-backed test service verifies forward/backward behavior and, critically, a toy case where the exact same `"30"` node at the exact same coordinates must be classified as medication duration vs receipt noise solely from different neighboring OCR context.

`ocr-parser-train` trains this model document-at-a-time rather than padding many page graphs into one large batch, which keeps peak memory bounded and makes OOM behavior easier to reason about. Multiple training observation views are mixed only through explicit repeatable `--train-manifest`/`--train-weight` pairs. The sampler normalizes those weights, deterministically assigns a fixed number of document updates per epoch, and records each view's realized step count in the checkpoint so a nominal mix is auditable instead of being an accidental consequence of dataset size. The initial runtime-first recipe is 60% frozen runtime OCR, 20% deterministic synthetic OCR and 20% oracle observations; these are experiment defaults rather than hard-coded model behavior.

Every epoch writes an atomic model+optimizer checkpoint and validation metrics before advancing the authoritative training state; an interrupted run resumes from the last complete checkpoint and rejects dataset, implementation, architecture, hyperparameter, or view-weight drift. Validation model selection uses a precision-favoring association F0.5 score together with role macro-F1 so a degenerate model that simply suppresses all medication associations cannot win. Relation positive weighting is derived from the training graph and capped explicitly.

`graph_decode.py` is the fail-closed boundary from learned graph scores to medication rows. A row is emitted only for a product node that clears both a probability threshold and a role margin. A field is attached only when its learned role is confident and the learned product↔field association clears both an absolute threshold and a best-vs-second-product margin; otherwise the field remains unresolved rather than being borrowed from the nearest plausible medication. Deterministic code after that boundary only normalizes typed dose/frequency/duration/instruction values and enforces cross-field invariants such as PRN vs fixed schedules. Every exact value keeps the OCR node id that proved it. `evaluate_parser_document()` adapts strict parser gold to the existing evidence-aware safety metrics, so a high-confidence wrong-row association remains visible as `cross_medication_associations` even when the copied numeric value happens to match.

`ocr-parser-eval-model` binds evaluation to the completed training state, selected checkpoint hash, strict dataset fingerprints, decoder thresholds and evaluation implementation. It evaluates one document at a time, atomically checkpoints each prediction+metric record, and resumes from the last verified document after interruption. Train documents are always rejected. Test documents are also rejected by default and require the explicit `--allow-test` flag, so routine validation cannot casually consume the locked holdout. Aggregation uses the same evidence-aware safety metrics as the parser contract; unresolved fields do not count as false exact claims, while invented values, unproven evidence and cross-medication associations remain release-blocking.

```sh
docker compose run --rm ocr-parser-train \
  --train-manifest /workspace/path/to/views/parsing/datasets/train-oracle/manifest.json \
  --train-weight 0.2 \
  --train-manifest /workspace/path/to/views/parsing/datasets/train-synthetic-ocr/manifest.json \
  --train-weight 0.2 \
  --train-manifest /artifacts/parser/synthetic-runtime-v6/datasets/train/manifest.json \
  --train-weight 0.6 \
  --val-manifest /artifacts/parser/synthetic-runtime-v6/datasets/val/manifest.json \
  --run-dir /artifacts/parser/models/sparse-graph-v1 \
  --json
```

After training, validation can be run without unlocking the test split:

```sh
docker compose run --rm ocr-parser-eval-model \
  --model-result /artifacts/parser/models/sparse-graph-v1/result.json \
  --dataset-manifest /artifacts/parser/synthetic-runtime-v6/datasets/val/manifest.json \
  --output-dir /artifacts/parser/evaluations/sparse-graph-v1-runtime-val \
  --json
```

Only after the candidate is frozen should a test manifest be evaluated, with `--allow-test` recorded in the evaluation profile.

Use the dataset Agent Control service directly when needed. Writable OCR/parser services mount host `~/dev/artifacts/medicine` at `/artifacts`; create that host directory as your normal user before the first run (`mkdir -p ~/dev/artifacts/medicine`). Set `MEDICINE_ARTIFACTS_DIR=/absolute/path` to override the host root without changing container paths. Compose refuses to auto-create the bind source so Docker cannot leave a root-owned artifact directory:

```sh
docker compose run --rm ocr-parser-data validate \
  --manifest /workspace/path/to/parser-dataset/manifest.json --json

docker compose run --rm ocr-parser-data build-runtime \
  --truth-samples /workspace/path/to/views/parsing/samples.jsonl \
  --results-root /artifacts/ocr/runtime/full-document-results \
  --output-dir /artifacts/parser/datasets/runtime-val \
  --dataset-id parser-runtime-val --split val --json
```

## Real de-identified prescription photos

Private prescription photos stay outside Git. Ingestion accepts only an external `real_deidentified` source manifest whose documents are already de-identified, use pseudonymous lowercase ids, use the document id as the image filename stem, and declare `contains_patient_data=false`. Only `val` and `test` are accepted, and image SHA-256 values must be unique across the source manifest so the same photo cannot leak between validation and test under different pseudonyms.

The GPU `ocr-parser-real` service sends every photo through the exact selected full-document detector/crop/recognizer path and writes runtime OCR results plus annotation drafts. Runtime observations use an exact metadata allowlist and require pinned detector/recognizer/config/implementation SHA-256 metadata plus hashes of the exact loaded detector ONNX/config bytes, the actual PaddleOCR inference Python source tree, the dictionary selected by the recognizer config, and a canonical fingerprint of the Python inference runtime environment. That runtime fingerprint includes installed Debian package versions and content hashes for the native shared-library set that backs the OCR stack, plus the actual native payload bytes installed by inference-critical Python wheels such as PaddlePaddle, ONNX Runtime, OpenCV, NumPy, and NVIDIA CUDA/cuDNN packages. Native file hashes are cached only while their size/mtime/ctime snapshot remains unchanged, so repeated documents avoid rehashing immutable binaries while binary replacement still changes producer identity. GPU profiles additionally bind the runtime-visible device selector plus Paddle-reported CUDA/cuDNN/device/capability identity and available NVIDIA driver identity. Model identifiers are bounded ASCII ids rather than free-form metadata. Arbitrary runtime-profile fields are rejected instead of being copied into de-identified artifacts. The detector runtime additionally verifies that extracted ONNX/config bytes match the pinned archive before inference. A batch checkpoint binds one normalized OCR producer identity, so an interrupted batch cannot resume with a different detector/recognizer/PaddleOCR/runtime environment and mix producers. The standalone `build-runtime` path and the strict parser artifact contract enforce the same one-producer-per-dataset invariant.

```sh
docker compose run --rm \
  -v /absolute/deidentified-corpus:/real:ro \
  ocr-parser-real \
  --source-manifest /real/manifest.json \
  --baseline-result /artifacts/ocr/training/selected/baseline-result.json \
  --output-dir /artifacts/parser/real-holdout \
  --json
```

Human annotation assigns node roles/groups and image-level `gold_rows`, then explicitly sets `gold_rows_reviewed=true` even when the reviewed image contains zero medication rows. A reviewed-empty gold set is accepted only when no OCR node has been positively assigned to a medication association group; each observed medication group must use a matching `gold_row_id`, while OCR-missed medications may still appear as extra gold rows. Annotation index schema v3 binds the exact source document set, source manifest/sample hashes, one homogeneous OCR producer identity, each per-document runtime result, and the immutable OCR/source projection by SHA-256. `ocr-parser-real`, `prepare-real`, and `finalize-real` share one output lock so they cannot concurrently mutate/read the same annotation snapshot. Rerunning either preparation command adopts only matching crash-window drafts and never rewrites human labels. Finalization repeats the exact source-set/path checks instead of trusting the index, preserves source/license provenance plus exact source manifest/sample hashes, and fails while any OCR node remains unlabeled or image-level gold remains unreviewed:

```sh
docker compose run --rm ocr-parser-data finalize-real \
  --annotations-dir /artifacts/parser/real-holdout/annotations \
  --dataset-id parser-real-holdout-v1 \
  --output-dir /artifacts/parser/real-final \
  --json
```

A missed medication can therefore remain present in `gold_rows` even when OCR produced no corresponding node. This keeps parser-only evaluation distinct from detector/recognizer end-to-end recall.
