#!/usr/bin/env bash
set -Eeuo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"; cd "$ROOT"
SKIP=0
DEVELOPER="${VASTO_DEVELOPER:-}"
OWNER_USERNAME="${VASTO_OWNER_USERNAME:-}"
REDIRECT_URI="http://127.0.0.1:8001/api/auth/vasto/callback/"
CREDENTIALS_FILE=""
while (($#)); do
  case "$1" in
    --skip-containers) SKIP=1; shift ;;
    --developer) (($# >= 2)) || { echo "--developer requires an identifier." >&2; exit 2; }; DEVELOPER="$2"; shift 2 ;;
    --owner-username) (($# >= 2)) || { echo "--owner-username requires a Vasto staff username." >&2; exit 2; }; OWNER_USERNAME="$2"; shift 2 ;;
    --redirect-uri) (($# >= 2)) || { echo "--redirect-uri requires a callback URL." >&2; exit 2; }; REDIRECT_URI="$2"; shift 2 ;;
    --credentials-file) (($# >= 2)) || { echo "--credentials-file requires an output path." >&2; exit 2; }; CREDENTIALS_FILE="$2"; shift 2 ;;
    -h|--help) echo "Usage: $0 [--developer ID] [--owner-username USER] [--redirect-uri URL] [--credentials-file PATH] [--skip-containers]"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 2 ;;
  esac
done
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
"$PY" -m pip install --upgrade pip >/dev/null || { echo "pip upgrade failed; check package-index access." >&2; exit 1; }
"$PY" -m pip install -r requirements.txt || { echo "Dependency installation failed; review the output." >&2; exit 1; }
"$PY" -m pip check || { echo "Dependency consistency check failed." >&2; exit 1; }
if [[ ! -e .env ]]; then cp .env.example .env; chmod 600 .env; echo "Created .env from .env.example."; else echo "Retained existing .env."; fi
if grep -Eq '^SECRET_KEY=(your-secret-key-here)?$' .env; then
 "$PY" -c 'import secrets,pathlib; p=pathlib.Path(".env"); s=p.read_text(); p.write_text("\n".join(("SECRET_KEY="+secrets.token_urlsafe(50)) if x.startswith("SECRET_KEY=") else x for x in s.splitlines())+"\n")'
 chmod 600 .env; echo "Generated local Django SECRET_KEY (not displayed)."
fi
KD="$HOME/.config/vasto/keys"; KF="$KD/oidc_rsa_private.pem"; LEGACY_KEY="$ROOT/.secrets/oidc_rsa_private.pem"
mkdir -p "$KD"; chmod 700 "$HOME/.config/vasto" "$KD"
if [[ -e "$KF" ]]; then
  echo "Retaining existing local signing key."
elif [[ -f "$LEGACY_KEY" ]]; then
  cp -n "$LEGACY_KEY" "$KF"
  [[ -e "$KF" ]] || { echo "Could not preserve the existing legacy signing key." >&2; exit 1; }
  chmod 600 "$KF"
  echo "Preserved the existing repository-local key in the per-user config directory."
else
  TEMP_KEY="$(mktemp "$KD/.oidc-rsa.XXXXXX")"
  if ! openssl genrsa -traditional -out "$TEMP_KEY" 4096 >/dev/null 2>&1; then
    rm -f "$TEMP_KEY"; echo "OpenSSL RSA key generation failed." >&2; exit 1
  fi
  chmod 600 "$TEMP_KEY"
  if ln "$TEMP_KEY" "$KF" 2>/dev/null; then
    echo "Created local RSA signing key at $KF."
  elif [[ -e "$KF" ]]; then
    echo "Another local signing key appeared; retaining it without replacement."
  else
    rm -f "$TEMP_KEY"; echo "Could not securely publish the RSA key file." >&2; exit 1
  fi
  rm -f "$TEMP_KEY"
fi
chmod 600 "$KF"
"$PY" - "$KF" <<'PY'
import sys
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
try: key=serialization.load_pem_private_key(open(sys.argv[1],"rb").read(),password=None)
except Exception as exc: raise SystemExit(f"Local RSA key is invalid ({type(exc).__name__}); preserve it and repair deliberately.")
if not isinstance(key,rsa.RSAPrivateKey) or key.key_size<4096: raise SystemExit("OIDC key must be RSA and at least 4096 bits.")
print("Signing key parsed as RSA (4096 bits or larger).")
PY
# Validate the setting Django will actually load, including environment overrides.
"$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'from django.conf import settings; from cryptography.hazmat.primitives import serialization; from cryptography.hazmat.primitives.asymmetric import rsa; p=settings.OAUTH2_PROVIDER.get("OIDC_RSA_PRIVATE_KEY"); k=serialization.load_pem_private_key(p.encode() if isinstance(p,str) else p,password=None); assert isinstance(k,rsa.RSAPrivateKey) and k.key_size>=4096; print("Django loaded a valid local RSA signing key.")' || { echo "Django did not load a valid 4096-bit RSA signing key. Check OIDC_RSA_PRIVATE_KEY and the local key file." >&2; exit 1; }
if (( ! SKIP )); then
 command -v docker >/dev/null || { echo "Docker is needed for PostgreSQL and Redis; install it or use --skip-containers for separately managed services." >&2; exit 1; }
 docker compose version >/dev/null 2>&1 || { echo "Docker Compose v2 is needed; install the plugin or use --skip-containers." >&2; exit 1; }
 docker compose up -d db redis || { echo "Could not start Compose services db and redis; check Docker and docker-compose.yml." >&2; exit 1; }
else echo "Skipped Compose startup as requested."; fi
ready=0
for _ in {1..30}; do if "$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'from django.db import connection; connection.ensure_connection()' >/dev/null 2>&1; then ready=1; break; fi; sleep 1; done
(( ready )) || { echo "PostgreSQL is unreachable with DB_* settings; no migrations were run. Check .env or start your managed database." >&2; exit 1; }
"$PY" manage.py check --settings=pwaninet.settings.local || { echo "Django system check failed." >&2; exit 1; }
"$PY" manage.py migrate --settings=pwaninet.settings.local || { echo "Migrations failed; database was not reset." >&2; exit 1; }

if [[ -z "$OWNER_USERNAME" ]]; then
  STAFF_USERS="$("$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'from django.contrib.auth import get_user_model; print("\n".join(get_user_model().objects.filter(is_active=True,is_staff=True).values_list("username",flat=True)))')"
  if [[ -n "$STAFF_USERS" ]]; then
    printf 'Active Vasto staff accounts:\n%s\n' "$STAFF_USERS"
  else
    echo "No active Vasto staff account exists yet."
  fi
  read -r -p "Owner username (leave blank to create a new superuser): " OWNER_USERNAME
  if [[ -z "$OWNER_USERNAME" ]]; then
    read -r -p "New Vasto superuser username: " OWNER_USERNAME
    [[ -n "$OWNER_USERNAME" ]] || { echo "A username is required to create the Vasto superuser." >&2; exit 2; }
    "$PY" manage.py createsuperuser --username "$OWNER_USERNAME" --settings=pwaninet.settings.local || { echo "Superuser creation failed; rerun setup and select an active staff account." >&2; exit 1; }
  fi
fi
if [[ -z "$CREDENTIALS_FILE" ]]; then
  CREDENTIALS_FILE="$HOME/.config/vasto-workspaces/$DEVELOPER-local.env"
fi
APP_NAME="Vasto Workspaces Local - $DEVELOPER"
APP_EXISTS="$(APP_NAME="$APP_NAME" "$PY" manage.py shell --no-imports --settings=pwaninet.settings.local -c 'import os; from oauth2_provider.models import Application; print(int(Application.objects.filter(name=os.environ["APP_NAME"]).exists()))')"
if [[ "$APP_EXISTS" == "1" ]]; then
  APP_NAME="$APP_NAME" OWNER_USERNAME="$OWNER_USERNAME" REDIRECT_URI="$REDIRECT_URI" CREDENTIALS_FILE="$CREDENTIALS_FILE" "$PY" manage.py shell --no-imports --settings=pwaninet.settings.local <<'APP_CHECK_PY'
import os
from django.contrib.auth import get_user_model
from oauth2_provider.models import Application
app = Application.objects.get(name=os.environ["APP_NAME"])
owner = get_user_model().objects.get(username=os.environ["OWNER_USERNAME"])
if app.user_id != owner.pk:
    raise SystemExit("Existing OAuth client belongs to a different selected owner; no changes were made.")
if app.redirect_uris != os.environ["REDIRECT_URI"]:
    raise SystemExit("Existing OAuth client has a different callback URI; no changes were made.")
if not os.path.isfile(os.path.expanduser(os.environ["CREDENTIALS_FILE"])):
    raise SystemExit("Existing OAuth client credential file is missing; its hashed secret cannot be recovered. No changes were made.")
print("Existing local Workspaces OAuth registration and credential file match; retaining them.")
APP_CHECK_PY
elif [[ "$APP_EXISTS" == "0" ]]; then
  "$PY" manage.py register_workspaces_oauth --settings=pwaninet.settings.local --developer "$DEVELOPER" --owner-username "$OWNER_USERNAME" --redirect-uri "$REDIRECT_URI" --credentials-file "$CREDENTIALS_FILE" || { echo "OAuth registration failed; resolve the message above and rerun setup." >&2; exit 1; }
else
  echo "Could not determine whether the local Workspaces OAuth client is already registered; refusing to create a duplicate." >&2
  exit 1
fi
echo "Vasto environment, key checks, migrations, admin selection, and Workspaces client registration are complete."
echo "Credential file: $CREDENTIALS_FILE"
printf 'Start Vasto for login testing: %q %q runserver 127.0.0.1:8000 --settings=pwaninet.settings.local\n' "$PY" "$ROOT/manage.py"
