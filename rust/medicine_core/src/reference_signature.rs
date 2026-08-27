//! Verification and parsing for the signed reference-release protocol.
//!
//! The authenticated envelope and the release-root protocol are separate wire
//! boundaries. Keep their implementations modular while preserving this module
//! as the stable public facade consumed by JNI, the development adapter, and
//! contract tests.

mod envelope;
mod protocol;

use sha2::{Digest, Sha256};
use std::fmt;

pub use envelope::{
    ReferenceManifestVerifier, TrustedSigningKey, VerifiedReferenceManifestSignature,
};
pub use protocol::{
    ReferenceArtifactKind, ReferenceReleaseArtifact, ReferenceReleaseProtocolV2,
    ReferenceRootSelection, VerifiedReferenceRelease,
};

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceSignatureError(String);

impl ReferenceSignatureError {
    pub(super) fn new(message: impl Into<String>) -> Self {
        Self(message.into())
    }
}

impl fmt::Display for ReferenceSignatureError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.0)
    }
}

impl std::error::Error for ReferenceSignatureError {}

const MAX_SEQUENCE: i64 = i64::MAX;

fn validate_sequence(value: i64, label: &str) -> Result<(), ReferenceSignatureError> {
    if !(1..=MAX_SEQUENCE).contains(&value) {
        return Err(ReferenceSignatureError::new(format!("invalid {label}")));
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn hex_digest(bytes: &[u8]) -> String {
    Sha256::digest(bytes)
        .iter()
        .map(|byte| format!("{byte:02x}"))
        .collect()
}
