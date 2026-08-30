#!/bin/sh
set -eu

bundle=${1:?usage: verify-signed-android-bundle.sh <bundle.aab>}

if [ ! -f "$bundle" ]; then
    echo "Android bundle is missing: $bundle" >&2
    exit 2
fi
if ! command -v jarsigner >/dev/null 2>&1; then
    echo "jarsigner is unavailable" >&2
    exit 3
fi

report_file=$(mktemp)
trap 'rm -f "$report_file"' EXIT
if ! LC_ALL=C jarsigner -verify -verbose -certs "$bundle" >"$report_file" 2>&1; then
    cat "$report_file" >&2
    exit 3
fi

if ! grep -F "jar verified." "$report_file" >/dev/null; then
    cat "$report_file" >&2
    echo "Android bundle signature was not verified" >&2
    exit 3
fi
if grep -Fi "jar is unsigned" "$report_file" >/dev/null \
    || grep -Fi "unsigned entries" "$report_file" >/dev/null \
    || grep -Eq '^[[:space:]]*\?[[:space:]]' "$report_file"; then
    cat "$report_file" >&2
    echo "Android bundle contains unsigned content" >&2
    exit 3
fi
if ! grep -F ">>> Signer" "$report_file" >/dev/null \
    || ! grep -Eq '^[[:space:]]*s[a-z]*[[:space:]]+[0-9]+' "$report_file"; then
    cat "$report_file" >&2
    echo "Android bundle has no signed payload entries" >&2
    exit 3
fi

printf 'verified signed Android bundle: %s\n' "$bundle"
