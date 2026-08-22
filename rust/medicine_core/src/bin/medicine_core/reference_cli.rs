use medicine_core::reference_artifacts::{
    apply_chunk_patch_verified, decompress_snapshot, ArtifactObserver, ArtifactResult,
};
use medicine_core::reference_state::ReferenceStateCodec;
use serde_json::{json, Value};
use std::fs;
use std::path::{Path, PathBuf};

const PROGRESS_BUCKETS: u64 = 20;

pub fn reference_state(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let mut state_file: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--state-file" => {
                index += 1;
                state_file = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }

    let state_file = PathBuf::from(state_file.ok_or_else(usage)?);
    let bytes = fs::read(&state_file).map_err(|error| {
        format!(
            "cannot read reference state {}: {error}",
            state_file.display()
        )
    })?;
    let legacy = ReferenceStateCodec::is_legacy_v1(&bytes);
    let state = ReferenceStateCodec::decode(&bytes)
        .map_err(|error| format!("cannot decode reference state: {error}"))?;
    let response = json!({
        "status": 200,
        "body": {
            "format": if legacy { "MEDREFSTATE1" } else { "MEDREFSTATE3" },
            "legacy": legacy,
            "state": state,
        }
    });
    emit(&response, json_output)
}

pub fn reference_apply(args: &[String], usage: fn() -> String) -> Result<(), String> {
    let mut kind: Option<String> = None;
    let mut artifact: Option<String> = None;
    let mut source: Option<String> = None;
    let mut destination: Option<String> = None;
    let mut target_size: Option<u64> = None;
    let mut target_sha256: Option<String> = None;
    let mut json_output = false;
    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--kind" => {
                index += 1;
                kind = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--artifact" => {
                index += 1;
                artifact = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--source" => {
                index += 1;
                source = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--destination" => {
                index += 1;
                destination = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--target-size" => {
                index += 1;
                let raw = args.get(index).ok_or_else(usage)?;
                let parsed = raw
                    .parse::<u64>()
                    .map_err(|_| "--target-size must be a positive integer".to_owned())?;
                if parsed == 0 {
                    return Err("--target-size must be a positive integer".to_owned());
                }
                target_size = Some(parsed);
            }
            "--target-sha256" => {
                index += 1;
                target_sha256 = Some(args.get(index).ok_or_else(usage)?.to_owned());
            }
            "--json" => json_output = true,
            _ => return Err(usage()),
        }
        index += 1;
    }

    let kind = kind.ok_or_else(usage)?;
    let artifact = PathBuf::from(artifact.ok_or_else(usage)?);
    let destination = PathBuf::from(destination.ok_or_else(usage)?);
    let target_size = target_size.ok_or_else(usage)?;
    let target_sha256 = target_sha256.ok_or_else(usage)?;
    if !is_sha256(&target_sha256) {
        return Err("--target-sha256 must be 64 lowercase hexadecimal characters".to_owned());
    }

    let mut observer = JsonArtifactObserver::default();
    let result = match kind.as_str() {
        "full" => {
            if source.is_some() {
                return Err("--source is only valid with --kind patch".to_owned());
            }
            decompress_snapshot(
                &artifact,
                &destination,
                target_size,
                &target_sha256,
                &mut observer,
            )
        }
        "patch" => {
            let source =
                source.ok_or_else(|| "--source is required with --kind patch".to_owned())?;
            apply_chunk_patch_verified(
                Path::new(&source),
                &artifact,
                &destination,
                target_size,
                &target_sha256,
                &mut observer,
            )
        }
        _ => return Err("--kind must be full or patch".to_owned()),
    }
    .map_err(|error| format!("reference apply failed: {error}"))?;

    emit_apply_result(&kind, &destination, result, json_output)
}

fn emit_apply_result(
    kind: &str,
    destination: &Path,
    result: ArtifactResult,
    json_output: bool,
) -> Result<(), String> {
    let response = json!({
        "status": 200,
        "body": {
            "kind": kind,
            "destination": destination,
            "source_size_bytes": result.source_size_bytes,
            "source_sha256": result.source_sha256,
            "target_size_bytes": result.target_size_bytes,
            "target_sha256": result.target_sha256,
        }
    });
    emit(&response, json_output)
}

fn emit(response: &Value, json_output: bool) -> Result<(), String> {
    if json_output {
        println!("{response}");
    } else {
        println!("{}", response["body"]);
    }
    Ok(())
}

fn is_sha256(value: &str) -> bool {
    value.len() == 64
        && value
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

#[derive(Default)]
struct JsonArtifactObserver {
    phase: Option<String>,
    bucket: u64,
}

impl ArtifactObserver for JsonArtifactObserver {
    fn progress(&mut self, phase: &str, completed_bytes: u64, total_bytes: u64) {
        let phase_changed = self.phase.as_deref() != Some(phase);
        let bucket = if total_bytes == 0 {
            0
        } else {
            completed_bytes
                .min(total_bytes)
                .saturating_mul(PROGRESS_BUCKETS)
                / total_bytes
        };
        let finished = total_bytes > 0 && completed_bytes >= total_bytes;
        if phase_changed || bucket > self.bucket || finished {
            eprintln!(
                "{}",
                json!({
                    "event": "progress",
                    "phase": phase,
                    "completed_bytes": completed_bytes,
                    "total_bytes": total_bytes,
                })
            );
            self.phase = Some(phase.to_owned());
            self.bucket = bucket;
        }
    }

    fn checkpoint(&mut self, path: &Path) {
        eprintln!(
            "{}",
            json!({
                "event": "checkpoint",
                "path": path,
            })
        );
    }
}
