import importlib
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


EXPECTED_CORE_TABLES = {
    "analysis_policy",
    "assessment_change_result",
    "association_result",
    "context_event",
    "coverage_result",
    "derivation_receipt",
    "metric_definition",
    "metric_definition_version",
    "normalized_fact",
    "quarantine_envelope",
    "quality_issue",
    "schema_migration",
    "source_epoch",
    "source_metric_map",
    "source_record_version",
    "source_registry",
    "source_contract_version",
    "subject",
    "trend_result",
    "assessment_protocol",
    "assessment_required_metric",
    "assessment_attempt",
    "assessment_session",
    "association_definition",
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
                self.assertEqual(24, len(tables))
                self.assertEqual(EXPECTED_CORE_TABLES, tables)
                self.assertEqual({"current_source_record"}, views)
                self.assertEqual(5, database.schema_version(conn))
                receipts = conn.execute(
                    "SELECT version,name,sha256 FROM schema_migration ORDER BY version"
                ).fetchall()
                self.assertEqual(5, len(receipts))
                self.assertTrue(all(len(row[2]) == 64 for row in receipts))

    def test_assessment_and_association_contracts_are_versioned(self):
        database = load_module(self, "anders_health_core.database")
        records = load_module(self, "anders_health_core.records")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "contracts.db"
            database.initialize(db_path)
            with database.connect(db_path) as conn:
                records.register_metric(conn, records.synthetic_metric_definition())
                conn.execute("INSERT INTO assessment_protocol VALUES (?,?,?,?,?)", (
                    "capacity", "v1", "grip", "same-test-version", "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO assessment_required_metric VALUES (?,?,?)", (
                    "capacity", "v1", "synthetic.measurement"))
                conn.execute("INSERT INTO association_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)", (
                    "food-sleep", "v1", "synthetic.measurement", "metric-v1",
                    "synthetic.measurement", "metric-v1", "[]", "next-main-sleep",
                    '{"minimum_pairs":7}', "continuous", "theil_sen", "observational",
                    "2026-01-01T00:00:00Z"))
                self.assertEqual((1, 1, 1), (
                    conn.execute("SELECT COUNT(*) FROM assessment_protocol").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM assessment_required_metric").fetchone()[0],
                    conn.execute("SELECT COUNT(*) FROM association_definition").fetchone()[0]))

    def test_version_two_upgrade_is_collision_safe_when_current_versions_equal_legacy_sentinel(self):
        database = load_module(self, "anders_health_core.database")
        migration_dir = Path(database.__file__).with_name("migrations")
        with tempfile.TemporaryDirectory() as tmp:
            legacy_path = Path(tmp) / "legacy-v2.db"
            with sqlite3.connect(legacy_path) as conn:
                for version, filename in ((1, "001_foundation.sql"), (2, "002_policies.sql")):
                    conn.executescript((migration_dir / filename).read_text(encoding="utf-8"))
                    conn.execute(
                        "INSERT INTO schema_migration(version,name,applied_at) VALUES (?,?,?)",
                        (version, filename.split("_", 1)[1].removesuffix(".sql"), "2026-01-01T00:00:00Z"),
                    )
                subject = "00000000-0000-4000-8000-000000000001"
                conn.execute("INSERT INTO subject VALUES (?,?)", (subject, "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO source_registry VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy", "file", "v2 projection", '["measurement"]', 1, "not_required", "instant", "Etc/UTC", "versioned", None, "legacy-unknown", "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO metric_definition VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy.metric", "v2 projection", "number", "count", None, "daily_last", 0, None, "fixture", "source_local_date", "legacy-unknown", "2026-01-01T00:00:00Z"))
                conn.execute("INSERT INTO source_record_version VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy-raw", subject, "legacy", "key", 1, "{}", "a" * 64, "2026-01-01T00:00:00Z", None, "instant", "Etc/UTC", 0, "2026-01-01", "v1", None, "2026-01-01T00:00:00Z", 0, "accepted"))
                conn.execute("INSERT INTO normalized_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)", ("legacy-fact", subject, "measurement", "legacy.metric", "2026-01-01", None, None, 1, None, "count", "legacy-raw", None, "{}", "accepted", "v1", "2026-01-01T00:00:00Z"))
            try:
                database.initialize(legacy_path)
            except sqlite3.IntegrityError as exc:
                self.fail(f"public-valid legacy-unknown versions must not collide: {exc}")
            fresh_path = Path(tmp) / "fresh.db"
            database.initialize(fresh_path)
            for path in (legacy_path, fresh_path):
                with sqlite3.connect(path) as conn:
                    self.assertEqual(5, database.schema_version(conn))
                    self.assertEqual(
                        "phase1_contract_completion",
                        conn.execute(
                            "SELECT name FROM schema_migration WHERE version=3"
                        ).fetchone()[0],
                    )
                    self.assertIn(
                        "completeness_rule",
                        {row[1] for row in conn.execute("PRAGMA table_info(source_registry)")},
                    )
                    self.assertTrue(
                        {"policy_version", "exclusions_json"}.issubset(
                            {row[1] for row in conn.execute("PRAGMA table_info(coverage_result)")}
                        )
                    )
                    if path == legacy_path:
                        self.assertEqual(("legacy-unknown-1", "quarantined"), conn.execute("SELECT source_contract_version,validation_state FROM source_record_version").fetchone())
                        self.assertEqual(("legacy-unknown-1", "quarantined"), conn.execute("SELECT metric_definition_version,validation_state FROM normalized_fact").fetchone())

    def test_migration_and_receipt_roll_back_together_on_mid_script_failure(self):
        database = load_module(self, "anders_health_core.database")
        with tempfile.TemporaryDirectory() as tmp:
            migration_dir = Path(tmp) / "migrations"
            migration_dir.mkdir()
            (migration_dir / "001_first.sql").write_text(
                "CREATE TABLE subject(subject_id TEXT PRIMARY KEY, created_at TEXT NOT NULL);",
                encoding="utf-8",
            )
            (migration_dir / "002_broken.sql").write_text(
                "CREATE TABLE partial_ddl(value TEXT); INSERT INTO absent_table VALUES (1);",
                encoding="utf-8",
            )
            db_path = Path(tmp) / "atomic.db"
            with patch.object(database, "MIGRATIONS", migration_dir):
                with self.assertRaises(sqlite3.DatabaseError):
                    database.initialize(db_path)
            with sqlite3.connect(db_path) as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertNotIn("partial_ddl", tables)
                self.assertEqual([(1, "first")], conn.execute(
                    "SELECT version,name FROM schema_migration ORDER BY version"
                ).fetchall())

    def test_foreign_key_check_precedes_migration_commit_and_receipt(self):
        database = load_module(self, "anders_health_core.database")
        with tempfile.TemporaryDirectory() as tmp:
            source_migrations = Path(database.__file__).with_name("migrations")
            valid_dir = Path(tmp) / "valid-migrations"
            invalid_dir = Path(tmp) / "invalid-migrations"
            shutil.copytree(source_migrations, valid_dir)
            shutil.copytree(source_migrations, invalid_dir)
            valid_sql = (
                "CREATE TABLE atomic_parent(id INTEGER PRIMARY KEY); "
                "CREATE TABLE atomic_child(parent_id INTEGER REFERENCES atomic_parent(id)); "
                "INSERT INTO atomic_parent(id) VALUES (1); "
                "INSERT INTO atomic_child(parent_id) VALUES (1);"
            )
            invalid_sql = (
                "CREATE TABLE atomic_parent(id INTEGER PRIMARY KEY); "
                "CREATE TABLE atomic_child(parent_id INTEGER REFERENCES atomic_parent(id)); "
                "INSERT INTO atomic_child(parent_id) VALUES (99);"
            )
            (valid_dir / "006_valid_fk.sql").write_text(valid_sql, encoding="utf-8")
            (invalid_dir / "006_invalid_fk.sql").write_text(invalid_sql, encoding="utf-8")

            valid_db = Path(tmp) / "valid.db"
            with patch.object(database, "MIGRATIONS", valid_dir):
                database.initialize(valid_db)
            with sqlite3.connect(valid_db) as conn:
                self.assertEqual(1, conn.execute(
                    "SELECT COUNT(*) FROM schema_migration WHERE version=6"
                ).fetchone()[0])
                self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

            invalid_db = Path(tmp) / "invalid.db"
            with patch.object(database, "MIGRATIONS", invalid_dir):
                with self.assertRaisesRegex(sqlite3.IntegrityError, "foreign key violations"):
                    database.initialize(invalid_db)
            with sqlite3.connect(invalid_db) as conn:
                tables = {
                    row[0]
                    for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
                }
                self.assertNotIn("atomic_parent", tables)
                self.assertNotIn("atomic_child", tables)
                self.assertEqual(0, conn.execute(
                    "SELECT COUNT(*) FROM schema_migration WHERE version=6"
                ).fetchone()[0])
                self.assertEqual([], conn.execute("PRAGMA foreign_key_check").fetchall())

    def test_quarantine_is_append_only_but_governed_subject_purge_cascades(self):
        database = load_module(self, "anders_health_core.database")
        lifecycle = load_module(self, "anders_health_core.lifecycle")
        records = load_module(self, "anders_health_core.records")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "quarantine-purge.db"
            subject_id = database.initialize(db_path)
            with database.connect(db_path) as conn:
                records.register_source(conn, records.synthetic_source_manifest())
                future = records.synthetic_envelope(subject_id, source_record_key="future-purge")
                future["event_start_utc"] = "2026-01-01T09:06:00Z"
                path = Path(tmp) / "future.jsonl"
                path.write_text(f"{json.dumps(future)}\n", encoding="utf-8")
                self.assertEqual(1, records.import_jsonl(conn, path)["rejected"])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM quarantine_envelope").fetchone()[0])
                with self.assertRaisesRegex(sqlite3.IntegrityError, "append-only"):
                    conn.execute("DELETE FROM quarantine_envelope")
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM quarantine_envelope").fetchone()[0])
                lifecycle.purge_subject(conn, subject_id)
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0])
                self.assertEqual(0, conn.execute("SELECT COUNT(*) FROM quarantine_envelope").fetchone()[0])

    def test_changed_and_out_of_order_migration_history_fail_closed(self):
        database = load_module(self, "anders_health_core.database")
        with tempfile.TemporaryDirectory() as tmp:
            migration_dir = Path(tmp) / "migrations"
            shutil.copytree(Path(database.__file__).with_name("migrations"), migration_dir)
            changed_db = Path(tmp) / "changed.db"
            with patch.object(database, "MIGRATIONS", migration_dir):
                database.initialize(changed_db)
                migration = migration_dir / "004_provenance_integrity.sql"
                migration.write_text(
                    migration.read_text(encoding="utf-8") + "\n-- changed after application\n",
                    encoding="utf-8",
                )
                with self.assertRaisesRegex(RuntimeError, "migration history changed"):
                    database.initialize(changed_db)

            ordered_db = Path(tmp) / "ordered.db"
            with patch.object(database, "MIGRATIONS", Path(database.__file__).with_name("migrations")):
                database.initialize(ordered_db)
                with sqlite3.connect(ordered_db) as conn:
                    conn.execute("DELETE FROM schema_migration WHERE version=2")
                with self.assertRaisesRegex(RuntimeError, "out-of-order migration history"):
                    database.initialize(ordered_db)
                    self.assertIn(
                        "possible_pairs",
                        {row[1] for row in conn.execute("PRAGMA table_info(association_result)")},
                    )
                    self.assertTrue(
                        {"possible_sessions", "exclusions_json"}.issubset(
                            {row[1] for row in conn.execute("PRAGMA table_info(assessment_change_result)")}
                        )
                    )

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
                    "policy_version,input_snapshot_hash,generated_at,exclusions_json) "
                    "VALUES ('result-1',?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
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
                        "[]",
                    ),
                )
                conn.commit()
                deleted = lifecycle.purge_subject(conn, subject_id)
                self.assertEqual(1, deleted["subject"])
                self.assertEqual(1, deleted["source_record_version"])
                self.assertEqual(1, deleted["normalized_fact"])
                self.assertEqual(1, deleted["trend_result"])
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
                subject_tables = {
                    name for (name,) in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name NOT LIKE 'sqlite_%'"
                    ) if "subject_id" in {
                        column[1] for column in conn.execute(f'PRAGMA table_info("{name}")')
                    }
                }
                deleted = lifecycle.purge_subject(conn, subject_id)
            self.assertEqual(subject_id, exported["subject"]["subject_id"])
            self.assertEqual(subject_tables - {"subject"}, set(exported["datasets"]))
            self.assertEqual(subject_tables, set(deleted))
            self.assertNotIn("external_account", str(exported))
            with database.connect(restored) as conn:
                for table in subject_tables:
                    self.assertEqual(0, conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])

    def test_restore_rejects_invalid_backup_without_changing_destination(self):
        database = load_module(self, "anders_health_core.database")
        lifecycle = load_module(self, "anders_health_core.lifecycle")
        with tempfile.TemporaryDirectory() as tmp:
            invalid, destination = Path(tmp) / "invalid.db", Path(tmp) / "destination.db"
            with sqlite3.connect(invalid) as conn:
                conn.execute("CREATE TABLE schema_migration(version INTEGER PRIMARY KEY)")
                conn.execute("INSERT INTO schema_migration VALUES (5)")
            database.initialize(destination)
            before = destination.read_bytes()
            with self.assertRaisesRegex(ValueError, "backup"):
                lifecycle.restore_database(invalid, destination)
            self.assertEqual(before, destination.read_bytes())

    def test_purge_rolls_back_when_a_subject_linked_table_does_not_cascade(self):
        database = load_module(self, "anders_health_core.database")
        lifecycle = load_module(self, "anders_health_core.lifecycle")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "incomplete-purge.db"
            subject_id = database.initialize(db_path)
            with database.connect(db_path) as conn:
                conn.execute("CREATE TABLE non_cascading_fixture(subject_id TEXT NOT NULL)")
                conn.execute("INSERT INTO non_cascading_fixture VALUES (?)", (subject_id,))
                conn.commit()
                with self.assertRaisesRegex(RuntimeError, "subject purge incomplete"):
                    lifecycle.purge_subject(conn, subject_id)
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM subject").fetchone()[0])
                self.assertEqual(1, conn.execute("SELECT COUNT(*) FROM non_cascading_fixture").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
