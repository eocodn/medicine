#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
output_dir=${1:-"$workspace/dist/play"}

require_env() {
    name=$1
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "$name is required for Android Play bundle builds" >&2
        exit 2
    fi
}

for name in \
    MEDICINE_ANDROID_VERSION_CODE \
    MEDICINE_ANDROID_VERSION_NAME \
    MEDICINE_ANDROID_KEYSTORE_PATH \
    MEDICINE_ANDROID_KEYSTORE_PASSWORD \
    MEDICINE_ANDROID_KEY_ALIAS \
    MEDICINE_ANDROID_KEY_PASSWORD \
    MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL
do
    require_env "$name"
done

if [ -n "${MEDICINE_OCR_ASSETS_DIR-}" ]; then
    echo "OCR is not enabled for the current Android production release" >&2
    exit 2
fi

: "${ANDROID_HOME:?ANDROID_HOME is required to verify the Play bundle}"
: "${BUNDLETOOL_JAR:?BUNDLETOOL_JAR is required to verify the Play bundle}"
if [ ! -r "$BUNDLETOOL_JAR" ]; then
    echo "bundletool is unavailable at $BUNDLETOOL_JAR" >&2
    exit 3
fi
if ! command -v jarsigner >/dev/null 2>&1; then
    echo "jarsigner is unavailable" >&2
    exit 3
fi
if ! command -v python3 >/dev/null 2>&1; then
    echo "python3 is unavailable" >&2
    exit 3
fi

MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL="$MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL" \
    "$workspace/scripts/verify-android-reference-contract.sh"

cd "$workspace/android"
./gradlew --no-daemon --dependency-verification strict testDebugUnitTest lintRelease bundleRelease

aab="$workspace/android/app/build/outputs/bundle/release/app-release.aab"
if [ ! -f "$aab" ]; then
    echo "signed Play AAB was not produced at $aab" >&2
    exit 3
fi

bundle_manifest_value() {
    xpath=$1
    java -jar "$BUNDLETOOL_JAR" dump manifest --bundle="$aab" --xpath="$xpath"
}

application_id=$(bundle_manifest_value "/manifest/@package")
version_code=$(bundle_manifest_value "/manifest/@android:versionCode")
version_name=$(bundle_manifest_value "/manifest/@android:versionName")
target_sdk=$(bundle_manifest_value "/manifest/uses-sdk/@android:targetSdkVersion")

if [ "$application_id" != "kr.yakbom.app" ]; then
    echo "Play AAB applicationId is not kr.yakbom.app: $application_id" >&2
    exit 3
fi
if [ "$version_code" != "$MEDICINE_ANDROID_VERSION_CODE" ]; then
    echo "Play AAB versionCode does not match MEDICINE_ANDROID_VERSION_CODE" >&2
    exit 3
fi
if [ "$version_name" != "$MEDICINE_ANDROID_VERSION_NAME" ]; then
    echo "Play AAB versionName does not match MEDICINE_ANDROID_VERSION_NAME" >&2
    exit 3
fi
if [ "$target_sdk" != "36" ]; then
    echo "Play AAB must targetSdk 36, got $target_sdk" >&2
    exit 3
fi

"$workspace/scripts/verify-signed-android-bundle.sh" "$aab"
python3 "$workspace/scripts/verify-no-ocr-android-artifact.py" "$aab"
MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL="$MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL" \
    "$workspace/scripts/verify-android-reference-contract.sh"

mkdir -p "$output_dir"
artifact="$output_dir/yakbom-v${MEDICINE_ANDROID_VERSION_NAME}.aab"
install -m 0644 "$aab" "$artifact"
cmp -s "$aab" "$artifact"
printf 'verified Android Play bundle: %s (applicationId=%s versionName=%s versionCode=%s targetSdk=%s)\n' \
    "$artifact" "$application_id" "$version_name" "$version_code" "$target_sdk"
