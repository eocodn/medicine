use crate::reference_bootstrap::{
    ReferenceBootstrapCoordinator, ReferenceBootstrapSnapshot, ReferenceBootstrapState,
};
use crate::reference_manager::{
    ReferenceBootstrapPreparation, ReferenceDatabaseValidator, ReferenceManager,
    ReferenceReleaseSource, ReferenceRuntimeError, ReferenceSelection, ReferenceUpdateStatus,
};
use std::fs;
use std::sync::Mutex;

#[cfg(test)]
mod tests;

#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ReferenceRuntimeResult {
    pub selection: Option<ReferenceSelection>,
    pub snapshot: ReferenceBootstrapSnapshot,
}

pub(crate) struct ReferenceLifecycleRuntime<S, V> {
    manager: ReferenceManager<S, V>,
    bootstrap: Mutex<ReferenceBootstrapCoordinator>,
    prepared: Mutex<Option<ReferenceBootstrapPreparation>>,
}

impl<S: ReferenceReleaseSource, V: ReferenceDatabaseValidator> ReferenceLifecycleRuntime<S, V> {
    pub(crate) fn new(
        root: std::path::PathBuf,
        contract_major: i32,
        source: S,
        validator: V,
    ) -> Self {
        Self {
            manager: ReferenceManager::new(root, contract_major, source, validator),
            bootstrap: Mutex::new(ReferenceBootstrapCoordinator::checking()),
            prepared: Mutex::new(None),
        }
    }

    pub(crate) fn prepare(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        {
            let mut bootstrap = self.lock_bootstrap()?;
            if !bootstrap.begin_prepare() {
                return Ok(ReferenceRuntimeResult {
                    selection: None,
                    snapshot: bootstrap.snapshot(),
                });
            }
        }

        match self.manager.prepare_bootstrap() {
            Ok(ReferenceBootstrapPreparation::Ready(selection)) => {
                *self.lock_prepared()? = None;
                let mut bootstrap = self.lock_bootstrap()?;
                bootstrap.ready();
                Ok(ReferenceRuntimeResult {
                    selection: Some(selection),
                    snapshot: bootstrap.snapshot(),
                })
            }
            Ok(ReferenceBootstrapPreparation::Unavailable) => {
                *self.lock_prepared()? = None;
                let selection = ReferenceSelection {
                    database: None,
                    unavailable_reason: Some("update_required".to_owned()),
                };
                let mut bootstrap = self.lock_bootstrap()?;
                bootstrap.unavailable(Some("update_required"));
                Ok(ReferenceRuntimeResult {
                    selection: Some(selection),
                    snapshot: bootstrap.snapshot(),
                })
            }
            Ok(preparation @ ReferenceBootstrapPreparation::Download { .. }) => {
                let (completed, total) = match &preparation {
                    ReferenceBootstrapPreparation::Download {
                        release,
                        checkpoint_bytes,
                    } => (*checkpoint_bytes, release.full.size_bytes),
                    _ => unreachable!(),
                };
                *self.lock_prepared()? = Some(preparation);
                let mut bootstrap = self.lock_bootstrap()?;
                bootstrap.prepared_download(completed, total);
                Ok(ReferenceRuntimeResult {
                    selection: None,
                    snapshot: bootstrap.snapshot(),
                })
            }
            Err(error) => {
                self.fail(&error);
                Err(error)
            }
        }
    }

    pub(crate) fn start(&self) -> Result<ReferenceRuntimeResult, ReferenceRuntimeError> {
        let state = self.status().state;
        match state {
            ReferenceBootstrapState::Downloading | ReferenceBootstrapState::Installing => {
                return Ok(ReferenceRuntimeResult {
                    selection: None,
                    snapshot: self.status(),
                });
            }
            ReferenceBootstrapState::Ready | ReferenceBootstrapState::Unavailable => {
                return Ok(ReferenceRuntimeResult {
                    selection: None,
                    snapshot: self.status(),
                });
            }
            ReferenceBootstrapState::Checking | ReferenceBootstrapState::Failed => {
                {
                    let mut bootstrap = self.lock_bootstrap()?;
                    bootstrap.reset_for_prepare();
                }
                let retry = self.prepare()?;
                if retry.selection.is_some() {
                    return Ok(retry);
                }
                if retry.snapshot.state != ReferenceBootstrapState::DownloadRequired {
                    return Ok(retry);
                }
            }
            ReferenceBootstrapState::DownloadRequired => {}
        }

        let install_snapshot = {
            let mut bootstrap = self.lock_bootstrap()?;
            if !bootstrap.begin_install() {
                return Ok(ReferenceRuntimeResult {
                    selection: None,
                    snapshot: bootstrap.snapshot(),
                });
            }
            bootstrap.snapshot()
        };
        let preparation = {
            let mut prepared = self.lock_prepared()?;
            prepared.take()
        };
        let Some(preparation) = preparation else {
            let error = ReferenceRuntimeError::from_message(
                "reference bootstrap preparation did not produce a download",
            );
            self.fail(&error);
            return Err(error);
        };
        debug_assert_eq!(install_snapshot.state, ReferenceBootstrapState::Downloading);

        let mut phase_observer = || {
            let mut bootstrap = self.lock_bootstrap()?;
            bootstrap.installing();
            Ok(())
        };
        match self
            .manager
            .install_prepared_with_observer(preparation, &mut phase_observer)
        {
            Ok(selection) => {
                let mut bootstrap = self.lock_bootstrap()?;
                if selection.database.is_some() {
                    bootstrap.ready();
                } else {
                    bootstrap.unavailable(selection.unavailable_reason.as_deref());
                }
                Ok(ReferenceRuntimeResult {
                    selection: Some(selection),
                    snapshot: bootstrap.snapshot(),
                })
            }
            Err(error) => {
                self.fail(&error);
                Err(error)
            }
        }
    }

    pub(crate) fn status(&self) -> ReferenceBootstrapSnapshot {
        let Ok(mut bootstrap) = self.bootstrap.lock() else {
            return ReferenceBootstrapSnapshot {
                state: ReferenceBootstrapState::Failed,
                completed_bytes: 0,
                total_bytes: 0,
                detail: Some("runtime_state_unavailable".to_owned()),
            };
        };
        let current = bootstrap.snapshot();
        if current.state == ReferenceBootstrapState::Downloading && current.total_bytes > 0 {
            let completed =
                bootstrap_checkpoint_bytes(self.manager.reference_dir()).min(current.total_bytes);
            bootstrap.progress(completed, current.total_bytes);
            if completed >= current.total_bytes {
                bootstrap.installing();
            }
        }
        bootstrap.snapshot()
    }

    pub(crate) fn check_for_update(&self) -> Result<ReferenceUpdateStatus, ReferenceRuntimeError> {
        self.manager.check_for_update()
    }

    fn fail(&self, error: &ReferenceRuntimeError) {
        let Ok(mut bootstrap) = self.bootstrap.lock() else {
            return;
        };
        let message = error.to_string();
        if message.contains("insufficient storage") {
            bootstrap.failed("insufficient_storage");
        } else if let Some(detail) = stable_failure_detail(&message) {
            bootstrap.failed(&detail);
        } else {
            bootstrap.failed_for_current_phase();
        }
    }

    fn lock_bootstrap(
        &self,
    ) -> Result<std::sync::MutexGuard<'_, ReferenceBootstrapCoordinator>, ReferenceRuntimeError>
    {
        self.bootstrap.lock().map_err(|_| {
            ReferenceRuntimeError::from_message("reference bootstrap state is unavailable")
        })
    }

    fn lock_prepared(
        &self,
    ) -> Result<
        std::sync::MutexGuard<'_, Option<ReferenceBootstrapPreparation>>,
        ReferenceRuntimeError,
    > {
        self.prepared.lock().map_err(|_| {
            ReferenceRuntimeError::from_message("reference bootstrap preparation is unavailable")
        })
    }
}

fn stable_failure_detail(message: &str) -> Option<String> {
    if let Some(status) = message.strip_prefix("manifest_http_") {
        let code = status
            .bytes()
            .take_while(u8::is_ascii_digit)
            .map(char::from)
            .collect::<String>();
        if !code.is_empty() {
            return Some(format!("manifest_http_{code}"));
        }
    }
    for detail in [
        "manifest_json",
        "manifest_signature",
        "manifest_release",
        "network_failed",
    ] {
        if message.starts_with(detail) {
            return Some(detail.to_owned());
        }
    }
    None
}

fn bootstrap_checkpoint_bytes(reference_dir: &std::path::Path) -> u64 {
    fs::read_dir(reference_dir)
        .ok()
        .into_iter()
        .flatten()
        .filter_map(Result::ok)
        .filter(|entry| {
            let name = entry.file_name();
            let name = name.to_string_lossy();
            name.starts_with(".bootstrap-artifact-") && name.ends_with(".part")
        })
        .filter_map(|entry| entry.metadata().ok().map(|metadata| metadata.len()))
        .max()
        .unwrap_or(0)
}
