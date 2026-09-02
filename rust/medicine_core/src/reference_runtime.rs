use crate::reference_http::ReferenceHttpSource;
use crate::reference_lifecycle_runtime::{ReferenceLifecycleRuntime, ReferenceRuntimeResult};
use crate::reference_manager::{
    ReferenceRuntimeError, ReferenceUpdateStatus, RustReferenceDatabaseValidator,
};
use crate::{
    ReferenceBootstrapSnapshot, ReferenceManifestVerifier, ReferenceTrustManifest,
    REFERENCE_CONTRACT_MAJOR,
};
use std::path::PathBuf;

pub struct ReferenceRuntime {
    inner: ReferenceLifecycleRuntime<ReferenceHttpSource, RustReferenceDatabaseValidator>,
}

impl ReferenceRuntime {
    pub(crate) fn new(
        reference_dir: PathBuf,
        base_url: &str,
        trust: ReferenceTrustManifest,
    ) -> Result<Self, ReferenceRuntimeError> {
        let verifier = ReferenceManifestVerifier::new(trust.keys);
        let source = ReferenceHttpSource::new(base_url, verifier, REFERENCE_CONTRACT_MAJOR as u64)?;
        Ok(Self {
            inner: ReferenceLifecycleRuntime::new(
                reference_dir,
                REFERENCE_CONTRACT_MAJOR,
                source,
                RustReferenceDatabaseValidator,
            ),
        })
    }

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
