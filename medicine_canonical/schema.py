from __future__ import annotations

SCHEMA_VERSION = "4"
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
    id INTEGER PRIMARY KEY,
    source_dataset_key TEXT NOT NULL REFERENCES source_snapshots(dataset_key),
    source_row INTEGER NOT NULL,
    category TEXT NOT NULL,
    item_seq TEXT NOT NULL,
    ingredient_code TEXT,
    ingredient_name TEXT,
    ingredient_name_en TEXT,
    paired_item_seq TEXT,
    paired_ingredient_code TEXT,
    paired_ingredient_name TEXT,
    paired_ingredient_name_en TEXT,
    effect_name TEXT,
    dosage_form TEXT,
    details TEXT,
    notification_date TEXT,
    change_date TEXT,
    UNIQUE(source_dataset_key, source_row)
);
CREATE INDEX idx_product_rules_item_category ON product_rules(item_seq, category);
CREATE INDEX idx_product_rules_pair ON product_rules(item_seq, paired_item_seq, category);
CREATE INDEX idx_product_rules_category ON product_rules(category);
CREATE INDEX idx_product_rules_ingredient_code ON product_rules(category, ingredient_code);
CREATE INDEX idx_product_rules_ingredient_name_en ON product_rules(category, ingredient_name_en);

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
    id INTEGER PRIMARY KEY,
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
    UNIQUE(source_dataset_key, source_row)
);
CREATE INDEX idx_ingredient_rules_category ON ingredient_rules(category);
CREATE INDEX idx_ingredient_rules_name ON ingredient_rules(ingredient_name);
CREATE INDEX idx_ingredient_rules_name_ko ON ingredient_rules(ingredient_name_ko);
CREATE INDEX idx_ingredient_rules_pair ON ingredient_rules(paired_ingredient_name);

CREATE TABLE product_criterion_links (
    product_rule_id INTEGER NOT NULL REFERENCES product_rules(id),
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    match_method TEXT NOT NULL CHECK(match_method IN ('english_exact','mfds_ingredient_code')),
    pair_orientation TEXT CHECK(pair_orientation IN ('forward','reverse') OR pair_orientation IS NULL),
    PRIMARY KEY(product_rule_id, criterion_rule_id)
) WITHOUT ROWID;
CREATE INDEX idx_product_criterion_links_criterion
    ON product_criterion_links(criterion_rule_id);
CREATE INDEX idx_product_criterion_links_method ON product_criterion_links(match_method);

CREATE VIEW product_rule_criteria AS
SELECT
    r.source_dataset_key AS product_source_dataset_key,
    r.source_row AS product_source_row,
    i.source_dataset_key AS criterion_source_dataset_key,
    i.source_row AS criterion_source_row,
    r.category,
    r.item_seq,
    r.ingredient_code,
    r.ingredient_name,
    r.ingredient_name_en,
    r.paired_item_seq,
    r.paired_ingredient_code,
    r.paired_ingredient_name,
    r.paired_ingredient_name_en,
    r.effect_name,
    r.dosage_form AS product_dosage_form,
    r.details AS product_details,
    i.sequence_text AS criterion_sequence_text,
    i.ingredient_name AS criterion_ingredient_name,
    i.ingredient_name_ko AS criterion_ingredient_name_ko,
    i.paired_ingredient_name AS criterion_paired_ingredient_name,
    i.rule_value AS criterion_rule_value,
    i.dosage_form AS criterion_dosage_form,
    i.note AS criterion_note,
    i.details AS criterion_details,
    l.match_method,
    l.pair_orientation
FROM product_criterion_links l
JOIN product_rules r
  ON r.id = l.product_rule_id
JOIN ingredient_rules i
  ON i.id = l.criterion_rule_id;
"""
