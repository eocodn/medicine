use crate::reference_http::ReferenceHttpSource;
use crate::reference_lifecycle_runtime::{ReferenceLifecycleRuntime, ReferenceRuntimeResult};
use crate::reference_manager::{
    ReferenceRuntimeError, ReferenceUpdateStatus, RustReferenceDatabaseValidator,
};
use crate::reference_signature::ReferenceManifestVerifier;
use crate::reference_trust::load_reference_trust_manifest;
use crate::{ReferenceBootstrapSnapshot, REFERENCE_CONTRACT_MAJOR};
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct ReferenceChannelConfig {
    pub reference_dir: PathBuf,
    pub base_url: String,
    pub trust_manifest: PathBuf,
}

pub struct ReferenceChannelRuntime {
    inner: ReferenceLifecycleRuntime<ReferenceHttpSource, RustReferenceDatabaseValidator>,
}

impl ReferenceChannelRuntime {
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

pub fn reference_channel_runtime(
    config: ReferenceChannelConfig,
) -> Result<ReferenceChannelRuntime, ReferenceRuntimeError> {
    let trust = load_reference_trust_manifest(&config.trust_manifest)
        .map_err(|error| ReferenceRuntimeError::from_message(error.to_string()))?;
    let verifier = ReferenceManifestVerifier::new(trust.keys);
    let source =
        ReferenceHttpSource::new(&config.base_url, verifier, REFERENCE_CONTRACT_MAJOR as u64)?;
    Ok(ReferenceChannelRuntime {
        inner: ReferenceLifecycleRuntime::new(
            config.reference_dir,
            REFERENCE_CONTRACT_MAJOR,
            source,
            RustReferenceDatabaseValidator,
        ),
    })
}
