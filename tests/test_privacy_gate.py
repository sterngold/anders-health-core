import importlib
import tempfile
import unittest
from pathlib import Path


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
        bad = f"home={home_path}\ntoken={jwt}\n{private_key}"
        violations = privacy_gate.scan_text(bad)
        self.assertEqual(3, len(violations))
        self.assertEqual(
            {"absolute_home_path", "jwt_like_token", "private_key"},
            {item["reason_code"] for item in violations},
        )

    def test_known_good_synthetic_contract_passes(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        good = '{"subject_id":"00000000-0000-4000-8000-000000000001","source":"synthetic.json"}'
        self.assertEqual([], privacy_gate.scan_text(good))

    def test_repository_scan_skips_git_metadata_and_reports_file_and_line(self):
        privacy_gate = load_module(self, "anders_health_core.privacy_gate")
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".git").mkdir()
            (root / ".git" / "config").write_text("/" + "Users/ignored", encoding="utf-8")
            private_path = "/" + "Users/private/file"
            (root / "bad.txt").write_text(f"line one\n{private_path}\n", encoding="utf-8")
            violations = privacy_gate.scan_repository(root)
        self.assertEqual(1, len(violations))
        self.assertEqual("bad.txt", violations[0]["file"])
        self.assertEqual(2, violations[0]["line"])


if __name__ == "__main__":
    unittest.main()
