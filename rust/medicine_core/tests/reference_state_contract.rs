use medicine_core::reference_state::{
    ReferenceFileSeal, ReferenceStateCodec, ReferenceStore, ReferenceStoreState, ReferenceVersion,
};
use serde_json::Value;

fn version(sequence: i64, marker: char, contract_major: i32) -> ReferenceVersion {
    let digest = marker.to_string().repeat(64);
    ReferenceVersion {
        dataset_id: format!("sha256:{digest}"),
        sha256: digest,
        size_bytes: sequence * 10,
        contract_major,
        release_sequence: sequence,
    }
}

fn seal(marker: i64) -> ReferenceFileSeal {
    ReferenceFileSeal {
        size_bytes: marker * 10,
        modified_marker: marker + 1,
        changed_marker: marker + 2,
        identity_key: format!("devino:{marker}"),
        writable: false,
    }
}

fn put_utf(output: &mut Vec<u8>, value: &str) {
    let bytes = value.as_bytes();
    output.extend_from_slice(&(bytes.len() as u16).to_be_bytes());
    output.extend_from_slice(bytes);
}

fn put_version_v1(output: &mut Vec<u8>, value: Option<&ReferenceVersion>) {
    output.push(u8::from(value.is_some()));
    if let Some(value) = value {
        put_utf(output, &value.dataset_id);
        put_utf(output, &value.sha256);
        output.extend_from_slice(&value.size_bytes.to_be_bytes());
        put_utf(output, "10");
        output.extend_from_slice(&value.release_sequence.to_be_bytes());
    }
}

fn legacy_v1(active: Option<&ReferenceVersion>, high_water: i64) -> Vec<u8> {
    let mut output = Vec::new();
    put_utf(&mut output, "MEDREFSTATE1");
    output.extend_from_slice(&high_water.to_be_bytes());
    put_version_v1(&mut output, active);
    put_version_v1(&mut output, None);
    put_version_v1(&mut output, None);
    output
}

#[test]
fn initial_install_and_interrupted_adoption_are_idempotent() {
    let initial = version(12, 'a', 1);
    let mut store = ReferenceStore::default();

    assert_eq!(store.install_initial(initial.clone()).unwrap(), initial);
    let first = store.snapshot();
    assert_eq!(first.active, Some(initial.clone()));
    assert_eq!(first.previous, None);
    assert_eq!(first.pending, None);
    assert_eq!(first.highest_activated_sequence, 12);

    // A process may have installed verified content before its state write.
    // Re-adopting the same identity must not create a second transition.
    assert_eq!(store.install_initial(initial).unwrap(), version(12, 'a', 1));
    assert_eq!(store.snapshot(), first);
}

#[test]
fn signed_root_is_observed_before_download_and_is_strictly_monotonic() {
    let mut store = ReferenceStore::default();
    let root = "a".repeat(64);
    let other_root = "b".repeat(64);

    store.observe_signed_root(20, &root).unwrap();
    assert_eq!(store.snapshot().highest_seen_root_sequence, 20);
    assert_eq!(
        store.snapshot().highest_seen_root_hash.as_deref(),
        Some(root.as_str())
    );

    // Retrying the same signed root is a no-op.
    let encoded = ReferenceStateCodec::encode(&store.snapshot()).unwrap();
    store.observe_signed_root(20, &root).unwrap();
    assert_eq!(
        ReferenceStateCodec::encode(&store.snapshot()).unwrap(),
        encoded
    );

    assert!(store.observe_signed_root(19, &root).is_err());
    assert!(store.observe_signed_root(20, &other_root).is_err());
    assert_eq!(store.snapshot().highest_seen_root_sequence, 20);
    assert_eq!(
        store.snapshot().highest_seen_root_hash.as_deref(),
        Some(root.as_str())
    );
}

#[test]
fn retirement_is_monotonic_and_keeps_root_observation_atomic() {
    let mut store = ReferenceStore::default();
    let root = "c".repeat(64);
    store.mark_contract_retired(3, 30, &root).unwrap();
    store.mark_contract_retired(2, 30, &root).unwrap();

    let state = store.snapshot();
    assert_eq!(state.highest_retired_contract_major, 3);
    assert_eq!(state.highest_seen_root_sequence, 30);
    assert_eq!(state.highest_seen_root_hash.as_deref(), Some(root.as_str()));
    assert!(store.is_contract_retired(1));
    assert!(store.is_contract_retired(3));
    assert!(!store.is_contract_retired(4));
}

#[test]
fn pending_update_does_not_activate_until_next_startup() {
    let current = version(1, '1', 1);
    let pending = version(7, '7', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current.clone()).unwrap();

    store.stage_pending(pending.clone(), Some(seal(7))).unwrap();
    let staged = store.snapshot();
    assert_eq!(staged.active, Some(current.clone()));
    assert_eq!(staged.pending, Some(pending.clone()));
    assert_eq!(staged.pending_seal, Some(seal(7)));
    assert_eq!(staged.highest_activated_sequence, 1);

    let selected = store.open_for_startup(1, true, true, true).unwrap();
    assert_eq!(selected, Some(pending.clone()));
    let activated = store.snapshot();
    assert_eq!(activated.active, Some(pending));
    assert_eq!(activated.previous, Some(current));
    assert_eq!(activated.pending, None);
    assert_eq!(activated.highest_activated_sequence, 7);
}

#[test]
fn invalid_active_falls_back_to_previous_lkg_without_lowering_high_water() {
    let current = version(1, '1', 1);
    let next = version(7, '7', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current.clone()).unwrap();
    store.stage_pending(next.clone(), None).unwrap();
    assert_eq!(
        store.open_for_startup(1, true, true, true).unwrap(),
        Some(next)
    );

    // The active content is corrupted, while the previous content remains a
    // valid last-known-good reference.
    let selected = store.open_for_startup(1, false, true, true).unwrap();
    assert_eq!(selected, Some(current.clone()));
    let recovered = store.snapshot();
    assert_eq!(recovered.active, Some(current));
    assert_eq!(recovered.previous, None);
    assert_eq!(recovered.highest_activated_sequence, 7);
}

#[test]
fn stale_pending_is_discarded_after_a_newer_signed_root_is_seen() {
    let current = version(1, '1', 1);
    let stale = version(10, 'a', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current.clone()).unwrap();
    store.stage_pending(stale.clone(), None).unwrap();
    store.observe_signed_root(11, &"b".repeat(64)).unwrap();

    assert_eq!(
        store.open_for_startup(1, true, true, true).unwrap(),
        Some(current.clone())
    );
    let state = store.snapshot();
    assert_eq!(state.active, Some(current));
    assert_eq!(state.pending, None);
    assert_eq!(state.highest_activated_sequence, 1);
    assert_eq!(state.highest_seen_root_sequence, 11);
    assert_ne!(state.pending, Some(stale));
}

#[test]
fn state_json_uses_stable_cross_platform_camel_case_and_preserves_seals() {
    let state = ReferenceStoreState {
        active: Some(version(7, 'a', 1)),
        previous: Some(version(3, 'b', 1)),
        pending: Some(version(9, 'c', 1)),
        highest_activated_sequence: 7,
        highest_seen_root_sequence: 11,
        highest_seen_root_hash: Some("d".repeat(64)),
        highest_retired_contract_major: 2,
        active_seal: Some(seal(7)),
        previous_seal: Some(seal(3)),
        pending_seal: Some(seal(9)),
    };
    let json = serde_json::to_value(&state).unwrap();
    assert_eq!(json["highestActivatedSequence"], 7);
    assert_eq!(json["highestSeenRootSequence"], 11);
    assert_eq!(json["highestRetiredContractMajor"], 2);
    assert_eq!(json["active"]["releaseSequence"], 7);
    assert_eq!(json["activeSeal"]["identityKey"], "devino:7");
    assert!(json.get("highest_activated_sequence").is_none());

    let decoded: ReferenceStoreState = serde_json::from_value(json).unwrap();
    assert_eq!(decoded, state);
}

#[test]
fn codec_decodes_legacy_v1_and_round_trips_integrated_v3() {
    let active = version(5, 'e', 1);
    let decoded = ReferenceStateCodec::decode(&legacy_v1(Some(&active), 5)).unwrap();
    assert_eq!(decoded.active, Some(active.clone()));
    assert_eq!(decoded.active.unwrap().contract_major, 1);
    assert_eq!(decoded.highest_activated_sequence, 5);
    assert_eq!(decoded.highest_seen_root_sequence, 0);
    assert_eq!(decoded.active_seal, None);

    let state = ReferenceStoreState {
        active: Some(active),
        previous: Some(version(3, 'd', 1)),
        pending: Some(version(8, 'f', 1)),
        highest_activated_sequence: 5,
        highest_seen_root_sequence: 9,
        highest_seen_root_hash: Some("1".repeat(64)),
        highest_retired_contract_major: 1,
        active_seal: Some(seal(5)),
        previous_seal: Some(seal(3)),
        pending_seal: Some(seal(8)),
    };
    let encoded = ReferenceStateCodec::encode(&state).unwrap();
    assert_eq!(&encoded[2..14], b"MEDREFSTATE3");
    assert_eq!(ReferenceStateCodec::decode(&encoded).unwrap(), state);
}

#[test]
fn malformed_or_trailing_codec_bytes_fail_closed_without_resetting_state() {
    let mut store = ReferenceStore::default();
    store.observe_signed_root(4, &"a".repeat(64)).unwrap();
    let before = store.snapshot();
    let mut bytes = ReferenceStateCodec::encode(&before).unwrap();
    bytes.push(0xff);

    assert!(ReferenceStateCodec::decode(&bytes).is_err());
    assert_eq!(store.snapshot(), before);
    assert!(ReferenceStateCodec::decode(b"corrupt").is_err());
}

#[test]
fn repeated_startup_is_idempotent_after_activation_and_recovery() {
    let current = version(1, '1', 1);
    let pending = version(2, '2', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current).unwrap();
    store.stage_pending(pending.clone(), None).unwrap();

    assert_eq!(
        store.open_for_startup(1, true, true, true).unwrap(),
        Some(pending.clone())
    );
    let once = store.snapshot();
    assert_eq!(
        store.open_for_startup(1, true, true, true).unwrap(),
        Some(pending)
    );
    assert_eq!(store.snapshot(), once);
}

#[test]
fn valid_pending_replaces_corrupt_active_but_preserves_valid_previous_lkg() {
    let first = version(1, '1', 1);
    let active = version(7, '7', 1);
    let pending = version(9, '9', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(first.clone()).unwrap();
    store.stage_pending(active.clone(), None).unwrap();
    assert_eq!(
        store.open_for_startup(1, true, true, true).unwrap(),
        Some(active)
    );
    store.stage_pending(pending.clone(), None).unwrap();

    // The pending file was independently verified, the active file is
    // corrupt, and the previous file remains the last-known-good reference.
    assert_eq!(
        store.open_for_startup(1, false, true, true).unwrap(),
        Some(pending.clone())
    );
    let state = store.snapshot();
    assert_eq!(state.active, Some(pending));
    assert_eq!(state.previous, Some(first));
    assert_eq!(state.pending, None);
}

#[test]
fn invalid_pending_is_discarded_before_active_previous_selection() {
    let current = version(1, '1', 1);
    let pending = version(7, '7', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current.clone()).unwrap();
    store.stage_pending(pending, None).unwrap();

    assert_eq!(
        store.open_for_startup(1, true, true, false).unwrap(),
        Some(current.clone())
    );
    let state = store.snapshot();
    assert_eq!(state.active, Some(current));
    assert_eq!(state.pending, None);
}

#[test]
fn install_initial_replaces_existing_state_and_preserves_high_waters() {
    let current = version(1, '1', 1);
    let pending = version(7, '7', 1);
    let replacement = version(9, '9', 1);
    let root = "e".repeat(64);
    let mut store = ReferenceStore::default();
    store.install_initial(current).unwrap();
    store.stage_pending(pending, None).unwrap();
    store.mark_contract_retired(2, 8, &root).unwrap();

    assert_eq!(
        store.install_initial(replacement.clone()).unwrap(),
        replacement.clone()
    );
    let state = store.snapshot();
    assert_eq!(state.active, Some(replacement));
    assert_eq!(state.previous, None);
    assert_eq!(state.pending, None);
    assert_eq!(state.highest_activated_sequence, 9);
    assert_eq!(state.highest_seen_root_sequence, 8);
    assert_eq!(state.highest_seen_root_hash.as_deref(), Some(root.as_str()));
    assert_eq!(state.highest_retired_contract_major, 2);
}

#[test]
fn initial_install_equal_sequence_is_idempotent_only_for_the_same_identity() {
    let current = version(7, 'a', 1);
    let collision = version(7, 'b', 1);
    let mut store = ReferenceStore::default();
    store.install_initial(current.clone()).unwrap();
    let before = store.snapshot();

    assert!(store.install_initial(collision).is_err());
    assert_eq!(store.snapshot(), before);
    assert_eq!(store.install_initial(current.clone()).unwrap(), current);
}

#[test]
fn dataset_identity_requires_lowercase_hex_suffix() {
    let mut malformed = version(1, 'a', 1);
    malformed.dataset_id = format!("sha256:{}g", "a".repeat(63));
    assert!(ReferenceStore::default()
        .install_initial(malformed)
        .is_err());

    let mut json = serde_json::to_value(version(1, 'a', 1)).unwrap();
    json["datasetId"] = Value::String(format!("sha256:{}G", "a".repeat(63)));
    assert!(serde_json::from_value::<ReferenceVersion>(json).is_err());
}

#[test]
fn serde_state_deserialization_enforces_state_invariants() {
    let state = ReferenceStoreState {
        active: Some(version(7, 'a', 1)),
        highest_activated_sequence: 7,
        ..ReferenceStoreState::default()
    };
    let mut too_new = serde_json::to_value(&state).unwrap();
    too_new["active"]["releaseSequence"] = Value::from(8);
    assert!(serde_json::from_value::<ReferenceStoreState>(too_new).is_err());

    let mut malformed_root = serde_json::to_value(&state).unwrap();
    malformed_root["highestSeenRootSequence"] = Value::from(1);
    malformed_root["highestSeenRootHash"] = Value::String("not-a-hash".into());
    assert!(serde_json::from_value::<ReferenceStoreState>(malformed_root).is_err());
}

#[allow(dead_code)]
fn assert_json_object(value: &Value) {
    assert!(value.is_object());
}
