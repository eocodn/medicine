use medicine_core::development_reference::{
    check_development_reference_update, inspect_development_reference_bootstrap,
    open_development_reference, DevelopmentReferenceBootstrapInspection,
    DevelopmentReferenceConfig, DevelopmentReferenceUpdateStatus,
};
use medicine_core::web::{build_runtime, WebConfig, WebReferenceBootstrapConfig};
use std::{env, net::IpAddr, path::PathBuf, thread, time::Duration};
use tokio::net::TcpListener;

const DEFAULT_PERSONAL_DB: &str = "data/db/personal.sqlite";
const DEFAULT_REFERENCE_DIR: &str = "data/reference";
const DEFAULT_REFERENCE_TRUST_MANIFEST: &str = "deploy/reference-signing-trusted-keys.json";
const DEFAULT_STATIC_DIR: &str = "ui/dist";
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
    let mut reference_unavailable_reason = env::var("MEDICINE_REFERENCE_UNAVAILABLE_REASON")
        .ok()
        .filter(|value| !value.trim().is_empty());

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

    let mut managed_reference_update = None;
    let mut reference_bootstrap = None;
    let canonical_db = if reference_unavailable_reason.is_some() {
        None
    } else if let Some(path) = canonical_db {
        Some(PathBuf::from(path))
    } else {
        let base_url = reference_update_base_url.ok_or_else(|| {
            "reference distribution base URL is not configured and no --canonical-db override was supplied"
                .to_owned()
        })?;
        let config = DevelopmentReferenceConfig {
            reference_dir: PathBuf::from(reference_dir),
            base_url,
            trust_manifest: PathBuf::from(reference_trust_manifest),
            contract_major: REFERENCE_CONTRACT_MAJOR,
        };
        let startup_config = config.clone();
        let selection =
            tokio::task::spawn_blocking(move || open_development_reference(startup_config))
                .await
                .map_err(|error| format!("reference preparation task failed: {error}"))?
                .map_err(|error| format!("reference preparation failed: {error}"))?;
        if selection.database.is_some() && selection.unavailable_reason.is_none() {
            managed_reference_update = Some(config);
        } else if selection.unavailable_reason.is_none() {
            let inspect_config = config.clone();
            let inspection = tokio::task::spawn_blocking(move || {
                inspect_development_reference_bootstrap(inspect_config)
            })
            .await
            .map_err(|error| format!("reference bootstrap inspection task failed: {error}"))?
            .map_err(|error| format!("reference bootstrap inspection failed: {error}"))?;
            match inspection {
                DevelopmentReferenceBootstrapInspection::Download(info) => {
                    reference_unavailable_reason = Some("bootstrap_required".to_owned());
                    reference_bootstrap = Some(WebReferenceBootstrapConfig {
                        development: config,
                        download_size_bytes: info.download_size_bytes,
                        total_download_bytes: info.total_download_bytes,
                    });
                }
                DevelopmentReferenceBootstrapInspection::Unavailable => {
                    reference_unavailable_reason = Some("update_required".to_owned());
                }
            }
        } else {
            reference_unavailable_reason = selection.unavailable_reason.clone();
        }
        selection.database
    };
    let runtime = build_runtime(WebConfig {
        canonical_db,
        personal_db: PathBuf::from(personal_db),
        static_dir: PathBuf::from(static_dir),
        ocr_assets_dir: ocr_assets_dir.map(PathBuf::from),
        reference_unavailable_reason,
        reference_bootstrap,
    })?;
    let address = (host, port);
    let listener = TcpListener::bind(address)
        .await
        .map_err(|error| format!("cannot bind local web service on {host}:{port}: {error}"))?;
    eprintln!("medicine-core-web listening on http://{host}:{port}");

    let heartbeat_status = runtime.status.clone();
    thread::spawn(move || loop {
        thread::sleep(Duration::from_secs(5));
        heartbeat_status.heartbeat();
    });

    if let Some(config) = managed_reference_update {
        let engine = runtime.engine.clone();
        let status = runtime.status.clone();
        status.reference_update_running("check");
        tokio::spawn(async move {
            let result =
                tokio::task::spawn_blocking(move || check_development_reference_update(config))
                    .await;
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
                Ok(Ok(DevelopmentReferenceUpdateStatus::NoChange)) => {
                    status.reference_update_completed("no_change");
                    eprintln!("reference update check completed: no change");
                }
                Ok(Ok(DevelopmentReferenceUpdateStatus::Staged)) => {
                    status.reference_update_completed("staged");
                    eprintln!("reference update staged for next startup");
                }
                Ok(Ok(DevelopmentReferenceUpdateStatus::UpdateRequired)) => match engine.write() {
                    Ok(mut engine) => {
                        if let Err(error) =
                            engine.set_reference_available(false, Some("update_required"))
                        {
                            let detail = format!(
                                "cannot apply reference retirement to live engine: {error}"
                            );
                            status.reference_update_failed("retire", &detail);
                            eprintln!("cannot apply reference retirement to live engine: {error}");
                        } else {
                            status.reference_update_completed("update_required");
                            eprintln!(
                                "reference contract retired; live safety-data access disabled"
                            );
                        }
                    }
                    Err(_) => {
                        status.reference_update_failed(
                            "retire",
                            "cannot lock live medicine engine after reference retirement",
                        );
                        eprintln!("cannot lock live medicine engine after reference retirement")
                    }
                },
            }
        });
    } else {
        runtime
            .status
            .reference_update_disabled("managed reference update is not configured");
    }

    axum::serve(listener, runtime.router)
        .await
        .map_err(|error| format!("local web service failed: {error}"))
}

fn usage() -> String {
    "usage: medicine-core-web [--host <IP>] [--port <PORT>] [--canonical-db <PATH>] [--personal-db <PATH>] [--reference-dir <PATH>] [--reference-update-base-url <HTTPS_BASE_URL>] [--reference-trust-manifest <PATH>] [--static-dir <PATH>] [--ocr-assets-dir <PATH>] [--reference-unavailable-reason <REASON>]".to_owned()
}
