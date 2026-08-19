# Releasing the Android app

Medicine follows the same release handoff used by COWI: build and validate the signed artifact once before tagging, then publish that exact artifact after the tag is created.

## One-time repository setup

Configure these GitHub Actions secrets. Keep the keystore itself out of Git and provide it as base64 only through GitHub Secrets.

- `ANDROID_RELEASE_KEYSTORE_BASE64`
- `ANDROID_RELEASE_KEYSTORE_PASSWORD`
- `ANDROID_RELEASE_KEY_ALIAS`
- `ANDROID_RELEASE_KEY_PASSWORD`

The tag-driven publish workflow does not receive these signing secrets. Only the manually started **Android Release Check** workflow can reconstruct the temporary keystore and build a signed APK.

## Release version

`android/release.properties` is the source-controlled Android release version. `versionName` becomes the `vX.Y.Z` tag and `versionCode` must increase monotonically for Android upgrades.

The initial configuration is:

```properties
versionName=0.2.0
versionCode=1
```

For the next release, update both values in the release-preparation commit, for example `versionName=0.2.1` with a larger `versionCode`. Release Gradle tasks reject version environment values which do not match this file.

## Release flow

1. Merge the release-preparation changes and identify the exact commit SHA intended for release.
2. In GitHub Actions, manually run **Android Release Check** against that exact ref.
3. Release Check resolves the expected tag, builds the pinned Android Docker image, reconstructs the keystore only in the ephemeral runner, and runs `scripts/check-android-release.sh`.
4. The check runs the existing Android unit-test/release-lint/signing gate, verifies the APK version and signature, packages `medicine-vX.Y.Z-arm64-v8a.apk`, and saves `dist` under a cache key containing the exact commit SHA and Release Check run ID.
5. After that workflow succeeds, create and push the matching tag on the same commit, for example `v0.2.0`.
6. **Android Release** verifies that the tag matches `android/release.properties` and that the exact tag commit has a successful Android Release Check.
7. The tag workflow restores that run's exact-SHA APK and **does not rebuild** it. It uploads the APK to a draft GitHub Release, creates `SHA256SUMS`, and only then publishes the release.

A tag whose commit has no successful Release Check cannot publish. The cache key also includes the successful workflow run ID, so an APK from another commit or a partial run cannot be mixed into the release. Tag promptly after validation because GitHub Actions caches are subject to retention and eviction.

## Publishing commands

After Android Release Check succeeds on the intended commit:

```bash
git tag v0.2.0 <validated-commit-sha>
git push origin v0.2.0
```

The resulting GitHub Release contains:

```text
medicine-v0.2.0-arm64-v8a.apk
SHA256SUMS
```

The current Android package intentionally targets `arm64-v8a` only. Google Play/AAB publishing is a separate distribution path and is not part of this GitHub Release workflow.
