from __future__ import annotations


SUBSTANCE_SCHEMA_VERSION = "3"


SUBSTANCE_SCHEMA = r"""
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA foreign_keys = ON;

CREATE TABLE substance_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE source_snapshots (
    dataset_key TEXT PRIMARY KEY,
    source_family TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    effective_date TEXT,
    fetched_at TEXT,
    row_count INTEGER NOT NULL,
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_substance_source_family ON source_snapshots(source_family);

CREATE TABLE substances (
    substance_id TEXT PRIMARY KEY,
    canonical_name TEXT NOT NULL,
    identity_status TEXT NOT NULL CHECK(
        identity_status IN (
            'resolved_external_exact',
            'resolved_external_structured',
            'resolved_source_relation',
            'local_exact_unsolved'
        )
    )
);
CREATE INDEX idx_substances_status ON substances(identity_status);

CREATE TABLE substance_names (
    normalized_name TEXT PRIMARY KEY,
    substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    representative_name TEXT NOT NULL
);
CREATE INDEX idx_substance_names_substance ON substance_names(substance_id);

CREATE TABLE source_identities (
    id INTEGER PRIMARY KEY,
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_scope TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    ingredient_code TEXT,
    name_en TEXT,
    name_ko TEXT,
    normalized_name TEXT NOT NULL,
    occurrence_count INTEGER NOT NULL CHECK(occurrence_count > 0),
    substance_id TEXT NOT NULL REFERENCES substances(substance_id)
);
CREATE INDEX idx_source_identities_substance ON source_identities(substance_id);
CREATE INDEX idx_source_identities_scope ON source_identities(source_scope);
CREATE INDEX idx_source_identities_code ON source_identities(ingredient_code);
CREATE INDEX idx_source_identities_name ON source_identities(normalized_name);

CREATE TABLE source_unparsed_expressions (
    id INTEGER PRIMARY KEY,
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_scope TEXT NOT NULL,
    source_row INTEGER NOT NULL,
    raw_text TEXT NOT NULL,
    reason TEXT NOT NULL CHECK(reason='ambiguous_composition_delimiter'),
    UNIQUE(source_dataset_key,source_scope,source_row)
);
CREATE INDEX idx_source_unparsed_scope ON source_unparsed_expressions(source_scope);

CREATE TABLE substance_match_candidates (
    substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    normalized_name TEXT NOT NULL REFERENCES substance_names(normalized_name),
    system TEXT NOT NULL,
    value TEXT NOT NULL,
    external_name TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK(match_method IN ('normalized_name_exact','source_wrapper_exact','source_declared_alias','typography_greek','typography_apostrophe','typography_isotope')),
    evidence_source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    selected INTEGER NOT NULL CHECK(selected IN (0,1)),
    PRIMARY KEY(normalized_name,system,value)
) WITHOUT ROWID;
CREATE INDEX idx_substance_candidates_value ON substance_match_candidates(system,value);

CREATE TABLE substance_identifiers (
    substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    system TEXT NOT NULL,
    value TEXT NOT NULL,
    evidence_source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    match_method TEXT NOT NULL CHECK(match_method IN ('normalized_name_exact','source_wrapper_exact','source_declared_alias','typography_greek','typography_apostrophe','typography_isotope')),
    PRIMARY KEY(substance_id,system),
    UNIQUE(system,value)
) WITHOUT ROWID;
CREATE INDEX idx_substance_identifiers_value ON substance_identifiers(system,value);

CREATE TABLE substance_unsolved (
    substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    reason TEXT NOT NULL CHECK(
        reason IN ('external_exact_no_match','external_exact_multiple_matches')
    ),
    detail_json TEXT NOT NULL,
    PRIMARY KEY(substance_id,reason)
) WITHOUT ROWID;
CREATE INDEX idx_substance_unsolved_reason ON substance_unsolved(reason);

-- Source-declared physical/formulation relations may be added only when an
-- exact-resolved local base substance exists. Parent/active-moiety, salt,
-- ester, hydrate and equivalence edges still require authoritative relationship
-- evidence; generic suffix stripping never creates those chemical relations.
CREATE TABLE substance_relations (
    subject_substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    relation_type TEXT NOT NULL CHECK(
        relation_type IN (
            'active_moiety_of','salt_of','ester_of','hydrate_of','equivalent_to',
            'physical_form_of','formulation_of'
        )
    ),
    object_substance_id TEXT NOT NULL REFERENCES substances(substance_id),
    evidence_source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    evidence_json TEXT NOT NULL,
    PRIMARY KEY(subject_substance_id,relation_type,object_substance_id,evidence_source_dataset_key)
) WITHOUT ROWID;
"""