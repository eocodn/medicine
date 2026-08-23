use crate::reference_lifecycle::{
    plan_reference_bootstrap, plan_reference_update, ReferenceUpdatePlan,
};
use crate::reference_signature::{
    ReferenceArtifactKind, ReferenceReleaseArtifact, VerifiedReferenceRelease,
};
use crate::reference_state::{ReferenceStoreState, ReferenceVersion};
use jni::objects::{JObject, JString};
use jni::sys::{jlong, jstring};
use jni::JNIEnv;
use serde::Deserialize;
use serde_json::json;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;

#[derive(Deserialize)]
struct ArtifactWire {
    contract_major: u64,
    key: String,
    sha256: String,
    size_bytes: u64,
    kind: String,
    from_sha256: Option<String>,
    from_size_bytes: Option<u64>,
}

#[derive(Deserialize)]
struct ReleaseWire {
    release_sequence: i64,
    root_hash: String,
    dataset_id: String,
    contract_major: u64,
    target_sha256: String,
    target_size_bytes: u64,
    full: ArtifactWire,
    patches: Vec<ArtifactWire>,
}

impl ArtifactWire {
    fn into_artifact(self) -> Result<ReferenceReleaseArtifact, String> {
        let kind = match self.kind.as_str() {
            "full_gzip" => ReferenceArtifactKind::FullGzip,
            "chunk_patch" => ReferenceArtifactKind::ChunkPatch,
            _ => return Err("invalid reference artifact kind".to_owned()),
        };
        Ok(ReferenceReleaseArtifact {
            contract_major: self.contract_major,
            key: self.key,
            sha256: self.sha256,
            size_bytes: self.size_bytes,
            kind,
            from_sha256: self.from_sha256,
            from_size_bytes: self.from_size_bytes,
        })
    }
}

impl ReleaseWire {
    fn into_release(self) -> Result<VerifiedReferenceRelease, String> {
        Ok(VerifiedReferenceRelease {
            release_sequence: self.release_sequence,
            root_hash: self.root_hash,
            dataset_id: self.dataset_id,
            contract_major: self.contract_major,
            target_sha256: self.target_sha256,
            target_size_bytes: self.target_size_bytes,
            full: self.full.into_artifact()?,
            patches: self
                .patches
                .into_iter()
                .map(ArtifactWire::into_artifact)
                .collect::<Result<Vec<_>, _>>()?,
        })
    }
}

fn required_string(env: &mut JNIEnv<'_>, value: JString<'_>) -> Result<String, String> {
    if value.is_null() {
        return Err("required Java string was null".to_owned());
    }
    env.get_string(&value)
        .map(Into::into)
        .map_err(|error| format!("cannot read Java string: {error}"))
}

fn string_result(env: &mut JNIEnv<'_>, result: Result<String, String>) -> jstring {
    match result {
        Ok(value) => env
            .new_string(value)
            .map(|value| value.into_raw())
            .unwrap_or(ptr::null_mut()),
        Err(message) => {
            let _ = env.throw_new("java/lang/IllegalStateException", message);
            ptr::null_mut()
        }
    }
}

fn release(env: &mut JNIEnv<'_>, value: JString<'_>) -> Result<VerifiedReferenceRelease, String> {
    let raw = required_string(env, value)?;
    serde_json::from_str::<ReleaseWire>(&raw)
        .map_err(|_| "invalid reference release planner input".to_owned())?
        .into_release()
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativePlanReferenceBootstrap(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    expected_contract_major: jlong,
    highest_activated_sequence: jlong,
    release_json: JString<'_>,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let expected_contract_major = i32::try_from(expected_contract_major)
            .map_err(|_| "reference contract major is too large".to_owned())?;
        let release = release(&mut env, release_json)?;
        let state = ReferenceStoreState {
            highest_activated_sequence,
            ..ReferenceStoreState::default()
        };
        plan_reference_bootstrap(expected_contract_major, &state, &release)
            .map_err(|error| error.to_string())?;
        Ok(json!({"status": "bootstrap"}).to_string())
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap planning panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativePlanReferenceUpdate(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    current_json: JString<'_>,
    highest_activated_sequence: jlong,
    release_json: JString<'_>,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let current_raw = required_string(&mut env, current_json)?;
        let current: ReferenceVersion = serde_json::from_str(&current_raw)
            .map_err(|_| "invalid installed reference planner input".to_owned())?;
        let release = release(&mut env, release_json)?;
        let state = ReferenceStoreState {
            active: Some(current.clone()),
            highest_activated_sequence,
            ..ReferenceStoreState::default()
        };
        let response = match plan_reference_update(&current, &state, &release)
            .map_err(|error| error.to_string())?
        {
            ReferenceUpdatePlan::UpToDate => json!({"status": "up_to_date"}),
            ReferenceUpdatePlan::RollbackRejected => json!({"status": "rollback_rejected"}),
            ReferenceUpdatePlan::IdentityConflict => json!({"status": "identity_conflict"}),
            ReferenceUpdatePlan::Stage(stage) => json!({
                "status": "stage",
                "primary_key": stage.primary.key,
                "fallback_full_key": stage
                    .fallback_full
                    .map(|artifact| artifact.key)
                    .unwrap_or_default(),
            }),
        };
        Ok(response.to_string())
    }))
    .unwrap_or_else(|_| Err("native reference update planning panicked".to_owned()));
    string_result(&mut env, result)
}
