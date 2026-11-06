#!/bin/bash
# Build a debug APK that loads the LOCAL Django dev server over adb reverse.
#
# The app opens http://localhost:8000 inside the WebView. `adb reverse`
# tunnels the phone's localhost:8000 to this machine's Django on port 8000,
# so the APK exercises local code (including the offline-pages service
# worker) instead of production https://pwaninet.app.
#
# localhost is a secure context, so the service worker registers and
# offline page caching works exactly like the HTTPS production origin.
#
# Offline test: `adb reverse --remove_all` (or stop Django), then reopen
# the app — the service worker serves the saved pages.
#
# All config changes are temporary: capacitor.config.json, the generated
# android assets config, and AndroidManifest.xml are restored on exit.

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

ACCESS_URL="http://localhost:8000"

echo "=== Vasto debug build (local server) ==="
echo "Device will load: $ACCESS_URL"
echo "Make sure the dev server is running:  ./serve1.sh  (uvicorn ASGI on 127.0.0.1:8000)"
echo
read -r -p "Press Enter to build, or Ctrl+C to abort... " _

# --- Backups (restored on exit, success or failure) ---
BACKUP_DIR="$(mktemp -d)"
cp capacitor.config.json "$BACKUP_DIR/capacitor.config.json"
cp android/app/src/main/AndroidManifest.xml "$BACKUP_DIR/AndroidManifest.xml"
if [ -f android/app/src/main/assets/capacitor.config.json ]; then
    cp android/app/src/main/assets/capacitor.config.json "$BACKUP_DIR/assets-capacitor.config.json"
fi
CORDOVA_MANIFEST="$ROOT/android/capacitor-cordova-android-plugins/src/main/AndroidManifest.xml"
if [ -f "$CORDOVA_MANIFEST" ]; then
    cp "$CORDOVA_MANIFEST" "$BACKUP_DIR/cordova-AndroidManifest.xml"
fi

restore() {
    cp "$BACKUP_DIR/capacitor.config.json" "$ROOT/capacitor.config.json"
    cp "$BACKUP_DIR/AndroidManifest.xml" "$ROOT/android/app/src/main/AndroidManifest.xml"
    if [ -f "$BACKUP_DIR/assets-capacitor.config.json" ]; then
        cp "$BACKUP_DIR/assets-capacitor.config.json" "$ROOT/android/app/src/main/assets/capacitor.config.json"
    fi
    if [ -f "$BACKUP_DIR/cordova-AndroidManifest.xml" ]; then
        cp "$BACKUP_DIR/cordova-AndroidManifest.xml" "$CORDOVA_MANIFEST"
    fi
    rm -rf "$BACKUP_DIR"
}
trap restore EXIT

# --- Point Capacitor at the local dev server ---
# server.hostname avoids collision with Capacitor's built-in
# http(s)://localhost asset proxy; server.url is the live-reload URL.
python3 - <<'PY'
import json

path = 'capacitor.config.json'
with open(path) as f:
    cfg = json.load(f)

server = cfg.setdefault('server', {})
server['url'] = 'http://localhost:8000'
server['cleartext'] = True
server['hostname'] = 'vasto-debug'

with open(path, 'w') as f:
    json.dump(cfg, f, indent=2)
    f.write('\n')
PY

npx cap sync android

# --- Allow cleartext HTTP for this debug build only ---
sed -i 's/android:usesCleartextTraffic="false"/android:usesCleartextTraffic="true"/' \
    android/app/src/main/AndroidManifest.xml

# --- Build ---
cd android
unset ANDROID_PREFS_ROOT
VERSION_CODE=100 VERSION_NAME=1.4.1 JAVA_HOME=/opt/android-studio/jbr \
    ./gradlew assembleDebug --no-daemon

# --- Tunnel + install + launch ---
"$ADB" reverse tcp:8000 tcp:8000
"$ADB" install -r app/build/outputs/apk/debug/app-debug.apk
"$ADB" shell am start -n com.vasto.app/.MainActivity

cat <<'EOF'

Done. The app now loads http://localhost:8000 (this machine) over adb reverse.

Offline test:
  1. Browse pages online so the service worker saves them.
  2. adb reverse --remove tcp:8000   # cut this tunnel (or stop Django)
  3. Toggle WiFi off, reopen the app — saved pages should render.
  4. adb reverse tcp:8000 tcp:8000   # restore tunnel when done.
EOF
