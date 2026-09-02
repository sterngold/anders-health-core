"""Transparent, cadence-aware analytical primitives."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import uuid
from datetime import datetime, timezone
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Tuple


DateValue = Tuple[date, float]


def _day_set(days: Iterable[date]) -> List[date]:
    return sorted(set(days))


def _missing_gaps(observed: Sequence[date], start: date, end: date) -> List[int]:
    present = set(observed)
    gaps: List[int] = []
    current = 0
    cursor = start
    while cursor <= end:
        if cursor in present:
            if current:
                gaps.append(current)
            current = 0
        else:
            current += 1
        cursor += timedelta(days=1)
    if current:
        gaps.append(current)
    return gaps


def coverage(
    usable_dates: Iterable[date],
    *,
    start: date,
    end: date,
    quarantined_records: int = 0,
) -> Dict[str, Any]:
    usable = [day for day in _day_set(usable_dates) if start <= day <= end]
    possible = (end - start).days + 1
    gaps = _missing_gaps(usable, start, end)
    return {
        "possible_days": possible,
        "usable_days": len(usable),
        "missing_days": possible - len(usable),
        "longest_gap_days": max(gaps, default=0),
        "quarantined_records": quarantined_records,
        "first_usable_date": usable[0].isoformat() if usable else None,
        "latest_usable_date": usable[-1].isoformat() if usable else None,
    }


def _direction(magnitude: float, tolerance: float = 1e-12) -> str:
    if magnitude > tolerance:
        return "up"
    if magnitude < -tolerance:
        return "down"
    return "stable"


def _week_buckets(days: Iterable[date]) -> int:
    return len({(day.isocalendar()[0], day.isocalendar()[1]) for day in days})


def food_trend(rows: Iterable[Tuple[date, float, str]], as_of: date) -> Dict[str, Any]:
    values_by_day: Dict[date, List[float]] = {}
    for day, value, state in rows:
        if state in {"complete", "likely_complete"} and as_of - timedelta(days=29) <= day <= as_of:
            values_by_day.setdefault(day, []).append(float(value))
    usable = sorted(
        (day, statistics.median(values)) for day, values in values_by_day.items()
    )
    recent_start = as_of - timedelta(days=6)
    recent = [(day, value) for day, value in usable if day >= recent_start]
    if len(recent) >= 6:
        status = "normal"
    elif len(recent) >= 3:
        status = "provisional"
    elif _week_buckets(day for day, _ in usable) >= 3:
        status = "historical_context"
    elif len(usable) >= 6 and not recent:
        status = "historical_only"
    else:
        status = "insufficient"
    values = [value for _, value in recent]
    return {
        "status": status,
        "eligible_days": len(recent),
        "historical_days": len(usable),
        "mean": statistics.fmean(values) if values else None,
        "current_interpretation": status in {"normal", "provisional"},
    }


def window_trend(
    rows: Iterable[DateValue],
    as_of: date,
    *,
    window_days: int,
    baseline_days: int,
) -> Dict[str, Any]:
    values = sorted((day, float(value)) for day, value in rows if day <= as_of)
    current_start = as_of - timedelta(days=window_days - 1)
    baseline_start = as_of - timedelta(days=baseline_days - 1)
    current = [value for day, value in values if day >= current_start]
    baseline = [value for day, value in values if day >= baseline_start]
    if not current or not baseline:
        return {
            "status": "insufficient",
            "eligible_days": len(current),
            "baseline_days": len(baseline),
            "direction": None,
            "magnitude": None,
        }
    magnitude = statistics.median(current) - statistics.median(baseline)
    return {
        "status": "normal" if len(current) >= window_days else "provisional",
        "eligible_days": len(current),
        "baseline_days": len(baseline),
        "direction": _direction(magnitude),
        "magnitude": round(magnitude, 12),
    }


def _ranks(values: Sequence[float]) -> List[float]:
    indexed = sorted(enumerate(values), key=lambda item: item[1])
    ranks = [0.0] * len(values)
    position = 0
    while position < len(indexed):
        end = position
        while end + 1 < len(indexed) and indexed[end + 1][1] == indexed[position][1]:
            end += 1
        average = (position + end + 2) / 2.0
        for index in range(position, end + 1):
            ranks[indexed[index][0]] = average
        position = end + 1
    return ranks


def _pearson(left: Sequence[float], right: Sequence[float]) -> float:
    left_mean = statistics.fmean(left)
    right_mean = statistics.fmean(right)
    numerator = sum((a - left_mean) * (b - right_mean) for a, b in zip(left, right))
    left_scale = math.sqrt(sum((a - left_mean) ** 2 for a in left))
    right_scale = math.sqrt(sum((b - right_mean) ** 2 for b in right))
    if left_scale == 0 or right_scale == 0:
        return 0.0
    return numerator / (left_scale * right_scale)


def association(
    exposures: Iterable[DateValue],
    outcomes: Iterable[DateValue],
    *,
    lag_days: int,
    min_pairs: int,
) -> Dict[str, Any]:
    exposure_groups: Dict[date, List[float]] = {}
    outcome_groups: Dict[date, List[float]] = {}
    for day, value in exposures:
        exposure_groups.setdefault(day, []).append(float(value))
    for day, value in outcomes:
        outcome_groups.setdefault(day, []).append(float(value))
    exposure_by_day = {
        day: statistics.median(values) for day, values in exposure_groups.items()
    }
    outcome_by_day = {
        day: statistics.median(values) for day, values in outcome_groups.items()
    }
    pairs = [
        (float(value), outcome_by_day[day + timedelta(days=lag_days)])
        for day, value in exposure_by_day.items()
        if day + timedelta(days=lag_days) in outcome_by_day
    ]
    if len(pairs) < min_pairs:
        return {
            "status": "insufficient",
            "paired_count": len(pairs),
            "claim_type": "observational",
            "effect_method": None,
            "effect_value": None,
        }
    effect = _pearson(_ranks([p[0] for p in pairs]), _ranks([p[1] for p in pairs]))
    return {
        "status": "early_association",
        "paired_count": len(pairs),
        "claim_type": "observational",
        "effect_method": "spearman_rank_direction",
        "effect_value": round(effect, 6),
    }


def relationship_eligible(kind: str, food_days: Iterable[date], outcome_days: Iterable[date]) -> bool:
    food = _day_set(food_days)
    outcomes = _day_set(outcome_days)
    if kind == "food_weight":
        return len(food) >= 7 and len(outcomes) >= 3
    if kind == "food_body_composition":
        if len(food) < 14 or _week_buckets(food) < 3:
            return False
        gaps = _missing_gaps(food, food[0], food[-1])
        return max(gaps, default=0) <= 7
    raise ValueError("unknown relationship kind")


def assessment_change(sessions: Iterable[DateValue]) -> Dict[str, Any]:
    ordered = sorted((day, float(value)) for day, value in sessions)
    if not ordered:
        return {"status": "insufficient", "compatible_session_count": 0}
    status = "baseline" if len(ordered) == 1 else "change" if len(ordered) == 2 else "trend"
    delta = ordered[-1][1] - ordered[0][1]
    return {
        "status": status,
        "compatible_session_count": len(ordered),
        "baseline_date": ordered[0][0].isoformat(),
        "latest_date": ordered[-1][0].isoformat(),
        "baseline_value": ordered[0][1],
        "latest_value": ordered[-1][1],
        "delta_from_baseline": delta,
        "direction": _direction(delta),
    }


def input_snapshot_hash(rows: Iterable[Dict[str, Any]]) -> str:
    canonical_rows = [
        json.dumps(row, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        for row in rows
    ]
    canonical = "[" + ",".join(sorted(canonical_rows)) + "]"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def persist_trend(
    conn: sqlite3.Connection,
    *,
    subject_id: str,
    metric_id: str,
    as_of_date: str,
    window_days: int,
    baseline_days: Any,
    result: Dict[str, Any],
    input_rows: Iterable[Dict[str, Any]],
    method_version: str,
    policy_version: str,
) -> Dict[str, str]:
    materialized_rows = list(input_rows)
    snapshot = input_snapshot_hash(materialized_rows)
    identity = {
        "subject_id": subject_id,
        "metric_id": metric_id,
        "as_of_date": as_of_date,
        "window_days": window_days,
        "baseline_days": baseline_days,
        "snapshot": snapshot,
        "method_version": method_version,
        "policy_version": policy_version,
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result_id = str(uuid.UUID(digest[:32]))
    receipt_id = str(uuid.UUID(digest[32:64]))
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    conn.execute(
        "INSERT INTO trend_result "
        "(result_id,subject_id,metric_id,as_of_date,window_days,baseline_days,status,"
        "eligible_days,possible_days,missing_days,longest_gap_days,direction,magnitude,"
        "method_version,policy_version,input_snapshot_hash,generated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
        "ON CONFLICT(result_id) DO NOTHING",
        (
            result_id,
            subject_id,
            metric_id,
            as_of_date,
            window_days,
            baseline_days,
            result["status"],
            result["eligible_days"],
            result["possible_days"],
            result["missing_days"],
            result.get("longest_gap_days"),
            result.get("direction"),
            result.get("magnitude"),
            method_version,
            policy_version,
            snapshot,
            generated_at,
        ),
    )
    conn.execute(
        "INSERT INTO derivation_receipt "
        "(receipt_id,subject_id,result_type,result_id,method_version,policy_version,"
        "input_snapshot_hash,input_count,output_count,exclusions_json,generated_at) "
        "VALUES (?,?,?,?,?,?,?,?,1,'[]',?) "
        "ON CONFLICT(receipt_id) DO NOTHING",
        (
            receipt_id,
            subject_id,
            "trend",
            result_id,
            method_version,
            policy_version,
            snapshot,
            len(materialized_rows),
            generated_at,
        ),
    )
    conn.commit()
    return {"result_id": result_id, "receipt_id": receipt_id, "input_snapshot_hash": snapshot}
