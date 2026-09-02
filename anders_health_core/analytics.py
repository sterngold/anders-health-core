"""Transparent, cadence-aware analytical primitives."""

from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import statistics
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from datetime import date, timedelta
from typing import Any, Dict, Iterable, List, Sequence, Tuple


DateValue = Tuple[date, float]


@dataclass(frozen=True)
class EpochTrendInput:
    local_date: date
    value: float
    epoch_id: str
    comparable_to_previous: bool


def _daily_medians(rows: Iterable[DateValue]) -> List[DateValue]:
    grouped: Dict[date, List[float]] = {}
    for day, value in rows:
        grouped.setdefault(day, []).append(float(value))
    return sorted((day, statistics.median(values)) for day, values in grouped.items())


def _epoch_daily_medians(
    rows: Iterable[EpochTrendInput], start: date, end: date
) -> Tuple[List[DateValue], List[str], List[Dict[str, str]]]:
    selected = [row for row in rows if start <= row.local_date <= end]
    by_epoch: Dict[str, List[EpochTrendInput]] = {}
    for row in selected:
        by_epoch.setdefault(row.epoch_id, []).append(row)
    epochs = sorted(by_epoch, key=lambda epoch_id: (min(row.local_date for row in by_epoch[epoch_id]), epoch_id))
    exclusions: List[Dict[str, str]] = []
    for previous, current in zip(epochs, epochs[1:]):
        flags = {row.comparable_to_previous for row in by_epoch[current]}
        if flags != {True}:
            exclusions.append({
                "reason": "non_comparable_epoch_transition",
                "from_epoch_id": previous,
                "to_epoch_id": current,
            })
    return _daily_medians((row.local_date, row.value) for row in selected), epochs, exclusions


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
    values = _daily_medians((day, value) for day, value in rows if day <= as_of)
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


def epoch_window_trend(
    rows: Iterable[EpochTrendInput], as_of: date, *, window_days: int, baseline_days: int
) -> Dict[str, Any]:
    current_start = as_of - timedelta(days=window_days - 1)
    baseline_start = as_of - timedelta(days=baseline_days - 1)
    values, epochs, exclusions = _epoch_daily_medians(rows, min(current_start, baseline_start), as_of)
    current = [value for day, value in values if day >= current_start]
    baseline = [value for day, value in values if day >= baseline_start]
    result = {
        "method": "epoch_cadence_median",
        "eligible_days": len(current), "possible_days": window_days,
        "missing_days": window_days - len(current),
        "baseline_observed_days": len(baseline), "baseline_possible_days": baseline_days,
        "baseline_missing_days": baseline_days - len(baseline),
        "epoch_id": epochs[0] if len(epochs) == 1 else None,
        "exclusions": exclusions,
    }
    if exclusions:
        return {**result, "status": "excluded_non_comparable_epoch", "direction": None, "magnitude": None}
    if not current or not baseline:
        return {**result, "status": "insufficient", "direction": None, "magnitude": None}
    magnitude = statistics.median(current) - statistics.median(baseline)
    return {
        **result,
        "status": "normal" if len(current) >= window_days and len(baseline) >= baseline_days else "provisional",
        "direction": _direction(magnitude), "magnitude": round(magnitude, 12),
    }


def history_comparison(
    rows: Iterable[EpochTrendInput], as_of: date, *, history_days: int
) -> Dict[str, Any]:
    if history_days not in {60, 90}:
        raise ValueError("history_days must be 60 or 90")
    start = as_of - timedelta(days=history_days - 1)
    values, _, exclusions = _epoch_daily_medians(rows, start, as_of)
    periods = []
    for offset in range(0, history_days, 30):
        period_start = start + timedelta(days=offset)
        period_end = period_start + timedelta(days=29)
        period_values = [value for day, value in values if period_start <= day <= period_end]
        periods.append({"start": period_start.isoformat(), "end": period_end.isoformat(),
                        "observed_days": len(period_values), "possible_days": 30,
                        "missing_days": 30 - len(period_values),
                        "median": statistics.median(period_values) if period_values else None})
    previous, current = periods[-2:]
    result = {"method": "non_overlapping_30_day_periods", "periods": periods, "exclusions": exclusions}
    if exclusions:
        return {**result, "status": "excluded_non_comparable_epoch", "direction": None, "magnitude": None}
    if previous["median"] is None or current["median"] is None:
        return {**result, "status": "insufficient", "direction": None, "magnitude": None}
    magnitude = current["median"] - previous["median"]
    return {**result, "status": "normal", "direction": _direction(magnitude), "magnitude": round(magnitude, 12)}


def weight_trend(
    rows: Iterable[DateValue], as_of: date, body_composition: Iterable[DateValue]
) -> Dict[str, Any]:
    weights = _daily_medians((day, value) for day, value in rows if as_of - timedelta(days=29) <= day <= as_of)
    smooth = [value for day, value in weights if day >= as_of - timedelta(days=6)]
    composition = _daily_medians((day, value) for day, value in body_composition if as_of - timedelta(days=29) <= day <= as_of)
    smoothed = statistics.median(smooth) if smooth else None
    has_direction_history = len(weights) == 30
    direction = _direction(smoothed - statistics.median([value for _, value in weights])) if smoothed is not None and has_direction_history else None
    return {
        "status": "normal" if len(smooth) >= 7 and has_direction_history else "provisional" if smooth else "insufficient",
        "smooth_window_days": 7, "direction_window_days": 30, "smoothed_value": smoothed,
        "direction": direction, "direction_observed_days": len(weights), "direction_possible_days": 30,
        "body_composition_context": {"cadence_days": 30,
        "observed_days": len(composition), "latest_value": composition[-1][1] if composition else None},
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
        windows = [food[index:index + 7] for index in range(max(0, len(food) - 6))]
        return any((window[-1] - window[0]).days == 6 and
                   len([day for day in outcomes if day > window[-1]]) >= 3
                   for window in windows)
    if kind == "food_body_composition":
        for anchor in outcomes:
            selected = [day for day in food if anchor - timedelta(days=30) <= day < anchor]
            if len(selected) >= 14 and _week_buckets(selected) >= 3 and \
                    max(_missing_gaps(selected, anchor - timedelta(days=30), anchor - timedelta(days=1)), default=0) <= 7:
                return True
        return False
    raise ValueError("unknown relationship kind")


def complete_assessment_sessions(
    attempts: Iterable[Dict[str, Any]], *, test_id: str, protocol_version: str,
    required_metrics: Iterable[str], compatibility_rule: str,
) -> List[Dict[str, Any]]:
    required = set(required_metrics)
    by_day: Dict[date, Dict[str, List[float]]] = {}
    if not required or not compatibility_rule:
        return []
    for row in attempts:
        if row.get("test_id") != test_id or row.get("protocol_version") != protocol_version:
            continue
        metric = row.get("metric_id")
        if metric in required:
            by_day.setdefault(row["local_date"], {}).setdefault(metric, []).append(float(row["value"]))
    return [
        {"local_date": day, "test_id": test_id, "protocol_version": protocol_version,
         "compatibility_rule": compatibility_rule,
         "values": {metric: statistics.median(metrics[metric]) for metric in required}}
        for day, metrics in sorted(by_day.items()) if set(metrics) == required
    ]


def assessment_change(sessions: Iterable[Any], metric_id: Any = None) -> Dict[str, Any]:
    materialized = list(sessions)
    if materialized and isinstance(materialized[0], dict):
        reference = tuple(materialized[0].get(key) for key in
                          ("test_id", "protocol_version", "compatibility_rule"))
        compatible = [row for row in materialized if reference == tuple(row.get(key) for key in
                      ("test_id", "protocol_version", "compatibility_rule")) and all(reference)]
        by_day = {row["local_date"]: float(row.get("values", {}).get(metric_id, row.get("value")))
                  for row in compatible if row.get("values", {}).get(metric_id, row.get("value")) is not None}
        ordered = sorted(by_day.items())
    else:
        ordered = sorted((day, float(value)) for day, value in materialized)
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


def pair_final_meal_to_sleep(
    meals: Iterable[Dict[str, Any]], sleeps: Iterable[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    substantial = [row for row in meals if row.get("substantial")]
    pairs, previous_end = [], None
    for sleep in sorted((row for row in sleeps if row.get("main")), key=lambda row: row["start"]):
        candidates = [row for row in substantial if row["end"] <= sleep["start"] and
                      (previous_end is None or row["end"] > previous_end)]
        if candidates:
            meal = max(candidates, key=lambda row: (row["end"], row["id"]))
            pairs.append({"meal_id": meal["id"], "sleep_id": sleep["id"],
                          "exposure": float(meal["exposure"]), "outcome": float(sleep["outcome"])})
        previous_end = sleep["end"]
    return pairs


def _quantile_bounds(values: Sequence[float]) -> Tuple[float, float]:
    ordered = sorted(values)
    return ordered[int((len(ordered) - 1) * .25)], ordered[int((len(ordered) - 1) * .75)]


def association_effect(
    pairs: Iterable[Tuple[float, float]], exposure_kind: str, *, possible_pairs: int,
    exclusions: Any = None, min_pairs: int = 7,
) -> Dict[str, Any]:
    rows = [(float(exposure), float(outcome)) for exposure, outcome in pairs]
    base = {"paired_count": len(rows), "possible_pairs": possible_pairs,
            "claim_type": "observational", "exclusions": list(exclusions or [])}
    if len(rows) < min_pairs:
        return {**base, "status": "insufficient", "effect_method": None, "effect_value": None,
                "rank_direction": None, "uncertainty_low": None, "uncertainty_high": None}
    if exposure_kind == "binary":
        groups = [[outcome for exposure, outcome in rows if bool(exposure) == flag] for flag in (False, True)]
        if not all(groups):
            return {**base, "status": "insufficient", "effect_method": None, "effect_value": None,
                    "rank_direction": None, "uncertainty_low": None, "uncertainty_high": None}
        effect = statistics.median(groups[1]) - statistics.median(groups[0])
        jackknife = []
        for index in range(len(rows)):
            subset = rows[:index] + rows[index + 1:]
            split = [[outcome for exposure, outcome in subset if bool(exposure) == flag] for flag in (False, True)]
            if all(split):
                jackknife.append(statistics.median(split[1]) - statistics.median(split[0]))
        low, high = min(jackknife), max(jackknife)
        method, rank = "median_difference", None
    elif exposure_kind == "continuous":
        slopes = [(right[1] - left[1]) / (right[0] - left[0]) for index, left in enumerate(rows)
                  for right in rows[index + 1:] if right[0] != left[0]]
        if not slopes:
            return {**base, "status": "insufficient", "effect_method": None, "effect_value": None,
                    "rank_direction": 0.0, "uncertainty_low": None, "uncertainty_high": None}
        effect, (low, high) = statistics.median(slopes), _quantile_bounds(slopes)
        rank = _pearson(_ranks([row[0] for row in rows]), _ranks([row[1] for row in rows]))
        method = "theil_sen_median_slope"
    else:
        raise ValueError("exposure_kind must be binary or continuous")
    return {**base, "status": "early_association", "effect_method": method,
            "effect_value": round(effect, 6), "rank_direction": None if rank is None else round(rank, 6),
            "uncertainty_low": round(low, 6), "uncertainty_high": round(high, 6)}


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
        "epoch_id": result.get("epoch_id"),
        "exclusions": result.get("exclusions", []),
    }
    digest = hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    result_id = str(uuid.UUID(digest[:32]))
    receipt_id = str(uuid.UUID(digest[32:64]))
    generated_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    exclusions_json = json.dumps(result.get("exclusions", []), sort_keys=True, separators=(",", ":"))
    conn.execute(
        "INSERT INTO trend_result "
        "(result_id,subject_id,metric_id,as_of_date,window_days,baseline_days,status,"
        "eligible_days,possible_days,missing_days,longest_gap_days,direction,magnitude,"
        "epoch_id,exclusions_json,method_version,policy_version,input_snapshot_hash,generated_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
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
            result.get("epoch_id"),
            exclusions_json,
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
        "VALUES (?,?,?,?,?,?,?,?,1,?,?) "
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
            exclusions_json,
            generated_at,
        ),
    )
    conn.commit()
    return {"result_id": result_id, "receipt_id": receipt_id, "input_snapshot_hash": snapshot}


def _persist_derived(
    conn: sqlite3.Connection, kind: str, rows: List[Dict[str, Any]], values: Dict[str, Any],
    *, method_version: str, policy_version: str, exclusions: List[Dict[str, Any]],
) -> Dict[str, str]:
    snapshot = input_snapshot_hash(rows)
    identity = json.dumps({"kind": kind, "values": values, "snapshot": snapshot,
                           "method": method_version, "policy": policy_version},
                          sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    result_id, receipt_id = str(uuid.UUID(digest[:32])), str(uuid.UUID(digest[32:]))
    generated = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    exclusions_json = json.dumps(exclusions, sort_keys=True, separators=(",", ":"))
    stored = {"result_id": result_id, **values, "exclusions_json": exclusions_json,
              "method_version": method_version, "policy_version": policy_version,
              "input_snapshot_hash": snapshot, "generated_at": generated}
    columns = list(stored)
    conn.execute("SAVEPOINT derived_result")
    try:
        conn.execute(f"INSERT INTO {kind}_result ({','.join(columns)}) VALUES "
                     f"({','.join('?' for _ in columns)}) ON CONFLICT(result_id) DO NOTHING",
                     tuple(stored[column] for column in columns))
        conn.execute("INSERT INTO derivation_receipt VALUES (?,?,?,?,?,?,?,?,?,?,?) "
                     "ON CONFLICT(receipt_id) DO NOTHING",
                     (receipt_id, values["subject_id"], kind, result_id, method_version,
                      policy_version, snapshot, len(rows), 1, exclusions_json, generated))
        conn.execute("RELEASE SAVEPOINT derived_result")
    except Exception:
        conn.execute("ROLLBACK TO SAVEPOINT derived_result")
        conn.execute("RELEASE SAVEPOINT derived_result")
        raise
    return {"result_id": result_id, "receipt_id": receipt_id, "input_snapshot_hash": snapshot}


def derive_and_persist_coverage(
    conn: sqlite3.Connection, *, input_rows: Iterable[Dict[str, Any]], method_version: str,
    policy_version: str, **spec: Any,
) -> Dict[str, str]:
    rows = list(input_rows)
    start, end = spec["start"], spec["end"]
    usable = [date.fromisoformat(row["date"]) for row in rows if row.get("usable")]
    event_times = [row["event_at"] for row in rows if row.get("event_at")]
    parsed_events = [(datetime.fromisoformat(value.replace("Z", "+00:00")), value) for value in event_times]
    for parsed, _ in parsed_events:
        if parsed.utcoffset() is None:
            raise ValueError("coverage event_at must be an offset-aware timestamp")
    quarantined = sum(bool(row.get("quarantined")) for row in rows)
    result = coverage(usable, start=start, end=end, quarantined_records=quarantined)
    exclusions = ([{"reason": "missing_local_days", "count": result["missing_days"]}]
                  if result["missing_days"] else [])
    values = {"subject_id": spec["subject_id"], "metric_id": spec.get("metric_id"),
              "source_id": spec.get("source_id"), "period_start": start.isoformat(),
              "period_end": end.isoformat(), "expected_records": spec.get("expected_records"),
              "observed_records": len(rows), "usable_records": len(usable),
              "possible_days": result["possible_days"], "usable_days": result["usable_days"],
              "missing_days": result["missing_days"], "longest_gap_days": result["longest_gap_days"],
              "quarantined_records": quarantined,
              "freshest_event_at": max(parsed_events, default=(None, None))[1]}
    return _persist_derived(conn, "coverage", rows, values, method_version=method_version,
                            policy_version=policy_version, exclusions=exclusions)


def derive_and_persist_trend(
    conn: sqlite3.Connection, *, input_rows: Iterable[Dict[str, Any]], method_version: str,
    policy_version: str, **spec: Any,
) -> Dict[str, str]:
    rows, as_of, window = list(input_rows), spec["as_of"], spec["window_days"]
    baseline = spec.get("baseline_days", window)
    if any(not row.get("epoch_id") or "comparable_to_previous" not in row for row in rows):
        raise ValueError("persisted trend requires epoch_id and comparability")
    epoch_columns = ("epoch_id", "source_id", "starts_at", "ends_at", "reason_category", "comparable_to_previous")
    declarations = [dict(zip(epoch_columns, row)) for row in conn.execute(
        "SELECT epoch_id,source_id,starts_at,ends_at,reason_category,comparable_to_previous FROM source_epoch WHERE subject_id=? AND metric_id=? ORDER BY starts_at,epoch_id",
        (spec["subject_id"], spec["metric_id"]))]
    declared_epochs = {row["epoch_id"]: (bool(row["comparable_to_previous"]), date.fromisoformat(row["starts_at"][:10]),
                                         date.fromisoformat(row["ends_at"][:10]) if row["ends_at"] else None) for row in declarations}
    for epoch_id in {row["epoch_id"] for row in rows}:
        if epoch_id not in declared_epochs:
            raise ValueError("persisted trend requires a declared metric epoch")
    if any(bool(row["comparable_to_previous"]) != declared_epochs[row["epoch_id"]][0] for row in rows):
        raise ValueError("persisted trend comparability contradicts its declared epoch")
    trend_rows = [EpochTrendInput(date.fromisoformat(row["date"]), float(row["value"]),
                                  row["epoch_id"], bool(row["comparable_to_previous"])) for row in rows]
    if any(row.local_date < declared_epochs[row.epoch_id][1] or
           (declared_epochs[row.epoch_id][2] and row.local_date > declared_epochs[row.epoch_id][2])
           for row in trend_rows):
        raise ValueError("persisted trend input falls outside its declared epoch")
    analysis_start = as_of - timedelta(days=max(window, baseline) - 1)
    spanning = [row for row in declarations if date.fromisoformat(row["starts_at"][:10]) <= as_of and
                (not row["ends_at"] or date.fromisoformat(row["ends_at"][:10]) >= analysis_start)]
    epoch_exclusions = [{"reason": "non_comparable_epoch_transition", "from_epoch_id": previous["epoch_id"],
                         "to_epoch_id": current["epoch_id"]} for previous, current in zip(spanning, spanning[1:])
                        if not current["comparable_to_previous"]]
    result = epoch_window_trend(trend_rows, as_of, window_days=window, baseline_days=baseline)
    for exclusion in epoch_exclusions:
        if exclusion not in result["exclusions"]: result["exclusions"].append(exclusion)
    if epoch_exclusions:
        result.update(status="excluded_non_comparable_epoch", direction=None, magnitude=None, epoch_id=None)
    cov = coverage((row.local_date for row in trend_rows), start=as_of - timedelta(days=window - 1), end=as_of)
    exclusions = list(result["exclusions"])
    if cov["missing_days"]:
        exclusions.append({"reason": "missing_local_days", "count": cov["missing_days"]})
    values = {"subject_id": spec["subject_id"], "metric_id": spec["metric_id"],
              "as_of_date": as_of.isoformat(), "window_days": window,
              "baseline_days": baseline, "status": result["status"],
              "eligible_days": result["eligible_days"], "possible_days": window,
              "missing_days": cov["missing_days"], "longest_gap_days": cov["longest_gap_days"],
              "direction": result["direction"], "magnitude": result["magnitude"],
              "epoch_id": result["epoch_id"]}
    return _persist_derived(conn, "trend", rows + [{"input_kind": "source_epoch", **row} for row in spanning], values, method_version=method_version,
                            policy_version=policy_version, exclusions=exclusions)


def derive_and_persist_association(
    conn: sqlite3.Connection, *, input_rows: Iterable[Dict[str, Any]], method_version: str,
    policy_version: str, **spec: Any,
) -> Dict[str, str]:
    rows = list(input_rows)
    association_id, separator, version = spec["association_id"].rpartition("@")
    definition = conn.execute("SELECT * FROM association_definition WHERE association_id=? AND definition_version=?", (association_id, version)).fetchone() if separator else None
    if definition is None:
        raise ValueError("declared association required")
    eligible = set(json.loads(definition["eligible_source_ids_json"])); material = [row for row in rows if not eligible or row.get("source_id") in eligible]
    exclusions = ([{"reason": "ineligible_source", "count": len(rows) - len(material)}] if len(material) < len(rows) else [])
    coverage_rule = json.loads(definition["coverage_rule_json"])
    expected_method = {"binary": "median_difference", "continuous": "theil_sen_median_slope"}[definition["exposure_kind"]]
    if definition["effect_method"] != expected_method:
        raise ValueError("unsupported declared association effect method")
    pairing_rule = definition["pairing_rule"]
    try:
        if pairing_rule == "next-main-sleep":
            if not material or any(row.get("kind") not in {"meal", "sleep"} for row in material):
                raise ValueError
            if any(not ({"id", "end", "substantial", "exposure"} if row["kind"] == "meal" else
                        {"id", "start", "end", "main", "outcome"}).issubset(row) for row in material):
                raise ValueError
            meals = [{**row, "end": datetime.fromisoformat(row["end"].replace("Z", "+00:00"))}
                     for row in material if row["kind"] == "meal"]
            sleeps = [{**row, "start": datetime.fromisoformat(row["start"].replace("Z", "+00:00")),
                       "end": datetime.fromisoformat(row["end"].replace("Z", "+00:00"))}
                      for row in material if row["kind"] == "sleep"]
            if any(row["end"].utcoffset() is None for row in meals) or any(
                row["start"].utcoffset() is None or row["end"].utcoffset() is None for row in sleeps
            ):
                raise ValueError
            if any(row["end"] < row["start"] for row in sleeps):
                raise ValueError
            paired, possible, lag = pair_final_meal_to_sleep(meals, sleeps), len(sleeps), 0
        elif pairing_rule.startswith("lag_days:"):
            lag = int(pairing_rule.split(":", 1)[1]); paired = material; possible = len(material)
            if any((date.fromisoformat(row["outcome_date"]) - date.fromisoformat(row["exposure_date"])).days != lag for row in paired):
                raise ValueError
        else:
            raise ValueError
    except (KeyError, TypeError, ValueError) as error:
        raise ValueError("inputs cannot prove the declared pairing rule") from error
    result = association_effect(((row["exposure"], row["outcome"]) for row in paired),
        definition["exposure_kind"], possible_pairs=possible, exclusions=exclusions,
        min_pairs=int(coverage_rule["minimum_pairs"]))
    values = {"subject_id": spec["subject_id"], "association_id": spec["association_id"],
              "exposure_metric_id": definition["exposure_metric_id"], "outcome_metric_id": definition["outcome_metric_id"],
              "period_start": spec["period_start"].isoformat(), "period_end": spec["period_end"].isoformat(),
              "lag_days": lag,
              "paired_count": result["paired_count"],
              "possible_pairs": result["possible_pairs"], "status": result["status"],
              "claim_type": "observational", "effect_method": result["effect_method"],
              "effect_value": result["effect_value"], "rank_direction": result["rank_direction"],
              "uncertainty_low": result["uncertainty_low"],
              "uncertainty_high": result["uncertainty_high"]}
    return _persist_derived(conn, "association", rows, values, method_version=method_version,
                            policy_version=policy_version, exclusions=result["exclusions"])


def derive_and_persist_assessment_change(
    conn: sqlite3.Connection, *, method_version: str, policy_version: str, **spec: Any,
) -> Dict[str, str]:
    protocol_id, separator, version = spec["protocol_id"].rpartition("@")
    protocol = conn.execute("SELECT * FROM assessment_protocol WHERE protocol_id=? AND protocol_version=?", (protocol_id, version)).fetchone() if separator else None
    if protocol is None or not protocol["compatibility_rule"]:
        raise ValueError("declared protocol with compatibility rule required")
    required_rows = list(conn.execute("SELECT * FROM assessment_required_metric WHERE protocol_id=? AND protocol_version=? ORDER BY metric_id", (protocol_id, version)))
    required = {row["metric_id"] for row in required_rows}
    if spec["metric_id"] not in required:
        raise ValueError("assessment metric is not required by declared protocol")
    sessions = list(conn.execute("SELECT * FROM assessment_session WHERE subject_id=? AND protocol_id=? AND protocol_version=? ORDER BY local_date", (spec["subject_id"], protocol_id, version)))
    attempts = list(conn.execute("SELECT * FROM assessment_attempt WHERE subject_id=? AND protocol_id=? AND protocol_version=? ORDER BY local_date,attempt_id", (spec["subject_id"], protocol_id, version)))
    complete_days = {row["local_date"] for row in sessions if row["completeness_state"] == "complete"}
    grouped: Dict[str, Dict[str, List[float]]] = {}
    for attempt in attempts:
        if attempt["local_date"] in complete_days and attempt["metric_id"] in required:
            grouped.setdefault(attempt["local_date"], {}).setdefault(attempt["metric_id"], []).append(attempt["numeric_value"])
    rows = [{"local_date": day, "value": statistics.median(metrics[spec["metric_id"]])}
            for day, metrics in sorted(grouped.items()) if required.issubset(metrics)]
    result = assessment_change((date.fromisoformat(row["local_date"]), row["value"]) for row in rows)
    possible = len(sessions)
    exclusions = ([{"reason": "incompatible_or_incomplete_session", "count": possible - len(rows)}]
                  if possible > len(rows) else [])
    values = {"subject_id": spec["subject_id"], "metric_id": spec["metric_id"],
              "protocol_id": spec["protocol_id"], "compatible_session_count": result["compatible_session_count"],
              "possible_sessions": possible, "baseline_date": result.get("baseline_date"),
              "latest_date": result.get("latest_date"), "baseline_value": result.get("baseline_value"),
              "latest_value": result.get("latest_value"), "delta_from_baseline": result.get("delta_from_baseline"),
              "status": result["status"], "direction": result.get("direction")}
    snapshot_rows = [{"kind": "protocol", **dict(protocol)}] + [
        {"kind": "required_metric", **dict(row)} for row in required_rows] + [
        {"kind": "session", **dict(row)} for row in sessions] + [
        {"kind": "attempt", **dict(row)} for row in attempts]
    return _persist_derived(conn, "assessment_change", snapshot_rows, values, method_version=method_version,
                            policy_version=policy_version, exclusions=exclusions)
