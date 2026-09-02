use rusqlite::{Connection, OpenFlags};
#[cfg(feature = "agentctl")]
use serde::Serialize;
use serde_json::json;
use std::path::{Path, PathBuf};
#[cfg(feature = "agentctl")]
use std::time::Instant;

use crate::{
    dashboard, doses, medication_list, medications, ocr_medication_candidates, people, personal_schema,
    planning, preview, prn, product_search, reference_capabilities,
};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccessClass {
    Reference,
    PersonalRead,
    PersonalWrite,
}

#[cfg(feature = "agentctl")]
#[derive(Clone, Debug, Serialize)]
pub struct RequestObservation {
    pub method: String,
    pub path: String,
    pub access: &'static str,
    pub status: u64,
    pub elapsed_ms: u128,
}

impl AccessClass {
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Reference => "reference",
            Self::PersonalRead => "personal_read",
            Self::PersonalWrite => "personal_write",
        }
    }
}

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
struct RequestPolicy {
    access: AccessClass,
    requires_reference: bool,
}

impl RequestPolicy {
    const fn new(access: AccessClass, requires_reference: bool) -> Self {
        Self {
            access,
            requires_reference,
        }
    }
}

pub struct MedicineEngine {
    canonical_db: Option<PathBuf>,
    personal_db: Option<PathBuf>,
    reference_available: bool,
    reference_status: Option<String>,
}

impl MedicineEngine {
    pub fn new(
        canonical_db: Option<&Path>,
        personal_db: Option<&Path>,
        reference_unavailable_reason: Option<&str>,
    ) -> Self {
        let reference_available = canonical_db.is_some();
        Self {
            canonical_db: canonical_db.map(Path::to_path_buf),
            personal_db: personal_db.map(Path::to_path_buf),
            reference_available,
            reference_status: if reference_available {
                None
            } else {
                Some(
                    reference_unavailable_reason
                        .unwrap_or("unavailable")
                        .to_owned(),
                )
            },
        }
    }

    pub fn set_reference_available(
        &mut self,
        available: bool,
        reason: Option<&str>,
    ) -> Result<(), &'static str> {
        if available && self.canonical_db.is_none() {
            return Err("reference database is unavailable");
        }
        self.reference_available = available;
        self.reference_status = if available {
            None
        } else {
            Some(reason.unwrap_or("unavailable").to_owned())
        };
        Ok(())
    }

    pub fn initialize_personal_database(&self) -> Result<(), String> {
        let path = self
            .personal_db
            .as_deref()
            .ok_or_else(|| "personal database is unavailable".to_owned())?;
        personal_schema::initialize(path).map_err(|error| error.to_string())
    }

    pub fn prepare_personal_database_for_seal(&self) -> Result<(), String> {
        let path = self
            .personal_db
            .as_deref()
            .ok_or_else(|| "personal database is unavailable".to_owned())?;
        personal_schema::checkpoint(path).map_err(|error| error.to_string())
    }

    pub fn request_access(&self, method: &str, raw_path: &str) -> AccessClass {
        classify_request(method, raw_path).access
    }

    pub fn handles_request(&self, method: &str, raw_path: &str) -> bool {
        let path = request_path(raw_path);
        (normalized_method(method) == "GET" && path == "/api/health")
            || product_search::handles_request(method, raw_path)
            || ocr_medication_candidates::handles_request(method, path)
            || people::handles_request(method, path)
            || medication_list::handles_request(method, path)
            || dashboard::handles_request(method, path)
            || planning::handles_request(method, path)
            || preview::handles_request(method, path)
            || medications::handles_request(method, path)
            || prn::handles_request(method, path)
            || doses::handles_request(method, path)
    }

    pub fn request(&self, method: &str, raw_path: &str, body_json: &str) -> String {
        let path = request_path(raw_path);
        let policy = classify_request(method, raw_path);
        if policy.requires_reference
            && !self.reference_available
            && self.handles_request(method, raw_path)
        {
            return json!({
                "status": 503,
                "body": {
                    "detail": "reference data unavailable; app update required",
                    "reference_status": self.reference_status.as_deref().unwrap_or("unavailable"),
                }
            })
            .to_string();
        }
        if normalized_method(method) == "GET" && path == "/api/health" {
            return self.health_response();
        }
        if let Some((status, body)) =
            product_search::handle_request(self.canonical_db.as_deref(), method, raw_path)
        {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) = ocr_medication_candidates::handle_request(
            self.canonical_db.as_deref(), method, path, body_json,
        ) {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) =
            people::handle_request(self.personal_db.as_deref(), method, path, body_json)
        {
            return json!({"status": status, "body": body}).to_string();
        }
        let active_reference = self
            .reference_available
            .then_some(self.canonical_db.as_deref())
            .flatten();
        if let Some((status, body)) = medication_list::handle_request(
            active_reference,
            self.personal_db.as_deref(),
            method,
            raw_path,
            path,
        ) {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) = dashboard::handle_request(
            active_reference,
            self.personal_db.as_deref(),
            method,
            raw_path,
            path,
        ) {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) =
            planning::handle_request(self.personal_db.as_deref(), method, raw_path, path)
        {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) = preview::handle_request(
            self.canonical_db.as_deref(),
            self.personal_db.as_deref(),
            method,
            path,
            body_json,
        ) {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) = medications::handle_request(
            self.canonical_db.as_deref(),
            self.personal_db.as_deref(),
            method,
            raw_path,
            path,
            body_json,
        ) {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) =
            prn::handle_request(self.personal_db.as_deref(), method, path, body_json)
        {
            return json!({"status": status, "body": body}).to_string();
        }
        if let Some((status, body)) =
            doses::handle_request(self.personal_db.as_deref(), method, path, body_json)
        {
            return json!({"status": status, "body": body}).to_string();
        }
        json!({"status": 404, "body": {"detail": "route not found"}}).to_string()
    }

    #[cfg(feature = "agentctl")]
    pub fn request_with_observation(
        &self,
        method: &str,
        raw_path: &str,
        body_json: &str,
    ) -> (String, RequestObservation) {
        let started = Instant::now();
        let response = self.request(method, raw_path, body_json);
        let status = serde_json::from_str::<serde_json::Value>(&response)
            .ok()
            .and_then(|value| value.get("status").and_then(serde_json::Value::as_u64))
            .unwrap_or(500);
        (
            response,
            RequestObservation {
                method: normalized_method(method).to_owned(),
                path: request_path(raw_path).to_owned(),
                access: self.request_access(method, raw_path).as_str(),
                status,
                elapsed_ms: started.elapsed().as_millis(),
            },
        )
    }

    fn health_response(&self) -> String {
        let full_catalog = self.reference_available && self.has_full_catalog();
        json!({
            "status": 200,
            "body": {
                "ok": true,
                "full_catalog": full_catalog,
                "reference_available": self.reference_available,
                "reference_status": self.reference_status,
            }
        })
        .to_string()
    }

    fn has_full_catalog(&self) -> bool {
        let Some(path) = self.canonical_db.as_deref() else {
            return false;
        };
        let flags = OpenFlags::SQLITE_OPEN_READ_ONLY | OpenFlags::SQLITE_OPEN_NO_MUTEX;
        let Ok(con) = Connection::open_with_flags(path, flags) else {
            return false;
        };
        let has_products = con
            .query_row("SELECT EXISTS(SELECT 1 FROM products LIMIT 1)", [], |row| {
                row.get::<_, i64>(0)
            })
            .map(|value| value != 0)
            .unwrap_or(false);
        has_products && reference_capabilities::verify_product_search_schema(&con).is_ok()
    }
}

fn classify_request(method: &str, raw_path: &str) -> RequestPolicy {
    let method = normalized_method(method);
    let path = request_path(raw_path);

    if method == "GET" && path == "/api/health" {
        return RequestPolicy::new(AccessClass::Reference, false);
    }
    if method == "GET" && path == "/api/products" {
        return RequestPolicy::new(AccessClass::Reference, true);
    }
    if method == "POST" && path == "/api/products/ocr-candidates" {
        return RequestPolicy::new(AccessClass::Reference, true);
    }
    if method == "GET" && path == "/api/people" {
        return RequestPolicy::new(AccessClass::PersonalRead, false);
    }
    if method == "GET" && single_segment_route(path, "/api/medications/", "/history") {
        return RequestPolicy::new(AccessClass::PersonalRead, false);
    }
    if method == "GET" && single_segment_route(path, "/api/people/", "/medications") {
        return RequestPolicy::new(AccessClass::PersonalRead, false);
    }
    if method == "POST" && single_segment_route(path, "/api/people/", "/medications/preview") {
        return RequestPolicy::new(AccessClass::PersonalRead, true);
    }
    if method == "POST" && single_segment_route(path, "/api/people/", "/medications") {
        return RequestPolicy::new(AccessClass::PersonalWrite, true);
    }
    if method == "PATCH" && single_segment_route(path, "/api/medications/", "") {
        return RequestPolicy::new(AccessClass::PersonalWrite, true);
    }

    RequestPolicy::new(AccessClass::PersonalWrite, false)
}

fn normalized_method(method: &str) -> String {
    method.trim().to_ascii_uppercase()
}

fn request_path(raw_path: &str) -> &str {
    let before_query = raw_path.split_once('?').map_or(raw_path, |(path, _)| path);
    before_query
        .split_once('#')
        .map_or(before_query, |(path, _)| path)
}

fn single_segment_route(path: &str, prefix: &str, suffix: &str) -> bool {
    let Some(rest) = path.strip_prefix(prefix) else {
        return false;
    };
    let Some(segment) = rest.strip_suffix(suffix) else {
        return false;
    };
    !segment.is_empty() && !segment.contains('/')
}
