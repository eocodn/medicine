mod common;

use std::collections::HashSet;
use std::fs;
use std::sync::{Arc, Mutex};
use std::thread;

#[test]
fn temp_sqlite_paths_are_reserved_uniquely_under_parallel_allocation() {
    let paths = Arc::new(Mutex::new(Vec::new()));
    let mut workers = Vec::new();
    for _ in 0..8 {
        let paths = Arc::clone(&paths);
        workers.push(thread::spawn(move || {
            for _ in 0..64 {
                let path = common::temp_sqlite_path("collision-contract");
                assert!(path.exists(), "allocator must reserve the path atomically");
                paths.lock().expect("path collection lock").push(path);
            }
        }));
    }
    for worker in workers {
        worker.join().expect("temp allocation worker");
    }

    let paths = Arc::try_unwrap(paths)
        .expect("all workers released path collection")
        .into_inner()
        .expect("path collection lock");
    let unique = paths.iter().collect::<HashSet<_>>();
    assert_eq!(unique.len(), paths.len());

    for path in paths {
        fs::remove_file(path).expect("remove reserved temp sqlite path");
    }
}
