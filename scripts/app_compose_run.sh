#!/bin/sh
set -eu

cargo build --locked --release \
  --manifest-path rust/medicine_core/Cargo.toml \
  --features agentctl \
  --bin medicine-agentctl

exec "$CARGO_TARGET_DIR/release/medicine-agentctl" "$@"
