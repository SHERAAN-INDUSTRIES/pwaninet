#!/usr/bin/env bash
# Build and start an isolated development stack without host Python dependencies.
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
if [[ $# -gt 0 ]]; then
  echo 'Usage: ./scripts/setup-docker.sh'
  echo 'Configure ports in .env.docker-dev after the first run, then rerun.'
  [[ "$1" == --help || "$1" == -h ]] && exit 0
  exit 2
fi
command -v docker >/dev/null || { echo 'Install Docker with Compose, then retry.' >&2; exit 1; }
docker compose version >/dev/null
docker info >/dev/null 2>&1 || { echo 'Start Docker and ensure your account can access it, then retry.' >&2; exit 1; }
command -v openssl >/dev/null || { echo 'OpenSSL is required to generate local secrets.' >&2; exit 1; }
umask 077
if [[ ! -e .env.docker-dev ]]; then
  cat > .env.docker-dev <<ENV
# Private Docker development configuration; independent of .env and production.
SECRET_KEY=$(openssl rand -hex 48)
DB_NAME=pwaninet_dev
DB_USER=postgres
DB_PASSWORD=$(openssl rand -hex 24)
POSTGRES_HOST_PORT=5432
REDIS_HOST_PORT=6379
WEB_HOST_PORT=8000
VASTO_INTERNAL_API_ENABLED=true
ENV
  echo 'Created .env.docker-dev with private local credentials.'
fi
mkdir -p .secrets
if [[ ! -e .secrets/docker-dev-oidc.pem ]]; then
  key_tmp=$(mktemp .secrets/docker-dev-oidc.XXXXXX)
  trap 'rm -f "$key_tmp"' EXIT
  openssl genrsa -traditional -out "$key_tmp" 4096 >/dev/null 2>&1
  mv "$key_tmp" .secrets/docker-dev-oidc.pem
  trap - EXIT
fi
compose=(docker compose --project-name pwaninet-dev --env-file .env.docker-dev -f docker-compose.dev.yml)
trap 'echo "Setup stopped. Check port conflicts and .env.docker-dev; rerun after fixing the error. Existing data is retained." >&2' ERR
"${compose[@]}" config --quiet
echo 'Starting PostgreSQL and Redis and waiting for readiness...'
"${compose[@]}" up -d --wait --wait-timeout 120 db redis
echo 'Building the Django backend (the first build can take several minutes)...'
"${compose[@]}" build web
# Stop existing application processes before applying migrations on a rerun.
"${compose[@]}" stop web celery_worker celery_beat
"${compose[@]}" run --rm --no-deps web python manage.py shell --no-imports -c 'from django.db import connection; from django.conf import settings; import redis; connection.ensure_connection(); assert redis.Redis.from_url(settings.CACHES["default"]["LOCATION"]).ping(); print("Django connected to PostgreSQL and Redis.")'
"${compose[@]}" run --rm --no-deps web python manage.py check
"${compose[@]}" run --rm --no-deps web python manage.py migrate --noinput
# Capture the real database token and save it on the host, without displaying it.
service_account_failed=0
if account_output=$("${compose[@]}" run -T --rm --no-deps web python manage.py create_service_account --name Workspaces --username workspaces_service --email workspaces@service.local --token-only); then
  token_line=$(printf '%s\n' "$account_output" | sed -n '/^VASTO_SERVICE_TOKEN=[0-9a-f]\{40\}$/p' | tail -1)
  if [[ -n "$token_line" ]]; then
    env_tmp=$(mktemp .env.docker-dev.XXXXXX)
    while IFS= read -r line || [[ -n "$line" ]]; do
      [[ "$line" == VASTO_SERVICE_TOKEN=* ]] || printf '%s\n' "$line"
    done < .env.docker-dev > "$env_tmp"
    printf '%s\n' "$token_line" >> "$env_tmp"
    mv "$env_tmp" .env.docker-dev
    echo 'Saved the database-backed service token to .env.docker-dev (value hidden).'
  else
    service_account_failed=1
  fi
else
  service_account_failed=1
fi
unset account_output token_line
if (( service_account_failed )); then
  echo 'Service-account setup failed; continuing web startup. Rerun setup to retry it.' >&2
fi
"${compose[@]}" run --rm --no-deps web python manage.py collectstatic --noinput
echo 'Starting Django, Celery worker, and scheduler...'
"${compose[@]}" up -d --no-build --wait --wait-timeout 180
"${compose[@]}" ps
echo 'Saved service token: .env.docker-dev → VASTO_SERVICE_TOKEN.'
echo 'Recover it with: bash scripts/setup-dev.sh --show-service-token-only --docker'
echo 'Backend is ready at the web address shown above (default http://127.0.0.1:8000).'
echo 'Create your administrator: docker compose -p pwaninet-dev --env-file .env.docker-dev -f docker-compose.dev.yml exec web python manage.py createsuperuser'

if (( service_account_failed )); then exit 1; fi
