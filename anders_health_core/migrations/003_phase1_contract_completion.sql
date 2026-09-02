ALTER TABLE source_registry ADD COLUMN completeness_rule TEXT NOT NULL
    DEFAULT 'legacy contract did not declare expected versus usable records';

ALTER TABLE coverage_result ADD COLUMN policy_version TEXT;
ALTER TABLE coverage_result ADD COLUMN exclusions_json TEXT;

ALTER TABLE association_result RENAME TO association_result_v2;
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
    possible_pairs INTEGER,
    status TEXT NOT NULL,
    claim_type TEXT NOT NULL CHECK (claim_type = 'observational'),
    effect_method TEXT,
    effect_value REAL,
    rank_direction REAL,
    uncertainty_low REAL,
    uncertainty_high REAL,
    exclusions_json TEXT NOT NULL,
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
INSERT INTO association_result (
    result_id, subject_id, association_id, exposure_metric_id, outcome_metric_id,
    period_start, period_end, lag_days, paired_count, possible_pairs, status,
    claim_type, effect_method, effect_value, rank_direction, uncertainty_low, uncertainty_high,
    exclusions_json, method_version, policy_version, input_snapshot_hash, generated_at
)
SELECT
    result_id, subject_id, association_id, exposure_metric_id, outcome_metric_id,
    period_start, period_end, lag_days, paired_count, NULL, status, claim_type,
    effect_method, effect_value, NULL, uncertainty_low, uncertainty_high, exclusions_json,
    method_version, policy_version, input_snapshot_hash, generated_at
FROM association_result_v2;
DROP TABLE association_result_v2;

ALTER TABLE assessment_change_result RENAME TO assessment_change_result_v2;
CREATE TABLE assessment_change_result (
    result_id TEXT PRIMARY KEY, subject_id TEXT NOT NULL REFERENCES subject(subject_id) ON DELETE CASCADE,
    metric_id TEXT NOT NULL REFERENCES metric_definition(metric_id), protocol_id TEXT NOT NULL,
    compatible_session_count INTEGER NOT NULL, possible_sessions INTEGER NOT NULL,
    baseline_date TEXT, latest_date TEXT, baseline_value REAL, latest_value REAL,
    delta_from_baseline REAL, status TEXT NOT NULL CHECK (status IN ('baseline','change','trend','insufficient')),
    direction TEXT, exclusions_json TEXT NOT NULL, method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL, input_snapshot_hash TEXT NOT NULL, generated_at TEXT NOT NULL
);
INSERT INTO assessment_change_result
SELECT result_id,subject_id,metric_id,protocol_id,compatible_session_count,compatible_session_count,
       baseline_date,latest_date,baseline_value,latest_value,delta_from_baseline,status,direction,'[]',
       method_version,policy_version,input_snapshot_hash,generated_at FROM assessment_change_result_v2;
DROP TABLE assessment_change_result_v2;

ALTER TABLE trend_result RENAME TO trend_result_v2;
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
    exclusions_json TEXT NOT NULL,
    method_version TEXT NOT NULL,
    policy_version TEXT NOT NULL,
    input_snapshot_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL
);
INSERT INTO trend_result (
    result_id, subject_id, metric_id, as_of_date, window_days, baseline_days,
    status, eligible_days, possible_days, missing_days, longest_gap_days,
    direction, magnitude, epoch_id, exclusions_json, method_version,
    policy_version, input_snapshot_hash, generated_at
)
SELECT
    result_id, subject_id, metric_id, as_of_date, window_days, baseline_days,
    status, eligible_days, possible_days, missing_days, longest_gap_days,
    direction, magnitude, epoch_id, exclusions_json, method_version,
    policy_version, input_snapshot_hash, generated_at
FROM trend_result_v2;
DROP TABLE trend_result_v2;
