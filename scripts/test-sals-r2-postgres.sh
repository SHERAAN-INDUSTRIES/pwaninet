#!/usr/bin/env bash
# OWNER ONLY. Existing runner provisions disposable PostgreSQL; no owner DB URL.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sals_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
"$sals_python" -m tests.sals_wp0.run --suite sals03wp2 \
  tests.spark_memory tests.sals_02 tests.sals_03_wp1 tests.sals_03_wp2
