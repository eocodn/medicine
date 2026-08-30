#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
image=${MEDICINE_ANDROID_IMAGE:-medicine-android:latest}
artifact_root=${MEDICINE_ARTIFACTS_DIR:-"${HOME}/dev/.artifacts/medicine"}
keystore=${MEDICINE_ANDROID_KEYSTORE_PATH:?MEDICINE_ANDROID_KEYSTORE_PATH is required}

for name in \
    MEDICINE_ANDROID_VERSION_CODE \
    MEDICINE_ANDROID_VERSION_NAME \
    MEDICINE_ANDROID_KEYSTORE_PASSWORD \
    MEDICINE_ANDROID_KEY_ALIAS \
    MEDICINE_ANDROID_KEY_PASSWORD \
    MEDICINE_ANDROID_UPLOAD_CERT_SHA256 \
    MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL
do
    eval "value=\${$name-}"
    if [ -z "$value" ]; then
        echo "$name is required for Android Play releases" >&2
        exit 2
    fi
done

if [ ! -f "$keystore" ]; then
    echo "MEDICINE_ANDROID_KEYSTORE_PATH does not point to a readable file" >&2
    exit 2
fi

source_commit=$("$workspace/scripts/verify-release-source-state.sh" "$workspace")
play_root="$artifact_root/android-play"
output_dir="$play_root/$source_commit"
staging_name=".pending-$source_commit-$$"
staging_dir="$play_root/$staging_name"
source_name=".source-$source_commit-$$"
source_dir="$play_root/$source_name"
lock_dir="$play_root/.locks"
lock_file="$lock_dir/$source_commit.lock"
gradle_cache="$artifact_root/android-gradle-cache"
mkdir -p "$play_root" "$lock_dir" "$gradle_cache"
exec 9>"$lock_file"
if ! flock -n 9; then
    echo "Play release already in progress for source commit $source_commit" >&2
    exit 3
fi
if [ -e "$output_dir" ]; then
    echo "Play release output already exists for source commit $source_commit: $output_dir" >&2
    exit 3
fi
"$workspace/scripts/materialize-release-source.sh" "$workspace" "$source_commit" "$source_dir"
mkdir "$staging_dir"
cleanup() {
    rm -rf "$staging_dir"
    rm -rf "$source_dir"
}
trap cleanup EXIT

docker run --rm \
    -u "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -e GRADLE_USER_HOME=/artifacts/android-gradle-cache \
    -e MEDICINE_ANDROID_VERSION_CODE \
    -e MEDICINE_ANDROID_VERSION_NAME \
    -e MEDICINE_ANDROID_KEYSTORE_PASSWORD \
    -e MEDICINE_ANDROID_KEY_ALIAS \
    -e MEDICINE_ANDROID_KEY_PASSWORD \
    -e MEDICINE_ANDROID_UPLOAD_CERT_SHA256 \
    -e MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL \
    -e MEDICINE_ANDROID_KEYSTORE_PATH=/run/secrets/yakbom-upload.jks \
    -e MEDICINE_RELEASE_SOURCE_COMMIT="$source_commit" \
    -v "$source_dir:/workspace" \
    -v "$artifact_root:/artifacts" \
    -v "$keystore:/run/secrets/yakbom-upload.jks:ro" \
    -w /workspace/android \
    "$image" \
    sh /workspace/scripts/android_play_bundle.sh "/artifacts/android-play/$staging_name"

"$workspace/scripts/verify-release-source-state.sh" "$workspace" "$source_commit" >/dev/null
if [ -e "$output_dir" ]; then
    echo "Play release output appeared before publication for source commit $source_commit: $output_dir" >&2
    exit 3
fi
mv -T "$staging_dir" "$output_dir"
rm -rf "$source_dir"
trap - EXIT
printf 'Play release source verified at %s\n' "$source_commit"
printf 'Play release artifacts: %s\n' "$output_dir"
