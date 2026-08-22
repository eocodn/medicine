use std::fs::OpenOptions;
use std::io::ErrorKind;
use std::path::PathBuf;
use std::sync::atomic::{AtomicU64, Ordering};
use std::time::{SystemTime, UNIX_EPOCH};

static NEXT_PATH: AtomicU64 = AtomicU64::new(0);

pub(crate) fn temp_sqlite_path(label: &str) -> PathBuf {
    let started_at = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .expect("clock before epoch")
        .as_nanos();
    loop {
        let sequence = NEXT_PATH.fetch_add(1, Ordering::Relaxed);
        let path = std::env::temp_dir().join(format!(
            "medicine-{label}-p{}-{started_at}-{sequence}.sqlite",
            std::process::id()
        ));
        match OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => {
                drop(file);
                return path;
            }
            Err(error) if error.kind() == ErrorKind::AlreadyExists => continue,
            Err(error) => panic!("reserve temp sqlite path {path:?}: {error}"),
        }
    }
}
