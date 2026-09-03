use super::ui_browser::UiBrowser;
use rusqlite::{backup::Backup, Connection, OpenFlags};
use serde_json::{json, Value};
use std::fs::{self, File};
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use uuid::Uuid;

pub(super) struct UiRuntimeConfig {
    pub(super) canonical_db: PathBuf,
    pub(super) personal_db: PathBuf,
}

pub(super) struct UiRuntime {
    server: Child,
    base_url: String,
    browser: UiBrowser,
    client: reqwest::blocking::Client,
    _temp: TempDir,
}

impl UiRuntime {
    pub(super) fn start(
        config: &UiRuntimeConfig,
        static_dir: &Path,
        reference_unavailable_reason: Option<&str>,
        width: u32,
        height: u32,
    ) -> Result<Self, String> {
        let temp = TempDir::new()?;
        let personal_snapshot = temp.path.join("personal.sqlite");
        snapshot_personal_database(&config.personal_db, &personal_snapshot)?;
        let port = reserve_port()?;
        let base_url = format!("http://127.0.0.1:{port}");
        let web_binary = std::env::var_os("MEDICINE_CORE_WEB_BINARY")
            .map(PathBuf::from)
            .unwrap_or_else(|| PathBuf::from("medicine-core-web"));
        let server_log = temp.path.join("web.stderr.log");
        let log_file = File::create(&server_log)
            .map_err(|error| format!("cannot create UI scenario web log: {error}"))?;
        let mut command = Command::new(web_binary);
        command
            .args([
                "--host",
                "127.0.0.1",
                "--port",
                &port.to_string(),
                "--personal-db",
                path_string(&personal_snapshot)?.as_str(),
                "--static-dir",
                path_string(static_dir)?.as_str(),
                "--agent-control",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::from(log_file));
        if let Some(reason) = reference_unavailable_reason {
            command.arg("--reference-unavailable-reason").arg(reason);
        } else {
            if !config.canonical_db.is_file() {
                return Err(format!(
                    "ui-scenario requires an existing canonical database or --reference-unavailable-reason: {}",
                    config.canonical_db.display()
                ));
            }
            command.arg("--canonical-db").arg(&config.canonical_db);
        }
        let mut server = command
            .spawn()
            .map_err(|error| format!("cannot start UI scenario web service: {error}"))?;
        wait_until_ready(&mut server, &base_url, &server_log)?;
        let browser = UiBrowser::start(&temp.path, width, height)?;
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(8))
            .build()
            .map_err(|error| format!("cannot create UI scenario HTTP client: {error}"))?;
        Ok(Self {
            server,
            base_url,
            browser,
            client,
            _temp: temp,
        })
    }

    pub(super) fn open(&mut self, screen: Option<&str>) -> Result<(), String> {
        let url = match screen {
            Some(screen) => format!("{}/?screen={}", self.base_url, percent_encode(screen)),
            None => format!("{}/", self.base_url),
        };
        self.browser.navigate(&url)
    }

    pub(super) fn reload(&mut self) -> Result<(), String> {
        self.browser.reload()
    }

    pub(super) fn click(&mut self, target: &str) -> Result<Value, String> {
        self.browser.click(target)
    }

    pub(super) fn set_field(&mut self, target: &str, value: &Value) -> Result<Value, String> {
        self.browser.set_field(target, value)
    }

    pub(super) fn observe(&mut self) -> Result<Value, String> {
        self.browser.observe()
    }

    pub(super) fn set_local_storage(
        &mut self,
        key: &str,
        value: Option<&str>,
    ) -> Result<(), String> {
        self.browser.set_local_storage(key, value)
    }

    pub(super) fn set_clock(&mut self, iso_datetime: &str) -> Result<Value, String> {
        self.browser.set_clock(iso_datetime)
    }

    pub(super) fn screenshot(&mut self, output: &Path) -> Result<Value, String> {
        self.browser.screenshot(output)
    }

    pub(super) fn take_browser_events(&mut self) -> Vec<Value> {
        self.browser.take_events()
    }

    pub(super) fn install_fault(&self, rule: &Value) -> Result<Value, String> {
        self.post_json("/api/development/agent/faults", rule)
    }

    pub(super) fn fault_status(&self, id: &str) -> Result<Value, String> {
        self.get_json(&format!(
            "/api/development/agent/faults/{}",
            percent_encode(id)
        ))
    }

    pub(super) fn release_fault(&self, id: &str) -> Result<Value, String> {
        self.post_json(
            &format!(
                "/api/development/agent/faults/{}/release",
                percent_encode(id)
            ),
            &json!({}),
        )
    }

    pub(super) fn request(
        &self,
        method: &str,
        path: &str,
        body: Option<&Value>,
    ) -> Result<Value, String> {
        let method = reqwest::Method::from_bytes(method.as_bytes())
            .map_err(|error| format!("invalid UI scenario request method: {error}"))?;
        let url = format!("{}{}", self.base_url, path);
        let mut request = self.client.request(method, &url);
        if let Some(body) = body {
            request = request
                .header(reqwest::header::CONTENT_TYPE, "application/json")
                .body(body.to_string());
        }
        let response = request
            .send()
            .map_err(|error| format!("UI scenario request failed: {error}"))?;
        response_envelope(response)
    }

    fn get_json(&self, path: &str) -> Result<Value, String> {
        let response = self
            .client
            .get(format!("{}{}", self.base_url, path))
            .send()
            .map_err(|error| format!("UI scenario control GET failed: {error}"))?;
        if !response.status().is_success() {
            return Err(format!(
                "UI scenario control GET {path} returned {}",
                response.status()
            ));
        }
        let text = response
            .text()
            .map_err(|error| format!("cannot read UI scenario control response: {error}"))?;
        serde_json::from_str(&text)
            .map_err(|error| format!("cannot decode UI scenario control response: {error}"))
    }

    fn post_json(&self, path: &str, body: &Value) -> Result<Value, String> {
        let response = self
            .client
            .post(format!("{}{}", self.base_url, path))
            .header(reqwest::header::CONTENT_TYPE, "application/json")
            .body(body.to_string())
            .send()
            .map_err(|error| format!("UI scenario control POST failed: {error}"))?;
        if !response.status().is_success() {
            let status = response.status();
            let detail = response.text().unwrap_or_default();
            return Err(format!(
                "UI scenario control POST {path} returned {status}: {detail}"
            ));
        }
        let text = response
            .text()
            .map_err(|error| format!("cannot read UI scenario control response: {error}"))?;
        serde_json::from_str(&text)
            .map_err(|error| format!("cannot decode UI scenario control response: {error}"))
    }
}

impl Drop for UiRuntime {
    fn drop(&mut self) {
        stop_child(&mut self.server);
    }
}

fn response_envelope(response: reqwest::blocking::Response) -> Result<Value, String> {
    let status = response.status().as_u16();
    let text = response
        .text()
        .map_err(|error| format!("cannot read UI scenario API response: {error}"))?;
    let body = serde_json::from_str::<Value>(&text)
        .map_err(|error| format!("cannot decode UI scenario API response: {error}"))?;
    Ok(json!({"status":status,"body":body}))
}

fn snapshot_personal_database(source: &Path, destination: &Path) -> Result<(), String> {
    if !source.exists() {
        return Ok(());
    }
    let source = Connection::open_with_flags(
        source,
        OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX,
    )
    .map_err(|error| format!("cannot open personal database read only for UI scenario: {error}"))?;
    let mut destination = Connection::open(destination)
        .map_err(|error| format!("cannot create UI scenario personal snapshot: {error}"))?;
    let backup = Backup::new(&source, &mut destination)
        .map_err(|error| format!("cannot start UI scenario personal snapshot: {error}"))?;
    backup
        .run_to_completion(32, Duration::from_millis(10), None)
        .map_err(|error| format!("cannot snapshot personal database for UI scenario: {error}"))
}

fn wait_until_ready(server: &mut Child, base_url: &str, log_path: &Path) -> Result<(), String> {
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Some(status) = server
            .try_wait()
            .map_err(|error| format!("cannot inspect UI scenario web service: {error}"))?
        {
            return Err(format!(
                "UI scenario web service exited before ready ({status}): {}",
                read_log(log_path)
            ));
        }
        if reqwest::blocking::get(format!("{base_url}/api/health"))
            .map(|response| response.status().is_success())
            .unwrap_or(false)
        {
            return Ok(());
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "UI scenario web service did not become ready: {}",
                read_log(log_path)
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn reserve_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("cannot reserve local UI scenario port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("cannot inspect local UI scenario port: {error}"))
}

fn path_string(path: &Path) -> Result<String, String> {
    path.to_str()
        .map(str::to_owned)
        .ok_or_else(|| format!("path is not valid UTF-8: {}", path.display()))
}

fn read_log(path: &Path) -> String {
    fs::read_to_string(path)
        .unwrap_or_else(|_| "no UI scenario web diagnostics available".to_owned())
        .trim()
        .to_owned()
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

fn percent_encode(value: &str) -> String {
    value
        .bytes()
        .map(|byte| {
            if byte.is_ascii_alphanumeric() || matches!(byte, b'-' | b'.' | b'_' | b'~') {
                char::from(byte).to_string()
            } else {
                format!("%{byte:02X}")
            }
        })
        .collect()
}

pub(super) struct TempDir {
    pub(super) path: PathBuf,
}

impl TempDir {
    fn new() -> Result<Self, String> {
        let path = std::env::temp_dir().join(format!("medicine-ui-scenario-{}", Uuid::new_v4()));
        fs::create_dir(&path)
            .map_err(|error| format!("cannot create UI scenario temp directory: {error}"))?;
        Ok(Self { path })
    }
}

impl Drop for TempDir {
    fn drop(&mut self) {
        let _ = fs::remove_dir_all(&self.path);
    }
}
