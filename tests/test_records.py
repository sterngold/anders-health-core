import importlib
import csv
import json
import math
import tempfile
import unittest
from collections import Counter
from pathlib import Path


def load_module(testcase, name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        testcase.fail(f"required module {name} is unavailable: {exc}")


class RecordContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.database = load_module(self, "anders_health_core.database")
        self.records = load_module(self, "anders_health_core.records")
        self.db_path = Path(self.tmp.name) / "core.db"
        self.subject_id = self.database.initialize(self.db_path)
        self.conn = self.database.connect(self.db_path)
        self.addCleanup(self.conn.close)
        self.records.register_source(self.conn, self.records.synthetic_source_manifest())
        self.records.register_metric(self.conn, self.records.synthetic_metric_definition())

    def test_same_timestamp_distinct_source_keys_are_preserved(self):
        first = self.records.synthetic_envelope(self.subject_id, source_record_key="a")
        second = self.records.synthetic_envelope(self.subject_id, source_record_key="b")
        self.assertEqual("inserted", self.records.import_envelope(self.conn, first)["status"])
        self.assertEqual("inserted", self.records.import_envelope(self.conn, second)["status"])
        self.assertEqual(
            2,
            self.conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0],
        )

    def test_transaction_ownership_and_interval_text_fact(self):
        EXPECTED_TRANSACTION_ARMS = 4
        completed_arms = 0

        with self.subTest("default single-record import commits"):
            envelope = self.records.synthetic_envelope(
                self.subject_id, source_record_key="default-commit"
            )
            conn = self.database.connect(self.db_path)
            try:
                self.assertEqual("inserted", self.records.import_envelope(conn, envelope)["status"])
            finally:
                conn.close()
            with self.database.connect(self.db_path) as conn:
                self.assertEqual(
                    1,
                    conn.execute(
                        "SELECT COUNT(*) FROM source_record_version WHERE source_record_key=?",
                        ("default-commit",),
                    ).fetchone()[0],
                )
            completed_arms += 1

        with self.subTest("commit false rolls back with caller"):
            first = self.records.synthetic_envelope(
                self.subject_id, source_record_key="caller-rollback-1"
            )
            second = self.records.synthetic_envelope(
                self.subject_id, source_record_key="caller-rollback-2"
            )
            with self.database.connect(self.db_path) as conn:
                conn.execute("BEGIN")
                outcomes = self.records.import_envelopes(conn, [first, second], commit=False)
                self.records.normalize_fact(
                    conn,
                    source_record_version_id=outcomes[0]["record_version_id"],
                    metric_id="synthetic.measurement",
                    fact_type="sleep_session",
                    text_value="long_sleep",
                    event_start_utc="2026-01-01T22:00:00Z",
                    event_end_utc="2026-01-02T06:00:00Z",
                    attributes={"main": True},
                    calculation_version="normalize-v2",
                    commit=False,
                )
                conn.rollback()
                self.assertEqual(
                    0,
                    conn.execute(
                        "SELECT COUNT(*) FROM source_record_version "
                        "WHERE source_record_key LIKE 'caller-rollback-%'"
                    ).fetchone()[0],
                )
            completed_arms += 1

        with self.subTest("second normalization error rolls back page"):
            first = self.records.synthetic_envelope(
                self.subject_id, source_record_key="failed-page-1"
            )
            second = self.records.synthetic_envelope(
                self.subject_id, source_record_key="failed-page-2"
            )
            with self.database.connect(self.db_path) as conn:
                conn.execute("BEGIN")
                try:
                    outcomes = self.records.import_envelopes(
                        conn, [first, second], commit=False
                    )
                    self.assertEqual(
                        ["inserted", "inserted"],
                        [outcome["status"] for outcome in outcomes],
                    )
                    first_fact = self.records.normalize_fact(
                        conn,
                        source_record_version_id=outcomes[0]["record_version_id"],
                        metric_id="synthetic.measurement",
                        fact_type="measurement",
                        numeric_value=1.0,
                        unit="count",
                        calculation_version="normalize-page-v1",
                        commit=False,
                    )
                    self.assertEqual("accepted", first_fact["status"])
                    with self.assertRaises(self.records.RecordValidationError):
                        self.records.normalize_fact(
                            conn,
                            source_record_version_id=outcomes[1]["record_version_id"],
                            metric_id="synthetic.measurement",
                            fact_type="measurement",
                            numeric_value=2.0,
                            text_value="ambiguous",
                            unit="count",
                            calculation_version="normalize-page-v1",
                            commit=False,
                        )
                finally:
                    conn.rollback()
                self.assertEqual(
                    0,
                    conn.execute(
                        "SELECT COUNT(*) FROM source_record_version "
                        "WHERE source_record_key LIKE 'failed-page-%'"
                    ).fetchone()[0],
                )
                self.assertEqual(
                    0,
                    conn.execute(
                        "SELECT COUNT(*) FROM normalized_fact "
                        "WHERE calculation_version='normalize-page-v1'"
                    ).fetchone()[0],
                )
            completed_arms += 1

        with self.subTest("interval text fact retains canonical metadata"):
            metric = self.records.synthetic_metric_definition()
            metric.update(
                {
                    "metric_id": "synthetic.sleep.type",
                    "display_name": "Synthetic sleep type",
                    "value_kind": "text",
                    "canonical_unit": None,
                    "minimum_value": None,
                    "maximum_value": None,
                }
            )
            self.records.register_metric(self.conn, metric)
            envelope = self.records.synthetic_envelope(
                self.subject_id, source_record_key="interval-text"
            )
            envelope.update(
                {
                    "timestamp_precision": "interval",
                    "event_start_utc": "2026-01-01T22:00:00Z",
                    "event_end_utc": "2026-01-02T06:00:00Z",
                    "source_updated_at": "2026-01-02T06:00:00Z",
                    "ingested_at": "2026-01-02T07:00:00Z",
                }
            )
            imported = self.records.import_envelope(self.conn, envelope)
            fact = self.records.normalize_fact(
                self.conn,
                source_record_version_id=imported["record_version_id"],
                metric_id="synthetic.sleep.type",
                fact_type="sleep_session",
                text_value="long_sleep",
                event_start_utc="2026-01-01T22:00:00Z",
                event_end_utc="2026-01-02T06:00:00Z",
                attributes={"main": True},
                calculation_version="normalize-v2",
            )
            row = self.conn.execute(
                "SELECT text_value,event_start_utc,event_end_utc,unit,attributes_json "
                "FROM normalized_fact WHERE fact_id=?",
                (fact["fact_id"],),
            ).fetchone()
            self.assertEqual(
                (
                    "long_sleep",
                    "2026-01-01T22:00:00Z",
                    "2026-01-02T06:00:00Z",
                    None,
                    '{"main":true}',
                ),
                tuple(row),
            )
            completed_arms += 1

        self.assertEqual(EXPECTED_TRANSACTION_ARMS, completed_arms)

    def test_source_registration_persists_an_explicit_completeness_rule(self):
        manifest = self.records.synthetic_source_manifest()
        self.assertEqual(
            "expected_records=days in requested period; usable_records=accepted current records in that period",
            manifest["completeness_rule"],
        )
        self.records.register_source(self.conn, manifest)
        stored = self.conn.execute(
            "SELECT completeness_rule FROM source_registry WHERE source_id=?",
            ("synthetic.json",),
        ).fetchone()[0]
        self.assertEqual(manifest["completeness_rule"], stored)

    def test_new_version_supersedes_without_destroying_lineage(self):
        first = self.records.synthetic_envelope(self.subject_id, source_record_key="a")
        second = self.records.synthetic_envelope(
            self.subject_id,
            source_record_key="a",
            source_version=2,
            payload={"metric": "synthetic.measurement", "value": 2.0, "unit": "count"},
        )
        self.records.import_envelope(self.conn, first)
        self.records.import_envelope(self.conn, second)
        self.assertEqual(
            2,
            self.conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0],
        )
        current = self.conn.execute(
            "SELECT source_version FROM current_source_record WHERE source_record_key='a'"
        ).fetchone()[0]
        self.assertEqual(2, current)

    def test_conflicting_payload_for_same_version_is_quarantined(self):
        first = self.records.synthetic_envelope(self.subject_id, source_record_key="a")
        conflict = self.records.synthetic_envelope(
            self.subject_id,
            source_record_key="a",
            payload={"metric": "synthetic.measurement", "value": 9.0, "unit": "count"},
        )
        self.records.import_envelope(self.conn, first)
        result = self.records.import_envelope(self.conn, conflict)
        self.assertEqual("rejected", result["status"])
        self.assertEqual("version_conflict", result["reason_code"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM quality_issue").fetchone()[0])

    def test_tombstone_removes_record_from_current_view_but_retains_versions(self):
        first = self.records.synthetic_envelope(self.subject_id, source_record_key="a")
        deleted = self.records.synthetic_envelope(
            self.subject_id,
            source_record_key="a",
            source_version=2,
            tombstone=True,
            payload={},
        )
        self.records.import_envelope(self.conn, first)
        self.records.import_envelope(self.conn, deleted)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM current_source_record").fetchone()[0])

    def test_normalized_values_accept_zero_and_reject_non_finite_numbers(self):
        imported = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id)
        )
        fact = self.records.normalize_numeric_fact(
            self.conn,
            subject_id=self.subject_id,
            source_record_version_id=imported["record_version_id"],
            metric_id="synthetic.measurement",
            value=0.0,
            unit="count",
            local_date="2026-01-01",
        )
        self.assertEqual("accepted", fact["status"])
        row = self.conn.execute("SELECT numeric_value FROM normalized_fact").fetchone()
        self.assertEqual(0.0, row[0])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0])

        EXPECTED_NON_FINITE_CLASSES = 3
        non_finite = (("nan", math.nan), ("positive-infinity", math.inf), ("negative-infinity", -math.inf))
        self.assertEqual(EXPECTED_NON_FINITE_CLASSES, len(non_finite))
        for label, value in non_finite:
            with self.subTest(numeric_value=label):
                envelope = self.records.synthetic_envelope(
                    self.subject_id, source_record_key=f"non-finite-numeric-{label}"
                )
                outcome = self.records.import_envelope(self.conn, envelope)
                with self.assertRaisesRegex(
                    self.records.RecordValidationError, "invalid_numeric_value"
                ):
                    self.records.normalize_fact(
                        self.conn,
                        source_record_version_id=outcome["record_version_id"],
                        metric_id="synthetic.measurement",
                        fact_type="measurement",
                        calculation_version="normalize-finite-v1",
                        numeric_value=value,
                        unit="count",
                    )
            with self.subTest(attribute_value=label):
                envelope = self.records.synthetic_envelope(
                    self.subject_id, source_record_key=f"non-finite-attribute-{label}"
                )
                outcome = self.records.import_envelope(self.conn, envelope)
                with self.assertRaisesRegex(
                    self.records.RecordValidationError, "invalid_attributes"
                ):
                    self.records.normalize_fact(
                        self.conn,
                        source_record_version_id=outcome["record_version_id"],
                        metric_id="synthetic.measurement",
                        fact_type="measurement",
                        calculation_version="normalize-attributes-v1",
                        numeric_value=1.0,
                        unit="count",
                        attributes={"nested": {"value": value}},
                    )
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0])

    def test_unknown_unit_is_quarantined_without_creating_fact(self):
        imported = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id)
        )
        result = self.records.normalize_numeric_fact(
            self.conn,
            subject_id=self.subject_id,
            source_record_version_id=imported["record_version_id"],
            metric_id="synthetic.measurement",
            value=1.0,
            unit="mystery",
            local_date="2026-01-01",
        )
        self.assertEqual("quarantined", result["status"])
        self.assertEqual("unit_mismatch", result["reason_code"])
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0])

    def test_context_event_accepts_categories_and_rejects_narrative(self):
        accepted = self.records.add_context_event(
            self.conn,
            subject_id=self.subject_id,
            category="travel",
            starts_on="2026-01-01",
            ends_on="2026-01-02",
        )
        self.assertEqual("accepted", accepted["status"])
        with self.assertRaises(ValueError):
            self.records.add_context_event(
                self.conn,
                subject_id=self.subject_id,
                category="illness",
                starts_on="2026-01-03",
                narrative="private free text",
            )

    def test_jsonl_import_reports_literal_inserted_rejected_and_unchanged_counts(self):
        path = Path(self.tmp.name) / "records.jsonl"
        good = self.records.synthetic_envelope(self.subject_id, source_record_key="good")
        bad = {"subject_id": self.subject_id, "source_id": "synthetic.json"}
        path.write_text(
            "\n".join((json.dumps(good), json.dumps(good), json.dumps(bad))) + "\n",
            encoding="utf-8",
        )
        receipt = self.records.import_jsonl(self.conn, path)
        self.assertEqual(3, receipt["pulled"])
        self.assertEqual(1, receipt["inserted"])
        self.assertEqual(1, receipt["unchanged"])
        self.assertEqual(1, receipt["rejected"])

    def test_csv_import_uses_the_same_append_only_contract(self):
        path = Path(self.tmp.name) / "records.csv"
        envelope = self.records.synthetic_envelope(self.subject_id, source_record_key="csv-good")
        with path.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(envelope))
            writer.writeheader()
            writer.writerow({**envelope, "payload": json.dumps(envelope["payload"])})
        receipt = self.records.import_csv(self.conn, path)
        self.assertEqual(
            {"pulled": 1, "inserted": 1, "unchanged": 0, "rejected": 0}, receipt
        )

    def test_date_only_and_offset_aware_records_have_explicit_precision(self):
        date_only = self.records.synthetic_envelope(self.subject_id, source_record_key="date-only")
        date_only.update({"timestamp_precision": "date", "event_start_utc": None})
        offset = self.records.synthetic_envelope(self.subject_id, source_record_key="offset")
        offset.update(
            {
                "timestamp_precision": "instant",
                "event_start_utc": "2026-03-29T00:30:00Z",
                "source_updated_at": "2026-03-29T00:30:00Z",
                "ingested_at": "2026-03-29T01:00:00Z",
                "offset_minutes": 60,
                "local_date": "2026-03-29",
                "source_timezone": "Europe/Amsterdam",
            }
        )
        self.assertEqual("inserted", self.records.import_envelope(self.conn, date_only)["status"])
        self.assertEqual("inserted", self.records.import_envelope(self.conn, offset)["status"])
        invalid = self.records.synthetic_envelope(self.subject_id, source_record_key="invalid-date")
        invalid.update({"timestamp_precision": "date", "event_start_utc": "2026-01-01T00:00:00Z"})
        with self.assertRaises(self.records.RecordValidationError):
            self.records.import_envelope(self.conn, invalid)

    def test_declared_unit_conversion_normalizes_without_changing_raw_payload(self):
        metric = self.records.synthetic_metric_definition()
        metric.update({"metric_id": "synthetic.mass", "canonical_unit": "kg"})
        self.records.register_metric(self.conn, metric)
        self.records.register_source_metric_map(
            self.conn,
            source_id="synthetic.json",
            source_metric="body_mass_lb",
            metric_id="synthetic.mass",
            source_unit="lb",
            factor=0.45359237,
        )
        converted = self.records.convert_source_value(
            self.conn,
            source_id="synthetic.json",
            source_metric="body_mass_lb",
            value=220.0,
            source_unit="lb",
        )
        self.assertEqual("synthetic.mass", converted["metric_id"])
        self.assertEqual("kg", converted["unit"])
        self.assertAlmostEqual(99.7903214, converted["value"], places=7)

    def test_revoked_source_is_hidden_and_device_change_starts_new_epoch(self):
        imported = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id)
        )
        first = self.records.add_source_epoch(
            self.conn,
            subject_id=self.subject_id,
            source_id="synthetic.json",
            starts_at="2026-01-01T00:00:00Z",
            reason_category="initial",
            comparable_to_previous=False,
        )
        second = self.records.add_source_epoch(
            self.conn,
            subject_id=self.subject_id,
            source_id="synthetic.json",
            starts_at="2026-02-01T00:00:00Z",
            reason_category="device_change",
            comparable_to_previous=False,
        )
        self.assertNotEqual(first["epoch_id"], second["epoch_id"])
        self.assertEqual(1, self.conn.execute("SELECT COUNT(*) FROM current_source_record").fetchone()[0])
        self.records.set_source_consent(self.conn, "synthetic.json", "revoked")
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM current_source_record").fetchone()[0])

    def test_local_date_rejects_a_datetime_string(self):
        envelope = self.records.synthetic_envelope(self.subject_id, source_record_key="bad-date-shape")
        envelope["local_date"] = "2026-01-01T10:00:00"
        with self.assertRaises(self.records.RecordValidationError):
            self.records.import_envelope(self.conn, envelope)

    def test_offset_and_utc_timestamp_must_agree_with_local_date(self):
        envelope = self.records.synthetic_envelope(self.subject_id, source_record_key="bad-offset-date")
        envelope.update(
            {
                "event_start_utc": "2026-01-01T23:30:00Z",
                "offset_minutes": 60,
                "local_date": "2026-01-01",
            }
        )
        with self.assertRaises(self.records.RecordValidationError):
            self.records.import_envelope(self.conn, envelope)

    def test_unknown_subject_is_rejected_without_crashing_import_receipt(self):
        path = Path(self.tmp.name) / "unknown-subject.jsonl"
        envelope = self.records.synthetic_envelope(
            "00000000-0000-4000-8000-000000000099", source_record_key="unknown-subject"
        )
        path.write_text(json.dumps(envelope) + "\n", encoding="utf-8")
        receipt = self.records.import_jsonl(self.conn, path)
        self.assertEqual(
            {"pulled": 1, "inserted": 0, "unchanged": 0, "rejected": 1}, receipt
        )

    def test_immutable_source_and_metric_versions_coexist_and_rewrites_fail(self):
        source_v1 = self.records.synthetic_source_manifest()
        source_v2 = {**source_v1, "contract_version": "source-v2", "display_name": "Synthetic fixture v2"}
        metric_v1 = self.records.synthetic_metric_definition()
        metric_v2 = {**metric_v1, "definition_version": "metric-v2", "display_name": "Synthetic measurement v2"}
        self.records.register_source(self.conn, source_v2)
        self.records.register_metric(self.conn, metric_v2)
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM source_contract_version").fetchone()[0])
        self.assertEqual(2, self.conn.execute("SELECT COUNT(*) FROM metric_definition_version").fetchone()[0])
        with self.assertRaisesRegex(self.records.RecordValidationError, "source_contract_version_conflict"):
            self.records.register_source(self.conn, {**source_v1, "display_name": "rewritten"})
        with self.assertRaisesRegex(self.records.RecordValidationError, "metric_definition_version_conflict"):
            self.records.register_metric(self.conn, {**metric_v1, "display_name": "rewritten"})
        stored = self.conn.execute(
            "SELECT manifest_json FROM source_contract_version WHERE source_id=? AND contract_version=?",
            ("synthetic.json", "source-v1"),
        ).fetchone()[0]
        self.assertEqual(source_v1, json.loads(stored))

    def test_semantically_identical_migrated_version_serialization_is_not_a_rewrite(self):
        manifest = self.records.synthetic_source_manifest()
        metric = self.records.synthetic_metric_definition()
        self.conn.execute("DROP TRIGGER source_contract_version_no_update")
        self.conn.execute(
            "UPDATE source_contract_version SET manifest_json=? WHERE source_id=? AND contract_version=?",
            (json.dumps(manifest, indent=2), manifest["source_id"], manifest["contract_version"]),
        )
        self.conn.executescript(
            "CREATE TRIGGER source_contract_version_no_update "
            "BEFORE UPDATE ON source_contract_version BEGIN "
            "SELECT RAISE(ABORT, 'source contract versions are immutable'); END;"
        )
        self.records.register_source(self.conn, manifest)
        self.conn.execute("DROP TRIGGER metric_definition_version_no_update")
        self.conn.execute(
            "UPDATE metric_definition_version SET definition_json=? WHERE metric_id=? AND definition_version=?",
            (json.dumps(metric, indent=2), metric["metric_id"], metric["definition_version"]),
        )
        self.conn.executescript(
            "CREATE TRIGGER metric_definition_version_no_update "
            "BEFORE UPDATE ON metric_definition_version BEGIN "
            "SELECT RAISE(ABORT, 'metric definition versions are immutable'); END;"
        )
        self.records.register_metric(self.conn, metric)
        self.assertEqual(1, self.conn.execute(
            "SELECT COUNT(*) FROM source_contract_version"
        ).fetchone()[0])
        self.assertEqual(1, self.conn.execute(
            "SELECT COUNT(*) FROM metric_definition_version"
        ).fetchone()[0])

    def test_raw_identity_is_subject_scoped_and_fact_provenance_is_derived(self):
        second_subject = "00000000-0000-4000-8000-000000000002"
        self.conn.execute(
            "INSERT INTO subject(subject_id,created_at) VALUES (?,?)",
            (second_subject, "2026-01-01T00:00:00Z"),
        )
        first = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id, source_record_key="shared")
        )
        second = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(second_subject, source_record_key="shared")
        )
        self.assertEqual(2, self.conn.execute(
            "SELECT COUNT(*) FROM current_source_record WHERE source_record_key='shared'"
        ).fetchone()[0])
        fact = self.records.normalize_numeric_fact(
            self.conn,
            source_record_version_id=second["record_version_id"],
            metric_id="synthetic.measurement",
            value=1.0,
            unit="count",
        )
        self.assertEqual("accepted", fact["status"])
        row = self.conn.execute(
            "SELECT subject_id,local_date,metric_definition_version FROM normalized_fact WHERE fact_id=?",
            (fact["fact_id"],),
        ).fetchone()
        self.assertEqual((second_subject, "2026-01-01", "metric-v1"), tuple(row))
        self.assertNotEqual(first["record_version_id"], second["record_version_id"])

    def test_normalization_rejects_literal_bad_provenance_classes(self):
        current = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id, source_record_key="current")
        )
        old = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id, source_record_key="superseded")
        )
        self.records.import_envelope(
            self.conn,
            self.records.synthetic_envelope(
                self.subject_id, source_record_key="superseded", source_version=2
            ),
        )
        tombstone = self.records.import_envelope(
            self.conn,
            self.records.synthetic_envelope(self.subject_id, source_record_key="deleted", tombstone=True),
        )
        quarantined = self.records.import_envelope(
            self.conn, self.records.synthetic_envelope(self.subject_id, source_record_key="quarantined")
        )
        self.conn.execute(
            "UPDATE source_record_version SET validation_state='quarantined' WHERE record_version_id=?",
            (quarantined["record_version_id"],),
        )
        common = {"metric_id": "synthetic.measurement", "value": 1.0, "unit": "count"}
        results = [
            self.records.normalize_numeric_fact(
                self.conn, source_record_version_id=current["record_version_id"],
                subject_id="00000000-0000-4000-8000-000000000099", **common
            ),
            self.records.normalize_numeric_fact(
                self.conn, source_record_version_id=current["record_version_id"],
                local_date="2026-01-02", **common
            ),
            self.records.normalize_numeric_fact(
                self.conn, source_record_version_id=old["record_version_id"], **common
            ),
            self.records.normalize_numeric_fact(
                self.conn, source_record_version_id=tombstone["record_version_id"], **common
            ),
            self.records.normalize_numeric_fact(
                self.conn, source_record_version_id=quarantined["record_version_id"], **common
            ),
        ]
        self.assertEqual(
            Counter({
                "provenance_subject_mismatch": 1,
                "provenance_date_mismatch": 1,
                "superseded_provenance": 1,
                "tombstoned_provenance": 1,
                "quarantined_provenance": 1,
            }),
            Counter(result["reason_code"] for result in results),
        )
        self.assertTrue(all(result["status"] == "quarantined" for result in results))
        self.assertEqual(0, self.conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0])

    def test_timestamp_controls_accept_dst_and_travel_and_quarantine_bad_envelopes(self):
        good_cases = (
            ("winter", "2026-01-15T08:00:00Z", "Europe/Amsterdam", 60, "2026-01-15"),
            ("summer", "2026-07-15T08:00:00Z", "Europe/Amsterdam", 120, "2026-07-15"),
            ("travel", "2026-07-15T08:00:00Z", "America/New_York", -240, "2026-07-15"),
        )
        self.assertEqual(3, len(good_cases))
        for key, event_at, zone, offset, local_day in good_cases:
            envelope = self.records.synthetic_envelope(self.subject_id, source_record_key=key)
            envelope.update({
                "event_start_utc": event_at,
                "source_timezone": zone,
                "offset_minutes": offset,
                "local_date": local_day,
                "source_updated_at": event_at,
                "ingested_at": "2026-07-15T09:00:00Z" if key != "winter" else "2026-01-15T09:00:00Z",
            })
            self.assertEqual("inserted", self.records.import_envelope(self.conn, envelope)["status"])

        valid_interval = self.records.synthetic_envelope(self.subject_id, "valid-interval")
        valid_interval.update({
            "timestamp_precision": "interval",
            "event_end_utc": "2026-01-01T09:04:00Z",
        })
        self.assertEqual("inserted", self.records.import_envelope(self.conn, valid_interval)["status"])

        bad_cases = []
        interval_missing = self.records.synthetic_envelope(self.subject_id, "interval-missing")
        interval_missing["timestamp_precision"] = "interval"
        bad_cases.append(interval_missing)
        interval_reversed = self.records.synthetic_envelope(self.subject_id, "interval-reversed")
        interval_reversed.update({"timestamp_precision": "interval", "event_end_utc": "2026-01-01T07:00:00Z"})
        bad_cases.append(interval_reversed)
        for key, field in (
            ("malformed-event", "event_start_utc"),
            ("malformed-end", "event_end_utc"),
            ("malformed-ingested", "ingested_at"),
            ("malformed-updated", "source_updated_at"),
        ):
            envelope = self.records.synthetic_envelope(self.subject_id, key)
            if field == "event_end_utc":
                envelope["timestamp_precision"] = "interval"
            envelope[field] = "not-a-timestamp"
            bad_cases.append(envelope)
        mismatch = self.records.synthetic_envelope(self.subject_id, "offset-mismatch")
        mismatch.update({"source_timezone": "Europe/Amsterdam", "offset_minutes": 120})
        bad_cases.append(mismatch)
        future = self.records.synthetic_envelope(self.subject_id, "future")
        future["event_start_utc"] = "2026-01-01T09:06:00Z"
        bad_cases.append(future)
        future_interval = self.records.synthetic_envelope(self.subject_id, "future-interval")
        future_interval.update({
            "timestamp_precision": "interval",
            "event_end_utc": "2026-01-01T09:06:00Z",
        })
        bad_cases.append(future_interval)
        future_date = self.records.synthetic_envelope(self.subject_id, "future-date")
        future_date.update({"timestamp_precision": "date", "event_start_utc": None,
                            "local_date": "2099-01-01"})
        bad_cases.append(future_date)
        unanchored = self.records.synthetic_envelope(self.subject_id, "unanchored")
        unanchored.update({"source_timezone": None, "offset_minutes": None})
        bad_cases.append(unanchored)
        self.assertEqual(11, len(bad_cases))
        path = Path(self.tmp.name) / "bad-timestamps.jsonl"
        path.write_text("\n".join(json.dumps(row) for row in bad_cases) + "\n", encoding="utf-8")
        self.assertEqual(
            {"pulled": 11, "inserted": 0, "unchanged": 0, "rejected": 11},
            self.records.import_jsonl(self.conn, path),
        )
        reason_counts = Counter({row[0]: row[1] for row in self.conn.execute(
            "SELECT reason_code,COUNT(*) FROM quarantine_envelope GROUP BY reason_code"
        )})
        self.assertEqual(Counter({
            "invalid_timestamp": 4,
            "interval_end_required": 1,
            "interval_order": 1,
            "offset_timezone_mismatch": 1,
            "future_event": 3,
            "local_date_authority_required": 1,
        }), reason_counts)
        self.assertEqual(0, self.conn.execute(
            "SELECT COUNT(*) FROM source_record_version WHERE source_record_key LIKE 'malformed-%'"
        ).fetchone()[0])


if __name__ == "__main__":
    unittest.main()
