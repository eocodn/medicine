use serde::{de::Error as _, Deserialize, Deserializer, Serialize};
use std::fmt;

const MAGIC_V1: &str = "MEDREFSTATE1";
const MAGIC_V3: &str = "MEDREFSTATE3";
const LEGACY_SCHEMA_VERSION: &str = "10";

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ReferenceVersion {
    #[serde(rename = "datasetId")]
    pub dataset_id: String,
    pub sha256: String,
    #[serde(rename = "sizeBytes")]
    pub size_bytes: i64,
    #[serde(rename = "contractMajor")]
    pub contract_major: i32,
    #[serde(rename = "releaseSequence")]
    pub release_sequence: i64,
}

#[derive(Deserialize)]
struct ReferenceVersionFields {
    #[serde(rename = "datasetId")]
    dataset_id: String,
    sha256: String,
    #[serde(rename = "sizeBytes")]
    size_bytes: i64,
    #[serde(rename = "contractMajor")]
    contract_major: i32,
    #[serde(rename = "releaseSequence")]
    release_sequence: i64,
}

impl<'de> Deserialize<'de> for ReferenceVersion {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let fields = ReferenceVersionFields::deserialize(deserializer)?;
        let value = Self {
            dataset_id: fields.dataset_id,
            sha256: fields.sha256,
            size_bytes: fields.size_bytes,
            contract_major: fields.contract_major,
            release_sequence: fields.release_sequence,
        };
        validate_version(&value).map_err(D::Error::custom)?;
        Ok(value)
    }
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub struct ReferenceFileSeal {
    #[serde(rename = "sizeBytes")]
    pub size_bytes: i64,
    #[serde(rename = "modifiedMarker")]
    pub modified_marker: i64,
    #[serde(rename = "changedMarker")]
    pub changed_marker: i64,
    #[serde(rename = "identityKey")]
    pub identity_key: String,
    pub writable: bool,
}

#[derive(Clone, Debug, Default, PartialEq, Eq, Serialize)]
pub struct ReferenceStoreState {
    pub active: Option<ReferenceVersion>,
    pub previous: Option<ReferenceVersion>,
    pub pending: Option<ReferenceVersion>,
    #[serde(rename = "highestActivatedSequence")]
    pub highest_activated_sequence: i64,
    #[serde(rename = "highestSeenRootSequence")]
    pub highest_seen_root_sequence: i64,
    #[serde(rename = "highestSeenRootHash")]
    pub highest_seen_root_hash: Option<String>,
    #[serde(rename = "highestRetiredContractMajor")]
    pub highest_retired_contract_major: i32,
    #[serde(rename = "activeSeal")]
    pub active_seal: Option<ReferenceFileSeal>,
    #[serde(rename = "previousSeal")]
    pub previous_seal: Option<ReferenceFileSeal>,
    #[serde(rename = "pendingSeal")]
    pub pending_seal: Option<ReferenceFileSeal>,
}

#[derive(Deserialize)]
struct ReferenceStoreStateFields {
    active: Option<ReferenceVersion>,
    previous: Option<ReferenceVersion>,
    pending: Option<ReferenceVersion>,
    #[serde(rename = "highestActivatedSequence")]
    highest_activated_sequence: i64,
    #[serde(rename = "highestSeenRootSequence")]
    highest_seen_root_sequence: i64,
    #[serde(rename = "highestSeenRootHash")]
    highest_seen_root_hash: Option<String>,
    #[serde(rename = "highestRetiredContractMajor")]
    highest_retired_contract_major: i32,
    #[serde(rename = "activeSeal")]
    active_seal: Option<ReferenceFileSeal>,
    #[serde(rename = "previousSeal")]
    previous_seal: Option<ReferenceFileSeal>,
    #[serde(rename = "pendingSeal")]
    pending_seal: Option<ReferenceFileSeal>,
}

impl<'de> Deserialize<'de> for ReferenceStoreState {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let fields = ReferenceStoreStateFields::deserialize(deserializer)?;
        let value = Self {
            active: fields.active,
            previous: fields.previous,
            pending: fields.pending,
            highest_activated_sequence: fields.highest_activated_sequence,
            highest_seen_root_sequence: fields.highest_seen_root_sequence,
            highest_seen_root_hash: fields.highest_seen_root_hash,
            highest_retired_contract_major: fields.highest_retired_contract_major,
            active_seal: fields.active_seal,
            previous_seal: fields.previous_seal,
            pending_seal: fields.pending_seal,
        };
        validate_state(&value).map_err(D::Error::custom)?;
        Ok(value)
    }
}

#[derive(Clone, Debug, PartialEq, Eq)]
pub struct ReferenceStateError(String);

impl ReferenceStateError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for ReferenceStateError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.0.fmt(formatter)
    }
}

impl std::error::Error for ReferenceStateError {}

mod codec;
pub use codec::ReferenceStateCodec;

#[derive(Clone, Debug, Default)]
pub struct ReferenceStore {
    state: ReferenceStoreState,
}

impl ReferenceStore {
    pub fn snapshot(&self) -> ReferenceStoreState {
        self.state.clone()
    }

    pub fn install_initial(
        &mut self,
        version: ReferenceVersion,
    ) -> Result<ReferenceVersion, ReferenceStateError> {
        validate_version(&version)?;
        if version.release_sequence <= 0 {
            return Err(ReferenceStateError::new(
                "downloaded reference release sequence must be positive",
            ));
        }
        if version.release_sequence < self.state.highest_activated_sequence {
            return Err(ReferenceStateError::new(
                "reference rollback is not allowed",
            ));
        }
        if version.release_sequence == self.state.highest_activated_sequence
            && self.state.active.as_ref() != Some(&version)
        {
            return Err(ReferenceStateError::new(
                "reference release sequence identity conflict",
            ));
        }
        // Bootstrap is the recovery path after startup could not trust the
        // stored active reference. Android replaces that slot and clears all
        // staged roles, but never lowers signed-root or retirement watermarks.
        let next = ReferenceStoreState {
            active: Some(version.clone()),
            previous: None,
            pending: None,
            highest_activated_sequence: self
                .state
                .highest_activated_sequence
                .max(version.release_sequence),
            highest_seen_root_sequence: self.state.highest_seen_root_sequence,
            highest_seen_root_hash: self.state.highest_seen_root_hash.clone(),
            highest_retired_contract_major: self.state.highest_retired_contract_major,
            active_seal: None,
            previous_seal: None,
            pending_seal: None,
        };
        validate_state(&next)?;
        self.state = next;
        Ok(version)
    }

    pub fn observe_signed_root(
        &mut self,
        release_sequence: i64,
        root_hash: &str,
    ) -> Result<(), ReferenceStateError> {
        let next = with_observed_signed_root(&self.state, release_sequence, root_hash)?;
        self.state = next;
        Ok(())
    }

    pub fn mark_contract_retired(
        &mut self,
        contract_major: i32,
        release_sequence: i64,
        root_hash: &str,
    ) -> Result<(), ReferenceStateError> {
        if contract_major <= 0 {
            return Err(ReferenceStateError::new(
                "retired reference contract major must be positive",
            ));
        }
        // Build the complete next state before committing either part. This
        // makes root observation and retirement one atomic in-memory change.
        let mut next = with_observed_signed_root(&self.state, release_sequence, root_hash)?;
        next.highest_retired_contract_major =
            next.highest_retired_contract_major.max(contract_major);
        validate_state(&next)?;
        self.state = next;
        Ok(())
    }

    pub fn is_contract_retired(&self, contract_major: i32) -> bool {
        contract_major > 0 && self.state.highest_retired_contract_major >= contract_major
    }

    pub fn stage_pending(
        &mut self,
        version: ReferenceVersion,
        seal: Option<ReferenceFileSeal>,
    ) -> Result<(), ReferenceStateError> {
        validate_version(&version)?;
        if version.release_sequence <= 0 {
            return Err(ReferenceStateError::new(
                "downloaded reference release sequence must be positive",
            ));
        }
        if let Some(pending) = &self.state.pending {
            if pending == &version && self.state.pending_seal == seal {
                return Ok(());
            }
        }
        if version.release_sequence < self.state.highest_activated_sequence {
            return Err(ReferenceStateError::new(
                "reference rollback is not allowed",
            ));
        }
        if version.release_sequence == self.state.highest_activated_sequence
            && self
                .state
                .active
                .as_ref()
                .is_some_and(|active| active.release_sequence >= version.release_sequence)
        {
            return Err(ReferenceStateError::new(
                "reference release is already activated",
            ));
        }
        if let Some(pending) = &self.state.pending {
            if version.release_sequence < pending.release_sequence {
                return Err(ReferenceStateError::new(
                    "older reference release cannot replace a newer pending release",
                ));
            }
        }
        let mut next = self.state.clone();
        next.pending = Some(version);
        next.pending_seal = seal;
        validate_state(&next)?;
        self.state = next;
        Ok(())
    }

    pub fn open_for_startup(
        &mut self,
        expected_contract_major: i32,
        active_valid: bool,
        previous_valid: bool,
        pending_valid: bool,
    ) -> Result<Option<ReferenceVersion>, ReferenceStateError> {
        if expected_contract_major <= 0 {
            return Err(ReferenceStateError::new(
                "invalid expected reference contract major",
            ));
        }
        let mut activated_pending = false;
        if let Some(pending) = self.state.pending.clone() {
            let stale = pending.release_sequence < self.state.highest_seen_root_sequence;
            let incompatible = pending.contract_major != expected_contract_major
                || self.is_contract_retired(pending.contract_major);
            let eligible =
                pending.release_sequence > self.state.highest_activated_sequence
                    || (pending.release_sequence == self.state.highest_activated_sequence
                        && self.state.active.as_ref().is_some_and(|active| {
                            active.release_sequence < pending.release_sequence
                        }));
            if stale || incompatible || !pending_valid || !eligible {
                self.state.pending = None;
                self.state.pending_seal = None;
            } else {
                // Preserve only the verified current LKG. If active is
                // corrupt, Android verifies and retains previous instead of
                // moving the corrupt active into the previous slot.
                let old_active = if active_valid {
                    self.state.active.clone()
                } else if previous_valid {
                    self.state.previous.clone()
                } else {
                    None
                };
                let old_seal = if active_valid {
                    self.state.active_seal.clone()
                } else if previous_valid {
                    self.state.previous_seal.clone()
                } else {
                    None
                };
                self.state.active = Some(pending.clone());
                self.state.active_seal = self.state.pending_seal.clone();
                self.state.previous = old_active;
                self.state.previous_seal = old_seal;
                self.state.pending = None;
                self.state.pending_seal = None;
                self.state.highest_activated_sequence = self
                    .state
                    .highest_activated_sequence
                    .max(pending.release_sequence);
                activated_pending = true;
            }
        }

        let selected = if activated_pending || active_valid {
            self.state.active.clone()
        } else if previous_valid {
            let previous = self.state.previous.clone();
            if previous.is_some() {
                self.state.active = previous.clone();
                self.state.active_seal = self.state.previous_seal.clone();
                self.state.previous = None;
                self.state.previous_seal = None;
            }
            previous
        } else {
            None
        };
        validate_state(&self.state)?;
        Ok(selected)
    }
}

fn validate_state(state: &ReferenceStoreState) -> Result<(), ReferenceStateError> {
    if state.highest_activated_sequence < 0
        || state.highest_seen_root_sequence < 0
        || state.highest_retired_contract_major < 0
    {
        return Err(ReferenceStateError::new(
            "negative reference state high-water mark",
        ));
    }
    match (
        state.highest_seen_root_sequence,
        &state.highest_seen_root_hash,
    ) {
        (0, None) => {}
        (sequence, Some(hash)) if sequence > 0 && is_sha256(hash) => {}
        _ => {
            return Err(ReferenceStateError::new(
                "invalid signed root high-water mark",
            ))
        }
    }
    for version in [&state.active, &state.previous, &state.pending]
        .into_iter()
        .flatten()
    {
        validate_version(version)?;
    }
    if state
        .active
        .as_ref()
        .is_some_and(|v| v.release_sequence > state.highest_activated_sequence)
        || state
            .previous
            .as_ref()
            .is_some_and(|v| v.release_sequence > state.highest_activated_sequence)
    {
        return Err(ReferenceStateError::new(
            "reference sequence exceeds activation high-water mark",
        ));
    }
    if state.active.is_none() && state.active_seal.is_some()
        || state.previous.is_none() && state.previous_seal.is_some()
        || state.pending.is_none() && state.pending_seal.is_some()
    {
        return Err(ReferenceStateError::new(
            "reference seal has no matching version",
        ));
    }
    Ok(())
}

fn validate_version(version: &ReferenceVersion) -> Result<(), ReferenceStateError> {
    if version
        .dataset_id
        .strip_prefix("sha256:")
        .is_none_or(|suffix| !is_sha256(suffix))
        || !is_sha256(&version.sha256)
        || version.size_bytes <= 0
        || version.contract_major <= 0
        || version.release_sequence < 0
    {
        return Err(ReferenceStateError::new("invalid reference version"));
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn with_observed_signed_root(
    state: &ReferenceStoreState,
    release_sequence: i64,
    root_hash: &str,
) -> Result<ReferenceStoreState, ReferenceStateError> {
    if release_sequence <= 0 || !is_sha256(root_hash) {
        return Err(ReferenceStateError::new("invalid signed root"));
    }
    if release_sequence < state.highest_seen_root_sequence {
        return Err(ReferenceStateError::new(
            "signed reference root rollback is not allowed",
        ));
    }
    if release_sequence == state.highest_seen_root_sequence {
        if state.highest_seen_root_hash.as_deref() != Some(root_hash) {
            return Err(ReferenceStateError::new(
                "signed reference root changed without advancing sequence",
            ));
        }
        return Ok(state.clone());
    }
    let mut next = state.clone();
    next.highest_seen_root_sequence = release_sequence;
    next.highest_seen_root_hash = Some(root_hash.to_owned());
    Ok(next)
}
