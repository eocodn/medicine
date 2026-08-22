# Reference signing key rotation

Reference release signing separates the active KMS signer from the set of public keys that remain trusted. A key rotation always receives a new logical `key_id`; changing the public key behind an existing `key_id` is not a supported rotation because an overlap window could not distinguish the old and new keys.

The authoritative publisher trust set is `deploy/reference-signing-trusted-keys.json`. Its `active_key_id` must match `REFERENCE_SIGNING_KEY_ID`, and the active entry must match the public key returned by the configured KMS key version. Android embeds the same reviewed key set in `ReferenceTrust`; Android does not have an active-key concept and accepts any key that remains in that set.

Rotate in three releases:

1. **Prepare overlap.** Allocate a new `key_id`, add its reviewed public key to both the publisher trust file and Android `ReferenceTrust`, but leave the old signer active. Ship the Android release containing both keys before changing the publisher signer.
2. **Switch signer.** Keep both keys trusted, then change `active_key_id`, `REFERENCE_SIGNING_KEY_ID`, and the KMS key version to the new key. The publisher must be able to authenticate the existing root with the old key before it advances the channel with a root signed by the new key.
3. **Retire the old key.** After the supported Android population no longer requires the old key, remove it from both trust sets. Omission is revocation: an old-key root is no longer accepted after this boundary.

Do not switch the signer before an overlap-capable Android release is deployed, and do not remove the old publisher key before the first new-key publication succeeds. CI verifies that the publisher and Android trust sets contain the same key IDs and SPKI bytes and that the workflow signer matches the configured active key.