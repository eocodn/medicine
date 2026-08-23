use super::storage::{io_error, normalize_checkpoint, verify_file_identity};
use super::{DevelopmentReferenceError, ReferenceReleaseSource};
use crate::reference_signature::{
    ReferenceManifestVerifier, ReferenceReleaseArtifact, ReferenceReleaseProtocolV2,
    ReferenceRootSelection,
};
use reqwest::blocking::{Client, Response};
use reqwest::header::{CONTENT_LENGTH, CONTENT_RANGE, RANGE};
use reqwest::{StatusCode, Url};
use serde::Deserialize;
use std::fs::{self, OpenOptions};
use std::io::{Read, Write};
use std::path::Path;

const ROOT_KEY: &str = "reference/v2/latest.json";
const MAX_ROOT_BYTES: u64 = 1024 * 1024;
const IO_BUFFER_SIZE: usize = 1024 * 1024;
const PROGRESS_BUCKETS: u64 = 20;

#[derive(Debug, Clone)]
pub(super) struct HttpsReferenceReleaseSource {
    base_url: Url,
    client: Client,
    verifier: ReferenceManifestVerifier,
    contract_major: u64,
}

#[derive(Deserialize)]
struct SignedEnvelope {
    envelope_version: i64,
    algorithm: String,
    key_id: String,
    release_sequence: i64,
    payload_base64: String,
    signature_base64: String,
}

impl HttpsReferenceReleaseSource {
    pub(super) fn new(
        base_url: &str,
        verifier: ReferenceManifestVerifier,
        contract_major: u64,
    ) -> Result<Self, DevelopmentReferenceError> {
        let base_url = Url::parse(base_url)
            .map_err(|_| DevelopmentReferenceError::new("reference update base URL is invalid"))?;
        if base_url.scheme() != "https"
            || base_url.host_str().is_none()
            || base_url.query().is_some()
            || base_url.fragment().is_some()
            || !base_url.path().ends_with('/')
        {
            return Err(DevelopmentReferenceError::new(
                "reference update base URL must be an HTTPS base path ending in /",
            ));
        }
        if contract_major == 0 {
            return Err(DevelopmentReferenceError::new(
                "reference contract major must be positive",
            ));
        }
        let client = Client::builder()
            .user_agent("medicine-core-web/0.1")
            .build()
            .map_err(|error| DevelopmentReferenceError::new(format!("HTTP client: {error}")))?;
        Ok(Self {
            base_url,
            client,
            verifier,
            contract_major,
        })
    }

    fn object_url(&self, key: &str) -> Result<Url, DevelopmentReferenceError> {
        let url = self
            .base_url
            .join(key)
            .map_err(|_| DevelopmentReferenceError::new("invalid reference object key"))?;
        if url.scheme() != self.base_url.scheme()
            || url.host_str() != self.base_url.host_str()
            || url.port_or_known_default() != self.base_url.port_or_known_default()
        {
            return Err(DevelopmentReferenceError::new(
                "reference object escaped configured origin",
            ));
        }
        Ok(url)
    }

    fn small_body(&self, key: &str) -> Result<Vec<u8>, DevelopmentReferenceError> {
        let response = self
            .client
            .get(self.object_url(key)?)
            .send()
            .map_err(|error| DevelopmentReferenceError::new(format!("reference HTTP: {error}")))?;
        if !response.status().is_success() {
            return Err(DevelopmentReferenceError::new(format!(
                "reference HTTP status {}",
                response.status()
            )));
        }
        if response
            .content_length()
            .is_some_and(|length| length > MAX_ROOT_BYTES)
        {
            return Err(DevelopmentReferenceError::new(
                "signed reference root is too large",
            ));
        }
        let mut body = Vec::new();
        response
            .take(MAX_ROOT_BYTES + 1)
            .read_to_end(&mut body)
            .map_err(|error| DevelopmentReferenceError::new(format!("reference HTTP: {error}")))?;
        if body.len() as u64 > MAX_ROOT_BYTES {
            return Err(DevelopmentReferenceError::new(
                "signed reference root is too large",
            ));
        }
        Ok(body)
    }
}

impl ReferenceReleaseSource for HttpsReferenceReleaseSource {
    fn fetch_latest(&self) -> Result<ReferenceRootSelection, DevelopmentReferenceError> {
        let body = self.small_body(ROOT_KEY)?;
        let envelope: SignedEnvelope = serde_json::from_slice(&body)
            .map_err(|_| DevelopmentReferenceError::new("signed reference root is invalid JSON"))?;
        let verified = self
            .verifier
            .verify(
                envelope.envelope_version,
                &envelope.algorithm,
                &envelope.key_id,
                envelope.release_sequence,
                &envelope.payload_base64,
                &envelope.signature_base64,
                None,
            )
            .map_err(|error| DevelopmentReferenceError::new(error.to_string()))?;
        ReferenceReleaseProtocolV2::select_verified_root(
            verified.release_sequence,
            &verified.payload,
            self.contract_major,
        )
        .map_err(|error| DevelopmentReferenceError::new(error.to_string()))
    }

    fn download(
        &self,
        artifact: &ReferenceReleaseArtifact,
        target: &Path,
    ) -> Result<(), DevelopmentReferenceError> {
        if let Some(parent) = target.parent() {
            fs::create_dir_all(parent).map_err(io_error("create reference download directory"))?;
        }
        normalize_checkpoint(target, artifact)?;
        let mut completed = target.metadata().map(|value| value.len()).unwrap_or(0);
        if completed == artifact.size_bytes {
            return Ok(());
        }
        let mut request = self.client.get(self.object_url(&artifact.key)?);
        if completed > 0 {
            request = request.header(RANGE, format!("bytes={completed}-"));
        }
        let response = request
            .send()
            .map_err(|error| DevelopmentReferenceError::new(format!("reference HTTP: {error}")))?;
        let status = response.status();
        let append = completed > 0 && status == StatusCode::PARTIAL_CONTENT;
        if completed > 0 && status == StatusCode::OK {
            completed = 0;
        } else if append {
            validate_content_range(&response, completed, artifact.size_bytes)?;
        } else if completed == 0 && status != StatusCode::OK {
            return Err(DevelopmentReferenceError::new(format!(
                "reference artifact HTTP status {status}"
            )));
        } else if completed > 0 {
            return Err(DevelopmentReferenceError::new(format!(
                "reference artifact resume HTTP status {status}"
            )));
        }
        validate_content_length(&response, artifact.size_bytes - completed)?;
        write_response(response, target, append, completed, artifact.size_bytes)?;
        verify_file_identity(target, artifact.size_bytes, &artifact.sha256)?;
        Ok(())
    }
}

fn validate_content_range(
    response: &Response,
    completed: u64,
    total: u64,
) -> Result<(), DevelopmentReferenceError> {
    let value = response
        .headers()
        .get(CONTENT_RANGE)
        .and_then(|value| value.to_str().ok())
        .ok_or_else(|| DevelopmentReferenceError::new("missing reference Content-Range"))?;
    let expected = format!("bytes {completed}-{}-{total}", total - 1);
    let normalized = value.replace('/', "-");
    if normalized != expected {
        return Err(DevelopmentReferenceError::new(
            "reference artifact Content-Range mismatch",
        ));
    }
    Ok(())
}

fn validate_content_length(
    response: &Response,
    expected: u64,
) -> Result<(), DevelopmentReferenceError> {
    if let Some(length) = response
        .headers()
        .get(CONTENT_LENGTH)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.parse::<u64>().ok())
    {
        if length != expected {
            return Err(DevelopmentReferenceError::new(
                "reference artifact Content-Length mismatch",
            ));
        }
    }
    Ok(())
}

fn write_response(
    mut response: Response,
    target: &Path,
    append: bool,
    mut completed: u64,
    total: u64,
) -> Result<(), DevelopmentReferenceError> {
    let mut options = OpenOptions::new();
    options.create(true).write(true);
    if append {
        options.append(true);
    } else {
        options.truncate(true);
    }
    let mut output = options
        .open(target)
        .map_err(io_error("open reference checkpoint"))?;
    let mut buffer = vec![0u8; IO_BUFFER_SIZE];
    let mut last_progress_bucket = None;
    if should_emit_progress(last_progress_bucket, completed, total) {
        emit_progress(completed, total);
        last_progress_bucket = Some(progress_bucket(completed, total));
    }
    loop {
        let count = response
            .read(&mut buffer)
            .map_err(|error| DevelopmentReferenceError::new(format!("reference HTTP: {error}")))?;
        if count == 0 {
            break;
        }
        completed = completed
            .checked_add(count as u64)
            .ok_or_else(|| DevelopmentReferenceError::new("reference download size overflow"))?;
        if completed > total {
            return Err(DevelopmentReferenceError::new(
                "reference artifact exceeds signed size",
            ));
        }
        output
            .write_all(&buffer[..count])
            .map_err(io_error("write reference checkpoint"))?;
        if should_emit_progress(last_progress_bucket, completed, total) {
            emit_progress(completed, total);
            last_progress_bucket = Some(progress_bucket(completed, total));
        }
    }
    output
        .sync_all()
        .map_err(io_error("sync reference checkpoint"))?;
    Ok(())
}

fn progress_bucket(completed: u64, total: u64) -> u64 {
    if total == 0 {
        return 0;
    }
    completed
        .min(total)
        .saturating_mul(PROGRESS_BUCKETS)
        .checked_div(total)
        .unwrap_or(0)
}

fn should_emit_progress(last_bucket: Option<u64>, completed: u64, total: u64) -> bool {
    let bucket = progress_bucket(completed, total);
    last_bucket.is_none_or(|previous| bucket > previous) || (total > 0 && completed >= total)
}

fn emit_progress(completed: u64, total: u64) {
    eprintln!(
        "{}",
        serde_json::json!({
            "event": "reference_progress",
            "phase": "download",
            "completed_bytes": completed,
            "total_bytes": total,
        })
    );
}

#[cfg(test)]
mod tests {
    use super::{progress_bucket, should_emit_progress};

    #[test]
    fn progress_is_bucketed_and_always_emits_completion() {
        assert_eq!(progress_bucket(0, 100), 0);
        assert_eq!(progress_bucket(4, 100), 0);
        assert_eq!(progress_bucket(5, 100), 1);
        assert!(should_emit_progress(None, 0, 100));
        assert!(!should_emit_progress(Some(0), 4, 100));
        assert!(should_emit_progress(Some(0), 5, 100));
        assert!(should_emit_progress(Some(19), 100, 100));
    }
}
