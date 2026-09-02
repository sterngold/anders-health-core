CREATE TABLE assessment_protocol (protocol_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL, test_id TEXT NOT NULL,
    compatibility_rule TEXT NOT NULL, created_at TEXT NOT NULL,
    PRIMARY KEY (protocol_id,protocol_version)
);
CREATE TABLE assessment_required_metric (
    protocol_id TEXT NOT NULL, protocol_version TEXT NOT NULL,
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id),
    PRIMARY KEY (protocol_id,protocol_version,metric_id), FOREIGN KEY (protocol_id,protocol_version) REFERENCES assessment_protocol(protocol_id,protocol_version)
);
CREATE TABLE assessment_attempt (
    attempt_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE, protocol_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL, metric_id TEXT NOT NULL,
    metric_definition_version TEXT NOT NULL, local_date TEXT NOT NULL,
    numeric_value REAL NOT NULL, recorded_at TEXT NOT NULL,
    FOREIGN KEY (protocol_id,protocol_version) REFERENCES assessment_protocol(protocol_id,protocol_version), FOREIGN KEY (metric_id,metric_definition_version) REFERENCES metric_definition_version(metric_id,definition_version)
);
CREATE TABLE assessment_session (
    session_id TEXT PRIMARY KEY,
    subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE, protocol_id TEXT NOT NULL,
    protocol_version TEXT NOT NULL,
    local_date TEXT NOT NULL,
    completeness_state TEXT NOT NULL CHECK (completeness_state IN ('partial','complete')),
    input_snapshot_hash TEXT NOT NULL CHECK (length(input_snapshot_hash)=64),
    created_at TEXT NOT NULL,
    FOREIGN KEY (protocol_id,protocol_version) REFERENCES assessment_protocol(protocol_id,protocol_version), UNIQUE (subject_id,protocol_id,protocol_version,local_date)
);
CREATE TABLE association_definition (
    association_id TEXT NOT NULL,
    definition_version TEXT NOT NULL,
    exposure_metric_id TEXT NOT NULL,
    exposure_metric_version TEXT NOT NULL,
    outcome_metric_id TEXT NOT NULL,
    outcome_metric_version TEXT NOT NULL,
    eligible_source_ids_json TEXT NOT NULL,
    pairing_rule TEXT NOT NULL,
    coverage_rule_json TEXT NOT NULL,
    exposure_kind TEXT NOT NULL CHECK (exposure_kind IN ('binary','continuous')),
    effect_method TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type='observational'),
    created_at TEXT NOT NULL,
    PRIMARY KEY (association_id,definition_version), FOREIGN KEY (exposure_metric_id,exposure_metric_version) REFERENCES metric_definition_version(metric_id,definition_version), FOREIGN KEY (outcome_metric_id,outcome_metric_version) REFERENCES metric_definition_version(metric_id,definition_version)
);
CREATE TRIGGER assessment_protocol_no_update BEFORE UPDATE ON assessment_protocol BEGIN SELECT RAISE(ABORT,'assessment protocols are immutable'); END;
CREATE TRIGGER assessment_protocol_no_delete BEFORE DELETE ON assessment_protocol BEGIN SELECT RAISE(ABORT,'assessment protocols are immutable'); END;
CREATE TRIGGER assessment_required_no_update BEFORE UPDATE ON assessment_required_metric BEGIN SELECT RAISE(ABORT,'assessment protocols are immutable'); END;
CREATE TRIGGER assessment_required_no_delete BEFORE DELETE ON assessment_required_metric BEGIN SELECT RAISE(ABORT,'assessment protocols are immutable'); END;
CREATE TRIGGER assessment_required_no_late_insert BEFORE INSERT ON assessment_required_metric WHEN NOT EXISTS(SELECT 1 FROM assessment_required_metric WHERE protocol_id=NEW.protocol_id AND protocol_version=NEW.protocol_version AND metric_id=NEW.metric_id) AND EXISTS(SELECT 1 FROM assessment_session WHERE protocol_id=NEW.protocol_id AND protocol_version=NEW.protocol_version) BEGIN SELECT RAISE(ABORT,'assessment protocols are immutable after use'); END;
CREATE TRIGGER association_definition_no_update BEFORE UPDATE ON association_definition BEGIN SELECT RAISE(ABORT,'association definitions are immutable'); END;
CREATE TRIGGER association_definition_no_delete BEFORE DELETE ON association_definition BEGIN SELECT RAISE(ABORT,'association definitions are immutable'); END;
CREATE TRIGGER assessment_complete_insert BEFORE INSERT ON assessment_session WHEN NEW.completeness_state='complete' BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assessment_required_metric r WHERE r.protocol_id=NEW.protocol_id AND r.protocol_version=NEW.protocol_version) OR EXISTS(SELECT 1 FROM assessment_required_metric r WHERE r.protocol_id=NEW.protocol_id AND r.protocol_version=NEW.protocol_version AND NOT EXISTS(SELECT 1 FROM assessment_attempt a WHERE a.subject_id=NEW.subject_id AND a.protocol_id=NEW.protocol_id AND a.protocol_version=NEW.protocol_version AND a.local_date=NEW.local_date AND a.metric_id=r.metric_id)) THEN RAISE(ABORT,'complete assessment requires all required attempts') END; END;
CREATE TRIGGER assessment_complete_update BEFORE UPDATE ON assessment_session WHEN NEW.completeness_state='complete' BEGIN SELECT CASE WHEN NOT EXISTS(SELECT 1 FROM assessment_required_metric r WHERE r.protocol_id=NEW.protocol_id AND r.protocol_version=NEW.protocol_version) OR EXISTS(SELECT 1 FROM assessment_required_metric r WHERE r.protocol_id=NEW.protocol_id AND r.protocol_version=NEW.protocol_version AND NOT EXISTS(SELECT 1 FROM assessment_attempt a WHERE a.subject_id=NEW.subject_id AND a.protocol_id=NEW.protocol_id AND a.protocol_version=NEW.protocol_version AND a.local_date=NEW.local_date AND a.metric_id=r.metric_id)) THEN RAISE(ABORT,'complete assessment requires all required attempts') END; END;
CREATE TRIGGER assessment_attempt_no_update BEFORE UPDATE ON assessment_attempt BEGIN SELECT RAISE(ABORT,'assessment attempts are immutable'); END;
CREATE TRIGGER assessment_attempt_no_delete BEFORE DELETE ON assessment_attempt WHEN EXISTS(SELECT 1 FROM subject WHERE subject_id=OLD.subject_id) BEGIN SELECT RAISE(ABORT,'assessment attempts are immutable'); END;
