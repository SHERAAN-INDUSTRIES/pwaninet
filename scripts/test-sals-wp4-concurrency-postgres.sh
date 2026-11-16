#!/usr/bin/env bash
# OWNER ONLY: same disposable isolation runner as the complete WP4 suite.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sals_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
"$sals_python" -m tests.sals_wp0.run --suite sals03wp2 \
  tests.sals_03_wp2.test_concurrency \
  tests.sals_academic_continuity.test_postgres_locking \
  tests.sals_wp4
