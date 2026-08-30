#!/bin/sh
set -eu

bundle=${1:?usage: verify-signed-android-bundle.sh <bundle.aab> <expected-cert-sha256>}
expected_cert_sha256=${2:?usage: verify-signed-android-bundle.sh <bundle.aab> <expected-cert-sha256>}

normalize_sha256() {
    printf '%s' "$1" | tr -d '[:space:]:' | tr '[:upper:]' '[:lower:]'
}

expected_cert_sha256=$(normalize_sha256 "$expected_cert_sha256")
case "$expected_cert_sha256" in
    *[!0-9a-f]*|'')
        echo "expected Android upload certificate SHA-256 must be 64 hexadecimal characters" >&2
        exit 2
        ;;
esac
if [ "${#expected_cert_sha256}" -ne 64 ]; then
    echo "expected Android upload certificate SHA-256 must be 64 hexadecimal characters" >&2
    exit 2
fi

if [ ! -f "$bundle" ]; then
    echo "Android bundle is missing: $bundle" >&2
    exit 2
fi
if ! command -v jarsigner >/dev/null 2>&1; then
    echo "jarsigner is unavailable" >&2
    exit 3
fi
if ! command -v keytool >/dev/null 2>&1; then
    echo "keytool is unavailable" >&2
    exit 3
fi

verification_dir=$(mktemp -d)
report_file="$verification_dir/jarsigner.txt"
cert_file="$verification_dir/keytool.txt"
trap 'rm -rf "$verification_dir"' EXIT
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

if ! LC_ALL=C keytool -printcert -jarfile "$bundle" >"$cert_file" 2>&1; then
    cat "$cert_file" >&2
    exit 3
fi
actual_cert_sha256=$(sed -n 's/^[[:space:]]*SHA256:[[:space:]]*//p' "$cert_file" | head -n 1)
actual_cert_sha256=$(normalize_sha256 "$actual_cert_sha256")
if [ "${#actual_cert_sha256}" -ne 64 ]; then
    cat "$cert_file" >&2
    echo "Android bundle signer certificate SHA-256 is unavailable" >&2
    exit 3
fi
if [ "$actual_cert_sha256" != "$expected_cert_sha256" ]; then
    echo "Android bundle signer certificate SHA-256 does not match the reviewed Play upload certificate" >&2
    echo "expected: $expected_cert_sha256" >&2
    echo "actual:   $actual_cert_sha256" >&2
    exit 3
fi

printf 'verified signed Android bundle: %s (uploadCertSha256=%s)\n' \
    "$bundle" "$actual_cert_sha256"
