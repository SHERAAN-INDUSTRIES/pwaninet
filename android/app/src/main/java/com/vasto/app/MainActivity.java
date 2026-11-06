package com.vasto.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.DownloadManager;
import android.Manifest;
import android.content.Context;
import android.content.ContentValues;
import android.content.Intent;
import android.content.pm.PackageManager;
import android.media.MediaScannerConnection;
import android.content.pm.PackageInfo;
import android.content.res.Configuration;
import android.graphics.Color;
import android.net.ConnectivityManager;
import android.net.Network;
import android.net.NetworkCapabilities;
import android.net.NetworkRequest;
import android.net.Uri;
import android.os.Environment;
import android.os.Build;
import android.os.Bundle;
import android.speech.RecognitionListener;
import android.speech.RecognizerIntent;
import android.speech.SpeechRecognizer;
import android.util.Log;
import android.view.View;
import android.view.Window;
import android.webkit.JavascriptInterface;
import android.webkit.WebResourceError;
import android.webkit.WebResourceRequest;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;
import android.webkit.DownloadListener;
import android.webkit.CookieManager;
import android.webkit.URLUtil;

import androidx.activity.EdgeToEdge;
import androidx.activity.SystemBarStyle;
import androidx.core.graphics.Insets;
import androidx.core.content.ContextCompat;
import androidx.core.splashscreen.SplashScreen;
import androidx.core.view.ViewCompat;
import androidx.core.view.WindowCompat;
import androidx.core.view.WindowInsetsCompat;
import androidx.core.view.WindowInsetsControllerCompat;

import ee.forgr.capacitor.social.login.ModifiedMainActivityForSocialLoginPlugin;
import com.getcapacitor.BridgeActivity;
import com.getcapacitor.BridgeWebViewClient;
import com.getcapacitor.WebViewListener;
import com.getcapacitor.JSExport;
import com.getcapacitor.PluginHandle;
import androidx.webkit.WebViewCompat;
import androidx.webkit.WebViewFeature;
import java.util.Collections;

import android.os.Handler;
import android.os.Looper;
import android.os.VibrationEffect;
import android.os.Vibrator;
import android.os.VibratorManager;
import android.view.HapticFeedbackConstants;
import android.widget.Toast;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.io.File;
import java.io.FileOutputStream;
import java.net.URLConnection;
import java.util.Locale;
import java.util.ArrayList;
import org.json.JSONObject;

public class MainActivity extends BridgeActivity implements ModifiedMainActivityForSocialLoginPlugin {
    private static final String GALLERY_BRIDGE_TAG = "GalleryBridge";
    private boolean isNetworkAvailable = true;
    private boolean isOfflinePageShowing = false;
    private static final String WEBVIEW_STATE_KEY = "WEBVIEW_STATE";
    private volatile boolean isPageReady = false;
    private static final long SPLASH_WATCHDOG_TIMEOUT_MS = 8000L;

    private int lastSafeTop = 0;
    private int lastSafeBottom = 0;
    private int lastSafeLeft = 0;
    private int lastSafeRight = 0;
    private boolean lastImeVisible = false;
    private int lastImeHeight = 0;
    private String pendingDeepLinkPath = null;
    private static final int REQUEST_SPEECH_AUDIO_PERMISSION = 4301;
    private static final int REQUEST_MEDIA_DOWNLOAD_PERMISSION = 4302;
    private PendingMediaDownload pendingMediaDownload;
    private WebAppInterface androidWebAppInterface;
    private WebView androidBridgeWebView;

    /**
     * True while MainActivity is resumed. Read by PwaninetFirebaseMessagingService
     * to skip posting the system notification while the app is open — the WebView
     * already renders the thin in-app banner for foreground pushes. Prevents the
     * duplicate thick system heads-up banner appearing on top of the in-app card.
     */
    public static volatile boolean isAppForeground = false;

    private static final class PendingMediaDownload {
        final String url, filename, mimeType, category;
        PendingMediaDownload(String url, String filename, String mimeType, String category) {
            this.url = url; this.filename = filename; this.mimeType = mimeType; this.category = category;
        }
    }
    private SpeechRecognizer speechRecognizer;
    private boolean speechListening = false;
    private boolean speechSessionRequested = false;
    private String pendingSpeechLanguage = "en-US";

    public static class WebAppInterface {
        private final java.lang.ref.WeakReference<MainActivity> activityRef;

        public WebAppInterface(MainActivity activity) {
            this.activityRef = new java.lang.ref.WeakReference<>(activity);
        }

        @JavascriptInterface
        public void setSystemBarTheme(String theme) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                boolean isLight = !"dark".equalsIgnoreCase(theme);
                activity.applySystemBarTheme(isLight);
            }
        }

        @JavascriptInterface
        public void notifyPageReady() {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.runOnUiThread(() -> activity.isPageReady = true);
            }
        }

        @JavascriptInterface
        public void setNavigationBarTheme(String theme) {
            setSystemBarTheme(theme);
        }

        @JavascriptInterface
        public void openExternalUrl(String url) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.openExternalUrl(url);
            }
        }

        @JavascriptInterface
        public boolean saveMediaToGallery(String sourceUri, String filename, String mimeType, String category) {
            Log.i(GALLERY_BRIDGE_TAG, "Gallery save requested (" + category + ")");
            MainActivity activity = activityRef.get();
            if (activity == null || sourceUri == null || filename == null) return false;
            boolean isVideo = "video".equalsIgnoreCase(category);
            if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q) {
                if (ContextCompat.checkSelfPermission(activity, Manifest.permission.WRITE_EXTERNAL_STORAGE)
                        != PackageManager.PERMISSION_GRANTED) {
                    activity.runOnUiThread(() -> activity.requestPermissions(
                        new String[] { Manifest.permission.WRITE_EXTERNAL_STORAGE }, REQUEST_MEDIA_DOWNLOAD_PERMISSION));
                    return false;
                }
                File targetFile = null;
                try {
                    File publicMediaDir = Environment.getExternalStoragePublicDirectory(
                        isVideo ? Environment.DIRECTORY_MOVIES : Environment.DIRECTORY_PICTURES);
                    File appMediaDir = new File(publicMediaDir, "Vasto");
                    if (!appMediaDir.exists() && !appMediaDir.mkdirs()) {
                        throw new IOException("Could not create public media directory");
                    }
                    targetFile = new File(appMediaDir, filename.replaceAll("[\\\\/:*?\"<>|]", "_"));
                    try (InputStream input = activity.getContentResolver().openInputStream(Uri.parse(sourceUri));
                         OutputStream output = new FileOutputStream(targetFile)) {
                        if (input == null) throw new IOException("Unable to open offline media file");
                        byte[] buffer = new byte[8192];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    }
                    MediaScannerConnection.scanFile(activity, new String[] { targetFile.getAbsolutePath() },
                        new String[] { mimeType }, null);
                    Log.i(GALLERY_BRIDGE_TAG, "Gallery save completed");
                    return true;
                } catch (Exception error) {
                    if (targetFile != null) targetFile.delete();
                    Log.e(GALLERY_BRIDGE_TAG, "Legacy gallery publish failed", error);
                    return false;
                }
            }
            Uri collection = isVideo
                ? android.provider.MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                : android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
            ContentValues values = new ContentValues();
            values.put(android.provider.MediaStore.MediaColumns.DISPLAY_NAME, filename);
            values.put(android.provider.MediaStore.MediaColumns.MIME_TYPE, mimeType);
            values.put(android.provider.MediaStore.MediaColumns.RELATIVE_PATH,
                isVideo ? "Movies/Vasto" : "Pictures/Vasto");
            values.put(android.provider.MediaStore.MediaColumns.IS_PENDING, 1);
            Uri target = null;
            try {
                target = activity.getContentResolver().insert(collection, values);
                if (target == null) throw new IOException("MediaStore did not create a gallery entry");
                try (InputStream input = activity.getContentResolver().openInputStream(Uri.parse(sourceUri));
                     OutputStream output = activity.getContentResolver().openOutputStream(target)) {
                    if (input == null || output == null) throw new IOException("Unable to open media stream");
                    byte[] buffer = new byte[8192];
                    int count;
                    while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                }
                ContentValues ready = new ContentValues();
                ready.put(android.provider.MediaStore.MediaColumns.IS_PENDING, 0);
                if (activity.getContentResolver().update(target, ready, null, null) == 0) {
                    throw new IOException("MediaStore could not publish the gallery entry");
                }
                Log.i(GALLERY_BRIDGE_TAG, "Gallery save completed");
                return true;
            } catch (Exception error) {
                if (target != null) activity.getContentResolver().delete(target, null, null);
                Log.e(GALLERY_BRIDGE_TAG, "Gallery publish failed", error);
                return false;
            }
        }

        @JavascriptInterface
        public String startMediaDownload(String url, String filename, String mimeType, String category) {
            MainActivity activity = activityRef.get();
            return activity == null ? "failed" : activity.startMediaDownload(url, filename, mimeType, category);
        }

        @JavascriptInterface
        public void hapticImpact(String style) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.performNativeHapticImpact(style);
            }
        }

        @JavascriptInterface
        public void hapticSelection() {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.performNativeHapticSelection();
            }
        }

        @JavascriptInterface
        public void hapticNotification(String type) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.performNativeHapticNotification(type);
            }
        }

        @JavascriptInterface
        public void vibrate(long durationMs) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.performNativeVibrate(durationMs);
            }
        }

        @JavascriptInterface
        public void showToast(String message) {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                activity.showToast(message);
            }
        }

        @JavascriptInterface
        public void startSpeechRecognition(String language) {
            MainActivity activity = activityRef.get();
            if (activity != null) activity.requestSpeechRecognition(language);
        }

        @JavascriptInterface
        public void stopSpeechRecognition() {
            MainActivity activity = activityRef.get();
            if (activity != null) activity.stopSpeechRecognition();
        }

        @JavascriptInterface
        public void cancelSpeechRecognition() {
            MainActivity activity = activityRef.get();
            if (activity != null) activity.cancelSpeechRecognition();
        }

        @JavascriptInterface
        public String getAppVersionInfo() {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                try {
                    PackageInfo pInfo = activity.getPackageManager().getPackageInfo(activity.getPackageName(), 0);
                    String versionName = pInfo.versionName != null ? pInfo.versionName : "1.0.0";
                    long versionCode = 1;
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                        versionCode = pInfo.getLongVersionCode();
                    } else {
                        versionCode = pInfo.versionCode;
                    }
                    return "{\"versionName\":\"" + versionName + "\",\"versionCode\":" + versionCode + "}";
                } catch (Exception e) {
                    return "{\"versionName\":\"1.0.0\",\"versionCode\":1}";
                }
            }
            return "{}";
        }

        @JavascriptInterface
        public boolean isPushNotificationsAvailable() {
            MainActivity activity = activityRef.get();
            if (activity != null) {
                return MainActivity.isFirebaseInitialized(activity);
            }
            return false;
        }
    }

    public static boolean isFirebaseInitialized(Context context) {
        try {
            Class<?> clazz = Class.forName("com.google.firebase.FirebaseApp");
            java.lang.reflect.Method getAppsMethod = clazz.getMethod("getApps", Context.class);
            java.util.List<?> apps = (java.util.List<?>) getAppsMethod.invoke(null, context);
            return apps != null && !apps.isEmpty();
        } catch (Throwable ignored) {
            return false;
        }
    }

    public void applySystemBarTheme(boolean isLight) {
        runOnUiThread(() -> {
            try {
                Window window = getWindow();
                if (window == null) return;

                View decorView = window.getDecorView();
                WindowInsetsControllerCompat controller = WindowCompat.getInsetsController(window, decorView);
                if (controller != null) {
                    controller.setAppearanceLightStatusBars(isLight);
                    controller.setAppearanceLightNavigationBars(isLight);
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
    }

    private void setupAndroidBridge() {
        if (getBridge() == null || getBridge().getWebView() == null) return;

        WebView webView = getBridge().getWebView();
        if (webView == androidBridgeWebView) return;

        // Keep one Java object for the lifetime of this WebView. Replacing the
        // object from onPageLoaded/onPageCommitVisible invalidates Chromium's
        // JavaScript wrappers and produces "WebView: Unknown object" errors.
        if (androidWebAppInterface == null) {
            androidWebAppInterface = new WebAppInterface(this);
        }
        webView.addJavascriptInterface(androidWebAppInterface, "AndroidBridge");
        webView.addJavascriptInterface(androidWebAppInterface, "PwaninetBridge");
        androidBridgeWebView = webView;
        Log.i("MainActivity", "Android JavaScript bridge registered on WebView");
    }

    private void requestSpeechRecognition(String language) {
        runOnUiThread(() -> {
            speechSessionRequested = true;
            pendingSpeechLanguage = language == null || language.trim().isEmpty() ? "en-US" : language.trim();
            if (!SpeechRecognizer.isRecognitionAvailable(this)) {
                speechSessionRequested = false;
                dispatchSpeechEvent("", false, "unavailable");
                return;
            }
            if (ContextCompat.checkSelfPermission(this, Manifest.permission.RECORD_AUDIO)
                    != PackageManager.PERMISSION_GRANTED) {
                requestPermissions(new String[] { Manifest.permission.RECORD_AUDIO }, REQUEST_SPEECH_AUDIO_PERMISSION);
                return;
            }
            startSpeechRecognition();
        });
    }

    private void startSpeechRecognition() {
        runOnUiThread(() -> {
            if (!SpeechRecognizer.isRecognitionAvailable(this)) {
                speechSessionRequested = false;
                dispatchSpeechEvent("", false, "unavailable");
                return;
            }
            destroySpeechRecognizer();
            speechRecognizer = SpeechRecognizer.createSpeechRecognizer(this);
            speechRecognizer.setRecognitionListener(new RecognitionListener() {
                @Override public void onReadyForSpeech(Bundle params) { speechListening = true; }
                @Override public void onBeginningOfSpeech() { speechListening = true; }
                @Override public void onRmsChanged(float rmsdB) {
                    float level = Math.max(0f, Math.min(1f, (rmsdB + 2f) / 15f));
                    dispatchSpeechEvent("", false, null, level);
                }
                @Override public void onBufferReceived(byte[] buffer) {}
                @Override public void onEndOfSpeech() { speechListening = false; }
                @Override public void onEvent(int eventType, Bundle params) {}

                @Override
                public void onPartialResults(Bundle partialResults) {
                    ArrayList<String> matches = partialResults == null ? null
                            : partialResults.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    if (matches != null && !matches.isEmpty()) dispatchSpeechEvent(matches.get(0), false, null);
                }

                @Override
                public void onResults(Bundle results) {
                    speechListening = false;
                    ArrayList<String> matches = results == null ? null
                            : results.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION);
                    // Always send a terminal event. An empty result is still completion and
                    // the WebView must not remain stuck in its transcribing state.
                    dispatchSpeechEvent(matches != null && !matches.isEmpty() ? matches.get(0) : "", true, null);
                    destroySpeechRecognizer();
                    if (speechSessionRequested) {
                        new Handler(Looper.getMainLooper()).postDelayed(() -> {
                            if (speechSessionRequested) startSpeechRecognition();
                        }, 250);
                    }
                }

                @Override
                public void onError(int error) {
                    speechListening = false;
                    boolean recoverable = error == SpeechRecognizer.ERROR_NO_MATCH
                            || error == SpeechRecognizer.ERROR_SPEECH_TIMEOUT
                            || error == SpeechRecognizer.ERROR_RECOGNIZER_BUSY
                            || error == SpeechRecognizer.ERROR_NETWORK
                            || error == SpeechRecognizer.ERROR_NETWORK_TIMEOUT;
                    boolean retry = speechSessionRequested && recoverable;
                    if (!retry) speechSessionRequested = false;
                    dispatchSpeechEvent("", false, speechErrorName(error));
                    destroySpeechRecognizer();
                    if (retry) {
                        new Handler(Looper.getMainLooper()).postDelayed(() -> {
                            if (speechSessionRequested) startSpeechRecognition();
                        }, 400);
                    }
                }
            });

            Intent intent = new Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM);
            intent.putExtra(RecognizerIntent.EXTRA_LANGUAGE, pendingSpeechLanguage);
            intent.putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, true);
            intent.putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 1);
            // Keep ordinary pauses inside the same recognition session. Restarting after every
            // short silence can replay the Android listening chime and interrupts dictation.
            intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_POSSIBLY_COMPLETE_SILENCE_LENGTH_MILLIS, 8000L);
            intent.putExtra(RecognizerIntent.EXTRA_SPEECH_INPUT_COMPLETE_SILENCE_LENGTH_MILLIS, 10000L);
            try {
                speechRecognizer.startListening(intent);
                speechListening = true;
            } catch (Exception error) {
                speechListening = false;
                speechSessionRequested = false;
                dispatchSpeechEvent("", false, "start-failed");
                destroySpeechRecognizer();
            }
        });
    }

    private void stopSpeechRecognition() {
        runOnUiThread(() -> {
            speechSessionRequested = false;
            // onResults destroys the recognizer before the continuous-listening restart is
            // scheduled. A stop during that gap must complete the JS session itself.
            if (speechRecognizer == null) {
                dispatchSpeechEvent("", true, null);
                return;
            }
            try {
                speechRecognizer.stopListening();
            } catch (Exception ignored) {
                destroySpeechRecognizer();
                dispatchSpeechEvent("", true, null);
            }
        });
    }

    private void cancelSpeechRecognition() {
        runOnUiThread(() -> {
            speechSessionRequested = false;
            destroySpeechRecognizer();
        });
    }

    private void destroySpeechRecognizer() {
        if (speechRecognizer != null) {
            try { speechRecognizer.setRecognitionListener(null); } catch (Exception ignored) {}
            try { speechRecognizer.cancel(); } catch (Exception ignored) {}
            try { speechRecognizer.destroy(); } catch (Exception ignored) {}
            speechRecognizer = null;
        }
        speechListening = false;
    }

    private void dispatchSpeechEvent(String text, boolean isFinal, String error) {
        dispatchSpeechEvent(text, isFinal, error, null);
    }

    private void dispatchSpeechEvent(String text, boolean isFinal, String error, Float level) {
        JSONObject detail = new JSONObject();
        try {
            detail.put("text", text == null ? "" : text);
            detail.put("final", isFinal);
            detail.put("listening", speechSessionRequested);
            if (level != null) detail.put("level", level);
            if (error != null) detail.put("error", error);
        } catch (Exception ignored) {}
        String js = "window.dispatchEvent(new CustomEvent('vasto:native-speech', {detail:" + detail + "}));";
        runOnUiThread(() -> {
            if (getBridge() != null && getBridge().getWebView() != null) {
                getBridge().getWebView().evaluateJavascript(js, null);
            }
        });
    }

    private String speechErrorName(int error) {
        switch (error) {
            case SpeechRecognizer.ERROR_AUDIO: return "audio";
            case SpeechRecognizer.ERROR_CLIENT: return "client";
            case SpeechRecognizer.ERROR_INSUFFICIENT_PERMISSIONS: return "permission";
            case SpeechRecognizer.ERROR_NETWORK:
            case SpeechRecognizer.ERROR_NETWORK_TIMEOUT: return "network";
            case SpeechRecognizer.ERROR_NO_MATCH:
            case SpeechRecognizer.ERROR_SPEECH_TIMEOUT: return "no-speech";
            case SpeechRecognizer.ERROR_RECOGNIZER_BUSY: return "busy";
            case SpeechRecognizer.ERROR_SERVER: return "server";
            default: return "unknown";
        }
    }

    @Override
    public void onRequestPermissionsResult(int requestCode, String[] permissions, int[] grantResults) {
        super.onRequestPermissionsResult(requestCode, permissions, grantResults);
        if (requestCode == REQUEST_MEDIA_DOWNLOAD_PERMISSION) {
            PendingMediaDownload pending = pendingMediaDownload;
            pendingMediaDownload = null;
            if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
                if (pending != null) startMediaDownload(pending.url, pending.filename, pending.mimeType, pending.category);
            } else {
                if (pending != null) {
                    Toast.makeText(this, "Storage permission is needed to save media on this Android version.", Toast.LENGTH_LONG).show();
                }
            }
            return;
        }
        if (requestCode != REQUEST_SPEECH_AUDIO_PERMISSION) return;
        if (!speechSessionRequested) return;
        if (grantResults.length > 0 && grantResults[0] == PackageManager.PERMISSION_GRANTED) {
            startSpeechRecognition();
        } else {
            speechSessionRequested = false;
            dispatchSpeechEvent("", false, "permission");
        }
    }

    @Override
    public void onDestroy() {
        destroySpeechRecognizer();
        super.onDestroy();
    }

    private void setupNotificationChannels() {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                NotificationManager notificationManager = getSystemService(NotificationManager.class);
                if (notificationManager != null) {
                    NotificationChannel defaultChannel = new NotificationChannel(
                        "vasto_notifications",
                        "Vasto Notifications",
                        NotificationManager.IMPORTANCE_HIGH
                    );
                    defaultChannel.setDescription("All social updates, mentions, posts, documents, and messages");
                    defaultChannel.enableVibration(true);
                    defaultChannel.enableLights(true);
                    defaultChannel.setLightColor(Color.parseColor("#2563eb"));
                    notificationManager.createNotificationChannel(defaultChannel);

                    NotificationChannel socialChannel = new NotificationChannel(
                        "vasto_social",
                        "Social Updates",
                        NotificationManager.IMPORTANCE_HIGH
                    );
                    socialChannel.setDescription("Likes, comments, shares, follows, and mentions");
                    socialChannel.enableVibration(true);
                    notificationManager.createNotificationChannel(socialChannel);

                    NotificationChannel messagesChannel = new NotificationChannel(
                        "vasto_messages",
                        "Direct & Group Messages",
                        NotificationManager.IMPORTANCE_HIGH
                    );
                    messagesChannel.setDescription("Chat messages and conversation updates");
                    messagesChannel.enableVibration(true);
                    notificationManager.createNotificationChannel(messagesChannel);
                }
            } catch (Exception e) {
                e.printStackTrace();
            }
        }
    }

    public void injectThemeObserver() {
        runOnUiThread(() -> {
            if (getBridge() != null && getBridge().getWebView() != null) {
                String js =
                    "(function() {" +
                    "  function syncTheme() {" +
                    "    if (document.body && document.body.classList.contains('reels-active')) {" +
                    "      var bridge = window.AndroidBridge || window.PwaninetBridge;" +
                    "      if (bridge && bridge.setSystemBarTheme) {" +
                    "        bridge.setSystemBarTheme('dark');" +
                    "      } else if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.NavigationBar) {" +
                    "        window.Capacitor.Plugins.NavigationBar.setStyle({ style: 'DARK' });" +
                    "      }" +
                    "      return;" +
                    "    }" +
                    "    var root = document.documentElement;" +
                    "    var theme = root.getAttribute('data-theme');" +
                    "    if (!theme || theme === 'system') {" +
                    "      var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;" +
                    "      theme = prefersDark ? 'dark' : 'light';" +
                    "    }" +
                    "    var isLight = theme !== 'dark';" +
                    "    var bridge = window.AndroidBridge || window.PwaninetBridge;" +
                    "    if (bridge && bridge.setSystemBarTheme) {" +
                    "      bridge.setSystemBarTheme(theme);" +
                    "    } else if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.NavigationBar) {" +
                    "      window.Capacitor.Plugins.NavigationBar.setStyle({ style: isLight ? 'LIGHT' : 'DARK' });" +
                    "    }" +
                    "  }" +
                    "  if (!window._vasto_native_theme_observer_active) {" +
                    "    window._vasto_native_theme_observer_active = true;" +
                    "    var observer = new MutationObserver(function(mutations) {" +
                    "      for (var i = 0; i < mutations.length; i++) {" +
                    "        if (mutations[i].attributeName === 'data-theme') {" +
                    "          syncTheme();" +
                    "          break;" +
                    "        }" +
                    "      }" +
                    "    });" +
                    "    observer.observe(document.documentElement, { attributes: true, attributeFilter: ['data-theme'] });" +
                    "    document.addEventListener('click', function() { setTimeout(syncTheme, 150); }, { passive: true });" +
                    "    window.addEventListener('storage', function(e) { if (e.key === 'theme') syncTheme(); });" +
                    "  }" +
                    "  syncTheme();" +
                    "})();";
                getBridge().getWebView().evaluateJavascript(js, null);
            }
        });
    }

    private void syncSystemBarThemeFromDom() {
        runOnUiThread(() -> {
            if (getBridge() != null && getBridge().getWebView() != null) {
                getBridge().getWebView().evaluateJavascript(
                    "(function() {" +
                    "  if (document.body && document.body.classList.contains('reels-active')) {" +
                    "    return 'dark';" +
                    "  }" +
                    "  var theme = document.documentElement.getAttribute('data-theme');" +
                    "  if (!theme || theme === 'system') {" +
                    "    var prefersDark = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches;" +
                    "    theme = prefersDark ? 'dark' : 'light';" +
                    "  }" +
                    "  return theme;" +
                    "})();",
                    themeValue -> {
                        if (themeValue != null) {
                            String cleanTheme = themeValue.replace("\"", "").trim();
                            if (!cleanTheme.isEmpty() && !"null".equalsIgnoreCase(cleanTheme) && !"undefined".equalsIgnoreCase(cleanTheme)) {
                                boolean isLight = !"dark".equalsIgnoreCase(cleanTheme);
                                applySystemBarTheme(isLight);
                            }
                        }
                    }
                );
            }
        });
    }

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        // Crash Guard: intercept uninitialized FirebaseApp crashes on background threads gracefully
        final Thread.UncaughtExceptionHandler defaultHandler = Thread.getDefaultUncaughtExceptionHandler();
        Thread.setDefaultUncaughtExceptionHandler((thread, throwable) -> {
            boolean isFirebaseMissing = false;
            Throwable current = throwable;
            while (current != null) {
                String msg = current.getMessage();
                if (msg != null && (msg.contains("Default FirebaseApp is not initialized") || msg.contains("FirebaseApp.initializeApp"))) {
                    isFirebaseMissing = true;
                    break;
                }
                current = current.getCause();
            }

            if (isFirebaseMissing) {
                System.err.println("[MainActivity] Gracefully suppressed uninitialized FirebaseApp crash on thread '" + (thread != null ? thread.getName() : "unknown") + "'");
                return;
            }

            if (defaultHandler != null) {
                defaultHandler.uncaughtException(thread, throwable);
            }
        });

        // Initialize Firebase safely if google-services.json was packaged or credentials exist
        try {
            Class<?> clazz = Class.forName("com.google.firebase.FirebaseApp");
            java.lang.reflect.Method getAppsMethod = clazz.getMethod("getApps", Context.class);
            java.util.List<?> apps = (java.util.List<?>) getAppsMethod.invoke(null, this);
            if (apps != null && apps.isEmpty()) {
                java.lang.reflect.Method initMethod = clazz.getMethod("initializeApp", Context.class);
                initMethod.invoke(null, this);
            }
        } catch (Throwable ignored) {}

        // Keep splash screen on screen until WebView renders initial page content
        SplashScreen splashScreen = SplashScreen.installSplashScreen(this);
        splashScreen.setKeepOnScreenCondition(() -> !isPageReady);

        // Safety watchdog: ensure splash screen dismisses if network is slow or hangs
        new Handler(Looper.getMainLooper()).postDelayed(() -> {
            isPageReady = true;
        }, SPLASH_WATCHDOG_TIMEOUT_MS);

        registerPlugin(NavigationBarPlugin.class);
        registerPlugin(HapticsPlugin.class);
        registerPlugin(UploadManagerPlugin.class);
        registerPlugin(AppUpdaterPlugin.class);

        // Configure edge-to-edge once at Activity creation
        EdgeToEdge.enable(this,
            SystemBarStyle.dark(Color.TRANSPARENT),
            SystemBarStyle.dark(Color.TRANSPARENT)
        );

        super.onCreate(savedInstanceState);

        boolean isSystemNight = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
        applySystemBarTheme(!isSystemNight);

        setupAndroidBridge();
        setupHostedCapacitor();
        setupNotificationChannels();
        setupSafeAreaInsets();
        setupNetworkMonitoring();
        setupCustomWebViewClient();
        setupWebViewCaching();
        setupWebViewDownloads();

        // Ensure custom user agent identifier with version and build is appended
        if (this.bridge != null && this.bridge.getWebView() != null) {
            WebSettings settings = this.bridge.getWebView().getSettings();
            String defaultUserAgent = settings.getUserAgentString();
            String appVersion = "1.0.0";
            long appCode = 1;
            try {
                PackageInfo pInfo = getPackageManager().getPackageInfo(getPackageName(), 0);
                if (pInfo.versionName != null) {
                    appVersion = pInfo.versionName;
                }
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                    appCode = pInfo.getLongVersionCode();
                } else {
                    appCode = pInfo.versionCode;
                }
            } catch (Exception ignored) {}
            settings.setUserAgentString(defaultUserAgent + " VastoApp/Android/" + appVersion + " (Build/" + appCode + ")");
        }
        
        // Restore WebView state if available
        if (savedInstanceState != null) {
            restoreWebViewState(savedInstanceState);
        }

        // Check if app was launched via deep link Intent
        if (getIntent() != null) {
            handleDeepLinkIntent(getIntent());
        }
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleDeepLinkIntent(intent);
    }

    private void handleDeepLinkIntent(Intent intent) {
        if (intent == null) return;
        Uri data = intent.getData();
        if (data == null) return;

        String path = null;
        String scheme = data.getScheme();
        if ("vasto".equalsIgnoreCase(scheme)) {
            String host = data.getHost();
            String dataPath = data.getPath();
            if (host != null && !host.isEmpty() && !host.contains(".")) {
                path = "/" + host + (dataPath != null ? dataPath : "");
            } else if (dataPath != null && !dataPath.isEmpty()) {
                path = dataPath;
            } else {
                path = "/";
            }
        } else if ("http".equalsIgnoreCase(scheme) || "https".equalsIgnoreCase(scheme)) {
            String dataPath = data.getPath();
            String dataQuery = data.getQuery();
            path = (dataPath != null ? dataPath : "/") + (dataQuery != null ? "?" + dataQuery : "");
        }

        if (path != null && !path.isEmpty()) {
            final String targetPath = path;
            if (!isPageReady) {
                pendingDeepLinkPath = targetPath;
            } else {
                executeDeepLinkNavigation(targetPath);
            }
        }
    }

    private void executeDeepLinkNavigation(String targetPath) {
        if (targetPath == null || targetPath.isEmpty()) return;
        runOnUiThread(() -> {
            if (getBridge() != null && getBridge().getWebView() != null) {
                WebView webView = getBridge().getWebView();
                String js = "(function() {" +
                            "  var target = '" + targetPath.replace("'", "\\'") + "';" +
                            "  if (window.htmx && document.getElementById('page-content-target')) {" +
                            "    window.htmx.ajax('GET', target, { target: '#page-content-target', swap: 'innerHTML' });" +
                            "    window.history.pushState({}, '', target);" +
                            "  } else {" +
                            "    window.location.href = target;" +
                            "  }" +
                            "})();";
                webView.evaluateJavascript(js, null);
            }
        });
    }

    @Override
    public void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        
        // Save WebView state
        if (getBridge() != null && getBridge().getWebView() != null) {
            WebView webView = getBridge().getWebView();
            Bundle webViewState = new Bundle();
            webView.saveState(webViewState);
            outState.putBundle(WEBVIEW_STATE_KEY, webViewState);
            System.out.println("[MainActivity] WebView state saved");
        }
    }

    @Override
    public void onRestoreInstanceState(Bundle savedInstanceState) {
        super.onRestoreInstanceState(savedInstanceState);
        
        // Restore WebView state
        if (savedInstanceState != null) {
            restoreWebViewState(savedInstanceState);
        }
    }

    private void restoreWebViewState(Bundle savedInstanceState) {
        if (getBridge() != null && getBridge().getWebView() != null) {
            Bundle webViewState = savedInstanceState.getBundle(WEBVIEW_STATE_KEY);
            if (webViewState != null) {
                WebView webView = getBridge().getWebView();
                webView.restoreState(webViewState);
                System.out.println("[MainActivity] WebView state restored");
            }
        }
    }

    private void setupNetworkMonitoring() {
        ConnectivityManager connectivityManager = 
            (ConnectivityManager) getSystemService(Context.CONNECTIVITY_SERVICE);
        
        if (connectivityManager != null) {
            NetworkRequest networkRequest = new NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .build();
            
            connectivityManager.registerNetworkCallback(networkRequest, 
                new ConnectivityManager.NetworkCallback() {
                    @Override
                    public void onAvailable(Network network) {
                        isNetworkAvailable = true;
                    }

                    @Override
                    public void onLost(Network network) {
                        isNetworkAvailable = false;
                    }
                });
            
            // Check initial network state
            Network activeNetwork = connectivityManager.getActiveNetwork();
            if (activeNetwork == null) {
                isNetworkAvailable = false;
            }
        }
    }

    public void openExternalUrl(String url) {
        if (url == null || url.trim().isEmpty()) return;
        runOnUiThread(() -> {
            try {
                Uri uri = Uri.parse(url.trim());
                Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                intent.addCategory(Intent.CATEGORY_BROWSABLE);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
            } catch (Exception e) {
                e.printStackTrace();
            }
        });
    }

    private boolean isInternalHost(String host) {
        return "pwaninet.app".equalsIgnoreCase(host) || "localhost".equalsIgnoreCase(host);
    }

    private boolean isDebugServerOrigin(Uri uri) {
        if ((getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) == 0 ||
                getBridge() == null || getBridge().getServerUrl() == null) return false;
        Uri server = Uri.parse(getBridge().getServerUrl());
        return uri != null && uri.getUserInfo() == null && server.getScheme() != null &&
            server.getScheme().equalsIgnoreCase(uri.getScheme()) && server.getHost() != null &&
            server.getHost().equalsIgnoreCase(uri.getHost()) && server.getPort() == uri.getPort();
    }

    private void setupHostedCapacitor() {
        // Moving startup local must not remove native sign-in/camera/downloads
        // from the existing trusted hosted interface. No wildcard origins.
        if (!WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)) return;
        try {
            ArrayList<PluginHandle> plugins = new ArrayList<>();
            for (String id : new String[] { "App", "Camera", "Filesystem", "PushNotifications",
                    "Share", "StatusBar", "SocialLogin", "NavigationBar", "Haptics",
                    "UploadManager", "AppUpdater", "WebView", "CapacitorHttp", "CapacitorCookies" }) {
                PluginHandle plugin = getBridge().getPlugin(id);
                if (plugin != null) plugins.add(plugin);
            }
            String script = JSExport.getGlobalJS(this, getBridge().getConfig().isLoggingEnabled(), getBridge().isDevMode())
                + "\nwindow.WEBVIEW_SERVER_URL=" + JSONObject.quote(getBridge().getLocalUrl()) + ";\n"
                + JSExport.getBridgeJS(this) + "\n" + JSExport.getPluginJS(plugins);
            WebViewCompat.addDocumentStartJavaScript(getBridge().getWebView(), script,
                Collections.singleton("https://pwaninet.app"));
            String debugServer = getBridge().getServerUrl();
            if (debugServer != null && isDebugServerOrigin(Uri.parse(debugServer))) {
                Uri origin = Uri.parse(debugServer).buildUpon().path(null).clearQuery().fragment(null).build();
                WebViewCompat.addDocumentStartJavaScript(getBridge().getWebView(), script,
                    Collections.singleton(origin.toString()));
            }
        } catch (Exception error) {
            Log.e("VastoOffline", "Could not initialize hosted native plugins", error);
        }
    }

    private void updateLaunchState(WebView webView) {
        Uri url = Uri.parse(webView.getUrl() == null ? "" : webView.getUrl());
        android.content.SharedPreferences prefs = getSharedPreferences("offline-launch", MODE_PRIVATE);
        if ("localhost".equals(url.getHost())) {
            try {
                JSONObject state = new JSONObject();
                state.put("returning", prefs.getBoolean("returning", false));
                state.put("signedOut", prefs.getBoolean("signedOut", false));
                state.put("route", prefs.getString("route", "/"));
                webView.evaluateJavascript("window.showVastoLaunchState && window.showVastoLaunchState(" + state + ")", null);
                isPageReady = true;
            } catch (Exception ignored) {}
        } else if ("pwaninet.app".equals(url.getHost())) {
            final String route = url.getEncodedPath() + (url.getEncodedQuery() == null ? "" : "?" + url.getEncodedQuery());
            webView.evaluateJavascript("Boolean(document.body && document.body.classList.contains('auth-entry-page') && !document.body.dataset.userId)", value -> {
                if ("true".equals(value)) prefs.edit().putBoolean("returning", false)
                    .putBoolean("signedOut", true).remove("route").apply();
            });
            webView.evaluateJavascript("Boolean(document.body && document.body.dataset.userId)", value -> {
                if ("true".equals(value)) prefs.edit().putBoolean("returning", true)
                    .putBoolean("signedOut", false).putString("route", route).apply();
            });
        }
    }

    private boolean handleExternalUri(Uri uri) {
        if (uri == null) return false;
        String scheme = uri.getScheme();
        if (scheme == null) return false;
        scheme = scheme.toLowerCase(Locale.US);

        // Allow WebView to handle internal data/blob/about/javascript
        if (scheme.equals("data") || scheme.equals("blob") || scheme.equals("about") || scheme.equals("javascript")) {
            return false;
        }

        // Custom protocols (e.g., tel:, mailto:, sms:, whatsapp:, market:)
        if (!scheme.equals("http") && !scheme.equals("https")) {
            try {
                Intent intent = new Intent(Intent.ACTION_VIEW, uri);
                intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
                startActivity(intent);
                return true;
            } catch (Exception e) {
                e.printStackTrace();
                return true;
            }
        }

        // For http/https, if host is external, launch via native Intent
        if (isDebugServerOrigin(uri)) return false;
        String host = uri.getHost();
        if (host != null && (!isInternalHost(host) || !scheme.equals("https"))) {
            openExternalUrl(uri.toString());
            return true;
        }

        return false;
    }

    private void setupCustomWebViewClient() {
        if (getBridge() != null) {
            getBridge().setWebViewClient(new BridgeWebViewClient(getBridge()) {
                @Override
                public android.webkit.WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                    // Hosted requests belong to Chromium (cookies, service worker,
                    // response policies), not Capacitor's native HTML proxy.
                    if (("pwaninet.app".equals(request.getUrl().getHost()) || isDebugServerOrigin(request.getUrl())) &&
                        !request.getUrl().getPath().startsWith("/_capacitor_")) return null;
                    return super.shouldInterceptRequest(view, request);
                }

                @Override
                public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                    if (request != null && request.getUrl() != null) {
                        String path = request.getUrl().getPath();
                        if (path != null && (path.contains("/logout/") || (path.contains("/switch-account/") || path.contains("/users/accounts/switch/") || path.contains("/users/accounts/remove/")))) {
                            getSharedPreferences("offline-launch", MODE_PRIVATE).edit()
                                .putBoolean("returning", false).putBoolean("signedOut", true).remove("route").apply();
                        }
                        if (handleExternalUri(request.getUrl())) {
                            return true;
                        }
                    }
                    return super.shouldOverrideUrlLoading(view, request);
                }

                @Override
                public boolean shouldOverrideUrlLoading(WebView view, String url) {
                    if (url != null) {
                        if (handleExternalUri(Uri.parse(url))) {
                            return true;
                        }
                    }
                    return super.shouldOverrideUrlLoading(view, url);
                }

                @Override
                public void onReceivedError(WebView view, WebResourceRequest request, WebResourceError error) {
                    super.onReceivedError(view, request, error);
                    // Only handle genuine network failures on top-level main frame navigation
                    if (request != null && request.isForMainFrame()) {
                        int errorCode = 0;
                        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.M) {
                            errorCode = error.getErrorCode();
                        }
                        if (errorCode == WebViewClient.ERROR_HOST_LOOKUP ||
                            errorCode == WebViewClient.ERROR_CONNECT ||
                            errorCode == WebViewClient.ERROR_TIMEOUT ||
                            !isNetworkAvailable) {
                            isPageReady = true;
                            isOfflinePageShowing = true;
                            loadOfflinePage(view, request.getUrl().toString());
                        }
                    }
                }
            });

            getBridge().addWebViewListener(new WebViewListener() {
                @Override
                public void onPageLoaded(WebView webView) {
                    // Do NOT dismiss splash here: committed DOM is often still blank
                    // (remote CSS/fonts pending). Web signals readiness via
                    // AndroidBridge.notifyPageReady(); watchdog covers failure.
                    isOfflinePageShowing = false;
                    updateLaunchState(webView);
                    setupAndroidBridge();
                    injectSafeAreaInsets();
                    syncSystemBarThemeFromDom();
                    injectThemeObserver();

                    if (pendingDeepLinkPath != null) {
                        String path = pendingDeepLinkPath;
                        pendingDeepLinkPath = null;
                        new Handler(Looper.getMainLooper()).postDelayed(() -> {
                            executeDeepLinkNavigation(path);
                        }, 300);
                    }
                }

                @Override
                public void onPageCommitVisible(WebView view, String url) {
                    // First pixels committed are usually the blank body background.
                    // Keep native splash up so logo persists until web-ready.
                    isOfflinePageShowing = false;
                    setupAndroidBridge();
                    injectSafeAreaInsets();
                    syncSystemBarThemeFromDom();
                    injectThemeObserver();
                }

                @Override
                public void onReceivedError(WebView webView) {
                    // Do NOT trigger loadOfflinePage here because WebViewListener
                    // receives callbacks for all subresources (images, fonts, canceled requests).
                    isPageReady = true;
                }
            });
        }
    }

    private void setupSafeAreaInsets() {
        View decorView = getWindow().getDecorView();
        ViewCompat.setOnApplyWindowInsetsListener(decorView, (v, windowInsets) -> {
            updateInsetsFrom(windowInsets);
            return ViewCompat.onApplyWindowInsets(v, windowInsets);
        });

        View contentView = findViewById(android.R.id.content);
        if (contentView != null) {
            ViewCompat.setOnApplyWindowInsetsListener(contentView, (v, windowInsets) -> {
                updateInsetsFrom(windowInsets);
                return ViewCompat.onApplyWindowInsets(v, windowInsets);
            });
        }

        // Check root insets immediately if already available
        WindowInsetsCompat rootInsets = ViewCompat.getRootWindowInsets(decorView);
        if (rootInsets != null) {
            updateInsetsFrom(rootInsets);
        } else if (hasNavigationBar()) {
            lastSafeBottom = getNavigationBarHeightDp();
            injectSafeAreaInsets();
        }
    }

    private void updateInsetsFrom(WindowInsetsCompat windowInsets) {
        if (windowInsets == null) return;

        Insets sysBars = windowInsets.getInsets(
            WindowInsetsCompat.Type.systemBars() | WindowInsetsCompat.Type.displayCutout()
        );
        Insets navBars = windowInsets.getInsets(WindowInsetsCompat.Type.navigationBars());
        Insets imeInsets = windowInsets.getInsets(WindowInsetsCompat.Type.ime());
        boolean imeVisible = windowInsets.isVisible(WindowInsetsCompat.Type.ime());

        float density = getResources().getDisplayMetrics().density;
        if (density > 0) {
            int top = Math.round(sysBars.top / density);
            int bottom = Math.round(Math.max(sysBars.bottom, navBars.bottom) / density);
            int left = Math.round(sysBars.left / density);
            int right = Math.round(sysBars.right / density);
            int imeHeightDp = Math.max(0, Math.round(imeInsets.bottom / density));

            // Fallback for 3-button navigation if bottom reports 0 but system navigation bar exists
            if (bottom == 0 && hasNavigationBar()) {
                bottom = getNavigationBarHeightDp();
            }

            lastSafeTop = top;
            lastSafeBottom = bottom;
            lastSafeLeft = left;
            lastSafeRight = right;
            lastImeVisible = imeVisible || imeHeightDp > bottom + 40;
            lastImeHeight = Math.max(0, imeHeightDp - bottom);

            injectSafeAreaInsets();
        }
    }

    private boolean hasNavigationBar() {
        int id = getResources().getIdentifier("config_showNavigationBar", "bool", "android");
        return id > 0 && getResources().getBoolean(id);
    }

    private int getNavigationBarHeightDp() {
        int resourceId = getResources().getIdentifier("navigation_bar_height", "dimen", "android");
        if (resourceId > 0) {
            float density = getResources().getDisplayMetrics().density;
            if (density > 0) {
                return Math.round(getResources().getDimensionPixelSize(resourceId) / density);
            }
        }
        return 48; // Standard Android 3-button navigation height in dp
    }

    private void injectSafeAreaInsets() {
        runOnUiThread(() -> {
            if (getBridge() != null && getBridge().getWebView() != null) {
                if (lastSafeBottom == 0) {
                    View decorView = getWindow().getDecorView();
                    WindowInsetsCompat rootInsets = ViewCompat.getRootWindowInsets(decorView);
                    if (rootInsets != null) {
                        float density = getResources().getDisplayMetrics().density;
                        if (density > 0) {
                            Insets navBars = rootInsets.getInsets(WindowInsetsCompat.Type.navigationBars());
                            Insets sysBars = rootInsets.getInsets(WindowInsetsCompat.Type.systemBars());
                            Insets imeInsets = rootInsets.getInsets(WindowInsetsCompat.Type.ime());
                            int b = Math.round(Math.max(sysBars.bottom, navBars.bottom) / density);
                            if (b > 0) lastSafeBottom = b;
                            int t = Math.round(sysBars.top / density);
                            if (t > 0) lastSafeTop = t;
                            int imeDp = Math.max(0, Math.round(imeInsets.bottom / density));
                            lastImeVisible = rootInsets.isVisible(WindowInsetsCompat.Type.ime()) || imeDp > lastSafeBottom + 40;
                            lastImeHeight = Math.max(0, imeDp - lastSafeBottom);
                        }
                    }
                    if (lastSafeBottom == 0 && hasNavigationBar()) {
                        lastSafeBottom = getNavigationBarHeightDp();
                    }
                }

                int effectiveSafeBottom = lastImeVisible ? 0 : lastSafeBottom;

                String js = String.format(Locale.US,
                    "(function() {" +
                    "  var root = document.documentElement;" +
                    "  var imeOpen = %s;" +
                    "  root.classList.add('is-capacitor', 'is-native-app');" +
                    "  root.classList.toggle('keyboard-open', imeOpen);" +
                    "  root.dataset.imeVisible = imeOpen ? 'true' : 'false';" +
                    "  root.style.setProperty('--vasto-safe-area-top', '%dpx');" +
                    "  root.style.setProperty('--vasto-safe-area-bottom', '%dpx');" +
                    "  root.style.setProperty('--vasto-safe-area-left', '%dpx');" +
                    "  root.style.setProperty('--vasto-safe-area-right', '%dpx');" +
                    "  root.style.setProperty('--vasto-keyboard-height', '%dpx');" +
                    "  if (document.body) {" +
                    "    document.body.classList.add('is-capacitor', 'is-native-app');" +
                    "    document.body.classList.toggle('keyboard-open', imeOpen);" +
                    "    document.body.style.setProperty('--vasto-safe-area-top', '%dpx');" +
                    "    document.body.style.setProperty('--vasto-safe-area-bottom', '%dpx');" +
                    "  }" +
                    "  window.dispatchEvent(new CustomEvent('vasto:safe-area-changed', { detail: { top: %d, bottom: %d, left: %d, right: %d, imeVisible: imeOpen, imeHeight: %d } }));" +
                    "})();",
                    lastImeVisible ? "true" : "false",
                    lastSafeTop, effectiveSafeBottom, lastSafeLeft, lastSafeRight,
                    lastImeVisible ? lastImeHeight : 0,
                    lastSafeTop, effectiveSafeBottom,
                    lastSafeTop, effectiveSafeBottom, lastSafeLeft, lastSafeRight,
                    lastImeVisible ? lastImeHeight : 0
                );
                getBridge().getWebView().evaluateJavascript(js, null);
            }
        });
    }

    @Override
    public void onResume() {
        super.onResume();
        MainActivity.isAppForeground = true;
        injectSafeAreaInsets();
        syncSystemBarThemeFromDom();
        injectThemeObserver();
    }

    @Override
    public void onPause() {
        super.onPause();
        MainActivity.isAppForeground = false;
    }

    private void setupWebViewDownloads() {
        if (getBridge() == null || getBridge().getWebView() == null) return;

        getBridge().getWebView().setDownloadListener((url, userAgent, contentDisposition, mimeType, contentLength) -> {
            try {
                Uri downloadUri = Uri.parse(url);
                DownloadManager downloadManager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
                if (downloadManager == null) {
                    Toast.makeText(this, "Unable to start download", Toast.LENGTH_SHORT).show();
                    return;
                }

                String filename = URLUtil.guessFileName(url, contentDisposition, mimeType);
                filename = filename == null ? "vasto-resource" : filename.replaceAll("[\\\\/:*?\"<>|]", "_");
                DownloadManager.Request downloadRequest = new DownloadManager.Request(downloadUri)
                    .setTitle(filename)
                    .setDescription("Downloading from Vasto")
                    .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                    .setDestinationInExternalPublicDir(Environment.DIRECTORY_DOWNLOADS, "Vasto/" + filename);

                if (mimeType != null && !mimeType.isEmpty()) downloadRequest.setMimeType(mimeType);
                if (userAgent != null && !userAgent.isEmpty()) downloadRequest.addRequestHeader("User-Agent", userAgent);
                String cookies = CookieManager.getInstance().getCookie(url);
                if (cookies != null && !cookies.isEmpty()) downloadRequest.addRequestHeader("Cookie", cookies);

                downloadManager.enqueue(downloadRequest);
                Toast.makeText(this, "Download started. Find it in Downloads/Vasto", Toast.LENGTH_LONG).show();
            } catch (Exception error) {
                System.err.println("[MainActivity] Could not start WebView download: " + error.getMessage());
                Toast.makeText(this, "Unable to start download", Toast.LENGTH_SHORT).show();
            }
        });
    }

    private String startMediaDownload(String url, String filename, String mimeType, String category) {
        if (url == null || !url.startsWith("https://") && !url.startsWith("http://")) return "failed";
        String safeName = filename == null || filename.trim().isEmpty() ? "vasto-media" : filename;
        safeName = safeName.replaceAll("[\\\\/:*?\"<>|]", "_");
        boolean isImage = "image".equalsIgnoreCase(category);
        boolean isVideo = "video".equalsIgnoreCase(category) || "reels".equalsIgnoreCase(category);
        if (!isImage && !isVideo && (mimeType == null || mimeType.isEmpty())) {
            mimeType = URLConnection.guessContentTypeFromName(safeName);
        }
        final String resolvedMimeType = mimeType == null || mimeType.isEmpty()
            ? (isImage ? "image/jpeg" : isVideo ? "video/mp4" : "application/octet-stream")
            : mimeType;

        // On Android 10+ write straight into the shared MediaStore collection. This
        // avoids scoped-storage restrictions on DownloadManager's public-directory
        // destination and makes the finished item immediately visible to gallery apps.
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q && (isImage || isVideo)) {
            final String displayName = safeName;
            final String destination = url;
            final String mediaType = resolvedMimeType;
            final String webViewUserAgent = getBridge() != null && getBridge().getWebView() != null
                ? getBridge().getWebView().getSettings().getUserAgentString()
                : System.getProperty("http.agent", "Vasto");
            new Thread(() -> {
                Uri target = null;
                try {
                    java.net.HttpURLConnection connection = (java.net.HttpURLConnection) new java.net.URL(destination).openConnection();
                    connection.setConnectTimeout(20000);
                    connection.setReadTimeout(60000);
                    connection.setInstanceFollowRedirects(true);
                    connection.setRequestProperty("User-Agent", webViewUserAgent);
                    String cookies = CookieManager.getInstance().getCookie(destination);
                    if (cookies != null && !cookies.isEmpty()) connection.setRequestProperty("Cookie", cookies);
                    connection.connect();
                    int responseCode = connection.getResponseCode();
                    if (responseCode < 200 || responseCode >= 300) {
                        throw new IOException("Media server returned HTTP " + responseCode);
                    }
                    String responseMimeType = connection.getContentType();
                    if (responseMimeType != null && responseMimeType.contains(";")) {
                        responseMimeType = responseMimeType.substring(0, responseMimeType.indexOf(';')).trim();
                    }
                    Uri collection = isVideo
                        ? android.provider.MediaStore.Video.Media.EXTERNAL_CONTENT_URI
                        : android.provider.MediaStore.Images.Media.EXTERNAL_CONTENT_URI;
                    ContentValues values = new ContentValues();
                    values.put(android.provider.MediaStore.MediaColumns.DISPLAY_NAME, displayName);
                    values.put(android.provider.MediaStore.MediaColumns.MIME_TYPE,
                        responseMimeType != null && responseMimeType.startsWith(isVideo ? "video/" : "image/")
                            ? responseMimeType : mediaType);
                    values.put(android.provider.MediaStore.MediaColumns.RELATIVE_PATH,
                        isVideo ? "Movies/Vasto" : "Pictures/Vasto");
                    values.put(android.provider.MediaStore.MediaColumns.IS_PENDING, 1);
                    target = getContentResolver().insert(collection, values);
                    if (target == null) throw new IOException("Could not create gallery media entry");

                    try (InputStream input = connection.getInputStream();
                         OutputStream output = getContentResolver().openOutputStream(target, "w")) {
                        if (output == null) throw new IOException("Could not open gallery media entry");
                        byte[] buffer = new byte[32768];
                        int count;
                        while ((count = input.read(buffer)) != -1) output.write(buffer, 0, count);
                    } finally {
                        connection.disconnect();
                    }
                    ContentValues ready = new ContentValues();
                    ready.put(android.provider.MediaStore.MediaColumns.IS_PENDING, 0);
                    getContentResolver().update(target, ready, null, null);
                    runOnUiThread(() -> Toast.makeText(this, "Saved to your gallery", Toast.LENGTH_LONG).show());
                } catch (Exception error) {
                    if (target != null) getContentResolver().delete(target, null, null);
                    System.err.println("[MainActivity] Gallery download failed: " + error.getMessage());
                    runOnUiThread(() -> Toast.makeText(this, "Could not save media. Check your connection and try again.", Toast.LENGTH_LONG).show());
                }
            }, "vasto-media-download").start();
            Toast.makeText(this, "Saving to your gallery…", Toast.LENGTH_SHORT).show();
            return "started";
        }
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.Q &&
            ContextCompat.checkSelfPermission(this, Manifest.permission.WRITE_EXTERNAL_STORAGE) != PackageManager.PERMISSION_GRANTED) {
            pendingMediaDownload = new PendingMediaDownload(url, safeName, mimeType, category);
            requestPermissions(new String[]{Manifest.permission.WRITE_EXTERNAL_STORAGE}, REQUEST_MEDIA_DOWNLOAD_PERMISSION);
            Toast.makeText(this, "Allow storage access to save this media", Toast.LENGTH_LONG).show();
            return "permission_requested";
        }
        try {
            DownloadManager manager = (DownloadManager) getSystemService(DOWNLOAD_SERVICE);
            if (manager == null) return "failed";
            Uri uri = Uri.parse(url);
            String destinationDirectory = isImage ? Environment.DIRECTORY_PICTURES
                : isVideo ? Environment.DIRECTORY_MOVIES : Environment.DIRECTORY_DOWNLOADS;
            DownloadManager.Request request = new DownloadManager.Request(uri)
                .setTitle(safeName)
                .setDescription("Downloading from Vasto")
                .setNotificationVisibility(DownloadManager.Request.VISIBILITY_VISIBLE_NOTIFY_COMPLETED)
                .setDestinationInExternalPublicDir(destinationDirectory, "Vasto/" + safeName);
            request.setMimeType(resolvedMimeType);
            String cookies = CookieManager.getInstance().getCookie(url);
            if (cookies != null && !cookies.isEmpty()) request.addRequestHeader("Cookie", cookies);
            request.addRequestHeader("User-Agent", System.getProperty("http.agent", "Vasto"));
            manager.enqueue(request);
            Toast.makeText(this, "Download started. You can find it in your gallery or Downloads.", Toast.LENGTH_LONG).show();
            return "started";
        } catch (Exception error) {
            System.err.println("[MainActivity] Could not start media download: " + error.getMessage());
            Toast.makeText(this, "Unable to start download. Check storage permission and try again.", Toast.LENGTH_LONG).show();
            return "failed";
        }
    }

    private void setupWebViewCaching() {
        if (getBridge() != null && getBridge().getWebView() != null) {
            WebView webView = getBridge().getWebView();
            
            // Set initial WebView background to match system splash background
            boolean isSystemNight = (getResources().getConfiguration().uiMode & Configuration.UI_MODE_NIGHT_MASK) == Configuration.UI_MODE_NIGHT_YES;
            webView.setBackgroundColor(isSystemNight ? Color.parseColor("#0f172a") : Color.WHITE);

            // Enable caching for better performance and offline support
            webView.getSettings().setDomStorageEnabled(true);
            webView.getSettings().setDatabaseEnabled(true);
            
            // HTTP policy stays intact; the service worker owns explicit saved copies.
            webView.getSettings().setCacheMode(android.webkit.WebSettings.LOAD_DEFAULT);
            
            // Enable hardware acceleration for better performance
            webView.setLayerType(WebView.LAYER_TYPE_HARDWARE, null);
            
            System.out.println("[MainActivity] WebView caching enabled with hardware acceleration");
        }
    }

    private void loadOfflinePage(WebView webView, String failedUrl) {
        isOfflinePageShowing = true;
        if (isDebugServerOrigin(Uri.parse(failedUrl))) {
            String retry = android.text.TextUtils.htmlEncode(getBridge().getServerUrl());
            webView.loadDataWithBaseURL(getBridge().getServerUrl(),
                "<!doctype html><meta name='viewport' content='width=device-width,initial-scale=1'>" +
                "<body style='font:18px system-ui;padding:24px;background:#f5f7fb;color:#17233b'>" +
                "<h1>Vasto</h1><p>The local development server could not be reached. " +
                "Start Django and restore the USB tunnel, then try again.</p><a href='" + retry +
                "'>Retry connection</a></body>", "text/html", "UTF-8", null);
            return;
        }
        Uri failed = Uri.parse(failedUrl);
        String route = failed.getEncodedPath() + (failed.getEncodedQuery() == null ? "" : "?" + failed.getEncodedQuery());
        webView.loadUrl("https://localhost/index.html?route=" + Uri.encode(route));
    }

    private Vibrator getVibratorService() {
        try {
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                VibratorManager vibratorManager = (VibratorManager) getSystemService(Context.VIBRATOR_MANAGER_SERVICE);
                if (vibratorManager != null) {
                    return vibratorManager.getDefaultVibrator();
                }
            }
            return (Vibrator) getSystemService(Context.VIBRATOR_SERVICE);
        } catch (Exception ignored) {
            return null;
        }
    }

    public void performNativeHapticImpact(String style) {
        runOnUiThread(() -> {
            try {
                String cleanStyle = style != null ? style.trim().toLowerCase(Locale.US) : "light";
                Vibrator vibrator = getVibratorService();
                boolean vibratorHandled = false;
                if (vibrator != null && vibrator.hasVibrator()) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        int effectId = VibrationEffect.EFFECT_CLICK;
                        if ("light".equals(cleanStyle)) {
                            effectId = VibrationEffect.EFFECT_TICK;
                        } else if ("heavy".equals(cleanStyle)) {
                            effectId = VibrationEffect.EFFECT_HEAVY_CLICK;
                        }
                        try {
                            vibrator.vibrate(VibrationEffect.createPredefined(effectId));
                            vibratorHandled = true;
                        } catch (Exception e) {
                            int amp = "light".equals(cleanStyle) ? 45 : ("heavy".equals(cleanStyle) ? 165 : 95);
                            int dur = "light".equals(cleanStyle) ? 10 : ("heavy".equals(cleanStyle) ? 28 : 16);
                            vibrator.vibrate(VibrationEffect.createOneShot(dur, amp));
                            vibratorHandled = true;
                        }
                    } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        int amplitude = "light".equals(cleanStyle) ? 45 : ("heavy".equals(cleanStyle) ? 165 : 95);
                        int duration = "light".equals(cleanStyle) ? 10 : ("heavy".equals(cleanStyle) ? 28 : 16);
                        vibrator.vibrate(VibrationEffect.createOneShot(duration, amplitude));
                        vibratorHandled = true;
                    }
                }

                if (!vibratorHandled) {
                    View view = getBridge() != null && getBridge().getWebView() != null 
                        ? getBridge().getWebView() 
                        : getWindow().getDecorView();
                    if (view != null) {
                        int feedbackConstant = HapticFeedbackConstants.VIRTUAL_KEY;
                        if ("light".equals(cleanStyle)) {
                            feedbackConstant = HapticFeedbackConstants.KEYBOARD_TAP;
                        } else if ("heavy".equals(cleanStyle)) {
                            feedbackConstant = HapticFeedbackConstants.LONG_PRESS;
                        }
                        view.performHapticFeedback(feedbackConstant);
                    }
                }
            } catch (Exception ignored) {}
        });
    }

    public void performNativeHapticSelection() {
        runOnUiThread(() -> {
            try {
                Vibrator vibrator = getVibratorService();
                if (vibrator != null && vibrator.hasVibrator()) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        vibrator.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_TICK));
                        return;
                    } else if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        vibrator.vibrate(VibrationEffect.createOneShot(6, 35));
                        return;
                    }
                }
                View view = getBridge() != null && getBridge().getWebView() != null 
                    ? getBridge().getWebView() 
                    : getWindow().getDecorView();
                if (view != null) {
                    view.performHapticFeedback(HapticFeedbackConstants.CLOCK_TICK);
                }
            } catch (Exception ignored) {}
        });
    }

    public void performNativeHapticNotification(String type) {
        runOnUiThread(() -> {
            try {
                Vibrator vibrator = getVibratorService();
                String cleanType = type != null ? type.trim().toLowerCase(Locale.US) : "success";
                if (vibrator != null && vibrator.hasVibrator()) {
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
                        if ("success".equals(cleanType)) {
                            vibrator.vibrate(VibrationEffect.createPredefined(VibrationEffect.EFFECT_DOUBLE_CLICK));
                            return;
                        }
                    }
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        if ("error".equals(cleanType)) {
                            long[] timings = {0, 35, 55, 35, 55, 45};
                            int[] amplitudes = {0, 170, 0, 170, 0, 220};
                            vibrator.vibrate(VibrationEffect.createWaveform(timings, amplitudes, -1));
                            return;
                        } else if ("warning".equals(cleanType)) {
                            long[] timings = {0, 40, 70, 40};
                            int[] amplitudes = {0, 150, 0, 150};
                            vibrator.vibrate(VibrationEffect.createWaveform(timings, amplitudes, -1));
                            return;
                        } else { // success
                            long[] timings = {0, 16, 55, 24};
                            int[] amplitudes = {0, 100, 0, 150};
                            vibrator.vibrate(VibrationEffect.createWaveform(timings, amplitudes, -1));
                            return;
                        }
                    }
                }
                View view = getBridge() != null && getBridge().getWebView() != null 
                    ? getBridge().getWebView() 
                    : getWindow().getDecorView();
                if (view != null) {
                    view.performHapticFeedback(HapticFeedbackConstants.VIRTUAL_KEY);
                }
            } catch (Exception ignored) {}
        });
    }

    public void performNativeVibrate(long durationMs) {
        runOnUiThread(() -> {
            try {
                Vibrator vibrator = getVibratorService();
                if (vibrator != null && vibrator.hasVibrator()) {
                    long dur = Math.max(10, Math.min(durationMs, 2000));
                    if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
                        vibrator.vibrate(VibrationEffect.createOneShot(dur, VibrationEffect.DEFAULT_AMPLITUDE));
                    } else {
                        vibrator.vibrate(dur);
                    }
                }
            } catch (Exception ignored) {}
        });
    }

    public void showToast(String message) {
        runOnUiThread(() -> {
            try {
                if (message != null && !message.trim().isEmpty()) {
                    Toast.makeText(this, message.trim(), Toast.LENGTH_SHORT).show();
                }
            } catch (Exception ignored) {}
        });
    }

    // Marker required by @capgo/capacitor-social-login so scoped Google login
    // (openid/email/profile) can launch from this activity.
    @Override
    public void IHaveModifiedTheMainActivityForTheUseWithSocialLoginPlugin() {
        // Intentional no-op: presence of this method satisfies the plugin.
    }
}
