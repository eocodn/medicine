use super::{required_string, string_result, throw};
use crate::parse_reference_trust_manifest_json;
use crate::reference_lifecycle_runtime::ReferenceRuntimeResult;
use crate::reference_runtime::ReferenceRuntime;
use jni::objects::{JObject, JString};
use jni::sys::{jlong, jstring};
use jni::JNIEnv;
use std::collections::HashMap;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Arc, Mutex, OnceLock};

type AndroidReferenceRuntime = ReferenceRuntime;

static NEXT_HANDLE: AtomicI64 = AtomicI64::new(1);
static RUNTIMES: OnceLock<Mutex<HashMap<i64, Arc<AndroidReferenceRuntime>>>> = OnceLock::new();

fn runtimes() -> &'static Mutex<HashMap<i64, Arc<AndroidReferenceRuntime>>> {
    RUNTIMES.get_or_init(|| Mutex::new(HashMap::new()))
}

fn runtime(handle: jlong) -> Result<Arc<AndroidReferenceRuntime>, String> {
    if handle <= 0 {
        return Err("native reference runtime is closed".to_owned());
    }
    runtimes()
        .lock()
        .map_err(|_| "native reference runtime registry is unavailable".to_owned())?
        .get(&handle)
        .cloned()
        .ok_or_else(|| "native reference runtime is closed".to_owned())
}

fn result_json(selection: Option<crate::ReferenceSelection>, error: Option<&str>) -> String {
    let selection = selection.map(|selection| {
        serde_json::json!({
            "database_path": selection
                .database
                .map(|path| path.to_string_lossy().into_owned()),
            "unavailable_reason": selection.unavailable_reason,
        })
    });
    serde_json::json!({
        "selection": selection,
        "error": error,
    })
    .to_string()
}

fn operation_json(
    runtime: &AndroidReferenceRuntime,
    operation: impl FnOnce(
        &AndroidReferenceRuntime,
    ) -> Result<
        ReferenceRuntimeResult,
        crate::reference_manager::ReferenceRuntimeError,
    >,
) -> String {
    match operation(runtime) {
        Ok(result) => result_json(result.selection, None),
        Err(error) => result_json(None, Some(&error.to_string())),
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeCreateReferenceRuntime(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    reference_dir: JString<'_>,
    base_url: JString<'_>,
    trust_manifest_json: JString<'_>,
) -> jlong {
    match catch_unwind(AssertUnwindSafe(|| {
        let reference_dir = required_string(&mut env, reference_dir)?;
        let base_url = required_string(&mut env, base_url)?;
        let trust_manifest_json = required_string(&mut env, trust_manifest_json)?;
        let trust = parse_reference_trust_manifest_json(&trust_manifest_json)
            .map_err(|error| error.to_string())?;
        let runtime = Arc::new(
            ReferenceRuntime::new(reference_dir.into(), &base_url, trust)
                .map_err(|error| error.to_string())?,
        );
        let handle = NEXT_HANDLE.fetch_add(1, Ordering::Relaxed);
        if handle <= 0 {
            return Err("native reference runtime handle space exhausted".to_owned());
        }
        runtimes()
            .lock()
            .map_err(|_| "native reference runtime registry is unavailable".to_owned())?
            .insert(handle, runtime);
        Ok::<_, String>(handle)
    })) {
        Ok(Ok(handle)) => handle,
        Ok(Err(error)) => {
            throw(&mut env, &error);
            0
        }
        Err(_) => {
            throw(&mut env, "native reference runtime initialization panicked");
            0
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeDestroyReferenceRuntime(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        runtimes()
            .lock()
            .map_err(|_| "native reference runtime registry is unavailable".to_owned())?
            .remove(&handle);
        Ok::<_, String>(())
    }))
    .unwrap_or_else(|_| Err("native reference runtime shutdown panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeReferencePrepare(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let runtime = runtime(handle)?;
        Ok(operation_json(&runtime, AndroidReferenceRuntime::prepare))
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap prepare panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeReferenceStart(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let runtime = runtime(handle)?;
        Ok(operation_json(&runtime, AndroidReferenceRuntime::start))
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap install panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeReferenceStatus(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let runtime = runtime(handle)?;
        serde_json::to_string(&runtime.status())
            .map_err(|error| format!("cannot encode reference runtime status: {error}"))
    }))
    .unwrap_or_else(|_| Err("native reference runtime status panicked".to_owned()));
    string_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeReferenceCheckForUpdate(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let runtime = runtime(handle)?;
        let value = match runtime.check_for_update() {
            Ok(status) => serde_json::json!({"status": status.as_str()}),
            Err(error) => serde_json::json!({
                "status": "failed",
                "detail": error.to_string(),
            }),
        };
        Ok(value.to_string())
    }))
    .unwrap_or_else(|_| Err("native reference update check panicked".to_owned()));
    string_result(&mut env, result)
}
