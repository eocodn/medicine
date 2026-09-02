use crate::reference_http::ReferenceHttpSource;
use crate::reference_lifecycle_runtime::{ReferenceLifecycleRuntime, ReferenceRuntimeResult};
use crate::reference_manager::{ReferenceRuntimeError, ReferenceUpdateStatus};
use crate::reference_signature::ReferenceManifestVerifier;
use crate::reference_trust::load_reference_trust_manifest;
use crate::ReferenceBootstrapSnapshot;
use std::path::PathBuf;

pub use crate::reference_manager::{
    ReferenceBootstrapInfo as DevelopmentReferenceBootstrapInfo,
    ReferenceBootstrapInspection as DevelopmentReferenceBootstrapInspection,
    ReferenceDatabaseValidator, ReferenceManager as DevelopmentReferenceManager,
    ReferenceReleaseSource, ReferenceRuntimeError as DevelopmentReferenceError,
    ReferenceSelection as DevelopmentReferenceSelection,
    ReferenceUpdateStatus as DevelopmentReferenceUpdateStatus, RustReferenceDatabaseValidator,
};

#[derive(Debug, Clone)]
pub struct DevelopmentReferenceConfig {
    pub reference_dir: PathBuf,
    pub base_url: String,
    pub trust_manifest: PathBuf,
    pub contract_major: i32,
}

pub struct DevelopmentReferenceRuntime {
    inner: ReferenceLifecycleRuntime<ReferenceHttpSource, RustReferenceDatabaseValidator>,
}

impl DevelopmentReferenceRuntime {
    pub fn prepare(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        self.inner.prepare()
    }

    pub fn start(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        self.inner.start()
    }

    pub fn status(&self) -> ReferenceBootstrapSnapshot {
        self.inner.status()
    }

    pub fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        self.inner.check_for_update()
    }
}

pub fn development_reference_runtime(
    config: DevelopmentReferenceConfig,
) -> Result<DevelopmentReferenceRuntime, DevelopmentReferenceError> {
    if config.contract_major <= 0 {
        return Err(DevelopmentReferenceError::from_message(
            "reference contract major must be positive",
        ));
    }
    let trust = load_reference_trust_manifest(&config.trust_manifest)
        .map_err(|error| DevelopmentReferenceError::from_message(error.to_string()))?;
    let verifier = ReferenceManifestVerifier::new(trust.keys);
    let source =
        ReferenceHttpSource::new(&config.base_url, verifier, config.contract_major as u64)?;
    Ok(DevelopmentReferenceRuntime {
        inner: ReferenceLifecycleRuntime::new(
            config.reference_dir,
            config.contract_major,
            source,
            RustReferenceDatabaseValidator,
        ),
    })
}
