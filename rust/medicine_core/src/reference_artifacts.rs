//! Atomic reconstruction and extraction of published reference artifacts.
//!
//! The artifact formats are deliberately decoded here instead of through a shell
//! utility: an update must either produce a fully verified database or leave the
//! currently installed database untouched.

use flate2::read::{MultiGzDecoder, ZlibDecoder};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fmt::{Display, Formatter};
use std::fs::{self, File, OpenOptions};
use std::io::{self, Read, Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const PATCH_MAGIC: &[u8] = b"MEDPATCH1";
const PATCH_HEADER_MAX: u32 = 16 * 1024 * 1024;
const IO_BUFFER_SIZE: usize = 64 * 1024;

pub trait ArtifactObserver {
    fn progress(&mut self, phase: &str, completed_bytes: u64, total_bytes: u64);
    fn checkpoint(&mut self, path: &Path);
}

#[derive(Debug, Default)]
pub struct NoopArtifactObserver;
impl ArtifactObserver for NoopArtifactObserver {
    fn progress(&mut self, _phase: &str, _completed_bytes: u64, _total_bytes: u64) {}
    fn checkpoint(&mut self, _path: &Path) {}
}

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArtifactResult {
    pub source_size_bytes: u64,
    pub source_sha256: String,
    pub target_size_bytes: u64,
    pub target_sha256: String,
}

#[derive(Debug)]
pub struct ArtifactError(String);
impl Display for ArtifactError {
    fn fmt(&self, f: &mut Formatter<'_>) -> std::fmt::Result {
        f.write_str(&self.0)
    }
}
impl std::error::Error for ArtifactError {}
impl From<std::io::Error> for ArtifactError {
    fn from(e: std::io::Error) -> Self {
        Self(format!("artifact I/O: {e}"))
    }
}

fn error(message: impl Into<String>) -> ArtifactError {
    ArtifactError(message.into())
}

fn sha256_file(
    path: &Path,
    phase: &str,
    observer: &mut dyn ArtifactObserver,
) -> Result<(u64, String), ArtifactError> {
    let mut file = File::open(path)?;
    let total = file.metadata()?.len();
    let mut digest = Sha256::new();
    let mut buffer = [0u8; IO_BUFFER_SIZE];
    let mut size = 0u64;
    loop {
        let count = file.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
        size = size
            .checked_add(count as u64)
            .ok_or_else(|| error("file size overflow"))?;
        observer.progress(phase, size, total);
    }
    Ok((size, format!("{:x}", digest.finalize())))
}

fn temp_path(destination: &Path) -> PathBuf {
    destination.with_file_name(format!(
        "{}.tmp",
        destination
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("artifact")
    ))
}

fn finish_atomic(temp: &Path, destination: &Path) -> Result<(), ArtifactError> {
    match fs::rename(temp, destination) {
        Ok(()) => Ok(()),
        Err(error) => {
            let _ = fs::remove_file(temp);
            Err(ArtifactError::from(error))
        }
    }
}

fn prepare_destination(destination: &Path) -> Result<PathBuf, ArtifactError> {
    if let Some(parent) = destination.parent() {
        fs::create_dir_all(parent)?;
    }
    let temporary = temp_path(destination);
    let _ = fs::remove_file(&temporary);
    Ok(temporary)
}

fn cleanup_temp(temp: &Path) {
    let _ = fs::remove_file(temp);
}

struct Header {
    chunk_size: u64,
    source_sha256: String,
    source_size_bytes: u64,
    target_sha256: String,
    target_size_bytes: u64,
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn json_string(value: &Value, key: &str) -> Result<String, ArtifactError> {
    value
        .get(key)
        .and_then(Value::as_str)
        .map(str::to_owned)
        .ok_or_else(|| error(format!("patch header missing {key}")))
}
fn json_u64(value: &Value, key: &str) -> Result<u64, ArtifactError> {
    value
        .get(key)
        .and_then(Value::as_u64)
        .ok_or_else(|| error(format!("patch header missing {key}")))
}

fn read_exact<R: Read>(reader: &mut R, buffer: &mut [u8]) -> Result<(), ArtifactError> {
    reader
        .read_exact(buffer)
        .map_err(|e| error(format!("truncated patch: {e}")))
}

fn parse_header<R: Read>(reader: &mut R) -> Result<Header, ArtifactError> {
    let mut magic = [0u8; 9];
    read_exact(reader, &mut magic)?;
    if magic != PATCH_MAGIC {
        return Err(error("invalid patch magic"));
    }
    let mut length_bytes = [0u8; 4];
    read_exact(reader, &mut length_bytes)?;
    let length = u32::from_be_bytes(length_bytes);
    if length == 0 || length > PATCH_HEADER_MAX {
        return Err(error("invalid patch header length"));
    }
    let mut header_bytes = vec![0u8; length as usize];
    read_exact(reader, &mut header_bytes)?;
    let value: Value = serde_json::from_slice(&header_bytes)
        .map_err(|e| error(format!("invalid patch header JSON: {e}")))?;
    if json_string(&value, "format")? != "medicine-chunk-v1" {
        return Err(error("unsupported patch format"));
    }
    let header = Header {
        chunk_size: json_u64(&value, "chunk_size")?,
        source_sha256: json_string(&value, "source_sha256")?,
        source_size_bytes: json_u64(&value, "source_size_bytes")?,
        target_sha256: json_string(&value, "target_sha256")?,
        target_size_bytes: json_u64(&value, "target_size_bytes")?,
    };
    if header.chunk_size == 0
        || header.source_sha256.len() != 64
        || header.target_sha256.len() != 64
    {
        return Err(error("invalid patch dimensions or SHA-256"));
    }
    Ok(header)
}

struct LimitedReader<'a, R> {
    inner: &'a mut R,
    remaining: u64,
}

impl<'a, R> LimitedReader<'a, R> {
    fn new(inner: &'a mut R, remaining: u64) -> Self {
        Self { inner, remaining }
    }
}

impl<R: Read> Read for LimitedReader<'_, R> {
    fn read(&mut self, buffer: &mut [u8]) -> io::Result<usize> {
        if self.remaining == 0 || buffer.is_empty() {
            return Ok(0);
        }
        let limit = usize::try_from(self.remaining)
            .unwrap_or(usize::MAX)
            .min(buffer.len());
        let count = self.inner.read(&mut buffer[..limit])?;
        self.remaining -= count as u64;
        Ok(count)
    }
}

fn copy_source_to_temp(
    source: &Path,
    temporary: &Path,
    target_size: u64,
    observer: &mut dyn ArtifactObserver,
) -> Result<File, ArtifactError> {
    let mut input = File::open(source)?;
    let total = input.metadata()?.len();
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .read(true)
        .write(true)
        .open(temporary)?;
    let mut buffer = [0u8; IO_BUFFER_SIZE];
    let mut copied = 0u64;
    loop {
        let count = input.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        output.write_all(&buffer[..count])?;
        copied += count as u64;
        observer.progress("patch-copy-source", copied, total);
    }
    output.set_len(target_size)?;
    output.seek(SeekFrom::Start(0))?;
    Ok(output)
}

fn decode_zlib_chunk<R: Read>(
    reader: &mut R,
    compressed_length: u64,
    output: &mut File,
    offset: u64,
    raw_length: u64,
) -> Result<(), ArtifactError> {
    output.seek(SeekFrom::Start(offset))?;
    let limited = LimitedReader::new(reader, compressed_length);
    let mut decoder = ZlibDecoder::new(limited);
    let mut buffer = [0u8; IO_BUFFER_SIZE];
    let mut written = 0u64;
    while written < raw_length {
        let limit = usize::try_from(raw_length - written)
            .unwrap_or(IO_BUFFER_SIZE)
            .min(buffer.len());
        let count = decoder
            .read(&mut buffer[..limit])
            .map_err(|e| error(format!("compressed patch chunk: {e}")))?;
        if count == 0 {
            return Err(error("compressed patch chunk length mismatch"));
        }
        output.write_all(&buffer[..count])?;
        written += count as u64;
    }
    let extra = decoder
        .read(&mut buffer)
        .map_err(|e| error(format!("compressed patch chunk: {e}")))?;
    if extra != 0 {
        return Err(error("compressed patch chunk length mismatch"));
    }
    // Python's zlib.decompress accepts trailing bytes in a record. Consume them
    // so the next record starts at the format-defined boundary.
    let mut limited = decoder.into_inner();
    io::copy(&mut limited, &mut io::sink())?;
    if limited.remaining != 0 {
        return Err(error("truncated compressed patch chunk"));
    }
    Ok(())
}

fn copy_gzip_to_temp(
    archive: &Path,
    temporary: &Path,
    expected_size_bytes: u64,
    observer: &mut dyn ArtifactObserver,
) -> Result<u64, ArtifactError> {
    let input = File::open(archive)?;
    let mut decoder = MultiGzDecoder::new(input);
    let mut output = OpenOptions::new()
        .create(true)
        .truncate(true)
        .write(true)
        .open(temporary)?;
    let mut buffer = [0u8; IO_BUFFER_SIZE];
    let mut written = 0u64;
    loop {
        let count = decoder
            .read(&mut buffer)
            .map_err(|e| error(format!("gzip extraction: {e}")))?;
        if count == 0 {
            break;
        }
        written = written
            .checked_add(count as u64)
            .ok_or_else(|| error("snapshot size overflow"))?;
        if written > expected_size_bytes {
            return Err(error("snapshot exceeds expected size"));
        }
        output.write_all(&buffer[..count])?;
        observer.progress("snapshot", written, expected_size_bytes);
    }
    output.flush()?;
    Ok(written)
}

pub fn apply_chunk_patch(
    source: &Path,
    patch: &Path,
    destination: &Path,
    observer: &mut dyn ArtifactObserver,
) -> Result<ArtifactResult, ArtifactError> {
    apply_chunk_patch_inner(source, patch, destination, None, observer)
}

pub fn apply_chunk_patch_verified(
    source: &Path,
    patch: &Path,
    destination: &Path,
    expected_target_size_bytes: u64,
    expected_target_sha256: &str,
    observer: &mut dyn ArtifactObserver,
) -> Result<ArtifactResult, ArtifactError> {
    if expected_target_size_bytes == 0 || !is_sha256(expected_target_sha256) {
        return Err(error("invalid signed target identity"));
    }
    apply_chunk_patch_inner(
        source,
        patch,
        destination,
        Some((expected_target_size_bytes, expected_target_sha256)),
        observer,
    )
}

fn apply_chunk_patch_inner(
    source: &Path,
    patch: &Path,
    destination: &Path,
    expected_target: Option<(u64, &str)>,
    observer: &mut dyn ArtifactObserver,
) -> Result<ArtifactResult, ArtifactError> {
    let mut patch_handle = File::open(patch)?;
    let header = parse_header(&mut patch_handle)?;
    if let Some((expected_size, expected_sha256)) = expected_target {
        if header.target_size_bytes != expected_size || header.target_sha256 != expected_sha256 {
            return Err(error("patch target does not match signed target identity"));
        }
    }
    let (source_size, source_hash) = sha256_file(source, "patch-verify-source", observer)?;
    if source_size != header.source_size_bytes || source_hash != header.source_sha256 {
        return Err(error("source size or SHA-256 does not match patch"));
    }
    let temporary = prepare_destination(destination)?;
    let result = (|| {
        let mut output =
            copy_source_to_temp(source, &temporary, header.target_size_bytes, observer)?;
        let mut previous_index = None;
        let mut record_header = [0u8; 16];
        let total = header.target_size_bytes;
        loop {
            let first = patch_handle.read(&mut record_header[..1])?;
            if first == 0 {
                break;
            }
            read_exact(&mut patch_handle, &mut record_header[1..])?;
            let index = u64::from_be_bytes(record_header[..8].try_into().unwrap());
            let raw_length = u32::from_be_bytes(record_header[8..12].try_into().unwrap()) as u64;
            let compressed_length =
                u32::from_be_bytes(record_header[12..16].try_into().unwrap()) as u64;
            if previous_index.is_some_and(|previous| index <= previous) {
                return Err(error("chunk indexes must be strictly increasing"));
            }
            previous_index = Some(index);
            let offset = index
                .checked_mul(header.chunk_size)
                .ok_or_else(|| error("chunk offset overflow"))?;
            if offset >= total {
                return Err(error("patch chunk is outside target bounds"));
            }
            let expected_length = header.chunk_size.min(total - offset);
            if raw_length == 0 || raw_length != expected_length {
                return Err(error("patch chunk length does not match target geometry"));
            }
            if compressed_length == 0 {
                return Err(error("invalid compressed patch chunk length"));
            }
            decode_zlib_chunk(
                &mut patch_handle,
                compressed_length,
                &mut output,
                offset,
                raw_length,
            )?;
            observer.progress("patch", offset + raw_length, total);
        }
        output.flush()?;
        output.sync_all()?;
        drop(output);
        let (target_size, target_hash) = sha256_file(&temporary, "patch-verify-target", observer)?;
        if target_size != header.target_size_bytes || target_hash != header.target_sha256 {
            return Err(error("target size or SHA-256 does not match patch"));
        }
        observer.checkpoint(&temporary);
        finish_atomic(&temporary, destination)?;
        observer.progress("patch", total, total);
        Ok(ArtifactResult {
            source_size_bytes: source_size,
            source_sha256: source_hash,
            target_size_bytes: target_size,
            target_sha256: target_hash,
        })
    })();
    if result.is_err() {
        cleanup_temp(&temporary);
    }
    result
}

pub fn decompress_snapshot(
    archive: &Path,
    destination: &Path,
    expected_size_bytes: u64,
    expected_sha256: &str,
    observer: &mut dyn ArtifactObserver,
) -> Result<ArtifactResult, ArtifactError> {
    let (source_size, source_hash) = sha256_file(archive, "snapshot-verify-archive", observer)?;
    let temporary = prepare_destination(destination)?;
    let result = (|| {
        let output_size = copy_gzip_to_temp(archive, &temporary, expected_size_bytes, observer)?;
        if output_size != expected_size_bytes {
            return Err(error("target size or SHA-256 does not match snapshot"));
        }
        let (actual_size, actual_hash) =
            sha256_file(&temporary, "snapshot-verify-target", observer)?;
        if actual_size != expected_size_bytes || actual_hash != expected_sha256 {
            return Err(error("target size or SHA-256 does not match snapshot"));
        }
        observer.checkpoint(&temporary);
        finish_atomic(&temporary, destination)?;
        observer.progress("snapshot", actual_size, expected_size_bytes);
        Ok(ArtifactResult {
            source_size_bytes: source_size,
            source_sha256: source_hash,
            target_size_bytes: actual_size,
            target_sha256: actual_hash,
        })
    })();
    if result.is_err() {
        cleanup_temp(&temporary);
    }
    result
}
