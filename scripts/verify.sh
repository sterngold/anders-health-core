#!/bin/sh
set -eu

AHC_PYTHON_BIN="${AHC_PYTHON_BIN:-python3}"
AHC_TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$AHC_TMP_DIR"' EXIT

"$AHC_PYTHON_BIN" -m tests.run
"$AHC_PYTHON_BIN" scripts/privacy-check.py .
AHC_PYTHON_BIN="$AHC_PYTHON_BIN" sh scripts/verify-history.sh .
"$AHC_PYTHON_BIN" -m anders_health_core.cli demo --db "$AHC_TMP_DIR/demo.db" >/dev/null
"$AHC_PYTHON_BIN" -m anders_health_core.cli verify --db "$AHC_TMP_DIR/demo.db"
