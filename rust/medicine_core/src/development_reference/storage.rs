use super::DevelopmentReferenceError;
use crate::reference_signature::ReferenceReleaseArtifact;
use sha2::{Digest, Sha256};
use std::ffi::CString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::path::Path;

const IO_BUFFER_SIZE: usize = 1024 * 1024;

pub(super) struct ReferenceDirectoryLock {
    file: File,
}

impl ReferenceDirectoryLock {
    pub(super) fn acquire(path: &Path) -> Result<Self, DevelopmentReferenceError> {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)
            .map_err(io_error("open reference operation lock"))?;
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if result != 0 {
            return Err(DevelopmentReferenceError::new(
                "cannot lock reference operation coordinator",
            ));
        }
        Ok(Self { file })
    }
}

impl Drop for ReferenceDirectoryLock {
    fn drop(&mut self) {
        let _ = unsafe { libc::flock(self.file.as_raw_fd(), libc::LOCK_UN) };
    }
}

pub(super) fn normalize_checkpoint(
    target: &Path,
    artifact: &ReferenceReleaseArtifact,
) -> Result<(), DevelopmentReferenceError> {
    if !target.is_file() {
        return Ok(());
    }
    let size = target
        .metadata()
        .map_err(io_error("read reference checkpoint"))?
        .len();
    if size < artifact.size_bytes {
        return Ok(());
    }
    if size == artifact.size_bytes
        && verify_file_identity(target, artifact.size_bytes, &artifact.sha256).is_ok()
    {
        return Ok(());
    }
    fs::remove_file(target).map_err(io_error("discard invalid reference checkpoint"))
}

pub(super) fn verify_file_identity(
    path: &Path,
    expected_size: u64,
    expected_sha256: &str,
) -> Result<(), DevelopmentReferenceError> {
    let mut file = File::open(path).map_err(io_error("open reference file"))?;
    let size = file
        .metadata()
        .map_err(io_error("stat reference file"))?
        .len();
    if size != expected_size {
        return Err(DevelopmentReferenceError::new(
            "reference file size does not match signed identity",
        ));
    }
    let mut digest = Sha256::new();
    let mut buffer = vec![0u8; IO_BUFFER_SIZE];
    loop {
        let count = file
            .read(&mut buffer)
            .map_err(io_error("hash reference file"))?;
        if count == 0 {
            break;
        }
        digest.update(&buffer[..count]);
    }
    if format!("{:x}", digest.finalize()) != expected_sha256 {
        return Err(DevelopmentReferenceError::new(
            "reference file SHA-256 does not match signed identity",
        ));
    }
    Ok(())
}

pub(super) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), DevelopmentReferenceError> {
    let parent = path
        .parent()
        .ok_or_else(|| DevelopmentReferenceError::new("reference state path has no parent"))?;
    fs::create_dir_all(parent).map_err(io_error("create reference state directory"))?;
    let temp = path.with_extension("tmp");
    let result = (|| {
        let mut file = OpenOptions::new()
            .create(true)
            .truncate(true)
            .write(true)
            .open(&temp)
            .map_err(io_error("open reference state temp file"))?;
        file.write_all(bytes)
            .map_err(io_error("write reference state temp file"))?;
        file.sync_all()
            .map_err(io_error("sync reference state temp file"))?;
        drop(file);
        fs::rename(&temp, path).map_err(io_error("commit reference state"))?;
        File::open(parent)
            .and_then(|directory| directory.sync_all())
            .map_err(io_error("sync reference state directory"))?;
        Ok(())
    })();
    if result.is_err() {
        let _ = fs::remove_file(temp);
    }
    result
}

pub(super) fn available_bytes(path: &Path) -> Result<u64, DevelopmentReferenceError> {
    let c_path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| DevelopmentReferenceError::new("reference path contains NUL"))?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(c_path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(DevelopmentReferenceError::new(
            "cannot inspect reference filesystem capacity",
        ));
    }
    let stats = unsafe { stats.assume_init() };
    Ok(stats.f_bavail.saturating_mul(stats.f_frsize))
}

pub(super) fn io_error(
    context: &'static str,
) -> impl FnOnce(std::io::Error) -> DevelopmentReferenceError {
    move |error| DevelopmentReferenceError::new(format!("{context}: {error}"))
}
