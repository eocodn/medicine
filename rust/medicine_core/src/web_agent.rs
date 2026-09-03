#![cfg(feature = "agentctl-web")]

use axum::http::StatusCode;
use serde_json::{json, Value};
use std::sync::{Arc, Mutex};

#[derive(Clone, Default)]
pub(crate) struct AgentFaultController {
    inner: Arc<Mutex<Vec<AgentFaultRule>>>,
}

struct AgentFaultRule {
    id: String,
    method: String,
    path: String,
    remaining: u64,
    matched: u64,
    waiting: u64,
    action: AgentFaultAction,
}

enum AgentFaultAction {
    Fail { status: StatusCode, body: Value },
    Delay { delay_ms: u64 },
    Gate { notify: Arc<tokio::sync::Notify> },
}

pub(crate) enum AgentFaultEffect {
    Fail {
        status: StatusCode,
        body: Value,
    },
    Delay {
        delay_ms: u64,
    },
    Gate {
        id: String,
        notify: Arc<tokio::sync::Notify>,
    },
}

impl AgentFaultController {
    pub(crate) fn install(&self, value: Value) -> Result<Value, String> {
        let object = value
            .as_object()
            .ok_or_else(|| "fault rule must be an object".to_owned())?;
        let id = required_string(object.get("id"), "fault id")?;
        let method = required_string(object.get("method"), "fault method")?.to_ascii_uppercase();
        let path = required_string(object.get("path"), "fault path")?;
        if !path.starts_with('/') {
            return Err("fault path must be absolute".to_owned());
        }
        let remaining = object.get("times").and_then(Value::as_u64).unwrap_or(1);
        if remaining == 0 {
            return Err("fault times must be positive".to_owned());
        }
        let action_name = required_string(object.get("action"), "fault action")?;
        let action = match action_name.as_str() {
            "fail" => {
                let status = object.get("status").and_then(Value::as_u64).unwrap_or(500);
                let status = u16::try_from(status)
                    .ok()
                    .and_then(|value| StatusCode::from_u16(value).ok())
                    .ok_or_else(|| "fault status must be a valid HTTP status".to_owned())?;
                let body = object
                    .get("body")
                    .cloned()
                    .unwrap_or_else(|| json!({"detail":"agentctl injected failure"}));
                AgentFaultAction::Fail { status, body }
            }
            "delay" => {
                let delay_ms = object
                    .get("delay_ms")
                    .and_then(Value::as_u64)
                    .ok_or_else(|| "delay fault requires delay_ms".to_owned())?;
                AgentFaultAction::Delay { delay_ms }
            }
            "gate" => AgentFaultAction::Gate {
                notify: Arc::new(tokio::sync::Notify::new()),
            },
            _ => return Err("fault action must be fail, delay, or gate".to_owned()),
        };
        let mut rules = self
            .inner
            .lock()
            .map_err(|_| "fault controller state is unavailable".to_owned())?;
        if rules.iter().any(|rule| rule.id == id) {
            return Err(format!("fault id is duplicated: {id}"));
        }
        rules.push(AgentFaultRule {
            id: id.clone(),
            method,
            path,
            remaining,
            matched: 0,
            waiting: 0,
            action,
        });
        Ok(json!({"id":id,"installed":true}))
    }

    pub(crate) fn effect(
        &self,
        method: &str,
        path: &str,
    ) -> Result<Option<AgentFaultEffect>, String> {
        let mut rules = self
            .inner
            .lock()
            .map_err(|_| "fault controller state is unavailable".to_owned())?;
        let Some(rule) = rules.iter_mut().find(|rule| {
            rule.remaining > 0 && rule.method == method && path_matches(&rule.path, path)
        }) else {
            return Ok(None);
        };
        rule.remaining -= 1;
        rule.matched += 1;
        let effect = match &rule.action {
            AgentFaultAction::Fail { status, body } => AgentFaultEffect::Fail {
                status: *status,
                body: body.clone(),
            },
            AgentFaultAction::Delay { delay_ms } => AgentFaultEffect::Delay {
                delay_ms: *delay_ms,
            },
            AgentFaultAction::Gate { notify } => {
                rule.waiting += 1;
                AgentFaultEffect::Gate {
                    id: rule.id.clone(),
                    notify: Arc::clone(notify),
                }
            }
        };
        Ok(Some(effect))
    }

    pub(crate) fn released(&self, id: &str) -> Result<(), String> {
        let mut rules = self
            .inner
            .lock()
            .map_err(|_| "fault controller state is unavailable".to_owned())?;
        let rule = rules
            .iter_mut()
            .find(|rule| rule.id == id)
            .ok_or_else(|| format!("unknown fault id: {id}"))?;
        if rule.waiting == 0 {
            return Err(format!("fault gate is not waiting: {id}"));
        }
        let AgentFaultAction::Gate { notify } = &rule.action else {
            return Err(format!("fault is not a gate: {id}"));
        };
        rule.waiting -= 1;
        notify.notify_one();
        Ok(())
    }

    pub(crate) fn snapshot(&self, id: &str) -> Result<Value, String> {
        let rules = self
            .inner
            .lock()
            .map_err(|_| "fault controller state is unavailable".to_owned())?;
        let rule = rules
            .iter()
            .find(|rule| rule.id == id)
            .ok_or_else(|| format!("unknown fault id: {id}"))?;
        Ok(
            json!({"id":rule.id,"remaining":rule.remaining,"matched":rule.matched,"waiting":rule.waiting}),
        )
    }
}

fn required_string(value: Option<&Value>, label: &str) -> Result<String, String> {
    value
        .and_then(Value::as_str)
        .filter(|value| !value.trim().is_empty())
        .map(str::to_owned)
        .ok_or_else(|| format!("{label} is required"))
}

fn path_matches(pattern: &str, value: &str) -> bool {
    let mut parts = pattern.split('*');
    let first = parts.next().unwrap_or_default();
    if !value.starts_with(first) {
        return false;
    }
    let mut offset = first.len();
    let saw_wildcard = pattern.contains('*');
    for part in parts {
        if part.is_empty() {
            continue;
        }
        let Some(index) = value[offset..].find(part) else {
            return false;
        };
        offset += index + part.len();
    }
    if !saw_wildcard {
        value == pattern
    } else if pattern.ends_with('*') {
        true
    } else {
        pattern
            .rsplit('*')
            .next()
            .is_some_and(|last| value.ends_with(last))
    }
}

#[cfg(test)]
mod tests {
    use super::path_matches;

    #[test]
    fn wildcard_paths_match_dynamic_segments_without_crossing_order() {
        assert!(path_matches(
            "/api/people/*/dashboard",
            "/api/people/person-1/dashboard"
        ));
        assert!(path_matches(
            "/api/*/medications/*",
            "/api/people/p1/medications/m1"
        ));
        assert!(!path_matches(
            "/api/people/*/dashboard",
            "/api/people/person-1/medications"
        ));
        assert!(!path_matches("/api/*/dashboard", "/api/dashboard/people"));
    }
}
