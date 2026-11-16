#!/usr/bin/env bash
# Owner-run PostgreSQL handoff. Never reads the application's database settings.
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
sals_phase="${1:-all}"
sals_python="${SALS_TEST_PYTHON:-venv/bin/python}"
if [[ "$sals_phase" == cleanup ]]; then
  # Normal runs remove their own container in finally. Only remove stopped
  # leftovers with the explicit SALS 0.2 test label after interrupted runs.
  docker ps -aq --filter label=sals.suite=sals02 --filter status=exited | xargs -r docker rm
  docker ps -a --filter label=sals.suite=sals02 --format 'table {{.Names}}\t{{.Status}}'
  exit 0
fi
test -x "$sals_python"
"$sals_python" -c 'import django, psycopg2, pgvector; print("Python test dependencies available")'
docker info --format 'Docker server: {{.ServerVersion}}'
if [[ "$sals_phase" == prerequisites ]]; then exit 0; fi
if [[ "$sals_phase" == prepare ]]; then
  docker pull pgvector/pgvector:pg15
  exit 0
fi
sals_regressions=(
  tests.spark_memory
  tests.sals_wp3.test_chat_memory tests.sals_wp3.test_offline
  tests.sals_wp3.test_database.MemoryConsentTests
  tests.sals_wp3.test_database.ChatProposalTests
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests
  pwanimate.tests.test_conversation_memory
  pwanimate.tests.test_streaming.GatewayStreamingTests
  pwanimate.tests.test_streaming.GroqStreamingTests
  pwanimate.tests.test_streaming.OpenRouterStreamingTests
  pwanimate.tests.test_streaming.GeminiStreamingTests
  pwanimate.tests.test_streaming.MockProviderStreamingTests
  pwanimate.tests.test_agent_reconciliation.ActivityTaxonomyTests
)
case "$sals_phase" in
  schema)
    sals_labels=(tests.sals_wp0.test_isolation tests.sals_wp0.test_database tests.sals_02.test_persistence) ;;
  concurrency)
    sals_labels=(tests.sals_02.test_foundation.PostgreSQLUnitConcurrencyTests
      tests.spark_memory.test_pipeline.PostgreSQLConcurrencyTests
      tests.sals_wp3.test_database.ConcurrentConsentTests) ;;
  ownership)
    sals_labels=(tests.sals_02.test_foundation.UnitFoundationTests) ;;
  regressions)
    sals_labels=("${sals_regressions[@]}") ;;
  all)
    sals_labels=(tests.sals_wp0.test_isolation tests.sals_wp0.test_database tests.sals_02
      tests.sals_wp3.test_database.ConcurrentConsentTests "${sals_regressions[@]}") ;;
  *)
    echo 'Usage: bash scripts/test-sals02-postgres.sh prerequisites|prepare|schema|concurrency|ownership|regressions|all|cleanup' >&2
    exit 2 ;;
esac
# The runner creates fresh loopback-only PostgreSQL/pgvector resources and a
# disposable test database, migrates it, applies isolation guards and cleans up.
"$sals_python" -m tests.sals_wp0.run --suite sals02 "${sals_labels[@]}"
