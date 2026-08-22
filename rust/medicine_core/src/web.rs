use axum::{
    body::{to_bytes, Body},
    extract::State,
    http::{header, HeaderValue, Request, StatusCode},
    response::{Html, IntoResponse, Response},
    routing::{any, get},
    Json, Router,
};
use serde_json::{json, Value};
use std::{fs, path::PathBuf, sync::Arc};
use tower_http::services::ServeDir;

use crate::MedicineEngine;

pub const BROWSER_CSP: &str = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; style-src 'self'; img-src 'self' blob: data:; worker-src 'self' blob:; child-src 'self' blob:; connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'";
const MAX_API_BODY_BYTES: usize = 2 * 1024 * 1024;

#[derive(Clone, Debug)]
pub struct WebConfig {
    pub canonical_db: Option<PathBuf>,
    pub personal_db: PathBuf,
    pub static_dir: PathBuf,
    pub ocr_assets_dir: Option<PathBuf>,
    pub reference_unavailable_reason: Option<String>,
}

#[derive(Clone)]
struct AppState {
    engine: Arc<MedicineEngine>,
    index_html: Arc<String>,
}

pub fn build_router(config: WebConfig) -> Result<Router, String> {
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
    let state = AppState {
        engine: Arc::new(engine),
        index_html: Arc::new(index_html),
    };

    let mut router = Router::new()
        .route("/", get(index))
        .route("/api/{*path}", any(api_request))
        .nest_service("/static", ServeDir::new(config.static_dir));
    if let Some(ocr_root) = config.ocr_assets_dir {
        router = router.nest_service("/ocr-assets", ServeDir::new(ocr_root));
    }
    Ok(router.with_state(state))
}

async fn index(State(state): State<AppState>) -> Response {
    let mut response = Html(state.index_html.as_ref().clone()).into_response();
    response.headers_mut().insert(
        header::CONTENT_SECURITY_POLICY,
        HeaderValue::from_static(BROWSER_CSP),
    );
    response
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

    let raw = state.engine.request(&method, &raw_path, body_json);
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
