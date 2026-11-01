#!/usr/bin/env bash
set -euo pipefail

APP_DIR="/srv/prod-app/src"
COMPOSE_FILE="$APP_DIR/docker-compose.prod.yml"
SPARK_URL="http://127.0.0.1:8100"

usage() {
  cat <<'EOF'
Usage: ./deploy.sh [options]

  (no options)      Pull, build, migrate, collectstatic, recreate services.
  --force, -f       Force a rebuild even when there are no code changes.
  --bench           Run the spark-vector embedding benchmark after deploying.
  --bench-only      Skip the deploy; only run the benchmark (starts the worker).
  --reembed         After deploying, show the popularity-first re-embed plan
                    and run the migration (docs/local-models-design.md §6).
  --help, -h        This message.

The embedding benchmark measures query p50/p95, batch latency, the 768-dim
contract and the query-during-bulk case against the real 2-vCPU box.
EOF
}

FORCE_REBUILD=false
RUN_BENCH=false
RUN_REEMBED=false
BENCH_ONLY=false

for arg in "$@"; do
  case "$arg" in
    --force|-f) FORCE_REBUILD=true ;;
    --bench) RUN_BENCH=true ;;
    --bench-only) BENCH_ONLY=true; RUN_BENCH=true ;;
    --reembed) RUN_REEMBED=true ;;
    --help|-h) usage; exit 0 ;;
    *) echo "[!] Unknown argument: $arg"; usage; exit 1 ;;
  esac
done

compose() {
  docker compose -f "$COMPOSE_FILE" "$@"
}

cd "$APP_DIR"

# Wait for the local embedding worker to report a warm model.
wait_for_spark_vector() {
  local tries="${1:-45}"
  echo -n "[i] Waiting for spark-vector to warm up"
  for _ in $(seq 1 "$tries"); do
    if compose exec -T spark_vector python -c "
import sys, urllib.request
try:
    body = urllib.request.urlopen('${SPARK_URL}/healthz', timeout=4).read().decode()
except Exception:
    sys.exit(1)
sys.exit(0 if '\"warm\":true' in body else 1)
" >/dev/null 2>&1; then
      echo " — ready."
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo " — NOT ready."
  return 1
}

# Report the configured embedding provider (never any secret value).
embedding_provider() {
  grep -E '^PWANIMATE_EMBEDDING_PROVIDER=' .env 2>/dev/null \
    | tail -1 | cut -d= -f2- | tr -d "\"' \r" || true
}

run_benchmark() {
  echo ""
  echo "=================================================="
  echo " [vasto] spark-vector embedding benchmark"
  echo "=================================================="
  compose exec -T spark_vector python bench.py --url "${SPARK_URL}/v1/embeddings"
}

if [ "$BENCH_ONLY" = true ]; then
  echo "=================================================="
  echo " [vasto] Benchmark only (no deploy)"
  echo "=================================================="
  compose up -d spark_vector
  wait_for_spark_vector || true
  run_benchmark
  echo "=================================================="
  echo " [✓] Benchmark complete."
  echo "=================================================="
  exit 0
fi

echo "=================================================="
echo " [vasto] Initiating Automated Production Deploy"
echo "=================================================="

# 1. Check for dirty local working directory
if ! git diff-index --quiet HEAD --; then
  echo "[!] ERROR: Local uncommitted changes detected in $APP_DIR. Aborting deploy."
  git status -s
  exit 1
fi

# 2. Record HEAD hash before pulling
OLD_HEAD=$(git rev-parse HEAD)

echo "[1/7] Pulling latest code from origin/main..."
git pull origin main

NEW_HEAD=$(git rev-parse HEAD)

if [ "$OLD_HEAD" = "$NEW_HEAD" ]; then
  echo "[i] Already up to date. Proceeding with service refresh check."
fi

# 3. Build container images on code updates (Docker layer cache makes code-only builds take ~1-2s).
#    spark_vector bakes the pinned model at build time: the asset layer is cached,
#    so only the application layer rebuilds after the first build.
#
#    The build is the only step that needs a lot of disk: a fresh image plus
#    its build cache is several GB, and the old image stays on disk until the
#    new container starts. On a 29 GB box that can run the filesystem out
#    mid-export, which fails the deploy with "no space left on device" and
#    leaves a half-written image behind. So the space is checked first, and
#    the services are built one at a time to keep the peak down.
MIN_BUILD_GB="${MIN_BUILD_GB:-8}"

free_gb() {
  df -BG --output=avail / | tail -1 | tr -dc '0-9'
}

ensure_build_space() {
  local available
  available="$(free_gb)"
  if [ "${available:-0}" -ge "$MIN_BUILD_GB" ]; then
    echo "[i] Disk: ${available}G available (need ${MIN_BUILD_GB}G)."
    return 0
  fi

  echo "[!] Only ${available}G free; the build needs about ${MIN_BUILD_GB}G."
  echo "[i] Reclaiming the Docker build cache and unused images..."
  docker builder prune -af >/dev/null 2>&1 || true
  docker image prune -af >/dev/null 2>&1 || true
  sudo apt-get clean >/dev/null 2>&1 || true

  available="$(free_gb)"
  if [ "${available:-0}" -lt "$MIN_BUILD_GB" ]; then
    echo "[!] ERROR: ${available}G free after cleanup; the build needs ${MIN_BUILD_GB}G."
    echo "[!] Free space on the VM (old media, old backups) and try again."
    exit 1
  fi
  echo "[i] Disk: ${available}G available after cleanup."
}

if [ "$OLD_HEAD" != "$NEW_HEAD" ] || [ "$FORCE_REBUILD" = true ]; then
  echo "[2/7] Code updates detected. Building fresh container images..."
  ensure_build_space
  for service in web celery_worker spark_vector; do
    echo "    building ${service}..."
    compose build "$service"
  done
else
  echo "[2/7] No code changes detected. Rebuild skipped (use --force to override)."
fi

# 4. Database Migrations
echo "[3/7] Applying database migrations..."
compose exec -T web python manage.py migrate --noinput || \
  compose run --rm web python manage.py migrate --noinput

# 4b. Billing registry: idempotent, derived from the live engine ladders.
# Keeps the tier catalogue, the model registry and the ladder in step, and
# re-classifies anything the ladders have changed (e.g. a paid rung).
echo "[3b/7] Seeding the tier catalogue and model registry..."
compose exec -T web python manage.py seed_model_registry || \
  compose run --rm web python manage.py seed_model_registry
# The $0 guarantee: every rung registered, no free rung pointing at a paid model.
compose exec -T web python manage.py check_model_registry || \
  echo "[!] Registry check reported problems — see the output above."

# 5. Collect Static Files
echo "[4/7] Collecting static assets..."
compose exec -T web python manage.py collectstatic --noinput || \
  compose run --rm web python manage.py collectstatic --noinput

# 6. Recreate Services with Updated Image
echo "[5/7] Refreshing web, celery and spark-vector services..."
compose up -d --force-recreate web celery_worker spark_vector

# 7. Health: the local embedding worker + provider consistency
echo "[6/7] Checking the embedding worker..."
if wait_for_spark_vector; then
  compose exec -T spark_vector python -c "
import urllib.request
print('   ', urllib.request.urlopen('${SPARK_URL}/healthz', timeout=4).read().decode())
" || true
else
  echo "[!] WARNING: spark-vector is not warm. Semantic search will degrade to lexical"
  echo "    until it recovers. Check: docker compose -f $COMPOSE_FILE logs spark_vector"
fi

PROVIDER="$(embedding_provider)"
if [ "$PROVIDER" = "selfhosted" ]; then
  echo "[i] Embedding provider: selfhosted (local spark-vector, \$0 API cost)."
  echo "[i] Corpus size (chunks to migrate):"
  compose exec -T web python manage.py shell -c \
    "from pwanimate.models import DocumentChunk; print('   ', DocumentChunk.objects.count(), 'chunks')" || true
else
  echo "[i] Embedding provider: ${PROVIDER:-gemini} (API). Flip with the runbook in docs/local-models-design.md §6."
fi

if [ "$RUN_BENCH" = true ]; then
  run_benchmark
fi

if [ "$RUN_REEMBED" = true ]; then
  echo ""
  echo "=================================================="
  echo " [vasto] Popularity-first re-embed (plan)"
  echo "=================================================="
  compose exec -T web python manage.py reembed_corpus --dry-run
  echo ""
  echo "[i] Starting the migration (most-searched documents first)..."
  compose exec -T web python manage.py reembed_corpus --wait --concurrency 2
fi

# 8. System Health Status
echo "[7/7] Verifying service statuses..."
compose ps

echo "=================================================="
echo " [✓] Deployment Complete. Systems Operational."
echo "=================================================="
