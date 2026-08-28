#!/bin/sh
set -eu

cargo build --locked --release \
  --manifest-path rust/medicine_core/Cargo.toml \
  --features agentctl \
  --bin medicine-agentctl
export PATH="$CARGO_TARGET_DIR/release:$PATH"

exec python -m unittest discover -s tests -v "$@"
