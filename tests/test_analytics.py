import importlib
import tempfile
import unittest
from datetime import date, timedelta
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
        self.assertEqual("historical_only", self.analytics.food_trend(stale, self.as_of)["status"])

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

    def test_association_requires_seven_pairs_and_is_labelled_observational(self):
        exposures = [(date(2026, 1, 1) + timedelta(days=i), float(i)) for i in range(7)]
        outcomes = [(date(2026, 1, 2) + timedelta(days=i), float(i * 2)) for i in range(7)]
        early = self.analytics.association(exposures, outcomes, lag_days=1, min_pairs=7)
        insufficient = self.analytics.association(exposures[:6], outcomes[:6], lag_days=1, min_pairs=7)
        self.assertEqual("early_association", early["status"])
        self.assertEqual(7, early["paired_count"])
        self.assertEqual("observational", early["claim_type"])
        self.assertEqual("insufficient", insufficient["status"])

    def test_food_weight_and_body_composition_eligibility_enforce_counts_and_gaps(self):
        food_days = [date(2026, 1, 1) + timedelta(days=i) for i in range(21) if i != 10]
        weights = [date(2026, 1, 21) - timedelta(days=i * 2) for i in range(3)]
        self.assertTrue(self.analytics.relationship_eligible("food_weight", food_days[:7], weights))
        self.assertFalse(self.analytics.relationship_eligible("food_weight", food_days[:7], weights[:2]))
        self.assertTrue(self.analytics.relationship_eligible("food_body_composition", food_days[:14], []))
        gapped = [date(2026, 1, 1) + timedelta(days=i) for i in range(7)] + [
            date(2026, 1, 16) + timedelta(days=i) for i in range(7)
        ]
        self.assertFalse(self.analytics.relationship_eligible("food_body_composition", gapped, []))

    def test_assessment_requires_second_session_for_change_and_third_for_trend(self):
        one = [(date(2026, 1, 1), 10.0)]
        two = one + [(date(2026, 2, 1), 12.0)]
        three = two + [(date(2026, 3, 1), 14.0)]
        self.assertEqual("baseline", self.analytics.assessment_change(one)["status"])
        self.assertEqual("change", self.analytics.assessment_change(two)["status"])
        self.assertEqual("trend", self.analytics.assessment_change(three)["status"])
        self.assertEqual(4.0, self.analytics.assessment_change(three)["delta_from_baseline"])

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
