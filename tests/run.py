import sys
import unittest


EXPECTED_TEST_COUNT = 64


def main() -> int:
    suite = unittest.defaultTestLoader.discover("tests", pattern="test_*.py")
    count = suite.countTestCases()
    if count != EXPECTED_TEST_COUNT:
        print(f"ERROR: expected {EXPECTED_TEST_COUNT} tests, discovered {count}", file=sys.stderr)
        return 2
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
