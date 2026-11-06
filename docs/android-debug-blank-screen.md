# Android local debug blank screen — 6 November 2026

Confirmed on the connected TECNO BD4j using ADB logcat and the WebView debugger.

The APK built and installed successfully. The USB reverse tunnel was active and Django returned its normal login redirect. The native URL handler treated every HTTP URL as external, so the redirect to `http://localhost:8000/login/` launched Chrome and left Vasto's WebView empty. Logcat recorded the external VIEW intent from Vasto, followed by a Capacitor `triggerEvent` error on the empty document.

Changes:
- Permit the exact configured development-server origin inside debuggable builds only.
- Let Chromium fetch that origin, preserving cookies and service-worker behavior; initialize the Capacitor bridge at document start on that exact origin.
- Show an actionable development-server connection page if initial navigation fails.
- Restore temporary build files using absolute project paths, including the generated Cordova manifest.
- Install updates without uninstalling the app or clearing its saved data.
- Restore production configuration from the original pre-debug backup left by the failed script.

Validation:
- `assembleDebug --offline`: successful, 290 actionable tasks.
- Installed the corrected APK over the existing app and launched it.
- WebView debugger: `http://localhost:8000/login/?next=%2F`, title `vasto`; local service worker registered.
- Screenshot: `verification/android-debug-login-fixed.png`.
- Two script regression tests passed, covering restoration after success and after a failed build from the Android directory.

This check verifies startup and the displayed login screen. Authenticated offline history requires signing in and visiting pages before disconnecting.
