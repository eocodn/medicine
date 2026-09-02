use super::{
    ReferenceDatabaseValidator, ReferenceManager, ReferenceReleaseSource, ReferenceRuntimeError,
};
use crate::reference_state::ReferenceStore;
use std::fs;
use std::path::Path;

use super::storage::io_error;

impl<S: ReferenceReleaseSource, V: ReferenceDatabaseValidator> ReferenceManager<S, V> {
    pub(super) fn cleanup_unreferenced(
        &self,
        store: &ReferenceStore,
    ) -> Result<(), ReferenceRuntimeError> {
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

    pub(super) fn cleanup_bootstrap_files(
        &self,
        keep: Option<&Path>,
    ) -> Result<(), ReferenceRuntimeError> {
        self.cleanup_temporary_files(".bootstrap-artifact-", ".bootstrap-candidate-", keep)
    }

    pub(super) fn cleanup_update_files(
        &self,
        keep: Option<&Path>,
    ) -> Result<(), ReferenceRuntimeError> {
        self.cleanup_temporary_files(".artifact-", ".candidate-", keep)
    }

    fn cleanup_temporary_files(
        &self,
        artifact_prefix: &str,
        candidate_prefix: &str,
        keep: Option<&Path>,
    ) -> Result<(), ReferenceRuntimeError> {
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
