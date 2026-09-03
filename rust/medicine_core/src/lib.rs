mod assessment_runtime;
mod assessment_token;
mod canonical_products;
mod current_products;
mod dashboard;
mod dose_logs;
mod dose_quantity;
mod doses;
mod dur_display;
mod dur_display_support;
mod dur_product_flags;
mod dur_quantitative_display;
mod engine;
mod interaction_safety;
mod interaction_timing;
mod jni_bridge;
mod medication_create;
mod medication_list;
mod medication_records;
mod medication_update;
mod medications;
mod ocr_medication_candidates;
mod people;
mod personal_db;
mod personal_schema;
mod planning;
mod planning_medications;
mod prescriptions;
mod preview;
mod prn;
mod product_search;
mod profile_age;
mod profile_safety;
mod quantitative_safety;
pub mod reference_artifacts;
mod reference_bootstrap;
mod reference_capabilities;
mod reference_channel;
mod reference_contract;
mod reference_db_verifier;
mod reference_http;
mod reference_lifecycle;
mod reference_lifecycle_runtime;
mod reference_manager;
mod reference_queries;
mod reference_runtime;
mod reference_semantics;
mod reference_signature;
pub mod reference_state;
mod reference_trust;
mod regimen_review;
mod reminders;
mod safety_basis;
mod safety_time;
#[cfg(feature = "web")]
pub mod web;

#[cfg(feature = "agentctl")]
pub use engine::RequestObservation;
pub use engine::{AccessClass, MedicineEngine};
pub use reference_bootstrap::{ReferenceBootstrapSnapshot, ReferenceBootstrapState};
pub use reference_capabilities::{
    verify_reference_runtime_capabilities, ReferenceRuntimeCapabilityError,
};
pub use reference_channel::{open_reference_channel, ReferenceChannelConfig};
pub use reference_contract::REFERENCE_CONTRACT_MAJOR;
pub use reference_db_verifier::{
    verify_reference_database, ReferenceVerificationError, ReferenceVerificationReport,
};
pub use reference_lifecycle_runtime::ReferenceRuntimeResult;
pub use reference_manager::{ReferenceRuntimeError, ReferenceSelection, ReferenceUpdateStatus};
pub use reference_runtime::ReferenceRuntime;
pub use reference_signature::{
    ReferenceArtifactKind, ReferenceManifestVerifier, ReferenceReleaseArtifact,
    ReferenceReleaseProtocolV2, ReferenceRootSelection, ReferenceSignatureError, TrustedSigningKey,
    VerifiedReferenceManifestSignature, VerifiedReferenceRelease,
};
pub use reference_trust::{
    load_reference_trust_manifest, parse_reference_trust_manifest_json, ReferenceTrustError,
    ReferenceTrustManifest,
};
pub const PERSONAL_SCHEMA_VERSION: i64 = personal_schema::SCHEMA_VERSION;

pub fn initialize_personal_db(path: &std::path::Path) -> Result<(), String> {
    personal_schema::initialize(path).map_err(|error| error.to_string())
}

pub fn checkpoint_personal_db(path: &std::path::Path) -> Result<(), String> {
    personal_schema::checkpoint(path).map_err(|error| error.to_string())
}

pub fn inspect_product(canonical_db: Option<&std::path::Path>, product_ref: &str) -> String {
    let (status, body) = canonical_products::inspect(canonical_db, product_ref);
    serde_json::json!({"status": status, "body": body}).to_string()
}

pub fn normalize_prescription_draft(
    body_json: &str,
    person_id: Option<&str>,
    product_ref: Option<&str>,
) -> String {
    let (status, body) = prescriptions::inspect(body_json, person_id, product_ref);
    serde_json::json!({"status": status, "body": body}).to_string()
}

pub fn inspect_safety_basis(
    canonical_db: Option<&std::path::Path>,
    product_ref: &str,
    person_json: &str,
    draft_json: &str,
) -> String {
    let (status, body) = safety_basis::inspect(canonical_db, product_ref, person_json, draft_json);
    serde_json::json!({"status": status, "body": body}).to_string()
}

pub fn assemble_dur_display(input_json: &str) -> String {
    let (status, body) = dur_display::inspect(input_json);
    serde_json::json!({"status": status, "body": body}).to_string()
}

pub fn inspect_profile_risks(
    canonical_db: Option<&std::path::Path>,
    product_ref: &str,
    person_json: &str,
    candidate_course_json: &str,
    as_of: Option<&str>,
) -> String {
    let (status, body) = profile_safety::inspect(
        canonical_db,
        product_ref,
        person_json,
        candidate_course_json,
        as_of,
    );
    serde_json::json!({"status": status, "body": body}).to_string()
}

pub fn inspect_interaction_risks(
    canonical_db: Option<&std::path::Path>,
    product_ref: &str,
    current_json: &str,
    candidate_course_json: &str,
) -> String {
    let (status, body) = interaction_safety::inspect(
        canonical_db,
        product_ref,
        current_json,
        candidate_course_json,
    );
    serde_json::json!({"status": status, "body": body}).to_string()
}
