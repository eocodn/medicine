use rusqlite::{Connection, OpenFlags};
use serde_json::json;
use std::path::{Path, PathBuf};

#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub enum AccessClass {
    Reference,
    PersonalRead,
    PersonalWrite,
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
    reference_available: bool,
    reference_status: Option<String>,
}

impl MedicineEngine {
    pub fn new(canonical_db: Option<&Path>, reference_unavailable_reason: Option<&str>) -> Self {
        let reference_available = canonical_db.is_some();
        Self {
            canonical_db: canonical_db.map(Path::to_path_buf),
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

    pub fn request_access(&self, method: &str, raw_path: &str) -> AccessClass {
        classify_request(method, raw_path).access
    }

    pub fn handles_request(&self, method: &str, raw_path: &str) -> bool {
        normalized_method(method) == "GET" && request_path(raw_path) == "/api/health"
    }

    pub fn request(&self, method: &str, raw_path: &str, _body_json: &str) -> String {
        if self.handles_request(method, raw_path) {
            return self.health_response();
        }
        json!({"status": 404, "body": {"detail": "route not found"}}).to_string()
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
        con.query_row("SELECT EXISTS(SELECT 1 FROM products LIMIT 1)", [], |row| {
            row.get::<_, i64>(0)
        })
        .map(|value| value != 0)
        .unwrap_or(false)
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
    if method == "GET" && path == "/api/people" {
        return RequestPolicy::new(AccessClass::PersonalRead, false);
    }
    if method == "GET" && single_segment_route(path, "/api/medications/", "/history") {
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
