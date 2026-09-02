#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)
root=$workspace
. "$root/scripts/dev_dependencies.sh"
prepare_rust_dependencies
prepare_gradle_dependencies
gradle_bin=${MEDICINE_GRADLE_BIN:-./gradlew}

cd "$workspace/android"
"$gradle_bin" --no-daemon --dependency-verification strict testDebugUnitTest assembleDebug
