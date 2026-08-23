use medicine_core::development_reference::{
    ensure_development_reference, DevelopmentReferenceConfig,
};
use medicine_core::web::{build_router, WebConfig};
use std::{env, net::IpAddr, path::PathBuf};
use tokio::net::TcpListener;

const DEFAULT_PERSONAL_DB: &str = "data/db/personal.sqlite";
const DEFAULT_REFERENCE_DIR: &str = "data/reference";
const DEFAULT_REFERENCE_TRUST_MANIFEST: &str = "deploy/reference-signing-trusted-keys.json";
const DEFAULT_STATIC_DIR: &str = "medicine_app/static";
const REFERENCE_CONTRACT_MAJOR: i32 = 1;

#[tokio::main]
async fn main() {
    if let Err(error) = run(env::args().skip(1).collect()).await {
        eprintln!("{error}");
        std::process::exit(2);
    }
}

async fn run(args: Vec<String>) -> Result<(), String> {
    let mut host = env::var("MEDICINE_WEB_HOST").unwrap_or_else(|_| "127.0.0.1".to_owned());
    let mut port = env::var("MEDICINE_WEB_PORT").unwrap_or_else(|_| "8000".to_owned());
    let mut canonical_db = env::var("MEDICINE_CANONICAL_DB")
        .ok()
        .filter(|value| !value.trim().is_empty());
    let mut personal_db =
        env::var("MEDICINE_PERSONAL_DB").unwrap_or_else(|_| DEFAULT_PERSONAL_DB.to_owned());
    let mut reference_dir =
        env::var("MEDICINE_REFERENCE_DIR").unwrap_or_else(|_| DEFAULT_REFERENCE_DIR.to_owned());
    let mut reference_update_base_url = env::var("MEDICINE_REFERENCE_UPDATE_BASE_URL")
        .ok()
        .filter(|value| !value.trim().is_empty());
    let mut reference_trust_manifest = env::var("MEDICINE_REFERENCE_TRUST_MANIFEST")
        .unwrap_or_else(|_| DEFAULT_REFERENCE_TRUST_MANIFEST.to_owned());
    let mut static_dir =
        env::var("MEDICINE_STATIC_DIR").unwrap_or_else(|_| DEFAULT_STATIC_DIR.to_owned());
    let mut ocr_assets_dir = env::var("MEDICINE_OCR_ASSETS_DIR").ok();
    let mut reference_unavailable_reason = env::var("MEDICINE_REFERENCE_UNAVAILABLE_REASON").ok();

    let mut index = 0;
    while index < args.len() {
        match args[index].as_str() {
            "--host"
            | "--port"
            | "--canonical-db"
            | "--personal-db"
            | "--reference-dir"
            | "--reference-update-base-url"
            | "--reference-trust-manifest"
            | "--static-dir" => {
                let option = args[index].clone();
                index += 1;
                let value = args.get(index).ok_or_else(usage)?.to_owned();
                match option.as_str() {
                    "--host" => host = value,
                    "--port" => port = value,
                    "--canonical-db" => canonical_db = Some(value),
                    "--personal-db" => personal_db = value,
                    "--reference-dir" => reference_dir = value,
                    "--reference-update-base-url" => reference_update_base_url = Some(value),
                    "--reference-trust-manifest" => reference_trust_manifest = value,
                    "--static-dir" => static_dir = value,
                    _ => unreachable!(),
                }
                index += 1;
                continue;
            }
            "--ocr-assets-dir" => {
                index += 1;
                ocr_assets_dir = Some(args.get(index).ok_or_else(usage)?.to_owned());
                index += 1;
                continue;
            }
            "--reference-unavailable-reason" => {
                index += 1;
                reference_unavailable_reason = Some(args.get(index).ok_or_else(usage)?.to_owned());
                index += 1;
                continue;
            }
            _ => return Err(usage()),
        }
    }

    let host: IpAddr = host
        .parse()
        .map_err(|_| "web host must be an IP address".to_owned())?;
    let port: u16 = port
        .parse()
        .map_err(|_| "web port must be an integer between 1 and 65535".to_owned())?;
    if port == 0 {
        return Err("web port must be an integer between 1 and 65535".to_owned());
    }

    let canonical_db = if reference_unavailable_reason.is_some() {
        None
    } else if let Some(path) = canonical_db {
        Some(PathBuf::from(path))
    } else {
        let base_url = reference_update_base_url.ok_or_else(|| {
            "reference distribution base URL is not configured and no --canonical-db override was supplied"
                .to_owned()
        })?;
        let selection = tokio::task::spawn_blocking(move || {
            ensure_development_reference(DevelopmentReferenceConfig {
                reference_dir: PathBuf::from(reference_dir),
                base_url,
                trust_manifest: PathBuf::from(reference_trust_manifest),
                contract_major: REFERENCE_CONTRACT_MAJOR,
            })
        })
        .await
        .map_err(|error| format!("reference preparation task failed: {error}"))?
        .map_err(|error| format!("reference preparation failed: {error}"))?;
        reference_unavailable_reason = selection.unavailable_reason;
        selection.database
    };
    let app = build_router(WebConfig {
        canonical_db,
        personal_db: PathBuf::from(personal_db),
        static_dir: PathBuf::from(static_dir),
        ocr_assets_dir: ocr_assets_dir.map(PathBuf::from),
        reference_unavailable_reason,
    })?;
    let address = (host, port);
    let listener = TcpListener::bind(address)
        .await
        .map_err(|error| format!("cannot bind local web service on {host}:{port}: {error}"))?;
    eprintln!("medicine-core-web listening on http://{host}:{port}");
    axum::serve(listener, app)
        .await
        .map_err(|error| format!("local web service failed: {error}"))
}

fn usage() -> String {
    "usage: medicine-core-web [--host <IP>] [--port <PORT>] [--canonical-db <PATH>] [--personal-db <PATH>] [--reference-dir <PATH>] [--reference-update-base-url <HTTPS_BASE_URL>] [--reference-trust-manifest <PATH>] [--static-dir <PATH>] [--ocr-assets-dir <PATH>] [--reference-unavailable-reason <REASON>]".to_owned()
}
