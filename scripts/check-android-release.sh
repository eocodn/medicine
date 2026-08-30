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

./scripts/verify-android-reference-contract.sh

versionName="$(sed -n 's/^versionName=//p' android/release.properties | head -n 1)"
versionCode="$(sed -n 's/^versionCode=//p' android/release.properties | head -n 1)"
(
    cd android
    ./gradlew --no-daemon --dependency-verification strict testDebugUnitTest lintDebug assembleDebug
)

source_apk="android/app/build/outputs/apk/debug/app-debug.apk"
if [[ ! -f "${source_apk}" ]]; then
    printf 'debug-signed Android APK is missing: %s\n' "${source_apk}" >&2
    exit 1
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to verify the debug APK}"
aapt="${ANDROID_HOME}/build-tools/36.0.0/aapt"
apksigner="${ANDROID_HOME}/build-tools/36.0.0/apksigner"
if [[ ! -x "${aapt}" ]]; then
    printf 'aapt is unavailable at %s\n' "${aapt}" >&2
    exit 1
fi
if [[ ! -x "${apksigner}" ]]; then
    printf 'apksigner is unavailable at %s\n' "${apksigner}" >&2
    exit 1
fi

badging="$("${aapt}" dump badging "${source_apk}")"
if ! printf '%s\n' "${badging}" | grep -F "versionCode='${versionCode}'" >/dev/null; then
    printf 'debug APK versionCode does not match android/release.properties\n' >&2
    exit 1
fi
if ! printf '%s\n' "${badging}" | grep -F "versionName='${versionName}'" >/dev/null; then
    printf 'debug APK versionName does not match android/release.properties\n' >&2
    exit 1
fi

"${apksigner}" verify --verbose --print-certs "${source_apk}"
python "${workspace}/scripts/verify-no-ocr-android-artifact.py" "${source_apk}"

mkdir -p "${output_dir}"
artifact="${output_dir}/medicine-${tag}-arm64-v8a.apk"
install -m 0644 "${source_apk}" "${artifact}"
cmp --silent "${source_apk}" "${artifact}"
printf 'validated debug-signed Android release artifact: %s (versionName=%s versionCode=%s)\n' \
    "${artifact}" "${versionName}" "${versionCode}"
