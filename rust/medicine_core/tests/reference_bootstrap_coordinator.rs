use medicine_core::{ReferenceBootstrapCoordinator, ReferenceBootstrapState};

#[test]
fn bootstrap_coordinator_owns_shared_first_install_transitions() {
    let mut coordinator = ReferenceBootstrapCoordinator::checking();

    assert_eq!(coordinator.snapshot().state, ReferenceBootstrapState::Checking);
    assert!(coordinator.begin_prepare());
    assert!(!coordinator.begin_prepare());

    coordinator.prepared_download(25, 100);
    let snapshot = coordinator.snapshot();
    assert_eq!(snapshot.state, ReferenceBootstrapState::DownloadRequired);
    assert_eq!(snapshot.completed_bytes, 25);
    assert_eq!(snapshot.total_bytes, 100);

    assert!(coordinator.begin_install());
    assert!(!coordinator.begin_install());
    coordinator.progress(60, 100);
    coordinator.installing();
    assert_eq!(coordinator.snapshot().state, ReferenceBootstrapState::Installing);

    coordinator.ready();
    let snapshot = coordinator.snapshot();
    assert_eq!(snapshot.state, ReferenceBootstrapState::Ready);
    assert_eq!(snapshot.completed_bytes, 100);
    assert_eq!(snapshot.total_bytes, 100);
}

#[test]
fn bootstrap_coordinator_preserves_failure_progress_and_allows_retry() {
    let mut coordinator = ReferenceBootstrapCoordinator::checking();
    assert!(coordinator.begin_prepare());
    coordinator.prepared_download(40, 100);
    assert!(coordinator.begin_install());
    coordinator.progress(55, 100);
    coordinator.failed("insufficient_storage");

    let snapshot = coordinator.snapshot();
    assert_eq!(snapshot.state, ReferenceBootstrapState::Failed);
    assert_eq!(snapshot.completed_bytes, 55);
    assert_eq!(snapshot.total_bytes, 100);
    assert_eq!(snapshot.detail.as_deref(), Some("insufficient_storage"));
    assert!(coordinator.begin_install());
}

#[test]
fn bootstrap_coordinator_classifies_failure_from_authoritative_phase() {
    let mut coordinator = ReferenceBootstrapCoordinator::checking();
    assert!(coordinator.begin_prepare());
    coordinator.failed_for_current_phase();
    assert_eq!(coordinator.snapshot().detail.as_deref(), Some("manifest_failed"));

    coordinator.reset_for_prepare();
    assert!(coordinator.begin_prepare());
    coordinator.prepared_download(0, 100);
    assert!(coordinator.begin_install());
    coordinator.failed_for_current_phase();
    assert_eq!(coordinator.snapshot().detail.as_deref(), Some("download_failed"));

    assert!(coordinator.begin_install());
    coordinator.installing();
    coordinator.failed_for_current_phase();
    assert_eq!(coordinator.snapshot().detail.as_deref(), Some("install_failed"));
}