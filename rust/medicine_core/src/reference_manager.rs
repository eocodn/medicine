use crate::reference_capabilities::{
    verify_reference_runtime_capabilities, verify_reference_runtime_materialization,
};
use crate::reference_db_verifier::verify_reference_database;
use crate::reference_signature::{
    ReferenceReleaseArtifact, ReferenceRootSelection, VerifiedReferenceRelease,
};
use crate::reference_state::ReferenceVersion;
use std::fmt::{Display, Formatter};
use std::path::{Path, PathBuf};

mod cleanup;
mod operations;
pub(crate) mod storage;
#[cfg(test)]
mod tests;

#[derive(Debug)]
pub struct ReferenceRuntimeError(String);

impl ReferenceRuntimeError {
    pub(crate) fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }

    pub fn from_message(message: impl Into<String>) -> Self {
        Self::new(message)
    }
}

impl Display for ReferenceRuntimeError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReferenceRuntimeError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceSelection {
    pub database: Option<PathBuf>,
    pub unavailable_reason: Option<String>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub(crate) enum ReferenceBootstrapPreparation {
    Ready(ReferenceSelection),
    Download {
        release: VerifiedReferenceRelease,
        checkpoint_bytes: u64,
    },
    Unavailable,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReferenceUpdateStatus {
    NoChange,
    Staged,
    UpdateRequired,
}

impl ReferenceUpdateStatus {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::NoChange => "no_change",
            Self::Staged => "staged",
            Self::UpdateRequired => "update_required",
        }
    }
}

pub(crate) trait ReferenceReleaseSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, ReferenceRuntimeError>;
    fn download(
        &self,
        artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), ReferenceRuntimeError>;
}

pub(crate) trait ReferenceDatabaseValidator {
    fn verify(&self, file: &Path, version: &ReferenceVersion) -> Result<(), ReferenceRuntimeError>;

    fn verify_runtime_capabilities(
        &self,
        file: &Path,
        version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        self.verify(file, version)
    }
}

pub(crate) trait ReferenceBootstrapObserver {
    fn installing(&mut self) -> Result<(), ReferenceRuntimeError>;
}

impl<F> ReferenceBootstrapObserver for F
where
    F: FnMut() -> Result<(), ReferenceRuntimeError>,
{
    fn installing(&mut self) -> Result<(), ReferenceRuntimeError> {
        self()
    }
}

#[derive(Debug, Default, Clone, Copy)]
pub(crate) struct RustReferenceDatabaseValidator;

impl ReferenceDatabaseValidator for RustReferenceDatabaseValidator {
    fn verify(&self, file: &Path, version: &ReferenceVersion) -> Result<(), ReferenceRuntimeError> {
        verify_reference_database(file, version.contract_major as u64, &version.dataset_id)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        verify_reference_runtime_materialization(file)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        verify_reference_runtime_capabilities(file)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))?;
        Ok(())
    }

    fn verify_runtime_capabilities(
        &self,
        file: &Path,
        _version: &ReferenceVersion,
    ) -> Result<(), ReferenceRuntimeError> {
        verify_reference_runtime_capabilities(file)
            .map_err(|error| ReferenceRuntimeError::new(error.to_string()))
    }
}

pub(crate) struct ReferenceManager<S, V> {
    pub(super) root: PathBuf,
    pub(super) contract_major: i32,
    pub(super) source: S,
    pub(super) validator: V,
}
