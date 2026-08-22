use medicine_core::web::{build_router, WebConfig};
use std::{env, net::IpAddr, path::PathBuf};
use tokio::net::TcpListener;

const DEFAULT_CANONICAL_DB: &str = "data/db/canonical.sqlite";
const DEFAULT_PERSONAL_DB: &str = "data/db/personal.sqlite";
const DEFAULT_STATIC_DIR: &str = "medicine_app/static";

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
    let mut canonical_db =
        env::var("MEDICINE_CANONICAL_DB").unwrap_or_else(|_| DEFAULT_CANONICAL_DB.to_owned());
    let mut personal_db =
        env::var("MEDICINE_PERSONAL_DB").unwrap_or_else(|_| DEFAULT_PERSONAL_DB.to_owned());
    let mut static_dir =
        env::var("MEDICINE_STATIC_DIR").unwrap_or_else(|_| DEFAULT_STATIC_DIR.to_owned());
    let mut ocr_assets_dir = env::var("MEDICINE_OCR_ASSETS_DIR").ok();
    let mut reference_unavailable_reason = env::var("MEDICINE_REFERENCE_UNAVAILABLE_REASON").ok();

    let mut index = 0;
    while index < args.len() {
        let value = match args[index].as_str() {
            "--host" => &mut host,
            "--port" => &mut port,
            "--canonical-db" => &mut canonical_db,
            "--personal-db" => &mut personal_db,
            "--static-dir" => &mut static_dir,
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
        };
        index += 1;
        *value = args.get(index).ok_or_else(usage)?.to_owned();
        index += 1;
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
    } else {
        Some(PathBuf::from(canonical_db))
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
    "usage: medicine-core-web [--host <IP>] [--port <PORT>] [--canonical-db <PATH>] [--personal-db <PATH>] [--static-dir <PATH>] [--ocr-assets-dir <PATH>] [--reference-unavailable-reason <REASON>]".to_owned()
}
