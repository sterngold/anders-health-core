PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migration (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

CREATE TABLE subject (
    subject_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL
);

CREATE TABLE source_registry (
    source_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    display_name TEXT NOT NULL,
    capabilities_json TEXT NOT NULL,
    expected_cadence_days INTEGER,
    consent_state TEXT NOT NULL CHECK (consent_state IN ('granted','revoked','not_required')),
    timestamp_semantics TEXT NOT NULL,
    canonical_timezone TEXT,
    revision_behavior TEXT NOT NULL,
    license_reference TEXT,
    contract_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE metric_definition (
    metric_id TEXT PRIMARY KEY,
    display_name TEXT NOT NULL,
    value_kind TEXT NOT NULL CHECK (value_kind IN ('number','integer','text','category','boolean')),
    canonical_unit TEXT,
    percent_representation TEXT CHECK (percent_representation IN ('zero_to_one','zero_to_hundred') OR percent_representation IS NULL),
    aggregation_rule TEXT NOT NULL,
    minimum_value REAL,
    maximum_value REAL,
    measurement_method TEXT,
    local_day_policy TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    created_at TEXT NOT NULL
);

CREATE TABLE source_metric_map (
    source_id TEXT NOT NULL REFERENCES source_registry(source_id) ON DELETE CASCADE,
    source_metric TEXT NOT NULL,
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id) ON DELETE CASCADE,
    source_unit TEXT,
    conversion_rule TEXT,
    map_version TEXT NOT NULL,
    PRIMARY KEY (source_id, source_metric, map_version)
);

CREATE TABLE source_epoch (
    epoch_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES source_registry(source_id),
    metric_id TEXT REFERENCES metric_definition(metric_id),
    starts_at TEXT NOT NULL,
    ends_at TEXT,
    reason_category TEXT NOT NULL CHECK (reason_category IN ('initial','device_change','source_change','calibration_change','method_change')),
    comparable_to_previous INTEGER NOT NULL DEFAULT 0 CHECK (comparable_to_previous IN (0,1)),
    created_at TEXT NOT NULL
);

CREATE TABLE source_record_version (
    record_version_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES source_registry(source_id),
    source_record_key TEXT NOT NULL,
    source_version INTEGER NOT NULL CHECK (source_version > 0),
    payload_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    event_start_utc TEXT,
    event_end_utc TEXT,
    timestamp_precision TEXT NOT NULL CHECK (timestamp_precision IN ('date','instant','interval')),
    source_timezone TEXT,
    offset_minutes INTEGER CHECK (offset_minutes BETWEEN -840 AND 840),
    local_date TEXT NOT NULL,
    day_policy_version TEXT NOT NULL,
    source_updated_at TEXT,
    ingested_at TEXT NOT NULL,
    tombstone INTEGER NOT NULL DEFAULT 0 CHECK (tombstone IN (0,1)),
    validation_state TEXT NOT NULL CHECK (validation_state IN ('accepted','quarantined')),
    UNIQUE (source_id, source_record_key, source_version)
);

CREATE INDEX source_record_subject_date_idx
    ON source_record_version(subject_id, local_date);
CREATE INDEX source_record_source_key_idx
    ON source_record_version(source_id, source_record_key, source_version DESC);

CREATE VIEW current_source_record AS
SELECT r.*
FROM source_record_version AS r
JOIN source_registry AS registered ON registered.source_id = r.source_id
WHERE r.tombstone = 0
  AND registered.consent_state != 'revoked'
  AND r.validation_state = 'accepted'
  AND r.source_version = (
      SELECT MAX(newer.source_version)
      FROM source_record_version AS newer
      WHERE newer.source_id = r.source_id
        AND newer.source_record_key = r.source_record_key
  )
  AND NOT EXISTS (
      SELECT 1 FROM source_record_version AS deleted
      WHERE deleted.source_id = r.source_id
        AND deleted.source_record_key = r.source_record_key
        AND deleted.tombstone = 1
        AND deleted.source_version > r.source_version
  );

CREATE TABLE quality_issue (
    issue_id TEXT PRIMARY KEY,
    subject_id TEXT REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id TEXT REFERENCES source_registry(source_id),
    source_record_key TEXT,
    record_version_id TEXT REFERENCES source_record_version(record_version_id) ON DELETE CASCADE,
    reason_code TEXT NOT NULL,
    field_name TEXT,
    detected_at TEXT NOT NULL,
    resolution_state TEXT NOT NULL DEFAULT 'open' CHECK (resolution_state IN ('open','resolved','ignored'))
);

CREATE TABLE normalized_fact (
    fact_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL CHECK (fact_type IN ('food_entry','meal','sleep_session','measurement','body_composition','activity_session','strength_set','capacity_assessment')),
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    local_date TEXT NOT NULL,
    event_start_utc TEXT,
    event_end_utc TEXT,
    numeric_value REAL,
    text_value TEXT,
    unit TEXT,
    source_record_version_id TEXT NOT NULL REFERENCES source_record_version(record_version_id) ON DELETE CASCADE,
    epoch_id TEXT REFERENCES source_epoch(epoch_id),
    attributes_json TEXT NOT NULL DEFAULT '{}',
    validation_state TEXT NOT NULL CHECK (validation_state IN ('accepted','quarantined')),
    calculation_version TEXT NOT NULL,
    computed_at TEXT NOT NULL,
    CHECK ((numeric_value IS NOT NULL) != (text_value IS NOT NULL))
);

CREATE INDEX normalized_fact_metric_date_idx
    ON normalized_fact(subject_id, metric_id, local_date);
CREATE UNIQUE INDEX normalized_fact_derivation_identity_idx
    ON normalized_fact(source_record_version_id, metric_id, calculation_version);

CREATE TABLE context_event (
    context_event_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    category TEXT NOT NULL CHECK (category IN ('illness','injury','travel','medication_change','device_replacement','source_change')),
    starts_on TEXT NOT NULL,
    ends_on TEXT,
    source_record_version_id TEXT REFERENCES source_record_version(record_version_id) ON DELETE SET NULL,
    created_at TEXT NOT NULL,
    CHECK (ends_on IS NULL OR ends_on >= starts_on)
);
CREATE UNIQUE INDEX context_event_identity_idx
    ON context_event(subject_id, category, starts_on, COALESCE(ends_on,''));

CREATE TABLE analysis_policy (
    policy_id TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    domain TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    active_from TEXT NOT NULL,
    retired_at TEXT,
    PRIMARY KEY (policy_id, policy_version)
);

CREATE TABLE coverage_result (
    result_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    metric_id TEXT REFERENCES metric_definition(metric_id),
    source_id TEXT REFERENCES source_registry(source_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    expected_records INTEGER,
    observed_records INTEGER NOT NULL,
    usable_records INTEGER NOT NULL,
    possible_days INTEGER NOT NULL,
    usable_days INTEGER NOT NULL,
    missing_days INTEGER NOT NULL,
    longest_gap_days INTEGER NOT NULL,
    quarantined_records INTEGER NOT NULL,
    freshest_event_at TEXT,
    method_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE trend_result (
    result_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    as_of_date TEXT NOT NULL,
    window_days INTEGER NOT NULL,
    baseline_days INTEGER,
    status TEXT NOT NULL,
    eligible_days INTEGER NOT NULL,
    possible_days INTEGER NOT NULL,
    missing_days INTEGER NOT NULL,
    longest_gap_days INTEGER,
    direction TEXT,
    magnitude REAL,
    epoch_id TEXT REFERENCES source_epoch(epoch_id),
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE association_result (
    result_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    association_id TEXT NOT NULL,
    exposure_metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    outcome_metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    lag_days INTEGER NOT NULL,
    paired_count INTEGER NOT NULL,
    status TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type = 'observational'),
    effect_method TEXT,
    effect_value REAL,
    uncertainty_low REAL,
    uncertainty_high REAL,
    exclusions_json TEXT NOT NULL DEFAULT '[]',
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE assessment_change_result (
    result_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    protocol_id TEXT NOT NULL,
    compatible_session_count INTEGER NOT NULL,
    baseline_date TEXT NOT NULL,
    latest_date TEXT NOT NULL,
    baseline_value REAL,
    latest_value REAL,
    delta_from_baseline REAL,
    status TEXT NOT NULL CHECK (status IN ('baseline','change','trend','insufficient')),
    direction TEXT,
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);

CREATE TABLE derivation_receipt (
    receipt_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    result_type TEXT NOT NULL CHECK (result_type IN ('coverage','trend','association','assessment_change')),
    result_id TEXT NOT NULL,
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    input_count INTEGER NOT NULL,
    output_count INTEGER NOT NULL,
    exclusions_json TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
