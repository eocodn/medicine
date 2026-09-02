use super::ReferenceLifecycleRuntime;
use crate::reference_manager::{
    ReferenceDatabaseValidator, ReferenceReleaseSource, ReferenceRuntimeError,
};
use crate::reference_runtime::ReferenceRuntime;
use crate::reference_state::ReferenceVersion;
use crate::{
    load_reference_trust_manifest, ReferenceArtifactKind, ReferenceBootstrapState,
    ReferenceReleaseArtifact, ReferenceRootSelection, VerifiedReferenceRelease,
};
use flate2::write::GzEncoder;
use flate2::Compression;
use sha2::{Digest, Sha256};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{
    atomic::{AtomicUsize, Ordering},
    Arc, Barrier,
};
use std::thread;

#[derive(Clone)]
struct FakeSource {
    release: VerifiedReferenceRelease,
    archive: Vec<u8>,
}

impl ReferenceReleaseSource for FakeSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError> {
        Ok(ReferenceRootSelection::Release(self.release.clone()))
    }

    fn download(
        &self,
        _artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), ReferenceRuntimeError> {
        std::fs::write(target, &self.archive)
            .map_err(|error| ReferenceRuntimeError::from_message(error.to_string()))
    }
}

#[derive(Clone)]
struct BlockingSource {
    release: VerifiedReferenceRelease,
    archive: Vec<u8>,
    download_entered: Arc<Barrier>,
    release_download: Arc<Barrier>,
    downloads: Arc<AtomicUsize>,
}

impl ReferenceReleaseSource for BlockingSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError> {
        Ok(ReferenceRootSelection::Release(self.release.clone()))
    }

    fn download(
        &self,
        _artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), ReferenceRuntimeError> {
        self.downloads.fetch_add(1, Ordering::SeqCst);
        self.download_entered.wait();
        self.release_download.wait();
        std::fs::write(target, &self.archive)
            .map_err(|error| ReferenceRuntimeError::from_message(error.to_string()))
    }
}

#[derive(Clone)]
struct FetchFailureSource {
    detail: &'static str,
}

impl ReferenceReleaseSource for FetchFailureSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError> {
        Err(ReferenceRuntimeError::from_message(self.detail))
    }

    fn download(
        &self,
        _artifact: &ReferenceReleaseArtifact,
        _target: &Path,
    ) -> Result<(), ReferenceRuntimeError> {
        unreachable!("fetch failure cannot reach artifact download")
    }
}

#[derive(Clone)]
struct DownloadFailureSource {
    release: VerifiedReferenceRelease,
}

impl ReferenceReleaseSource for DownloadFailureSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError> {
        Ok(ReferenceRootSelection::Release(self.release.clone()))
    }

    fn download(
        &self,
        _artifact: &ReferenceReleaseArtifact,
        _target: &Path,
    ) -> Result<(), ReferenceRuntimeError> {
        Err(ReferenceRuntimeError::from_message(
            "simulated artifact transport failure",
        ))
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
            .ok_or_else(|| ReferenceRuntimeError::from_message("candidate missing"))
    }
}

#[derive(Clone, Copy)]
struct RejectingValidator;

impl ReferenceDatabaseValidator for RejectingValidator {
    fn verify(
        &self,
        _file: &Path,
        _version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        Err(ReferenceRuntimeError::from_message(
            "simulated installed database verification failure",
        ))
    }
}

fn digest(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn fixture_release(sequence: i64) -> (VerifiedReferenceRelease, Vec<u8>) {
    let target = vec![7u8; 4096];
    let target_sha = digest(&target);
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(&target).unwrap();
    let archive = encoder.finish().unwrap();
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
            dataset_id: format!("sha256:{}", digest(b"dataset")),
            contract_major: 1,
            target_sha256: target_sha,
            target_size_bytes: target.len() as u64,
            full,
            patches: vec![],
        },
        archive,
    )
}

fn temp_root() -> PathBuf {
    std::env::temp_dir().join(format!(
        "medicine-reference-runtime-{}",
        uuid::Uuid::new_v4()
    ))
}

#[test]
fn runtime_owns_prepare_install_and_status_transitions() {
    let root = temp_root();
    let (release, archive) = fixture_release(31);
    let runtime = ReferenceLifecycleRuntime::new(
        root.clone(),
        1,
        FakeSource { release, archive },
        AcceptingValidator,
    );

    let prepared = runtime.prepare().expect("prepare bootstrap");
    assert!(prepared.selection.is_none());
    assert_eq!(
        prepared.snapshot.state,
        ReferenceBootstrapState::DownloadRequired
    );
    assert!(prepared.snapshot.total_bytes > 0);

    let installed = runtime.start().expect("install prepared bootstrap");
    assert!(installed
        .selection
        .as_ref()
        .and_then(|selection| selection.database.as_ref())
        .is_some());
    assert_eq!(installed.snapshot.state, ReferenceBootstrapState::Ready);

    let status = runtime.status();
    assert_eq!(status.state, ReferenceBootstrapState::Ready);
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn concurrent_start_is_idempotent_while_download_is_running() {
    let root = temp_root();
    let (release, archive) = fixture_release(32);
    let download_entered = Arc::new(Barrier::new(2));
    let release_download = Arc::new(Barrier::new(2));
    let downloads = Arc::new(AtomicUsize::new(0));
    let runtime = Arc::new(ReferenceLifecycleRuntime::new(
        root.clone(),
        1,
        BlockingSource {
            release,
            archive,
            download_entered: Arc::clone(&download_entered),
            release_download: Arc::clone(&release_download),
            downloads: Arc::clone(&downloads),
        },
        AcceptingValidator,
    ));
    runtime.prepare().expect("prepare bootstrap");

    let worker_runtime = Arc::clone(&runtime);
    let worker = thread::spawn(move || worker_runtime.start());
    download_entered.wait();

    let duplicate = runtime.start();
    assert_eq!(downloads.load(Ordering::SeqCst), 1);

    release_download.wait();
    let installed = worker.join().unwrap().expect("first start completes");
    assert_eq!(installed.snapshot.state, ReferenceBootstrapState::Ready);
    assert_eq!(downloads.load(Ordering::SeqCst), 1);
    let duplicate = duplicate.expect("duplicate start must be observable, not fail");
    assert!(duplicate.selection.is_none());
    assert!(matches!(
        duplicate.snapshot.state,
        ReferenceBootstrapState::Downloading | ReferenceBootstrapState::Installing
    ));
    let _ = std::fs::remove_dir_all(root);
}

#[test]
fn bootstrap_failures_are_classified_by_authoritative_runtime_phase() {
    let download_root = temp_root();
    let (download_release, _) = fixture_release(33);
    let download_runtime = ReferenceLifecycleRuntime::new(
        download_root.clone(),
        1,
        DownloadFailureSource {
            release: download_release,
        },
        AcceptingValidator,
    );
    download_runtime
        .prepare()
        .expect("prepare download failure fixture");
    assert!(download_runtime.start().is_err());
    let download_status = download_runtime.status();
    assert_eq!(download_status.state, ReferenceBootstrapState::Failed);
    assert_eq!(download_status.detail.as_deref(), Some("download_failed"));

    let install_root = temp_root();
    let (install_release, archive) = fixture_release(34);
    let install_runtime = ReferenceLifecycleRuntime::new(
        install_root.clone(),
        1,
        FakeSource {
            release: install_release,
            archive,
        },
        RejectingValidator,
    );
    install_runtime
        .prepare()
        .expect("prepare install failure fixture");
    assert!(install_runtime.start().is_err());
    let install_status = install_runtime.status();
    assert_eq!(install_status.state, ReferenceBootstrapState::Failed);
    assert_eq!(install_status.detail.as_deref(), Some("install_failed"));

    let _ = std::fs::remove_dir_all(download_root);
    let _ = std::fs::remove_dir_all(install_root);
}

#[test]
fn prepare_preserves_stable_manifest_and_network_diagnostic_codes() {
    for (raw, expected) in [
        ("network_failed: tls handshake", "network_failed"),
        ("manifest_http_503", "manifest_http_503"),
        ("manifest_json", "manifest_json"),
        ("manifest_signature: bad signature", "manifest_signature"),
        ("manifest_release: invalid root", "manifest_release"),
        ("unclassified manifest failure", "manifest_failed"),
    ] {
        let root = temp_root();
        let runtime = ReferenceLifecycleRuntime::new(
            root.clone(),
            1,
            FetchFailureSource { detail: raw },
            AcceptingValidator,
        );
        assert!(runtime.prepare().is_err());
        let status = runtime.status();
        assert_eq!(status.state, ReferenceBootstrapState::Failed, "{raw}");
        assert_eq!(status.detail.as_deref(), Some(expected), "{raw}");
        let _ = std::fs::remove_dir_all(root);
    }
}

#[test]
fn concrete_runtime_owns_shared_http_construction() {
    let trust = load_reference_trust_manifest(
        &Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../deploy/reference-signing-trusted-keys.json"),
    )
    .expect("load tracked trust manifest");
    let root = temp_root();
    let runtime = ReferenceRuntime::new(root, "https://example.invalid/", trust)
        .expect("construct shared HTTP runtime");

    assert_eq!(runtime.status().state, ReferenceBootstrapState::Checking);
}
