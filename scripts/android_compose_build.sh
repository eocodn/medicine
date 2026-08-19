#!/bin/sh
set -eu

workspace=$(CDPATH= cd "$(dirname "$0")/.." && pwd)

cd "$workspace/android"
gradle --no-daemon testDebugUnitTest assembleDebug
