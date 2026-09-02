use crate::reference_manager::ReferenceRuntimeError;
use crate::reference_runtime::ReferenceRuntime;
use crate::reference_trust::load_reference_trust_manifest;
use std::path::PathBuf;

#[derive(Debug, Clone)]
pub struct ReferenceChannelConfig {
    pub reference_dir: PathBuf,
    pub base_url: String,
    pub trust_manifest: PathBuf,
}

pub fn open_reference_channel(
    config: ReferenceChannelConfig,
) -> Result<ReferenceRuntime, ReferenceRuntimeError> {
    let trust = load_reference_trust_manifest(&config.trust_manifest)
        .map_err(|error| ReferenceRuntimeError::from_message(error.to_string()))?;
    ReferenceRuntime::new(config.reference_dir, &config.base_url, trust)
}
