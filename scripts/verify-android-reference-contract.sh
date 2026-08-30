#!/usr/bin/env bash
set -euo pipefail

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "${workspace}"

verify_full_artifact=false
case "${1:-}" in
    "") ;;
    --verify-full-artifact) verify_full_artifact=true ;;
    *)
        printf 'usage: %s [--verify-full-artifact]\n' "$0" >&2
        exit 2
        ;;
esac

reference_base_url="${MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL:-$(
    sed -n 's/^val defaultReleaseReferenceUpdateBaseUrl = "\([^"]*\)"/\1/p' \
        android/app/build.gradle.kts | head -n 1
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

verification_dir="$(mktemp -d)"
reference_root="${verification_dir}/latest.json"
reference_full="${verification_dir}/full.sqlite.gz"
trap 'rm -rf "${verification_dir}"' EXIT
curl --fail --silent --show-error \
    --output "${reference_root}" \
    "${reference_base_url}reference/v2/latest.json"
"${python_bin}" ./scripts/verify-reference-contract-root.py --root "${reference_root}"

if [[ "${verify_full_artifact}" == true ]]; then
    full_key="$(
        "${python_bin}" ./scripts/verify-reference-contract-root.py \
            --root "${reference_root}" \
            --print-full-key
    )"
    if [[ "${full_key}" != reference/v2/contracts/*/full/*.sqlite.gz ]]; then
        printf 'signed Android reference full artifact key is invalid: %s\n' "${full_key}" >&2
        exit 1
    fi
    curl --fail --show-error --progress-bar \
        --output "${reference_full}" \
        "${reference_base_url}${full_key}"
    "${python_bin}" ./scripts/verify-reference-contract-root.py \
        --root "${reference_root}" \
        --full-artifact "${reference_full}"
fi
