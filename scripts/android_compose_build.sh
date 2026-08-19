#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

cd "$workspace"
if [ -n "${MEDICINE_MOBILE_DB:-}" ] || [ -n "${MEDICINE_MOBILE_MANIFEST:-}" ]; then
  if [ -z "${MEDICINE_MOBILE_DB:-}" ] || [ -z "${MEDICINE_MOBILE_MANIFEST:-}" ]; then
    echo 'Both MEDICINE_MOBILE_DB and MEDICINE_MOBILE_MANIFEST must be set together' >&2
    exit 2
  fi
  echo 'Skipping mobile database build; using prebuilt reference inputs'
else
  # Keep the Android path on the dependency-light mobile module. The canonical
  # CLI imports ETL dependencies which are intentionally absent from this image.
  PYTHONPATH="$workspace" python3.12 -c 'import json; from medicine_canonical.mobile import build_mobile_database; print(json.dumps(build_mobile_database("data/db/canonical.sqlite", "data/db/mobile.sqlite"), ensure_ascii=False))'
fi

cd "$workspace/android"
gradle --no-daemon testDebugUnitTest assembleDebug
