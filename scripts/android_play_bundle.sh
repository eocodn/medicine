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
    MEDICINE_ANDROID_UPLOAD_CERT_SHA256 \
    MEDICINE_RELEASE_SOURCE_COMMIT \
    MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL
do
    require_env "$name"
done

upload_cert_sha256=$(printf '%s' "$MEDICINE_ANDROID_UPLOAD_CERT_SHA256" \
    | tr -d '[:space:]:' \
    | tr '[:upper:]' '[:lower:]')
case "$upload_cert_sha256" in
    *[!0-9a-f]*|'')
        echo "MEDICINE_ANDROID_UPLOAD_CERT_SHA256 must be a SHA-256 certificate fingerprint" >&2
        exit 2
        ;;
esac
if [ "${#upload_cert_sha256}" -ne 64 ]; then
    echo "MEDICINE_ANDROID_UPLOAD_CERT_SHA256 must be a SHA-256 certificate fingerprint" >&2
    exit 2
fi

# The host release wrapper owns the authoritative clean-tree checks and builds
# this container from an exact-commit source snapshot. Git metadata is not a
# release input inside the snapshot; this value binds certified provenance to
# the same commit whose tracked bytes were materialized for the build.
source_commit=$(printf '%s' "$MEDICINE_RELEASE_SOURCE_COMMIT" | tr '[:upper:]' '[:lower:]')
case "$source_commit" in
    *[!0-9a-f]*)
        echo "MEDICINE_RELEASE_SOURCE_COMMIT must be an exact Git commit ID" >&2
        exit 2
        ;;
esac
if [ "${#source_commit}" -ne 40 ] && [ "${#source_commit}" -ne 64 ]; then
    echo "MEDICINE_RELEASE_SOURCE_COMMIT must be an exact Git commit ID" >&2
    exit 2
fi

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
if ! command -v keytool >/dev/null 2>&1; then
    echo "keytool is unavailable" >&2
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

java -jar "$BUNDLETOOL_JAR" validate --bundle="$aab"

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

"$workspace/scripts/verify-signed-android-bundle.sh" "$aab" "$upload_cert_sha256"
python3 "$workspace/scripts/verify-no-ocr-android-artifact.py" "$aab"
MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL="$MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL" \
    "$workspace/scripts/verify-android-reference-contract.sh" --verify-full-artifact

mkdir -p "$output_dir"
artifact="$output_dir/yakbom-v${MEDICINE_ANDROID_VERSION_NAME}.aab"
install -m 0644 "$aab" "$artifact"
cmp -s "$aab" "$artifact"
artifact_sha256=$(sha256sum "$artifact" | awk '{print $1}')
checksum="$artifact.sha256"
provenance="$output_dir/yakbom-v${MEDICINE_ANDROID_VERSION_NAME}.provenance.txt"
printf '%s  %s\n' "$artifact_sha256" "$(basename "$artifact")" >"$checksum"
cat >"$provenance" <<EOF
source_commit=$source_commit
artifact_sha256=$artifact_sha256
application_id=$application_id
version_name=$version_name
version_code=$version_code
target_sdk=$target_sdk
upload_certificate_sha256=$upload_cert_sha256
reference_base_url=$MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL
EOF
printf 'verified Android Play bundle: %s (applicationId=%s versionName=%s versionCode=%s targetSdk=%s)\n' \
    "$artifact" "$application_id" "$version_name" "$version_code" "$target_sdk"
printf 'Play bundle provenance: %s (sourceCommit=%s sha256=%s)\n' \
    "$provenance" "$source_commit" "$artifact_sha256"
