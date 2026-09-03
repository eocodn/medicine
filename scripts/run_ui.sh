#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
. "$root/scripts/dev_dependencies.sh"
prepare_rust_dependencies
cargo_target_dir=${CARGO_TARGET_DIR:-"$root/rust/medicine_core/target"}
PATH="/opt/medicine-ui-tools/node_modules/.bin:$PATH"
export PATH
(cd "$root/ui" && npm run build)

cargo build --locked --release \
  --manifest-path "$root/rust/medicine_core/Cargo.toml" \
  --features agentctl-web \
  --bin medicine-agentctl \
  --bin medicine-core-web

export MEDICINE_CORE_WEB_BINARY="$cargo_target_dir/release/medicine-core-web"
exec "$cargo_target_dir/release/medicine-agentctl" "$@"
