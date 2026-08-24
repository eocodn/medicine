use medicine_core::reference_state::{ReferenceStoreState, ReferenceVersion};
use medicine_core::{
    plan_reference_bootstrap, plan_reference_update, ReferenceArtifactKind, ReferenceBootstrapPlan,
    ReferenceReleaseArtifact, ReferenceUpdatePlan, VerifiedReferenceRelease,
};

fn sha(ch: char) -> String {
    std::iter::repeat_n(ch, 64).collect()
}

fn dataset(ch: char) -> String {
    format!("sha256:{}", sha(ch))
}

fn full(contract_major: u64, target: &str, artifact_sha: &str) -> ReferenceReleaseArtifact {
    ReferenceReleaseArtifact {
        contract_major,
        key: format!("reference/v2/contracts/{contract_major}/full/{target}.sqlite.gz"),
        sha256: artifact_sha.to_owned(),
        size_bytes: 80,
        kind: ReferenceArtifactKind::FullGzip,
        from_sha256: None,
        from_size_bytes: None,
    }
}

fn patch(
    contract_major: u64,
    from: &str,
    from_size: u64,
    target: &str,
    artifact_sha: &str,
) -> ReferenceReleaseArtifact {
    ReferenceReleaseArtifact {
        contract_major,
        key: format!("reference/v2/contracts/{contract_major}/patch/{from}-{target}.mpatch"),
        sha256: artifact_sha.to_owned(),
        size_bytes: 40,
        kind: ReferenceArtifactKind::ChunkPatch,
        from_sha256: Some(from.to_owned()),
        from_size_bytes: Some(from_size),
    }
}

fn release(sequence: i64, target: char) -> VerifiedReferenceRelease {
    let target_sha = sha(target);
    VerifiedReferenceRelease {
        release_sequence: sequence,
        root_hash: sha('f'),
        dataset_id: dataset(target),
        contract_major: 1,
        target_sha256: target_sha.clone(),
        target_size_bytes: 200,
        full: full(1, &target_sha, &sha('e')),
        patches: vec![],
    }
}

fn version(sequence: i64, target: char) -> ReferenceVersion {
    ReferenceVersion {
        dataset_id: dataset(target),
        sha256: sha(target),
        size_bytes: 200,
        contract_major: 1,
        release_sequence: sequence,
    }
}

#[test]
fn bootstrap_rejects_release_below_activation_high_water() {
    let state = ReferenceStoreState {
        highest_activated_sequence: 8,
        ..ReferenceStoreState::default()
    };
    let error = plan_reference_bootstrap(1, &state, &release(7, 'b')).unwrap_err();
    assert!(error.to_string().contains("rollback"));
}

#[test]
fn bootstrap_returns_signed_full_target() {
    let plan = plan_reference_bootstrap(1, &ReferenceStoreState::default(), &release(7, 'b'))
        .expect("bootstrap plan");
    assert_eq!(
        plan,
        ReferenceBootstrapPlan {
            target: version(7, 'b'),
            full: full(1, &sha('b'), &sha('e')),
        }
    );
}

#[test]
fn update_is_up_to_date_when_signed_target_identity_is_unchanged() {
    let current = version(5, 'a');
    let mut next = release(9, 'a');
    next.dataset_id = current.dataset_id.clone();
    next.target_size_bytes = current.size_bytes as u64;
    let state = ReferenceStoreState {
        active: Some(current.clone()),
        highest_activated_sequence: 5,
        ..ReferenceStoreState::default()
    };
    assert_eq!(
        plan_reference_update(&current, &state, &next).unwrap(),
        ReferenceUpdatePlan::UpToDate
    );
}

#[test]
fn update_rejects_different_target_at_activated_sequence() {
    let current = version(5, 'a');
    let state = ReferenceStoreState {
        active: Some(current.clone()),
        highest_activated_sequence: 5,
        ..ReferenceStoreState::default()
    };
    assert_eq!(
        plan_reference_update(&current, &state, &release(5, 'b')).unwrap(),
        ReferenceUpdatePlan::IdentityConflict
    );
}

#[test]
fn update_prefers_exact_direct_patch_and_keeps_full_as_fallback() {
    let current = version(5, 'a');
    let mut next = release(6, 'b');
    let direct = patch(
        1,
        &current.sha256,
        current.size_bytes as u64,
        &next.target_sha256,
        &sha('d'),
    );
    next.patches.push(direct.clone());
    let state = ReferenceStoreState {
        active: Some(current.clone()),
        highest_activated_sequence: 5,
        ..ReferenceStoreState::default()
    };
    let plan = plan_reference_update(&current, &state, &next).expect("update plan");
    match plan {
        ReferenceUpdatePlan::Stage(stage) => {
            assert_eq!(stage.target, version(6, 'b'));
            assert_eq!(stage.primary, direct);
            assert_eq!(stage.fallback_full, Some(next.full));
        }
        other => panic!("unexpected plan: {other:?}"),
    }
}

#[test]
fn update_uses_full_when_no_exact_direct_patch_matches() {
    let current = version(5, 'a');
    let mut next = release(6, 'b');
    next.patches
        .push(patch(1, &sha('c'), 200, &next.target_sha256, &sha('d')));
    let state = ReferenceStoreState {
        active: Some(current.clone()),
        highest_activated_sequence: 5,
        ..ReferenceStoreState::default()
    };
    let plan = plan_reference_update(&current, &state, &next).expect("update plan");
    match plan {
        ReferenceUpdatePlan::Stage(stage) => {
            assert_eq!(stage.primary, next.full);
            assert_eq!(stage.fallback_full, None);
        }
        other => panic!("unexpected plan: {other:?}"),
    }
}
