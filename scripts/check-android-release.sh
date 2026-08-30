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
export MEDICINE_ANDROID_VERSION_NAME="$versionName"
export MEDICINE_ANDROID_VERSION_CODE="$versionCode"

for name in \
    MEDICINE_ANDROID_KEYSTORE_PATH \
    MEDICINE_ANDROID_KEYSTORE_PASSWORD \
    MEDICINE_ANDROID_KEY_ALIAS \
    MEDICINE_ANDROID_KEY_PASSWORD
do
    if [[ -z "${!name:-}" ]]; then
        printf '%s is required for Android developer releases\n' "$name" >&2
        exit 1
    fi
done

./scripts/android_release_build.sh

source_apk="android/app/build/outputs/apk/release/app-release.apk"
if [[ ! -f "${source_apk}" ]]; then
    printf 'release-signed Android APK is missing: %s\n' "${source_apk}" >&2
    exit 1
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to verify the release APK}"
apksigner="${ANDROID_HOME}/build-tools/36.0.0/apksigner"
if [[ ! -x "${apksigner}" ]]; then
    printf 'apksigner is unavailable at %s\n' "${apksigner}" >&2
    exit 1
fi

fingerprint_file="deploy/android-release-signing-certificate.sha256"
if [[ ! -f "${fingerprint_file}" ]]; then
    printf 'Android release signing certificate fingerprint is missing: %s\n' "${fingerprint_file}" >&2
    exit 1
fi
expected_fingerprint="$(tr -d '[:space:]' < "${fingerprint_file}" | tr '[:upper:]' '[:lower:]')"
if [[ ! "${expected_fingerprint}" =~ ^[0-9a-f]{64}$ ]]; then
    printf 'Android release signing certificate fingerprint is invalid\n' >&2
    exit 1
fi
certificate_output="$("${apksigner}" verify --print-certs "${source_apk}")"
actual_fingerprint="$(printf '%s\n' "${certificate_output}" \
    | sed -n 's/^Signer #1 certificate SHA-256 digest: //p' \
    | head -n 1 \
    | tr '[:upper:]' '[:lower:]')"
if [[ "${actual_fingerprint}" != "${expected_fingerprint}" ]]; then
    printf 'Android release certificate SHA-256 digest does not match the pinned signing identity\n' >&2
    exit 1
fi

mkdir -p "${output_dir}"
artifact="${output_dir}/medicine-${tag}-arm64-v8a.apk"
install -m 0644 "${source_apk}" "${artifact}"
cmp --silent "${source_apk}" "${artifact}"
printf 'validated durable-signed Android release artifact: %s (versionName=%s versionCode=%s)\n' \
    "${artifact}" "${versionName}" "${versionCode}"
