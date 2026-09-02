use serde::Serialize;

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize)]
#[serde(rename_all = "snake_case")]
pub enum ReferenceBootstrapState {
    Checking,
    DownloadRequired,
    Downloading,
    Installing,
    Ready,
    Unavailable,
    Failed,
}

#[derive(Clone, Debug, PartialEq, Eq, Serialize)]
pub struct ReferenceBootstrapSnapshot {
    pub state: ReferenceBootstrapState,
    pub completed_bytes: u64,
    pub total_bytes: u64,
    pub detail: Option<String>,
}

#[derive(Clone, Debug)]
pub struct ReferenceBootstrapCoordinator {
    snapshot: ReferenceBootstrapSnapshot,
    initialized: bool,
    operation_running: bool,
}

impl ReferenceBootstrapCoordinator {
    pub fn checking() -> Self {
        Self {
            snapshot: ReferenceBootstrapSnapshot {
                state: ReferenceBootstrapState::Checking,
                completed_bytes: 0,
                total_bytes: 0,
                detail: None,
            },
            initialized: false,
            operation_running: false,
        }
    }

    pub fn ready_initial() -> Self {
        Self {
            snapshot: ReferenceBootstrapSnapshot {
                state: ReferenceBootstrapState::Ready,
                completed_bytes: 0,
                total_bytes: 0,
                detail: None,
            },
            initialized: true,
            operation_running: false,
        }
    }

    pub fn snapshot(&self) -> ReferenceBootstrapSnapshot {
        self.snapshot.clone()
    }

    pub fn begin_prepare(&mut self) -> bool {
        if self.initialized || self.operation_running {
            return false;
        }
        self.operation_running = true;
        self.snapshot.state = ReferenceBootstrapState::Checking;
        self.snapshot.detail = None;
        true
    }

    pub fn reset_for_prepare(&mut self) {
        if self.operation_running {
            return;
        }
        self.initialized = false;
        self.snapshot.state = ReferenceBootstrapState::Checking;
        self.snapshot.detail = None;
    }

    pub fn prepared_download(&mut self, completed_bytes: u64, total_bytes: u64) {
        self.initialized = true;
        self.operation_running = false;
        self.set_progress(
            ReferenceBootstrapState::DownloadRequired,
            completed_bytes,
            total_bytes,
            None,
        );
    }

    pub fn unavailable(&mut self, detail: Option<&str>) {
        self.initialized = true;
        self.operation_running = false;
        self.snapshot.state = ReferenceBootstrapState::Unavailable;
        self.snapshot.detail = detail.map(str::to_owned);
    }

    pub fn begin_install(&mut self) -> bool {
        if self.operation_running
            || !matches!(
                self.snapshot.state,
                ReferenceBootstrapState::DownloadRequired | ReferenceBootstrapState::Failed
            )
        {
            return false;
        }
        self.operation_running = true;
        self.snapshot.state = ReferenceBootstrapState::Downloading;
        self.snapshot.detail = None;
        true
    }

    pub fn checking_phase(&mut self) {
        self.snapshot.state = ReferenceBootstrapState::Checking;
    }

    pub fn progress(&mut self, completed_bytes: u64, total_bytes: u64) {
        self.set_progress(
            ReferenceBootstrapState::Downloading,
            completed_bytes,
            total_bytes,
            None,
        );
    }

    pub fn installing(&mut self) {
        self.snapshot.state = ReferenceBootstrapState::Installing;
        self.snapshot.detail = None;
    }

    pub fn ready(&mut self) {
        self.initialized = true;
        self.operation_running = false;
        self.snapshot.state = ReferenceBootstrapState::Ready;
        if self.snapshot.total_bytes > 0 {
            self.snapshot.completed_bytes = self.snapshot.total_bytes;
        }
        self.snapshot.detail = None;
    }

    pub fn failed(&mut self, detail: &str) {
        self.initialized = true;
        self.operation_running = false;
        self.snapshot.state = ReferenceBootstrapState::Failed;
        self.snapshot.detail = Some(detail.to_owned());
    }

    pub fn failed_for_current_phase(&mut self) {
        let detail = match self.snapshot.state {
            ReferenceBootstrapState::Checking => "manifest_failed",
            ReferenceBootstrapState::DownloadRequired | ReferenceBootstrapState::Downloading => {
                "download_failed"
            }
            ReferenceBootstrapState::Installing => "install_failed",
            _ => "bootstrap_failed",
        };
        self.failed(detail);
    }

    fn set_progress(
        &mut self,
        state: ReferenceBootstrapState,
        completed_bytes: u64,
        total_bytes: u64,
        detail: Option<&str>,
    ) {
        self.snapshot.state = state;
        self.snapshot.completed_bytes = completed_bytes.min(total_bytes);
        self.snapshot.total_bytes = total_bytes;
        self.snapshot.detail = detail.map(str::to_owned);
    }
}