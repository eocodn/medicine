use crate::{ReferenceSignatureError, TrustedSigningKey};
use serde::Deserialize;
use std::fmt::{Display, Formatter};
use std::path::Path;

const MAX_TRUST_MANIFEST_BYTES: u64 = 64 * 1024;

#[derive(Debug, Clone)]
pub struct ReferenceTrustManifest {
    pub active_key_id: String,
    pub keys: Vec<TrustedSigningKey>,
}

#[derive(Debug)]
pub struct ReferenceTrustError(String);

impl Display for ReferenceTrustError {
    fn fmt(&self, formatter: &mut Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReferenceTrustError {}

impl From<ReferenceSignatureError> for ReferenceTrustError {
    fn from(error: ReferenceSignatureError) -> Self {
        Self(error.to_string())
    }
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawManifest {
    active_key_id: String,
    keys: Vec<RawKey>,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct RawKey {
    key_id: String,
    public_key_pem: String,
    spki_sha256: String,
}

pub fn load_reference_trust_manifest(
    path: &Path,
) -> Result<ReferenceTrustManifest, ReferenceTrustError> {
    let metadata = std::fs::metadata(path).map_err(|_| {
        ReferenceTrustError("trusted release signing key file is unavailable".into())
    })?;
    if !metadata.is_file() {
        return Err(ReferenceTrustError(
            "trusted release signing key file is not a regular file".into(),
        ));
    }
    if metadata.len() > MAX_TRUST_MANIFEST_BYTES {
        return Err(ReferenceTrustError(
            "trusted release signing key file is too large".into(),
        ));
    }
    let bytes = std::fs::read(path)
        .map_err(|_| ReferenceTrustError("trusted release signing key file is invalid".into()))?;
    let raw: RawManifest = serde_json::from_slice(&bytes).map_err(|_| {
        ReferenceTrustError("trusted release signing key file shape is invalid".into())
    })?;
    if raw.keys.is_empty() {
        return Err(ReferenceTrustError(
            "trusted release signing keys must be a non-empty list".into(),
        ));
    }

    let mut seen = std::collections::HashSet::new();
    let mut keys = Vec::with_capacity(raw.keys.len());
    for raw_key in raw.keys {
        if !seen.insert(raw_key.key_id.clone()) {
            return Err(ReferenceTrustError(format!(
                "trusted release signing key ID is duplicated: {}",
                raw_key.key_id
            )));
        }
        let key = TrustedSigningKey::active_spki_pem(
            &raw_key.key_id,
            &raw_key.public_key_pem,
            &raw_key.spki_sha256,
        )?;
        keys.push(key);
    }
    if !seen.contains(&raw.active_key_id) {
        return Err(ReferenceTrustError(
            "active signer key ID is missing from trusted release signing keys".into(),
        ));
    }
    Ok(ReferenceTrustManifest {
        active_key_id: raw.active_key_id,
        keys,
    })
}
