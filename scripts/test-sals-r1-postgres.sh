#!/usr/bin/env bash
# OWNER ONLY: existing runner provisions disposable PostgreSQL; never owner DB.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sals_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
"$sals_python" -m tests.sals_wp0.run --suite sals03wp2 \
  tests.sals_03_wp2.test_followups tests.sals_03_wp2.test_followup_repair \
  tests.sals_03_wp2.test_concurrency tests.sals_03_wp2.test_persistence
