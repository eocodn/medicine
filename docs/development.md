# Medicine development guide

This document is for repository contributors and operators. The root `README.md` is intentionally reserved for end users.

## Standard development environment

Local development uses the repository's single standard `Dockerfile.dev` image. Source is mounted into the container; mutable build and dependency caches live outside the source tree.

```bash
export LOCAL_UID="$(id -u)"
export LOCAL_GID="$(id -g)"
export MEDICINE_DEV_HOME="${MEDICINE_DEV_HOME:-$HOME/dev/.artifacts/medicine/dev-home/$(basename "$PWD")}"
mkdir -p "$MEDICINE_DEV_HOME"

docker compose run --rm --build dev sh /workspace/scripts/check.sh core
docker compose run --rm dev sh /workspace/scripts/check.sh ui
docker compose run --rm dev sh /workspace/scripts/check.sh android
```

`scripts/check.sh`, `scripts/run_app.sh`, `scripts/run_ui.sh`, `scripts/build_web.sh`, `scripts/run_web.sh`, and `scripts/android_debug_build.sh` are repository-native entry points. Compose uses the same scripts instead of defining a separate development workflow. `scripts/check.sh all` runs the core, UI, and Android checks in sequence.

The core Rust test profile omits debug information so its integration-test links remain bounded under constrained development executors. The subsequent release build still compiles bundled SQLite at `-O3`, so allocate 4 GiB when an external executor applies a workload memory ceiling to `scripts/check.sh core`.

The Android check bounds Gradle to two workers and runs Kotlin compilation in-process so its memory use does not scale with the host CPU count. Allocate 4 GiB when an external executor applies a workload memory ceiling to `scripts/check.sh android`.

## Development-only web adapter

The standalone Rust HTTP adapter is a local development and testing surface, not a production deployment target.

Build and start are separate repository-native operations so a long-running service does not need to compile inside its service memory boundary:

```bash
docker compose run --rm dev sh /workspace/scripts/build_web.sh
```

`scripts/run_web.sh` starts an already-built server and defaults to `127.0.0.1:8000`. Container/service launchers that publish a localhost host port can pass `--host 0.0.0.0` for the container-side listener. The Compose `web` service performs the build followed by the run step for direct Docker convenience:

```bash
docker compose up -d --build web
```

Open `http://127.0.0.1:18787`. Override the port with `MEDICINE_PORT` when needed. Keep this service bound to localhost.

## Reference data

The authoritative reference database is rebuilt from preserved official snapshots. The compact `mobile.sqlite` is the runtime snapshot consumed by Android and the local development runtime.

Local credentials are loaded from `$HOME/.config/medicine/dev.env` inside the development environment. For the standard Docker development service, that path is backed by `$MEDICINE_DEV_HOME/.config/medicine/dev.env` on the host. Keep the file private:

```bash
mkdir -p "$MEDICINE_DEV_HOME/.config/medicine"
umask 077
touch "$MEDICINE_DEV_HOME/.config/medicine/dev.env"
chmod 600 "$MEDICINE_DEV_HOME/.config/medicine/dev.env"
```

The loader accepts only the repository's explicit local credential allowlist and rejects group/world-readable files, symlinks, duplicate keys, and unknown keys. Credential values are read from the file into the child process environment; they are not command-line arguments.

Source refreshes that need the public-data service key use the loader explicitly:

```bash
docker compose run --rm dev \
  python scripts/with_local_env.py --require DATA_GO_KR_SERVICE_KEY -- \
  python -m medicine_canonical.cli sync --json
```

The remaining repository-native reference operations do not require that key once the official source snapshots are present:

```bash
docker compose run --rm dev python -m medicine_canonical.cli integrated-rebuild --json
docker compose run --rm dev python -m medicine_canonical.cli integrated-build --json
docker compose run --rm dev python -m medicine_canonical.cli verify --json
docker compose run --rm dev python -m medicine_canonical.cli substance-verify --json
docker compose run --rm dev python -m medicine_canonical.cli stats --json
docker compose run --rm dev python -m medicine_canonical.cli mobile-build --json
```

The runtime does not infer missing product/ingredient identity from EDI, product names, or fuzzy aliases. Ambiguous or unsupported DUR coverage remains fail-closed.

## Agent control CLI

`medicine-agentctl` exercises the same Rust `MedicineEngine` domain core used by Android. `scripts/run_app.sh` builds and executes it through the standard development environment.

```bash
docker compose run --rm dev sh scripts/run_app.sh people --json
docker compose run --rm dev sh scripts/run_app.sh drug-search 졸피뎀 --limit 10 --json
```

Run `docker compose run --rm dev sh scripts/run_app.sh --help` for the complete command surface.

## Android

The Android UI is packaged with the APK and calls the Rust core through JNI. It does not depend on the standalone development web service at runtime. The release build is currently `arm64-v8a`, Android 9+ (API 28+), with on-device OCR packaged in the APK.

```bash
docker compose run --rm dev sh scripts/android_debug_build.sh
```

Use `scripts/check.sh android` for the full Android development gate (`testDebugUnitTest`, `lintDebug`, and `assembleDebug`).

Release signing, exact-SHA handoff, versioning, and GitHub Release publication are documented in `docs/android-releasing.md`.

## OCR runtime

The Android product always packages the validated on-device OCR runtime under `android/app/src/main/assets/ocr-assets`. OCR images and recognized text stay on-device; medication rows are created only from canonical catalog matches, and regimen fields are entered by the user.
