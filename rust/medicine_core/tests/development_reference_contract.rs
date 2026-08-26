#![cfg(feature = "web")]

use flate2::write::GzEncoder;
use flate2::Compression;
use medicine_core::development_reference::{
    DevelopmentReferenceError, DevelopmentReferenceManager, DevelopmentReferenceUpdateStatus,
    ReferenceDatabaseValidator, ReferenceReleaseSource,
};
use medicine_core::reference_state::ReferenceStateCodec;
use medicine_core::{
    ReferenceArtifactKind, ReferenceReleaseArtifact, ReferenceRootSelection,
    VerifiedReferenceRelease,
};
use sha2::{Digest, Sha256};
use std::collections::HashMap;
use std::io::{Read, Write};
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

#[derive(Clone)]
struct FakeSource {
    root: Result<ReferenceRootSelection, String>,
    artifacts: HashMap<String, Vec<u8>>,
}

impl ReferenceReleaseSource for FakeSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, DevelopmentReferenceError> {
        self.root
            .clone()
            .map_err(DevelopmentReferenceError::from_message)
    }

    fn download(
        &self,
        artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), DevelopmentReferenceError> {
        let bytes = self
            .artifacts
            .get(&artifact.sha256)
            .ok_or_else(|| DevelopmentReferenceError::from_message("missing fake artifact"))?;
        std::fs::write(target, bytes)
            .map_err(|error| DevelopmentReferenceError::from_message(error.to_string()))
    }
}

#[derive(Clone, Copy)]
struct PanicSource;

impl ReferenceReleaseSource for PanicSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, DevelopmentReferenceError> {
        panic!("valid LKG startup must not fetch latest release")
    }

    fn download(
        &self,
        _artifact: &ReferenceReleaseArtifact,
        _target: &Path,
    ) -> Result<(), DevelopmentReferenceError> {
        panic!("valid LKG startup must not download reference artifacts")
    }
}

#[derive(Clone, Copy)]
struct AcceptingValidator;

impl ReferenceDatabaseValidator for AcceptingValidator {
    fn verify(
        &self,
        file: &Path,
        _version: &medicine_core::reference_state::ReferenceVersion,
    ) -> Result<(), DevelopmentReferenceError> {
        if file.is_file() {
            Ok(())
        } else {
            Err(DevelopmentReferenceError::from_message(
                "candidate does not exist",
            ))
        }
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

fn fixture_release(sequence: i64, marker: u8) -> (VerifiedReferenceRelease, Vec<u8>) {
    let target = vec![marker; 4096];
    let target_sha = digest(&target);
    let archive = gzip(&target);
    let archive_sha = digest(&archive);
    let full = ReferenceReleaseArtifact {
        contract_major: 1,
        key: format!("reference/v2/contracts/1/full/{target_sha}.sqlite.gz"),
        sha256: archive_sha,
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

fn source_for(release: VerifiedReferenceRelease, archive: Vec<u8>) -> FakeSource {
    FakeSource {
        artifacts: HashMap::from([(release.full.sha256.clone(), archive)]),
        root: Ok(ReferenceRootSelection::Release(release)),
    }
}

fn temp_root() -> PathBuf {
    std::env::temp_dir().join(format!(
        "medicine-development-reference-{}",
        uuid::Uuid::new_v4()
    ))
}

#[test]
fn first_bootstrap_installs_content_addressed_reference_and_state() {
    let root = temp_root();
    let (release, archive) = fixture_release(17, 1);
    let manager = DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release.clone(), archive),
        AcceptingValidator,
    );
    let selected = manager.ensure_installed().expect("bootstrap");
    let expected = root.join(format!("mobile-{}.sqlite", release.target_sha256));
    assert_eq!(selected.database.as_deref(), Some(expected.as_path()));
    assert!(expected.is_file());
    assert!(root.join("state.v1").is_file());
    let state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(state.active.unwrap().release_sequence, 17);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn bootstrap_repairs_writable_content_addressed_file_before_state_adoption() {
    let root = temp_root();
    std::fs::create_dir_all(&root).unwrap();
    let (release, archive) = fixture_release(17, 7);
    let mut decoder = flate2::read::GzDecoder::new(archive.as_slice());
    let mut target = Vec::new();
    decoder.read_to_end(&mut target).unwrap();
    let final_path = root.join(format!("mobile-{}.sqlite", release.target_sha256));
    std::fs::write(&final_path, target).unwrap();
    let mut permissions = std::fs::metadata(&final_path).unwrap().permissions();
    permissions.set_mode(0o644);
    std::fs::set_permissions(&final_path, permissions).unwrap();

    let selected = DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release.clone(), archive),
        AcceptingValidator,
    )
    .ensure_installed()
    .expect("adopt crash-after-rename artifact");

    assert_eq!(selected.database.as_deref(), Some(final_path.as_path()));
    let mode = std::fs::metadata(&final_path).unwrap().permissions().mode();
    assert_eq!(mode & 0o222, 0, "adopted reference must be read-only");
    let state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(state.active.unwrap().release_sequence, 17);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn valid_lkg_startup_does_not_fetch_latest() {
    let root = temp_root();
    let (release, archive) = fixture_release(17, 2);
    DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release.clone(), archive),
        AcceptingValidator,
    )
    .ensure_installed()
    .unwrap();
    let installed = root.join(format!("mobile-{}.sqlite", release.target_sha256));
    let mut permissions = std::fs::metadata(&installed).unwrap().permissions();
    permissions.set_mode(0o644);
    std::fs::set_permissions(&installed, permissions).unwrap();

    let selected =
        DevelopmentReferenceManager::new(root.clone(), 1, PanicSource, AcceptingValidator)
            .ensure_installed()
            .expect("local LKG startup");
    assert_eq!(selected.database, Some(installed.clone()));
    let mode = std::fs::metadata(&installed).unwrap().permissions().mode();
    assert_eq!(
        mode & 0o222,
        0,
        "referenced LKG must be resealed before use"
    );
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn update_is_staged_and_activates_on_next_start() {
    let root = temp_root();
    let (release1, archive1) = fixture_release(17, 3);
    DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release1.clone(), archive1),
        AcceptingValidator,
    )
    .ensure_installed()
    .unwrap();

    let (release2, archive2) = fixture_release(18, 4);
    let update_manager = DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release2.clone(), archive2),
        AcceptingValidator,
    );
    let startup = update_manager
        .ensure_installed()
        .expect("start from existing LKG before update check");
    assert_eq!(
        startup.database,
        Some(root.join(format!("mobile-{}.sqlite", release1.target_sha256)))
    );
    let before_update =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert!(before_update.pending.is_none());
    assert_eq!(
        update_manager.check_for_update().expect("stage update"),
        DevelopmentReferenceUpdateStatus::Staged
    );
    let staged =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(staged.pending.unwrap().release_sequence, 18);

    let next = DevelopmentReferenceManager::new(root.clone(), 1, PanicSource, AcceptingValidator)
        .ensure_installed()
        .expect("activate pending without an update fetch");
    assert_eq!(
        next.database,
        Some(root.join(format!("mobile-{}.sqlite", release2.target_sha256)))
    );
    let activated =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(activated.active.unwrap().release_sequence, 18);
    assert_eq!(activated.previous.unwrap().release_sequence, 17);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn authenticated_update_retirement_is_applied_only_by_explicit_update_check() {
    let root = temp_root();
    let (release, archive) = fixture_release(17, 8);
    DevelopmentReferenceManager::new(
        root.clone(),
        1,
        source_for(release.clone(), archive),
        AcceptingValidator,
    )
    .ensure_installed()
    .unwrap();

    let retired = ReferenceRootSelection::Retired {
        release_sequence: 19,
        root_hash: digest(b"retired-update-root"),
        current_contract_major: 2,
        minimum_supported_contract_major: 2,
    };
    let manager = DevelopmentReferenceManager::new(
        root.clone(),
        1,
        FakeSource {
            root: Ok(retired),
            artifacts: HashMap::new(),
        },
        AcceptingValidator,
    );
    let startup = manager
        .ensure_installed()
        .expect("local startup before retirement check");
    assert_eq!(
        startup.database,
        Some(root.join(format!("mobile-{}.sqlite", release.target_sha256)))
    );
    assert_eq!(
        manager
            .check_for_update()
            .expect("authenticated retirement check"),
        DevelopmentReferenceUpdateStatus::UpdateRequired
    );
    let state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(state.highest_retired_contract_major, 1);
    assert_eq!(state.highest_seen_root_sequence, 19);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn authenticated_contract_retirement_is_persisted_and_fails_closed() {
    let root = temp_root();
    let retired = ReferenceRootSelection::Retired {
        release_sequence: 19,
        root_hash: digest(b"retired-root"),
        current_contract_major: 2,
        minimum_supported_contract_major: 2,
    };
    let source = FakeSource {
        root: Ok(retired),
        artifacts: HashMap::new(),
    };
    let selected = DevelopmentReferenceManager::new(root.clone(), 1, source, AcceptingValidator)
        .ensure_installed()
        .expect("retirement is a valid terminal state");
    assert_eq!(selected.database, None);
    assert_eq!(
        selected.unavailable_reason.as_deref(),
        Some("update_required")
    );
    let state =
        ReferenceStateCodec::decode(&std::fs::read(root.join("state.v1")).unwrap()).unwrap();
    assert_eq!(state.highest_retired_contract_major, 1);
    assert_eq!(state.highest_seen_root_sequence, 19);
    let _ = std::fs::remove_dir_all(root);
}
