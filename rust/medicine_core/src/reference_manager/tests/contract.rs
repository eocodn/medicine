use super::TestReferenceManagerExt;
use crate::reference_manager::{
    ReferenceDatabaseValidator, ReferenceManager, ReferenceReleaseSource, ReferenceRuntimeError,
    ReferenceUpdateStatus,
};
use crate::reference_state::{ReferenceStateCodec, ReferenceVersion};
use crate::{
    ReferenceArtifactKind, ReferenceReleaseArtifact, ReferenceRootSelection,
    VerifiedReferenceRelease,
};
use flate2::write::GzEncoder;
use flate2::Compression;
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc,
};

#[derive(Clone)]
struct FakeSource {
    release: VerifiedReferenceRelease,
    artifacts: HashMap<String, Vec<u8>>,
}

impl ReferenceReleaseSource for FakeSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError> {
        Ok(ReferenceRootSelection::Release(self.release.clone()))
    }

    fn download(
        &self,
        artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), ReferenceRuntimeError> {
        let bytes = self
            .artifacts
            .get(&artifact.sha256)
            .expect("fixture artifact");
        std::fs::write(target, bytes)
            .map_err(|e| ReferenceRuntimeError::from_message(e.to_string()))
    }
}

#[derive(Clone, Copy)]
struct AcceptingValidator;
impl ReferenceDatabaseValidator for AcceptingValidator {
    fn verify(
        &self,
        file: &Path,
        _version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        file.is_file()
            .then_some(())
            .ok_or_else(|| ReferenceRuntimeError::from_message("missing candidate"))
    }
}

#[derive(Clone, Default)]
struct CountingValidator {
    full_verifications: Arc<AtomicUsize>,
    capability_verifications: Arc<AtomicUsize>,
}

impl ReferenceDatabaseValidator for CountingValidator {
    fn verify(
        &self,
        file: &Path,
        _version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        self.full_verifications.fetch_add(1, Ordering::SeqCst);
        file.is_file()
            .then_some(())
            .ok_or_else(|| ReferenceRuntimeError::from_message("missing candidate"))
    }

    fn verify_runtime_capabilities(
        &self,
        file: &Path,
        _version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        self.capability_verifications.fetch_add(1, Ordering::SeqCst);
        file.is_file()
            .then_some(())
            .ok_or_else(|| ReferenceRuntimeError::from_message("missing installed reference"))
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}
fn gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(bytes).unwrap();
    encoder.finish().unwrap()
}
fn fixture(sequence: i64, marker: u8) -> (VerifiedReferenceRelease, Vec<u8>) {
    let target = vec![marker; 4096];
    let target_sha = digest(&target);
    let archive = gzip(&target);
    let full = ReferenceReleaseArtifact {
        contract_major: 1,
        key: format!("reference/v2/contracts/1/full/{target_sha}.sqlite.gz"),
        sha256: digest(&archive),
        size_bytes: archive.len() as u64,
        kind: ReferenceArtifactKind::FullGzip,
        from_sha256: None,
        from_size_bytes: None,
    };
    (
        VerifiedReferenceRelease {
            release_sequence: sequence,
            root_hash: digest(format!("root-{sequence}").as_bytes()),
            dataset_id: format!("sha256:{}", digest(format!("dataset-{marker}").as_bytes())),
            contract_major: 1,
            target_sha256: target_sha,
            target_size_bytes: target.len() as u64,
            full,
            patches: vec![],
        },
        archive,
    )
}
fn root() -> PathBuf {
    std::env::temp_dir().join(format!(
        "medicine-reference-manager-{}",
        uuid::Uuid::new_v4()
    ))
}
fn source(release: VerifiedReferenceRelease, archive: Vec<u8>) -> FakeSource {
    FakeSource {
        artifacts: HashMap::from([(release.full.sha256.clone(), archive)]),
        release,
    }
}

#[test]
fn shared_manager_bootstraps_and_stages_update_without_web_feature() {
    let root = root();
    let (v1, a1) = fixture(10, 1);
    ReferenceManager::new(root.clone(), 1, source(v1.clone(), a1), AcceptingValidator)
        .ensure_installed()
        .expect("bootstrap");
    let installed_state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.json")).unwrap()).unwrap();
    assert!(installed_state.active_seal.is_some());
    let (v2, a2) = fixture(11, 2);
    let manager =
        ReferenceManager::new(root.clone(), 1, source(v2.clone(), a2), AcceptingValidator);
    assert_eq!(
        manager.check_for_update().expect("stage"),
        ReferenceUpdateStatus::Staged
    );
    let state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.json")).unwrap()).unwrap();
    assert_eq!(state.pending.unwrap().release_sequence, 11);
    assert!(state.pending_seal.is_some());
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn unchanged_installed_reference_uses_file_seal_without_full_reverification() {
    let root = root();
    let (release, archive) = fixture(50, 5);
    let source = source(release, archive);
    let validator = CountingValidator::default();

    ReferenceManager::new(root.clone(), 1, source.clone(), validator.clone())
        .ensure_installed()
        .expect("bootstrap sealed reference");
    validator.full_verifications.store(0, Ordering::SeqCst);
    validator
        .capability_verifications
        .store(0, Ordering::SeqCst);

    let selected = ReferenceManager::new(root.clone(), 1, source, validator.clone())
        .open_installed()
        .expect("open unchanged sealed reference");
    assert!(selected.database.is_some());
    assert_eq!(validator.full_verifications.load(Ordering::SeqCst), 0);
    assert_eq!(validator.capability_verifications.load(Ordering::SeqCst), 1);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn update_status_has_one_shared_wire_name() {
    assert_eq!(ReferenceUpdateStatus::NoChange.as_str(), "no_change");
    assert_eq!(ReferenceUpdateStatus::Staged.as_str(), "staged");
    assert_eq!(
        ReferenceUpdateStatus::UpdateRequired.as_str(),
        "update_required"
    );
}
