#!/bin/sh
set -eu

PATH="/opt/medicine-ui-tools/node_modules/.bin:$PATH"
export PATH
(cd ui && npm run build)

cargo build --locked --release \
  --manifest-path rust/medicine_core/Cargo.toml \
  --features web \
  --bin medicine-core-web

exec "$CARGO_TARGET_DIR/release/medicine-core-web" --host 0.0.0.0 --port 8000 "$@"
