#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: publish-release.sh <tag> <asset-dir>}"
asset_dir="${2:?usage: publish-release.sh <tag> <asset-dir>}"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must identify owner/repository}"

shopt -s nullglob
apks=("${asset_dir}"/*.apk)
checksum_file="${asset_dir}/SHA256SUMS"
if (( ${#apks[@]} != 1 )) || [[ ! -f "${checksum_file}" ]]; then
    printf 'release assets are incomplete under %s\n' "${asset_dir}" >&2
    exit 1
fi

if is_draft="$(
    gh release view "${tag}" \
        --repo "${repository}" \
        --json isDraft \
        --jq '.isDraft' \
        2>/dev/null
)"; then
    if [[ "${is_draft}" == "false" ]]; then
        printf 'release %s is already published; leaving it unchanged\n' "${tag}"
        exit 0
    fi
    if [[ "${is_draft}" != "true" ]]; then
        printf 'release %s returned unexpected draft state: %s\n' "${tag}" "${is_draft}" >&2
        exit 1
    fi
else
    gh release create "${tag}" \
        --repo "${repository}" \
        --draft \
        --verify-tag \
        --generate-notes \
        --title "${tag}"
fi

# The draft is the retry boundary: failed uploads stay invisible and can be
# resumed, while an already-published release is treated as terminal.
gh release upload "${tag}" \
    "${apks[@]}" \
    "${checksum_file}" \
    --repo "${repository}" \
    --clobber

# Reference publication is independent from GitHub Release publication. Fetch
# and verify the live signed root at the final public state-change boundary so
# a contract retirement after earlier release checks fails closed.
script_dir=$(CDPATH= cd "$(dirname "$0")" && pwd)
"${script_dir}/verify-android-reference-contract.sh"

gh release edit "${tag}" \
    --repo "${repository}" \
    --draft=false
