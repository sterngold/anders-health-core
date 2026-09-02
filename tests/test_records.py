import importlib
import csv
import json
import tempfile
import unittest
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

    def test_observed_zero_is_a_fact_while_missing_stays_absent(self):
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


if __name__ == "__main__":
    unittest.main()
