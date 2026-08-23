use crate::MedicineEngine;
use jni::objects::{GlobalRef, JByteArray, JObject, JString, JValue};
use jni::sys::{jboolean, jlong, jstring, JNI_FALSE, JNI_TRUE};
use jni::{JNIEnv, JavaVM};
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::ptr;
use std::sync::Mutex;

type EngineHandle = Mutex<MedicineEngine>;

struct JniArtifactObserver {
    vm: JavaVM,
    observer: GlobalRef,
    callback_error: Option<String>,
}

impl JniArtifactObserver {
    fn callback(&mut self, method: &str, value: &str, completed: u64, total: u64) {
        if self.callback_error.is_some() {
            return;
        }
        let result = self
            .vm
            .attach_current_thread()
            .map_err(|error| error.to_string())
            .and_then(|mut env| {
                let value = env.new_string(value).map_err(|error| error.to_string())?;
                let arguments = if method == "progress" {
                    vec![
                        JValue::Object(&value),
                        JValue::Long(completed as jlong),
                        JValue::Long(total as jlong),
                    ]
                } else {
                    vec![JValue::Object(&value)]
                };
                env.call_method(
                    self.observer.as_obj(),
                    method,
                    if method == "progress" {
                        "(Ljava/lang/String;JJ)V"
                    } else {
                        "(Ljava/lang/String;)V"
                    },
                    &arguments,
                )
                .map(|_| ())
                .map_err(|error| {
                    let _ = env.exception_clear();
                    error.to_string()
                })
            });
        if let Err(error) = result {
            self.callback_error = Some(format!("reference artifact observer failed: {error}"));
        }
    }

    fn finish(self) -> Result<(), String> {
        self.callback_error.map_or(Ok(()), Err)
    }
}

impl crate::reference_artifacts::ArtifactObserver for JniArtifactObserver {
    fn progress(&mut self, phase: &str, completed_bytes: u64, total_bytes: u64) {
        self.callback("progress", phase, completed_bytes, total_bytes);
    }

    fn checkpoint(&mut self, path: &Path) {
        self.callback("checkpoint", &path.to_string_lossy(), 0, 0);
    }
}

fn optional_string(env: &mut JNIEnv<'_>, value: JString<'_>) -> Result<Option<String>, String> {
    if value.is_null() {
        return Ok(None);
    }
    env.get_string(&value)
        .map(|text| Some(text.into()))
        .map_err(|error| format!("cannot read Java string: {error}"))
}

fn required_string(env: &mut JNIEnv<'_>, value: JString<'_>) -> Result<String, String> {
    optional_string(env, value)?.ok_or_else(|| "required Java string was null".to_owned())
}

fn decode_hex(value: &str) -> Result<Vec<u8>, String> {
    if !value.len().is_multiple_of(2) || !value.bytes().all(|byte| byte.is_ascii_hexdigit()) {
        return Err("invalid reference signing key encoding".to_owned());
    }
    (0..value.len())
        .step_by(2)
        .map(|offset| {
            u8::from_str_radix(&value[offset..offset + 2], 16)
                .map_err(|_| "invalid reference signing key encoding".to_owned())
        })
        .collect()
}

fn with_engine<R>(
    handle: jlong,
    operation: impl FnOnce(&mut MedicineEngine) -> Result<R, String>,
) -> Result<R, String> {
    if handle == 0 {
        return Err("native medicine engine is closed".to_owned());
    }
    let mutex = unsafe { &*(handle as *const EngineHandle) };
    let mut engine = mutex
        .lock()
        .map_err(|_| "native medicine engine lock is poisoned".to_owned())?;
    operation(&mut engine)
}

fn throw(env: &mut JNIEnv<'_>, message: &str) {
    let _ = env.throw_new("java/lang/IllegalStateException", message);
}

fn string_result(env: &mut JNIEnv<'_>, result: Result<String, String>) -> jstring {
    match result {
        Ok(value) => match env.new_string(value) {
            Ok(value) => value.into_raw(),
            Err(error) => {
                throw(env, &format!("cannot create Java string: {error}"));
                ptr::null_mut()
            }
        },
        Err(error) => {
            throw(env, &error);
            ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeVerifyDatabase(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    database_path: JString<'_>,
    contract_major: jlong,
    dataset_id: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let database_path = required_string(&mut env, database_path)?;
        let dataset_id = required_string(&mut env, dataset_id)?;
        let contract_major = u64::try_from(contract_major)
            .map_err(|_| "reference contract major must be positive".to_owned())?;
        crate::verify_reference_database(Path::new(&database_path), contract_major, &dataset_id)
            .map(|_| ())
            .map_err(|error| error.to_string())
    }))
    .unwrap_or_else(|_| Err("native reference database verification panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeVerifyRuntimeCapabilities(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    database_path: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let database_path = required_string(&mut env, database_path)?;
        crate::verify_reference_runtime_capabilities(Path::new(&database_path))
            .map_err(|error| error.to_string())
    }))
    .unwrap_or_else(|_| {
        Err("native reference runtime capability verification panicked".to_owned())
    });
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeVerifyRuntimeMaterialization(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    database_path: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let database_path = required_string(&mut env, database_path)?;
        crate::reference_capabilities::verify_reference_runtime_materialization(Path::new(
            &database_path,
        ))
        .map_err(|error| error.to_string())
    }))
    .unwrap_or_else(|_| {
        Err("native reference runtime materialization verification panicked".to_owned())
    });
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeVerifyManifest(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    envelope_version: jlong,
    algorithm: JString<'_>,
    key_id: JString<'_>,
    release_sequence: jlong,
    payload_base64: JString<'_>,
    signature_base64: JString<'_>,
    minimum_exclusive_sequence: jlong,
    has_minimum_exclusive_sequence: jboolean,
    trusted_keys_json: JString<'_>,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let algorithm = required_string(&mut env, algorithm)?;
        let key_id = required_string(&mut env, key_id)?;
        let payload_base64 = required_string(&mut env, payload_base64)?;
        let signature_base64 = required_string(&mut env, signature_base64)?;
        let trusted_keys_json = required_string(&mut env, trusted_keys_json)?;
        let keys: serde_json::Value = serde_json::from_str(&trusted_keys_json)
            .map_err(|_| "invalid trusted reference signing keys".to_owned())?;
        let keys = keys
            .as_object()
            .ok_or_else(|| "invalid trusted reference signing keys".to_owned())?
            .iter()
            .map(|(key_id, encoded)| {
                let encoded = encoded
                    .as_str()
                    .ok_or_else(|| "invalid trusted reference signing key".to_owned())?;
                Ok(crate::TrustedSigningKey::active(
                    key_id,
                    decode_hex(encoded)?,
                ))
            })
            .collect::<Result<Vec<_>, String>>()?;
        let verifier = crate::ReferenceManifestVerifier::new(keys);
        let verified = verifier
            .verify(
                envelope_version,
                &algorithm,
                &key_id,
                release_sequence,
                &payload_base64,
                &signature_base64,
                (has_minimum_exclusive_sequence == JNI_TRUE).then_some(minimum_exclusive_sequence),
            )
            .map_err(|error| error.to_string())?;
        Ok(serde_json::json!({
            "key_id": verified.key_id,
            "release_sequence": verified.release_sequence,
            "payload_base64": payload_base64,
        })
        .to_string())
    }))
    .unwrap_or_else(|_| Err("native reference manifest verification panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeParseReleaseRoot(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    release_sequence: jlong,
    payload: JByteArray<'_>,
    contract_major: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let payload = env
            .convert_byte_array(&payload)
            .map_err(|error| format!("cannot read reference root payload: {error}"))?;
        let contract_major = u64::try_from(contract_major)
            .map_err(|_| "reference contract major must be positive".to_owned())?;
        match crate::ReferenceReleaseProtocolV2::parse_verified_root(
            release_sequence,
            &payload,
            contract_major,
        ) {
            Ok(release) => {
                let artifact = |value: &crate::ReferenceReleaseArtifact| {
                    serde_json::json!({
                        "contract_major": value.contract_major,
                        "key": value.key,
                        "sha256": value.sha256,
                        "size_bytes": value.size_bytes,
                        "kind": match value.kind {
                            crate::ReferenceArtifactKind::FullGzip => "full_gzip",
                            crate::ReferenceArtifactKind::ChunkPatch => "chunk_patch",
                        },
                        "from_sha256": value.from_sha256,
                        "from_size_bytes": value.from_size_bytes,
                    })
                };
                Ok(serde_json::json!({
                    "status": "verified",
                    "release_sequence": release.release_sequence,
                    "root_hash": release.root_hash,
                    "dataset_id": release.dataset_id,
                    "contract_major": release.contract_major,
                    "target_sha256": release.target_sha256,
                    "target_size_bytes": release.target_size_bytes,
                    "full": artifact(&release.full),
                    "patches": release.patches.iter().map(artifact).collect::<Vec<_>>(),
                })
                .to_string())
            }
            Err(error) if error.to_string().contains(" is retired") => {
                let root: serde_json::Value = serde_json::from_slice(&payload)
                    .map_err(|_| "invalid retired reference root JSON".to_owned())?;
                let current = root
                    .get("current_contract_major")
                    .and_then(serde_json::Value::as_u64)
                    .ok_or_else(|| "invalid retired reference current contract".to_owned())?;
                let minimum = root
                    .get("minimum_supported_contract_major")
                    .and_then(serde_json::Value::as_u64)
                    .ok_or_else(|| "invalid retired reference minimum contract".to_owned())?;
                use sha2::{Digest, Sha256};
                let root_hash = Sha256::digest(&payload)
                    .iter()
                    .map(|byte| format!("{byte:02x}"))
                    .collect::<String>();
                Ok(serde_json::json!({
                    "status": "retired",
                    "release_sequence": release_sequence,
                    "root_hash": root_hash,
                    "current_contract_major": current,
                    "minimum_supported_contract_major": minimum,
                })
                .to_string())
            }
            Err(error) => Err(error.to_string()),
        }
    }))
    .unwrap_or_else(|_| Err("native reference root parsing panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeRebuildArtifact(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    source_path: JString<'_>,
    artifact_path: JString<'_>,
    output_path: JString<'_>,
    target_size_bytes: jlong,
    target_sha256: JString<'_>,
    chunk_patch: jboolean,
    observer: JObject<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let source_path = optional_string(&mut env, source_path)?;
        let artifact_path = required_string(&mut env, artifact_path)?;
        let output_path = required_string(&mut env, output_path)?;
        let target_sha256 = required_string(&mut env, target_sha256)?;
        let target_size_bytes = u64::try_from(target_size_bytes)
            .ok()
            .filter(|size| *size > 0)
            .ok_or_else(|| "reference target size must be positive".to_owned())?;
        let mut observer = JniArtifactObserver {
            vm: env
                .get_java_vm()
                .map_err(|error| format!("cannot access Java VM: {error}"))?,
            observer: env
                .new_global_ref(observer)
                .map_err(|error| format!("cannot retain reference artifact observer: {error}"))?,
            callback_error: None,
        };
        if chunk_patch == JNI_TRUE {
            let source_path = source_path
                .ok_or_else(|| "reference patch requires an installed base".to_owned())?;
            let output = Path::new(&output_path);
            let file_name = output
                .file_name()
                .and_then(|name| name.to_str())
                .ok_or_else(|| "reference output path has no file name".to_owned())?;
            let verified_output = output.with_file_name(format!("{file_name}.native"));
            if verified_output.exists() {
                std::fs::remove_file(&verified_output)
                    .map_err(|error| format!("cannot remove stale reference output: {error}"))?;
            }
            let result = crate::reference_artifacts::apply_chunk_patch(
                Path::new(&source_path),
                Path::new(&artifact_path),
                &verified_output,
                &mut observer,
            )
            .map_err(|error| error.to_string())?;
            if result.target_size_bytes != target_size_bytes
                || result.target_sha256 != target_sha256
            {
                let _ = std::fs::remove_file(&verified_output);
                return Err("reference patch target does not match signed release".to_owned());
            }
            if let Err(error) = std::fs::rename(&verified_output, output) {
                let _ = std::fs::remove_file(&verified_output);
                return Err(format!("cannot finish verified reference output: {error}"));
            }
        } else {
            crate::reference_artifacts::decompress_snapshot(
                Path::new(&artifact_path),
                Path::new(&output_path),
                target_size_bytes,
                &target_sha256,
                &mut observer,
            )
            .map_err(|error| error.to_string())?;
        }
        observer.finish()?;
        Ok(())
    }))
    .unwrap_or_else(|_| Err("native reference artifact rebuild panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeCreate(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    canonical_db: JString<'_>,
    personal_db: JString<'_>,
    reference_unavailable_reason: JString<'_>,
) -> jlong {
    match catch_unwind(AssertUnwindSafe(|| {
        let canonical_db = optional_string(&mut env, canonical_db)?;
        let personal_db = optional_string(&mut env, personal_db)?;
        let reason = optional_string(&mut env, reference_unavailable_reason)?;
        let engine = MedicineEngine::new(
            canonical_db.as_deref().map(Path::new),
            personal_db.as_deref().map(Path::new),
            reason.as_deref(),
        );
        Ok::<_, String>(Box::into_raw(Box::new(Mutex::new(engine))) as jlong)
    })) {
        Ok(Ok(handle)) => handle,
        Ok(Err(error)) => {
            throw(&mut env, &error);
            0
        }
        Err(_) => {
            throw(&mut env, "native medicine engine initialization panicked");
            0
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeDestroy(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    if handle == 0 {
        return;
    }
    if catch_unwind(AssertUnwindSafe(|| unsafe {
        drop(Box::from_raw(handle as *mut EngineHandle));
    }))
    .is_err()
    {
        throw(&mut env, "native medicine engine shutdown panicked");
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeRequestAccess(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    method: JString<'_>,
    path: JString<'_>,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let method = required_string(&mut env, method)?;
        let path = required_string(&mut env, path)?;
        with_engine(handle, |engine| {
            Ok(engine.request_access(&method, &path).as_str().to_owned())
        })
    }))
    .unwrap_or_else(|_| Err("native request access classification panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeRequest(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    method: JString<'_>,
    path: JString<'_>,
    body: JString<'_>,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let method = required_string(&mut env, method)?;
        let path = required_string(&mut env, path)?;
        let body = optional_string(&mut env, body)?.unwrap_or_default();
        with_engine(handle, |engine| Ok(engine.request(&method, &path, &body)))
    }))
    .unwrap_or_else(|_| Err("native request handling panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeInitializePersonalDatabase(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_engine(handle, |engine| engine.initialize_personal_database())
    }))
    .unwrap_or_else(|_| Err("native personal database initialization panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativePrepareForSeal(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_engine(handle, |engine| engine.prepare_personal_database_for_seal())
    }))
    .unwrap_or_else(|_| Err("native personal database checkpoint panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_MedicineNativeCore_nativeSetReferenceAvailable(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    available: jboolean,
    reason: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let reason = optional_string(&mut env, reason)?;
        with_engine(handle, |engine| {
            engine
                .set_reference_available(available != JNI_FALSE, reason.as_deref())
                .map_err(str::to_owned)
        })
    }))
    .unwrap_or_else(|_| Err("native reference state update panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}
