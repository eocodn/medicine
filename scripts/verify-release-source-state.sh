#!/bin/sh
set -eu

repo=${1:?usage: verify-release-source-state.sh <repo> [expected-head]}
expected_head=${2:-}

if ! git -C "$repo" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "release source is not a Git worktree: $repo" >&2
    exit 2
fi

head=$(git -C "$repo" rev-parse HEAD)
if [ -n "$expected_head" ] && [ "$head" != "$expected_head" ]; then
    echo "release source HEAD changed during build: expected $expected_head got $head" >&2
    exit 3
fi

status=$(git -C "$repo" status --porcelain --untracked-files=all)
if [ -n "$status" ]; then
    echo "release source worktree is not clean" >&2
    printf '%s\n' "$status" >&2
    exit 3
fi

printf '%s\n' "$head"