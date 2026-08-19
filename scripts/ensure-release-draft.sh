#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: ensure-release-draft.sh <tag>}"
repository="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY must identify owner/repository}"

if is_draft="$(
    gh release view "${tag}" \
        --repo "${repository}" \
        --json isDraft \
        --jq '.isDraft' \
        2>/dev/null
)"; then
    if [[ "${is_draft}" == "true" ]]; then
        printf 'draft release %s already exists\n' "${tag}"
        exit 0
    fi
    if [[ "${is_draft}" == "false" ]]; then
        printf 'release %s is already published; refusing to reuse it\n' "${tag}" >&2
        exit 1
    fi
    printf 'release %s returned unexpected draft state: %s\n' "${tag}" "${is_draft}" >&2
    exit 1
fi

gh release create "${tag}" \
    --repo "${repository}" \
    --draft \
    --verify-tag \
    --generate-notes \
    --title "${tag}"
