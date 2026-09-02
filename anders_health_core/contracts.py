"""Runtime validation executed from the published Draft 2020-12 schemas."""

import json
import re
from functools import lru_cache
from importlib.resources import files
from pathlib import Path
from typing import Any, Dict, List, Mapping

from jsonschema import Draft202012Validator, FormatChecker


SCHEMA_FILES = {
    "SourceManifest": "source-manifest.schema.json",
    "MetricDefinition": "metric-definition.schema.json",
    "RawRecordEnvelope": "raw-record-envelope.schema.json",
    "NormalizedFact": "normalized-fact.schema.json",
    "ContextEvent": "context-event.schema.json",
    "QualityIssue": "quality-issue.schema.json",
    "AnalyticsResult": "analytics-result.schema.json",
}

REQUIRED = {
    "SourceManifest": (
        "source_id",
        "source_type",
        "display_name",
        "capabilities",
        "consent_state",
        "timestamp_semantics",
        "revision_behavior",
        "completeness_rule",
        "contract_version",
    ),
    "RawRecordEnvelope": (
        "subject_id",
        "source_id",
        "source_contract_version",
        "source_record_key",
        "source_version",
        "payload",
        "event_start_utc",
        "timestamp_precision",
        "local_date",
        "day_policy_version",
        "ingested_at",
    ),
    "NormalizedFact": (
        "fact_id",
        "subject_id",
        "fact_type",
        "metric_id",
        "metric_definition_version",
        "local_date",
        "source_record_version_id",
        "validation_state",
        "calculation_version",
    ),
    "MetricDefinition": (
        "metric_id",
        "display_name",
        "value_kind",
        "canonical_unit",
        "percent_representation",
        "aggregation_rule",
        "minimum_value",
        "maximum_value",
        "measurement_method",
        "local_day_policy",
        "definition_version",
    ),
    "ContextEvent": (
        "context_event_id",
        "subject_id",
        "category",
        "starts_on",
    ),
    "QualityIssue": (
        "issue_id",
        "reason_code",
        "detected_at",
        "resolution_state",
    ),
    "AnalyticsResult": (
        "result_id",
        "result_type",
        "subject_id",
        "status",
        "method_version",
        "policy_version",
        "input_snapshot_hash",
        "generated_at",
    ),
}

RESULT_REQUIRED = {
    "coverage": (
        "metric_id", "source_id", "period_start", "period_end", "expected_records",
        "observed_records", "usable_records", "possible_days", "usable_days",
        "missing_days", "longest_gap_days", "quarantined_records", "exclusions",
    ),
    "trend": (
        "metric_id", "as_of_date", "window_days", "baseline_days", "eligible_days",
        "possible_days", "missing_days", "longest_gap_days", "direction", "magnitude",
        "epoch_id", "exclusions",
    ),
    "association": (
        "association_id", "exposure_metric_id", "outcome_metric_id", "period_start",
        "period_end", "lag_days", "paired_count", "possible_pairs", "claim_type",
        "effect_method", "effect_value", "rank_direction", "uncertainty_low", "uncertainty_high", "exclusions",
    ),
    "assessment_change": (
        "metric_id", "protocol_id", "compatible_session_count", "possible_sessions",
        "baseline_date", "latest_date", "baseline_value", "latest_value",
        "delta_from_baseline", "direction", "exclusions",
    ),
}

RESULT_OPTIONAL = {
    "coverage": ("freshest_event_at",),
    "trend": (),
    "association": (),
    "assessment_change": (),
}


@lru_cache(maxsize=None)
def _validator(contract_name: str) -> Draft202012Validator:
    resource = files("anders_health_core").joinpath("schemas", SCHEMA_FILES[contract_name])
    if not resource.is_file():
        resource = Path(__file__).resolve().parents[1] / "schemas" / SCHEMA_FILES[contract_name]
    schema = json.loads(resource.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def _format_schema_error(error: Any) -> str:
    if error.validator == "required":
        missing = [field for field in error.validator_value if field not in error.instance]
        if len(missing) == 1:
            return f"missing required field: {missing[0]}"
    if error.validator == "additionalProperties":
        match = re.search(r"\('([^']+)' was unexpected\)", error.message)
        if match:
            return f"unexpected field: {match.group(1)}"
    location = ".".join(str(part) for part in error.absolute_path) or "$"
    return f"schema violation at {location}: {error.message}"


def validate_contract(contract_name: str, payload: Mapping[str, Any]) -> List[str]:
    if contract_name not in SCHEMA_FILES:
        return [f"unknown contract: {contract_name}"]
    errors: List[str] = []
    if contract_name == "AnalyticsResult":
        result_type = payload.get("result_type")
        if result_type not in RESULT_REQUIRED:
            errors.append("result_type must name a supported result contract")
        else:
            allowed_fields = (
                set(REQUIRED["AnalyticsResult"])
                | set(RESULT_REQUIRED[result_type])
                | set(RESULT_OPTIONAL[result_type])
            )
            errors.extend(
                f"missing required field: {field}"
                for field in RESULT_REQUIRED[result_type]
                if field not in payload
            )
            errors.extend(
                f"unexpected field: {field}"
                for field in payload
                if field not in allowed_fields
            )
    errors.extend(_format_schema_error(error) for error in _validator(contract_name).iter_errors(payload))
    return list(dict.fromkeys(errors))
