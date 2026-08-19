#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: check-android-release.sh <tag> <output-dir>}"
output_dir="${2:?usage: check-android-release.sh <tag> <output-dir>}"
workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cd "${workspace}"

./scripts/verify-android-release-version.sh "${tag}"

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'Android release check requires x86_64 Linux, got %s %s\n' \
        "$(uname -s)" "$(uname -m)" >&2
    exit 1
fi

versionName="$(sed -n 's/^versionName=//p' android/release.properties | head -n 1)"
versionCode="$(sed -n 's/^versionCode=//p' android/release.properties | head -n 1)"
if [[ "${MEDICINE_ANDROID_VERSION_NAME:-}" != "${versionName}" ]]; then
    printf 'MEDICINE_ANDROID_VERSION_NAME must match android/release.properties (%s)\n' "${versionName}" >&2
    exit 1
fi
if [[ "${MEDICINE_ANDROID_VERSION_CODE:-}" != "${versionCode}" ]]; then
    printf 'MEDICINE_ANDROID_VERSION_CODE must match android/release.properties (%s)\n' "${versionCode}" >&2
    exit 1
fi

./scripts/android_release_build.sh

source_apk="android/app/build/outputs/apk/release/app-release.apk"
if [[ ! -f "${source_apk}" ]]; then
    printf 'verified Android release APK is missing: %s\n' "${source_apk}" >&2
    exit 1
fi

mkdir -p "${output_dir}"
artifact="${output_dir}/medicine-${tag}-arm64-v8a.apk"
install -m 0644 "${source_apk}" "${artifact}"
cmp --silent "${source_apk}" "${artifact}"
printf 'validated Android release artifact: %s (versionName=%s versionCode=%s)\n' \
    "${artifact}" "${versionName}" "${versionCode}"
