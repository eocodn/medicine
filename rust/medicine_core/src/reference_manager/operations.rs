use super::{
    ReferenceBootstrapObserver, ReferenceBootstrapPreparation, ReferenceDatabaseValidator,
    ReferenceManager, ReferenceReleaseSource, ReferenceRuntimeError, ReferenceSelection,
    ReferenceUpdateStatus,
};
use crate::reference_artifacts::{
    apply_chunk_patch_verified, decompress_snapshot, NoopArtifactObserver,
};
use crate::reference_lifecycle::{
    plan_reference_bootstrap, plan_reference_update, ReferenceBootstrapPlan, ReferenceUpdatePlan,
};
use crate::reference_signature::{
    ReferenceArtifactKind, ReferenceReleaseArtifact, ReferenceRootSelection,
    VerifiedReferenceRelease,
};
use crate::reference_state::{
    ReferenceFileSeal, ReferenceStateCodec, ReferenceStore, ReferenceVersion,
};
use std::fs;
use std::path::{Path, PathBuf};

use super::storage::{
    atomic_write, available_bytes, capture_file_seal, io_error, normalize_checkpoint,
    recover_android_atomic_file_state, seal_read_only, sync_directory, verify_file_identity,
    ReferenceDirectoryLock,
};

const STORAGE_SAFETY_MARGIN_BYTES: u64 = 16 * 1024 * 1024;

#[derive(Debug)]
struct StartupVerification {
    valid: bool,
    seal: Option<ReferenceFileSeal>,
}

impl StartupVerification {
    fn invalid() -> Self {
        Self {
            valid: false,
            seal: None,
        }
    }
}

impl<S: ReferenceReleaseSource, V: ReferenceDatabaseValidator> ReferenceManager<S, V> {
    pub(crate) fn new(root: PathBuf, contract_major: i32, source: S, validator: V) -> Self {
        Self {
            root,
            contract_major,
            source,
            validator,
        }
    }

    pub(crate) fn reference_dir(&self) -> &Path {
        &self.root
    }

    pub(crate) fn prepare_bootstrap(
        &self,
    ) -> Result<ReferenceBootstrapPreparation, ReferenceRuntimeError> {
        fs::create_dir_all(&self.root).map_err(io_error("create reference data directory"))?;
        let _operation_lock = ReferenceDirectoryLock::acquire(&self.root.join(".operation.lock"))?;
        let mut store = self.load_store()?;
        if let Some(selection) = self.open_installed_exclusive(&mut store)? {
            return Ok(if selection.database.is_some() {
                ReferenceBootstrapPreparation::Ready(selection)
            } else {
                ReferenceBootstrapPreparation::Unavailable
            });
        }

        let release = match self.source.fetch_latest()? {
            ReferenceRootSelection::Release(release) => release,
            ReferenceRootSelection::Retired {
                release_sequence,
                root_hash,
                ..
            } => {
                store
                    .mark_contract_retired(self.contract_major, release_sequence, &root_hash)
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
                self.persist_store(&store)?;
                return Ok(ReferenceBootstrapPreparation::Unavailable);
            }
        };
        store
            .observe_signed_root(release.release_sequence, &release.root_hash)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        self.persist_store(&store)?;
        let plan = plan_reference_bootstrap(self.contract_major, &store.snapshot(), &release)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        let checkpoint = self.root.join(format!(
            ".bootstrap-artifact-{}-{}.part",
            release.release_sequence, plan.full.sha256
        ));
        self.cleanup_bootstrap_files(Some(&checkpoint))?;
        normalize_checkpoint(&checkpoint, &plan.full)?;
        let checkpoint_bytes = checkpoint.metadata().map(|value| value.len()).unwrap_or(0);
        Ok(ReferenceBootstrapPreparation::Download {
            release,
            checkpoint_bytes,
        })
    }

    pub(crate) fn install_prepared_with_observer<O: ReferenceBootstrapObserver>(
        &self,
        preparation: ReferenceBootstrapPreparation,
        observer: &mut O,
    ) -> Result<ReferenceSelection, ReferenceRuntimeError> {
        match preparation {
            ReferenceBootstrapPreparation::Ready(selection) => Ok(selection),
            ReferenceBootstrapPreparation::Unavailable => Ok(unavailable()),
            ReferenceBootstrapPreparation::Download { release, .. } => {
                fs::create_dir_all(&self.root)
                    .map_err(io_error("create reference data directory"))?;
                let _operation_lock =
                    ReferenceDirectoryLock::acquire(&self.root.join(".operation.lock"))?;
                let mut store = self.load_store()?;
                if let Some(selection) = self.open_installed_exclusive(&mut store)? {
                    return Ok(selection);
                }
                store
                    .observe_signed_root(release.release_sequence, &release.root_hash)
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
                self.persist_store(&store)?;
                let plan =
                    plan_reference_bootstrap(self.contract_major, &store.snapshot(), &release)
                        .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
                self.bootstrap_prepared(&mut store, release, plan, observer)
            }
        }
    }

    fn open_installed_exclusive(
        &self,
        store: &mut ReferenceStore,
    ) -> Result<Option<ReferenceSelection>, ReferenceRuntimeError> {
        let before = store.snapshot();
        let pending = before
            .pending
            .as_ref()
            .map(|version| self.verify_for_startup(version, before.pending_seal.as_ref()))
            .unwrap_or_else(StartupVerification::invalid);
        let active = before
            .active
            .as_ref()
            .map(|version| self.verify_for_startup(version, before.active_seal.as_ref()))
            .unwrap_or_else(StartupVerification::invalid);
        let previous = if active.valid {
            StartupVerification {
                valid: false,
                seal: before.previous_seal.clone(),
            }
        } else {
            before
                .previous
                .as_ref()
                .map(|version| self.verify_for_startup(version, before.previous_seal.as_ref()))
                .unwrap_or_else(StartupVerification::invalid)
        };
        store
            .set_role_seals(active.seal, previous.seal, pending.seal)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        let selected = store
            .open_for_startup(
                self.contract_major,
                active.valid,
                previous.valid,
                pending.valid,
            )
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        if store.snapshot() != before {
            self.persist_store(store)?;
            self.cleanup_unreferenced(store)?;
        }
        if let Some(current) = selected {
            if store.is_contract_retired(self.contract_major) {
                return Ok(Some(unavailable()));
            }
            return Ok(Some(ReferenceSelection {
                database: Some(self.file_for(&current)),
                unavailable_reason: None,
            }));
        }
        if store.is_contract_retired(self.contract_major) {
            return Ok(Some(unavailable()));
        }
        Ok(None)
    }

    pub(crate) fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        fs::create_dir_all(&self.root).map_err(io_error("create reference data directory"))?;
        let _operation_lock = ReferenceDirectoryLock::acquire(&self.root.join(".operation.lock"))?;
        let mut store = self.load_store()?;
        if store.is_contract_retired(self.contract_major) {
            return Ok(ReferenceUpdateStatus::UpdateRequired);
        }
        let selection = self.open_installed_exclusive(&mut store)?;
        if selection
            .as_ref()
            .is_none_or(|selection| selection.database.is_none())
        {
            return Err(ReferenceRuntimeError::new(
                "cannot check for reference update without a valid active LKG",
            ));
        }
        let current = store.snapshot().active.ok_or_else(|| {
            ReferenceRuntimeError::new(
                "cannot check for reference update without a valid active LKG",
            )
        })?;
        self.try_stage_update(&mut store, &current)
    }

    fn bootstrap_prepared<O: ReferenceBootstrapObserver>(
        &self,
        store: &mut ReferenceStore,
        release: VerifiedReferenceRelease,
        plan: ReferenceBootstrapPlan,
        observer: &mut O,
    ) -> Result<ReferenceSelection, ReferenceRuntimeError> {
        let final_path = self.file_for(&plan.target);
        if self
            .ensure_installed_file(&final_path, &plan.target)
            .is_ok()
        {
            observer.installing()?;
            let seal = self.capture_verified_seal(&final_path)?;
            store
                .install_initial_with_seal(plan.target.clone(), Some(seal))
                .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
            self.persist_store(store)?;
            self.cleanup_bootstrap_files(None)?;
            self.cleanup_unreferenced(store)?;
            return Ok(ReferenceSelection {
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
            .ok_or_else(|| ReferenceRuntimeError::new("reference storage size overflow"))?;
        let available = available_bytes(&self.root)?;
        if available < required {
            return Err(ReferenceRuntimeError::new(format!(
                "insufficient storage for reference bootstrap: required={required} available={available}"
            )));
        }
        self.source.download(&plan.full, &checkpoint)?;
        verify_file_identity(&checkpoint, plan.full.size_bytes, &plan.full.sha256)?;
        observer.installing()?;
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
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
            self.validator.verify(&candidate, &plan.target)?;
            self.finish_content_addressed(&candidate, &final_path, &plan.target)?;
            let seal = self.capture_verified_seal(&final_path)?;
            store
                .install_initial_with_seal(plan.target.clone(), Some(seal))
                .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
            self.persist_store(store)?;
            let _ = fs::remove_file(&checkpoint);
            self.cleanup_bootstrap_files(None)?;
            self.cleanup_unreferenced(store)?;
            Ok(ReferenceSelection {
                database: Some(final_path),
                unavailable_reason: None,
            })
        })();
        let _ = fs::remove_file(&candidate);
        result
    }

    fn try_stage_update(
        &self,
        store: &mut ReferenceStore,
        current: &ReferenceVersion,
    ) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        let release = match self.source.fetch_latest()? {
            ReferenceRootSelection::Release(release) => release,
            ReferenceRootSelection::Retired {
                release_sequence,
                root_hash,
                ..
            } => {
                store
                    .mark_contract_retired(current.contract_major, release_sequence, &root_hash)
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
                self.persist_store(store)?;
                self.cleanup_update_files(None)?;
                return Ok(ReferenceUpdateStatus::UpdateRequired);
            }
        };
        store
            .observe_signed_root(release.release_sequence, &release.root_hash)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        self.persist_store(store)?;
        match plan_reference_update(current, &store.snapshot(), &release)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?
        {
            ReferenceUpdatePlan::UpToDate | ReferenceUpdatePlan::RollbackRejected => {
                self.cleanup_update_files(None)?;
                Ok(ReferenceUpdateStatus::NoChange)
            }
            ReferenceUpdatePlan::IdentityConflict => Err(ReferenceRuntimeError::new(
                "activated release sequence has a different signed target identity",
            )),
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
                let seal = self.capture_verified_seal(&final_path)?;
                store
                    .stage_pending(target, Some(seal))
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
                self.persist_store(store)?;
                self.cleanup_update_files(None)?;
                self.cleanup_unreferenced(store)?;
                Ok(ReferenceUpdateStatus::Staged)
            }
        }
    }

    fn prepare_update_artifact(
        &self,
        current: &ReferenceVersion,
        target: &ReferenceVersion,
        artifact: &ReferenceReleaseArtifact,
    ) -> Result<PathBuf, ReferenceRuntimeError> {
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
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
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
                    .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
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

    fn load_store(&self) -> Result<ReferenceStore, ReferenceRuntimeError> {
        let path = self.root.join("state.v1");
        recover_android_atomic_file_state(&path)?;
        if !path.exists() {
            return Ok(ReferenceStore::default());
        }
        let bytes = fs::read(&path).map_err(io_error("read reference state"))?;
        let state = ReferenceStateCodec::decode(&bytes)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        ReferenceStore::from_state(state)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))
    }

    fn persist_store(&self, store: &ReferenceStore) -> Result<(), ReferenceRuntimeError> {
        let bytes = ReferenceStateCodec::encode(&store.snapshot())
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        atomic_write(&self.root.join("state.v1"), &bytes)
    }

    fn file_for(&self, version: &ReferenceVersion) -> PathBuf {
        self.root.join(format!("mobile-{}.sqlite", version.sha256))
    }

    fn verify_for_startup(
        &self,
        version: &ReferenceVersion,
        stored_seal: Option<&ReferenceFileSeal>,
    ) -> StartupVerification {
        if version.contract_major != self.contract_major {
            return StartupVerification::invalid();
        }
        let path = self.file_for(version);
        let current_seal = match capture_file_seal(&path) {
            Ok(Some(seal)) if seal.size_bytes == version.size_bytes => seal,
            _ => return StartupVerification::invalid(),
        };
        // A seal proves that immutable bytes are unchanged, but an upgraded
        // binary can still reject an older physical DB layout. Keep the cheap
        // capability check on every startup before taking the seal fast path.
        if self
            .validator
            .verify_runtime_capabilities(&path, version)
            .is_err()
        {
            return StartupVerification::invalid();
        }
        if stored_seal == Some(&current_seal) && !current_seal.writable {
            return StartupVerification {
                valid: true,
                seal: Some(current_seal),
            };
        }
        if self.validate_file(&path, version).is_err() || seal_read_only(&path).is_err() {
            return StartupVerification::invalid();
        }
        match self.capture_verified_seal(&path) {
            Ok(seal) => StartupVerification {
                valid: true,
                seal: Some(seal),
            },
            Err(_) => StartupVerification::invalid(),
        }
    }

    fn capture_verified_seal(
        &self,
        path: &Path,
    ) -> Result<ReferenceFileSeal, ReferenceRuntimeError> {
        let seal = capture_file_seal(path)?
            .ok_or_else(|| ReferenceRuntimeError::new("verified reference file is missing"))?;
        if seal.writable {
            return Err(ReferenceRuntimeError::new(
                "verified reference file remains writable",
            ));
        }
        Ok(seal)
    }

    fn validate_file(
        &self,
        path: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        verify_file_identity(path, version.size_bytes as u64, &version.sha256)?;
        self.validator.verify(path, version)
    }

    fn ensure_installed_file(
        &self,
        path: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        self.validate_file(path, version)?;
        seal_read_only(path)
    }

    fn finish_content_addressed(
        &self,
        candidate: &Path,
        final_path: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        if final_path.is_file() && self.ensure_installed_file(final_path, version).is_ok() {
            let _ = fs::remove_file(candidate);
            return Ok(());
        }
        if final_path.exists() {
            fs::remove_file(final_path).map_err(io_error("remove invalid reference target"))?;
        }
        self.validate_file(candidate, version)?;
        seal_read_only(candidate)?;
        fs::rename(candidate, final_path).map_err(io_error("install verified reference target"))?;
        sync_directory(&self.root)?;
        Ok(())
    }
}

fn unavailable() -> ReferenceSelection {
    ReferenceSelection {
        database: None,
        unavailable_reason: Some("update_required".to_owned()),
    }
}
