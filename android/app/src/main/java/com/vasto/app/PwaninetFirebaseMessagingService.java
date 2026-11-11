package com.vasto.app;

import android.app.NotificationChannel;
import android.app.NotificationManager;
import android.app.PendingIntent;
import android.content.Context;
import android.content.Intent;
import android.graphics.Bitmap;
import android.graphics.BitmapFactory;
import android.graphics.Canvas;
import android.graphics.Color;
import android.graphics.Paint;
import android.graphics.PorterDuff;
import android.graphics.PorterDuffXfermode;
import android.graphics.Rect;
import android.net.http.HttpResponseCache;
import android.net.Uri;
import android.os.Build;
import android.util.Log;

import androidx.annotation.NonNull;
import androidx.core.app.NotificationCompat;
import androidx.core.app.NotificationManagerCompat;
import androidx.core.content.ContextCompat;

import com.capacitorjs.plugins.pushnotifications.PushNotificationsPlugin;
import com.google.firebase.messaging.FirebaseMessagingService;
import com.google.firebase.messaging.RemoteMessage;

import org.json.JSONObject;

import java.io.File;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Map;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.concurrent.TimeUnit;

/**
 * Custom Firebase Messaging Service for Pwaninet.
 * Builds rich native notifications featuring:
 * - Status bar icon: Dedicated monochrome glyph (R.drawable.ic_stat_vasto)
 * - Large icon: Natively circular-masked Actor Avatar
 * - Big picture: Media thumbnail via BigPictureStyle
 * - Brand accent color: #2563eb
 * - Seamless forwarding to Capacitor for foreground WebView listeners
 * - Zero duplicate notifications across Foreground, Background, and Terminated states
 */
public class PwaninetFirebaseMessagingService extends FirebaseMessagingService {

    private static final String TAG = "PwaninetPushService";
    private static final String DEFAULT_CHANNEL_ID = "vasto_notifications";
    private static final int IMAGE_TIMEOUT_MS = 2500;
    private static final ExecutorService DOWNLOAD_EXECUTOR = Executors.newFixedThreadPool(2);

    @Override
    public void onCreate() {
        super.onCreate();
        initHttpCache();
    }

    private void initHttpCache() {
        try {
            if (HttpResponseCache.getInstalled() == null) {
                File cacheDir = new File(getCacheDir(), "notif_http_cache");
                long cacheSize = 10 * 1024 * 1024; // 10 MiB disk cache
                HttpResponseCache.install(cacheDir, cacheSize);
            }
        } catch (Exception e) {
            Log.d(TAG, "HTTP response cache could not be installed: " + e.getMessage());
        }
    }

    /**
     * Base URL the app is configured to load. Debug builds point Capacitor's
     * server.url at a dev origin (e.g. the Cloudflare tunnel), so notification
     * deep links must resolve against that origin instead of production.
     */
    private String appBaseUrl() {
        try (InputStream in = getAssets().open("capacitor.config.json")) {
            byte[] buf = new byte[in.available()];
            int read = in.read(buf);
            JSONObject cfg = new JSONObject(new String(buf, 0, Math.max(read, 0), "UTF-8"));
            JSONObject server = cfg.optJSONObject("server");
            String url = server == null ? null : server.optString("url", null);
            if (url != null && !url.trim().isEmpty()) {
                return url.trim().replaceAll("/+$", "");
            }
        } catch (Exception ignored) {}
        return "https://pwaninet.app";
    }

    @Override
    public void onNewToken(@NonNull String token) {
        super.onNewToken(token);
        Log.d(TAG, "Refreshed FCM registration token");
        try {
            PushNotificationsPlugin.onNewToken(token);
        } catch (Exception e) {
            Log.w(TAG, "Failed to pass token to Capacitor plugin: " + e.getMessage());
        }
    }

    @Override
    public void onMessageReceived(@NonNull RemoteMessage remoteMessage) {
        super.onMessageReceived(remoteMessage);
        Log.d(TAG, "Received FCM message from: " + remoteMessage.getFrom());

        // Always forward remoteMessage to Capacitor plugin so in-app listeners fire in foreground
        try {
            PushNotificationsPlugin.sendRemoteMessage(remoteMessage);
        } catch (Exception e) {
            Log.w(TAG, "Could not forward remote message to Capacitor: " + e.getMessage());
        }

        // If the message has an existing FCM notification block, Google Play Services
        // or Capacitor CommonNotificationBuilder handles posting. Avoid duplicate posting.
        if (remoteMessage.getNotification() != null) {
            Log.d(TAG, "Message already contains notification block; skipping custom native builder to prevent duplicates.");
            return;
        }

        // While the app is open, the WebView already renders the thin in-app
        // banner (PushNotifications plugin -> showNativePushBanner). Posting a
        // native notification here used to stack Android's thick heads-up
        // banner on top of it — so suppress it in the foreground. Background
        // and killed app states still post the rich native notification.
        if (MainActivity.isAppForeground) {
            Log.d(TAG, "App is in the foreground; skipping native system banner (in-app card handles this push).");
            return;
        }

        Map<String, String> data = remoteMessage.getData();
        if (data == null || data.isEmpty()) {
            Log.w(TAG, "Empty data payload received; cannot construct notification.");
            return;
        }

        buildAndDisplayNativeNotification(remoteMessage, data);
    }

    private void buildAndDisplayNativeNotification(RemoteMessage remoteMessage, Map<String, String> data) {
        try {
            String title = data.get("title");
            if (title == null || title.trim().isEmpty()) {
                title = "Pwaninet";
            }

            String body = data.get("body");
            if (body == null) {
                body = "";
            }

            String avatarUrl = data.get("avatar_url");
            if (avatarUrl == null || avatarUrl.trim().isEmpty()) {
                String iconCandidate = data.get("icon");
                if (iconCandidate != null && (iconCandidate.startsWith("http://") || iconCandidate.startsWith("https://"))) {
                    avatarUrl = iconCandidate;
                }
            }

            String imageUrl = data.get("image");
            String channelId = data.get("channel_id");
            if (channelId == null || channelId.trim().isEmpty()) {
                channelId = DEFAULT_CHANNEL_ID;
            }

            String tag = data.get("tag");
            if (tag == null || tag.trim().isEmpty()) {
                tag = "vasto_" + System.currentTimeMillis();
            }

            String notificationId = data.get("notification_id");
            int notifIntId = (notificationId != null && !notificationId.isEmpty())
                    ? Math.abs(notificationId.hashCode())
                    : (int) (System.currentTimeMillis() & 0xFFFFFFF);

            String destinationUrl = data.get("destination_url");
            if (destinationUrl == null || destinationUrl.trim().isEmpty()) {
                destinationUrl = data.get("url");
            }
            if (destinationUrl == null || destinationUrl.trim().isEmpty()) {
                destinationUrl = "/notifications/";
            }

            ensureNotificationChannel(channelId);

            NotificationCompat.Builder builder = new NotificationCompat.Builder(this, channelId)
                    .setSmallIcon(R.drawable.ic_stat_vasto)
                    .setColor(ContextCompat.getColor(this, R.color.colorPrimary))
                    .setContentTitle(title)
                    .setContentText(body)
                    .setPriority(NotificationCompat.PRIORITY_HIGH)
                    .setVisibility(NotificationCompat.VISIBILITY_PUBLIC)
                    .setAutoCancel(true)
                    .setDefaults(NotificationCompat.DEFAULT_ALL);

            // Deep-link intent to MainActivity
            Intent clickIntent = new Intent(this, MainActivity.class);
            clickIntent.setAction(Intent.ACTION_VIEW);
            clickIntent.addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP | Intent.FLAG_ACTIVITY_CLEAR_TOP);

            String fullUrl = destinationUrl;
            if (!fullUrl.startsWith("http://") && !fullUrl.startsWith("https://")) {
                fullUrl = appBaseUrl() + (fullUrl.startsWith("/") ? fullUrl : "/" + fullUrl);
            }
            clickIntent.setData(Uri.parse(fullUrl));

            // Populate all data extras so Capacitor pushNotificationActionPerformed receives them
            for (Map.Entry<String, String> entry : data.entrySet()) {
                clickIntent.putExtra(entry.getKey(), entry.getValue());
            }
            clickIntent.putExtra("google.message_id", remoteMessage.getMessageId());

            PendingIntent pendingIntent = PendingIntent.getActivity(
                    this,
                    notifIntId,
                    clickIntent,
                    PendingIntent.FLAG_UPDATE_CURRENT | PendingIntent.FLAG_IMMUTABLE
            );
            builder.setContentIntent(pendingIntent);

            // Concurrent Image Processing: Fetch avatar and media images in parallel
            Future<Bitmap> avatarFuture = null;
            if (avatarUrl != null && !avatarUrl.trim().isEmpty()) {
                final String aUrl = avatarUrl;
                avatarFuture = DOWNLOAD_EXECUTOR.submit(() -> downloadBitmapWithTimeout(aUrl));
            }

            Future<Bitmap> mediaFuture = null;
            if (imageUrl != null && !imageUrl.trim().isEmpty()) {
                final String mUrl = imageUrl;
                mediaFuture = DOWNLOAD_EXECUTOR.submit(() -> downloadBitmapWithTimeout(mUrl));
            }

            // 1. Actor Avatar Processing: Apply circular masking
            if (avatarFuture != null) {
                try {
                    Bitmap avatarBitmap = avatarFuture.get(IMAGE_TIMEOUT_MS, TimeUnit.MILLISECONDS);
                    if (avatarBitmap != null) {
                        Bitmap circularAvatar = createCircularBitmap(avatarBitmap);
                        if (circularAvatar != null) {
                            builder.setLargeIcon(circularAvatar);
                        }
                    }
                } catch (Exception e) {
                    Log.d(TAG, "Avatar image retrieval skipped/timed out: " + e.getMessage());
                }
            }

            // 2. Media Processing: Apply BigPictureStyle
            Bitmap mediaBitmap = null;
            if (mediaFuture != null) {
                try {
                    mediaBitmap = mediaFuture.get(IMAGE_TIMEOUT_MS, TimeUnit.MILLISECONDS);
                } catch (Exception e) {
                    Log.d(TAG, "Media image retrieval skipped/timed out: " + e.getMessage());
                }
            }

            if (mediaBitmap != null) {
                NotificationCompat.BigPictureStyle bigPicStyle = new NotificationCompat.BigPictureStyle()
                        .bigPicture(mediaBitmap)
                        .bigLargeIcon((Bitmap) null) // Cleanly removes largeIcon when expanded so media has full visual focus
                        .setSummaryText(body);
                builder.setStyle(bigPicStyle);
            } else {
                builder.setStyle(new NotificationCompat.BigTextStyle().bigText(body));
            }

            NotificationManagerCompat notificationManager = NotificationManagerCompat.from(this);
            notificationManager.notify(tag, notifIntId, builder.build());
            Log.d(TAG, "Native rich notification posted successfully for: " + tag);

        } catch (SecurityException se) {
            Log.w(TAG, "Notification permission missing (POST_NOTIFICATIONS): " + se.getMessage());
        } catch (Exception ex) {
            Log.e(TAG, "Failed to display native notification", ex);
        }
    }

    private void ensureNotificationChannel(String channelId) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O) {
            try {
                NotificationManager nm = (NotificationManager) getSystemService(Context.NOTIFICATION_SERVICE);
                if (nm != null && nm.getNotificationChannel(channelId) == null) {
                    NotificationChannel channel = new NotificationChannel(
                            channelId,
                            "Pwaninet Notifications",
                            NotificationManager.IMPORTANCE_HIGH
                    );
                    channel.setDescription("Social updates, mentions, posts, documents, and messages");
                    channel.enableVibration(true);
                    channel.enableLights(true);
                    channel.setLightColor(Color.parseColor("#2563eb"));
                    nm.createNotificationChannel(channel);
                }
            } catch (Exception e) {
                Log.w(TAG, "Could not ensure notification channel: " + e.getMessage());
            }
        }
    }

    private Bitmap downloadBitmapWithTimeout(String urlString) {
        HttpURLConnection connection = null;
        InputStream input = null;
        try {
            URL url = new URL(urlString);
            connection = (HttpURLConnection) url.openConnection();
            connection.setDoInput(true);
            connection.setConnectTimeout(IMAGE_TIMEOUT_MS);
            connection.setReadTimeout(IMAGE_TIMEOUT_MS);
            connection.setInstanceFollowRedirects(true);
            connection.connect();

            int responseCode = connection.getResponseCode();
            if (responseCode != HttpURLConnection.HTTP_OK) {
                Log.w(TAG, "Image download returned HTTP " + responseCode + " for " + urlString);
                return null;
            }

            input = connection.getInputStream();
            return BitmapFactory.decodeStream(input);
        } catch (Exception e) {
            Log.w(TAG, "Failed to download notification image from " + urlString + ": " + e.getMessage());
            return null;
        } finally {
            if (input != null) {
                try { input.close(); } catch (Exception ignored) {}
            }
            if (connection != null) {
                try { connection.disconnect(); } catch (Exception ignored) {}
            }
        }
    }

    /**
     * Crop source bitmap to center square and clip to circle using PorterDuff SRC_IN.
     */
    private Bitmap createCircularBitmap(Bitmap src) {
        if (src == null) return null;
        try {
            int size = Math.min(src.getWidth(), src.getHeight());
            Bitmap output = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888);
            Canvas canvas = new Canvas(output);

            Paint paint = new Paint();
            paint.setAntiAlias(true);
            paint.setFilterBitmap(true);
            paint.setDither(true);
            paint.setColor(Color.BLACK);

            float radius = size / 2f;
            canvas.drawCircle(radius, radius, radius, paint);

            paint.setXfermode(new PorterDuffXfermode(PorterDuff.Mode.SRC_IN));

            int x = (src.getWidth() - size) / 2;
            int y = (src.getHeight() - size) / 2;
            Rect srcRect = new Rect(x, y, x + size, y + size);
            Rect destRect = new Rect(0, 0, size, size);
            canvas.drawBitmap(src, srcRect, destRect, paint);

            return output;
        } catch (Exception e) {
            Log.w(TAG, "Failed to circular mask avatar bitmap: " + e.getMessage());
            return src;
        }
    }
}
