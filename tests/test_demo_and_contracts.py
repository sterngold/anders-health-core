import importlib
import importlib.util
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker


ROOT = Path(__file__).resolve().parents[1]


def load_module(testcase, name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        testcase.fail(f"required module {name} is unavailable: {exc}")


class DemoAndContractTests(unittest.TestCase):
    def test_demo_build_has_literal_multidomain_denominators(self):
        demo = load_module(self, "anders_health_core.demo")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "demo.db"
            receipt = demo.build_demo(db_path)
            self.assertEqual(42, receipt["raw_versions"])
            self.assertEqual(42, receipt["normalized_facts"])
            self.assertEqual(6, receipt["metrics"])
            self.assertEqual(2, receipt["context_events"])
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(42, conn.execute("SELECT COUNT(*) FROM current_source_record").fetchone()[0])
                self.assertEqual(
                    (1, 1, 1, 1, 4),
                    tuple(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] for table in (
                        "coverage_result", "trend_result", "association_result",
                        "assessment_change_result", "derivation_receipt")),
                )
                self.assertEqual(
                    [("complete", 2), ("partial", 1)],
                    conn.execute("SELECT completeness_state,COUNT(*) FROM assessment_session "
                                 "GROUP BY completeness_state ORDER BY completeness_state").fetchall(),
                )

    def test_demo_rebuild_is_idempotent(self):
        demo = load_module(self, "anders_health_core.demo")
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "demo.db"
            first = demo.build_demo(db_path)
            second = demo.build_demo(db_path)
            self.assertEqual(first, second)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(42, conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0])
                self.assertEqual(42, conn.execute("SELECT COUNT(*) FROM normalized_fact").fetchone()[0])

    def test_json_contract_examples_validate_and_missing_field_fails(self):
        contracts = load_module(self, "anders_health_core.contracts")
        examples = json.loads((ROOT / "examples" / "contracts.json").read_text(encoding="utf-8"))
        self.assertEqual(9, len(examples))
        for contract_name in (
            "SourceManifest",
            "RawRecordEnvelope",
            "NormalizedFact",
            "ContextEvent",
            "QualityIssue",
        ):
            payload = examples[contract_name]
            self.assertEqual([], contracts.validate_contract(contract_name, payload))
        broken = dict(examples["SourceManifest"])
        broken.pop("source_id")
        self.assertEqual(["missing required field: source_id"], contracts.validate_contract("SourceManifest", broken))
        missing_rule = dict(examples["SourceManifest"])
        missing_rule.pop("completeness_rule")
        self.assertEqual(
            ["missing required field: completeness_rule"],
            contracts.validate_contract("SourceManifest", missing_rule),
        )
        denominator_by_example = {
            "AnalyticsCoverageResult": "possible_days",
            "AnalyticsTrendResult": "possible_days",
            "AnalyticsAssociationResult": "possible_pairs",
            "AnalyticsAssessmentChangeResult": "possible_sessions",
        }
        for example_name, denominator in denominator_by_example.items():
            payload = examples[example_name]
            self.assertEqual([], contracts.validate_contract("AnalyticsResult", payload))
            missing_denominator = dict(payload)
            missing_denominator.pop(denominator)
            self.assertIn(
                f"missing required field: {denominator}",
                contracts.validate_contract("AnalyticsResult", missing_denominator),
            )
            missing_exclusions = dict(payload)
            missing_exclusions.pop("exclusions")
            self.assertIn(
                "missing required field: exclusions",
                contracts.validate_contract("AnalyticsResult", missing_exclusions),
            )
            unrelated = dict(payload)
            unrelated["unrelated"] = True
            self.assertIn(
                "unexpected field: unrelated",
                contracts.validate_contract("AnalyticsResult", unrelated),
            )

    def test_analytics_result_schema_and_runtime_reject_incomplete_or_cross_branch_results(self):
        contracts = load_module(self, "anders_health_core.contracts")
        examples = json.loads((ROOT / "examples" / "contracts.json").read_text(encoding="utf-8"))
        schema = json.loads((ROOT / "schemas" / "analytics-result.schema.json").read_text(encoding="utf-8"))
        validator = Draft202012Validator(schema, format_checker=FormatChecker())
        result_cases = {
            "AnalyticsCoverageResult": ("possible_days", "association_id"),
            "AnalyticsTrendResult": ("possible_days", "association_id"),
            "AnalyticsAssociationResult": ("possible_pairs", "possible_sessions"),
            "AnalyticsAssessmentChangeResult": ("possible_sessions", "possible_pairs"),
        }
        self.assertEqual(4, len(result_cases))
        rejected_cases = 0
        for example_name, (denominator, cross_branch_field) in result_cases.items():
            payload = examples[example_name]
            self.assertEqual([], list(validator.iter_errors(payload)))
            self.assertEqual([], contracts.validate_contract("AnalyticsResult", payload))
            for field, value in (
                (denominator, None),
                ("exclusions", None),
                ("unrelated", True),
                (cross_branch_field, 0),
            ):
                broken = dict(payload)
                if value is None:
                    broken.pop(field)
                else:
                    broken[field] = value
                self.assertTrue(list(validator.iter_errors(broken)), (example_name, field))
                self.assertTrue(
                    contracts.validate_contract("AnalyticsResult", broken),
                    (example_name, field),
                )
                rejected_cases += 1
        self.assertEqual(16, rejected_cases)

    def test_every_public_contract_uses_its_published_draft_2020_12_schema(self):
        contracts = load_module(self, "anders_health_core.contracts")
        records = load_module(self, "anders_health_core.records")
        examples = json.loads((ROOT / "examples" / "contracts.json").read_text(encoding="utf-8"))
        cases = (
            ("SourceManifest", records.synthetic_source_manifest(), "source-manifest.schema.json"),
            ("MetricDefinition", records.synthetic_metric_definition(), "metric-definition.schema.json"),
            ("RawRecordEnvelope", examples["RawRecordEnvelope"], "raw-record-envelope.schema.json"),
            ("NormalizedFact", examples["NormalizedFact"], "normalized-fact.schema.json"),
            ("ContextEvent", examples["ContextEvent"], "context-event.schema.json"),
            ("QualityIssue", examples["QualityIssue"], "quality-issue.schema.json"),
            ("AnalyticsResult", examples["AnalyticsCoverageResult"], "analytics-result.schema.json"),
            ("AnalyticsResult", examples["AnalyticsTrendResult"], "analytics-result.schema.json"),
            ("AnalyticsResult", examples["AnalyticsAssociationResult"], "analytics-result.schema.json"),
            ("AnalyticsResult", examples["AnalyticsAssessmentChangeResult"], "analytics-result.schema.json"),
        )
        self.assertEqual(10, len(cases))
        for contract_name, payload, schema_name in cases:
            schema = json.loads((ROOT / "schemas" / schema_name).read_text(encoding="utf-8"))
            Draft202012Validator.check_schema(schema)
            self.assertEqual([], list(Draft202012Validator(
                schema, format_checker=FormatChecker()
            ).iter_errors(payload)), contract_name)
            self.assertEqual([], contracts.validate_contract(contract_name, payload), contract_name)

        interval_without_end = dict(examples["RawRecordEnvelope"])
        interval_without_end["timestamp_precision"] = "interval"
        interval_without_end.pop("event_end_utc", None)
        both_values = {**examples["NormalizedFact"], "text_value": "ambiguous"}
        no_values = dict(examples["NormalizedFact"])
        no_values.pop("numeric_value")
        self.assertTrue(contracts.validate_contract("RawRecordEnvelope", interval_without_end))
        self.assertTrue(contracts.validate_contract("NormalizedFact", both_values))
        self.assertTrue(contracts.validate_contract("NormalizedFact", no_values))

    def test_built_wheel_loads_every_schema_outside_checkout(self):
        if importlib.util.find_spec("pip") is None:
            self.skipTest("pip is not importable in this interpreter; this test builds and installs the wheel with `python -m pip`")
        with tempfile.TemporaryDirectory() as tmp:
            root, source, wheels, target = Path(tmp), Path(tmp) / "source", Path(tmp) / "wheels", Path(tmp) / "site"
            shutil.copytree(ROOT, source, ignore=shutil.ignore_patterns(".git", "*.egg-info", "build", "dist", "__pycache__"))
            env = {**os.environ, "PIP_CACHE_DIR": str(root / "pip-cache")}
            built = subprocess.run([sys.executable, "-m", "pip", "wheel", ".", "--no-deps", "-w", str(wheels)], cwd=source, env=env, capture_output=True, text=True)
            self.assertEqual(0, built.returncode, built.stderr)
            wheel = next(wheels.glob("*.whl"))
            installed = subprocess.run([sys.executable, "-m", "pip", "install", "--no-deps", "--target", str(target), str(wheel)], env=env, capture_output=True, text=True)
            self.assertEqual(0, installed.returncode, installed.stderr)
            code = "from anders_health_core import contracts; assert len(contracts.SCHEMA_FILES)==7; [contracts.validate_contract(name,{}) for name in contracts.SCHEMA_FILES]"
            smoke = subprocess.run([sys.executable, "-c", code], cwd=root, env={**env, "PYTHONPATH": str(target)}, capture_output=True, text=True)
            self.assertEqual(0, smoke.returncode, smoke.stderr)

    def test_cli_init_demo_verify_export_and_purge_complete_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "demo.db"
            export_path = Path(tmp) / "export.json"
            build = subprocess.run(
                [sys.executable, "-m", "anders_health_core.cli", "demo", "--db", str(db_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, build.returncode, build.stderr)
            receipt = json.loads(build.stdout)
            verify = subprocess.run(
                [sys.executable, "-m", "anders_health_core.cli", "verify", "--db", str(db_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, verify.returncode, verify.stderr)
            self.assertEqual(42, json.loads(verify.stdout)["raw_versions"])
            export = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "anders_health_core.cli",
                    "export",
                    "--db",
                    str(db_path),
                    "--subject",
                    receipt["subject_id"],
                    "--output",
                    str(export_path),
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, export.returncode, export.stderr)
            self.assertTrue(export_path.exists())
            purge = subprocess.run(
                [
                    sys.executable,
                    "-m",
                    "anders_health_core.cli",
                    "purge",
                    "--db",
                    str(db_path),
                    "--subject",
                    receipt["subject_id"],
                    "--confirm-subject",
                    receipt["subject_id"],
                ],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, purge.returncode, purge.stderr)
            self.assertEqual(0, json.loads(purge.stdout)["remaining_subjects"])

    def test_cli_registers_contracts_and_maps_example_to_local_subject(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "core.db"
            init = subprocess.run(
                [sys.executable, "-m", "anders_health_core.cli", "init", "--db", str(db_path)],
                cwd=ROOT,
                check=False,
                capture_output=True,
                text=True,
            )
            self.assertEqual(0, init.returncode, init.stderr)
            initialized = json.loads(init.stdout)
            self.assertEqual(5, initialized["schema_version"])
            subject_id = initialized["subject_id"]
            commands = [
                ["register-source", "--input", str(ROOT / "examples" / "source-manifest.json")],
                ["register-metric", "--input", str(ROOT / "examples" / "metric-definition.json")],
                [
                    "import-jsonl",
                    "--input",
                    str(ROOT / "examples" / "raw-records.jsonl"),
                    "--subject",
                    subject_id,
                ],
            ]
            for command in commands:
                result = subprocess.run(
                    [sys.executable, "-m", "anders_health_core.cli", *command, "--db", str(db_path)],
                    cwd=ROOT,
                    check=False,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(0, result.returncode, result.stderr)
            with sqlite3.connect(db_path) as conn:
                self.assertEqual(2, conn.execute("SELECT COUNT(*) FROM source_record_version").fetchone()[0])


if __name__ == "__main__":
    unittest.main()
