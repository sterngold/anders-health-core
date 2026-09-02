import importlib
import sqlite3
import tempfile
import unittest
from datetime import date, datetime, timedelta, timezone
from pathlib import Path


def load_module(testcase, name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        testcase.fail(f"required module {name} is unavailable: {exc}")


class AnalyticsContractTests(unittest.TestCase):
    def setUp(self):
        self.analytics = load_module(self, "anders_health_core.analytics")
        self.as_of = date(2026, 1, 30)

    def test_coverage_counts_missing_days_and_longest_gap_without_zero_fill(self):
        result = self.analytics.coverage(
            [date(2026, 1, 24), date(2026, 1, 27), date(2026, 1, 30)],
            start=date(2026, 1, 24),
            end=date(2026, 1, 30),
            quarantined_records=2,
        )
        self.assertEqual(7, result["possible_days"])
        self.assertEqual(3, result["usable_days"])
        self.assertEqual(4, result["missing_days"])
        self.assertEqual(2, result["longest_gap_days"])
        self.assertEqual(2, result["quarantined_records"])

    def test_food_trend_distinguishes_provisional_normal_insufficient_and_stale(self):
        provisional = [(self.as_of - timedelta(days=i), 100 + i, "complete") for i in range(3)]
        normal = [(self.as_of - timedelta(days=i), 100 + i, "likely_complete") for i in range(6)]
        insufficient = [(self.as_of - timedelta(days=i), 100 + i, "complete") for i in range(2)]
        stale = [(self.as_of - timedelta(days=10 + i), 100 + i, "complete") for i in range(6)]
        self.assertEqual("provisional", self.analytics.food_trend(provisional, self.as_of)["status"])
        self.assertEqual("normal", self.analytics.food_trend(normal, self.as_of)["status"])
        self.assertEqual("insufficient", self.analytics.food_trend(insufficient, self.as_of)["status"])
        stale_result = self.analytics.food_trend(stale, self.as_of)
        self.assertEqual("historical_only", stale_result["status"])
        self.assertEqual(0, stale_result["eligible_days"])
        self.assertEqual(6, stale_result["historical_days"])

    def test_food_fallback_requires_observations_across_three_weeks(self):
        distributed = [
            (date(2026, 1, 2), 100, "complete"),
            (date(2026, 1, 10), 101, "complete"),
            (date(2026, 1, 18), 102, "complete"),
        ]
        clustered = [
            (date(2026, 1, 18), 100, "complete"),
            (date(2026, 1, 19), 101, "complete"),
            (date(2026, 1, 20), 102, "complete"),
        ]
        self.assertEqual("historical_context", self.analytics.food_trend(distributed, self.as_of)["status"])
        self.assertEqual("insufficient", self.analytics.food_trend(clustered, self.as_of)["status"])

    def test_sleep_trend_uses_seven_nights_against_thirty_day_baseline(self):
        values = [(self.as_of - timedelta(days=i), 80.0 if i < 7 else 70.0) for i in range(30)]
        result = self.analytics.window_trend(values, self.as_of, window_days=7, baseline_days=30)
        self.assertEqual(7, result["eligible_days"])
        self.assertEqual(30, result["baseline_days"])
        self.assertEqual("up", result["direction"])
        self.assertEqual(10.0, result["magnitude"])

    def test_epoch_trend_uses_daily_median_and_explicit_comparable_transition(self):
        rows = [
            self.analytics.EpochTrendInput(self.as_of, value, "new", True)
            for value in (70.0, 80.0, 90.0)
        ] + [
            self.analytics.EpochTrendInput(self.as_of - timedelta(days=day), 80.0, "new", True)
            for day in range(1, 15)
        ] + [
            self.analytics.EpochTrendInput(self.as_of - timedelta(days=day), 70.0, "old", False)
            for day in range(15, 30)
        ]
        result = self.analytics.epoch_window_trend(rows, self.as_of, window_days=7, baseline_days=30)
        self.assertEqual("normal", result["status"])
        self.assertEqual(7, result["eligible_days"])
        self.assertEqual(7, result["possible_days"])
        self.assertEqual(30, result["baseline_observed_days"])
        self.assertEqual(30, result["baseline_possible_days"])
        self.assertEqual("up", result["direction"])
        self.assertEqual(5.0, result["magnitude"])
        self.assertEqual([], result["exclusions"])

    def test_epoch_trend_rejects_non_comparable_transition_and_same_day_inflation(self):
        crossing = [
            self.analytics.EpochTrendInput(self.as_of - timedelta(days=day), 80.0, "new", False)
            for day in range(7)
        ] + [
            self.analytics.EpochTrendInput(self.as_of - timedelta(days=day), 70.0, "old", False)
            for day in range(7, 30)
        ]
        blocked = self.analytics.epoch_window_trend(crossing, self.as_of, window_days=7, baseline_days=30)
        inflated = self.analytics.epoch_window_trend(
            [self.analytics.EpochTrendInput(self.as_of, float(value), "only", False) for value in range(7)],
            self.as_of,
            window_days=7,
            baseline_days=7,
        )
        self.assertEqual("excluded_non_comparable_epoch", blocked["status"])
        self.assertEqual(1, len(blocked["exclusions"]))
        self.assertIsNone(blocked["direction"])
        self.assertEqual(1, inflated["eligible_days"])
        self.assertEqual(7, inflated["possible_days"])

    def test_history_comparison_uses_only_non_overlapping_thirty_day_periods(self):
        rows = [
            self.analytics.EpochTrendInput(self.as_of - timedelta(days=day), 80.0 if day < 30 else 70.0, "only", False)
            for day in range(90)
        ]
        sixty = self.analytics.history_comparison(rows, self.as_of, history_days=60)
        ninety = self.analytics.history_comparison(rows, self.as_of, history_days=90)
        self.assertEqual("non_overlapping_30_day_periods", sixty["method"])
        self.assertEqual(2, len(sixty["periods"]))
        self.assertEqual(30, sixty["periods"][0]["observed_days"])
        self.assertEqual(30, sixty["periods"][0]["possible_days"])
        self.assertEqual(30, sixty["periods"][1]["observed_days"])
        self.assertEqual("up", sixty["direction"])
        self.assertEqual(3, len(ninety["periods"]))
        self.assertEqual(30, ninety["periods"][0]["possible_days"])
        with self.assertRaisesRegex(ValueError, "60 or 90"):
            self.analytics.history_comparison(rows, self.as_of, history_days=30)

    def test_weight_trend_requires_thirty_days_for_direction_and_keeps_body_composition_context(self):
        weights = [(self.as_of - timedelta(days=day), 80.0 if day < 7 else 70.0) for day in range(30)]
        body_composition = [(self.as_of - timedelta(days=20), 20.0)]
        result = self.analytics.weight_trend(weights, self.as_of, body_composition)
        partial = self.analytics.weight_trend(weights[:7], self.as_of, [])
        self.assertEqual((7, 30, 80.0, "up"), tuple(result[key] for key in ("smooth_window_days", "direction_window_days", "smoothed_value", "direction")))
        self.assertEqual((30, 1), tuple(result["body_composition_context"][key] for key in ("cadence_days", "observed_days")))
        self.assertNotIn("body_fat_direction", result)
        self.assertEqual((7, 30, "provisional", None), tuple(partial[key] for key in ("direction_observed_days", "direction_possible_days", "status", "direction")))

    def test_association_requires_seven_pairs_and_is_labelled_observational(self):
        exposures = [(date(2026, 1, 1) + timedelta(days=i), float(i)) for i in range(7)]
        outcomes = [(date(2026, 1, 2) + timedelta(days=i), float(i * 2)) for i in range(7)]
        early = self.analytics.association(exposures, outcomes, lag_days=1, min_pairs=7)
        insufficient = self.analytics.association(exposures[:6], outcomes[:6], lag_days=1, min_pairs=7)
        self.assertEqual("early_association", early["status"])
        self.assertEqual(7, early["paired_count"])
        self.assertEqual("observational", early["claim_type"])
        self.assertEqual("insufficient", insufficient["status"])

    def test_assessment_requires_second_session_for_change_and_third_for_trend(self):
        one = [(date(2026, 1, 1), 10.0)]
        two = one + [(date(2026, 2, 1), 12.0)]
        three = two + [(date(2026, 3, 1), 14.0)]
        self.assertEqual("baseline", self.analytics.assessment_change(one)["status"])
        self.assertEqual("change", self.analytics.assessment_change(two)["status"])
        self.assertEqual("trend", self.analytics.assessment_change(three)["status"])
        self.assertEqual(4.0, self.analytics.assessment_change(three)["delta_from_baseline"])

    def test_protocol_completion_has_literal_good_and_bad_arms(self):
        def attempt(day, metric, value, version="v1"):
            return {"local_date": day, "test_id": "capacity", "protocol_version": version,
                    "metric_id": metric, "value": value}
        attempts = [
            attempt(date(2026, 1, 1), "grip", 10), attempt(date(2026, 1, 1), "balance", 20),
            attempt(date(2026, 1, 2), "grip", 11),
            attempt(date(2026, 1, 3), "grip", 12), attempt(date(2026, 1, 3), "grip", 13),
            attempt(date(2026, 1, 4), "grip", 14), attempt(date(2026, 1, 4), "balance", 24),
            attempt(date(2026, 1, 5), "grip", 15), attempt(date(2026, 1, 5), "balance", 25),
            attempt(date(2026, 1, 6), "grip", 99, "v2"), attempt(date(2026, 1, 6), "balance", 99),
        ]
        sessions = self.analytics.complete_assessment_sessions(
            attempts, test_id="capacity", protocol_version="v1",
            required_metrics={"grip", "balance"}, compatibility_rule="same-test-version")
        result = self.analytics.assessment_change(sessions, metric_id="grip")
        self.assertEqual([date(2026, 1, 1), date(2026, 1, 4), date(2026, 1, 5)],
                         [row["local_date"] for row in sessions])
        self.assertEqual(("trend", 3, 5.0),
                         (result["status"], result["compatible_session_count"], result["delta_from_baseline"]))

    def test_interval_pairing_uses_final_meal_before_next_main_sleep(self):
        utc = timezone.utc
        sleeps, meals = [], []
        for day in range(7):
            start = datetime(2026, 1, 2 + day, 22, tzinfo=utc)
            sleeps.append({"id": f"s{day}", "start": start, "end": start + timedelta(hours=8),
                           "main": True, "outcome": 70 + day})
            meals.extend([
                {"id": f"early{day}", "end": start - timedelta(hours=5), "substantial": True, "exposure": 1},
                {"id": f"final{day}", "end": start - timedelta(hours=2), "substantial": True, "exposure": day + 1},
                {"id": f"late{day}", "end": start + timedelta(minutes=1), "substantial": True, "exposure": 9},
            ])
        pairs = self.analytics.pair_final_meal_to_sleep(meals, sleeps)
        self.assertEqual(7, len(pairs))
        self.assertEqual([f"final{i}" for i in range(7)], [row["meal_id"] for row in pairs])
        self.assertEqual("early_association", self.analytics.association_effect(
            [(row["exposure"], row["outcome"]) for row in pairs], "continuous", possible_pairs=7)["status"])
        self.assertEqual("insufficient", self.analytics.association_effect(
            [(row["exposure"], row["outcome"]) for row in pairs[:6]], "continuous", possible_pairs=7)["status"])

    def test_anchored_relationship_windows_have_literal_good_and_bad_arms(self):
        food7 = [date(2026, 1, day) for day in range(1, 8)]
        weights = [date(2026, 1, day) for day in (8, 10, 12)]
        self.assertTrue(self.analytics.relationship_eligible("food_weight", food7, weights))
        self.assertFalse(self.analytics.relationship_eligible("food_weight", food7, weights[:2]))
        self.assertFalse(self.analytics.relationship_eligible("food_weight", food7, [date(2025, 12, 31)] * 3))
        anchor = date(2026, 2, 1)
        spread = [anchor - timedelta(days=day) for day in range(1, 30, 2)]
        boundary_gap = [anchor - timedelta(days=day) for day in range(1, 15)]
        gapped = [anchor - timedelta(days=day) for day in list(range(1, 8)) + list(range(16, 23))]
        self.assertTrue(self.analytics.relationship_eligible("food_body_composition", spread, [anchor]))
        self.assertFalse(self.analytics.relationship_eligible("food_body_composition", boundary_gap, [anchor]))
        self.assertFalse(self.analytics.relationship_eligible("food_body_composition", gapped, [anchor]))
        self.assertFalse(self.analytics.relationship_eligible("food_body_composition", spread, [anchor - timedelta(days=40)]))

    def test_binary_and_continuous_effects_are_robust_and_explicit(self):
        binary = self.analytics.association_effect(
            [(0, 7), (0, 8), (0, 9), (0, 10), (1, 12), (1, 13), (1, 14), (1, 15)],
            "binary", possible_pairs=10, exclusions=[{"reason": "missing", "count": 2}])
        continuous = self.analytics.association_effect(
            [(1, 2), (2, 4), (3, 6), (4, 8), (5, 10), (6, 12), (7, 14)],
            "continuous", possible_pairs=7)
        self.assertEqual(("median_difference", 5.0, 8, 10, "observational"),
                         tuple(binary[key] for key in ("effect_method", "effect_value", "paired_count", "possible_pairs", "claim_type")))
        self.assertEqual(("theil_sen_median_slope", 2.0, 1.0),
                         tuple(continuous[key] for key in ("effect_method", "effect_value", "rank_direction")))
        self.assertLessEqual(binary["uncertainty_low"], binary["uncertainty_high"])
        self.assertEqual([{"reason": "missing", "count": 2}], binary["exclusions"])

    def test_all_four_derivations_persist_once_and_unobserved_epoch_transition_blocks(self):
        database = load_module(self, "anders_health_core.database")
        records = load_module(self, "anders_health_core.records")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "all-results.db"
            subject_id = database.initialize(db_path)
            with database.connect(db_path) as conn:
                records.register_metric(conn, records.synthetic_metric_definition())
                records.register_source(conn, records.synthetic_source_manifest())
                with self.subTest("undefined assessment protocol"):
                    with self.assertRaisesRegex(ValueError, "declared protocol"):
                        self.analytics.derive_and_persist_assessment_change(conn, subject_id=subject_id,
                            metric_id="synthetic.measurement", protocol_id="absent@v9",
                            method_version="v1", policy_version="p1")
                with self.subTest("undefined association"):
                    with self.assertRaisesRegex(ValueError, "declared association"):
                        self.analytics.derive_and_persist_association(conn, subject_id=subject_id,
                            association_id="absent@v9", period_start=date(2026, 1, 1), period_end=date(2026, 1, 7),
                            input_rows=[{"exposure": i, "outcome": i} for i in range(7)],
                            method_version="v1", policy_version="p1")
                conn.execute("INSERT INTO assessment_protocol VALUES (?,?,?,?,?)", ("capacity", "v1", "grip", "same-test-version", "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO assessment_required_metric VALUES (?,?,?)", ("capacity", "v1", "synthetic.measurement"))
                with self.assertRaisesRegex(sqlite3.IntegrityError, "required attempts"):
                    conn.execute("INSERT INTO assessment_session VALUES (?,?,?,?,?,?,?,?)", ("false", subject_id, "capacity", "v1", "2025-12-01", "complete", "0" * 64, "2026-01-01T00:00:00Z"))
                conn.execute("SAVEPOINT insufficient")
                conn.execute("INSERT INTO assessment_session VALUES (?,?,?,?,?,?,?,?)", ("partial-only", subject_id, "capacity", "v1", "2025-12-01", "partial", "0" * 64, "2026-01-01T00:00:00Z"))
                self.analytics.derive_and_persist_assessment_change(conn, subject_id=subject_id, metric_id="synthetic.measurement", protocol_id="capacity@v1", method_version="v1", policy_version="p1")
                self.assertEqual(("insufficient", None, None), tuple(conn.execute("SELECT status,baseline_date,latest_date FROM assessment_change_result").fetchone()))
                conn.execute("ROLLBACK TO insufficient"); conn.execute("RELEASE insufficient")
                conn.executemany("INSERT INTO assessment_attempt VALUES (?,?,?,?,?,?,?,?,?)", [(f"a{i}", subject_id, "capacity", "v1", "synthetic.measurement", "metric-v1", day, value, "2026-01-01T00:00:00Z") for i, (day, value) in enumerate((("2026-01-01", 1), ("2026-02-01", 2), ("2026-03-01", 99)))])
                conn.executemany("INSERT INTO assessment_session VALUES (?,?,?,?,?,?,?,?)", [(f"s{i}", subject_id, "capacity", "v1", day, state, str(i) * 64, "2026-01-01T00:00:00Z") for i, (day, state) in enumerate((("2026-01-01", "complete"), ("2026-02-01", "complete"), ("2026-03-01", "partial")))])
                conn.execute("INSERT INTO association_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("food-sleep", "v1", "synthetic.measurement", "metric-v1", "synthetic.measurement", "metric-v1", "[]", "lag_days:0", '{"minimum_pairs":7}', "continuous", "theil_sen_median_slope", "observational", "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO source_epoch VALUES (?,?,?,?,?,?,?,?,?)", ("epoch-v1", subject_id, "synthetic.json", "synthetic.measurement", "2026-01-01T00:00:00Z", None, "initial", 0, "2026-01-01T00:00:00Z"))
                association_args = {"subject_id": subject_id,
                    "period_start": date(2026, 1, 1), "period_end": date(2026, 1, 7),
                    "input_rows": [{"exposure": i, "outcome": i * 2, "exposure_date": f"2026-01-0{i}", "outcome_date": f"2026-01-0{i}"} for i in range(1, 8)],
                    "method_version": "v1", "policy_version": "p1"}
                self.analytics.derive_and_persist_association(conn, association_id="food-sleep@v1", **association_args)
                self.assertEqual("food-sleep@v1", conn.execute("SELECT association_id FROM association_result").fetchone()[0])
                conn.execute("SAVEPOINT canonical_association")
                conn.execute("INSERT INTO association_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("canonical-name@v1", "v1", "synthetic.measurement", "metric-v1", "synthetic.measurement", "metric-v1", "[]", "lag_days:0", '{"minimum_pairs":7}', "continuous", "theil_sen_median_slope", "observational", "2026-01-01T00:00:00Z"))
                try:
                    self.analytics.derive_and_persist_association(conn, association_id="canonical-name@v1", **association_args)
                except ValueError as error:
                    self.fail(f"canonical stored association ID rejected: {error}")
                self.assertEqual("canonical-name@v1", conn.execute("SELECT association_id FROM association_result WHERE association_id='canonical-name@v1'").fetchone()[0])
                conn.execute("ROLLBACK TO canonical_association"); conn.execute("RELEASE canonical_association")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    conn.execute("UPDATE assessment_protocol SET compatibility_rule='changed'")
                with self.assertRaisesRegex(sqlite3.IntegrityError, "immutable"):
                    conn.execute("UPDATE association_definition SET pairing_rule='changed'")
                calls = [
                    lambda: self.analytics.derive_and_persist_coverage(conn, subject_id=subject_id,
                        metric_id="synthetic.measurement", start=date(2026, 1, 1), end=date(2026, 1, 7),
                        input_rows=[{"date": "2026-01-01", "event_at": "2026-01-01T08:00:00Z", "usable": True}], method_version="v1", policy_version="p1"),
                    lambda: self.analytics.derive_and_persist_trend(conn, subject_id=subject_id,
                        metric_id="synthetic.measurement", as_of=date(2026, 1, 7), window_days=7,
                        input_rows=[{"date": "2026-01-01", "value": 1, "epoch_id": "epoch-v1", "comparable_to_previous": False}], method_version="v1", policy_version="p1"),
                    lambda: self.analytics.derive_and_persist_association(conn, subject_id=subject_id,
                        association_id="food-sleep@v1", period_start=date(2026, 1, 1), period_end=date(2026, 1, 7),
                        input_rows=[{"exposure": i, "outcome": i * 2, "exposure_date": f"2026-01-0{i}", "outcome_date": f"2026-01-0{i}"} for i in range(1, 8)],
                        method_version="v1", policy_version="p1"),
                    lambda: self.analytics.derive_and_persist_assessment_change(conn, subject_id=subject_id,
                        metric_id="synthetic.measurement", protocol_id="capacity@v1",
                        method_version="v1", policy_version="p1"),
                ]
                first = [call() for call in calls]
                second = [call() for call in calls]
                self.assertEqual(first, second)
                self.assertEqual((1, 1, 1, 1, 4), tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                    for table in ("coverage_result", "trend_result", "association_result", "assessment_change_result", "derivation_receipt")))
                self.assertEqual(("change", 2, 3), tuple(conn.execute("SELECT status,compatible_session_count,possible_sessions FROM assessment_change_result").fetchone()))
                self.assertEqual(("synthetic.measurement", "theil_sen_median_slope", 1.0), tuple(conn.execute("SELECT exposure_metric_id,effect_method,rank_direction FROM association_result").fetchone()))
                self.assertEqual(8, conn.execute("SELECT input_count FROM derivation_receipt WHERE result_type='assessment_change'").fetchone()[0])
                conn.execute("SAVEPOINT persisted_bad_arms")
                conn.execute("INSERT INTO association_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", ("strict", "v1", "synthetic.measurement", "metric-v1", "synthetic.measurement", "metric-v1", "[]", "next-main-sleep", '{"minimum_pairs":7}', "continuous", "theil_sen_median_slope", "observational", "2026-01-01T00:00:00Z"))
                with self.assertRaisesRegex(ValueError, "pairing"):
                    self.analytics.derive_and_persist_association(conn, subject_id=subject_id, association_id="strict@v1", period_start=date(2026, 1, 1), period_end=date(2026, 1, 7), input_rows=[{"exposure": i, "outcome": i} for i in range(7)], method_version="v1", policy_version="p1")
                conn.execute("UPDATE source_epoch SET ends_at='2026-01-02T23:59:59Z' WHERE epoch_id='epoch-v1'")
                conn.executemany("INSERT INTO source_epoch VALUES (?,?,?,?,?,?,?,?,?)", [
                    ("epoch-v2", subject_id, "synthetic.json", "synthetic.measurement", "2026-01-03T00:00:00Z", "2026-01-03T23:59:59Z", "device_change", 0, "2026-01-01T00:00:00Z"),
                    ("epoch-v3", subject_id, "synthetic.json", "synthetic.measurement", "2026-01-04T00:00:00Z", None, "calibration_change", 1, "2026-01-01T00:00:00Z")])
                crossing = [{"date": f"2026-01-0{i}", "value": i, "epoch_id": "epoch-v1" if i < 3 else "epoch-v3", "comparable_to_previous": i >= 3} for i in (1, 2, 4, 5, 6, 7)]
                blocked = self.analytics.derive_and_persist_trend(conn, subject_id=subject_id, metric_id="synthetic.measurement", as_of=date(2026, 1, 7), window_days=7, baseline_days=7, input_rows=crossing, method_version="v1", policy_version="p1")
                self.assertEqual("excluded_non_comparable_epoch", conn.execute("SELECT status FROM trend_result WHERE result_id=?", (blocked["result_id"],)).fetchone()[0])
                conn.execute("ROLLBACK TO persisted_bad_arms"); conn.execute("RELEASE persisted_bad_arms")

    def test_snapshot_hash_is_order_independent_and_changes_with_input(self):
        left = [{"id": "b", "value": 2}, {"id": "a", "value": 1}]
        right = [{"id": "a", "value": 1}, {"id": "b", "value": 2}]
        changed = [{"id": "a", "value": 1}, {"id": "b", "value": 3}]
        self.assertEqual(self.analytics.input_snapshot_hash(left), self.analytics.input_snapshot_hash(right))
        self.assertNotEqual(self.analytics.input_snapshot_hash(left), self.analytics.input_snapshot_hash(changed))

    def test_persisted_trend_and_receipt_are_deterministic_for_same_snapshot(self):
        database = load_module(self, "anders_health_core.database")
        records = load_module(self, "anders_health_core.records")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "core.db"
            subject_id = database.initialize(db_path)
            with database.connect(db_path) as conn:
                records.register_metric(conn, records.synthetic_metric_definition())
                rows = [{"date": "2026-01-01", "value": 1.0}]
                result = {
                    "status": "provisional",
                    "eligible_days": 1,
                    "possible_days": 7,
                    "missing_days": 6,
                    "longest_gap_days": 6,
                    "direction": "stable",
                    "magnitude": 0.0,
                    "epoch_id": None,
                    "exclusions": [{"reason": "missing_local_days", "count": 6}],
                }
                first = self.analytics.persist_trend(
                    conn,
                    subject_id=subject_id,
                    metric_id="synthetic.measurement",
                    as_of_date="2026-01-07",
                    window_days=7,
                    baseline_days=None,
                    result=result,
                    input_rows=rows,
                    method_version="trend-v1",
                    policy_version="policy-v1",
                )
                first_generated_at = conn.execute(
                    "SELECT generated_at FROM derivation_receipt"
                ).fetchone()[0]
                second = self.analytics.persist_trend(
                    conn,
                    subject_id=subject_id,
                    metric_id="synthetic.measurement",
                    as_of_date="2026-01-07",
                    window_days=7,
                    baseline_days=None,
                    result=result,
                    input_rows=rows,
                    method_version="trend-v1",
                    policy_version="policy-v1",
                )
                self.assertEqual(first, second)
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM trend_result").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM derivation_receipt").fetchone()[0])
                stored = conn.execute(
                    "SELECT epoch_id, exclusions_json FROM trend_result"
                ).fetchone()
                self.assertIsNone(stored[0])
                self.assertEqual('[{"count":6,"reason":"missing_local_days"}]', stored[1])
                self.assertEqual(
                    first_generated_at,
                    conn.execute("SELECT generated_at FROM derivation_receipt").fetchone()[0],
                )

    def test_duplicate_food_rows_do_not_inflate_eligible_day_count(self):
        duplicate_day = [(self.as_of, 100 + i, "complete") for i in range(6)]
        result = self.analytics.food_trend(duplicate_day, self.as_of)
        self.assertEqual("insufficient", result["status"])
        self.assertEqual(1, result["eligible_days"])

    def test_duplicate_exposure_rows_do_not_inflate_association_pairs(self):
        exposures = [(date(2026, 1, 1), float(i)) for i in range(7)]
        outcomes = [(date(2026, 1, 2), 10.0)]
        result = self.analytics.association(exposures, outcomes, lag_days=1, min_pairs=7)
        self.assertEqual("insufficient", result["status"])
        self.assertEqual(1, result["paired_count"])



if __name__ == "__main__":
    unittest.main()
