#!/usr/bin/env bash
set -euo pipefail

tag="${1:?usage: check-android-release.sh <tag> <output-dir>}"
output_dir="${2:?usage: check-android-release.sh <tag> <output-dir>}"
workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
python_bin=${MEDICINE_PYTHON_BIN:-python3}
cd "${workspace}"

./scripts/verify-android-release-version.sh "${tag}"

if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
    printf 'Android release check requires x86_64 Linux, got %s %s\n' \
        "$(uname -s)" "$(uname -m)" >&2
    exit 1
fi

versionName="$(sed -n 's/^versionName=//p' android/release.properties | head -n 1)"
versionCode="$(sed -n 's/^versionCode=//p' android/release.properties | head -n 1)"

./scripts/verify-android-reference-contract.sh
(
    cd android
    env \
        -u MEDICINE_ANDROID_VERSION_CODE \
        -u MEDICINE_ANDROID_VERSION_NAME \
        -u MEDICINE_ANDROID_KEYSTORE_PATH \
        -u MEDICINE_ANDROID_KEYSTORE_PASSWORD \
        -u MEDICINE_ANDROID_KEY_ALIAS \
        -u MEDICINE_ANDROID_KEY_PASSWORD \
        ./gradlew --no-daemon --dependency-verification strict \
            testDebugUnitTest lintRelease assembleRelease
)

source_apk="android/app/build/outputs/apk/release/app-release-unsigned.apk"
if [[ ! -f "${source_apk}" ]]; then
    printf 'unsigned Android release APK is missing: %s\n' "${source_apk}" >&2
    exit 1
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to verify the unsigned release APK}"
aapt="${ANDROID_HOME}/build-tools/36.0.0/aapt"
if [[ ! -x "${aapt}" ]]; then
    printf 'aapt is unavailable at %s\n' "${aapt}" >&2
    exit 1
fi

badging="$("${aapt}" dump badging "${source_apk}")"
if ! printf '%s\n' "${badging}" | grep -F "package: name='kr.yakbom.app'" >/dev/null; then
    printf 'unsigned release APK applicationId is not kr.yakbom.app\n' >&2
    exit 1
fi
if ! printf '%s\n' "${badging}" | grep -F "versionCode='${versionCode}'" >/dev/null; then
    printf 'unsigned release APK versionCode does not match android/release.properties\n' >&2
    exit 1
fi
if ! printf '%s\n' "${badging}" | grep -F "versionName='${versionName}'" >/dev/null; then
    printf 'unsigned release APK versionName does not match android/release.properties\n' >&2
    exit 1
fi
"${python_bin}" "${workspace}/scripts/verify-ocr-android-artifact.py" "${source_apk}"
./scripts/verify-android-reference-contract.sh --verify-full-artifact

mkdir -p "${output_dir}"
artifact="${output_dir}/medicine-${tag}-arm64-v8a-unsigned.apk"
install -m 0644 "${source_apk}" "${artifact}"
cmp --silent "${source_apk}" "${artifact}"
printf 'validated unsigned Android release candidate: %s (versionName=%s versionCode=%s)\n' \
    "${artifact}" "${versionName}" "${versionCode}"
