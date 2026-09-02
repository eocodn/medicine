use super::ReferenceRuntimeError;
use crate::reference_signature::ReferenceReleaseArtifact;
use crate::reference_state::ReferenceFileSeal;
use sha2::{Digest, Sha256};
use std::ffi::CString;
use std::fs::{self, File, OpenOptions};
use std::io::{Read, Write};
use std::os::fd::AsRawFd;
use std::os::unix::ffi::OsStrExt;
use std::os::unix::fs::{MetadataExt, PermissionsExt};
use std::path::{Path, PathBuf};

const IO_BUFFER_SIZE: usize = 1024 * 1024;

pub(crate) struct ReferenceDirectoryLock {
    file: File,
}

impl ReferenceDirectoryLock {
    pub(crate) fn acquire(path: &Path) -> Result<Self, ReferenceRuntimeError> {
        let file = OpenOptions::new()
            .create(true)
            .truncate(false)
            .read(true)
            .write(true)
            .open(path)
            .map_err(io_error("open reference operation lock"))?;
        let result = unsafe { libc::flock(file.as_raw_fd(), libc::LOCK_EX) };
        if result != 0 {
            return Err(ReferenceRuntimeError::new(
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

pub(crate) fn normalize_checkpoint(
    target: &Path,
    artifact: &ReferenceReleaseArtifact,
) -> Result<(), ReferenceRuntimeError> {
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

pub(crate) fn seal_read_only(path: &Path) -> Result<(), ReferenceRuntimeError> {
    let mut permissions = fs::metadata(path)
        .map_err(io_error("read reference file permissions"))?
        .permissions();
    if permissions.mode() & 0o222 != 0 {
        permissions.set_mode(permissions.mode() & !0o222);
        fs::set_permissions(path, permissions)
            .map_err(io_error("make reference file read-only"))?;
    }
    let mode = fs::metadata(path)
        .map_err(io_error("verify reference file permissions"))?
        .permissions()
        .mode();
    if mode & 0o222 != 0 {
        return Err(ReferenceRuntimeError::new(
            "reference file remains writable after sealing",
        ));
    }
    File::open(path)
        .and_then(|file| file.sync_all())
        .map_err(io_error("sync sealed reference file"))?;
    Ok(())
}

pub(crate) fn capture_file_seal(
    path: &Path,
) -> Result<Option<ReferenceFileSeal>, ReferenceRuntimeError> {
    let metadata = match fs::metadata(path) {
        Ok(metadata) if metadata.is_file() => metadata,
        Ok(_) => return Ok(None),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => return Ok(None),
        Err(error) => return Err(io_error("stat reference file")(error)),
    };
    Ok(Some(ReferenceFileSeal {
        size_bytes: i64::try_from(metadata.len())
            .map_err(|_| ReferenceRuntimeError::new("reference file size overflow"))?,
        modified_marker: metadata.mtime(),
        changed_marker: metadata.ctime(),
        identity_key: format!("{}:{}", metadata.dev(), metadata.ino()),
        writable: metadata.mode() & 0o222 != 0,
    }))
}

pub(crate) fn sync_directory(path: &Path) -> Result<(), ReferenceRuntimeError> {
    File::open(path)
        .and_then(|directory| directory.sync_all())
        .map_err(io_error("sync reference directory"))
}

pub(crate) fn verify_file_identity(
    path: &Path,
    expected_size: u64,
    expected_sha256: &str,
) -> Result<(), ReferenceRuntimeError> {
    let mut file = File::open(path).map_err(io_error("open reference file"))?;
    let size = file
        .metadata()
        .map_err(io_error("stat reference file"))?
        .len();
    if size != expected_size {
        return Err(ReferenceRuntimeError::new(
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
        return Err(ReferenceRuntimeError::new(
            "reference file SHA-256 does not match signed identity",
        ));
    }
    Ok(())
}

pub(crate) fn atomic_write(path: &Path, bytes: &[u8]) -> Result<(), ReferenceRuntimeError> {
    let parent = path
        .parent()
        .ok_or_else(|| ReferenceRuntimeError::new("reference state path has no parent"))?;
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

pub(crate) fn recover_android_atomic_file_state(path: &Path) -> Result<(), ReferenceRuntimeError> {
    let backup = suffixed_path(path, ".bak");
    let new = suffixed_path(path, ".new");
    let mut changed = false;

    // Existing Android installs used android.util.AtomicFile. Its read path
    // restores the legacy backup after an interrupted write and discards an
    // incomplete .new file. Consume those real upgrade artifacts before Rust
    // becomes the sole state writer; otherwise high-water state could appear
    // missing or corrupt after an app/process interruption during an upgrade.
    if backup.exists() {
        fs::rename(&backup, path).map_err(io_error("restore legacy Android reference state"))?;
        changed = true;
    }
    if new.exists() {
        fs::remove_file(&new).map_err(io_error("discard legacy Android reference state temp"))?;
        changed = true;
    }
    if changed {
        let parent = path
            .parent()
            .ok_or_else(|| ReferenceRuntimeError::new("reference state path has no parent"))?;
        sync_directory(parent)?;
    }
    Ok(())
}

fn suffixed_path(path: &Path, suffix: &str) -> PathBuf {
    let mut value = path.as_os_str().to_os_string();
    value.push(suffix);
    PathBuf::from(value)
}

pub(crate) fn available_bytes(path: &Path) -> Result<u64, ReferenceRuntimeError> {
    let c_path = CString::new(path.as_os_str().as_bytes())
        .map_err(|_| ReferenceRuntimeError::new("reference path contains NUL"))?;
    let mut stats = std::mem::MaybeUninit::<libc::statvfs>::uninit();
    let result = unsafe { libc::statvfs(c_path.as_ptr(), stats.as_mut_ptr()) };
    if result != 0 {
        return Err(ReferenceRuntimeError::new(
            "cannot inspect reference filesystem capacity",
        ));
    }
    let stats = unsafe { stats.assume_init() };
    Ok(stats.f_bavail.saturating_mul(stats.f_frsize))
}

pub(crate) fn io_error(
    context: &'static str,
) -> impl FnOnce(std::io::Error) -> ReferenceRuntimeError {
    move |error| ReferenceRuntimeError::new(format!("{context}: {error}"))
}
