#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
target=${1:-all}

case "$target" in
  all|core|ui|android) ;;
  *)
    echo "usage: $0 [all|core|ui|android]" >&2
    exit 2
    ;;
esac

ui_dependencies_ready=false

prepare_ui_dependencies() {
  if [ "$ui_dependencies_ready" = true ]; then
    return
  fi
  (
    cd "$root/ui"
    npm ci
  )
  ui_dependencies_ready=true
}

run_core() {
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
  prepare_ui_dependencies
  (
    cd "$root/ui"
    npm run check
  )
}

run_android() {
  prepare_ui_dependencies
  MEDICINE_TSC_BINARY="$root/ui/node_modules/.bin/tsc"
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
