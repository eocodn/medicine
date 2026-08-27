use super::{hex_digest, is_sha256, validate_sequence, ReferenceSignatureError};
use serde_json::Value;
use std::collections::HashSet;

const PROTOCOL_VERSION: u64 = 2;
const PATCH_FORMAT: &str = "medicine-chunk-v1";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ReferenceArtifactKind {
    FullGzip,
    ChunkPatch,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceReleaseArtifact {
    pub contract_major: u64,
    pub key: String,
    pub sha256: String,
    pub size_bytes: u64,
    pub kind: ReferenceArtifactKind,
    pub from_sha256: Option<String>,
    pub from_size_bytes: Option<u64>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedReferenceRelease {
    pub release_sequence: i64,
    pub root_hash: String,
    pub dataset_id: String,
    pub contract_major: u64,
    pub target_sha256: String,
    pub target_size_bytes: u64,
    pub full: ReferenceReleaseArtifact,
    pub patches: Vec<ReferenceReleaseArtifact>,
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ReferenceRootSelection {
    Release(VerifiedReferenceRelease),
    Retired {
        release_sequence: i64,
        root_hash: String,
        current_contract_major: u64,
        minimum_supported_contract_major: u64,
    },
}

pub struct ReferenceReleaseProtocolV2;

impl ReferenceReleaseProtocolV2 {
    pub fn select_verified_root(
        release_sequence: i64,
        payload: &[u8],
        contract_major: u64,
    ) -> Result<ReferenceRootSelection, ReferenceSignatureError> {
        validate_sequence(release_sequence, "reference root sequence")?;
        if contract_major == 0 {
            return Err(ReferenceSignatureError::new(
                "invalid reference contract major",
            ));
        }
        let root: Value = serde_json::from_slice(payload)
            .map_err(|_| ReferenceSignatureError::new("invalid reference release root JSON"))?;
        let protocol = root
            .get("protocol_version")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                ReferenceSignatureError::new("unsupported reference release protocol")
            })?;
        if protocol != PROTOCOL_VERSION {
            return Err(ReferenceSignatureError::new(
                "unsupported reference release protocol",
            ));
        }
        let current = positive_u64(&root, "current_contract_major")?;
        let minimum = positive_u64(&root, "minimum_supported_contract_major")?;
        if minimum > current || current - minimum > 1 {
            return Err(ReferenceSignatureError::new(
                "invalid reference contract support window",
            ));
        }
        if contract_major < minimum {
            return Ok(ReferenceRootSelection::Retired {
                release_sequence,
                root_hash: hex_digest(payload),
                current_contract_major: current,
                minimum_supported_contract_major: minimum,
            });
        }
        Self::parse_verified_root(release_sequence, payload, contract_major)
            .map(ReferenceRootSelection::Release)
    }

    pub fn parse_verified_root(
        release_sequence: i64,
        payload: &[u8],
        contract_major: u64,
    ) -> Result<VerifiedReferenceRelease, ReferenceSignatureError> {
        validate_sequence(release_sequence, "reference root sequence")?;
        if contract_major == 0 {
            return Err(ReferenceSignatureError::new(
                "invalid reference contract major",
            ));
        }
        let root: Value = serde_json::from_slice(payload)
            .map_err(|_| ReferenceSignatureError::new("invalid reference release root JSON"))?;
        let protocol = root
            .get("protocol_version")
            .and_then(Value::as_u64)
            .ok_or_else(|| {
                ReferenceSignatureError::new("unsupported reference release protocol")
            })?;
        if protocol != PROTOCOL_VERSION {
            return Err(ReferenceSignatureError::new(
                "unsupported reference release protocol",
            ));
        }
        let current = positive_u64(&root, "current_contract_major")?;
        let minimum = positive_u64(&root, "minimum_supported_contract_major")?;
        if minimum > current || current - minimum > 1 {
            return Err(ReferenceSignatureError::new(
                "invalid reference contract support window",
            ));
        }
        // Retirement is an authenticated terminal result for this client.
        // Do not require it to understand or validate the newer contract map.
        if contract_major < minimum {
            return Err(ReferenceSignatureError::new(format!(
                "reference contract {contract_major} is retired"
            )));
        }
        if contract_major > current {
            return Err(ReferenceSignatureError::new(
                "signed reference root does not yet support this app contract",
            ));
        }
        let contracts = root
            .get("contracts")
            .and_then(Value::as_object)
            .ok_or_else(|| ReferenceSignatureError::new("invalid reference contracts"))?;
        let expected: HashSet<String> =
            (minimum..=current).map(|major| major.to_string()).collect();
        if contracts.keys().cloned().collect::<HashSet<_>>() != expected {
            return Err(ReferenceSignatureError::new(
                "signed reference root contracts do not match support window",
            ));
        }
        let entry = contracts
            .get(&contract_major.to_string())
            .ok_or_else(|| ReferenceSignatureError::new("missing reference contract entry"))?;
        let dataset_id = string_field(entry, "dataset_id")?;
        if !is_dataset_id(&dataset_id) {
            return Err(ReferenceSignatureError::new(
                "invalid reference dataset identity",
            ));
        }
        let target = entry
            .get("target")
            .ok_or_else(|| ReferenceSignatureError::new("invalid reference target"))?;
        let target_sha256 = string_field(target, "sha256")?;
        let target_size_bytes = positive_u64(target, "size_bytes")?;
        if !is_sha256(&target_sha256) {
            return Err(ReferenceSignatureError::new(
                "invalid reference target SHA-256",
            ));
        }
        let full_value = entry
            .get("full")
            .ok_or_else(|| ReferenceSignatureError::new("invalid reference full artifact"))?;
        if string_field(full_value, "compression")? != "gzip" {
            return Err(ReferenceSignatureError::new(
                "unsupported reference full compression",
            ));
        }
        let full_sha256 = string_field(full_value, "sha256")?;
        let full_size_bytes = positive_u64(full_value, "size_bytes")?;
        let full_key = string_field(full_value, "key")?;
        if !is_sha256(&full_sha256) {
            return Err(ReferenceSignatureError::new(
                "invalid reference full artifact SHA-256",
            ));
        }
        validate_full_key(contract_major, &full_key, &target_sha256)?;
        let full = ReferenceReleaseArtifact {
            contract_major,
            key: full_key,
            sha256: full_sha256,
            size_bytes: full_size_bytes,
            kind: ReferenceArtifactKind::FullGzip,
            from_sha256: None,
            from_size_bytes: None,
        };

        let patches_value = entry
            .get("patches")
            .and_then(Value::as_array)
            .ok_or_else(|| ReferenceSignatureError::new("invalid reference patches"))?;
        let mut patches = Vec::new();
        let mut sources = HashSet::new();
        for patch in patches_value {
            // Unknown codecs are intentionally ignored. Full-gzip remains the
            // mandatory fallback for clients that understand this contract.
            if patch.get("format").and_then(Value::as_str) != Some(PATCH_FORMAT) {
                continue;
            }
            let from_sha256 = string_field(patch, "from_sha256")?;
            let from_size_bytes = positive_u64(patch, "from_size_bytes")?;
            let sha256 = string_field(patch, "sha256")?;
            let size_bytes = positive_u64(patch, "size_bytes")?;
            let key = string_field(patch, "key")?;
            if !is_sha256(&from_sha256) || !is_sha256(&sha256) {
                return Err(ReferenceSignatureError::new(
                    "invalid reference patch SHA-256",
                ));
            }
            if size_bytes >= full.size_bytes {
                return Err(ReferenceSignatureError::new(
                    "reference patch must be smaller than the signed full snapshot",
                ));
            }
            if !sources.insert(from_sha256.clone()) {
                return Err(ReferenceSignatureError::new(
                    "reference release contains duplicate patch sources",
                ));
            }
            validate_patch_key(contract_major, &key, &from_sha256, &target_sha256)?;
            patches.push(ReferenceReleaseArtifact {
                contract_major,
                key,
                sha256,
                size_bytes,
                kind: ReferenceArtifactKind::ChunkPatch,
                from_sha256: Some(from_sha256),
                from_size_bytes: Some(from_size_bytes),
            });
        }

        let root_hash = hex_digest(payload);
        Ok(VerifiedReferenceRelease {
            release_sequence,
            root_hash,
            dataset_id,
            contract_major,
            target_sha256,
            target_size_bytes,
            full,
            patches,
        })
    }
}

fn positive_u64(value: &Value, field: &str) -> Result<u64, ReferenceSignatureError> {
    let number = value
        .get(field)
        .and_then(Value::as_u64)
        .filter(|number| *number > 0)
        .ok_or_else(|| ReferenceSignatureError::new(format!("invalid reference {field}")))?;
    Ok(number)
}

fn string_field(value: &Value, field: &str) -> Result<String, ReferenceSignatureError> {
    value
        .get(field)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| ReferenceSignatureError::new(format!("invalid reference {field}")))
}

fn is_dataset_id(value: &str) -> bool {
    value.len() == 71 && value.starts_with("sha256:") && is_sha256(&value[7..])
}

fn validate_full_key(
    contract_major: u64,
    key: &str,
    sha256: &str,
) -> Result<(), ReferenceSignatureError> {
    let expected = format!("reference/v2/contracts/{contract_major}/full/{sha256}.sqlite.gz");
    if key != expected {
        return Err(ReferenceSignatureError::new(
            "invalid reference full artifact key",
        ));
    }
    Ok(())
}

fn validate_patch_key(
    contract_major: u64,
    key: &str,
    from_sha256: &str,
    target_sha256: &str,
) -> Result<(), ReferenceSignatureError> {
    let expected = format!(
        "reference/v2/contracts/{contract_major}/patch/{from_sha256}-{target_sha256}.mpatch"
    );
    if key != expected {
        return Err(ReferenceSignatureError::new(
            "invalid reference patch artifact key",
        ));
    }
    Ok(())
}
