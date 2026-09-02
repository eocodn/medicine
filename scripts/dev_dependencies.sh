#!/bin/sh

seed_cache() {
  seed_dir=$1
  target_dir=$2
  seed_id_file=$3
  marker_name=$4

  if [ -z "$seed_dir" ] || [ -z "$seed_id_file" ] || [ ! -d "$seed_dir" ]; then
    return
  fi
  if [ ! -f "$seed_id_file" ]; then
    echo "dependency seed ID is missing: $seed_id_file" >&2
    exit 1
  fi

  seed_id=$(cat "$seed_id_file")
  marker="$target_dir/$marker_name"
  if [ -f "$marker" ] && [ "$(cat "$marker")" = "$seed_id" ]; then
    return
  fi

  mkdir -p "$target_dir"
  cp -R "$seed_dir/." "$target_dir/"
  printf '%s\n' "$seed_id" > "$marker.tmp.$$"
  mv "$marker.tmp.$$" "$marker"
}

prepare_rust_dependencies() {
  CARGO_BUILD_JOBS=${CARGO_BUILD_JOBS:-1}
  export CARGO_BUILD_JOBS
  seed_cache \
    "${MEDICINE_CARGO_HOME_SEED:-}" \
    "${CARGO_HOME:-$HOME/.cargo}" \
    "${MEDICINE_CARGO_SEED_ID_FILE:-}" \
    .medicine-rust-seed-id
}

prepare_gradle_dependencies() {
  seed_cache \
    "${MEDICINE_GRADLE_HOME_SEED:-}" \
    "${GRADLE_USER_HOME:-$HOME/.gradle}" \
    "${MEDICINE_GRADLE_SEED_ID_FILE:-}" \
    .medicine-gradle-seed-id
}
