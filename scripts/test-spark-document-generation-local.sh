#!/usr/bin/env bash
# Isolated SQLite and mocked providers only; never loads owner database settings.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
export_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings "$export_python" manage.py test \
  tests.spark_exports --noinput
bash scripts/test-spark-r4-local.sh
node tests/spark/generated-documents.cjs
