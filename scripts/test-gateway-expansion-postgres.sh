#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
"${SALS_TEST_PYTHON:-.venv/bin/python}" -m tests.sals_wp0.run --suite sals03wp2 \
  tests.gateway_expansion pwanimate.tests.test_gateway \
  tests.sals_wp4.test_sri_repairs tests.sals_03_wp2 tests.spark_memory
