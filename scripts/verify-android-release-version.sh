#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: verify-android-release-version.sh <tag>}"
properties="android/release.properties"

if [[ "${tag}" != v* ]]; then
    printf 'release tag must start with v: %s\n' "${tag}" >&2
    exit 1
fi
if [[ ! -f "${properties}" ]]; then
    printf 'Android release version file is missing: %s\n' "${properties}" >&2
    exit 1
fi

version_name="$(sed -n 's/^versionName=//p' "${properties}" | head -n 1)"
version_code="$(sed -n 's/^versionCode=//p' "${properties}" | head -n 1)"
if [[ -z "${version_name}" || -z "${version_code}" ]]; then
    printf 'Android release version file must define versionName and versionCode\n' >&2
    exit 1
fi
if [[ ! "${version_code}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'Android release versionCode must be a positive integer: %s\n' "${version_code}" >&2
    exit 1
fi

tag_version="${tag#v}"
if [[ "${tag_version}" != "${version_name}" ]]; then
    printf 'release tag %s does not match Android release version %s\n' \
        "${tag}" "${version_name}" >&2
    exit 1
fi

printf 'release version verified: %s (versionCode=%s)\n' "${tag}" "${version_code}"
