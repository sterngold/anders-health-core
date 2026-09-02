#!/bin/sh
set -eu
ROOT="${1:-.}"
exec "${AHC_PYTHON_BIN:-python3}" "$(dirname "$0")/privacy-check.py" --history "$ROOT"
