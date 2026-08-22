//! RED contract for the portable reference-release trust boundary.
//!
//! These vectors are shared with the Android verifier.  In particular, the
//! signature is over the exact `MEDREFSIG1` frame, not over a re-encoded JSON
//! object.  The implementation is expected to expose the same verifier and
//! protocol parser to JNI, the local CLI, and the Linux development service.

use medicine_core::{
    ReferenceArtifactKind, ReferenceManifestVerifier, ReferenceReleaseProtocolV2, TrustedSigningKey,
};
use serde_json::{json, Value};

const FIXTURE_KEY_ID: &str = "test-2026";
const FIXTURE_PUBLIC_KEY_SPKI_HEX: &str =
    "3059301306072a8648ce3d020106082a8648ce3d030107034200043c8ebb038edeb1bae7ca5aeb3bb5aa01a494c254a3f51108cf01244255906f82f7bf1f2d4790d20e5f341a4b817bc70599627862d6d2d27da7b78b8a28fcb100";
const PRODUCTION_PUBLIC_KEY_SPKI_HEX: &str =
    "3059301306072a8648ce3d020106082a8648ce3d030107034200042fe843d039b5e12d8fb81526bb8601a548ff8d2c204493856905d25cb3d3332dc56f6a2144bb8f2406847505a2604e62501561cedcdb3415bb057f4a14d3866f";
const PAYLOAD_BASE64: &str =
    "eyJkYXRhc2V0X2lkIjoic2hhMjU2OmFuZHJvaWQtZml4dHVyZSIsInNjaGVtYV92ZXJzaW9uIjoxfQo=";
const SIGNATURE_BASE64: &str =
    "MEUCIATBn3O5nGmYpbcMJbWLrGxMAkW7KUiSzUL6kxX0M9zSAiEA+JEUVIHbLxxZWE3Ofht8NVw6WBX+3d+2o5tdodnICsc=";

fn hex_bytes(value: &str) -> Vec<u8> {
    assert_eq!(value.len() % 2, 0);
    (0..value.len())
        .step_by(2)
        .map(|offset| u8::from_str_radix(&value[offset..offset + 2], 16).unwrap())
        .collect()
}

fn verifier(keys: Vec<TrustedSigningKey>) -> ReferenceManifestVerifier {
    ReferenceManifestVerifier::new(keys)
}

fn active_key(key_id: &str, spki_hex: &str) -> TrustedSigningKey {
    TrustedSigningKey::active(key_id, hex_bytes(spki_hex))
}

fn revoked_key(key_id: &str, spki_hex: &str) -> TrustedSigningKey {
    TrustedSigningKey::revoked(key_id, hex_bytes(spki_hex))
}

fn verify_fixture(
    verifier: &ReferenceManifestVerifier,
    release_sequence: i64,
    minimum_exclusive_sequence: Option<i64>,
    payload_base64: &str,
) -> Result<medicine_core::VerifiedReferenceManifestSignature, String> {
    verifier
        .verify(
            1,
            "ECDSA_P256_SHA256",
            FIXTURE_KEY_ID,
            release_sequence,
            payload_base64,
            SIGNATURE_BASE64,
            minimum_exclusive_sequence,
        )
        .map_err(|error| error.to_string())
}

fn valid_verifier() -> ReferenceManifestVerifier {
    verifier(vec![active_key(
        FIXTURE_KEY_ID,
        FIXTURE_PUBLIC_KEY_SPKI_HEX,
    )])
}

fn contract_entry(major: u64, target: &str, full: &str, dataset: &str) -> Value {
    json!({
        "dataset_id": format!("sha256:{dataset}"),
        "target": {"sha256": target, "size_bytes": 2_000},
        "full": {
            "key": format!("reference/v2/contracts/{major}/full/{target}.sqlite.gz"),
            "compression": "gzip",
            "sha256": full,
            "size_bytes": 500,
        },
        "patches": [],
        "history": [],
    })
}

fn valid_root() -> Value {
    let target = "a".repeat(64);
    let full = "b".repeat(64);
    let source = "c".repeat(64);
    let patch = "d".repeat(64);
    let mut current = contract_entry(1, &target, &full, &"e".repeat(64));
    current["patches"] = json!([
        {
            "key": format!("reference/v2/contracts/1/patch/{source}-{target}.mpatch"),
            "format": "medicine-chunk-v1",
            "from_sha256": source,
            "from_size_bytes": 1_900,
            "sha256": patch,
            "size_bytes": 200,
        },
        {
            "format": "future-patch-v9",
            "key": "future/codec.bin",
            "sha256": "not-a-sha",
            "size_bytes": -1,
        }
    ]);
    json!({
        "protocol_version": 2,
        "created_at": "2026-08-20T00:00:00Z",
        "current_contract_major": 2,
        "minimum_supported_contract_major": 1,
        "contracts": {
            "1": current,
            "2": contract_entry(2, &"f".repeat(64), &"0".repeat(64), &"1".repeat(64)),
        },
        "server_diagnostic": "additive metadata is allowed",
    })
}

fn root_bytes(root: Value) -> Vec<u8> {
    serde_json::to_vec(&root).unwrap()
}

#[test]
fn python_signed_fixture_verifies_exact_payload_and_sequence_frame() {
    let verified = verify_fixture(&valid_verifier(), 77, Some(76), PAYLOAD_BASE64).unwrap();
    assert_eq!(verified.release_sequence, 77);
    assert_eq!(verified.key_id, FIXTURE_KEY_ID);
    assert_eq!(
        verified.payload,
        b"{\"dataset_id\":\"sha256:android-fixture\",\"schema_version\":1}\n"
    );
}

#[test]
fn sequence_must_be_positive_and_strictly_newer_than_accepted_sequence() {
    let verifier = valid_verifier();
    assert!(verify_fixture(&verifier, 0, None, PAYLOAD_BASE64).is_err());
    assert!(verify_fixture(&verifier, 77, Some(77), PAYLOAD_BASE64).is_err());
    assert!(verify_fixture(&verifier, 77, Some(78), PAYLOAD_BASE64).is_err());
    assert!(verify_fixture(&verifier, 77, Some(0), PAYLOAD_BASE64).is_err());
}

#[test]
fn payload_tampering_unknown_key_and_revoked_key_are_rejected() {
    let tampered_payload = "dGFtcGVyZWQK";
    assert!(verify_fixture(&valid_verifier(), 77, None, tampered_payload).is_err());

    let unknown = verifier(vec![active_key("other-key", FIXTURE_PUBLIC_KEY_SPKI_HEX)]);
    assert!(verify_fixture(&unknown, 77, None, PAYLOAD_BASE64).is_err());

    let revoked = verifier(vec![revoked_key(
        FIXTURE_KEY_ID,
        FIXTURE_PUBLIC_KEY_SPKI_HEX,
    )]);
    assert!(verify_fixture(&revoked, 77, None, PAYLOAD_BASE64).is_err());
}

#[test]
fn old_and_new_trusted_keys_overlap_without_invalidating_old_signed_releases() {
    let overlap = verifier(vec![
        active_key(FIXTURE_KEY_ID, FIXTURE_PUBLIC_KEY_SPKI_HEX),
        active_key("reference-prod-2026-01", PRODUCTION_PUBLIC_KEY_SPKI_HEX),
    ]);
    assert!(verify_fixture(&overlap, 77, None, PAYLOAD_BASE64).is_ok());
}

#[test]
fn malformed_and_noncanonical_base64_are_rejected_before_signature_check() {
    let verifier = valid_verifier();
    for invalid_payload in ["A===", "AB==", "AA@=", "AAAA A==", ""] {
        assert!(verify_fixture(&verifier, 77, None, invalid_payload).is_err());
    }
    assert!(verifier
        .verify(
            1,
            "ECDSA_P256_SHA256",
            "bad key!",
            77,
            PAYLOAD_BASE64,
            SIGNATURE_BASE64,
            None,
        )
        .is_err());
    assert!(verifier
        .verify(
            1,
            "unsupported",
            FIXTURE_KEY_ID,
            77,
            PAYLOAD_BASE64,
            SIGNATURE_BASE64,
            None,
        )
        .is_err());
}

#[test]
fn protocol_v2_selects_own_contract_and_ignores_unknown_patch_codec() {
    let release =
        ReferenceReleaseProtocolV2::parse_verified_root(41, &root_bytes(valid_root()), 1).unwrap();
    assert_eq!(release.contract_major, 1);
    assert_eq!(release.dataset_id, format!("sha256:{}", "e".repeat(64)));
    assert_eq!(release.target_sha256, "a".repeat(64));
    assert_eq!(release.target_size_bytes, 2_000);
    assert_eq!(release.full.kind, ReferenceArtifactKind::FullGzip);
    assert_eq!(release.patches.len(), 1);
    assert_eq!(release.patches[0].kind, ReferenceArtifactKind::ChunkPatch);
}

#[test]
fn protocol_v2_enforces_n_over_n_minus_one_window_and_explicit_retirement() {
    let mut too_wide = valid_root();
    too_wide["minimum_supported_contract_major"] = json!(1);
    too_wide["current_contract_major"] = json!(3);
    assert!(ReferenceReleaseProtocolV2::parse_verified_root(42, &root_bytes(too_wide), 1).is_err());

    let mut missing_entry = valid_root();
    missing_entry["contracts"]
        .as_object_mut()
        .unwrap()
        .remove("2");
    assert!(
        ReferenceReleaseProtocolV2::parse_verified_root(42, &root_bytes(missing_entry), 1).is_err()
    );

    let mut retired = valid_root();
    retired["minimum_supported_contract_major"] = json!(2);
    let error = ReferenceReleaseProtocolV2::parse_verified_root(43, &root_bytes(retired), 1)
        .unwrap_err()
        .to_string();
    assert!(
        error.contains("retired"),
        "unexpected retirement error: {error}"
    );
}

#[test]
fn protocol_v2_rejects_invalid_target_and_full_artifact_identity() {
    let mut invalid_target_hash = valid_root();
    invalid_target_hash["contracts"]["1"]["target"]["sha256"] = json!("bad");
    assert!(ReferenceReleaseProtocolV2::parse_verified_root(
        44,
        &root_bytes(invalid_target_hash),
        1
    )
    .is_err());

    let mut invalid_target_size = valid_root();
    invalid_target_size["contracts"]["1"]["target"]["size_bytes"] = json!(0);
    assert!(ReferenceReleaseProtocolV2::parse_verified_root(
        44,
        &root_bytes(invalid_target_size),
        1
    )
    .is_err());

    for (field, value) in [
        ("key", json!("reference/v2/contracts/9/full/b.sqlite.gz")),
        ("sha256", json!("not-a-sha")),
        ("size_bytes", json!(0)),
    ] {
        let mut invalid = valid_root();
        invalid["contracts"]["1"]["full"][field] = value;
        assert!(
            ReferenceReleaseProtocolV2::parse_verified_root(44, &root_bytes(invalid), 1).is_err()
        );
    }
}

#[test]
fn protocol_v2_rejects_duplicate_patch_sources_and_invalid_patch_identity() {
    let mut duplicate = valid_root();
    let patch = duplicate["contracts"]["1"]["patches"][0].clone();
    duplicate["contracts"]["1"]["patches"] = json!([patch.clone(), patch]);
    assert!(
        ReferenceReleaseProtocolV2::parse_verified_root(45, &root_bytes(duplicate), 1).is_err()
    );

    for (field, value) in [
        ("key", json!("reference/v2/contracts/1/patch/bad.mpatch")),
        ("from_sha256", json!("bad")),
        ("from_size_bytes", json!(0)),
        ("size_bytes", json!(500)),
    ] {
        let mut invalid = valid_root();
        invalid["contracts"]["1"]["patches"][0][field] = value;
        assert!(
            ReferenceReleaseProtocolV2::parse_verified_root(45, &root_bytes(invalid), 1).is_err()
        );
    }
}
