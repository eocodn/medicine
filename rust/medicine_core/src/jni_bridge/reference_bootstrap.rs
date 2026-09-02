use crate::ReferenceBootstrapCoordinator;
use jni::objects::{JObject, JString};
use jni::sys::{jboolean, jlong, jstring, JNI_FALSE, JNI_TRUE};
use jni::JNIEnv;
use std::collections::HashMap;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::sync::atomic::{AtomicI64, Ordering};
use std::sync::{Mutex, OnceLock};

static NEXT_HANDLE: AtomicI64 = AtomicI64::new(1);
static BOOTSTRAPS: OnceLock<Mutex<HashMap<i64, ReferenceBootstrapCoordinator>>> = OnceLock::new();

fn bootstraps() -> &'static Mutex<HashMap<i64, ReferenceBootstrapCoordinator>> {
    BOOTSTRAPS.get_or_init(|| Mutex::new(HashMap::new()))
}

fn with_bootstrap<R>(
    handle: jlong,
    operation: impl FnOnce(&mut ReferenceBootstrapCoordinator) -> Result<R, String>,
) -> Result<R, String> {
    if handle == 0 {
        return Err("native reference bootstrap coordinator is closed".to_owned());
    }
    let mut coordinators = bootstraps()
        .lock()
        .map_err(|_| "native reference bootstrap coordinator registry is poisoned".to_owned())?;
    let coordinator = coordinators
        .get_mut(&handle)
        .ok_or_else(|| "native reference bootstrap coordinator is closed".to_owned())?;
    operation(coordinator)
}

fn required_string(env: &mut JNIEnv<'_>, value: JString<'_>) -> Result<String, String> {
    if value.is_null() {
        return Err("required Java string was null".to_owned());
    }
    env.get_string(&value)
        .map(Into::into)
        .map_err(|error| format!("cannot read Java string: {error}"))
}

fn throw(env: &mut JNIEnv<'_>, message: &str) {
    let _ = env.throw_new("java/lang/IllegalStateException", message);
}

fn bool_result(env: &mut JNIEnv<'_>, result: Result<bool, String>) -> jboolean {
    match result {
        Ok(true) => JNI_TRUE,
        Ok(false) => JNI_FALSE,
        Err(error) => {
            throw(env, &error);
            JNI_FALSE
        }
    }
}

fn string_result(env: &mut JNIEnv<'_>, result: Result<String, String>) -> jstring {
    match result {
        Ok(value) => env
            .new_string(value)
            .map(|value| value.into_raw())
            .unwrap_or(ptr::null_mut()),
        Err(error) => {
            throw(env, &error);
            ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeCreateBootstrapCoordinator(
    _env: JNIEnv<'_>,
    _this: JObject<'_>,
) -> jlong {
    let handle = NEXT_HANDLE.fetch_add(1, Ordering::Relaxed);
    if let Ok(mut coordinators) = bootstraps().lock() {
        coordinators.insert(handle, ReferenceBootstrapCoordinator::checking());
        handle
    } else {
        0
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeDestroyBootstrapCoordinator(
    _env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    if let Ok(mut coordinators) = bootstraps().lock() {
        coordinators.remove(&handle);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapBeginPrepare(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jboolean {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_bootstrap(handle, |coordinator| Ok(coordinator.begin_prepare()))
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap prepare transition panicked".to_owned()));
    bool_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapResetForPrepare(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_bootstrap(handle, |coordinator| {
            coordinator.reset_for_prepare();
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap reset transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapPreparedDownload(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    completed_bytes: jlong,
    total_bytes: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let completed = u64::try_from(completed_bytes)
            .map_err(|_| "reference bootstrap completed bytes must be non-negative".to_owned())?;
        let total = u64::try_from(total_bytes)
            .map_err(|_| "reference bootstrap total bytes must be non-negative".to_owned())?;
        with_bootstrap(handle, |coordinator| {
            coordinator.prepared_download(completed, total);
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap prepared transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapBeginInstall(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jboolean {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_bootstrap(handle, |coordinator| Ok(coordinator.begin_install()))
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap install transition panicked".to_owned()));
    bool_result(&mut env, result)
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapPhase(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    phase: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let phase = required_string(&mut env, phase)?;
        with_bootstrap(handle, |coordinator| {
            match phase.as_str() {
                "manifest" => coordinator.checking_phase(),
                "full-download" => {}
                "rebuild" | "rebuild-checkpoint" | "verify-and-install" => {
                    coordinator.installing()
                }
                "ready" => coordinator.ready(),
                _ => return Err("unknown reference bootstrap phase".to_owned()),
            }
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap phase transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapProgress(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    completed_bytes: jlong,
    total_bytes: jlong,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let completed = u64::try_from(completed_bytes)
            .map_err(|_| "reference bootstrap completed bytes must be non-negative".to_owned())?;
        let total = u64::try_from(total_bytes)
            .map_err(|_| "reference bootstrap total bytes must be non-negative".to_owned())?;
        with_bootstrap(handle, |coordinator| {
            coordinator.progress(completed, total);
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap progress transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapReady(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) {
    let result = with_bootstrap(handle, |coordinator| {
        coordinator.ready();
        Ok(())
    });
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapUnavailable(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    detail: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let detail = required_string(&mut env, detail)?;
        with_bootstrap(handle, |coordinator| {
            coordinator.unavailable(Some(&detail));
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap unavailable transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapFailed(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
    detail: JString<'_>,
) {
    let result = catch_unwind(AssertUnwindSafe(|| {
        let detail = required_string(&mut env, detail)?;
        with_bootstrap(handle, |coordinator| {
            coordinator.failed(&detail);
            Ok(())
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap failure transition panicked".to_owned()));
    if let Err(error) = result {
        throw(&mut env, &error);
    }
}

#[no_mangle]
pub extern "system" fn Java_com_medicine_android_ReferenceNativeCore_nativeBootstrapSnapshot(
    mut env: JNIEnv<'_>,
    _this: JObject<'_>,
    handle: jlong,
) -> jstring {
    let result = catch_unwind(AssertUnwindSafe(|| {
        with_bootstrap(handle, |coordinator| {
            serde_json::to_string(&coordinator.snapshot())
                .map_err(|error| format!("cannot serialize reference bootstrap state: {error}"))
        })
    }))
    .unwrap_or_else(|_| Err("native reference bootstrap snapshot panicked".to_owned()));
    string_result(&mut env, result)
}