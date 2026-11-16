#!/usr/bin/env bash
# Disposable SQLite + mocked providers. Never loads owner settings.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
r4_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings "$r4_python" manage.py test \
  tests.spark_r4 tests.sals_wp3.test_runtime_pipeline tests.sals_wp4 \
  tests.sals_academic_continuity tests.sals_03_wp2 tests.spark_memory \
  pwanimate.tests.test_document_context \
  pwanimate.tests.test_streaming.GatewayStreamingTests \
  pwanimate.tests.test_streaming.GroqStreamingTests \
  pwanimate.tests.test_streaming.OpenRouterStreamingTests \
  pwanimate.tests.test_streaming.GeminiStreamingTests \
  pwanimate.tests.test_activity_explanations pwanimate.tests.test_agent_activity \
  --noinput --verbosity 1
for check in activity-explanations activity-privacy lifecycle-resume study-context \
  markdown-math-unit question-controls composer-interaction stream-collapse followup-repair; do
  node "tests/spark/$check.cjs"
done
node --check static/js/pwanimate/activity-trail.js
node --check static/js/pwanimate/pwanimate-chat.js
