#!/usr/bin/env bash
set -Eeuo pipefail
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
