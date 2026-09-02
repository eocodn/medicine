#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
. "$root/scripts/dev_dependencies.sh"
prepare_rust_dependencies
PATH="/opt/medicine-ui-tools/node_modules/.bin:$PATH"
export PATH

(cd "$root/ui" && npm run build)

cargo build --locked --release \
  --manifest-path "$root/rust/medicine_core/Cargo.toml" \
  --features web \
  --bin medicine-core-web
