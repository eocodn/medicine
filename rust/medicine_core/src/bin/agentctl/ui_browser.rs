use base64::Engine;
use serde_json::{json, Value};
use std::fs;
use std::net::TcpListener;
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use std::thread;
use std::time::{Duration, Instant};
use tungstenite::stream::MaybeTlsStream;
use tungstenite::{connect, Message, WebSocket};

pub(super) struct UiBrowser {
    child: Child,
    socket: WebSocket<MaybeTlsStream<std::net::TcpStream>>,
    next_id: u64,
    events: Vec<Value>,
    stderr_path: PathBuf,
}

impl UiBrowser {
    pub(super) fn start(temp_dir: &Path, width: u32, height: u32) -> Result<Self, String> {
        let browser = browser_binary()?;
        let debug_port = reserve_port()?;
        let stderr_path = temp_dir.join("chromium.stderr.log");
        let stderr = fs::File::create(&stderr_path)
            .map_err(|error| format!("cannot create Chromium log: {error}"))?;
        let user_data = temp_dir.join("chromium-profile");
        fs::create_dir_all(&user_data)
            .map_err(|error| format!("cannot create Chromium profile: {error}"))?;
        let child = Command::new(browser)
            .args([
                "--headless",
                "--no-sandbox",
                "--disable-gpu",
                "--hide-scrollbars",
                "--disable-background-networking",
                "--disable-component-update",
                "--disable-default-apps",
                "--disable-sync",
                &format!("--remote-debugging-port={debug_port}"),
                &format!("--window-size={width},{height}"),
                &format!("--user-data-dir={}", user_data.display()),
                "about:blank",
            ])
            .stdout(Stdio::null())
            .stderr(Stdio::from(stderr))
            .spawn()
            .map_err(|error| format!("cannot start Chromium: {error}"))?;

        let ws_url = wait_for_page(debug_port, &stderr_path)?;
        let (mut socket, _) = connect(ws_url.as_str())
            .map_err(|error| format!("cannot connect Chromium DevTools: {error}"))?;
        if let MaybeTlsStream::Plain(stream) = socket.get_mut() {
            let timeout = Some(Duration::from_secs(8));
            let _ = stream.set_read_timeout(timeout);
            let _ = stream.set_write_timeout(timeout);
        }
        let mut browser = Self {
            child,
            socket,
            next_id: 1,
            events: Vec::new(),
            stderr_path,
        };
        browser.call("Runtime.enable", json!({}))?;
        browser.call("Page.enable", json!({}))?;
        browser.call("Log.enable", json!({}))?;
        Ok(browser)
    }

    pub(super) fn navigate(&mut self, url: &str) -> Result<(), String> {
        self.call("Page.navigate", json!({"url":url}))?;
        self.wait_document_ready()
    }

    pub(super) fn reload(&mut self) -> Result<(), String> {
        self.call("Page.reload", json!({"ignoreCache":true}))?;
        self.wait_document_ready()
    }

    pub(super) fn click(&mut self, target: &str) -> Result<Value, String> {
        let target_json = serde_json::to_string(target).map_err(|error| error.to_string())?;
        let expression = format!(
            r#"(() => {{
                const target = {target_json};
                let node = null;
                if (target === 'profile-shortcut') node = document.querySelector('#profile-shortcut');
                else if (target === 'add-person') node = document.querySelector('#open-person-form, #home-add-person');
                else if (target === 'person:submit') node = document.querySelector('#person-submit');
                else if (target === 'person:confirm-delete') node = document.querySelector('#confirm-delete-person');
                else if (target === 'add-medication') node = document.querySelector('[data-go="search"]');
                else if (target === 'search:clear') node = document.querySelector('#drug-query-clear');
                else if (target === 'prescription:open') node = document.querySelector('[data-open-prescription]');
                else if (target === 'prescription:add-time') node = document.querySelector('[data-add-schedule-time]');
                else if (target === 'prescription:save') node = document.querySelector('#confirm-add-med, #confirm-edit-med');
                else if (target === 'medication:confirm-stop') node = document.querySelector('#confirm-stop-medication');
                else if (target === 'sheet:close') node = Array.from(document.querySelectorAll('[data-close-sheet]')).find((item) => !item.closest('.hidden'));
                else if (target.startsWith('screen:')) {{
                  const id = target.slice('screen:'.length);
                  node = Array.from(document.querySelectorAll('[data-nav]')).find((item) => item.dataset.nav === id);
                }} else if (target.startsWith('person-name:')) {{
                  const name = target.slice('person-name:'.length);
                  node = Array.from(document.querySelectorAll('[data-person-select]')).find((item) => item.textContent.includes(name));
                }} else if (target.startsWith('person:')) {{
                  const parts = target.split(':');
                  const id = parts[1]; const action = parts[2] || 'select';
                  const attr = action === 'edit' ? 'personEdit' : action === 'delete' ? 'personDelete' : 'personSelect';
                  node = Array.from(document.querySelectorAll('[data-person-select],[data-person-edit],[data-person-delete]')).find((item) => item.dataset[attr] === id);
                }} else if (target.startsWith('product:')) {{
                  const id = target.slice('product:'.length);
                  node = Array.from(document.querySelectorAll('[data-product-select]')).find((item) => item.dataset.productSelect === id);
                }} else if (target.startsWith('dose:')) {{
                  const parts = target.split(':');
                  const id = parts[1]; const action = parts[2];
                  const attr = action === 'taken' ? 'instanceTaken' : action === 'skipped' ? 'instanceSkipped' : 'instanceCancel';
                  node = Array.from(document.querySelectorAll('[data-instance-taken],[data-instance-skipped],[data-instance-cancel]')).find((item) => item.dataset[attr] === id);
                }} else if (target.startsWith('medication:')) {{
                  const parts = target.split(':');
                  const id = parts[1]; const action = parts[2];
                  const attr = action === 'edit' ? 'edit' : 'stop';
                  node = Array.from(document.querySelectorAll('[data-edit],[data-stop]')).find((item) => item.dataset[attr] === id);
                }}
                if (!node) return {{clicked:false,target}};
                node.click();
                return {{clicked:true,target,tag:node.tagName}};
            }})()"#
        );
        let value = self.evaluate(&expression)?;
        if value.get("clicked") != Some(&Value::Bool(true)) {
            return Err(format!("UI target is not available: {target}"));
        }
        Ok(value)
    }

    pub(super) fn set_field(&mut self, target: &str, value: &Value) -> Result<Value, String> {
        let target_json = serde_json::to_string(target).map_err(|error| error.to_string())?;
        let value_json = serde_json::to_string(value).map_err(|error| error.to_string())?;
        let expression = format!(
            r#"(() => {{
              const target = {target_json};
              const value = {value_json};
              const prescription = {{
                'dose-amount':'#pending-dose-amount', 'dose-unit':'#pending-dose-unit',
                'route':'#pending-route', 'prn':'#pending-prn', 'frequency':'#pending-frequency',
                'meal':'#pending-meal', 'prn-max':'#pending-prn-max', 'start-date':'#pending-start-date',
                'long-term':'#pending-long-term', 'days':'#pending-days'
              }};
              let node = null;
              if (target === 'search:query') node = document.querySelector('#drug-query');
              else if (target.startsWith('person:')) {{
                const field = target.slice('person:'.length);
                if (['name','birth_year','birth_month','birth_day','sex','pregnancy_status','lactation_status','notes'].includes(field)) {{
                  node = document.querySelector(`#person-form [name="${{field}}"]`);
                }}
              }} else if (target.startsWith('prescription:time:')) {{
                const index = Number(target.slice('prescription:time:'.length));
                node = document.querySelectorAll('[data-schedule-time]')[index] || null;
              }} else if (target.startsWith('prescription:')) {{
                const field = target.slice('prescription:'.length);
                node = prescription[field] ? document.querySelector(prescription[field]) : null;
              }}
              if (!node) return {{updated:false,target}};
              if (node.type === 'checkbox' || node.type === 'radio') node.checked = Boolean(value);
              else node.value = value == null ? '' : String(value);
              node.dispatchEvent(new Event('input', {{bubbles:true}}));
              node.dispatchEvent(new Event('change', {{bubbles:true}}));
              return {{updated:true,target,value: node.type === 'checkbox' ? node.checked : node.value}};
            }})()"#,
        );
        let result = self.evaluate(&expression)?;
        if result.get("updated") != Some(&Value::Bool(true)) {
            return Err(format!("UI field is not available: {target}"));
        }
        Ok(result)
    }

    pub(super) fn observe(&mut self) -> Result<Value, String> {
        self.evaluate(
            r#"(() => {
              const s = typeof state !== 'undefined' ? state : null;
              const session = s?.dashboardSession ?? null;
              const uniq = (values) => Array.from(new Set(values.filter(Boolean)));
              return {
                currentScreen: document.querySelector('.screen.active')?.dataset?.screen ?? null,
                currentPersonId: s?.currentPersonId ?? null,
                people: Array.isArray(s?.people) ? s.people.map((person) => ({id:person.id,name:person.name})) : [],
                dashboard: session ? {
                  ownerPersonId: session.ownerPersonId ?? null,
                  date: session.date ?? null,
                  phase: session.phase ?? null,
                  generation: session.generation ?? null,
                  reason: session.reason ?? null,
                  dataPersonId: session.data?.person?.id ?? null,
                  medicationIds: (session.data?.medications ?? []).map((item) => item.id)
                } : null,
                visibleMedicationActionIds: uniq(Array.from(document.querySelectorAll('[data-edit],[data-stop]')).flatMap((item) => [item.dataset.edit, item.dataset.stop])),
                actionableDoseIds: uniq(Array.from(document.querySelectorAll('[data-instance-taken],[data-instance-skipped],[data-instance-cancel]')).flatMap((item) => [item.dataset.instanceTaken, item.dataset.instanceSkipped, item.dataset.instanceCancel])),
                profileShortcutName: document.querySelector('#profile-shortcut-name')?.textContent ?? null,
                homeText: document.querySelector('#home-content')?.textContent?.replace(/\s+/g, ' ').trim() ?? null,
                toast: document.querySelector('#toast')?.textContent?.trim() ?? null,
                today: typeof todayInKorea === 'function' ? todayInKorea() : null,
                visibleSheet: Array.from(document.querySelectorAll('.bottom-sheet')).find((item) => !item.classList.contains('hidden'))?.id ?? null,
                search: {
                  query: document.querySelector('#drug-query')?.value ?? '',
                  status: document.querySelector('#search-status')?.textContent?.trim() ?? '',
                  productRefs: Array.from(document.querySelectorAll('[data-product-select]')).map((item) => item.dataset.productSelect).filter(Boolean)
                }
              };
            })()"#,
        )
    }

    pub(super) fn set_local_storage(
        &mut self,
        key: &str,
        value: Option<&str>,
    ) -> Result<(), String> {
        let key = serde_json::to_string(key).map_err(|error| error.to_string())?;
        let expression = match value {
            Some(value) => format!(
                "localStorage.setItem({key}, {}); true",
                serde_json::to_string(value).map_err(|error| error.to_string())?
            ),
            None => format!("localStorage.removeItem({key}); true"),
        };
        self.evaluate(&expression).map(|_| ())
    }

    pub(super) fn set_clock(&mut self, iso_datetime: &str) -> Result<Value, String> {
        let value = serde_json::to_string(iso_datetime).map_err(|error| error.to_string())?;
        let source = format!(
            r#"(() => {{
          const fixed = {value};
          const NativeDate = globalThis.__medicineAgentNativeDate || Date;
          globalThis.__medicineAgentNativeDate = NativeDate;
          class MedicineAgentDate extends NativeDate {{
            constructor(...args) {{ super(...(args.length ? args : [fixed])); }}
            static now() {{ return new NativeDate(fixed).getTime(); }}
          }}
          globalThis.Date = MedicineAgentDate;
          globalThis.__medicineAgentClock = fixed;
        }})();"#
        );
        self.call(
            "Page.addScriptToEvaluateOnNewDocument",
            json!({"source":source}),
        )?;
        let applied = self.evaluate(&source)?;
        let _ = self.evaluate("window.dispatchEvent(new Event('focus')); document.dispatchEvent(new Event('visibilitychange')); true")?;
        Ok(json!({"clock":iso_datetime,"applied":applied.is_null()}))
    }

    pub(super) fn screenshot(&mut self, output: &Path) -> Result<Value, String> {
        let result = self.call(
            "Page.captureScreenshot",
            json!({"format":"png","fromSurface":true}),
        )?;
        let data = result
            .pointer("/result/data")
            .and_then(Value::as_str)
            .ok_or_else(|| "Chromium screenshot response is missing data".to_owned())?;
        let bytes = base64::engine::general_purpose::STANDARD
            .decode(data)
            .map_err(|error| format!("cannot decode Chromium screenshot: {error}"))?;
        if let Some(parent) = output.parent() {
            fs::create_dir_all(parent)
                .map_err(|error| format!("cannot create screenshot directory: {error}"))?;
        }
        fs::write(output, &bytes)
            .map_err(|error| format!("cannot write screenshot {}: {error}", output.display()))?;
        Ok(json!({"path":output,"size_bytes":bytes.len()}))
    }

    pub(super) fn take_events(&mut self) -> Vec<Value> {
        std::mem::take(&mut self.events)
    }

    fn wait_document_ready(&mut self) -> Result<(), String> {
        let deadline = Instant::now() + Duration::from_secs(8);
        loop {
            let ready = self.evaluate("document.readyState")?;
            if ready == Value::String("complete".to_owned()) {
                return Ok(());
            }
            if Instant::now() >= deadline {
                return Err("Chromium document did not reach readyState=complete".to_owned());
            }
            thread::sleep(Duration::from_millis(25));
        }
    }

    fn evaluate(&mut self, expression: &str) -> Result<Value, String> {
        let response = self.call(
            "Runtime.evaluate",
            json!({"expression":expression,"returnByValue":true,"awaitPromise":true}),
        )?;
        if let Some(details) = response.pointer("/result/exceptionDetails") {
            return Err(format!("browser evaluation failed: {details}"));
        }
        Ok(response
            .pointer("/result/result/value")
            .cloned()
            .unwrap_or(Value::Null))
    }

    fn call(&mut self, method: &str, params: Value) -> Result<Value, String> {
        let id = self.next_id;
        self.next_id = self.next_id.saturating_add(1);
        self.socket
            .send(Message::Text(
                json!({"id":id,"method":method,"params":params}).to_string(),
            ))
            .map_err(|error| format!("cannot send Chromium DevTools command {method}: {error}"))?;
        loop {
            let message = self.socket.read().map_err(|error| {
                format!(
                    "cannot read Chromium DevTools response for {method}: {error}; {}",
                    read_log(&self.stderr_path)
                )
            })?;
            let Message::Text(text) = message else {
                continue;
            };
            let value: Value = serde_json::from_str(&text)
                .map_err(|error| format!("invalid Chromium DevTools JSON: {error}"))?;
            if value.get("id").and_then(Value::as_u64) == Some(id) {
                if let Some(error) = value.get("error") {
                    return Err(format!(
                        "Chromium DevTools command {method} failed: {error}"
                    ));
                }
                return Ok(value);
            }
            self.capture_event(value);
        }
    }

    fn capture_event(&mut self, value: Value) {
        let Some(method) = value.get("method").and_then(Value::as_str) else {
            return;
        };
        if matches!(
            method,
            "Runtime.exceptionThrown" | "Runtime.consoleAPICalled" | "Log.entryAdded"
        ) {
            self.events.push(value);
        }
    }
}

impl Drop for UiBrowser {
    fn drop(&mut self) {
        let _ = self.socket.close(None);
        stop_child(&mut self.child);
    }
}

fn wait_for_page(port: u16, stderr_path: &Path) -> Result<String, String> {
    let endpoint = format!("http://127.0.0.1:{port}/json/list");
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if let Ok(response) = reqwest::blocking::get(&endpoint) {
            if let Ok(text) = response.text() {
                if let Ok(value) = serde_json::from_str::<Value>(&text) {
                    if let Some(url) = value
                        .as_array()
                        .into_iter()
                        .flatten()
                        .find(|item| item.get("type").and_then(Value::as_str) == Some("page"))
                        .and_then(|item| item.get("webSocketDebuggerUrl"))
                        .and_then(Value::as_str)
                    {
                        return Ok(url.to_owned());
                    }
                }
            }
        }
        if Instant::now() >= deadline {
            return Err(format!(
                "Chromium DevTools endpoint did not become ready: {}",
                read_log(stderr_path)
            ));
        }
        thread::sleep(Duration::from_millis(50));
    }
}

fn reserve_port() -> Result<u16, String> {
    let listener = TcpListener::bind(("127.0.0.1", 0))
        .map_err(|error| format!("cannot reserve local Chromium port: {error}"))?;
    listener
        .local_addr()
        .map(|address| address.port())
        .map_err(|error| format!("cannot inspect local Chromium port: {error}"))
}

fn browser_binary() -> Result<PathBuf, String> {
    if let Some(value) = std::env::var_os("MEDICINE_CHROMIUM_BINARY") {
        return Ok(PathBuf::from(value));
    }
    let path = std::env::var_os("PATH").unwrap_or_default();
    for directory in std::env::split_paths(&path) {
        for name in ["chromium", "chromium-browser", "google-chrome"] {
            let candidate = directory.join(name);
            if candidate.is_file() {
                return Ok(candidate);
            }
        }
    }
    Err("Chromium is not installed; use the standard development image".to_owned())
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

fn read_log(path: &Path) -> String {
    fs::read_to_string(path)
        .unwrap_or_else(|_| "no Chromium diagnostics available".to_owned())
        .trim()
        .to_owned()
}
