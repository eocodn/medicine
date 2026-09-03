#![cfg(feature = "agentctl")]

mod common;

use flate2::{write::GzEncoder, Compression};
use medicine_core::reference_state::{ReferenceStateCodec, ReferenceStoreState, ReferenceVersion};
use serde_json::Value;
use sha2::{Digest, Sha256};
use std::fs;
use std::io::Write;
use std::process::Command;

fn sha256_hex(bytes: &[u8]) -> String {
    format!("{:x}", Sha256::digest(bytes))
}

fn encode_gzip(bytes: &[u8]) -> Vec<u8> {
    let mut encoder = GzEncoder::new(Vec::new(), Compression::default());
    encoder.write_all(bytes).expect("write gzip fixture");
    encoder.finish().expect("finish gzip fixture")
}

const PATCH_SOURCE: &[u8] = b"abcdefgh";
const PATCH_TARGET: &[u8] = b"abCDefgh!";
const PATCH_SOURCE_SHA: &str = "9c56cc51b374c3ba189210d5b6d4bf57790d351c96c47c02190ecf1e430635ab";
const PATCH_TARGET_SHA: &str = "659849ee82da005b08ffa9687311998063fb2bc1abed9367b04e894494cff7e6";
const PATCH_FIRST_ZLIB: &[u8] = &[
    0x78, 0xda, 0x4b, 0x4c, 0x72, 0x76, 0x01, 0x00, 0x03, 0x78, 0x01, 0x4b,
];
const PATCH_LAST_ZLIB: &[u8] = &[0x78, 0xda, 0x53, 0x04, 0x00, 0x00, 0x22, 0x00, 0x22];

fn valid_patch_bytes() -> Vec<u8> {
    let header = format!(
        "{{\"chunk_size\":4,\"format\":\"medicine-chunk-v1\",\"source_sha256\":\"{PATCH_SOURCE_SHA}\",\"source_size_bytes\":8,\"target_sha256\":\"{PATCH_TARGET_SHA}\",\"target_size_bytes\":9}}"
    );
    let mut bytes = Vec::from(&b"MEDPATCH1"[..]);
    bytes.extend_from_slice(&(header.len() as u32).to_be_bytes());
    bytes.extend_from_slice(header.as_bytes());
    for (index, raw_length, compressed) in [
        (0u64, 4u32, PATCH_FIRST_ZLIB),
        (2u64, 1u32, PATCH_LAST_ZLIB),
    ] {
        bytes.extend_from_slice(&index.to_be_bytes());
        bytes.extend_from_slice(&raw_length.to_be_bytes());
        bytes.extend_from_slice(&(compressed.len() as u32).to_be_bytes());
        bytes.extend_from_slice(compressed);
    }
    bytes
}

#[test]
fn reference_state_command_reports_current_state_without_migration_metadata() {
    let state_path = common::temp_sqlite_path("cli-reference-state");
    let version = ReferenceVersion {
        dataset_id: format!("sha256:{}", "a".repeat(64)),
        sha256: "b".repeat(64),
        size_bytes: 123,
        contract_major: 1,
        release_sequence: 7,
    };
    let state = ReferenceStoreState {
        active: Some(version),
        highest_activated_sequence: 7,
        highest_seen_root_sequence: 8,
        highest_seen_root_hash: Some("c".repeat(64)),
        ..ReferenceStoreState::default()
    };
    fs::write(
        &state_path,
        ReferenceStateCodec::encode(&state).expect("encode state"),
    )
    .expect("write state");

    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run reference-state");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("state json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["state"]["active"]["releaseSequence"], 7);
    assert_eq!(value["body"]["state"]["highestSeenRootSequence"], 8);
    assert!(value["body"].get("format").is_none());
    assert!(value["body"].get("legacy").is_none());

    fs::remove_file(state_path).ok();
}

#[test]
fn reference_state_command_fails_closed_for_missing_malformed_or_trailing_state() {
    let state_path = common::temp_sqlite_path("cli-reference-state-invalid");
    fs::remove_file(&state_path).expect("remove reserved missing path");
    let missing = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run missing reference-state");
    assert!(!missing.status.success());
    assert!(String::from_utf8_lossy(&missing.stderr).contains("reference state"));

    fs::write(&state_path, b"not-a-reference-state").expect("write malformed state");
    let malformed = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run malformed reference-state");
    assert!(!malformed.status.success());
    assert!(String::from_utf8_lossy(&malformed.stderr).contains("reference state"));

    let valid = ReferenceStateCodec::encode(&ReferenceStoreState::default()).expect("encode state");
    let mut trailing = valid;
    trailing.push(0xff);
    fs::write(&state_path, trailing).expect("write trailing state");
    let trailing = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-state",
            "--state-file",
            state_path.to_str().expect("state path"),
            "--json",
        ])
        .output()
        .expect("run trailing reference-state");
    assert!(!trailing.status.success());
    assert!(String::from_utf8_lossy(&trailing.stderr).contains("reference state"));
    fs::remove_file(state_path).ok();
}

#[test]
fn reference_apply_full_uses_shared_atomic_core_and_structured_observer_events() {
    let archive = common::temp_sqlite_path("cli-reference-full-gzip");
    let destination = common::temp_sqlite_path("cli-reference-full-destination");
    let target = b"portable-reference-full-target";
    fs::write(&archive, encode_gzip(target)).expect("write gzip fixture");
    fs::write(&destination, b"old-reference").expect("write old destination");
    let target_sha = sha256_hex(target);

    let output = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "full",
            "--artifact",
            archive.to_str().expect("archive path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            &target.len().to_string(),
            "--target-sha256",
            &target_sha,
            "--json",
        ])
        .output()
        .expect("run full reference-apply");
    assert!(
        output.status.success(),
        "{}",
        String::from_utf8_lossy(&output.stderr)
    );
    let value: Value = serde_json::from_slice(&output.stdout).expect("apply json");
    assert_eq!(value["status"], 200);
    assert_eq!(value["body"]["kind"], "full");
    assert_eq!(value["body"]["target_size_bytes"], target.len() as u64);
    assert_eq!(value["body"]["target_sha256"], target_sha);
    assert_eq!(
        fs::read(&destination).expect("read rebuilt destination"),
        target
    );

    let events: Vec<Value> = String::from_utf8_lossy(&output.stderr)
        .lines()
        .filter(|line| !line.trim().is_empty())
        .map(|line| serde_json::from_str(line).expect("observer stderr JSON"))
        .collect();
    assert!(events.iter().any(|event| event["event"] == "progress"));
    assert!(events.iter().any(|event| event["event"] == "checkpoint"));
    assert!(
        events.len() <= 16,
        "observer must stay bounded for a tiny artifact: {events:?}"
    );

    fs::remove_file(archive).ok();
    fs::remove_file(destination).ok();
}

#[test]
fn reference_apply_patch_requires_source_and_checks_signed_target_before_replacement() {
    let artifact = common::temp_sqlite_path("cli-reference-patch");
    let source = common::temp_sqlite_path("cli-reference-patch-source");
    let destination = common::temp_sqlite_path("cli-reference-patch-destination");
    fs::write(&artifact, valid_patch_bytes()).expect("write patch fixture");
    fs::write(&source, PATCH_SOURCE).expect("write patch source");
    fs::write(&destination, b"old-reference").expect("write old destination");

    let missing_source = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            PATCH_TARGET_SHA,
            "--json",
        ])
        .output()
        .expect("run patch without source");
    assert!(!missing_source.status.success());
    assert!(String::from_utf8_lossy(&missing_source.stderr).contains("--source"));
    assert_eq!(
        fs::read(&destination).expect("destination after usage failure"),
        b"old-reference"
    );

    let applied = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--source",
            source.to_str().expect("source path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            PATCH_TARGET_SHA,
            "--json",
        ])
        .output()
        .expect("run valid patch apply");
    assert!(
        applied.status.success(),
        "{}",
        String::from_utf8_lossy(&applied.stderr)
    );
    let value: Value = serde_json::from_slice(&applied.stdout).expect("patch apply json");
    assert_eq!(value["body"]["kind"], "patch");
    assert_eq!(value["body"]["target_sha256"], PATCH_TARGET_SHA);
    assert_eq!(
        fs::read(&destination).expect("read patch destination"),
        PATCH_TARGET
    );

    fs::write(&destination, b"old-reference").expect("restore old destination");
    let mismatch = Command::new(env!("CARGO_BIN_EXE_medicine-agentctl"))
        .args([
            "reference-apply",
            "--kind",
            "patch",
            "--artifact",
            artifact.to_str().expect("artifact path"),
            "--source",
            source.to_str().expect("source path"),
            "--destination",
            destination.to_str().expect("destination path"),
            "--target-size",
            "9",
            "--target-sha256",
            &"d".repeat(64),
            "--json",
        ])
        .output()
        .expect("run patch signed-target mismatch");
    assert!(!mismatch.status.success());
    assert!(String::from_utf8_lossy(&mismatch.stderr).contains("signed target"));
    assert_eq!(
        fs::read(&destination).expect("destination after signed-target mismatch"),
        b"old-reference"
    );

    fs::remove_file(artifact).ok();
    fs::remove_file(source).ok();
    fs::remove_file(destination).ok();
}
