ALTER TABLE schema_migration ADD COLUMN sha256 TEXT;

CREATE TABLE source_contract_version (
    source_id TEXT NOT NULL REFERENCES source_registry(source_id) ON DELETE CASCADE,
    contract_version TEXT NOT NULL,
    manifest_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (source_id, contract_version)
);

CREATE TABLE metric_definition_version (
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id) ON DELETE CASCADE,
    definition_version TEXT NOT NULL,
    definition_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    PRIMARY KEY (metric_id, definition_version)
);

INSERT INTO source_contract_version(source_id,contract_version,manifest_json,created_at)
SELECT source_id,contract_version,json_object(
    'source_id',source_id,
    'source_type',source_type,
    'display_name',display_name,
    'capabilities',json(capabilities_json),
    'expected_cadence_days',expected_cadence_days,
    'consent_state',consent_state,
    'timestamp_semantics',timestamp_semantics,
    'canonical_timezone',canonical_timezone,
    'revision_behavior',revision_behavior,
    'completeness_rule',completeness_rule,
    'license_reference',license_reference,
    'contract_version',contract_version
),created_at FROM source_registry;
INSERT INTO source_contract_version SELECT source_id,CASE contract_version WHEN 'legacy-unknown' THEN 'legacy-unknown-1' ELSE 'legacy-unknown' END,'{"provenance_state":"unknown"}',created_at FROM source_registry;

INSERT INTO metric_definition_version(metric_id,definition_version,definition_json,created_at)
SELECT metric_id,definition_version,json_object(
    'metric_id',metric_id,
    'display_name',display_name,
    'value_kind',value_kind,
    'canonical_unit',canonical_unit,
    'percent_representation',percent_representation,
    'aggregation_rule',aggregation_rule,
    'minimum_value',minimum_value,
    'maximum_value',maximum_value,
    'measurement_method',measurement_method,
    'local_day_policy',local_day_policy,
    'definition_version',definition_version
),created_at FROM metric_definition;
INSERT INTO metric_definition_version SELECT metric_id,CASE definition_version WHEN 'legacy-unknown' THEN 'legacy-unknown-1' ELSE 'legacy-unknown' END,'{"provenance_state":"unknown"}',created_at FROM metric_definition;

CREATE TRIGGER source_contract_version_no_update
BEFORE UPDATE ON source_contract_version BEGIN
    SELECT RAISE(ABORT, 'source contract versions are immutable');
END;
CREATE TRIGGER source_contract_version_no_delete
BEFORE DELETE ON source_contract_version BEGIN
    SELECT RAISE(ABORT, 'source contract versions are immutable');
END;
CREATE TRIGGER metric_definition_version_no_update
BEFORE UPDATE ON metric_definition_version BEGIN
    SELECT RAISE(ABORT, 'metric definition versions are immutable');
END;
CREATE TRIGGER metric_definition_version_no_delete
BEFORE DELETE ON metric_definition_version BEGIN
    SELECT RAISE(ABORT, 'metric definition versions are immutable');
END;

CREATE TABLE source_record_version_new (
    record_version_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES source_registry(source_id),
    source_contract_version TEXT NOT NULL,
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
    validation_state TEXT NOT NULL CHECK (validation_state IN ('accepted','quarantined','rejected')),
    FOREIGN KEY (source_id,source_contract_version)
        REFERENCES source_contract_version(source_id,contract_version),
    UNIQUE (subject_id,source_id,source_record_key,source_version)
);

INSERT INTO source_record_version_new(
    record_version_id,subject_id,source_id,source_contract_version,source_record_key,
    source_version,payload_json,payload_sha256,event_start_utc,event_end_utc,
    timestamp_precision,source_timezone,offset_minutes,local_date,day_policy_version,
    source_updated_at,ingested_at,tombstone,validation_state
)
SELECT r.record_version_id,r.subject_id,r.source_id,CASE s.contract_version WHEN 'legacy-unknown' THEN 'legacy-unknown-1' ELSE 'legacy-unknown' END,r.source_record_key,
       r.source_version,r.payload_json,r.payload_sha256,r.event_start_utc,r.event_end_utc,
       r.timestamp_precision,r.source_timezone,r.offset_minutes,r.local_date,r.day_policy_version,
       r.source_updated_at,r.ingested_at,r.tombstone,'quarantined'
FROM source_record_version AS r
JOIN source_registry AS s ON s.source_id=r.source_id;

DROP VIEW current_source_record;
DROP TABLE source_record_version;
ALTER TABLE source_record_version_new RENAME TO source_record_version;
CREATE INDEX source_record_subject_date_idx
    ON source_record_version(subject_id,local_date);
CREATE INDEX source_record_source_key_idx
    ON source_record_version(subject_id,source_id,source_record_key,source_version DESC);
CREATE VIEW current_source_record AS
SELECT r.*
FROM source_record_version AS r
JOIN source_registry AS registered ON registered.source_id=r.source_id
WHERE r.tombstone=0
  AND registered.consent_state!='revoked'
  AND r.validation_state='accepted'
  AND r.source_version=(
      SELECT MAX(newer.source_version)
      FROM source_record_version AS newer
      WHERE newer.subject_id=r.subject_id
        AND newer.source_id=r.source_id
        AND newer.source_record_key=r.source_record_key
  )
  AND NOT EXISTS (
      SELECT 1 FROM source_record_version AS deleted
      WHERE deleted.subject_id=r.subject_id
        AND deleted.source_id=r.source_id
        AND deleted.source_record_key=r.source_record_key
        AND deleted.tombstone=1
        AND deleted.source_version>r.source_version
  );

CREATE TABLE normalized_fact_new (
    fact_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    fact_type TEXT NOT NULL CHECK (fact_type IN ('food_entry','meal','sleep_session','measurement','body_composition','activity_session','strength_set','capacity_assessment')),
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    metric_definition_version TEXT NOT NULL,
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
    FOREIGN KEY (metric_id,metric_definition_version)
        REFERENCES metric_definition_version(metric_id,definition_version),
    CHECK ((numeric_value IS NOT NULL)!=(text_value IS NOT NULL))
);

INSERT INTO normalized_fact_new(
    fact_id,subject_id,fact_type,metric_id,metric_definition_version,local_date,
    event_start_utc,event_end_utc,numeric_value,text_value,unit,source_record_version_id,
    epoch_id,attributes_json,validation_state,calculation_version,computed_at
)
SELECT f.fact_id,f.subject_id,f.fact_type,f.metric_id,CASE m.definition_version WHEN 'legacy-unknown' THEN 'legacy-unknown-1' ELSE 'legacy-unknown' END,f.local_date,
       f.event_start_utc,f.event_end_utc,f.numeric_value,f.text_value,f.unit,
       f.source_record_version_id,f.epoch_id,f.attributes_json,'quarantined',
       f.calculation_version,f.computed_at
FROM normalized_fact AS f
JOIN metric_definition AS m ON m.metric_id=f.metric_id;

DROP TABLE normalized_fact;
ALTER TABLE normalized_fact_new RENAME TO normalized_fact;
CREATE INDEX normalized_fact_metric_date_idx
    ON normalized_fact(subject_id,metric_id,local_date);
CREATE UNIQUE INDEX normalized_fact_derivation_identity_idx
    ON normalized_fact(source_record_version_id,metric_id,calculation_version);

CREATE TABLE quarantine_envelope (
    quarantine_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    source_id TEXT NOT NULL REFERENCES source_registry(source_id),
    source_record_key TEXT,
    source_version INTEGER,
    envelope_json TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    diagnostic_reference TEXT,
    quarantined_at TEXT NOT NULL
);
CREATE INDEX quarantine_reason_idx ON quarantine_envelope(reason_code);
CREATE TRIGGER quarantine_envelope_no_update
BEFORE UPDATE ON quarantine_envelope BEGIN
    SELECT RAISE(ABORT, 'quarantine envelopes are append-only');
END;
CREATE TRIGGER quarantine_envelope_no_delete
BEFORE DELETE ON quarantine_envelope
WHEN EXISTS (SELECT 1 FROM subject WHERE subject_id=OLD.subject_id)
BEGIN
    SELECT RAISE(ABORT, 'quarantine envelopes are append-only');
END;
