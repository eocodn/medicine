use medicine_core::load_reference_trust_manifest;
use std::path::Path;

#[test]
fn tracked_trust_manifest_loads_as_runtime_verifier_input() {
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../deploy/reference-signing-trusted-keys.json");
    let trust = load_reference_trust_manifest(&path).expect("load tracked trust manifest");
    assert_eq!(trust.active_key_id, "reference-prod-2026-01");
    assert_eq!(trust.keys.len(), 1);
    assert_eq!(trust.keys[0].key_id, trust.active_key_id);
    assert!(!trust.keys[0].public_key_spki.is_empty());
}

#[test]
fn trust_manifest_rejects_fingerprint_mismatch() {
    let source = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../../deploy/reference-signing-trusted-keys.json");
    let mut document: serde_json::Value =
        serde_json::from_slice(&std::fs::read(source).unwrap()).unwrap();
    document["keys"][0]["spki_sha256"] = serde_json::json!("0".repeat(64));
    let path = std::env::temp_dir().join(format!(
        "medicine-reference-trust-{}.json",
        uuid::Uuid::new_v4()
    ));
    std::fs::write(&path, serde_json::to_vec(&document).unwrap()).unwrap();
    let result = load_reference_trust_manifest(&path);
    let _ = std::fs::remove_file(path);
    assert!(result.unwrap_err().to_string().contains("fingerprint"));
}
