"""Deterministic, fully synthetic multi-domain demonstration database."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List

from . import analytics, database, records


METRICS = (
    ("demo.energy", "Energy", "kcal", "food_entry", 1800.0),
    ("demo.fiber", "Fiber", "g", "food_entry", 24.0),
    ("demo.sleep_score", "Sleep score", "score", "sleep_session", 72.0),
    ("demo.body_mass", "Body mass", "kg", "measurement", 82.0),
    ("demo.activity_sessions", "Activity sessions", "count", "activity_session", 1.0),
    ("demo.grip_strength", "Grip strength", "kg", "capacity_assessment", 32.0),
)


def _metric(metric_id: str, display_name: str, unit: str) -> Dict[str, Any]:
    return {
        "metric_id": metric_id,
        "display_name": display_name,
        "value_kind": "number",
        "canonical_unit": unit,
        "percent_representation": None,
        "aggregation_rule": "daily_last",
        "minimum_value": 0,
        "maximum_value": None,
        "measurement_method": "synthetic fixture",
        "local_day_policy": "source_local_date",
        "definition_version": "demo-metric-v1",
    }


def build_demo(path: Path) -> Dict[str, Any]:
    subject_id = database.initialize(path)
    with database.connect(path) as conn:
        manifest = records.synthetic_source_manifest()
        manifest.update(
            {
                "display_name": "Synthetic multi-domain demo",
                "capabilities": ["nutrition", "sleep", "measurement", "activity", "assessment"],
            }
        )
        records.register_source(conn, manifest)
        for metric_id, display_name, unit, _, _ in METRICS:
            records.register_metric(conn, _metric(metric_id, display_name, unit))

        start = date(2026, 1, 1)
        for day_offset in range(7):
            local_day = start + timedelta(days=day_offset)
            event_time = datetime(
                local_day.year,
                local_day.month,
                local_day.day,
                8,
                tzinfo=timezone.utc,
            ).isoformat().replace("+00:00", "Z")
            for metric_id, _, unit, fact_type, base_value in METRICS:
                key = f"{metric_id}:{local_day.isoformat()}"
                envelope = records.synthetic_envelope(
                    subject_id,
                    source_record_key=key,
                    payload={
                        "metric": metric_id,
                        "value": base_value + day_offset,
                        "unit": unit,
                    },
                )
                envelope.update(
                    {
                        "event_start_utc": event_time,
                        "source_updated_at": event_time,
                        "ingested_at": event_time,
                        "local_date": local_day.isoformat(),
                    }
                )
                imported = records.import_envelope(conn, envelope)
                records.normalize_numeric_fact(
                    conn,
                    subject_id=subject_id,
                    source_record_version_id=imported["record_version_id"],
                    metric_id=metric_id,
                    value=base_value + day_offset,
                    unit=unit,
                    local_date=local_day.isoformat(),
                    fact_type=fact_type,
                    calculation_version="demo-normalize-v1",
                )

        records.add_context_event(
            conn,
            subject_id=subject_id,
            category="travel",
            starts_on="2026-01-03",
            ends_on="2026-01-04",
        )
        records.add_context_event(
            conn,
            subject_id=subject_id,
            category="device_replacement",
            starts_on="2026-01-06",
        )
        created = "2026-01-01T00:00:00Z"
        conn.execute("INSERT OR IGNORE INTO assessment_protocol VALUES (?,?,?,?,?)",
                     ("demo-capacity", "v1", "grip", "same-version", created))
        conn.execute("INSERT OR IGNORE INTO assessment_required_metric VALUES (?,?,?)",
                     ("demo-capacity", "v1", "demo.grip_strength"))
        sessions = (("s1", "2026-01-01", "complete", 32.0),
                    ("s2", "2026-01-02", "complete", 33.0),
                    ("s3", "2026-01-03", "partial", 34.0))
        for session_id, local_day, state, value in sessions:
            snapshot = analytics.input_snapshot_hash([{"metric_id": "demo.grip_strength",
                                                        "local_date": local_day, "value": value}])
            conn.execute("INSERT OR IGNORE INTO assessment_attempt VALUES (?,?,?,?,?,?,?,?,?)",
                         (f"attempt-{session_id}", subject_id, "demo-capacity", "v1",
                          "demo.grip_strength", "demo-metric-v1", local_day, value, created))
            conn.execute("INSERT OR IGNORE INTO assessment_session VALUES (?,?,?,?,?,?,?,?)",
                         (session_id, subject_id, "demo-capacity", "v1", local_day,
                          "partial", snapshot, created))
            if state == "complete":
                conn.execute("UPDATE assessment_session SET completeness_state='complete' WHERE session_id=?",
                             (session_id,))
        conn.execute("INSERT OR IGNORE INTO association_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     ("demo-fiber-sleep", "v1", "demo.fiber", "demo-metric-v1",
                      "demo.sleep_score", "demo-metric-v1", '[]', "lag_days:0",
                      '{"minimum_pairs":7}', "continuous", "theil_sen_median_slope",
                      "observational", created))
        common = {"subject_id": subject_id, "method_version": "demo-v1",
                  "policy_version": "demo-policy-v1"}
        conn.execute("INSERT OR IGNORE INTO source_epoch VALUES (?,?,?,?,?,?,?,?,?)",
                     ("demo-epoch-v1", subject_id, "synthetic.json", "demo.sleep_score",
                      "2026-01-01T00:00:00Z", None, "initial", 0, created))
        rows = [{"date": f"2026-01-0{day}", "event_at": f"2026-01-0{day}T08:00:00Z",
                 "value": day, "usable": True, "epoch_id": "demo-epoch-v1",
                 "comparable_to_previous": False}
                for day in range(1, 8)]
        analytics.derive_and_persist_coverage(
            conn, **common, metric_id="demo.fiber", start=start,
            end=start + timedelta(days=6), input_rows=rows)
        analytics.derive_and_persist_trend(
            conn, **common, metric_id="demo.sleep_score", as_of=start + timedelta(days=6),
            window_days=7, baseline_days=7, input_rows=rows)
        analytics.derive_and_persist_association(
            conn, **common, association_id="demo-fiber-sleep@v1", start=start,
            period_start=start, period_end=start + timedelta(days=6),
            input_rows=[{"exposure": day, "outcome": day * 2,
                         "exposure_date": f"2026-01-0{day}",
                         "outcome_date": f"2026-01-0{day}"} for day in range(1, 8)])
        analytics.derive_and_persist_assessment_change(
            conn, **common, metric_id="demo.grip_strength", protocol_id="demo-capacity@v1")
        return {
            "subject_id": subject_id,
            "raw_versions": conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0],
            "normalized_facts": conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0],
            "metrics": conn.execute("SELECT COUNT(*) FROM metric_definition").fetchone()[0],
            "context_events": conn.execute("SELECT COUNT(*) FROM context_event").fetchone()[0],
        }
