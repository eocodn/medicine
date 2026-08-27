use super::{hex_digest, is_sha256, validate_sequence, ReferenceSignatureError};
use p256::ecdsa::{signature::Verifier, Signature, VerifyingKey};

const ENVELOPE_VERSION: i64 = 1;
const ALGORITHM: &str = "ECDSA_P256_SHA256";
const SIGNATURE_MAGIC: &[u8] = b"MEDREFSIG1";

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

    pub fn active_spki_pem(
        key_id: &str,
        public_key_pem: &str,
        expected_spki_sha256: &str,
    ) -> Result<Self, ReferenceSignatureError> {
        validate_key_id(key_id)?;
        if !is_sha256(expected_spki_sha256) {
            return Err(ReferenceSignatureError::new(
                "invalid release signing public key fingerprint",
            ));
        }
        const BEGIN: &str = "-----BEGIN PUBLIC KEY-----";
        const END: &str = "-----END PUBLIC KEY-----";
        let trimmed = public_key_pem.trim();
        let body = trimmed
            .strip_prefix(BEGIN)
            .and_then(|value| value.strip_suffix(END))
            .ok_or_else(|| ReferenceSignatureError::new("invalid release signing public key"))?
            .lines()
            .map(str::trim)
            .filter(|line| !line.is_empty())
            .collect::<String>();
        let spki = decode_base64(&body, "release signing public key")?;
        parse_p256_spki(&spki)?;
        if hex_digest(&spki) != expected_spki_sha256 {
            return Err(ReferenceSignatureError::new(
                "release signing public key fingerprint does not match",
            ));
        }
        Ok(Self::active(key_id, spki))
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
