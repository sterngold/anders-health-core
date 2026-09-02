"""Small runtime validator aligned with the published JSON Schemas."""

from typing import Any, Dict, List, Mapping


REQUIRED = {
    "SourceManifest": (
        "source_id",
        "source_type",
        "display_name",
        "capabilities",
        "consent_state",
        "timestamp_semantics",
        "revision_behavior",
        "contract_version",
    ),
    "RawRecordEnvelope": (
        "subject_id",
        "source_id",
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
        "local_date",
        "source_record_version_id",
        "validation_state",
        "calculation_version",
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


def validate_contract(contract_name: str, payload: Mapping[str, Any]) -> List[str]:
    if contract_name not in REQUIRED:
        return [f"unknown contract: {contract_name}"]
    errors = [
        f"missing required field: {field}"
        for field in REQUIRED[contract_name]
        if field not in payload
    ]
    if "subject_id" in payload and not isinstance(payload["subject_id"], str):
        errors.append("subject_id must be a string")
    if "source_version" in payload and (
        not isinstance(payload["source_version"], int) or payload["source_version"] < 1
    ):
        errors.append("source_version must be a positive integer")
    if "payload" in payload and not isinstance(payload["payload"], dict):
        errors.append("payload must be an object")
    return errors
