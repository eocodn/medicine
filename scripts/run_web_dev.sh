#!/bin/sh
set -eu

root=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

sh "$root/scripts/build_web.sh"
exec sh "$root/scripts/run_web.sh" --host 0.0.0.0 --port 8000 "$@"
