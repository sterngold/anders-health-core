import importlib
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]


def load_module(testcase, name):
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        testcase.fail(f"required module {name} is unavailable: {exc}")


class PrivacyGateTests(unittest.TestCase):
    def test_known_bad_secrets_and_private_paths_fail(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        home_path = "/" + "Users/example/private"
        jwt = ".".join(("eyJhbGciOiJIUzI1NiJ9", "aaaaaaaaaaaaaaaa", "bbbbbbbbbbbbbbbb"))
        private_key = "-----BEGIN " + "PRIVATE KEY-----"
        private_identifier = "patient" + "_id=real-person-123"
        health_marker = "health" + "_record=real-observation"
        bad = f"home={home_path}\ntoken={jwt}\n{private_key}\n{private_identifier}\n{health_marker}"
        violations = privacy_gate.scan_text(bad)
        self.assertEqual(5, len(violations))
        self.assertEqual(
            {"absolute_home_path", "jwt_like_token", "private_key",
             "private_identifier", "health_record_marker"},
            {item["reason_code"] for item in violations},
        )

    def test_synthetic_binary_requires_explicit_name_and_valid_magic(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        good = '{"subject_id":"00000000-0000-4000-8000-000000000001","source":"synthetic.json"}'
        self.assertEqual([], privacy_gate.scan_text(good))
        self.assertEqual([], privacy_gate.scan_blob("synthetic-fixture.png", b"\x89PNG\r\n\x1a\n\xff"))
        self.assertTrue(privacy_gate.scan_blob("nonsynthetic-private.png", b"\x89PNG\r\n\x1a\n\xff"))
        for name, data in (("private.png", b"\x89PNG\r\n\x1a\n\xff"),
                           ("private.pdf", b"%PDF-1.7\n\xff"), ("private.dat", b"\x00\xff")):
            self.assertTrue(privacy_gate.scan_blob(name, data), name)

    def test_repository_scan_skips_git_metadata_and_reports_file_and_line(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("/" + "Users/ignored", encoding="utf-8")
            private_path = "/" + "Users/private/file"
            (root / "bad.txt").write_text(f"line one\n{private_path}\n", encoding="utf-8")
            (root / "health.db").write_bytes(b"SQLite format 3\0private")
            (root / "synthetic-fixture.db").write_bytes(b"SQLite format 3\0synthetic")
            violations = privacy_gate.scan_repository(root)
        self.assertEqual(2, len(violations))
        self.assertEqual("bad.txt", violations[0]["file"])
        self.assertEqual(2, violations[0]["line"])
        self.assertEqual(("health.db", "private_health_artifact"),
                         (violations[1]["file"], violations[1]["reason_code"]))

    def test_repository_scan_order_does_not_depend_on_directory_listing_order(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        real_rglob = Path.rglob

        def reversed_listing(path, pattern):
            return iter(sorted(real_rglob(path, pattern), reverse=True))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "bad.txt").write_text("/" + "Users/private/file\n", encoding="utf-8")
            (root / "health.db").write_bytes(b"SQLite format 3\0private")
            natural = [v["file"] for v in privacy_gate.scan_repository(root)]
            with mock.patch.object(Path, "rglob", reversed_listing):
                reversed_order = [v["file"] for v in privacy_gate.scan_repository(root)]
        self.assertEqual(["bad.txt", "health.db"], natural)
        self.assertEqual(natural, reversed_order)

    def test_history_scan_has_literal_clean_and_forbidden_arms(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.email", "synthetic@example.invalid"], check=True)
            subprocess.run(["git", "-C", str(root), "config", "user.name", "Synthetic Fixture"], check=True)
            fixture = root / "fixture.txt"
            fixture.write_text("subject_id=00000000-0000-4000-8000-000000000001\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "add", "fixture.txt"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "clean"], check=True)
            command = ["sh", str(ROOT / "scripts" / "verify-history.sh"), str(root)]
            self.assertEqual(0, subprocess.run(command, check=False).returncode)
            synthetic = root / "synthetic-fixture.db"
            synthetic.write_bytes(b"SQLite format 3\0synthetic")
            subprocess.run(["git", "-C", str(root), "add", synthetic.name], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "synthetic"], check=True)
            self.assertEqual(0, subprocess.run(command, check=False).returncode)
            private = root / "health.db"
            private.write_bytes(b"SQLite format 3\0private")
            subprocess.run(["git", "-C", str(root), "add", private.name], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "private"], check=True)
            subprocess.run(["git", "-C", str(root), "mv", private.name, "synthetic-renamed.db"], check=True)
            subprocess.run(["git", "-C", str(root), "commit", "-qm", "renamed"], check=True)
            self.assertEqual(1, subprocess.run(command, check=False).returncode)
            fixture.write_text("patient" + "_id=real-person-123\n", encoding="utf-8")
            subprocess.run(["git", "-C", str(root), "commit", "-qam", "bad"], check=True)
            self.assertEqual(1, subprocess.run(command, check=False).returncode)


if __name__ == "__main__":
    unittest.main()
