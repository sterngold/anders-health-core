import importlib
import sqlite3
import tempfile
import unittest
from pathlib import Path


EXPECTED_CORE_TABLES = {
    "analysis_policy",
    "assessment_change_result",
    "association_result",
    "context_event",
    "coverage_result",
    "derivation_receipt",
    "metric_definition",
    "normalized_fact",
    "quality_issue",
    "schema_migration",
    "source_epoch",
    "source_metric_map",
    "source_record_version",
    "source_registry",
    "subject",
    "trend_result",
}


def load_module(testcase, name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        testcase.fail(f"required module {name} is unavailable: {exc}")


class DatabaseContractTests(unittest.TestCase):
    def test_bootstrap_creates_literal_table_set_and_current_record_view(self):
        database = load_module(self, "anders_health_core.database")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "core.db"
            database.initialize(db_path)
            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    )
                }
                views = {
                    row[0]
                    for row in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='view'"
                    )
                }
                self.assertEqual(16, len(tables))
                self.assertEqual(EXPECTED_CORE_TABLES, tables)
                self.assertEqual({"current_source_record"}, views)
                self.assertEqual(2, database.schema_version(conn))

    def test_each_installation_gets_a_distinct_local_subject(self):
        database = load_module(self, "anders_health_core.database")
        with tempfile.TemporaryDirectory() as tmp:
            first = database.initialize(Path(tmp) / "first.db")
            second = database.initialize(Path(tmp) / "second.db")
            self.assertNotEqual(first, second)
            self.assertEqual(36, len(first))
            self.assertEqual(36, len(second))

    def test_subject_purge_cascades_through_raw_normalized_and_results(self):
        database = load_module(self, "anders_health_core.database")
        lifecycle = load_module(self, "anders_health_core.lifecycle")
        records = load_module(self, "anders_health_core.records")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "core.db"
            subject_id = database.initialize(db_path)
            with database.connect(db_path) as conn:
                records.register_source(conn, records.synthetic_source_manifest())
                records.register_metric(conn, records.synthetic_metric_definition())
                records.import_envelope(conn, records.synthetic_envelope(subject_id))
                records.normalize_numeric_fact(
                    conn,
                    subject_id=subject_id,
                    source_record_version_id=conn.execute(
                        "SELECT record_version_id FROM source_record_version"
                    ).fetchone()[0],
                    metric_id="synthetic.measurement",
                    value=1.0,
                    unit="count",
                    local_date="2026-01-01",
                )
                conn.execute(
                    "INSERT INTO trend_result "
                    "(result_id,subject_id,metric_id,as_of_date,window_days,status,"
                    "eligible_days,possible_days,missing_days,direction,method_version,"
                    "policy_version,input_snapshot_hash,generated_at) "
                    "VALUES ('result-1',?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        subject_id,
                        "synthetic.measurement",
                        "2026-01-01",
                        7,
                        "normal",
                        1,
                        7,
                        6,
                        "stable",
                        "trend-v1",
                        "policy-v1",
                        "abc",
                        "2026-01-01T00:00:00Z",
                    ),
                )
                conn.commit()
                deleted = lifecycle.purge_subject(conn, subject_id)
                self.assertEqual(1, deleted["subjects"])
                self.assertEqual(1, deleted["raw_versions"])
                self.assertEqual(1, deleted["normalized_facts"])
                self.assertEqual(1, deleted["trend_results"])
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0])

    def test_backup_restore_and_export_round_trip(self):
        database = load_module(self, "anders_health_core.database")
        lifecycle = load_module(self, "anders_health_core.lifecycle")
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source.db"
            backup = Path(tmp) / "backup.db"
            restored = Path(tmp) / "restored.db"
            subject_id = database.initialize(source)
            lifecycle.backup_database(source, backup)
            lifecycle.restore_database(backup, restored)
            with database.connect(restored) as conn:
                exported = lifecycle.export_subject(conn, subject_id)
            self.assertEqual(subject_id, exported["subject"]["subject_id"])
            self.assertEqual(10, len(exported["datasets"]))
            self.assertNotIn("external_account", str(exported))


if __name__ == "__main__":
    unittest.main()
