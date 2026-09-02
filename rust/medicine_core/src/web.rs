use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{header, HeaderValue, Request, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{any, get, post},
    Json, Router,
};
use serde_json::{json, Value};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, RwLock},
    time::{SystemTime, UNIX_EPOCH},
};
use tower_http::services::ServeDir;

use crate::reference_channel::ReferenceChannelRuntime;
use crate::reference_lifecycle_runtime::ReferenceRuntimeResult;
use crate::reference_manager::{ReferenceRuntimeError, ReferenceUpdateStatus};
use crate::{MedicineEngine, ReferenceBootstrapSnapshot, ReferenceBootstrapState};

pub const BROWSER_CSP: &str = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; worker-src 'self' blob:; child-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";
const MAX_API_BODY_BYTES: usize = 2 * 1024 * 1024;

pub trait WebReferenceRuntime: Send + Sync {
    fn start(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError>;
    fn status(&self) -> ReferenceBootstrapSnapshot;
    fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError>;
}

impl WebReferenceRuntime for ReferenceChannelRuntime {
    fn start(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        ReferenceChannelRuntime::start(self)
    }

    fn status(&self) -> ReferenceBootstrapSnapshot {
        ReferenceChannelRuntime::status(self)
    }

    fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        ReferenceChannelRuntime::check_for_update(self)
    }
}

#[derive(Clone)]
pub struct WebConfig {
    pub canonical_db: Option<PathBuf>,
    pub personal_db: PathBuf,
    pub static_dir: PathBuf,
    pub ocr_assets_dir: Option<PathBuf>,
    pub reference_unavailable_reason: Option<String>,
    pub reference_runtime: Option<Arc<dyn WebReferenceRuntime>>,
}

#[derive(Clone)]
struct AppState {
    engine: Arc<RwLock<MedicineEngine>>,
    index_html: Arc<String>,
    status: DevelopmentWebStatus,
    personal_db: PathBuf,
    reference_runtime: Option<Arc<dyn WebReferenceRuntime>>,
}

pub struct WebRuntime {
    pub router: Router,
    pub engine: Arc<RwLock<MedicineEngine>>,
    pub status: DevelopmentWebStatus,
}

#[derive(Clone)]
pub struct DevelopmentWebStatus {
    inner: Arc<RwLock<DevelopmentWebStatusState>>,
    reference_runtime: Option<Arc<dyn WebReferenceRuntime>>,
}

#[derive(Debug)]
struct DevelopmentWebStatusState {
    started_unix_ms: u64,
    heartbeat_unix_ms: u64,
    heartbeat_sequence: u64,
    reference_update_state: String,
    reference_update_phase: Option<String>,
    reference_update_detail: Option<String>,
}

impl DevelopmentWebStatus {
    fn new(reference_runtime: Option<Arc<dyn WebReferenceRuntime>>) -> Self {
        let now = unix_ms();
        Self {
            inner: Arc::new(RwLock::new(DevelopmentWebStatusState {
                started_unix_ms: now,
                heartbeat_unix_ms: now,
                heartbeat_sequence: 0,
                reference_update_state: "idle".to_owned(),
                reference_update_phase: None,
                reference_update_detail: None,
            })),
            reference_runtime,
        }
    }

    pub fn heartbeat(&self) {
        if let Ok(mut state) = self.inner.write() {
            state.heartbeat_sequence = state.heartbeat_sequence.saturating_add(1);
            state.heartbeat_unix_ms = unix_ms();
        }
    }

    pub fn reference_update_running(&self, phase: &str) {
        self.set_reference_update("running", Some(phase), None);
    }

    pub fn reference_update_completed(&self, state: &str) {
        self.set_reference_update(state, None, None);
    }

    pub fn reference_update_failed(&self, phase: &str, detail: &str) {
        self.set_reference_update("failed", Some(phase), Some(detail));
    }

    pub fn reference_update_disabled(&self, detail: &str) {
        self.set_reference_update("disabled", None, Some(detail));
    }

    fn set_reference_update(&self, state: &str, phase: Option<&str>, detail: Option<&str>) {
        if let Ok(mut current) = self.inner.write() {
            current.reference_update_state = state.to_owned();
            current.reference_update_phase = phase.map(str::to_owned);
            current.reference_update_detail = detail.map(str::to_owned);
        }
    }

    fn snapshot(&self) -> Result<Value, String> {
        let state = self
            .inner
            .read()
            .map_err(|_| "development status state is unavailable".to_owned())?;
        let reference_bootstrap = self
            .reference_runtime
            .as_ref()
            .map(|runtime| runtime.status())
            .unwrap_or(ReferenceBootstrapSnapshot {
                state: ReferenceBootstrapState::Ready,
                completed_bytes: 0,
                total_bytes: 0,
                detail: None,
            });
        Ok(json!({
            "process": {
                "started_unix_ms": state.started_unix_ms,
                "heartbeat_unix_ms": state.heartbeat_unix_ms,
                "heartbeat_sequence": state.heartbeat_sequence,
            },
            "reference_update": {
                "state": state.reference_update_state,
                "phase": state.reference_update_phase,
                "detail": state.reference_update_detail,
            },
            "reference_bootstrap": reference_bootstrap,
        }))
    }
}

pub fn schedule_reference_update(
    reference_runtime: Arc<dyn WebReferenceRuntime>,
    engine: Arc<RwLock<MedicineEngine>>,
    status: DevelopmentWebStatus,
) {
    status.reference_update_running("check");
    tokio::spawn(async move {
        let update_runtime = Arc::clone(&reference_runtime);
        let result = tokio::task::spawn_blocking(move || update_runtime.check_for_update()).await;
        match result {
            Err(error) => {
                let detail = format!("reference update task failed: {error}");
                status.reference_update_failed("join", &detail);
                eprintln!("{detail}");
            }
            Ok(Err(error)) => {
                let detail = format!("reference update skipped; using LKG: {error}");
                status.reference_update_failed("check", &detail);
                eprintln!("{detail}");
            }
            Ok(Ok(ReferenceUpdateStatus::NoChange)) => {
                status.reference_update_completed("no_change");
                eprintln!("reference update check completed: no change");
            }
            Ok(Ok(ReferenceUpdateStatus::Staged)) => {
                status.reference_update_completed("staged");
                eprintln!("reference update staged for next startup");
            }
            Ok(Ok(ReferenceUpdateStatus::UpdateRequired)) => match engine.write() {
                Ok(mut engine) => {
                    if let Err(error) =
                        engine.set_reference_available(false, Some("update_required"))
                    {
                        let detail =
                            format!("cannot apply reference retirement to live engine: {error}");
                        status.reference_update_failed("retire", &detail);
                        eprintln!("{detail}");
                    } else {
                        status.reference_update_completed("update_required");
                        eprintln!("reference contract retired; live safety-data access disabled");
                    }
                }
                Err(_) => {
                    status.reference_update_failed(
                        "retire",
                        "cannot lock live medicine engine after reference retirement",
                    );
                    eprintln!("cannot lock live medicine engine after reference retirement");
                }
            },
        }
    });
}

pub fn build_router(config: WebConfig) -> Result<Router, String> {
    Ok(build_runtime(config)?.router)
}

pub fn build_runtime(config: WebConfig) -> Result<WebRuntime, String> {
    let index_path = config.static_dir.join("index.html");
    let index_html = fs::read_to_string(&index_path)
        .map_err(|error| format!("cannot read static index {}: {error}", index_path.display()))?;

    if let Some(canonical_db) = config.canonical_db.as_deref() {
        if !canonical_db.is_file() {
            return Err(format!(
                "canonical database not found: {}",
                canonical_db.display()
            ));
        }
    }

    if let Some(ocr_root) = config.ocr_assets_dir.as_deref() {
        for required in [
            ocr_root.join("runtime-manifest.json"),
            ocr_root.join("direct/ocr-worker.js"),
        ] {
            if !required.is_file() {
                return Err(format!(
                    "OCR runtime assets are incomplete: {}",
                    required.display()
                ));
            }
        }
    }

    let engine = MedicineEngine::new(
        config.canonical_db.as_deref(),
        Some(config.personal_db.as_path()),
        config.reference_unavailable_reason.as_deref(),
    );
    engine.initialize_personal_database()?;
    let engine = Arc::new(RwLock::new(engine));
    let status = DevelopmentWebStatus::new(config.reference_runtime.clone());
    let state = AppState {
        engine: Arc::clone(&engine),
        index_html: Arc::new(index_html),
        status: status.clone(),
        personal_db: config.personal_db.clone(),
        reference_runtime: config.reference_runtime.clone(),
    };

    let mut router = Router::new()
        .route("/", get(index))
        .route("/api/development/status", get(development_status))
        .route(
            "/api/development/reference-bootstrap/start",
            post(start_reference_bootstrap),
        )
        .route("/api/{*path}", any(api_request))
        .nest_service("/static", ServeDir::new(config.static_dir));
    if let Some(ocr_root) = config.ocr_assets_dir {
        router = router.nest_service("/ocr-assets", ServeDir::new(ocr_root));
    }
    Ok(WebRuntime {
        router: router.with_state(state),
        engine,
        status,
    })
}

async fn index(State(state): State<AppState>) -> Response {
    let mut response = Html(state.index_html.as_ref().clone()).into_response();
    response.headers_mut().insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static(BROWSER_CSP),
    );
    response
}

async fn development_status(State(state): State<AppState>) -> Response {
    let mut payload = match state.status.snapshot() {
        Ok(value) => value,
        Err(detail) => {
            return json_response(StatusCode::INTERNAL_SERVER_ERROR, json!({"detail": detail}));
        }
    };
    let health = match state.engine.read() {
        Ok(engine) => engine.request("GET", "/api/health", ""),
        Err(_) => {
            return json_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"detail": "medicine engine state is unavailable"}),
            );
        }
    };
    let health: Value = match serde_json::from_str(&health) {
        Ok(value) => value,
        Err(_) => {
            return json_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"detail": "invalid engine health response"}),
            );
        }
    };
    let body = health.get("body").cloned().unwrap_or(Value::Null);
    payload["reference"] = json!({
        "available": body
            .get("reference_available")
            .cloned()
            .unwrap_or(Value::Null),
        "status": body
            .get("reference_status")
            .cloned()
            .unwrap_or(Value::Null),
        "full_catalog": body.get("full_catalog").cloned().unwrap_or(Value::Null),
    });
    json_response(StatusCode::OK, payload)
}

async fn start_reference_bootstrap(State(state): State<AppState>) -> Response {
    let Some(reference_runtime) = state.reference_runtime.clone() else {
        return json_response(
            StatusCode::CONFLICT,
            json!({"detail": "reference bootstrap is not required"}),
        );
    };
    match reference_runtime.status().state {
        ReferenceBootstrapState::Downloading | ReferenceBootstrapState::Installing => {
            return json_response(StatusCode::ACCEPTED, json!({"status": "already_running"}));
        }
        ReferenceBootstrapState::DownloadRequired | ReferenceBootstrapState::Failed => {}
        _ => {
            return json_response(
                StatusCode::CONFLICT,
                json!({"detail": "reference bootstrap is not required"}),
            );
        }
    }

    let engine = Arc::clone(&state.engine);
    let update_runtime = Arc::clone(&reference_runtime);
    let update_status = state.status.clone();
    let personal_db = state.personal_db.clone();
    tokio::spawn(async move {
        match tokio::task::spawn_blocking(move || reference_runtime.start()).await {
            Err(error) => {
                eprintln!("reference bootstrap task failed: {error}");
            }
            Ok(Err(error)) => eprintln!("reference bootstrap failed: {error}"),
            Ok(Ok(result)) => {
                let Some(selection) = result.selection else {
                    return;
                };
                let Some(database) = selection.database else {
                    if let Ok(mut current) = engine.write() {
                        let _ = current.set_reference_available(
                            false,
                            selection.unavailable_reason.as_deref(),
                        );
                    }
                    return;
                };
                let replacement = MedicineEngine::new(
                    Some(database.as_path()),
                    Some(personal_db.as_path()),
                    None,
                );
                if let Err(error) = replacement.initialize_personal_database() {
                    eprintln!("reference bootstrap engine replacement failed: {error}");
                    return;
                }
                if let Ok(mut current) = engine.write() {
                    *current = replacement;
                }
                schedule_reference_update(update_runtime, Arc::clone(&engine), update_status);
            }
        }
    });

    json_response(StatusCode::ACCEPTED, json!({"status": "started"}))
}

async fn api_request(State(state): State<AppState>, request: Request<Body>) -> Response {
    let method = request.method().as_str().to_owned();
    let raw_path = request
        .uri()
        .path_and_query()
        .map(|value| value.as_str())
        .unwrap_or_else(|| request.uri().path())
        .to_owned();
    let body = match to_bytes(request.into_body(), MAX_API_BODY_BYTES).await {
        Ok(body) => body,
        Err(_) => {
            return json_response(
                StatusCode::PAYLOAD_TOO_LARGE,
                json!({"detail": "request body is too large"}),
            );
        }
    };
    let body_json = match std::str::from_utf8(&body) {
        Ok(body) => body,
        Err(_) => {
            return json_response(
                StatusCode::BAD_REQUEST,
                json!({"detail": "request body must be UTF-8"}),
            );
        }
    };

    let raw = match state.engine.read() {
        Ok(engine) => engine.request(&method, &raw_path, body_json),
        Err(_) => {
            return json_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"detail": "medicine engine state is unavailable"}),
            );
        }
    };
    let envelope: Value = match serde_json::from_str(&raw) {
        Ok(value) => value,
        Err(_) => {
            return json_response(
                StatusCode::INTERNAL_SERVER_ERROR,
                json!({"detail": "invalid engine response"}),
            );
        }
    };
    let status = envelope
        .get("status")
        .and_then(Value::as_u64)
        .and_then(|status| u16::try_from(status).ok())
        .and_then(|status| StatusCode::from_u16(status).ok())
        .unwrap_or(StatusCode::INTERNAL_SERVER_ERROR);
    let body = envelope
        .get("body")
        .cloned()
        .unwrap_or_else(|| json!({"detail": "invalid engine response"}));
    json_response(status, body)
}

fn json_response(status: StatusCode, body: Value) -> Response {
    let mut response = Json(body).into_response();
    *response.status_mut() = status;
    response
}

fn unix_ms() -> u64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .ok()
        .and_then(|duration| u64::try_from(duration.as_millis()).ok())
        .unwrap_or(u64::MAX)
}
