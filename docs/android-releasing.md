# Releasing the Android developer APK

Medicine follows the same release handoff used by COWI: build and validate the artifact once before tagging, then publish that exact artifact after the tag is created. This GitHub Release path is for developer/tester distribution and publishes an APK signed by Medicine's durable release signing identity.

## Signing model

This developer release path **does not require GitHub signing secrets**. The Android release keystore and its password live in GCP Secret Manager. GitHub Actions authenticates to GCP with Workload Identity Federation through the dedicated `medicine-android-signer` provider, reads only those signing secrets, materializes the keystore under the runner's temporary directory for the build, and deletes it when the build step exits.

The public signing certificate identity is pinned in `deploy/android-release-signing-certificate.sha256`. `scripts/check-android-release.sh` rejects a release APK unless `apksigner` reports that exact certificate digest. This prevents an accidental Secret Manager replacement from silently creating a new Android application identity. Keep the private signing key for the lifetime of the application; future releases are expected to install in place over earlier releases with the same package ID and signing lineage.

The app now uses `minSdk = 28` (Android 9). This removes the pre-API 28 compatibility floor and keeps the supported device set aligned with APK signing lineage rotation support if a future signing-key rotation is ever required. Raising the minimum SDK does not itself rotate the current key.

## Release version

`android/release.properties` is the source-controlled Android release version. `versionName` becomes the `vX.Y.Z` tag and `versionCode` must increase monotonically for Android upgrades.

The current configuration is:

```properties
versionName=0.2.1
versionCode=2
```

For the next release, update both values in the release-preparation commit, for example `versionName=0.2.2` with a `versionCode` larger than `2`. The same file also drives the release build metadata used by this GitHub Release path.


## Optional OCR capability

OCR is opt-in at Android build time. `MEDICINE_OCR_ASSETS_DIR` is the single capability boundary: when it points to an approved runtime bundle, Gradle packages that bundle, retains the OCR controls and `ocr-intake.js` in the generated shared UI, enables the OCR `FileProvider`, and compiles `src/ocr/java`. When the variable is absent, Gradle strips the OCR UI/script, disables the provider, omits OCR assets, and compiles only `src/noOcr/java`.

The normal Developer Release workflows intentionally leave this variable unset. An OCR-enabled distribution therefore requires an explicit release-policy change plus a validated runtime bundle; it must not happen implicitly because an OCR research artifact happens to exist on a builder.

## Release flow

1. Merge the release-preparation changes and identify the exact commit SHA intended for release.
2. In GitHub Actions, manually run **Android Developer Release Check** against that exact ref.
3. Developer Release Check runs on a self-hosted `wsl-ci` runner with JDK 17, Node 22, Android SDK 36, and the checked-in Gradle Wrapper. It obtains the durable release signing identity from GCP Secret Manager through Workload Identity Federation. It does not set `MEDICINE_OCR_ASSETS_DIR`, so the validated developer APK is intentionally built without OCR UI, OCR runtime assets, or the Android OCR camera/file-chooser source set.
4. The check runs `testDebugUnitTest`, `lintRelease`, and `assembleRelease`, verifies the package/version/signature and pinned release certificate digest, packages `medicine-vX.Y.Z-arm64-v8a.apk`, and saves `dist` under a cache key containing the exact commit SHA and Release Check run ID.
5. After that workflow succeeds, create and push the matching tag on the same commit, for example `v0.2.1`.
6. **Android Developer Release** verifies that the tag matches `android/release.properties` and that the exact tag commit has a successful Android Developer Release Check.
7. The tag workflow restores that run's exact-SHA APK and **does not rebuild** it. It uploads the APK to a draft GitHub Release, creates `SHA256SUMS`, and only then publishes the release.

A tag whose commit has no successful Developer Release Check cannot publish. The cache key also includes the successful workflow run ID, so an APK from another commit or a partial run cannot be mixed into the release. Tag promptly after validation because GitHub Actions caches are subject to retention and eviction.

## Publishing commands

After Android Developer Release Check succeeds on the intended commit:

```bash
git tag v0.2.1 <validated-commit-sha>
git push origin v0.2.1
```

The resulting GitHub Release contains:

```text
medicine-v0.2.1-arm64-v8a.apk
SHA256SUMS
```

The current Android package intentionally targets `arm64-v8a` only. Distribution is APK-only, and both the GitHub Developer Release Check and explicit `scripts/android_release_build.sh` path build the release variant using the configured signing inputs.

Local Android development and verification still use Docker/Compose. The self-hosted `wsl-ci` runner path is CI-only, so the local host does not need a Gradle or Android SDK installation.
