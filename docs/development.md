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

`./scripts/check.sh all` runs the core, UI, and Android checks in sequence.

## Development-only web adapter

The standalone Rust HTTP adapter is a local development and testing surface, not a production deployment target.

```bash
docker compose up -d --build web
```

Open `http://127.0.0.1:18787`. Override the port with `MEDICINE_PORT` when needed. Keep this service bound to localhost.

## Reference data

The authoritative reference database is rebuilt from preserved official snapshots. The compact `mobile.sqlite` is the runtime snapshot consumed by Android and the local development runtime.

```bash
docker compose run --rm canonical sync --json
docker compose run --rm canonical integrated-rebuild --json
docker compose run --rm canonical integrated-build --json
docker compose run --rm canonical verify --json
docker compose run --rm canonical substance-verify --json
docker compose run --rm canonical stats --json
docker compose run --rm canonical mobile-build --json
```

The runtime does not infer missing product/ingredient identity from EDI, product names, or fuzzy aliases. Ambiguous or unsupported DUR coverage remains fail-closed.

## Agent control CLI

`medicine-agentctl` exercises the same Rust `MedicineEngine` domain core used by Android. Use the Compose `app` service for exploratory application testing.

```bash
docker compose run --rm app people --json
docker compose run --rm app drug-search 졸피뎀 --limit 10 --json
```

Run `docker compose run --rm app --help` for the complete command surface.

## Android

The Android UI is packaged with the APK and calls the Rust core through JNI. It does not depend on the standalone development web service at runtime. The release build is currently `arm64-v8a`, Android 9+ (API 28+), and no-OCR by default.

```bash
docker compose run --rm android
```

Release signing, exact-SHA handoff, versioning, and GitHub Release publication are documented in `docs/android-releasing.md`.

## OCR research boundary

OCR research and training live under `browser_ocr/` and are not part of the default Android release. `MEDICINE_OCR_ASSETS_DIR` is the explicit Android build capability boundary; leaving it unset produces the no-OCR product build.

Large OCR datasets and model artifacts belong under the project artifact storage rather than Git.
