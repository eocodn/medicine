#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
python_bin=${MEDICINE_PYTHON_BIN:-python3}

require_env() {
    name=$1
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "$name is required for Android release builds" >&2
        exit 2
    fi
}

for name in \
    MEDICINE_ANDROID_VERSION_CODE \
    MEDICINE_ANDROID_VERSION_NAME \
    MEDICINE_ANDROID_KEYSTORE_PATH \
    MEDICINE_ANDROID_KEYSTORE_PASSWORD \
    MEDICINE_ANDROID_KEY_ALIAS \
    MEDICINE_ANDROID_KEY_PASSWORD
do
    require_env "$name"
done

case "$MEDICINE_ANDROID_VERSION_CODE" in
    *[!0-9]*|'')
        echo "MEDICINE_ANDROID_VERSION_CODE must be a positive integer" >&2
        exit 2
        ;;
esac
if [ "$MEDICINE_ANDROID_VERSION_CODE" -le 0 ]; then
    echo "MEDICINE_ANDROID_VERSION_CODE must be a positive integer" >&2
    exit 2
fi
if [ ! -f "$MEDICINE_ANDROID_KEYSTORE_PATH" ]; then
    echo "MEDICINE_ANDROID_KEYSTORE_PATH does not point to a readable file" >&2
    exit 2
fi

"$workspace/scripts/verify-android-reference-contract.sh"

cd "$workspace/android"
./gradlew --no-daemon --dependency-verification strict \
    verifyReleaseEnvironment testDebugUnitTest lintRelease assembleRelease

apk="$workspace/android/app/build/outputs/apk/release/app-release.apk"
if [ ! -f "$apk" ]; then
    echo "signed release APK was not produced at $apk" >&2
    exit 3
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to verify the release APK}"
aapt="$ANDROID_HOME/build-tools/36.0.0/aapt"
apksigner="$ANDROID_HOME/build-tools/36.0.0/apksigner"
if [ ! -x "$aapt" ]; then
    echo "aapt is unavailable at $aapt" >&2
    exit 3
fi
if [ ! -x "$apksigner" ]; then
    echo "apksigner is unavailable at $apksigner" >&2
    exit 3
fi

badging=$("$aapt" dump badging "$apk")
if ! printf '%s\n' "$badging" | grep -F "package: name='kr.yakbom.app'" >/dev/null; then
    echo "release APK applicationId is not kr.yakbom.app" >&2
    exit 3
fi
if ! printf '%s\n' "$badging" | grep -F "versionCode='$MEDICINE_ANDROID_VERSION_CODE'" >/dev/null; then
    echo "release APK versionCode does not match MEDICINE_ANDROID_VERSION_CODE" >&2
    exit 3
fi
if ! printf '%s\n' "$badging" | grep -F "versionName='$MEDICINE_ANDROID_VERSION_NAME'" >/dev/null; then
    echo "release APK versionName does not match MEDICINE_ANDROID_VERSION_NAME" >&2
    exit 3
fi

certificate_output=$("$apksigner" verify --verbose --print-certs "$apk")
printf '%s\n' "$certificate_output"
expected_fingerprint=$(tr -d '[:space:]' < "$workspace/deploy/android-release-signing-certificate.sha256" | tr '[:upper:]' '[:lower:]')
actual_fingerprint=$(printf '%s\n' "$certificate_output" \
    | sed -n 's/^Signer #1 certificate SHA-256 digest: //p' \
    | head -n 1 \
    | tr '[:upper:]' '[:lower:]')
case "$expected_fingerprint" in
    *[!0-9a-f]*|'')
        echo "pinned Android signing certificate SHA-256 is invalid" >&2
        exit 3
        ;;
esac
if [ "${#expected_fingerprint}" -ne 64 ]; then
    echo "pinned Android signing certificate SHA-256 is invalid" >&2
    exit 3
fi
if [ "$actual_fingerprint" != "$expected_fingerprint" ]; then
    echo "Android release certificate SHA-256 digest does not match pinned identity" >&2
    exit 3
fi
"$python_bin" "$workspace/scripts/verify-ocr-android-artifact.py" "$apk"
"$workspace/scripts/verify-android-reference-contract.sh" --verify-full-artifact
printf 'verified signed Android release: %s\n' "$apk"
