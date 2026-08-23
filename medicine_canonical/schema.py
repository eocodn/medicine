from __future__ import annotations

from medicine_reference.mfds_sources import MFDS_SOURCE_FAMILY_SET

from .product_search_documents import PRODUCT_SEARCH_DOCUMENT_DDL


SCHEMA_VERSION = "11"
CORE_SOURCE_FAMILIES = MFDS_SOURCE_FAMILY_SET

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
    qualifier_note TEXT,
    details TEXT,
    UNIQUE(source_dataset_key, source_row)
);
CREATE INDEX idx_ingredient_rules_category ON ingredient_rules(category);
CREATE INDEX idx_ingredient_rules_name ON ingredient_rules(ingredient_name);
CREATE INDEX idx_ingredient_rules_name_ko ON ingredient_rules(ingredient_name_ko);
CREATE INDEX idx_ingredient_rules_pair ON ingredient_rules(paired_ingredient_name);

-- Build-time regulatory identifiers published by MFDS ingredient criteria.
-- Keep them separate from the mobile runtime row shape: they are authoritative linking evidence,
-- not a chemical substance identity and are unnecessary after links are built.
CREATE TABLE ingredient_rule_codes (
    criterion_rule_id INTEGER PRIMARY KEY REFERENCES ingredient_rules(id),
    ingredient_code TEXT NOT NULL,
    paired_ingredient_code TEXT,
    mixture_type TEXT CHECK(mixture_type IN ('단일','복합') OR mixture_type IS NULL),
    mixture_ingredient_codes_json TEXT NOT NULL DEFAULT '[]',
    mixture_ingredient_names_json TEXT NOT NULL DEFAULT '[]'
);
CREATE INDEX idx_ingredient_rule_codes_ingredient ON ingredient_rule_codes(ingredient_code);
CREATE INDEX idx_ingredient_rule_codes_pair ON ingredient_rule_codes(paired_ingredient_code);

CREATE TABLE dose_criteria (
    criterion_rule_id INTEGER PRIMARY KEY REFERENCES ingredient_rules(id),
    maximum_daily_amount TEXT,
    maximum_daily_unit TEXT,
    parse_status TEXT NOT NULL CHECK(parse_status IN ('parsed','not_evaluable')),
    parse_reason TEXT,
    CHECK(
        (parse_status='parsed' AND maximum_daily_amount IS NOT NULL
         AND maximum_daily_unit IS NOT NULL AND parse_reason IS NULL)
        OR
        (parse_status='not_evaluable' AND maximum_daily_amount IS NULL
         AND maximum_daily_unit IS NULL AND parse_reason IS NOT NULL)
    )
);

-- DUR ingredient codes are regulatory applicability concepts, not precise
-- chemical identities. Keep this bridge separate from canonical substances so
-- salts/hydrates/esters may remain distinct substances while still sharing the
-- same Korean DUR scope when the official product-rule graph establishes it.
CREATE TABLE dur_ingredient_concepts (
    concept_id TEXT PRIMARY KEY,
    category TEXT NOT NULL,
    ingredient_code TEXT NOT NULL,
    UNIQUE(category, ingredient_code)
);
CREATE INDEX idx_dur_concepts_category_code
    ON dur_ingredient_concepts(category, ingredient_code);

CREATE TABLE dur_ingredient_code_map (
    category TEXT NOT NULL,
    source_ingredient_code TEXT NOT NULL,
    canonical_ingredient_code TEXT NOT NULL,
    concept_id TEXT NOT NULL REFERENCES dur_ingredient_concepts(concept_id),
    PRIMARY KEY(category, source_ingredient_code)
) WITHOUT ROWID;
CREATE INDEX idx_dur_code_map_concept ON dur_ingredient_code_map(concept_id);

CREATE TABLE dur_concept_substances (
    concept_id TEXT NOT NULL REFERENCES dur_ingredient_concepts(concept_id),
    category TEXT NOT NULL,
    ingredient_code TEXT NOT NULL,
    substance_id TEXT NOT NULL,
    source_name TEXT NOT NULL,
    evidence_kind TEXT NOT NULL CHECK(evidence_kind='direct_source_identity'),
    PRIMARY KEY(concept_id, substance_id, source_name)
) WITHOUT ROWID;
CREATE INDEX idx_dur_concept_substances_substance
    ON dur_concept_substances(substance_id);

CREATE TABLE dur_product_item_signatures (
    item_seq TEXT NOT NULL,
    signature_type TEXT NOT NULL CHECK(signature_type='code'),
    signature_key TEXT NOT NULL,
    component_count INTEGER NOT NULL CHECK(component_count > 0),
    match_method TEXT NOT NULL CHECK(match_method IN ('mfds_ingredient_code','permit_composition')),
    evidence_kind TEXT NOT NULL,
    PRIMARY KEY(item_seq, signature_type, signature_key)
) WITHOUT ROWID;
CREATE INDEX idx_dur_product_item_signatures_lookup
    ON dur_product_item_signatures(signature_type, signature_key);

-- MFDS ingredient criteria carry category-scoped DUR composition codes. The
-- The global product signature remains useful for ingredient applicability; this
-- table resolves permit composition only inside the product-rule category
-- so a name reused with another DUR code in another category cannot erase
-- otherwise authoritative composition evidence.
CREATE TABLE dur_product_category_signatures (
    item_seq TEXT NOT NULL,
    category TEXT NOT NULL,
    signature_key TEXT NOT NULL,
    component_count INTEGER NOT NULL CHECK(component_count > 0),
    match_method TEXT NOT NULL CHECK(match_method IN ('mfds_ingredient_code','permit_composition')),
    evidence_kind TEXT NOT NULL CHECK(
        evidence_kind IN ('category_permit_composition','category_single_component_rule')
    ),
    PRIMARY KEY(item_seq, category, signature_key)
) WITHOUT ROWID;
CREATE INDEX idx_dur_product_category_signatures_lookup
    ON dur_product_category_signatures(category, signature_key);

CREATE TABLE dur_criterion_signatures (
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    category TEXT NOT NULL,
    effect_key TEXT NOT NULL,
    signature_key TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK(match_method='mfds_ingredient_code'),
    evidence_kind TEXT NOT NULL,
    PRIMARY KEY(criterion_rule_id, signature_key)
) WITHOUT ROWID;
CREATE INDEX idx_dur_criterion_signatures_lookup
    ON dur_criterion_signatures(category, effect_key, signature_key);

CREATE TABLE dur_criterion_pair_signatures (
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    left_signature_key TEXT NOT NULL,
    right_signature_key TEXT NOT NULL,
    match_method TEXT NOT NULL CHECK(match_method='mfds_ingredient_code'),
    evidence_kind TEXT NOT NULL,
    PRIMARY KEY(criterion_rule_id, left_signature_key, right_signature_key)
) WITHOUT ROWID;
CREATE INDEX idx_dur_pair_signatures_lookup
    ON dur_criterion_pair_signatures(left_signature_key, right_signature_key);

CREATE TABLE product_criterion_links (
    product_rule_id INTEGER NOT NULL REFERENCES product_rules(id),
    criterion_rule_id INTEGER NOT NULL REFERENCES ingredient_rules(id),
    match_method TEXT NOT NULL CHECK(match_method IN ('mfds_ingredient_code','permit_composition','mfds_details_exact','mfds_unanimous_value')),
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
    i.qualifier_note AS criterion_qualifier_note,
    i.details AS criterion_details,
    d.maximum_daily_amount AS criterion_maximum_daily_amount,
    d.maximum_daily_unit AS criterion_maximum_daily_unit,
    d.parse_status AS criterion_dose_parse_status,
    d.parse_reason AS criterion_dose_parse_reason,
    l.match_method,
    l.pair_orientation
FROM product_criterion_links l
JOIN product_rules r
  ON r.id = l.product_rule_id
JOIN ingredient_rules i
  ON i.id = l.criterion_rule_id
LEFT JOIN dose_criteria d
  ON d.criterion_rule_id = i.id;

""" + PRODUCT_SEARCH_DOCUMENT_DDL
