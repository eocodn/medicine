#![cfg(feature = "web")]

mod common;

use axum::{
    body::{to_bytes, Body},
    http::{header, Request, StatusCode},
};
use medicine_core::web::{
    build_router, build_runtime, WebConfig, WebReferenceRuntime, BROWSER_CSP,
};
use medicine_core::{
    ReferenceBootstrapSnapshot, ReferenceBootstrapState, ReferenceRuntimeError,
    ReferenceRuntimeResult, ReferenceSelection, ReferenceUpdateStatus,
};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::{
    fs,
    path::PathBuf,
    sync::{Arc, Mutex},
};
use tokio::sync::Notify;
use tower::ServiceExt;

struct FakeWebReferenceRuntime {
    snapshot: Mutex<ReferenceBootstrapSnapshot>,
    start_called: Notify,
    update_called: Notify,
    database: PathBuf,
}

impl FakeWebReferenceRuntime {
    fn download_required(completed_bytes: u64, total_bytes: u64, database: PathBuf) -> Self {
        Self {
            snapshot: Mutex::new(ReferenceBootstrapSnapshot {
                state: ReferenceBootstrapState::DownloadRequired,
                completed_bytes,
                total_bytes,
                detail: None,
            }),
            start_called: Notify::new(),
            update_called: Notify::new(),
            database,
        }
    }
}

impl WebReferenceRuntime for FakeWebReferenceRuntime {
    fn start(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        let mut snapshot = self
            .snapshot
            .lock()
            .map_err(|_| ReferenceRuntimeError::from_message("fake runtime lock poisoned"))?;
        snapshot.state = ReferenceBootstrapState::Ready;
        snapshot.completed_bytes = snapshot.total_bytes;
        let snapshot = snapshot.clone();
        self.start_called.notify_one();
        Ok(ReferenceRuntimeResult {
            selection: Some(ReferenceSelection {
                database: Some(self.database.clone()),
                unavailable_reason: None,
            }),
            snapshot,
        })
    }

    fn status(&self) -> ReferenceBootstrapSnapshot {
        self.snapshot.lock().unwrap().clone()
    }

    fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        self.update_called.notify_one();
        Ok(ReferenceUpdateStatus::NoChange)
    }
}

fn temp_dir(label: &str) -> PathBuf {
    let path = common::temp_sqlite_path(label);
    fs::remove_file(&path).expect("release reserved path");
    fs::create_dir_all(&path).expect("create temp directory");
    path
}

fn reference_db(label: &str) -> PathBuf {
    let path = common::temp_sqlite_path(label);
    let con = Connection::open(&path).expect("open reference fixture");
    con.execute_batch(
        "CREATE TABLE products(
             item_seq TEXT PRIMARY KEY,
             product_name TEXT NOT NULL,
             manufacturer TEXT,
             ingredient_text TEXT,
             dosage_form TEXT,
             permit_date TEXT,
             cancel_date TEXT,
             cancel_name TEXT,
             permit_status TEXT NOT NULL
         );
         CREATE TABLE product_search_documents(
             item_seq TEXT PRIMARY KEY,
             normalized_product_name TEXT NOT NULL,
             normalized_manufacturer TEXT NOT NULL,
             normalized_ingredient_names TEXT NOT NULL
         );
         CREATE VIRTUAL TABLE product_search_fts USING fts5(
             searchable_text, tokenize='trigram', content=''
         );
         CREATE TABLE product_rules(
             id INTEGER PRIMARY KEY,
             item_seq TEXT NOT NULL,
             category TEXT NOT NULL,
             effect_name TEXT
         );
         CREATE TABLE product_criterion_links(
             product_rule_id INTEGER NOT NULL,
             criterion_rule_id INTEGER NOT NULL
         );
         INSERT INTO products VALUES(
             'fixture','Fixture medicine','Fixture manufacturer','fixture','tablet',
             '2020-01-01',NULL,NULL,'active'
         );
         INSERT INTO product_search_documents VALUES(
             'fixture','fixture medicine','fixture manufacturer',char(10)||'fixture'||char(10)
         );
         INSERT INTO product_search_fts(rowid,searchable_text)
         SELECT rowid,normalized_product_name||char(10)||normalized_manufacturer||normalized_ingredient_names
         FROM product_search_documents;",
    )
    .expect("create products fixture");
    drop(con);
    path
}

async fn response_json(response: axum::response::Response) -> Value {
    let body = to_bytes(response.into_body(), 1024 * 1024)
        .await
        .expect("read response body");
    serde_json::from_slice(&body).expect("decode response JSON")
}

fn fixture_config(reference: Option<PathBuf>, reason: Option<&str>) -> (WebConfig, PathBuf) {
    let root = temp_dir("web-server-root");
    let static_dir = root.join("static");
    let ocr_dir = root.join("ocr");
    fs::create_dir_all(&static_dir).expect("create static directory");
    fs::create_dir_all(ocr_dir.join("direct")).expect("create OCR directory");
    fs::write(static_dir.join("index.html"), "<html>medicine shell</html>").expect("write index");
    fs::write(static_dir.join("app.js"), "window.fixture = true;").expect("write static asset");
    fs::write(ocr_dir.join("runtime-manifest.json"), "{}\n").expect("write OCR manifest");
    fs::write(
        ocr_dir.join("direct/ocr-worker.js"),
        "self.fixture = true;\n",
    )
    .expect("write OCR worker");
    let personal_db = root.join("personal.sqlite");
    (
        WebConfig {
            canonical_db: reference,
            personal_db,
            static_dir,
            ocr_assets_dir: Some(ocr_dir),
            reference_unavailable_reason: reason.map(str::to_owned),
            reference_runtime: None,
            enable_agent_control: false,
        },
        root,
    )
}

#[cfg(not(feature = "agentctl-web"))]
#[test]
fn plain_web_usage_does_not_advertise_agent_control() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_medicine-core-web"))
        .arg("--agent-control")
        .output()
        .expect("run plain web usage");
    assert!(!output.status.success());
    let stderr = String::from_utf8_lossy(&output.stderr);
    assert!(!stderr.contains("[--agent-control]"), "{stderr}");
}

#[tokio::test]
async fn local_http_adapter_serves_ui_and_routes_api_through_medicine_engine() {
    let reference = reference_db("web-server-reference");
    let (config, root) = fixture_config(Some(reference.clone()), None);
    let personal_db = config.personal_db.clone();
    let app = build_router(config).expect("build Rust local web router");

    let root_response = app
        .clone()
        .oneshot(Request::builder().uri("/").body(Body::empty()).unwrap())
        .await
        .expect("root response");
    assert_eq!(root_response.status(), StatusCode::OK);
    assert_eq!(
        root_response.headers()[header::CONTENT_SECURITY_POLICY],
        BROWSER_CSP
    );
    let root_body = to_bytes(root_response.into_body(), 1024 * 1024)
        .await
        .expect("root body");
    assert_eq!(root_body.as_ref(), b"<html>medicine shell</html>");

    let static_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/static/app.js")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("static response");
    assert_eq!(static_response.status(), StatusCode::OK);
    assert_eq!(
        static_response.headers()[header::CONTENT_TYPE],
        "text/javascript"
    );

    let ocr_response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/ocr-assets/runtime-manifest.json")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("OCR response");
    assert_eq!(ocr_response.status(), StatusCode::OK);

    let health = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("health response");
    assert_eq!(health.status(), StatusCode::OK);
    assert_eq!(
        response_json(health).await,
        json!({
            "ok": true,
            "full_catalog": true,
            "reference_available": true,
            "reference_status": null
        })
    );

    let create = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/people")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    json!({
                        "name": "웹 사용자",
                        "birth_date": "1990-01-01",
                        "sex": "male",
                        "pregnancy_status": "not_applicable",
                        "lactation_status": "not_applicable"
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .expect("create person response");
    assert_eq!(create.status(), StatusCode::CREATED);
    assert_eq!(response_json(create).await["name"], "웹 사용자");

    let people = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/people")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("people response");
    assert_eq!(people.status(), StatusCode::OK);
    assert_eq!(
        response_json(people)
            .await
            .as_array()
            .expect("people array")
            .len(),
        1
    );

    for path in [
        "/api/people/example/medications/ocr-preview",
        "/api/people/example/medications/batch-preview",
        "/api/people/example/medications/batch",
    ] {
        let response = app
            .clone()
            .oneshot(
                Request::builder()
                    .method("POST")
                    .uri(path)
                    .header(header::CONTENT_TYPE, "application/json")
                    .body(Body::from("{}"))
                    .unwrap(),
            )
            .await
            .expect("unsupported ingestion response");
        assert_eq!(response.status(), StatusCode::NOT_FOUND, "{path}");
        assert_eq!(response_json(response).await["detail"], "route not found");
    }

    let search = app
        .oneshot(
            Request::builder()
                .uri("/api/products?q=fixture")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("product response");
    assert_eq!(search.status(), StatusCode::OK);
    let search_body = response_json(search).await;
    assert_eq!(search_body["items"][0]["product_ref"], "fixture");
    assert_eq!(search_body["has_more"], false);

    let con = Connection::open(&personal_db).expect("open initialized personal DB");
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("personal schema version"),
        medicine_core::PERSONAL_SCHEMA_VERSION
    );

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}

#[cfg(feature = "agentctl-web")]
#[test]
fn agent_control_requires_loopback_web_host() {
    let output = std::process::Command::new(env!("CARGO_BIN_EXE_medicine-core-web"))
        .args(["--host", "0.0.0.0", "--agent-control"])
        .output()
        .expect("run agent-controlled web with non-loopback host");
    assert!(!output.status.success());
    assert!(String::from_utf8_lossy(&output.stderr).contains("requires a loopback web host"));
}

#[cfg(feature = "agentctl-web")]
#[tokio::test]
async fn agent_fault_controller_fails_one_matching_request_then_restores_authoritative_api() {
    let reference = reference_db("web-agent-fault-reference");
    let (mut config, root) = fixture_config(Some(reference.clone()), None);
    config.enable_agent_control = true;
    let app = build_router(config).expect("build agent-controlled web router");

    let install = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/development/agent/faults")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    json!({
                        "id": "health-once",
                        "method": "GET",
                        "path": "/api/health",
                        "action": "fail",
                        "times": 1,
                        "status": 503,
                        "body": {"detail": "injected"}
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .expect("install fault");
    assert_eq!(install.status(), StatusCode::CREATED);

    let failed = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("faulted health response");
    assert_eq!(failed.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(response_json(failed).await["detail"], "injected");

    let recovered = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("recovered health response");
    assert_eq!(recovered.status(), StatusCode::OK);

    let status = app
        .oneshot(
            Request::builder()
                .uri("/api/development/agent/faults/health-once")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("fault status");
    let status = response_json(status).await;
    assert_eq!(status["remaining"], 0);
    assert_eq!(status["matched"], 1);

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}

#[cfg(feature = "agentctl-web")]
#[tokio::test]
async fn agent_fault_gate_waits_for_explicit_release() {
    let reference = reference_db("web-agent-gate-reference");
    let (mut config, root) = fixture_config(Some(reference.clone()), None);
    config.enable_agent_control = true;
    let app = build_router(config).expect("build agent-controlled web router");

    let install = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/development/agent/faults")
                .header(header::CONTENT_TYPE, "application/json")
                .body(Body::from(
                    json!({
                        "id": "health-gate",
                        "method": "GET",
                        "path": "/api/health",
                        "action": "gate",
                        "times": 1
                    })
                    .to_string(),
                ))
                .unwrap(),
        )
        .await
        .expect("install gate");
    assert_eq!(install.status(), StatusCode::CREATED);

    let request_app = app.clone();
    let pending = tokio::spawn(async move {
        request_app
            .oneshot(
                Request::builder()
                    .uri("/api/health")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .expect("gated health response")
    });

    tokio::time::timeout(std::time::Duration::from_secs(2), async {
        loop {
            let status = app
                .clone()
                .oneshot(
                    Request::builder()
                        .uri("/api/development/agent/faults/health-gate")
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .expect("gate status");
            let status = response_json(status).await;
            if status["waiting"] == 1 {
                break;
            }
            tokio::task::yield_now().await;
        }
    })
    .await
    .expect("gate reached waiting state");

    assert!(!pending.is_finished());
    let release = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/development/agent/faults/health-gate/release")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("release gate");
    assert_eq!(release.status(), StatusCode::OK);
    assert_eq!(
        pending.await.expect("join gated request").status(),
        StatusCode::OK
    );

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}

#[tokio::test]
async fn runtime_handle_can_disable_reference_after_listener_startup() {
    let reference = reference_db("web-server-live-retirement");
    let (config, root) = fixture_config(Some(reference.clone()), None);
    let runtime = build_runtime(config).expect("build mutable Rust web runtime");
    let app = runtime.router.clone();

    runtime
        .engine
        .write()
        .expect("lock medicine engine")
        .set_reference_available(false, Some("update_required"))
        .expect("disable retired reference");

    let health = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("health response after retirement");
    let health_body = response_json(health).await;
    assert_eq!(health_body["reference_available"], false);
    assert_eq!(health_body["reference_status"], "update_required");

    let products = app
        .oneshot(
            Request::builder()
                .uri("/api/products?q=fixture")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("product response after retirement");
    assert_eq!(products.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        response_json(products).await["reference_status"],
        "update_required"
    );

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}

#[tokio::test]
async fn local_http_adapter_exposes_reference_unavailable_state_without_python_fallback() {
    let (config, root) = fixture_config(None, Some("retired"));
    let app = build_router(config).expect("build unavailable-reference router");

    let health = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/health")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("health response");
    assert_eq!(health.status(), StatusCode::OK);
    assert_eq!(response_json(health).await["reference_status"], "retired");

    let products = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/products?q=fixture")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("products response");
    assert_eq!(products.status(), StatusCode::SERVICE_UNAVAILABLE);
    let products_body = response_json(products).await;
    assert_eq!(
        products_body["detail"],
        "reference data unavailable; app update required"
    );
    assert_eq!(products_body["reference_status"], "retired");

    let unknown = app
        .oneshot(
            Request::builder()
                .uri("/api/not-a-route")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("unknown route response");
    assert_eq!(unknown.status(), StatusCode::NOT_FOUND);
    assert_eq!(response_json(unknown).await["detail"], "route not found");

    fs::remove_dir_all(root).ok();
}

#[tokio::test]
async fn development_status_exposes_heartbeat_reference_phase_and_authoritative_engine_state() {
    let reference = reference_db("web-server-observability");
    let (config, root) = fixture_config(Some(reference.clone()), None);
    let runtime = build_runtime(config).expect("build observable Rust web runtime");
    let app = runtime.router.clone();

    let initial = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/development/status")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("initial development status response");
    assert_eq!(initial.status(), StatusCode::OK);
    let initial = response_json(initial).await;
    assert_eq!(initial["process"]["heartbeat_sequence"], 0);
    assert!(initial["process"]["started_unix_ms"].as_u64().is_some());
    assert!(initial["process"]["heartbeat_unix_ms"].as_u64().is_some());
    assert_eq!(initial["reference_update"]["state"], "idle");
    assert_eq!(initial["reference"]["available"], true);
    assert_eq!(initial["reference"]["status"], Value::Null);

    runtime.status.heartbeat();
    runtime.status.reference_update_running("check");
    runtime
        .status
        .reference_update_failed("check", "simulated update failure");
    runtime
        .engine
        .write()
        .expect("lock medicine engine")
        .set_reference_available(false, Some("simulated_unavailable"))
        .expect("disable reference for observability test");

    let changed = app
        .oneshot(
            Request::builder()
                .uri("/api/development/status")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("changed development status response");
    assert_eq!(changed.status(), StatusCode::OK);
    let changed = response_json(changed).await;
    assert_eq!(changed["process"]["heartbeat_sequence"], 1);
    assert_eq!(changed["reference_update"]["state"], "failed");
    assert_eq!(changed["reference_update"]["phase"], "check");
    assert_eq!(
        changed["reference_update"]["detail"],
        "simulated update failure"
    );
    assert_eq!(changed["reference"]["available"], false);
    assert_eq!(changed["reference"]["status"], "simulated_unavailable");

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}

#[tokio::test]
async fn development_status_exposes_shared_reference_bootstrap_contract() {
    let (mut config, root) = fixture_config(None, Some("bootstrap_required"));
    let reference = reference_db("web-server-bootstrap-reference");
    let reference_runtime = Arc::new(FakeWebReferenceRuntime::download_required(
        25,
        100,
        reference.clone(),
    ));
    config.reference_runtime = Some(reference_runtime.clone());
    let app = build_router(config).expect("build bootstrap-required router");

    let response = app
        .clone()
        .oneshot(
            Request::builder()
                .uri("/api/development/status")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("bootstrap development status response");
    assert_eq!(response.status(), StatusCode::OK);
    let body = response_json(response).await;
    assert_eq!(body["reference"]["available"], false);
    assert_eq!(body["reference"]["status"], "bootstrap_required");
    assert_eq!(body["reference_bootstrap"]["state"], "download_required");
    assert_eq!(body["reference_bootstrap"]["completed_bytes"], 25);
    assert_eq!(body["reference_bootstrap"]["total_bytes"], 100);

    let started = reference_runtime.start_called.notified();
    let update_checked = reference_runtime.update_called.notified();
    let start_response = app
        .clone()
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/api/development/reference-bootstrap/start")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("bootstrap start response");
    assert_eq!(start_response.status(), StatusCode::ACCEPTED);
    started.await;
    update_checked.await;

    let mut ready = Value::Null;
    for _ in 0..100 {
        let ready_response = app
            .clone()
            .oneshot(
                Request::builder()
                    .uri("/api/development/status")
                    .body(Body::empty())
                    .unwrap(),
            )
            .await
            .expect("ready bootstrap development status response");
        ready = response_json(ready_response).await;
        if ready["reference_update"]["state"] == "no_change" {
            break;
        }
        tokio::task::yield_now().await;
    }
    assert_eq!(ready["reference_bootstrap"]["state"], "ready");
    assert_eq!(ready["reference_bootstrap"]["completed_bytes"], 100);
    assert_eq!(ready["reference"]["available"], true);
    assert_eq!(ready["reference_update"]["state"], "no_change");

    fs::remove_file(reference).ok();
    fs::remove_dir_all(root).ok();
}
