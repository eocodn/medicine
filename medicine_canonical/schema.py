from __future__ import annotations

SCHEMA_VERSION = "3"
CORE_SOURCE_FAMILIES = frozenset({"mfds_permit_api", "mfds_dur_item_api", "kids_mfds_xlsx"})

SCHEMA = r"""
PRAGMA journal_mode = DELETE;
PRAGMA synchronous = NORMAL;
PRAGMA temp_store = MEMORY;
PRAGMA foreign_keys = ON;

CREATE TABLE canonical_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE source_snapshots (
    dataset_key TEXT PRIMARY KEY,
    source_family TEXT NOT NULL,
    source_locator TEXT NOT NULL,
    snapshot_path TEXT NOT NULL,
    title TEXT,
    effective_date TEXT,
    fetched_at TEXT,
    row_count INTEGER NOT NULL,
    reported_row_count INTEGER,
    sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL
);
CREATE INDEX idx_source_snapshots_family ON source_snapshots(source_family);

CREATE TABLE products (
    item_seq TEXT PRIMARY KEY,
    source_row INTEGER NOT NULL,
    product_name TEXT NOT NULL,
    manufacturer TEXT,
    ingredient_text TEXT,
    dosage_form TEXT,
    permit_date TEXT,
    cancel_date TEXT,
    cancel_name TEXT,
    permit_status TEXT NOT NULL,
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key)
);
CREATE INDEX idx_products_name ON products(product_name);
CREATE INDEX idx_products_ingredient ON products(ingredient_text);
CREATE INDEX idx_products_status ON products(permit_status);

CREATE TABLE product_identifiers (
    item_seq TEXT NOT NULL REFERENCES products(item_seq),
    system TEXT NOT NULL,
    value TEXT NOT NULL,
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    PRIMARY KEY(item_seq, system, value)
);
CREATE INDEX idx_product_identifiers_lookup ON product_identifiers(system, value);

CREATE TABLE product_rules (
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_row INTEGER NOT NULL,
    category TEXT NOT NULL,
    item_seq TEXT NOT NULL,
    ingredient_name TEXT,
    paired_item_seq TEXT,
    paired_ingredient_name TEXT,
    effect_name TEXT,
    dosage_form TEXT,
    details TEXT,
    notification_date TEXT,
    change_date TEXT,
    PRIMARY KEY(source_dataset_key, source_row)
);
CREATE INDEX idx_product_rules_item_category ON product_rules(item_seq, category);
CREATE INDEX idx_product_rules_pair ON product_rules(item_seq, paired_item_seq, category);
CREATE INDEX idx_product_rules_category ON product_rules(category);

CREATE TABLE product_flags (
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_row INTEGER NOT NULL,
    flag_ordinal INTEGER NOT NULL,
    item_seq TEXT NOT NULL,
    category TEXT NOT NULL,
    flag_code TEXT NOT NULL,
    flag_name TEXT NOT NULL,
    ingredient_name TEXT,
    dosage_form TEXT,
    details TEXT,
    change_date TEXT,
    PRIMARY KEY(source_dataset_key, source_row, flag_ordinal)
);
CREATE INDEX idx_product_flags_item_category ON product_flags(item_seq, category);
CREATE INDEX idx_product_flags_category ON product_flags(category);

CREATE TABLE ingredient_rules (
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_row INTEGER NOT NULL,
    category TEXT NOT NULL,
    sequence_text TEXT,
    ingredient_name TEXT,
    ingredient_name_ko TEXT,
    paired_ingredient_name TEXT,
    rule_value TEXT,
    dosage_form TEXT,
    note TEXT,
    details TEXT,
    PRIMARY KEY(source_dataset_key, source_row)
);
CREATE INDEX idx_ingredient_rules_category ON ingredient_rules(category);
CREATE INDEX idx_ingredient_rules_name ON ingredient_rules(ingredient_name);
CREATE INDEX idx_ingredient_rules_name_ko ON ingredient_rules(ingredient_name_ko);
CREATE INDEX idx_ingredient_rules_pair ON ingredient_rules(paired_ingredient_name);
"""
