# Reference signing key rotation

Reference release signing separates the active KMS signer from the set of public keys that remain trusted. A key rotation always receives a new logical `key_id`; changing the public key behind an existing `key_id` is not a supported rotation because an overlap window could not distinguish the old and new keys.

The authoritative trust manifest is `deploy/reference-signing-trusted-keys.json`. Each entry carries a `key_id`, reviewed public-key PEM, and reviewed SPKI SHA-256 fingerprint. Its `active_key_id` must match `REFERENCE_SIGNING_KEY_ID`, and the active entry must match the public key returned by the configured KMS key version. The Android Gradle build validates and embeds this same manifest as `BuildConfig.REFERENCE_TRUST_MANIFEST_JSON`; the shared Rust trust parser consumes it directly. Android accepts any key that remains in the manifest, while `active_key_id` remains publisher metadata for selecting the current signer.

Rotate in three releases:

1. **Prepare overlap.** Allocate a new `key_id`, add its reviewed entry to the trust manifest, but leave the old signer active. Ship the Android release built with both keys before changing the publisher signer.
2. **Switch signer.** Keep both keys trusted, then change `active_key_id`, `REFERENCE_SIGNING_KEY_ID`, and the KMS key version to the new key. The publisher authenticates the existing root with the old key and advances the channel with a new-key signature. If reference content is unchanged, the exact existing signed payload is re-signed at a strictly newer release sequence so rotation never waits for unrelated data changes.
3. **Retire the old key.** After the supported Android population no longer requires the old key, remove its entry from the trust manifest. Omission is revocation: an old-key root is no longer accepted after this boundary.

Do not switch the signer before an overlap-capable Android release is deployed, and do not remove the old key before the first new-key publication succeeds. Publisher validation, the Android build, the Rust runtime, and the Android release gate all consume the same checked-in trust manifest.
