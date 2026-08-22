#!/usr/bin/env bash
set -euo pipefail

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
target="aarch64-linux-android"
api_level="24"
ndk_version="${MEDICINE_ANDROID_NDK_VERSION:-29.0.14206865}"
sdk_root="${ANDROID_SDK_ROOT:-${ANDROID_HOME:-}}"
if [[ -z "${sdk_root}" ]]; then
    printf 'ANDROID_SDK_ROOT or ANDROID_HOME is required\n' >&2
    exit 1
fi
ndk_home="${ANDROID_NDK_HOME:-${sdk_root}/ndk/${ndk_version}}"
toolchain="${ndk_home}/toolchains/llvm/prebuilt/linux-x86_64/bin"
linker="${toolchain}/aarch64-linux-android${api_level}-clang"
ar="${toolchain}/llvm-ar"
if [[ ! -x "${linker}" || ! -x "${ar}" ]]; then
    printf 'Android NDK toolchain is unavailable under %s\n' "${ndk_home}" >&2
    exit 1
fi

target_dir="${MEDICINE_RUST_TARGET_DIR:-${workspace}/rust/target}"
export CARGO_TARGET_DIR="${target_dir}"
export CARGO_TARGET_AARCH64_LINUX_ANDROID_LINKER="${linker}"
export CC_aarch64_linux_android="${linker}"
export AR_aarch64_linux_android="${ar}"

cargo build \
    --manifest-path "${workspace}/rust/medicine_core/Cargo.toml" \
    --locked \
    --lib \
    --release \
    --target "${target}"
