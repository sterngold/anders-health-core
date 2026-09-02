"""Generic source contracts, append-only raw versions, and fact validation."""

from __future__ import annotations

import csv
import hashlib
import json
import sqlite3
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Mapping, Optional


REQUIRED_ENVELOPE_FIELDS = {
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
}
CONTEXT_CATEGORIES = {
    "illness",
    "injury",
    "travel",
    "medication_change",
    "device_replacement",
    "source_change",
}


class RecordValidationError(ValueError):
    def __init__(self, reason_code: str, field_name: Optional[str] = None):
        super().__init__(reason_code)
        self.reason_code = reason_code
        self.field_name = field_name


def _now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _payload_hash(payload: Mapping[str, Any]) -> str:
    return hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()


def synthetic_source_manifest() -> Dict[str, Any]:
    return {
        "source_id": "synthetic.json",
        "source_type": "file",
        "display_name": "Synthetic JSON fixture",
        "capabilities": ["measurement"],
        "expected_cadence_days": 1,
        "consent_state": "not_required",
        "timestamp_semantics": "event_interval",
        "canonical_timezone": "Etc/UTC",
        "revision_behavior": "monotonic_integer_version",
        "license_reference": "CC0 synthetic fixture",
        "contract_version": "source-v1",
    }


def synthetic_metric_definition() -> Dict[str, Any]:
    return {
        "metric_id": "synthetic.measurement",
        "display_name": "Synthetic measurement",
        "value_kind": "number",
        "canonical_unit": "count",
        "percent_representation": None,
        "aggregation_rule": "daily_last",
        "minimum_value": 0,
        "maximum_value": None,
        "measurement_method": "synthetic fixture",
        "local_day_policy": "source_local_date",
        "definition_version": "metric-v1",
    }


def synthetic_envelope(
    subject_id: str,
    source_record_key: str = "record-1",
    source_version: int = 1,
    payload: Optional[Mapping[str, Any]] = None,
    tombstone: bool = False,
) -> Dict[str, Any]:
    return {
        "subject_id": subject_id,
        "source_id": "synthetic.json",
        "source_record_key": source_record_key,
        "source_version": source_version,
        "payload": dict(
            payload
            if payload is not None
            else {"metric": "synthetic.measurement", "value": 1.0, "unit": "count"}
        ),
        "event_start_utc": "2026-01-01T08:00:00Z",
        "event_end_utc": None,
        "timestamp_precision": "instant",
        "source_timezone": "Etc/UTC",
        "offset_minutes": 0,
        "local_date": "2026-01-01",
        "day_policy_version": "day-v1",
        "source_updated_at": "2026-01-01T08:00:00Z",
        "ingested_at": "2026-01-01T09:00:00Z",
        "tombstone": tombstone,
    }


def register_source(conn: sqlite3.Connection, manifest: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO source_registry "
        "(source_id,source_type,display_name,capabilities_json,expected_cadence_days,"
        "consent_state,timestamp_semantics,canonical_timezone,revision_behavior,"
        "license_reference,contract_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(source_id) DO UPDATE SET "
        "source_type=excluded.source_type,display_name=excluded.display_name,"
        "capabilities_json=excluded.capabilities_json,expected_cadence_days=excluded.expected_cadence_days,"
        "consent_state=excluded.consent_state,timestamp_semantics=excluded.timestamp_semantics,"
        "canonical_timezone=excluded.canonical_timezone,revision_behavior=excluded.revision_behavior,"
        "license_reference=excluded.license_reference,contract_version=excluded.contract_version",
        (
            manifest["source_id"],
            manifest["source_type"],
            manifest["display_name"],
            _canonical_json(manifest["capabilities"]),
            manifest.get("expected_cadence_days"),
            manifest["consent_state"],
            manifest["timestamp_semantics"],
            manifest.get("canonical_timezone"),
            manifest["revision_behavior"],
            manifest.get("license_reference"),
            manifest["contract_version"],
            _now(),
        ),
    )
    conn.commit()


def register_metric(conn: sqlite3.Connection, metric: Mapping[str, Any]) -> None:
    conn.execute(
        "INSERT INTO metric_definition "
        "(metric_id,display_name,value_kind,canonical_unit,percent_representation,"
        "aggregation_rule,minimum_value,maximum_value,measurement_method,local_day_policy,"
        "definition_version,created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(metric_id) DO UPDATE SET "
        "display_name=excluded.display_name,value_kind=excluded.value_kind,"
        "canonical_unit=excluded.canonical_unit,percent_representation=excluded.percent_representation,"
        "aggregation_rule=excluded.aggregation_rule,minimum_value=excluded.minimum_value,"
        "maximum_value=excluded.maximum_value,measurement_method=excluded.measurement_method,"
        "local_day_policy=excluded.local_day_policy,definition_version=excluded.definition_version",
        (
            metric["metric_id"],
            metric["display_name"],
            metric["value_kind"],
            metric.get("canonical_unit"),
            metric.get("percent_representation"),
            metric["aggregation_rule"],
            metric.get("minimum_value"),
            metric.get("maximum_value"),
            metric.get("measurement_method"),
            metric["local_day_policy"],
            metric["definition_version"],
            _now(),
        ),
    )
    conn.commit()


def register_source_metric_map(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_metric: str,
    metric_id: str,
    source_unit: str,
    factor: float,
    map_version: str = "map-v1",
) -> None:
    rule = _canonical_json({"operation": "multiply", "factor": factor})
    conn.execute(
        "INSERT INTO source_metric_map "
        "(source_id,source_metric,metric_id,source_unit,conversion_rule,map_version) "
        "VALUES (?,?,?,?,?,?)",
        (source_id, source_metric, metric_id, source_unit, rule, map_version),
    )
    conn.commit()


def convert_source_value(
    conn: sqlite3.Connection,
    *,
    source_id: str,
    source_metric: str,
    value: float,
    source_unit: str,
) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT m.metric_id,m.canonical_unit,map.source_unit,map.conversion_rule "
        "FROM source_metric_map AS map "
        "JOIN metric_definition AS m ON m.metric_id=map.metric_id "
        "WHERE map.source_id=? AND map.source_metric=? "
        "ORDER BY map.map_version DESC LIMIT 1",
        (source_id, source_metric),
    ).fetchone()
    if row is None:
        raise RecordValidationError("unknown_source_metric", "source_metric")
    if row["source_unit"] != source_unit:
        raise RecordValidationError("unit_mismatch", "source_unit")
    rule = json.loads(row["conversion_rule"] or "{}")
    if rule.get("operation") == "multiply":
        normalized = float(value) * float(rule["factor"])
    elif source_unit == row["canonical_unit"]:
        normalized = float(value)
    else:
        raise RecordValidationError("unsupported_conversion", "conversion_rule")
    return {"metric_id": row["metric_id"], "value": normalized, "unit": row["canonical_unit"]}


def add_source_epoch(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    source_id: str,
    starts_at: str,
    reason_category: str,
    comparable_to_previous: bool,
    metric_id: Optional[str] = None,
    ends_at: Optional[str] = None,
) -> Dict[str, Any]:
    epoch_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO source_epoch "
        "(epoch_id,subject_id,source_id,metric_id,starts_at,ends_at,reason_category,"
        "comparable_to_previous,created_at) VALUES (?,?,?,?,?,?,?,?,?)",
        (
            epoch_id,
            subject_id,
            source_id,
            metric_id,
            starts_at,
            ends_at,
            reason_category,
            1 if comparable_to_previous else 0,
            _now(),
        ),
    )
    conn.commit()
    return {"status": "accepted", "epoch_id": epoch_id}


def set_source_consent(conn: sqlite3.Connection, source_id: str, consent_state: str) -> None:
    if consent_state not in {"granted", "revoked", "not_required"}:
        raise ValueError("invalid consent state")
    updated = conn.execute(
        "UPDATE source_registry SET consent_state=? WHERE source_id=?",
        (consent_state, source_id),
    ).rowcount
    if updated != 1:
        raise KeyError("source not found")
    conn.commit()


def _record_issue(
    conn: sqlite3.Connection,
    reason_code: str,
    subject_id: Optional[str] = None,
    source_id: Optional[str] = None,
    source_record_key: Optional[str] = None,
    record_version_id: Optional[str] = None,
    field_name: Optional[str] = None,
) -> None:
    conn.execute(
        "INSERT INTO quality_issue "
        "(issue_id,subject_id,source_id,source_record_key,record_version_id,reason_code,"
        "field_name,detected_at,resolution_state) VALUES (?,?,?,?,?,?,?,?, 'open')",
        (
            str(uuid.uuid4()),
            subject_id,
            source_id,
            source_record_key,
            record_version_id,
            reason_code,
            field_name,
            _now(),
        ),
    )
    conn.commit()


def validate_envelope(envelope: Mapping[str, Any]) -> None:
    missing = sorted(REQUIRED_ENVELOPE_FIELDS - set(envelope))
    if missing:
        raise RecordValidationError("missing_field", missing[0])
    if not isinstance(envelope["payload"], Mapping):
        raise RecordValidationError("invalid_payload", "payload")
    if not isinstance(envelope["source_version"], int) or envelope["source_version"] < 1:
        raise RecordValidationError("invalid_version", "source_version")
    precision = envelope["timestamp_precision"]
    if precision not in {"date", "instant", "interval"}:
        raise RecordValidationError("invalid_timestamp_precision", "timestamp_precision")
    event_start = envelope.get("event_start_utc")
    if precision == "date" and event_start is not None:
        raise RecordValidationError("date_precision_has_timestamp", "event_start_utc")
    if precision != "date" and not event_start:
        raise RecordValidationError("timestamp_required", "event_start_utc")
    if event_start:
        try:
            parsed = datetime.fromisoformat(str(event_start).replace("Z", "+00:00"))
        except ValueError as exc:
            raise RecordValidationError("invalid_timestamp", "event_start_utc") from exc
        if parsed.tzinfo is None:
            raise RecordValidationError("timezone_required", "event_start_utc")
    try:
        local_day = date.fromisoformat(str(envelope["local_date"]))
    except ValueError as exc:
        raise RecordValidationError("invalid_local_date", "local_date") from exc
    if local_day.isoformat() != str(envelope["local_date"]):
        raise RecordValidationError("invalid_local_date", "local_date")
    offset = envelope.get("offset_minutes")
    if offset is not None and (not isinstance(offset, int) or offset < -840 or offset > 840):
        raise RecordValidationError("invalid_offset", "offset_minutes")
    if event_start and offset is not None:
        source_local_day = (
            parsed.astimezone(timezone.utc) + timedelta(minutes=offset)
        ).date()
        if source_local_day != local_day:
            raise RecordValidationError("local_date_offset_mismatch", "local_date")


def import_envelope(conn: sqlite3.Connection, envelope: Mapping[str, Any]) -> Dict[str, Any]:
    validate_envelope(envelope)
    payload_json = _canonical_json(envelope["payload"])
    payload_sha256 = _payload_hash(envelope["payload"])
    existing = conn.execute(
        "SELECT record_version_id,payload_sha256 FROM source_record_version "
        "WHERE source_id=? AND source_record_key=? AND source_version=?",
        (
            envelope["source_id"],
            envelope["source_record_key"],
            envelope["source_version"],
        ),
    ).fetchone()
    if existing:
        if existing["payload_sha256"] == payload_sha256:
            return {"status": "unchanged", "record_version_id": existing["record_version_id"]}
        _record_issue(
            conn,
            "version_conflict",
            subject_id=str(envelope["subject_id"]),
            source_id=str(envelope["source_id"]),
            source_record_key=str(envelope["source_record_key"]),
        )
        return {"status": "rejected", "reason_code": "version_conflict"}

    highest = conn.execute(
        "SELECT MAX(source_version) FROM source_record_version "
        "WHERE source_id=? AND source_record_key=?",
        (envelope["source_id"], envelope["source_record_key"]),
    ).fetchone()[0]
    if highest is not None and envelope["source_version"] <= highest:
        _record_issue(
            conn,
            "non_monotonic_version",
            subject_id=str(envelope["subject_id"]),
            source_id=str(envelope["source_id"]),
            source_record_key=str(envelope["source_record_key"]),
        )
        return {"status": "rejected", "reason_code": "non_monotonic_version"}

    record_version_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO source_record_version "
        "(record_version_id,subject_id,source_id,source_record_key,source_version,payload_json,"
        "payload_sha256,event_start_utc,event_end_utc,timestamp_precision,source_timezone,offset_minutes,local_date,"
        "day_policy_version,source_updated_at,ingested_at,tombstone,validation_state) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?, 'accepted')",
        (
            record_version_id,
            envelope["subject_id"],
            envelope["source_id"],
            envelope["source_record_key"],
            envelope["source_version"],
            payload_json,
            payload_sha256,
            envelope["event_start_utc"],
            envelope.get("event_end_utc"),
            envelope["timestamp_precision"],
            envelope.get("source_timezone"),
            envelope.get("offset_minutes"),
            envelope["local_date"],
            envelope["day_policy_version"],
            envelope.get("source_updated_at"),
            envelope["ingested_at"],
            1 if envelope.get("tombstone") else 0,
        ),
    )
    conn.commit()
    return {"status": "inserted", "record_version_id": record_version_id}


def normalize_numeric_fact(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    source_record_version_id: str,
    metric_id: str,
    value: float,
    unit: str,
    local_date: str,
    fact_type: str = "measurement",
    calculation_version: str = "normalize-v1",
) -> Dict[str, Any]:
    metric = conn.execute(
        "SELECT canonical_unit,minimum_value,maximum_value FROM metric_definition WHERE metric_id=?",
        (metric_id,),
    ).fetchone()
    if metric is None:
        _record_issue(
            conn,
            "unknown_metric",
            subject_id=subject_id,
            record_version_id=source_record_version_id,
            field_name="metric_id",
        )
        return {"status": "quarantined", "reason_code": "unknown_metric"}
    if metric["canonical_unit"] != unit:
        _record_issue(
            conn,
            "unit_mismatch",
            subject_id=subject_id,
            record_version_id=source_record_version_id,
            field_name="unit",
        )
        return {"status": "quarantined", "reason_code": "unit_mismatch"}
    if metric["minimum_value"] is not None and value < metric["minimum_value"]:
        _record_issue(conn, "out_of_range", subject_id, record_version_id=source_record_version_id)
        return {"status": "quarantined", "reason_code": "out_of_range"}
    if metric["maximum_value"] is not None and value > metric["maximum_value"]:
        _record_issue(conn, "out_of_range", subject_id, record_version_id=source_record_version_id)
        return {"status": "quarantined", "reason_code": "out_of_range"}
    existing = conn.execute(
        "SELECT fact_id FROM normalized_fact "
        "WHERE source_record_version_id=? AND metric_id=? AND calculation_version=?",
        (source_record_version_id, metric_id, calculation_version),
    ).fetchone()
    if existing:
        return {"status": "unchanged", "fact_id": existing["fact_id"]}
    fact_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO normalized_fact "
        "(fact_id,subject_id,fact_type,metric_id,local_date,numeric_value,unit,"
        "source_record_version_id,validation_state,calculation_version,computed_at) "
        "VALUES (?,?,?,?,?,?,?,?, 'accepted',?,?)",
        (
            fact_id,
            subject_id,
            fact_type,
            metric_id,
            local_date,
            value,
            unit,
            source_record_version_id,
            calculation_version,
            _now(),
        ),
    )
    conn.commit()
    return {"status": "accepted", "fact_id": fact_id}


def add_context_event(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    category: str,
    starts_on: str,
    ends_on: Optional[str] = None,
    source_record_version_id: Optional[str] = None,
    **extra: Any,
) -> Dict[str, Any]:
    if extra:
        raise ValueError("context events do not accept narrative or untyped fields")
    if category not in CONTEXT_CATEGORIES:
        raise ValueError("unsupported context category")
    if ends_on is not None and ends_on < starts_on:
        raise ValueError("ends_on precedes starts_on")
    existing = conn.execute(
        "SELECT context_event_id FROM context_event "
        "WHERE subject_id=? AND category=? AND starts_on=? AND COALESCE(ends_on,'')=COALESCE(?, '')",
        (subject_id, category, starts_on, ends_on),
    ).fetchone()
    if existing:
        return {"status": "unchanged", "context_event_id": existing["context_event_id"]}
    event_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO context_event "
        "(context_event_id,subject_id,category,starts_on,ends_on,source_record_version_id,created_at) "
        "VALUES (?,?,?,?,?,?,?)",
        (event_id, subject_id, category, starts_on, ends_on, source_record_version_id, _now()),
    )
    conn.commit()
    return {"status": "accepted", "context_event_id": event_id}


def _import_rows(
    conn: sqlite3.Connection,
    rows: Iterable[Mapping[str, Any]],
    subject_id_override: Optional[str] = None,
) -> Dict[str, int]:
    receipt = {"pulled": 0, "inserted": 0, "unchanged": 0, "rejected": 0}
    for original in rows:
        envelope = dict(original)
        if subject_id_override is not None:
            envelope["subject_id"] = subject_id_override
        receipt["pulled"] += 1
        try:
            result = import_envelope(conn, envelope)
        except (RecordValidationError, KeyError, sqlite3.IntegrityError) as exc:
            reason = exc.reason_code if isinstance(exc, RecordValidationError) else "invalid_record"
            subject_id = envelope.get("subject_id")
            source_id = envelope.get("source_id")
            source_key = envelope.get("source_record_key")
            if subject_id and not conn.execute(
                "SELECT 1 FROM subject WHERE subject_id=?", (subject_id,)
            ).fetchone():
                subject_id = None
            if source_id and conn.execute(
                "SELECT 1 FROM source_registry WHERE source_id=?", (source_id,)
            ).fetchone():
                _record_issue(
                    conn,
                    reason,
                    subject_id=subject_id,
                    source_id=source_id,
                    source_record_key=source_key,
                    field_name=getattr(exc, "field_name", None),
                )
            receipt["rejected"] += 1
            continue
        receipt[result["status"]] += 1
    return receipt


def import_jsonl(
    conn: sqlite3.Connection,
    path: Path,
    subject_id_override: Optional[str] = None,
) -> Dict[str, int]:
    def rows() -> Iterable[Mapping[str, Any]]:
        with path.open(encoding="utf-8") as handle:
            for line in handle:
                if line.strip():
                    yield json.loads(line)

    return _import_rows(conn, rows(), subject_id_override)


def import_csv(
    conn: sqlite3.Connection,
    path: Path,
    subject_id_override: Optional[str] = None,
) -> Dict[str, int]:
    def rows() -> Iterable[Mapping[str, Any]]:
        with path.open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                converted = dict(row)
                converted["source_version"] = int(converted["source_version"])
                converted["offset_minutes"] = (
                    int(converted["offset_minutes"]) if converted.get("offset_minutes") else None
                )
                converted["tombstone"] = converted.get("tombstone", "").lower() in {"1", "true", "yes"}
                converted["payload"] = json.loads(converted["payload"])
                yield converted

    return _import_rows(conn, rows(), subject_id_override)
