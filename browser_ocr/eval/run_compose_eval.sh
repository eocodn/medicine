#!/bin/sh
set -eu

work_dir=$(mktemp -d)
trap 'rm -rf "$work_dir"' EXIT

node browser_ocr/fetch_assets.mjs "$work_dir/downloads"
node browser_ocr/export_runtime.mjs "$work_dir/downloads" "$work_dir/runtime"
exec node browser_ocr/eval/run_eval.mjs \
  --runtime-dir "$work_dir/runtime" \
  --corpus browser_ocr/eval/corpus/manifest.json \
  --json \
  "$@"
