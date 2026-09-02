import importlib
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


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
        self.assertEqual(6, len(examples))
        for contract_name, payload in examples.items():
            self.assertEqual([], contracts.validate_contract(contract_name, payload))
        broken = dict(examples["SourceManifest"])
        broken.pop("source_id")
        self.assertEqual(["missing required field: source_id"], contracts.validate_contract("SourceManifest", broken))

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
            subject_id = json.loads(init.stdout)["subject_id"]
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
