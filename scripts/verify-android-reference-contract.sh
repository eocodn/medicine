#!/usr/bin/env bash
set -euo pipefail

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "${workspace}"

reference_base_url="${MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL:-$(
    sed -n 's/^val defaultReleaseReferenceUpdateBaseUrl = "\([^"]*\)"/\1/p' \
        android/app/build.gradle.kts | head -n 1
)}"
if [[ -z "${reference_base_url}" || "${reference_base_url}" != https://*/ ]]; then
    printf 'cannot resolve Android release reference base URL\n' >&2
    exit 1
fi

reference_root="$(mktemp)"
trap 'rm -f "${reference_root}"' EXIT
curl --fail --silent --show-error \
    --output "${reference_root}" \
    "${reference_base_url}reference/v2/latest.json"
python ./scripts/verify-reference-contract-root.py --root "${reference_root}"