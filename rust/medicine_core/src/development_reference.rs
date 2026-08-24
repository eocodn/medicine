use crate::reference_artifacts::{
    apply_chunk_patch_verified, decompress_snapshot, NoopArtifactObserver,
};
use crate::reference_capabilities::{
    verify_reference_runtime_capabilities, verify_reference_runtime_materialization,
};
use crate::reference_db_verifier::verify_reference_database;
use crate::reference_lifecycle::{
    plan_reference_bootstrap, plan_reference_update, ReferenceUpdatePlan,
};
use crate::reference_signature::{
    ReferenceArtifactKind, ReferenceManifestVerifier, ReferenceReleaseArtifact,
    ReferenceRootSelection,
};
use crate::reference_state::{ReferenceStateCodec, ReferenceStore, ReferenceVersion};
use crate::reference_trust::load_reference_trust_manifest;
use std::fmt::{Display, Formatter};
use std::fs;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};

mod http;
mod storage;

use http::HttpsReferenceReleaseSource;
use storage::{
    atomic_write, available_bytes, io_error, normalize_checkpoint, verify_file_identity,
    ReferenceDirectoryLock,
};

const STORAGE_SAFETY_MARGIN_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Debug)]
pub struct DevelopmentReferenceError(String);

impl DevelopmentReferenceError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    pub fn from_message(message: impl Into<String>) -> Self {
        Self::new(message)
    }
}

impl Display for DevelopmentReferenceError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for DevelopmentReferenceError {}

#[derive(Debug, Clone)]
pub struct DevelopmentReferenceConfig {
    pub reference_dir: PathBuf,
    pub base_url: String,
    pub trust_manifest: PathBuf,
    pub contract_major: i32,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct DevelopmentReferenceSelection {
    pub database: Option<PathBuf>,
    pub unavailable_reason: Option<String>,
}

pub trait ReferenceReleaseSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, DevelopmentReferenceError>;
    fn download(
        &self,
        artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), DevelopmentReferenceError>;
}

pub trait ReferenceDatabaseValidator {
    fn verify(
        &self,
        file: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), DevelopmentReferenceError>;
}

#[derive(Debug, Default, Clone, Copy)]
pub struct RustReferenceDatabaseValidator;

impl ReferenceDatabaseValidator for RustReferenceDatabaseValidator {
    fn verify(
        &self,
        file: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), DevelopmentReferenceError> {
        verify_reference_database(file, version.contract_major as u64, &version.dataset_id)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        verify_reference_runtime_materialization(file)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        verify_reference_runtime_capabilities(file)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        Ok(())
    }
}

pub struct DevelopmentReferenceManager<S, V> {
    root: PathBuf,
    contract_major: i32,
    source: S,
    validator: V,
}

impl<S: ReferenceReleaseSource, V: ReferenceDatabaseValidator> DevelopmentReferenceManager<S, V> {
    pub fn new(root: PathBuf, contract_major: i32, source: S, validator: V) -> Self {
        Self {
            root,
            contract_major,
            source,
            validator,
        }
    }

    pub fn ensure_installed(
        &self,
    ) -> Result<DevelopmentReferenceSelection, DevelopmentReferenceError> {
        fs::create_dir_all(&self.root).map_err(io_error("create reference data directory"))?;
        let _operation_lock = ReferenceDirectoryLock::acquire(&self.root.join(".operation.lock"))?;
        let mut store = self.load_store()?;
        let before = store.snapshot();
        let active_valid = before
            .active
            .as_ref()
            .is_some_and(|version| self.is_installed_valid(version));
        let previous_valid = before
            .previous
            .as_ref()
            .is_some_and(|version| self.is_installed_valid(version));
        let pending_valid = before
            .pending
            .as_ref()
            .is_some_and(|version| self.is_installed_valid(version));
        let selected = store
            .open_for_startup(
                self.contract_major,
                active_valid,
                previous_valid,
                pending_valid,
            )
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        if store.snapshot() != before {
            self.persist_store(&store)?;
            self.cleanup_unreferenced(&store)?;
        }
        if let Some(current) = selected {
            if store.is_contract_retired(self.contract_major) {
                return Ok(unavailable());
            }
            match self.try_stage_update(&mut store, &current) {
                Ok(true) => return Ok(unavailable()),
                Ok(false) => {}
                Err(error) => eprintln!("reference update skipped; using LKG: {error}"),
            }
            return Ok(DevelopmentReferenceSelection {
                database: Some(self.file_for(&current)),
                unavailable_reason: None,
            });
        }
        if store.is_contract_retired(self.contract_major) {
            return Ok(unavailable());
        }
        self.bootstrap(&mut store)
    }

    fn bootstrap(
        &self,
        store: &mut ReferenceStore,
    ) -> Result<DevelopmentReferenceSelection, DevelopmentReferenceError> {
        let release = match self.source.fetch_latest()? {
            ReferenceRootSelection::Release(release) => release,
            ReferenceRootSelection::Retired {
                release_sequence,
                root_hash,
                ..
            } => {
                store
                    .mark_contract_retired(self.contract_major, release_sequence, &root_hash)
                    .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
                self.persist_store(store)?;
                return Ok(unavailable());
            }
        };
        store
            .observe_signed_root(release.release_sequence, &release.root_hash)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        self.persist_store(store)?;
        let plan = plan_reference_bootstrap(self.contract_major, &store.snapshot(), &release)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        self.cleanup_bootstrap_files(None)?;
        let final_path = self.file_for(&plan.target);
        if self.is_file_valid(&final_path, &plan.target) {
            store
                .install_initial(plan.target.clone())
                .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
            self.persist_store(store)?;
            self.cleanup_unreferenced(store)?;
            return Ok(DevelopmentReferenceSelection {
                database: Some(final_path),
                unavailable_reason: None,
            });
        }

        let checkpoint = self.root.join(format!(
            ".bootstrap-artifact-{}-{}.part",
            release.release_sequence, plan.full.sha256
        ));
        self.cleanup_bootstrap_files(Some(&checkpoint))?;
        normalize_checkpoint(&checkpoint, &plan.full)?;
        let checkpoint_size = checkpoint.metadata().map(|value| value.len()).unwrap_or(0);
        let required = (plan.full.size_bytes - checkpoint_size)
            .checked_add(release.target_size_bytes)
            .and_then(|value| value.checked_add(STORAGE_SAFETY_MARGIN_BYTES))
            .ok_or_else(|| DevelopmentReferenceError::new("reference storage size overflow"))?;
        let available = available_bytes(&self.root)?;
        if available < required {
            return Err(DevelopmentReferenceError::new(format!(
                "insufficient storage for reference bootstrap: required={required} available={available}"
            )));
        }
        self.source.download(&plan.full, &checkpoint)?;
        verify_file_identity(&checkpoint, plan.full.size_bytes, &plan.full.sha256)?;
        let candidate = self.root.join(format!(
            ".bootstrap-candidate-{}-{}.sqlite",
            release.release_sequence, release.target_sha256
        ));
        let _ = fs::remove_file(&candidate);
        let result = (|| {
            decompress_snapshot(
                &checkpoint,
                &candidate,
                release.target_size_bytes,
                &release.target_sha256,
                &mut NoopArtifactObserver,
            )
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
            self.validator.verify(&candidate, &plan.target)?;
            self.finish_content_addressed(&candidate, &final_path, &plan.target)?;
            store
                .install_initial(plan.target.clone())
                .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
            self.persist_store(store)?;
            let _ = fs::remove_file(&checkpoint);
            self.cleanup_bootstrap_files(None)?;
            self.cleanup_unreferenced(store)?;
            Ok(DevelopmentReferenceSelection {
                database: Some(final_path),
                unavailable_reason: None,
            })
        })();
        let _ = fs::remove_file(&candidate);
        result
    }

    /// Returns true only when an authenticated retirement makes the current
    /// runtime contract unavailable. Network/update failures retain the LKG.
    fn try_stage_update(
        &self,
        store: &mut ReferenceStore,
        current: &ReferenceVersion,
    ) -> Result<bool, DevelopmentReferenceError> {
        let release = match self.source.fetch_latest()? {
            ReferenceRootSelection::Release(release) => release,
            ReferenceRootSelection::Retired {
                release_sequence,
                root_hash,
                ..
            } => {
                store
                    .mark_contract_retired(current.contract_major, release_sequence, &root_hash)
                    .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
                self.persist_store(store)?;
                self.cleanup_update_files(None)?;
                return Ok(true);
            }
        };
        store
            .observe_signed_root(release.release_sequence, &release.root_hash)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        self.persist_store(store)?;
        match plan_reference_update(current, &store.snapshot(), &release)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?
        {
            ReferenceUpdatePlan::UpToDate | ReferenceUpdatePlan::RollbackRejected => {
                self.cleanup_update_files(None)?;
            }
            ReferenceUpdatePlan::IdentityConflict => {
                return Err(DevelopmentReferenceError::new(
                    "activated release sequence has a different signed target identity",
                ));
            }
            ReferenceUpdatePlan::Stage(stage) => {
                let target = stage.target;
                let primary = stage.primary;
                let fallback_full = stage.fallback_full;
                let prepared = self.prepare_update_artifact(current, &target, &primary);
                let candidate = match (prepared, fallback_full) {
                    (Ok(candidate), _) => candidate,
                    (Err(patch_error), Some(full)) => {
                        eprintln!(
                            "reference patch failed; falling back to signed full: {patch_error}"
                        );
                        self.prepare_update_artifact(current, &target, &full)?
                    }
                    (Err(error), None) => return Err(error),
                };
                let final_path = self.file_for(&target);
                self.finish_content_addressed(&candidate, &final_path, &target)?;
                store
                    .stage_pending(target, None)
                    .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
                self.persist_store(store)?;
                self.cleanup_update_files(None)?;
                self.cleanup_unreferenced(store)?;
            }
        }
        Ok(false)
    }

    fn prepare_update_artifact(
        &self,
        current: &ReferenceVersion,
        target: &ReferenceVersion,
        artifact: &ReferenceReleaseArtifact,
    ) -> Result<PathBuf, DevelopmentReferenceError> {
        let checkpoint = self.root.join(format!(
            ".artifact-{}-{}.part",
            target.release_sequence, artifact.sha256
        ));
        self.cleanup_update_files(Some(&checkpoint))?;
        let candidate = self.root.join(format!(
            ".candidate-{}-{}.sqlite",
            target.release_sequence, target.sha256
        ));
        let _ = fs::remove_file(&candidate);
        let preserve_checkpoint = artifact.kind == ReferenceArtifactKind::FullGzip;
        let result = (|| {
            self.source.download(artifact, &checkpoint)?;
            verify_file_identity(&checkpoint, artifact.size_bytes, &artifact.sha256)?;
            match artifact.kind {
                ReferenceArtifactKind::FullGzip => {
                    decompress_snapshot(
                        &checkpoint,
                        &candidate,
                        target.size_bytes as u64,
                        &target.sha256,
                        &mut NoopArtifactObserver,
                    )
                    .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
                }
                ReferenceArtifactKind::ChunkPatch => {
                    apply_chunk_patch_verified(
                        &self.file_for(current),
                        &checkpoint,
                        &candidate,
                        target.size_bytes as u64,
                        &target.sha256,
                        &mut NoopArtifactObserver,
                    )
                    .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
                }
            }
            self.validator.verify(&candidate, target)?;
            Ok(candidate.clone())
        })();
        if result.is_err() {
            let _ = fs::remove_file(&candidate);
            if !preserve_checkpoint {
                let _ = fs::remove_file(&checkpoint);
            }
        }
        result
    }

    fn load_store(&self) -> Result<ReferenceStore, DevelopmentReferenceError> {
        let path = self.root.join("state.v1");
        if !path.exists() {
            return Ok(ReferenceStore::default());
        }
        let bytes = fs::read(&path).map_err(io_error("read reference state"))?;
        let state = ReferenceStateCodec::decode(&bytes)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        ReferenceStore::from_state(state)
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))
    }

    fn persist_store(&self, store: &ReferenceStore) -> Result<(), DevelopmentReferenceError> {
        let bytes = ReferenceStateCodec::encode(&store.snapshot())
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        atomic_write(&self.root.join("state.v1"), &bytes)
    }

    fn file_for(&self, version: &ReferenceVersion) -> PathBuf {
        self.root.join(format!("mobile-{}.sqlite", version.sha256))
    }

    fn is_installed_valid(&self, version: &ReferenceVersion) -> bool {
        version.contract_major == self.contract_major
            && self.is_file_valid(&self.file_for(version), version)
    }

    fn is_file_valid(&self, path: &Path, version: &ReferenceVersion) -> bool {
        verify_file_identity(path, version.size_bytes as u64, &version.sha256).is_ok()
            && self.validator.verify(path, version).is_ok()
    }

    fn finish_content_addressed(
        &self,
        candidate: &Path,
        final_path: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), DevelopmentReferenceError> {
        if final_path.is_file() && self.is_file_valid(final_path, version) {
            let _ = fs::remove_file(candidate);
            return Ok(());
        }
        if final_path.exists() {
            fs::remove_file(final_path).map_err(io_error("remove invalid reference target"))?;
        }
        fs::rename(candidate, final_path).map_err(io_error("install verified reference target"))?;
        let mut permissions = fs::metadata(final_path)
            .map_err(io_error("read installed reference permissions"))?
            .permissions();
        permissions.set_mode(permissions.mode() & !0o222);
        fs::set_permissions(final_path, permissions)
            .map_err(io_error("make installed reference read-only"))?;
        Ok(())
    }

    fn cleanup_unreferenced(
        &self,
        store: &ReferenceStore,
    ) -> Result<(), DevelopmentReferenceError> {
        let state = store.snapshot();
        let keep = [state.active, state.previous, state.pending]
            .into_iter()
            .flatten()
            .map(|version| format!("mobile-{}.sqlite", version.sha256))
            .collect::<std::collections::HashSet<_>>();
        for entry in fs::read_dir(&self.root).map_err(io_error("list reference directory"))? {
            let entry = entry.map_err(io_error("read reference directory entry"))?;
            let name = entry.file_name().to_string_lossy().into_owned();
            if entry.path().is_file()
                && name.starts_with("mobile-")
                && name.ends_with(".sqlite")
                && !keep.contains(&name)
            {
                fs::remove_file(entry.path())
                    .map_err(io_error("remove unreferenced reference DB"))?;
            }
        }
        Ok(())
    }

    fn cleanup_bootstrap_files(
        &self,
        keep: Option<&Path>,
    ) -> Result<(), DevelopmentReferenceError> {
        self.cleanup_temporary_files(".bootstrap-artifact-", ".bootstrap-candidate-", keep)
    }

    fn cleanup_update_files(&self, keep: Option<&Path>) -> Result<(), DevelopmentReferenceError> {
        self.cleanup_temporary_files(".artifact-", ".candidate-", keep)
    }

    fn cleanup_temporary_files(
        &self,
        artifact_prefix: &str,
        candidate_prefix: &str,
        keep: Option<&Path>,
    ) -> Result<(), DevelopmentReferenceError> {
        let keep = keep.and_then(|path| path.canonicalize().ok());
        for entry in fs::read_dir(&self.root).map_err(io_error("list reference directory"))? {
            let entry = entry.map_err(io_error("read reference directory entry"))?;
            if !entry.path().is_file() {
                continue;
            }
            let name = entry.file_name().to_string_lossy().into_owned();
            let artifact = name.starts_with(artifact_prefix) && name.ends_with(".part");
            let candidate = name.starts_with(candidate_prefix) && name.ends_with(".sqlite");
            let is_keep = keep
                .as_ref()
                .is_some_and(|value| entry.path().canonicalize().ok().as_ref() == Some(value));
            if candidate || (artifact && !is_keep) {
                fs::remove_file(entry.path()).map_err(io_error("remove stale reference file"))?;
            }
        }
        Ok(())
    }
}

pub fn ensure_development_reference(
    config: DevelopmentReferenceConfig,
) -> Result<DevelopmentReferenceSelection, DevelopmentReferenceError> {
    if config.contract_major <= 0 {
        return Err(DevelopmentReferenceError::new(
            "reference contract major must be positive",
        ));
    }
    let trust = load_reference_trust_manifest(&config.trust_manifest)
        .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
    let verifier = ReferenceManifestVerifier::new(trust.keys);
    let source =
        HttpsReferenceReleaseSource::new(&config.base_url, verifier, config.contract_major as u64)?;
    DevelopmentReferenceManager::new(
        config.reference_dir,
        config.contract_major,
        source,
        RustReferenceDatabaseValidator,
    )
    .ensure_installed()
}

fn unavailable() -> DevelopmentReferenceSelection {
    DevelopmentReferenceSelection {
        database: None,
        unavailable_reason: Some("update_required".to_owned()),
    }
}
