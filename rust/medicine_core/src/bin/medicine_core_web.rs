use medicine_core::web::{
    build_runtime, schedule_reference_update, WebConfig, WebReferenceRuntime,
};
use medicine_core::{open_reference_channel, ReferenceBootstrapState, ReferenceChannelConfig};
use std::{env, net::IpAddr, path::PathBuf, sync::Arc, thread, time::Duration};
use tokio::net::TcpListener;

const DEFAULT_PERSONAL_DB: &str = "data/db/personal.sqlite";
const DEFAULT_REFERENCE_DIR: &str = "data/reference";
const DEFAULT_REFERENCE_TRUST_MANIFEST: &str = "deploy/reference-signing-trusted-keys.json";
const DEFAULT_STATIC_DIR: &str = "ui/dist";

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
    #[cfg(feature = "agentctl-web")]
    let mut enable_agent_control = false;
    #[cfg(not(feature = "agentctl-web"))]
    let enable_agent_control = false;

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
            "--agent-control" => {
                #[cfg(feature = "agentctl-web")]
                {
                    enable_agent_control = true;
                    index += 1;
                    continue;
                }
                #[cfg(not(feature = "agentctl-web"))]
                return Err("--agent-control requires the agentctl-web feature".to_owned());
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
    if enable_agent_control && !host.is_loopback() {
        return Err("--agent-control requires a loopback web host".to_owned());
    }

    let mut managed_reference_runtime = None;
    let canonical_db = if reference_unavailable_reason.is_some() {
        None
    } else if let Some(path) = canonical_db {
        Some(PathBuf::from(path))
    } else {
        let base_url = reference_update_base_url.ok_or_else(|| {
            "reference distribution base URL is not configured and no --canonical-db override was supplied"
                .to_owned()
        })?;
        let config = ReferenceChannelConfig {
            reference_dir: PathBuf::from(reference_dir),
            base_url,
            trust_manifest: PathBuf::from(reference_trust_manifest),
        };
        // ReferenceHttpSource deliberately uses the same synchronous HTTP
        // implementation as Android. reqwest::blocking must construct its
        // internal runtime outside Tokio's async context.
        let runtime = Arc::new(
            tokio::task::spawn_blocking(move || open_reference_channel(config))
                .await
                .map_err(|error| format!("reference runtime initialization task failed: {error}"))?
                .map_err(|error| format!("reference runtime initialization failed: {error}"))?,
        );
        let prepare_runtime = Arc::clone(&runtime);
        let prepared = tokio::task::spawn_blocking(move || prepare_runtime.prepare())
            .await
            .map_err(|error| format!("reference preparation task failed: {error}"))?
            .map_err(|error| format!("reference preparation failed: {error}"))?;
        let database = match prepared.selection {
            Some(selection) => {
                reference_unavailable_reason = selection.unavailable_reason.clone();
                selection.database
            }
            None => {
                match prepared.snapshot.state {
                    ReferenceBootstrapState::DownloadRequired => {
                        reference_unavailable_reason = Some("bootstrap_required".to_owned());
                    }
                    ReferenceBootstrapState::Unavailable => {
                        reference_unavailable_reason = Some("update_required".to_owned());
                    }
                    state => {
                        return Err(format!(
                            "reference preparation ended in unexpected state: {state:?}"
                        ));
                    }
                }
                None
            }
        };
        managed_reference_runtime = Some(runtime);
        database
    };
    let web_reference_runtime = managed_reference_runtime
        .clone()
        .map(|runtime| runtime as Arc<dyn WebReferenceRuntime>);
    let runtime = build_runtime(WebConfig {
        canonical_db,
        personal_db: PathBuf::from(personal_db),
        static_dir: PathBuf::from(static_dir),
        ocr_assets_dir: ocr_assets_dir.map(PathBuf::from),
        reference_unavailable_reason,
        reference_runtime: web_reference_runtime,
        enable_agent_control,
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

    if let Some(reference_runtime) = managed_reference_runtime {
        if reference_runtime.status().state == ReferenceBootstrapState::Ready {
            schedule_reference_update(
                reference_runtime as Arc<dyn WebReferenceRuntime>,
                runtime.engine.clone(),
                runtime.status.clone(),
            );
        } else {
            runtime
                .status
                .reference_update_disabled("reference bootstrap is not ready");
        }
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
    #[cfg(feature = "agentctl-web")]
    let agent_control = " [--agent-control]";
    #[cfg(not(feature = "agentctl-web"))]
    let agent_control = "";
    format!(
        "usage: medicine-core-web [--host <IP>] [--port <PORT>] [--canonical-db <PATH>] [--personal-db <PATH>] [--reference-dir <PATH>] [--reference-update-base-url <HTTPS_BASE_URL>] [--reference-trust-manifest <PATH>] [--static-dir <PATH>] [--ocr-assets-dir <PATH>] [--reference-unavailable-reason <REASON>]{agent_control}"
    )
}
