# Releasing the Android developer APK

Medicine follows the same release handoff used by COWI: build and validate the artifact once before tagging, then publish that exact artifact after the tag is created. This GitHub Release path is for developer/tester distribution and publishes an APK signed by Medicine's durable release signing identity.

## Signing model

This developer release path **does not require GitHub signing secrets**. The Android release keystore and its password live in GCP Secret Manager using its default Google-managed encryption; a separate application-managed CMEK is intentionally not part of this design. GitHub Actions authenticates to GCP with Workload Identity Federation through the dedicated `medicine-android-signer` provider.

The release check deliberately separates untrusted build execution from access to the durable key. A self-hosted `wsl-ci` runner builds and validates an **unsigned** release candidate with no GCP identity and no signing secret access. That exact unsigned APK is then handed to a fresh GitHub-hosted runner. Only the GitHub-hosted signing job receives `id-token: write`, authenticates to GCP, materializes the keystore under `RUNNER_TEMP`, signs with `apksigner`, verifies the certificate, and deletes both the temporary keystore and generated GCP credentials before any later action runs. No repository-controlled Gradle, shell, Python, Node, or Rust code executes after GCP authentication.

All actions in the Android release-check and tag-publication workflows are pinned to immutable commit SHAs rather than mutable major-version tags. The public signing certificate identity is pinned in `deploy/android-release-signing-certificate.sha256`, and the signing job rejects a release APK unless `apksigner` reports that exact certificate digest.

The primary keystore/password secrets have a 30-day Secret Manager version-destroy delay. The independent cold backup copy lives in the separate GCP project `yakbom-android-signing-vault` as `medicine-android-release-keystore-cold-b64` and `medicine-android-release-signing-password-cold`. That project has no Android CI/WIF binding and uses Google-managed encryption plus the same 30-day version-destroy delay. The cold project is operator recovery material only; CI must never read from it.

Keep the private signing key for the lifetime of the application. Future releases are expected to install in place over earlier releases with the same package ID and signing lineage. The app uses `minSdk = 28`, i.e. API 28 (Android 9), aligning the supported device floor with APK signing lineage rotation support if a future signing-key rotation is ever required. Raising the minimum SDK does not itself rotate the current key.

## Release version

`android/release.properties` is the source-controlled Android release version. `versionName` becomes the `vX.Y.Z` tag and `versionCode` must increase monotonically for Android upgrades.

The first durable-signed tester release is:

```properties
versionName=0.1.0
versionCode=1
```

For the next release, update both values in the release-preparation commit, for example `versionName=0.1.1` with a `versionCode` larger than `1`.

## Optional OCR capability

OCR is opt-in at Android build time. `MEDICINE_OCR_ASSETS_DIR` is the single capability boundary: when it points to an approved runtime bundle, Gradle packages that bundle, retains the OCR controls and `ocr-intake.js` in the generated shared UI, enables the OCR `FileProvider`, and compiles `src/ocr/java`. When the variable is absent, Gradle strips the OCR UI/script, disables the provider, omits OCR assets, and compiles only `src/noOcr/java`.

The normal Developer Release workflows intentionally leave this variable unset. An OCR-enabled distribution therefore requires an explicit release-policy change plus a validated runtime bundle; it must not happen implicitly because an OCR research artifact happens to exist on a builder.

## Release flow

1. Merge the release-preparation changes and identify the exact commit SHA intended for release.
2. In GitHub Actions, manually run **Android Developer Release Check** against that exact `main` commit.
3. `build-unsigned` runs on self-hosted `wsl-ci` with no GCP/OIDC permission. It provisions Node 22 and installs the checked-in `ui/package-lock.json` with `npm ci`, then runs `testDebugUnitTest`, `lintRelease`, and `assembleRelease`, verifies package/version/no-OCR/reference-contract requirements, and preserves exactly one `medicine-vX.Y.Z-arm64-v8a-unsigned.apk` candidate under a commit-SHA + workflow-run cache key.
4. `sign-and-validate` runs on a fresh GitHub-hosted runner. It restores only that exact candidate, validates that it is unsigned, authenticates to GCP, reads only the primary Android signing secrets, signs with APK Signature Scheme v3 for the API 28+ application floor, verifies the pinned certificate SHA-256, deletes temporary signing/GCP credentials, and preserves `medicine-vX.Y.Z-arm64-v8a.apk` under the existing exact-SHA cache handoff key.
5. After the full Release Check succeeds, create and push the matching tag on the same commit, starting with `v0.1.0`.
6. **Android Developer Release** verifies that the tag matches `android/release.properties` and that the exact tag commit has a successful Android Developer Release Check.
7. The tag workflow restores that run's signed APK and **does not rebuild** it. It uploads the APK to a draft GitHub Release, creates `SHA256SUMS`, re-verifies the live Reference Contract immediately before publication, and only then publishes the release.

A tag whose commit has no successful Android Developer Release Check cannot publish. Cache keys include both the exact commit SHA and successful workflow run ID, so an APK from another commit or partial run cannot be mixed into the release.

## Publishing commands

After Android Developer Release Check succeeds on the intended commit:

```bash
git tag v0.1.0 <validated-commit-sha>
git push origin v0.1.0
```

The resulting GitHub Release contains:

```text
medicine-v0.1.0-arm64-v8a.apk
THIRD_PARTY_NOTICES.txt
SHA256SUMS
```

`THIRD_PARTY_NOTICES.txt` is also packaged inside the APK. It is generated from the locked Android release runtime dependencies and Rust lockfile, with upstream license/notice texts preserved. When either dependency lock changes, regenerate and review the notice bundle in the standard development image:

```bash
docker compose run --rm dev python scripts/generate-android-third-party-notices.py \
  --cargo-registry-src /opt/medicine-cargo-home/registry/src \
  --gradle-module-cache /opt/medicine-gradle-home/caches/modules-2/files-2.1
```

The committed notice embeds the SHA-256 of both lockfiles, so release tests fail if dependency locks change without a reviewed notice update.

The current Android package intentionally targets `arm64-v8a` only. Distribution is APK-only. `scripts/check-android-release.sh` is the secret-free unsigned GitHub release gate; `scripts/android_release_build.sh` remains the explicit local/operator path for producing a directly signed release APK when signing inputs are intentionally supplied.

Local Android development and verification still use Docker/Compose. The self-hosted `wsl-ci` runner path is CI-only, so the local host does not need a Gradle or Android SDK installation for routine development.
