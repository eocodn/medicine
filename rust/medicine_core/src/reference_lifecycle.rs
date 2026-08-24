use crate::reference_signature::{ReferenceReleaseArtifact, VerifiedReferenceRelease};
use crate::reference_state::{ReferenceStoreState, ReferenceVersion};
use std::fmt::{Display, Formatter};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceLifecycleError(String);

impl ReferenceLifecycleError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl Display for ReferenceLifecycleError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReferenceLifecycleError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceBootstrapPlan {
    pub target: ReferenceVersion,
    pub full: ReferenceReleaseArtifact,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReferenceUpdatePlan {
    UpToDate,
    RollbackRejected,
    IdentityConflict,
    Stage(Box<ReferenceUpdateStagePlan>),
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceUpdateStagePlan {
    pub target: ReferenceVersion,
    pub primary: ReferenceReleaseArtifact,
    pub fallback_full: Option<ReferenceReleaseArtifact>,
}

pub fn plan_reference_bootstrap(
    expected_contract_major: i32,
    state: &ReferenceStoreState,
    release: &VerifiedReferenceRelease,
) -> Result<ReferenceBootstrapPlan, ReferenceLifecycleError> {
    if expected_contract_major <= 0 {
        return Err(ReferenceLifecycleError::new(
            "invalid expected reference contract major",
        ));
    }
    if release.contract_major != expected_contract_major as u64 {
        return Err(ReferenceLifecycleError::new(
            "reference release contract is incompatible with this runtime",
        ));
    }
    if release.release_sequence < state.highest_activated_sequence {
        return Err(ReferenceLifecycleError::new(
            "reference rollback is not allowed",
        ));
    }
    Ok(ReferenceBootstrapPlan {
        target: target_version(release)?,
        full: release.full.clone(),
    })
}

pub fn plan_reference_update(
    current: &ReferenceVersion,
    state: &ReferenceStoreState,
    release: &VerifiedReferenceRelease,
) -> Result<ReferenceUpdatePlan, ReferenceLifecycleError> {
    if release.contract_major != current.contract_major as u64 {
        return Err(ReferenceLifecycleError::new(
            "reference release contract does not match installed runtime",
        ));
    }
    if release.release_sequence < state.highest_activated_sequence {
        return Ok(ReferenceUpdatePlan::RollbackRejected);
    }
    if current.sha256 == release.target_sha256
        && current.size_bytes > 0
        && current.size_bytes as u64 == release.target_size_bytes
        && current.dataset_id == release.dataset_id
    {
        return Ok(ReferenceUpdatePlan::UpToDate);
    }
    if release.release_sequence == state.highest_activated_sequence
        && current.release_sequence == state.highest_activated_sequence
    {
        return Ok(ReferenceUpdatePlan::IdentityConflict);
    }

    let matching_patch = release.patches.iter().find(|artifact| {
        artifact.from_sha256.as_deref() == Some(current.sha256.as_str())
            && current.size_bytes > 0
            && artifact.from_size_bytes == Some(current.size_bytes as u64)
    });
    let (primary, fallback_full) = match matching_patch {
        Some(patch) => (patch.clone(), Some(release.full.clone())),
        None => (release.full.clone(), None),
    };
    Ok(ReferenceUpdatePlan::Stage(Box::new(
        ReferenceUpdateStagePlan {
            target: target_version(release)?,
            primary,
            fallback_full,
        },
    )))
}

fn target_version(
    release: &VerifiedReferenceRelease,
) -> Result<ReferenceVersion, ReferenceLifecycleError> {
    let size_bytes = i64::try_from(release.target_size_bytes)
        .map_err(|_| ReferenceLifecycleError::new("reference target size is too large"))?;
    let contract_major = i32::try_from(release.contract_major)
        .map_err(|_| ReferenceLifecycleError::new("reference contract major is too large"))?;
    Ok(ReferenceVersion {
        dataset_id: release.dataset_id.clone(),
        sha256: release.target_sha256.clone(),
        size_bytes,
        contract_major,
        release_sequence: release.release_sequence,
    })
}
