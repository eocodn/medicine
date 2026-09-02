#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
. "$root/scripts/dev_dependencies.sh"
prepare_rust_dependencies
cargo_target_dir=${CARGO_TARGET_DIR:-"$root/rust/medicine_core/target"}

cargo build --locked --release \
  --manifest-path "$root/rust/medicine_core/Cargo.toml" \
  --features agentctl \
  --bin medicine-agentctl
export PATH="$cargo_target_dir/release:$PATH"

cd "$root"
exec python -m unittest discover -s tests -v "$@"
