//! Verification and parsing for the signed reference-release protocol.
//!
//! This module deliberately keeps the wire format independent from JSON
//! serialization details.  The signature is over the exact MEDREFSIG1 frame;
//! the JSON root is parsed only after the envelope has been authenticated.

use p256::ecdsa::{signature::Verifier, Signature, VerifyingKey};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::collections::HashSet;
use std::fmt;

const ENVELOPE_VERSION: i64 = 1;
const ALGORITHM: &str = "ECDSA_P256_SHA256";
const PROTOCOL_VERSION: u64 = 2;
const PATCH_FORMAT: &str = "medicine-chunk-v1";
const SIGNATURE_MAGIC: &[u8] = b"MEDREFSIG1";
const MAX_SEQUENCE: i64 = i64::MAX;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceSignatureError(String);

impl ReferenceSignatureError {
    fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for ReferenceSignatureError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReferenceSignatureError {}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct TrustedSigningKey {
    pub key_id: String,
    pub public_key_spki: Vec<u8>,
    pub revoked: bool,
}

impl TrustedSigningKey {
    pub fn active(key_id: &str, public_key_spki: Vec<u8>) -> Self {
        Self {
            key_id: key_id.to_owned(),
            public_key_spki,
            revoked: false,
        }
    }

    pub fn revoked(key_id: &str, public_key_spki: Vec<u8>) -> Self {
        Self {
            key_id: key_id.to_owned(),
            public_key_spki,
            revoked: true,
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct VerifiedReferenceManifestSignature {
    pub key_id: String,
    pub release_sequence: i64,
    pub payload: Vec<u8>,
}

#[derive(Debug, Clone)]
pub struct ReferenceManifestVerifier {
    trusted_keys: Vec<TrustedSigningKey>,
}

impl ReferenceManifestVerifier {
    pub fn new(trusted_keys: Vec<TrustedSigningKey>) -> Self {
        Self { trusted_keys }
    }

    // Keep the signed-envelope fields explicit here: this is the shared wire
    // boundary used by JNI and tests, and grouping them would permit callers
    // to construct partially validated intermediate state.
    #[allow(clippy::too_many_arguments)]
    pub fn verify(
        &self,
        envelope_version: i64,
        algorithm: &str,
        key_id: &str,
        release_sequence: i64,
        payload_base64: &str,
        signature_base64: &str,
        minimum_exclusive_sequence: Option<i64>,
    ) -> Result<VerifiedReferenceManifestSignature, ReferenceSignatureError> {
        if envelope_version != ENVELOPE_VERSION {
            return Err(ReferenceSignatureError::new(
                "unsupported release envelope version",
            ));
        }
        if algorithm != ALGORITHM {
            return Err(ReferenceSignatureError::new(
                "unsupported release signature algorithm",
            ));
        }
        validate_key_id(key_id)?;
        validate_sequence(release_sequence, "release sequence")?;
        if let Some(minimum) = minimum_exclusive_sequence {
            validate_sequence(minimum, "accepted release sequence")?;
            if release_sequence <= minimum {
                return Err(ReferenceSignatureError::new(
                    "release sequence is not newer",
                ));
            }
        }

        let matching: Vec<&TrustedSigningKey> = self
            .trusted_keys
            .iter()
            .filter(|key| key.key_id == key_id)
            .collect();
        let key = match matching.as_slice() {
            [] => {
                return Err(ReferenceSignatureError::new(
                    "untrusted release signing key",
                ))
            }
            [key] if !key.revoked => *key,
            [_] => {
                return Err(ReferenceSignatureError::new(
                    "release signing key is revoked",
                ))
            }
            _ => {
                return Err(ReferenceSignatureError::new(
                    "duplicate release signing keys",
                ))
            }
        };

        let payload = decode_base64(payload_base64, "release payload")?;
        let signature_bytes = decode_base64(signature_base64, "release signature")?;
        if payload.is_empty() {
            return Err(ReferenceSignatureError::new("release payload is empty"));
        }
        if signature_bytes.is_empty() {
            return Err(ReferenceSignatureError::new("release signature is empty"));
        }

        let verifying_key = parse_p256_spki(&key.public_key_spki)?;
        let signature = Signature::from_der(&signature_bytes).map_err(|_| {
            ReferenceSignatureError::new("invalid release manifest signature encoding")
        })?;
        let message = signing_message(key_id, release_sequence, &payload)?;
        verifying_key
            .verify(&message, &signature)
            .map_err(|_| ReferenceSignatureError::new("release manifest signature is invalid"))?;

        Ok(VerifiedReferenceManifestSignature {
            key_id: key_id.to_owned(),
            release_sequence,
            payload,
        })
    }
}

fn validate_key_id(key_id: &str) -> Result<(), ReferenceSignatureError> {
    if key_id.is_empty() || key_id.len() > 64 || !key_id.is_ascii() {
        return Err(ReferenceSignatureError::new(
            "invalid release signing key id",
        ));
    }
    if !key_id
        .bytes()
        .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'_' | b'-'))
    {
        return Err(ReferenceSignatureError::new(
            "invalid release signing key id",
        ));
    }
    Ok(())
}

fn validate_sequence(value: i64, label: &str) -> Result<(), ReferenceSignatureError> {
    if !(1..=MAX_SEQUENCE).contains(&value) {
        return Err(ReferenceSignatureError::new(format!("invalid {label}")));
    }
    Ok(())
}

fn signing_message(
    key_id: &str,
    release_sequence: i64,
    payload: &[u8],
) -> Result<Vec<u8>, ReferenceSignatureError> {
    validate_key_id(key_id)?;
    validate_sequence(release_sequence, "release sequence")?;
    let key_bytes = key_id.as_bytes();
    let key_len = u32::try_from(key_bytes.len())
        .map_err(|_| ReferenceSignatureError::new("release signing key id is too long"))?;
    let payload_len = u64::try_from(payload.len())
        .map_err(|_| ReferenceSignatureError::new("release payload is too large"))?;
    let mut output = Vec::with_capacity(10 + 4 + 8 + 8 + key_bytes.len() + payload.len());
    output.extend_from_slice(SIGNATURE_MAGIC);
    output.extend_from_slice(&key_len.to_be_bytes());
    output.extend_from_slice(&(release_sequence as u64).to_be_bytes());
    output.extend_from_slice(&payload_len.to_be_bytes());
    output.extend_from_slice(key_bytes);
    output.extend_from_slice(payload);
    Ok(output)
}

fn decode_base64(value: &str, label: &str) -> Result<Vec<u8>, ReferenceSignatureError> {
    let bytes = value.as_bytes();
    if bytes.is_empty() || bytes.len() % 4 != 0 {
        return Err(ReferenceSignatureError::new(format!(
            "invalid base64 {label}"
        )));
    }
    let mut output = Vec::with_capacity(bytes.len() / 4 * 3);
    for (index, chunk) in bytes.chunks_exact(4).enumerate() {
        let last = index + 1 == bytes.len() / 4;
        let a = base64_value(chunk[0]);
        let b = base64_value(chunk[1]);
        if a < 0 || b < 0 {
            return Err(ReferenceSignatureError::new(format!(
                "invalid base64 {label}"
            )));
        }
        let c = if chunk[2] == b'=' {
            -1
        } else {
            base64_value(chunk[2])
        };
        let d = if chunk[3] == b'=' {
            -1
        } else {
            base64_value(chunk[3])
        };
        if (!last && (chunk[2] == b'=' || chunk[3] == b'='))
            || (c < 0 && chunk[2] != b'=')
            || (d < 0 && chunk[3] != b'=')
            || (chunk[2] == b'=' && chunk[3] != b'=')
            || (c >= 0 && d < 0 && chunk[3] != b'=')
            || (c < 0 && d >= 0)
        {
            return Err(ReferenceSignatureError::new(format!(
                "invalid base64 {label}"
            )));
        }
        output.push(((a << 2) | (b >> 4)) as u8);
        if c >= 0 {
            output.push((((b & 0x0f) << 4) | (c >> 2)) as u8);
            if d >= 0 {
                output.push((((c & 0x03) << 6) | d) as u8);
            } else if c & 0x03 != 0 {
                return Err(ReferenceSignatureError::new(format!(
                    "non-canonical base64 {label}"
                )));
            }
        } else if b & 0x0f != 0 {
            return Err(ReferenceSignatureError::new(format!(
                "non-canonical base64 {label}"
            )));
        }
    }
    Ok(output)
}

fn base64_value(value: u8) -> i32 {
    match value {
        b'A'..=b'Z' => i32::from(value - b'A'),
        b'a'..=b'z' => i32::from(value - b'a' + 26),
        b'0'..=b'9' => i32::from(value - b'0' + 52),
        b'+' => 62,
        b'/' => 63,
        _ => -1,
    }
}

fn parse_p256_spki(spki: &[u8]) -> Result<VerifyingKey, ReferenceSignatureError> {
    // The Android and Python implementations both trust an X.509 Subject
    // Public Key Info containing id-ecPublicKey/prime256v1.  Keep the parser
    // strict so a key for another curve cannot enter the trust set silently.
    const PREFIX: &[u8] = &[
        0x30, 0x59, 0x30, 0x13, 0x06, 0x07, 0x2a, 0x86, 0x48, 0xce, 0x3d, 0x02, 0x01, 0x06, 0x08,
        0x2a, 0x86, 0x48, 0xce, 0x3d, 0x03, 0x01, 0x07, 0x03, 0x42, 0x00,
    ];
    if spki.len() != PREFIX.len() + 65 || !spki.starts_with(PREFIX) || spki[PREFIX.len()] != 0x04 {
        return Err(ReferenceSignatureError::new(
            "invalid release signing public key",
        ));
    }
    VerifyingKey::from_sec1_bytes(&spki[PREFIX.len()..])
        .map_err(|_| ReferenceSignatureError::new("invalid release signing public key"))
}

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

pub struct ReferenceReleaseProtocolV2;

impl ReferenceReleaseProtocolV2 {
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

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
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

fn hex_digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
