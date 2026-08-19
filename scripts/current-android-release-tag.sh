#!/usr/bin/env bash
set -euo pipefail

version="$(sed -n 's/^versionName=//p' android/release.properties | head -n 1)"
if [[ -z "${version}" ]]; then
    printf 'failed to read Android release version from android/release.properties\n' >&2
    exit 1
fi

tag="v${version}"
./scripts/verify-android-release-version.sh "${tag}" >&2
printf '%s\n' "${tag}"
