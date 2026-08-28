#!/bin/sh
set -eu

PATH="/opt/medicine-ui-tools/node_modules/.bin:$PATH"
export PATH
(cd ui && npm run build)

cargo build --locked --release \
  --manifest-path rust/medicine_core/Cargo.toml \
  --features agentctl,web \
  --bin medicine-agentctl \
  --bin medicine-core-web

export MEDICINE_CORE_WEB_BINARY="$CARGO_TARGET_DIR/release/medicine-core-web"
exec "$CARGO_TARGET_DIR/release/medicine-agentctl" "$@"
