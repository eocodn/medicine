#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
target=${1:-all}
. "$root/scripts/dev_dependencies.sh"

case "$target" in
  all|core|ui|android) ;;
  *)
    echo "usage: $0 [all|core|ui|android]" >&2
    exit 2
    ;;
esac

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
