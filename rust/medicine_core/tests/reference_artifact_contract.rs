mod common;

use medicine_core::reference_artifacts::{
    apply_chunk_patch, apply_chunk_patch_verified, decompress_snapshot, ArtifactObserver,
};
use std::fs;
use std::path::{Path, PathBuf};

const SOURCE: &[u8] = b"abcdefgh";
const TARGET: &[u8] = b"abCDefgh!";
const SOURCE_SHA: &str = "9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab";
const TARGET_SHA: &str = "659849ee82da005b08ffa9687311998063fb2bc1abed9367b04e894494cff7e6";
const FULL_GZIP: &[u8] = &[
    0x1f, 0x8b, 0x08, 0x00, 0x00, 0x00, 0x00, 0x00, 0x02, 0xff, 0x4b, 0x4c, 0x72, 0x76, 0x49, 0x4d,
    0x4b, 0xcf, 0x50, 0x04, 0x00, 0xa7, 0x7d, 0xbf, 0x3d, 0x09, 0x00, 0x00, 0x00,
];

// These are the zlib level-9 records emitted by medicine_canonical.release.
const FIRST_ZLIB: &[u8] = &[
    0x78, 0xda, 0x4b, 0x4c, 0x72, 0x76, 0x01, 0x00, 0x03, 0x78, 0x01, 0x4b,
];
const LAST_ZLIB: &[u8] = &[0x78, 0xda, 0x53, 0x04, 0x00, 0x00, 0x22, 0x00, 0x22];

#[derive(Default)]
struct RecordingObserver {
    progress: Vec<(String, u64, u64)>,
    checkpoints: Vec<PathBuf>,
}

impl ArtifactObserver for RecordingObserver {
    fn progress(&mut self, phase: &str, completed_bytes: u64, total_bytes: u64) {
        self.progress
            .push((phase.to_owned(), completed_bytes, total_bytes));
    }

    fn checkpoint(&mut self, path: &Path) {
        self.checkpoints.push(path.to_owned());
    }
}

fn fixture_dir(label: &str) -> PathBuf {
    let path = common::temp_sqlite_path(label);
    fs::remove_file(&path).expect("remove reserved fixture path");
    fs::create_dir_all(&path).expect("create fixture directory");
    path
}

fn patch_bytes(target_sha: &str, records: &[(u64, u32, &[u8])]) -> Vec<u8> {
    let header = format!(
        "{{\"chunk_size\":4,\"format\":\"medicine-chunk-v1\",\"source_sha256\":\"{SOURCE_SHA}\",\"source_size_bytes\":8,\"target_sha256\":\"{target_sha}\",\"target_size_bytes\":9}}"
    );
    let mut bytes = Vec::from(&b"MEDPATCH1"[..]);
    bytes.extend_from_slice(&(header.len() as u32).to_be_bytes());
    bytes.extend_from_slice(header.as_bytes());
    for (index, raw_length, compressed) in records {
        bytes.extend_from_slice(&index.to_be_bytes());
        bytes.extend_from_slice(&raw_length.to_be_bytes());
        bytes.extend_from_slice(&(compressed.len() as u32).to_be_bytes());
        bytes.extend_from_slice(compressed);
    }
    bytes
}

fn write_patch(dir: &Path, name: &str, bytes: &[u8]) -> PathBuf {
    let path = dir.join(name);
    fs::write(&path, bytes).expect("write patch fixture");
    path
}

fn valid_patch(dir: &Path) -> PathBuf {
    write_patch(
        dir,
        "valid.mpatch",
        &patch_bytes(TARGET_SHA, &[(0, 4, FIRST_ZLIB), (2, 1, LAST_ZLIB)]),
    )
}

fn assert_destination_unchanged(path: &Path) {
    assert_eq!(
        fs::read(path).expect("read preserved destination"),
        b"keep-me"
    );
    assert!(!path.with_file_name("destination.tmp").exists());
}

#[test]
fn chunk_patch_rebuilds_exact_target_and_reports_progress_and_checkpoint() {
    let dir = fixture_dir("artifact-patch-valid");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let patch = valid_patch(&dir);
    let mut observer = RecordingObserver::default();

    let result = apply_chunk_patch(&source, &patch, &destination, &mut observer)
        .expect("apply valid medicine-chunk-v1 patch");

    assert_eq!(fs::read(&destination).expect("read rebuilt target"), TARGET);
    assert_eq!(result.source_size_bytes, SOURCE.len() as u64);
    assert_eq!(result.source_sha256, SOURCE_SHA);
    assert_eq!(result.target_size_bytes, TARGET.len() as u64);
    assert_eq!(result.target_sha256, TARGET_SHA);
    assert!(observer
        .progress
        .iter()
        .any(|(_, completed, total)| *completed == *total && *total > 0));
    assert!(observer
        .checkpoints
        .iter()
        .all(|checkpoint| checkpoint.file_name().is_some()));
    fs::remove_dir_all(dir).expect("remove patch fixture");
}

#[test]
fn verified_patch_rejects_signed_target_mismatch_before_mutating_destination() {
    let dir = fixture_dir("artifact-patch-signed-target-mismatch");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let patch = valid_patch(&dir);
    let mut observer = RecordingObserver::default();

    let error = apply_chunk_patch_verified(
        &source,
        &patch,
        &destination,
        TARGET.len() as u64,
        &"d".repeat(64),
        &mut observer,
    )
    .expect_err("signed target mismatch must fail before replacement");

    assert!(error.to_string().contains("signed target identity"));
    assert_destination_unchanged(&destination);
    assert!(observer.progress.is_empty());
    assert!(observer.checkpoints.is_empty());
    fs::remove_dir_all(dir).ok();
}

#[test]
fn patch_rejects_wrong_source_size_or_sha_before_mutating_destination() {
    let dir = fixture_dir("artifact-patch-source");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, b"wrong").expect("write wrong source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let patch = valid_patch(&dir);
    let mut observer = RecordingObserver::default();

    let error = apply_chunk_patch(&source, &patch, &destination, &mut observer)
        .expect_err("wrong source must fail closed");
    assert!(error.to_string().contains("source"));
    assert_destination_unchanged(&destination);
    fs::remove_dir_all(dir).expect("remove source fixture");
}

#[test]
fn patch_rejects_truncated_zlib_payload_and_preserves_existing_destination() {
    let dir = fixture_dir("artifact-patch-truncated");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let mut patch = patch_bytes(TARGET_SHA, &[(0, 4, FIRST_ZLIB), (2, 1, LAST_ZLIB)]);
    patch.truncate(patch.len() - 2);
    let patch_path = write_patch(&dir, "truncated.mpatch", &patch);
    let mut observer = RecordingObserver::default();

    let error = apply_chunk_patch(&source, &patch_path, &destination, &mut observer)
        .expect_err("truncated zlib record must fail");
    assert!(error.to_string().contains("truncated") || error.to_string().contains("compressed"));
    assert_destination_unchanged(&destination);
    fs::remove_dir_all(dir).expect("remove truncation fixture");
}

#[test]
fn patch_rejects_corrupt_zlib_payload_and_preserves_existing_destination() {
    let dir = fixture_dir("artifact-patch-zlib");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let corrupt = [0x78, 0xda, 0x00, 0x00, 0x00, 0x00];
    let patch_path = write_patch(
        &dir,
        "corrupt.mpatch",
        &patch_bytes(TARGET_SHA, &[(0, 4, &corrupt), (2, 1, LAST_ZLIB)]),
    );
    let mut observer = RecordingObserver::default();

    let error = apply_chunk_patch(&source, &patch_path, &destination, &mut observer)
        .expect_err("corrupt zlib record must fail");
    assert!(error.to_string().contains("compressed") || error.to_string().contains("zlib"));
    assert_destination_unchanged(&destination);
    fs::remove_dir_all(dir).expect("remove zlib fixture");
}

#[test]
fn patch_rejects_reordered_or_duplicate_chunk_indexes() {
    for (label, records) in [
        ("reordered", vec![(2, 1, LAST_ZLIB), (0, 4, FIRST_ZLIB)]),
        ("duplicate", vec![(0, 4, FIRST_ZLIB), (0, 4, FIRST_ZLIB)]),
    ] {
        let dir = fixture_dir(&format!("artifact-patch-{label}"));
        let source = dir.join("source.sqlite");
        let destination = dir.join("destination.sqlite");
        fs::write(&source, SOURCE).expect("write source");
        fs::write(&destination, b"keep-me").expect("write destination");
        let patch_path = write_patch(&dir, "invalid.mpatch", &patch_bytes(TARGET_SHA, &records));
        let mut observer = RecordingObserver::default();

        let error = apply_chunk_patch(&source, &patch_path, &destination, &mut observer)
            .expect_err("chunk ordering invariant must fail");
        assert!(error.to_string().contains("chunk"));
        assert_destination_unchanged(&destination);
        fs::remove_dir_all(dir).expect("remove ordering fixture");
    }
}

#[test]
fn patch_rejects_target_hash_mismatch_and_keeps_old_destination_atomically() {
    let dir = fixture_dir("artifact-patch-target");
    let source = dir.join("source.sqlite");
    let destination = dir.join("destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    fs::write(&destination, b"keep-me").expect("write destination");
    let patch_path = write_patch(
        &dir,
        "wrong-target.mpatch",
        &patch_bytes(&"0".repeat(64), &[(0, 4, FIRST_ZLIB), (2, 1, LAST_ZLIB)]),
    );
    let mut observer = RecordingObserver::default();

    let error = apply_chunk_patch(&source, &patch_path, &destination, &mut observer)
        .expect_err("target hash mismatch must fail");
    assert!(error.to_string().contains("target") || error.to_string().contains("SHA"));
    assert_destination_unchanged(&destination);
    fs::remove_dir_all(dir).expect("remove target fixture");
}

#[test]
fn full_gzip_extraction_is_deterministic_and_verifies_target_identity() {
    let dir = fixture_dir("artifact-full-valid");
    let archive = dir.join("snapshot.sqlite.gz");
    let first = dir.join("first.sqlite");
    let second = dir.join("second.sqlite");
    fs::write(&archive, FULL_GZIP).expect("write gzip fixture");
    let mut first_observer = RecordingObserver::default();
    let mut second_observer = RecordingObserver::default();

    let first_result = decompress_snapshot(
        &archive,
        &first,
        TARGET.len() as u64,
        TARGET_SHA,
        &mut first_observer,
    )
    .expect("extract full gzip");
    let second_result = decompress_snapshot(
        &archive,
        &second,
        TARGET.len() as u64,
        TARGET_SHA,
        &mut second_observer,
    )
    .expect("extract full gzip deterministically");

    assert_eq!(fs::read(&first).expect("read first extraction"), TARGET);
    assert_eq!(
        fs::read(&first).expect("read first bytes"),
        fs::read(&second).expect("read second bytes")
    );
    assert_eq!(first_result.target_size_bytes, TARGET.len() as u64);
    assert_eq!(first_result.target_sha256, TARGET_SHA);
    assert_eq!(second_result.target_sha256, first_result.target_sha256);
    assert!(first_observer
        .progress
        .iter()
        .any(|(_, completed, total)| completed == total));
    fs::remove_dir_all(dir).expect("remove full fixture");
}

#[test]
fn full_gzip_truncation_or_target_mismatch_preserves_destination_and_cleans_temp() {
    for (label, archive_bytes, expected_sha) in [
        (
            "truncated",
            &FULL_GZIP[..FULL_GZIP.len() - 3],
            TARGET_SHA.to_owned(),
        ),
        ("wrong-target", FULL_GZIP, "0".repeat(64)),
    ] {
        let dir = fixture_dir(&format!("artifact-full-{label}"));
        let archive = dir.join("snapshot.sqlite.gz");
        let destination = dir.join("destination.sqlite");
        fs::write(&archive, archive_bytes).expect("write gzip failure fixture");
        fs::write(&destination, b"keep-me").expect("write destination");
        let mut observer = RecordingObserver::default();

        let error = decompress_snapshot(
            &archive,
            &destination,
            TARGET.len() as u64,
            &expected_sha,
            &mut observer,
        )
        .expect_err("invalid full artifact must fail closed");
        assert!(error.to_string().contains("gzip") || error.to_string().contains("target"));
        assert_destination_unchanged(&destination);
        fs::remove_dir_all(dir).expect("remove full failure fixture");
    }
}

#[test]
fn patch_rejects_chunk_lengths_that_do_not_match_target_geometry() {
    for raw_length in [0, 2] {
        let dir = fixture_dir(&format!("artifact-patch-geometry-{raw_length}"));
        let source = dir.join("source.sqlite");
        let destination = dir.join("destination.sqlite");
        fs::write(&source, SOURCE).expect("write source");
        fs::write(&destination, b"keep-me").expect("write destination");
        let patch_path = write_patch(
            &dir,
            "invalid-geometry.mpatch",
            &patch_bytes(TARGET_SHA, &[(0, raw_length, FIRST_ZLIB)]),
        );
        let mut observer = RecordingObserver::default();

        let error = apply_chunk_patch(&source, &patch_path, &destination, &mut observer)
            .expect_err("chunk geometry must be strict");
        assert!(error.to_string().contains("geometry"));
        assert_destination_unchanged(&destination);
        fs::remove_dir_all(dir).expect("remove geometry fixture");
    }
}

#[test]
fn valid_patch_creates_missing_destination_parents() {
    let dir = fixture_dir("artifact-patch-parent");
    let source = dir.join("source.sqlite");
    let destination = dir.join("nested/deeper/destination.sqlite");
    fs::write(&source, SOURCE).expect("write source");
    let patch = valid_patch(&dir);
    let mut observer = RecordingObserver::default();

    apply_chunk_patch(&source, &patch, &destination, &mut observer)
        .expect("patch should create destination parents");
    assert_eq!(fs::read(&destination).expect("read rebuilt target"), TARGET);
    fs::remove_dir_all(dir).expect("remove parent fixture");
}

#[test]
fn concatenated_gzip_members_are_extracted_like_python_gzip_open() {
    let dir = fixture_dir("artifact-full-concatenated");
    let archive = dir.join("snapshot.sqlite.gz");
    let destination = dir.join("nested/destination.sqlite");
    let first = [
        31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 75, 4, 0, 67, 190, 183, 232, 1, 0, 0, 0,
    ];
    let second = [
        31, 139, 8, 0, 0, 0, 0, 0, 2, 255, 75, 2, 0, 249, 239, 190, 113, 1, 0, 0, 0,
    ];
    let mut archive_bytes = first.to_vec();
    archive_bytes.extend_from_slice(&second);
    fs::write(&archive, archive_bytes).expect("write concatenated gzip");
    let mut observer = RecordingObserver::default();

    let result = decompress_snapshot(
        &archive,
        &destination,
        2,
        "fb8e20fc2e4c3f248c60c39bd652f3c1347298bb977b8b4d5903b85055620603",
        &mut observer,
    )
    .expect("extract concatenated gzip members");
    assert_eq!(
        fs::read(&destination).expect("read concatenated output"),
        b"ab"
    );
    assert_eq!(result.target_size_bytes, 2);
    fs::remove_dir_all(dir).expect("remove concatenated fixture");
}
