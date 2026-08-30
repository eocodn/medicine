#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
target=${1:-all}
CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-1}
export CARGO_BUILD_JOBS

case "$target" in
  all|core|ui|android) ;;
  *)
    echo "usage: $0 [all|core|ui|android]" >&2
    exit 2
    ;;
esac

seed_cache() {
  seed_dir=$1
  target_dir=$2
  seed_id_file=$3
  marker_name=$4

  if [ -z "$seed_dir" ] || [ -z "$seed_id_file" ] || [ ! -d "$seed_dir" ]; then
    return
  fi
  if [ ! -f "$seed_id_file" ]; then
    echo "dependency seed ID is missing: $seed_id_file" >&2
    exit 1
  fi

  seed_id=$(cat "$seed_id_file")
  marker="$target_dir/$marker_name"
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$seed_id" ]; then
    return
  fi

  mkdir -p "$target_dir"
  cp -R "$seed_dir/." "$target_dir/"
  printf '%s\n' "$seed_id" > "$marker.tmp.$$"
  mv "$marker.tmp.$$" "$marker"
}

prepare_rust_dependencies() {
  seed_cache \
    "${MEDICINE_CARGO_HOME_SEED:-}" \
    "${CARGO_HOME:-$HOME/.cargo}" \
    "${MEDICINE_CARGO_SEED_ID_FILE:-}" \
    .medicine-rust-seed-id
}

prepare_gradle_dependencies() {
  seed_cache \
    "${MEDICINE_GRADLE_HOME_SEED:-}" \
    "${GRADLE_USER_HOME:-$HOME/.gradle}" \
    "${MEDICINE_GRADLE_SEED_ID_FILE:-}" \
    .medicine-gradle-seed-id
}

run_core() {
  prepare_rust_dependencies
  cargo test --locked \
    --manifest-path "$root/rust/medicine_core/Cargo.toml" \
    --all-targets \
    --all-features
  cargo build --locked --release \
    --manifest-path "$root/rust/medicine_core/Cargo.toml" \
    --features agentctl \
    --bin medicine-agentctl

  cargo_target_dir=${CARGO_TARGET_DIR:-"$root/rust/medicine_core/target"}
  PATH="$cargo_target_dir/release:$PATH"
  export PATH
  cd "$root"
  python -m unittest discover -s tests -v
}

run_ui() {
  (
    cd "$root/ui"
    npm run check
  )
}

run_android() {
  prepare_rust_dependencies
  prepare_gradle_dependencies
  MEDICINE_TSC_BINARY=${MEDICINE_TSC_BINARY:-"$root/ui/node_modules/.bin/tsc"}
  if [ ! -x "$MEDICINE_TSC_BINARY" ]; then
    echo "TypeScript compiler is unavailable: $MEDICINE_TSC_BINARY" >&2
    exit 1
  fi
  export MEDICINE_TSC_BINARY
  (
    cd "$root/android"
    ./gradlew --no-daemon --dependency-verification strict testDebugUnitTest lintDebug assembleDebug
  )
}

case "$target" in
  core)
    run_core
    ;;
  ui)
    run_ui
    ;;
  android)
    run_android
    ;;
  all)
    run_core
    run_ui
    run_android
    ;;
esac
