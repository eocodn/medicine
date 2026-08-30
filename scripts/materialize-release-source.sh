#!/bin/sh
set -eu

repo=${1:?usage: materialize-release-source.sh <repo> <commit> <destination>}
commit=${2:?usage: materialize-release-source.sh <repo> <commit> <destination>}
destination=${3:?usage: materialize-release-source.sh <repo> <commit> <destination>}

if [ -e "$destination" ]; then
    echo "release source snapshot destination already exists: $destination" >&2
    exit 3
fi

parent=$(dirname "$destination")
archive="$parent/.source-archive-$$.tar"
cleanup() {
    rm -f "$archive"
}
trap cleanup EXIT

git -C "$repo" archive --format=tar --output="$archive" "$commit"
mkdir "$destination"
if ! tar -xf "$archive" -C "$destination"; then
    rm -rf "$destination"
    exit 3
fi

rm -f "$archive"
trap - EXIT