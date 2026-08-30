# Google Play release preparation

This document covers the production Google Play path. The existing
`docs/android-releasing.md` flow remains a developer/tester GitHub Release path and intentionally
uses a debug-signed APK.

## Fixed application contract

- Play application ID: `kr.yakbom.app`
- Android namespace: `com.medicine.android` (internal source namespace; it does not define the Play identity)
- `compileSdk 36`
- `targetSdk 36`
- current production product capability: no OCR
- current native ABI: `arm64-v8a`

The application ID is intended to be permanent. Do not create a different Play Console app for the
same product without an explicit identity decision.

## Reference distribution boundary

Debug/developer builds may use the checked development `r2.dev` reference endpoint.

Production release tasks fail closed unless `MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL` is set to
an explicit HTTPS base URL ending in `/`. A production release URL using the development
`*.r2.dev` endpoint is rejected. Choose and provision the production reference hostname before the
first production bundle is built.

This is product-specific release infrastructure: the Play bundle ships application code, while the
signed medication reference database is published and updated separately. Before each production
rollout, verify that the production reference root supports the Android contract and that a fresh
install can bootstrap from it.

## Play App Signing and upload key

Use Play App Signing for the Play application. Keep the Play app-signing key managed by Google Play
and use a separate upload key for local/CI bundle signing. Never commit the upload keystore or its
passwords to this repository.

The existing Android release environment is reused for the upload key:

```text
MEDICINE_ANDROID_VERSION_CODE
MEDICINE_ANDROID_VERSION_NAME
MEDICINE_ANDROID_KEYSTORE_PATH
MEDICINE_ANDROID_KEYSTORE_PASSWORD
MEDICINE_ANDROID_KEY_ALIAS
MEDICINE_ANDROID_KEY_PASSWORD
MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL
```

`MEDICINE_ANDROID_VERSION_CODE` and `MEDICINE_ANDROID_VERSION_NAME` must match
`android/release.properties`.

## Build the Play AAB

Run the Play build inside the standard Android development image with the upload keystore mounted
read-only. Do not set `MEDICINE_OCR_ASSETS_DIR`.

```bash
docker compose -p medicine_android_play build android
docker compose -p medicine_android_play run --rm \
  -v "$ANDROID_RELEASE_KEYSTORE:/run/secrets/yakbom-upload.jks:ro" \
  -e MEDICINE_ANDROID_VERSION_CODE \
  -e MEDICINE_ANDROID_VERSION_NAME \
  -e MEDICINE_ANDROID_KEYSTORE_PASSWORD \
  -e MEDICINE_ANDROID_KEY_ALIAS \
  -e MEDICINE_ANDROID_KEY_PASSWORD \
  -e MEDICINE_ANDROID_KEYSTORE_PATH=/run/secrets/yakbom-upload.jks \
  -e MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL \
  android sh /workspace/scripts/android_play_bundle.sh
```

`android_play_bundle.sh` verifies the exact configured production signed Reference Contract before
the expensive build, runs Android unit tests, release lint, and `bundleRelease`, then verifies the
produced AAB's application ID, versionCode, versionName, target SDK, complete JAR signature, and
no-OCR artifact boundary. It rechecks the production Reference Contract immediately before
preserving the bundle. The preserved output is `dist/play/yakbom-v<versionName>.aab`.

## Operator / policy gates before production

The repository cannot complete these gates by itself. They require Play Console configuration,
public policy information, or a product/legal decision:

1. Create the Play Console app with package ID `kr.yakbom.app` and enable Play App Signing.
2. Provision the production reference hostname and set `MEDICINE_REFERENCE_UPDATE_RELEASE_BASE_URL`.
3. Publish a public, non-PDF 개인정보처리방침 URL and expose the policy or link from the app before
   production submission. Developer/controller identity and contact details must be supplied by the
   operator; do not ship placeholders.
4. Complete Play Console Data safety and Health Apps declarations using the actual production data
   flows. Do not infer "no data collected" until production CDN/logging behavior is also reviewed.
5. Complete the store listing, content rating, target audience, and ads declarations.
6. Review the intended use against the current MFDS medical-device versus personal-wellness criteria.
   This repository does not assert either regulatory classification.
7. Complete the MFDS/FDA-derived 데이터 이용조건 and redistribution review for the exact fields and
   artifacts shipped by the reference channel.
8. Test the Play-delivered build on representative physical devices: Android 16/API 36, Samsung and
   Pixel if available, fresh reference bootstrap, interrupted/retried download, offline LKG startup,
   low storage, medication/DUR flows, and an in-place update preserving the encrypted personal DB.

For a first public release, use Play Internal testing first, then any required Closed testing, before
Production. Do not use the debug-signed GitHub Developer Release APK as the production signing
identity.
