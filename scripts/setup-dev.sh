#!/usr/bin/env bash
set -Eeuo pipefail
# Docker mode has no host Python or Workspaces registration prerequisites.
if [[ "${1:-}" == "--docker" ]]; then
  shift
  exec bash "$(dirname "${BASH_SOURCE[0]}")/setup-docker.sh" "$@"
fi
if [[ "${1:-}" == "--show-service-token-only" ]]; then
  cd "$(dirname "${BASH_SOURCE[0]}")/.."
  TOKEN_ENV=.env
  if [[ "${2:-}" == "--docker" ]]; then TOKEN_ENV=.env.docker-dev; fi
  exec python3 scripts/show-service-token.py "$TOKEN_ENV"
fi
if [[ -t 1 ]]; then C_BLUE=$'\033[1;34m'; C_GREEN=$'\033[1;32m'; C_RESET=$'\033[0m'; else C_BLUE=''; C_GREEN=''; C_RESET=''; fi
section() { printf '\n%b── %s ──%b\n' "$C_BLUE" "$1" "$C_RESET"; }
success() { printf '%b✓%b %s\n' "$C_GREEN" "$C_RESET" "$1"; }
printf '\n%bPwaninet · Local setup%b\n' "$C_BLUE" "$C_RESET"
printf 'This setup checks your tools, configures local services, and prepares Workspaces sign-in.\n'
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
SKIP=0
SHOW_TOKEN=0
DEVELOPER="${VASTO_DEVELOPER:-}"
OWNER_USERNAME="${VASTO_OWNER_USERNAME:-}"
REDIRECT_URI="http://127.0.0.1:8001/api/auth/vasto/callback/"
CREDENTIALS_FILE=""
while (($#)); do
  case "$1" in
    --show-service-token) SHOW_TOKEN=1; shift ;;
    --skip-containers) SKIP=1; shift ;;
    --developer) (($# >= 2)) || { echo "--developer requires an identifier." >&2; exit 2; }; DEVELOPER="$2"; shift 2 ;;
    --owner-username) (($# >= 2)) || { echo "--owner-username requires a Vasto staff username." >&2; exit 2; }; OWNER_USERNAME="$2"; shift 2 ;;
    --redirect-uri) (($# >= 2)) || { echo "--redirect-uri requires a callback URL." >&2; exit 2; }; REDIRECT_URI="$2"; shift 2 ;;
    --credentials-file) (($# >= 2)) || { echo "--credentials-file requires an output path." >&2; exit 2; }; CREDENTIALS_FILE="$2"; shift 2 ;;
    -h|--help) echo "Usage: $0 [--docker] [--developer ID] [--owner-username USER] [--redirect-uri URL] [--credentials-file PATH] [--skip-containers] [--show-service-token]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
FAILURES=()
run_step() {
  local label="$1"; shift
  if "$@"; then success "$label"; return 0; fi
  FAILURES+=("$label")
  printf 'Needs attention: %s; continuing independent steps.\n' "$label" >&2
  return 1
}
setup_summary() {
  local status=$?
  printf '\nSetup summary (exit %s)\n' "$status"
  if ((${#FAILURES[@]})); then printf '  Needs attention: %s\n' "${FAILURES[@]}"; fi
  if [[ -f .env ]]; then
    echo '  Pwaninet service token: .env → VASTO_SERVICE_TOKEN (retained across retries).'
    echo '  To display it locally: bash scripts/setup-dev.sh --show-service-token-only'
    if (( SHOW_TOKEN )); then
      "${PY:-python3}" scripts/show-service-token.py .env || true
    fi
  fi
  echo "  OAuth credentials: ${CREDENTIALS_FILE:-$HOME/.config/vasto-workspaces/${DEVELOPER:-YOUR_IDENTIFIER}-local.env}"
  if (( status )); then echo '  Setup is incomplete. Fix the reported errors and rerun; saved credentials are retained.'; fi
}
trap setup_summary EXIT
if [[ -z "$DEVELOPER" ]]; then
  [[ -t 0 ]] || { echo "Pass --developer ID and --owner-username USER when running without a terminal." >&2; exit 2; }
  read -r -p "Developer identifier [${USER:-developer}]: " DEVELOPER
  DEVELOPER="${DEVELOPER:-${USER:-developer}}"
fi
[[ "$DEVELOPER" =~ ^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$ ]] || { echo "Developer identifier must use 1–64 letters, digits, dots, underscores, or hyphens." >&2; exit 2; }
if [[ -z "$OWNER_USERNAME" && ! -t 0 ]]; then
  echo "Pass --owner-username USER when running without a terminal." >&2
  exit 2
fi
case "$(uname -s)" in Linux*|Darwin*) ;; *) echo "Supported operating systems are Linux and macOS." >&2; exit 1;; esac
section 'Checking your computer'
command -v git >/dev/null || { echo "Git is required; install it and retry." >&2; exit 1; }
command -v openssl >/dev/null || { echo "OpenSSL is required; install it and retry." >&2; exit 1; }
PYTHON=""
VENV_DIR=""
python_is_supported() { "$1" -c 'import sys; raise SystemExit(sys.version_info[:2] < (3,11))' >/dev/null 2>&1; }
if [[ -n "${VIRTUAL_ENV:-}" && -x "$VIRTUAL_ENV/bin/python" ]]; then
  VENV_DIR="$VIRTUAL_ENV"
elif [[ -n "${CONDA_PREFIX:-}" && -x "$CONDA_PREFIX/bin/python" ]]; then
  VENV_DIR="$CONDA_PREFIX"
else
  FOUND_VENVS=()
  SUPPORTED_VENVS=()
  for dir in "$ROOT/.venv" "$ROOT/venv" "$ROOT/env"; do
    if [[ -x "$dir/bin/python" ]]; then
      FOUND_VENVS+=("$dir")
      if python_is_supported "$dir/bin/python"; then SUPPORTED_VENVS+=("$dir"); fi
    fi
  done
  if ((${#SUPPORTED_VENVS[@]})); then
    VENV_DIR="${SUPPORTED_VENVS[0]}"
  else
    shopt -s nullglob
    for cfg in "$ROOT"/*/pyvenv.cfg "$ROOT"/.[!.]*/pyvenv.cfg; do
      dir="${cfg%/pyvenv.cfg}"
      case "$dir" in "$ROOT/.venv"|"$ROOT/venv"|"$ROOT/env") continue;; esac
      [[ -x "$dir/bin/python" ]] || continue
      FOUND_VENVS+=("$dir")
      if python_is_supported "$dir/bin/python"; then SUPPORTED_VENVS+=("$dir"); fi
    done
    shopt -u nullglob
    if ((${#SUPPORTED_VENVS[@]} == 1)); then
      VENV_DIR="${SUPPORTED_VENVS[0]}"
    elif ((${#SUPPORTED_VENVS[@]} > 1)); then
      echo "Found multiple existing Python environments. Activate the one to use, then rerun setup." >&2
      exit 1
    fi
  fi
  if [[ -z "$VENV_DIR" && ${#FOUND_VENVS[@]} -gt 0 ]]; then
    echo "Found an existing Python environment, but it does not use Python 3.11 or newer. Activate a compatible environment or update it; setup will not create another." >&2
    exit 1
  fi
fi
if [[ -n "$VENV_DIR" ]]; then
  PY="$VENV_DIR/bin/python"
  python_is_supported "$PY" || { echo "The active Python environment must use Python 3.11 or newer. Activate a compatible environment and rerun." >&2; exit 1; }
  echo "Using existing Python environment: $VENV_DIR"
else
  for c in python3.14 python3.13 python3.12 python3.11 python3; do
    if command -v "$c" >/dev/null 2>&1 && python_is_supported "$(command -v "$c")"; then PYTHON="$c"; break; fi
  done
  [[ -n "$PYTHON" ]] || { echo "Python 3.11+ is required per repository documentation." >&2; exit 1; }
  "$PYTHON" -m venv .venv || { echo "Could not create .venv; install Python venv support." >&2; exit 1; }
  VENV_DIR="$ROOT/.venv"
  PY="$VENV_DIR/bin/python"
  echo "Created Python environment: $VENV_DIR"
fi
if [[ ! -e .env ]]; then cp .env.example .env; chmod 600 .env; echo "Created .env from .env.example."; else echo "Retained existing .env."; fi
"$PY" scripts/sync-env-example.py .env.example .env
chmod 600 .env
section 'Preparing local environment values'
if grep -Eq '^SECRET_KEY=(your-secret-key-here)?$' .env; then
 "$PY" -c 'import secrets,pathlib; p=pathlib.Path(".env"); s=p.read_text(); p.write_text("\n".join(("SECRET_KEY="+secrets.token_urlsafe(50)) if x.startswith("SECRET_KEY=") else x for x in s.splitlines())+"\n")'
 chmod 600 .env; echo "Generated local Django SECRET_KEY (not displayed)."
fi

echo 'The service-account token will be saved to .env after database migrations; existing credentials are retained.'

section 'Enabling internal API access'
printf true | "$PY" scripts/set-env-value.py .env VASTO_INTERNAL_API_ENABLED
success 'Enabled the local internal API for Workspaces.'

# Set allowed IPs for internal API
if ! grep -q '^INTERNAL_API_ALLOWED_IPS=' .env; then
  echo "INTERNAL_API_ALLOWED_IPS=127.0.0.1" >> .env
  echo "Set INTERNAL_API_ALLOWED_IPS to localhost."
fi
export PY ROOT DEVELOPER OWNER_USERNAME REDIRECT_URI CREDENTIALS_FILE
section 'Installing backend dependencies'
run_step "pip upgrade" "$PY" -m pip install --upgrade pip || true
"$PY" -m pip install -r requirements.txt || { echo "Dependency installation failed; review the output." >&2; exit 1; }
run_step "Dependency consistency" "$PY" -m pip check || true

KEY_READY=0
if run_step 'Signing key' bash scripts/setup-signing-key.sh; then KEY_READY=1; fi
section 'Starting local services'
if (( ! SKIP )); then
  run_step 'PostgreSQL startup' docker compose up -d db || true
  run_step 'Redis startup' docker compose up -d redis || true
else
  echo 'Skipped Compose startup as requested.'
fi
DB_READY=0
for _ in {1..30}; do
  if "$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'from django.db import connection; connection.ensure_connection()' >/dev/null 2>&1; then DB_READY=1; break; fi
  sleep 1
done
MIGRATED=0
if (( DB_READY )); then
  if run_step 'Django checks' "$PY" manage.py check --settings=pwaninet.settings.local; then
    if run_step 'Database migrations' "$PY" manage.py migrate --noinput --settings=pwaninet.settings.local; then MIGRATED=1; fi
  fi
else
  FAILURES+=('PostgreSQL connection: check DB_* settings; migrations and database-dependent registration were skipped')
fi
if (( MIGRATED )); then
  run_step 'Workspaces service account' "$PY" manage.py create_service_account --settings=pwaninet.settings.local --name=Workspaces --username=workspaces_service --email=workspaces@service.local --env-file "$ROOT/.env" --quiet-token || true
  if (( KEY_READY )); then
    run_step 'Workspaces OAuth registration' bash scripts/setup-workspaces-oauth.sh || true
  else
    FAILURES+=('OAuth registration skipped until signing key is repaired')
  fi
fi
REDIS_OPTIONS=()
if (( SKIP )); then REDIS_OPTIONS+=(--no-start-redis); fi
if (( MIGRATED )); then
  run_step 'Redis and Celery services' "$PY" scripts/dev-services.py start --root "$ROOT" --app pwaninet --settings pwaninet.settings.local "${REDIS_OPTIONS[@]}" || true
else
  run_step 'Redis connectivity' "$PY" scripts/dev-services.py check --root "$ROOT" --app pwaninet --settings pwaninet.settings.local "${REDIS_OPTIONS[@]}" || true
  FAILURES+=('Celery startup skipped until database migrations succeed')
fi
printf '\nStart Pwaninet: DJANGO_SETTINGS_MODULE=pwaninet.settings.local %q -m uvicorn pwaninet.asgi:application --host 127.0.0.1 --port 8000 --reload\n' "$PY"
if ((${#FAILURES[@]})); then exit 1; fi
success 'Pwaninet setup completed.'
