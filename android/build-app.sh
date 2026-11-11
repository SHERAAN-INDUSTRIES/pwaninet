#!/bin/bash
# Build a debug APK that loads the LOCAL Django dev server via the
# Cloudflare Tunnel at https://dev.pwaninet.app.
#
# The tunnel (cloudflared-pwaninet-dev.service) forwards dev.pwaninet.app to
# this machine's Django on port 8000, so the APK exercises local code over real
# HTTPS — no adb reverse needed, works on any network.
#
# Because the origin is a real https:// hostname, the service worker registers
# and offline page caching works exactly like the production origin.
#
# Offline test: toggle WiFi off and reopen the app — the service worker serves
# the saved pages.
#
# All config changes are temporary: capacitor.config.json and the generated
# android assets config are restored on exit.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ADB="${ADB:-$HOME/Android/Sdk/platform-tools/adb}"
if [ ! -x "$ADB" ]; then
    ADB="$(command -v adb || true)"
fi
if [ -z "$ADB" ] || [ ! -x "$ADB" ]; then
    echo "adb not found. Set ADB=/path/to/adb or install platform-tools." >&2
    exit 1
fi

ACCESS_URL="https://dev.pwaninet.app"

cat <<EOF
=== Vasto debug build (dev tunnel) ===
Device will load: $ACCESS_URL
Make sure the dev server is running:  ./serve1.sh  (uvicorn ASGI on 127.0.0.1:8000)
Make sure the tunnel is up:          systemctl --user status cloudflared-pwaninet-dev
EOF
read -r -p "Press Enter to build, or Ctrl+C to abort... " _

# --- Backups (restored on exit, success or failure) ---
BACKUP_DIR="$(mktemp -d)"
cp capacitor.config.json "$BACKUP_DIR/capacitor.config.json"
if [ -f android/app/src/main/assets/capacitor.config.json ]; then
    cp android/app/src/main/assets/capacitor.config.json "$BACKUP_DIR/assets-capacitor.config.json"
fi

restore() {
    cp "$BACKUP_DIR/capacitor.config.json" "$ROOT/capacitor.config.json"
    if [ -f "$BACKUP_DIR/assets-capacitor.config.json" ]; then
        cp "$BACKUP_DIR/assets-capacitor.config.json" "$ROOT/android/app/src/main/assets/capacitor.config.json"
    fi
    rm -rf "$BACKUP_DIR"
}
trap restore EXIT

# --- Point Capacitor at the dev tunnel ---
# server.url loads https://dev.pwaninet.app (Cloudflare Tunnel -> localhost:8000).
# HTTPS means no cleartext flag and no localhost asset-proxy collision.
python3 - <<'PY'
import json

path = 'capacitor.config.json'
with open(path) as f:
    cfg = json.load(f)

server = cfg.setdefault('server', {})
server['url'] = 'https://dev.pwaninet.app'
server['cleartext'] = False
server.pop('hostname', None)

with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
PY

npx cap sync android

# --- Build ---
cd android
unset ANDROID_PREFS_ROOT
VERSION_CODE=100 VERSION_NAME=1.4.1 JAVA_HOME=/opt/android-studio/jbr \
    ./gradlew assembleDebug --no-daemon

# --- Install + launch (no adb reverse needed — traffic goes via the tunnel) ---
"$ADB" install -r app/build/outputs/apk/debug/app-debug.apk
"$ADB" shell am start -n com.vasto.app/.MainActivity

cat <<'EOF'

Done. The app now loads https://dev.pwaninet.app (this machine via Cloudflare Tunnel).

Prerequisites on this machine:
  - Django dev server:  ./serve1.sh
  - Tunnel:             systemctl --user status cloudflared-pwaninet-dev

Offline test:
  1. Browse pages online so the service worker saves them.
  2. Toggle WiFi off (or stop Django + the tunnel), reopen the app —
     saved pages should render.
EOF
