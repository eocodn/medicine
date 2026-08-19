# Releasing the Android developer APK

Medicine follows the same release handoff used by COWI: build and validate the artifact once before tagging, then publish that exact artifact after the tag is created. This GitHub Release path is for developer/tester distribution and publishes a Gradle debug-signed APK.

## Signing model

This developer release path **does not require GitHub signing secrets**. Gradle creates and uses its normal debug signing key inside the ephemeral Release Check runner, and `apksigner` verifies that the produced APK is signed before it is preserved.

Because the debug key is runner-local and not a durable application signing identity, separately validated GitHub Releases may not install as an in-place update over one another. If Android reports a signing mismatch, uninstall the previous developer APK and install the new one. The existing explicit release-signing path remains separate for any future production or Play distribution where a stable signing identity is required.

## Release version

`android/release.properties` is the source-controlled Android release version. `versionName` becomes the `vX.Y.Z` tag and `versionCode` must increase monotonically for Android upgrades.

The initial configuration is:

```properties
versionName=0.2.0
versionCode=1
```

For the next release, update both values in the release-preparation commit, for example `versionName=0.2.1` with a larger `versionCode`. The same file also drives the debug build metadata used by this GitHub Release path.

## Release flow

1. Merge the release-preparation changes and identify the exact commit SHA intended for release.
2. In GitHub Actions, manually run **Android Developer Release Check** against that exact ref.
3. Developer Release Check runs on a native GitHub-hosted Ubuntu runner with JDK 17, Node 22, Android SDK 36, and the checked-in Gradle Wrapper. It prepares the pinned OCR runtime assets and then runs `scripts/check-android-release.sh` with no signing secrets or Docker.
4. The check runs `testDebugUnitTest`, `lintDebug`, and `assembleDebug`, verifies the debug APK version and signature, packages `medicine-vX.Y.Z-arm64-v8a.apk`, and saves `dist` under a cache key containing the exact commit SHA and Release Check run ID.
5. After that workflow succeeds, create and push the matching tag on the same commit, for example `v0.2.0`.
6. **Android Developer Release** verifies that the tag matches `android/release.properties` and that the exact tag commit has a successful Android Developer Release Check.
7. The tag workflow restores that run's exact-SHA APK and **does not rebuild** it. It uploads the APK to a draft GitHub Release, creates `SHA256SUMS`, and only then publishes the release.

A tag whose commit has no successful Developer Release Check cannot publish. The cache key also includes the successful workflow run ID, so an APK from another commit or a partial run cannot be mixed into the release. Tag promptly after validation because GitHub Actions caches are subject to retention and eviction.

## Publishing commands

After Android Developer Release Check succeeds on the intended commit:

```bash
git tag v0.2.0 <validated-commit-sha>
git push origin v0.2.0
```

The resulting GitHub Release contains:

```text
medicine-v0.2.0-arm64-v8a.apk
SHA256SUMS
```

The current Android package intentionally targets `arm64-v8a` only. This developer GitHub Release path is not a production signing path. Google Play/AAB publishing and durable release signing remain separate distribution work.

Local Android development and verification still use Docker/Compose. The native GitHub-hosted Ubuntu runner path is CI-only, so the local host does not need a Gradle or Android SDK installation.
