#!/usr/bin/env bash
# Isolated SQLite service/API tests and Node contracts. No PostgreSQL or Docker.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sals_python="${SALS_TEST_PYTHON:-.venv/bin/python}"
if [[ ! -x "$sals_python" && -z "${SALS_TEST_PYTHON:-}" ]]; then sals_python=venv/bin/python; fi
test -x "$sals_python"
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings "$sals_python" manage.py test \
  tests.sals_03_wp2 \
  tests.sals_03_wp1 \
  tests.sals_02 \
  tests.spark_memory \
  tests.sals_wp1 \
  tests.sals_wp3.test_chat_memory \
  tests.sals_wp3.test_runtime_pipeline \
  tests.sals_wp3.test_offline \
  tests.sals_wp3.test_database.MemoryConsentTests \
  tests.sals_wp3.test_database.ChatProposalTests \
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests \
  pwanimate.tests.test_conversation_memory \
  pwanimate.tests.test_streaming.GatewayStreamingTests \
  pwanimate.tests.test_streaming.GroqStreamingTests \
  pwanimate.tests.test_streaming.OpenRouterStreamingTests \
  pwanimate.tests.test_streaming.GeminiStreamingTests \
  pwanimate.tests.test_streaming.MockProviderStreamingTests \
  pwanimate.tests.test_agent_reconciliation.ActivityTaxonomyTests \
  --noinput --verbosity 1
node --check static/js/pwanimate/pwanimate-chat.js
node tests/spark/teaching-flow.cjs
node tests/spark/lifecycle-resume.cjs
node tests/spark/markdown-math-unit.cjs
node tests/spark/question-controls.cjs
node tests/spark/composer-interaction.cjs
node tests/spark/quiz-flow.cjs
bash -n scripts/test-sals03-wp2-postgres.sh
