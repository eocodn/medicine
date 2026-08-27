use super::support::{parse, AppConfig};
use rusqlite::{backup::Backup, Connection, OpenFlags};
use serde_json::{json, Value};
use std::env;
use std::fs::{self, File};
use std::io::{Read, Write};
use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;

const DEFAULT_OUTPUT: &str = "data/debug/mobile.png";
const DEFAULT_STATIC_DIR: &str = "medicine_app/static";

pub(super) fn capture(
    config: &AppConfig,
    args: &[String],
    usage: fn() -> String,
) -> Result<Value, String> {
    let parsed = parse(
        args,
        &["--output", "--width", "--height", "--screen"],
        &["--json"],
        usage,
    )?;
    parsed.expect_no_positionals()?;
    let width = parsed.parse_i64("--width", 390)?;
    let height = parsed.parse_i64("--height", 844)?;
    if width <= 0 || height <= 0 {
        return Err("screenshot width and height must be positive".to_owned());
    }
    let screen = parsed.optional("--screen").unwrap_or("home");
    if !matches!(screen, "home" | "meds" | "search" | "people") {
        return Err("screenshot screen must be home, meds, search, or people".to_owned());
    }

    let output = absolute_path(Path::new(
        parsed.optional("--output").unwrap_or(DEFAULT_OUTPUT),
    ))?;
    if let Some(parent) = output.parent() {
        fs::create_dir_all(parent)
            .map_err(|error| format!("cannot create screenshot output directory: {error}"))?;
    }

    let browser = browser_binary()?;
    let web_binary = env::var_os("MEDICINE_CORE_WEB_BINARY")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from("medicine-core-web"));
    let static_dir = env::var_os("MEDICINE_STATIC_DIR")
        .map(PathBuf::from)
        .unwrap_or_else(|| PathBuf::from(DEFAULT_STATIC_DIR));
    let temp = TempDir::new()?;
    let snapshot = temp.path.join("personal.sqlite");
    snapshot_personal_database(&config.personal_db, &snapshot)?;

    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("cannot reserve local screenshot port: {error}"))?;
    let port = listener
        .local_addr()
        .map_err(|error| format!("cannot inspect local screenshot port: {error}"))?
        .port();
    drop(listener);

    let server_log = temp.path.join("web.stderr.log");
    let log_file = File::create(&server_log)
        .map_err(|error| format!("cannot create screenshot web log: {error}"))?;
    let mut server_command = Command::new(&web_binary);
    server_command
        .args([
            "--host",
            "127.0.0.1",
            "--port",
            &port.to_string(),
            "--canonical-db",
            path_string(&config.canonical_db)?.as_str(),
            "--personal-db",
            path_string(&snapshot)?.as_str(),
            "--static-dir",
            path_string(&static_dir)?.as_str(),
        ])
        .stdout(Stdio::null())
        .stderr(Stdio::from(log_file));
    if let Some(ocr_assets) = env::var_os("MEDICINE_OCR_ASSETS_DIR") {
        server_command
            .arg("--ocr-assets-dir")
            .arg(PathBuf::from(ocr_assets));
    }

    emit_event(json!({"event":"screenshot_started","screen":screen}));
    let mut server = server_command
        .spawn()
        .map_err(|error| format!("cannot start local Rust web service: {error}"))?;
    let base_url = format!("http://127.0.0.1:{port}");
    if let Err(error) = wait_until_ready(&mut server, &base_url, &server_log) {
        stop_child(&mut server);
        return Err(error);
    }
    emit_event(json!({"event":"screenshot_web_ready"}));

    let url = format!("{base_url}/?screen={screen}");
    let browser_status = Command::new(browser)
        .args([
            "--headless",
            "--no-sandbox",
            "--disable-gpu",
            "--hide-scrollbars",
            "--virtual-time-budget=2000",
            &format!("--window-size={width},{height}"),
            &format!("--screenshot={}", output.display()),
            &url,
        ])
        .status()
        .map_err(|error| format!("cannot run Chromium screenshot: {error}"));
    stop_child(&mut server);
    let browser_status = browser_status?;
    if !browser_status.success() {
        return Err(format!(
            "Chromium screenshot failed with status {browser_status}"
        ));
    }

    let size_bytes = fs::metadata(&output)
        .map_err(|error| format!("screenshot output was not created: {error}"))?
        .len();
    emit_event(json!({"event":"screenshot_completed","size_bytes":size_bytes}));
    Ok(json!({
        "status": 200,
        "body": {
            "path": output,
            "width": width,
            "height": height,
            "screen": screen,
            "size_bytes": size_bytes,
        }
    }))
}

fn snapshot_personal_database(source: &Path, destination: &Path) -> Result<(), String> {
    if !source.exists() {
        return Ok(());
    }
    let source = Connection::open_with_flags(
        source,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("cannot open personal database read only for screenshot: {error}"))?;
    let mut destination = Connection::open(destination)
        .map_err(|error| format!("cannot create screenshot personal snapshot: {error}"))?;
    let backup = Backup::new(&source, &mut destination)
        .map_err(|error| format!("cannot start personal database screenshot snapshot: {error}"))?;
    backup
        .run_to_completion(32, Duration::from_millis(10), None)
        .map_err(|error| format!("cannot snapshot personal database for screenshot: {error}"))
}

fn wait_until_ready(server: &mut Child, base_url: &str, log_path: &Path) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = server
            .try_wait()
            .map_err(|error| format!("cannot inspect screenshot web service: {error}"))?
        {
            return Err(format!(
                "temporary Rust web service exited before screenshot ({status}): {}",
                read_log(log_path)
            ));
        }
        if health_is_ready(base_url) {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "temporary Rust web service did not become ready: {}",
                read_log(log_path)
            ));
        }
        thread::sleep(Duration::from_millis(100));
    }
}

fn health_is_ready(base_url: &str) -> bool {
    let Some(address) = base_url.strip_prefix("http://") else {
        return false;
    };
    let Ok(mut stream) = TcpStream::connect(address) else {
        return false;
    };
    let timeout = Some(Duration::from_millis(500));
    if stream.set_read_timeout(timeout).is_err() || stream.set_write_timeout(timeout).is_err() {
        return false;
    }
    if stream
        .write_all(b"GET /api/health HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n")
        .is_err()
    {
        return false;
    }
    let mut response = [0_u8; 32];
    let Ok(read) = stream.read(&mut response) else {
        return false;
    };
    response[..read].starts_with(b"HTTP/1.1 200") || response[..read].starts_with(b"HTTP/1.0 200")
}

fn stop_child(child: &mut Child) {
    if child.try_wait().ok().flatten().is_some() {
        return;
    }
    unsafe {
        libc::kill(child.id() as i32, libc::SIGTERM);
    }
    let deadline = Instant::now() + Duration::from_secs(2);
    while Instant::now() < deadline {
        if child.try_wait().ok().flatten().is_some() {
            return;
        }
        thread::sleep(Duration::from_millis(25));
    }
    let _ = child.kill();
    let _ = child.wait();
}

fn browser_binary() -> Result<PathBuf, String> {
    if let Some(value) = env::var_os("MEDICINE_CHROMIUM_BINARY") {
        return Ok(PathBuf::from(value));
    }
    let path = env::var_os("PATH").unwrap_or_default();
    for directory in env::split_paths(&path) {
        for name in ["chromium", "chromium-browser", "google-chrome"] {
            let candidate = directory.join(name);
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
    }
    Err("Chromium is not installed; use the compose 'ui' service".to_owned())
}

fn absolute_path(path: &Path) -> Result<PathBuf, String> {
    if path.is_absolute() {
        Ok(path.to_path_buf())
    } else {
        env::current_dir()
            .map(|current| current.join(path))
            .map_err(|error| format!("cannot resolve screenshot output path: {error}"))
    }
}

fn path_string(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("path is not valid UTF-8: {}", path.display()))
}

fn read_log(path: &Path) -> String {
    fs::read_to_string(path)
        .unwrap_or_else(|_| "no web service diagnostics available".to_owned())
        .trim()
        .to_owned()
}

fn emit_event(value: Value) {
    eprintln!("{value}");
}

struct TempDir {
    path: PathBuf,
}

impl TempDir {
    fn new() -> Result<Self, String> {
        let path = env::temp_dir().join(format!("medicine-screenshot-{}", Uuid::new_v4()));
        fs::create_dir(&path)
            .map_err(|error| format!("cannot create screenshot temporary directory: {error}"))?;
        Ok(Self { path })
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}
