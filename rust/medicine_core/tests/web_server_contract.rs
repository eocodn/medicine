#![cfg(feature = "web")]

mod common;

use axum::{
    body::{to_bytes, Body},
    http::{header, Request, StatusCode},
};
use medicine_core::web::{build_router, WebConfig, BROWSER_CSP};
use rusqlite::Connection;
use serde_json::{json, Value};
use std::{fs, path::PathBuf};
use tower::ServiceExt;

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
        "CREATE TABLE products(item_seq TEXT PRIMARY KEY);\n\
         INSERT INTO products(item_seq) VALUES('fixture');",
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
        },
        root,
    )
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

    let unavailable_search = app
        .oneshot(
            Request::builder()
                .uri("/api/products?q=fixture")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .expect("product response");
    assert_eq!(unavailable_search.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_eq!(
        response_json(unavailable_search).await["detail"],
        "product search engine is not implemented"
    );

    let con = Connection::open(&personal_db).expect("open initialized personal DB");
    assert_eq!(
        con.query_row("PRAGMA user_version", [], |row| row.get::<_, i64>(0))
            .expect("personal schema version"),
        medicine_core::PERSONAL_SCHEMA_VERSION
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
