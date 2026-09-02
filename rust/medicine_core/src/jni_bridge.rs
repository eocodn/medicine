use crate::MedicineEngine;
use jni::objects::{JObject, JString};
use jni::sys::{jboolean, jlong, jstring, JNI_FALSE};
use jni::JNIEnv;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::path::Path;
use std::ptr;
use std::sync::Mutex;

mod reference_runtime;

type EngineHandle = Mutex<MedicineEngine>;

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
