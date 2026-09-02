#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
cargo_target_dir=${CARGO_TARGET_DIR:-"$root/rust/medicine_core/target"}
binary=${MEDICINE_CORE_WEB_BINARY:-"$cargo_target_dir/release/medicine-core-web"}

if [ ! -x "$binary" ]; then
  echo "medicine-core-web is not built; run scripts/build_web.sh first" >&2
  exit 1
fi

exec "$binary" --host 127.0.0.1 --port 8000 "$@"
