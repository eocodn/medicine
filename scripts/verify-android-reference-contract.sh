#!/usr/bin/env bash
set -euo pipefail

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "${workspace}"

reference_base_url="${MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL:-$(
    sed -n 's/^developmentBaseUrl=//p' android/reference.properties | head -n 1
)}"
if [[ -z "${reference_base_url}" || "${reference_base_url}" != https://*/ ]]; then
    printf 'cannot resolve Android release reference base URL\n' >&2
    exit 1
fi

python_bin="${MEDICINE_PYTHON_BIN:-python}"
if ! command -v "${python_bin}" >/dev/null 2>&1 && [[ ! -x "${python_bin}" ]]; then
    printf 'reference contract verifier Python is unavailable: %s\n' "${python_bin}" >&2
    exit 1
fi

reference_root="$(mktemp)"
trap 'rm -f "${reference_root}"' EXIT
curl --fail --silent --show-error \
    --output "${reference_root}" \
    "${reference_base_url}reference/v2/latest.json"
"${python_bin}" ./scripts/verify-reference-contract-root.py --root "${reference_root}"
