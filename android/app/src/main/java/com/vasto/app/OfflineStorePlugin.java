package com.vasto.app;

import android.content.ContentValues;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.net.Uri;
import android.security.keystore.KeyGenParameterSpec;
import android.security.keystore.KeyProperties;
import android.util.Base64;
import android.webkit.CookieManager;
import android.webkit.WebResourceResponse;
import java.security.MessageDigest;
import com.getcapacitor.*;
import com.getcapacitor.annotation.CapacitorPlugin;
import java.io.*;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.security.KeyStore;
import java.util.*;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import javax.crypto.Cipher;
import javax.crypto.KeyGenerator;
import javax.crypto.SecretKey;
import javax.crypto.spec.GCMParameterSpec;
import org.json.*;

/** Native, account-bound read cache. No arbitrary SQL, URL, or path is exposed to JS. */
@CapacitorPlugin(name = "OfflineStore")
public class OfflineStorePlugin extends Plugin {
    private final ExecutorService worker = Executors.newSingleThreadExecutor();
    private final ExecutorService readers = Executors.newSingleThreadExecutor();
    private final java.util.concurrent.atomic.AtomicLong generation = new java.util.concurrent.atomic.AtomicLong();
    private volatile boolean revoked;
    private long operationGeneration;
    private SQLiteDatabase db;
    private File directory;
    private SecretKey key;
    private long validatedAt;
    private static final long MEDIA_BUDGET = 150L * 1024 * 1024;
    private static final long FILE_LIMIT = 30L * 1024 * 1024;
    private interface Job { JSObject run() throws Exception; }

    private boolean sameOrigin(Uri a, Uri b) {
        return a != null && b != null && a.getUserInfo() == null &&
            Objects.equals(a.getScheme(), b.getScheme()) && Objects.equals(a.getHost(), b.getHost()) && a.getPort() == b.getPort();
    }
    public String hostedOrigin() {
        String server=getBridge().getServerUrl();
        if ((getContext().getApplicationInfo().flags & android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE)!=0 && server!=null) {
            Uri uri=Uri.parse(server);
            if (uri.getHost()!=null && uri.getUserInfo()==null && Arrays.asList("http","https").contains(uri.getScheme()))
                return uri.getScheme()+"://"+uri.getAuthority();
        }
        return "https://pwaninet.app";
    }
    public String localAssetOrigin() { return getBridge().getScheme()+"://"+getBridge().getHost(); }
    private boolean hostedOrigin(Uri uri) { return sameOrigin(uri,Uri.parse(hostedOrigin())); }
    private void run(PluginCall call, boolean hostedOnly, Job job) {
        getBridge().executeOnMainThread(() -> {
            Uri uri = Uri.parse(getBridge().getWebView().getUrl());
            boolean hosted = hostedOrigin(uri);
            boolean local = sameOrigin(uri,Uri.parse(localAssetOrigin()));
            if ((!hosted && !local) || (hostedOnly && !hosted)) { call.reject("Untrusted origin"); return; }
            boolean read = call.getMethodName().equals("status") || call.getMethodName().equals("history");
            long epoch = generation.get();
            (read ? readers : worker).execute(() -> {
                try {
                    open();
                    if (epoch != generation.get()) throw new IOException("Account changed");
                    if (revoked && read) { call.resolve(emptyResult()); return; }
                    if (revoked && !read && !(hosted && call.getMethodName().equals("refresh") && call.getString("owner") != null)) throw new IOException("Sign in online first");
                    if (!read) operationGeneration = epoch;
                    JSObject result;
                    if (read) {
                        db.beginTransactionNonExclusive();
                        try { result = job.run(); db.setTransactionSuccessful(); }
                        finally { db.endTransaction(); }
                    } else result = job.run();
                    if ((read ? epoch : operationGeneration) != generation.get() || (read && revoked)) { call.resolve(emptyResult()); return; }
                    call.resolve(result);
                }
                catch (Exception error) { call.reject("Offline operation failed: " + error.getMessage()); }
            });
        });
    }

    private synchronized void open() throws Exception {
        if (db != null) return;
        directory = new File(getContext().getNoBackupFilesDir(), "offline-v1");
        if (!directory.exists() && !directory.mkdirs()) throw new IOException("Storage unavailable");
        KeyStore keys = KeyStore.getInstance("AndroidKeyStore"); keys.load(null);
        if (!keys.containsAlias("vasto-offline-v1")) {
            KeyGenerator generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore");
            generator.init(new KeyGenParameterSpec.Builder("vasto-offline-v1", KeyProperties.PURPOSE_ENCRYPT | KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build());
            generator.generateKey();
        }
        key = (SecretKey) keys.getKey("vasto-offline-v1", null);
        db = SQLiteDatabase.openOrCreateDatabase(new File(directory, "reader.db"), null);
        if (db.getVersion() > 1) throw new IOException("Offline database requires a newer app");
        db.execSQL("CREATE TABLE IF NOT EXISTS kv (id TEXT PRIMARY KEY, payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS conversations (id INTEGER PRIMARY KEY, payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS messages (id INTEGER PRIMARY KEY, conversation INTEGER NOT NULL, payload TEXT NOT NULL)");
        db.execSQL("CREATE INDEX IF NOT EXISTS message_history ON messages(conversation,id)");
        db.execSQL("CREATE TABLE IF NOT EXISTS snapshots (id TEXT PRIMARY KEY, payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS resources (id TEXT PRIMARY KEY, payload TEXT NOT NULL)");
        db.execSQL("CREATE TABLE IF NOT EXISTS hidden (id INTEGER PRIMARY KEY)");
        db.execSQL("CREATE TABLE IF NOT EXISTS reels (id TEXT PRIMARY KEY, payload TEXT NOT NULL, saved INTEGER NOT NULL)");
        if (db.getVersion() == 0) db.setVersion(1);
        File[] leftovers = directory.listFiles((dir, name) -> name.endsWith(".part"));
        if (leftovers != null) for (File file : leftovers) file.delete();
    }

    private String encrypt(String value) throws Exception {
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding"); cipher.init(Cipher.ENCRYPT_MODE, key);
        return Base64.encodeToString(cipher.getIV(), Base64.NO_WRAP) + ":" +
            Base64.encodeToString(cipher.doFinal(value.getBytes(StandardCharsets.UTF_8)), Base64.NO_WRAP);
    }
    private String decrypt(String value) throws Exception {
        String[] parts = value.split(":", 2);
        Cipher cipher = Cipher.getInstance("AES/GCM/NoPadding");
        cipher.init(Cipher.DECRYPT_MODE, key, new GCMParameterSpec(128, Base64.decode(parts[0], Base64.NO_WRAP)));
        return new String(cipher.doFinal(Base64.decode(parts[1], Base64.NO_WRAP)), StandardCharsets.UTF_8);
    }
    private String get(String id) throws Exception {
        try (Cursor cursor = db.rawQuery("SELECT payload FROM kv WHERE id=?", new String[]{id})) {
            return cursor.moveToFirst() ? decrypt(cursor.getString(0)) : "";
        }
    }
    private void put(String table, Object id, JSONObject value, Long conversation) throws Exception {
        if (table.equals("messages")) {
            try (Cursor hidden = db.rawQuery("SELECT id FROM hidden WHERE id=?", new String[]{String.valueOf(id)})) {
                if (hidden.moveToFirst()) return;
            }
        }
        ContentValues row = new ContentValues(); row.put("id", String.valueOf(id)); row.put("payload", encrypt(value.toString()));
        if (conversation != null) row.put("conversation", conversation);
        if (table.equals("reels")) row.put("saved", System.currentTimeMillis());
        db.insertWithOnConflict(table, null, row, SQLiteDatabase.CONFLICT_REPLACE);
    }
    private void set(String id, String value) throws Exception {
        ContentValues row = new ContentValues(); row.put("id", id); row.put("payload", encrypt(value));
        db.insertWithOnConflict("kv", null, row, SQLiteDatabase.CONFLICT_REPLACE);
    }
    private void purge() {
        db.beginTransaction();
        try { for (String table : new String[]{"messages", "conversations", "kv", "reels", "hidden", "snapshots", "resources"}) db.delete(table, null, null); db.setTransactionSuccessful(); }
        finally { db.endTransaction(); }
        File[] files = directory.listFiles((dir, name) -> name.endsWith(".mp4") || name.endsWith(".part") || name.endsWith(".asset"));
        if (files != null) for (File file : files) file.delete();
        validatedAt = 0;
    }
    private JSObject emptyResult() {
        JSObject result = new JSObject(); result.put("owner", ""); result.put("name", ""); result.put("route", "/");
        result.put("conversations", new JSONArray()); result.put("reels", new JSONArray()); result.put("messages", new JSONArray()); return result;
    }
    public void clearForSignOut() {
        revoked = true; generation.incrementAndGet();
        notifyListeners("invalidated", new JSObject());
        worker.execute(() -> { try { open(); purge(); } catch (Exception ignored) {} });
    }
    private HttpURLConnection connection(String path) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(hostedOrigin() + path).openConnection();
        connection.setInstanceFollowRedirects(false); connection.setConnectTimeout(10000); connection.setReadTimeout(20000);
        connection.setRequestProperty("Accept", "application/json");
        String cookies = CookieManager.getInstance().getCookie(hostedOrigin());
        if (cookies != null) connection.setRequestProperty("Cookie", cookies);
        return connection;
    }
    private JSONObject remote(String suffix) throws Exception {
        HttpURLConnection connection = connection("/offline/native-state/" + suffix);
        try {
            int status = connection.getResponseCode();
            if (status == 401 || status == 403) { revoked = true; generation.incrementAndGet(); purge(); throw new IOException("Sign in online to refresh"); }
            if (status != 200) throw new IOException("Server unavailable (" + status + ")");
            try (InputStream stream = connection.getInputStream()) {
                ByteArrayOutputStream bytes = new ByteArrayOutputStream(); byte[] buffer = new byte[8192]; int n;
                while ((n = stream.read(buffer)) != -1) { bytes.write(buffer, 0, n); if (bytes.size() > 4 * 1024 * 1024) throw new IOException("Response too large"); }
                return new JSONObject(bytes.toString("UTF-8"));
            }
        } finally { connection.disconnect(); }
    }
    private JSONArray payloads(String query, String[] args) throws Exception {
        JSONArray result = new JSONArray();
        try (Cursor cursor = db.rawQuery(query, args)) { while (cursor.moveToNext()) result.put(new JSONObject(decrypt(cursor.getString(0)))); }
        return result;
    }
    private void dropReel(String id) {
        db.delete("reels", "id=?", new String[]{id}); new File(directory, id + ".mp4").delete();
    }

    private JSONObject sync(String expectedOwner) throws Exception {
        JSONArray savedReels = payloads("SELECT payload FROM reels", null);
        ArrayList<String> reelIds = new ArrayList<>();
        for (int i=0; i<savedReels.length(); i++) reelIds.add(savedReels.getJSONObject(i).getString("id"));
        JSONObject state = remote("?reels=" + String.join(",", reelIds));
        if (operationGeneration != generation.get()) throw new IOException("Account changed");
        String owner = state.getString("owner");
        if (!get("owner").equals(owner)) {
            revoked = true;
            operationGeneration = generation.incrementAndGet();
            purge();
        }
        if (expectedOwner != null && !expectedOwner.equals(owner)) throw new IOException("Account changed");
        db.beginTransaction();
        try {
            set("owner", owner); set("name", state.getString("name")); set("origins", state.optJSONArray("asset_origins") == null ? "[]" : state.getJSONArray("asset_origins").toString()); revoked = false;
            JSONArray conversations = state.getJSONArray("conversations"); Set<String> allowed = new HashSet<>();
            for (int i=0; i<conversations.length(); i++) {
                JSONObject conversation = conversations.getJSONObject(i); allowed.add(conversation.getString("id"));
                put("conversations", conversation.getLong("id"), conversation, null);
            }
            try (Cursor cursor = db.rawQuery("SELECT id FROM conversations", null)) {
                while (cursor.moveToNext()) if (!allowed.contains(cursor.getString(0))) {
                    String[] args = {cursor.getString(0)}; db.delete("messages", "conversation=?", args); db.delete("conversations", "id=?", args);
                }
            }
            Set<String> groups=new HashSet<>();JSONArray groupIds=state.optJSONArray("groups");
            if(groupIds!=null)for(int i=0;i<groupIds.length();i++)groups.add(groupIds.getString(i));
            try(Cursor snapshots=db.rawQuery("SELECT id,payload FROM snapshots",null)) {
                while(snapshots.moveToNext()) {
                    String route=snapshots.getString(0);
                    java.util.regex.Matcher chatRoute=java.util.regex.Pattern.compile("^/messaging/conversation/([0-9]+)/").matcher(route);
                    java.util.regex.Matcher groupRoute=java.util.regex.Pattern.compile("^/groups/([0-9]+)/").matcher(route);
                    JSONObject snapshot=new JSONObject(decrypt(snapshots.getString(1)));
                    if((chatRoute.find()&&!allowed.contains(chatRoute.group(1))) ||
                       (snapshot.optBoolean("groupProtected")&&groupRoute.find()&&!groups.contains(groupRoute.group(1)))) {
                        db.delete("snapshots","id=?",new String[]{route});
                        if(route.equals(get("snapshotRoute")))set("snapshotRoute","");
                    }
                }
            }
            Set<String> media = new HashSet<>(); JSONArray accepted = state.getJSONArray("reels");
            for (int i=0; i<accepted.length(); i++) media.add(accepted.getString(i));
            for (String id : reelIds) if (!media.contains(id)) dropReel(id);
            db.setTransactionSuccessful();
        } finally { db.endTransaction(); }
        // Reconcile retained edits/deletions in bounded batches, including messages no longer returned.
        ArrayList<String> ids = new ArrayList<>();
        try (Cursor cursor = db.rawQuery("SELECT id FROM messages ORDER BY id", null)) { while (cursor.moveToNext()) ids.add(cursor.getString(0)); }
        for (int start=0; start<ids.size(); start+=500) {
            List<String> batch = ids.subList(start, Math.min(start+500, ids.size()));
            JSONObject update = remote("?messages=" + String.join(",", batch));
            if (operationGeneration != generation.get()) throw new IOException("Account changed");
            if (!owner.equals(update.getString("owner"))) { revoked = true; generation.incrementAndGet(); purge(); throw new IOException("Account changed"); }
            Map<String, JSONObject> revisions = new HashMap<>(); JSONArray rows = update.getJSONArray("revisions");
            for (int i=0; i<rows.length(); i++) { JSONObject row = rows.getJSONObject(i); revisions.put(row.getString("id"), row); }
            db.beginTransaction();
            try {
                for (String id : batch) {
                    JSONObject revision = revisions.get(id);
                    if (revision == null) { db.delete("messages", "id=?", new String[]{id}); continue; }
                    JSONArray stored = payloads("SELECT payload FROM messages WHERE id=?", new String[]{id});
                    if (stored.length() == 0) continue;
                    JSONObject message = stored.getJSONObject(0);
                    message.put("reply", revision.optJSONObject("reply"));
                    if (revision.getBoolean("deleted") || !revision.getString("version").equals(message.optString("version"))) {
                        message.put("text", revision.getBoolean("deleted") ? "This message was deleted" : "Message updated. Open online to refresh.");
                        message.remove("reply"); message.put("version", revision.getString("version"));
                    }
                    put("messages", id, message, revision.getLong("conversation"));
                }
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
        }
        validatedAt = System.currentTimeMillis();
        return state;
    }

    @PluginMethod public void refresh(PluginCall call) { run(call, false, () -> { sync(call.getString("owner")); return summary(); }); }
    private JSObject summary() throws Exception {
        JSObject result = new JSObject(); result.put("owner", get("owner")); result.put("name", get("name"));
        result.put("origin", hostedOrigin()); result.put("snapshotRoute", get("snapshotRoute"));
        result.put("route", get("route")); result.put("theme", get("theme"));
        String positions = get("positions"); result.put("positions", positions.isEmpty() ? new JSONObject() : new JSONObject(positions));
        result.put("conversations", payloads("SELECT payload FROM conversations ORDER BY id DESC", null));
        JSONArray reels = payloads("SELECT payload FROM reels ORDER BY saved DESC", null);
        for (int i=0; i<reels.length(); i++) {
            JSONObject reel = reels.getJSONObject(i); File file = new File(directory, reel.getString("id") + ".mp4");
            reel.put("path", file.exists() ? file.getAbsolutePath() : "");
        }
        result.put("reels", reels); return result;
    }
    @PluginMethod public void status(PluginCall call) { run(call, false, this::summary); }
    @PluginMethod public void clear(PluginCall call) {
        getBridge().executeOnMainThread(() -> {
            Uri uri = Uri.parse(getBridge().getWebView().getUrl());
            if (!"https".equals(uri.getScheme()) || uri.getPort() != -1 ||
                    !(hostedOrigin(uri) || "localhost".equals(uri.getHost()))) {
                call.reject("Untrusted origin"); return;
            }
            clearForSignOut(); call.resolve(emptyResult());
        });
    }
    @PluginMethod public void history(PluginCall call) {
        run(call, false, () -> {
            long conversation = call.getLong("conversation", -1L), before = call.getLong("before", Long.MAX_VALUE);
            JSObject result = new JSObject(); result.put("messages", payloads(
                "SELECT payload FROM (SELECT id,payload FROM messages WHERE conversation=? AND id<? AND id NOT IN (SELECT id FROM hidden) ORDER BY id DESC LIMIT 100) ORDER BY id",
                new String[]{String.valueOf(conversation), String.valueOf(before)})); return result;
        });
    }
    @PluginMethod public void refreshHistory(PluginCall call) {
        run(call, false, () -> {
            String owner = get("owner");
            if (owner.isEmpty()) throw new IOException("Sign in online first");
            long conversation = call.getLong("conversation", -1L), before = call.getLong("before", Long.MAX_VALUE);
            JSONObject state = remote("?conversation=" + conversation + "&before=" + before);
            if (operationGeneration != generation.get()) throw new IOException("Account changed");
            if (!owner.equals(state.getString("owner"))) { revoked = true; generation.incrementAndGet(); purge(); throw new IOException("Account changed"); }
            JSONArray messages = state.getJSONArray("messages");
            db.beginTransaction();
            try {
                for (int i=0; i<messages.length(); i++) {
                    JSONObject message = messages.getJSONObject(i);
                    // A previously rendered/decrypted body is usable if the server version still matches.
                    JSONArray old = payloads("SELECT payload FROM messages WHERE id=?", new String[]{message.getString("id")});
                    if (message.optBoolean("encrypted") && old.length()>0 &&
                        old.getJSONObject(0).optString("version").equals(message.optString("version"))) {
                        JSONObject retained = old.getJSONObject(0); retained.put("reply", message.optJSONObject("reply"));
                        put("messages", message.getLong("id"), retained, conversation); continue;
                    }
                    put("messages", message.getLong("id"), message, conversation);
                }
                db.execSQL("DELETE FROM messages WHERE conversation=? AND id NOT IN (SELECT id FROM messages WHERE conversation=? ORDER BY id DESC LIMIT 2000)", new Object[]{conversation, conversation});
                db.execSQL("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 20000)");
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
            JSObject result = new JSObject(); result.put("messages", messages); return result;
        });
    }
    @PluginMethod public void remember(PluginCall call) {
        run(call, false, () -> {
            if (get("owner").isEmpty()) return new JSObject();
            String route = call.getString("route", "");
            if (route.matches("/(messaging(/conversation/[0-9]+)?/|reels/)?")) set("route", route);
            set("theme", "dark".equals(call.getString("theme")) ? "dark" : "light");
            if (call.getInt("position") != null && route.matches("/(messaging(/conversation/[0-9]+)?/|reels/)?")) {
                String saved = get("positions"); JSONObject positions = saved.isEmpty() ? new JSONObject() : new JSONObject(saved);
                positions.put(route, Math.max(0, call.getInt("position")));
                if (positions.length() > 30) positions.remove(positions.keys().next());
                set("positions", positions.toString());
            }
            return new JSObject();
        });
    }
    @PluginMethod public void hideMessages(PluginCall call) {
        run(call, true, () -> {
            if (!get("owner").equals(call.getString("owner", ""))) throw new IOException("Account changed");
            JSArray ids = call.getArray("ids", new JSArray());
            if (ids.length()>20000) throw new IOException("Batch too large");
            db.beginTransaction();
            try {
                for(int i=0;i<ids.length();i++) {
                    long id=ids.getLong(i); if(id<=0)continue;
                    db.execSQL("INSERT OR IGNORE INTO hidden(id) VALUES (?)", new Object[]{id});
                    db.delete("messages", "id=?", new String[]{String.valueOf(id)});
                }
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
            return new JSObject();
        });
    }
    @PluginMethod public void saveMessages(PluginCall call) {
        run(call, true, () -> {
            String owner = call.getString("owner", "");
            if (validatedAt == 0 || System.currentTimeMillis()-validatedAt > 60000) sync(owner);
            if (owner.isEmpty() || !owner.equals(get("owner"))) throw new IOException("Account changed");
            long conversation = call.getLong("conversation", -1L);
            try (Cursor cursor = db.rawQuery("SELECT id FROM conversations WHERE id=?", new String[]{String.valueOf(conversation)})) {
                if (!cursor.moveToFirst()) throw new IOException("Conversation unavailable");
            }
            JSArray messages = call.getArray("messages", new JSArray());
            if (messages.length() > 2000) throw new IOException("Batch too large");
            db.beginTransaction();
            try {
                for (int i=0; i<messages.length(); i++) {
                    JSONObject message = messages.getJSONObject(i); long id = message.getLong("id");
                    if (id <= 0 || message.toString().length() > 50000) continue;
                    put("messages", id, message, conversation);
                }
                db.execSQL("DELETE FROM messages WHERE conversation=? AND id NOT IN (SELECT id FROM messages WHERE conversation=? ORDER BY id DESC LIMIT 2000)", new Object[]{conversation, conversation});
                db.execSQL("DELETE FROM messages WHERE id NOT IN (SELECT id FROM messages ORDER BY id DESC LIMIT 20000)");
                db.setTransactionSuccessful();
            } finally { db.endTransaction(); }
            return new JSObject();
        });
    }
    @PluginMethod public void saveReel(PluginCall call) {
        run(call, true, () -> {
            String owner = call.getString("owner", ""), id = UUID.fromString(call.getString("id", "")).toString();
            sync(owner);
            try (Cursor saved = db.rawQuery("SELECT id FROM reels WHERE id=?", new String[]{id})) {
                if (saved.moveToFirst() && new File(directory, id + ".mp4").isFile()) return summary();
            }
            HttpURLConnection connection = connection("/offline/native-reel/" + id + "/");
            File partial = new File(directory, id + ".part");
            File target = new File(directory, id + ".mp4");
            boolean committed = false;
            try {
                connection.setRequestProperty("Accept", "video/*");
                if (connection.getResponseCode() != 200 || !owner.equals(connection.getHeaderField("X-Vasto-Account"))) throw new IOException("Video unavailable");
                if (!String.valueOf(connection.getContentType()).startsWith("video/")) throw new IOException("Invalid video");
                long expected = connection.getContentLengthLong(), total=0;
                if (expected > FILE_LIMIT) throw new IOException("Video exceeds the 30 MB offline limit");
                try (InputStream input = connection.getInputStream(); FileOutputStream output = new FileOutputStream(partial)) {
                    byte[] buffer = new byte[65536]; int n;
                    while ((n=input.read(buffer)) != -1) { total+=n; if (total>FILE_LIMIT) throw new IOException("Video too large"); output.write(buffer,0,n); }
                    output.getFD().sync();
                }
                if (total==0 || (expected>=0 && total!=expected)) throw new IOException("Incomplete download");
                if (!owner.equals(remote("").getString("owner"))) { revoked = true; generation.incrementAndGet(); purge(); throw new IOException("Account changed"); }
                if (operationGeneration != generation.get()) throw new IOException("Account changed");
                if (!partial.renameTo(target)) throw new IOException("Could not commit download");
                JSONObject reel = new JSONObject(); reel.put("id", id); reel.put("caption", call.getString("caption", "").substring(0, Math.min(1000, call.getString("caption", "").length()))); reel.put("bytes", total);
                put("reels", id, reel, null); committed = true;
                JSONArray saved = payloads("SELECT payload FROM reels ORDER BY saved DESC", null); long used=0;
                for (int i=0; i<saved.length(); i++) { JSONObject item=saved.getJSONObject(i); used+=item.getLong("bytes"); if(i>=5 || used>MEDIA_BUDGET) dropReel(item.getString("id")); }
                return summary();
            } finally { connection.disconnect(); partial.delete(); if (!committed) target.delete(); }
        });
    }
    @PluginMethod public void deleteReel(PluginCall call) {
        run(call, false, () -> { dropReel(UUID.fromString(call.getString("id", "")).toString()); return summary(); });
    }
    private String routeKey(String value) throws Exception {
        Uri uri = Uri.parse(value);
        if ((uri.getHost()!=null && !hostedOrigin(uri)) ||
            uri.getEncodedPath()==null || !uri.getEncodedPath().startsWith("/")) throw new IOException("Invalid saved route");
        Uri.Builder route=new Uri.Builder().encodedPath(uri.getEncodedPath());
        for(String name : new TreeSet<>(uri.getQueryParameterNames())) {
            if(Arrays.asList("offline_saved","cached","view_cache").contains(name))continue;
            for(String item:uri.getQueryParameters(name)) route.appendQueryParameter(name,item);
        }
        return route.build().toString();
    }
    private String resourceId(String url) throws Exception {
        byte[] bytes=MessageDigest.getInstance("SHA-256").digest(url.getBytes(StandardCharsets.UTF_8));
        StringBuilder id=new StringBuilder(); for(byte item:bytes)id.append(String.format("%02x",item&255)); return id.toString();
    }
    @PluginMethod public void cacheResource(PluginCall call) {
        run(call,true,()-> {
            if(!get("owner").equals(call.getString("owner","")) || get("owner").isEmpty())throw new IOException("Account changed");
            String value=call.getString("url",""); Uri uri=Uri.parse(value);
            String origin=uri.getScheme()+"://"+uri.getAuthority();
            JSONArray origins=new JSONArray(get("origins").isEmpty()?"[]":get("origins"));
            boolean permitted=origin.equals(hostedOrigin());
            for(int i=0;i<origins.length();i++)permitted|=origin.equals(origins.getString(i));
            if(!permitted || (!"https".equals(uri.getScheme()) && !origin.equals(hostedOrigin())) || uri.getUserInfo()!=null)throw new IOException("Untrusted asset origin");
            String id=resourceId(value); File file=new File(directory,id+".asset");
            JSONArray stored=payloads("SELECT payload FROM resources WHERE id=?",new String[]{id});
            JSONObject metadata=stored.length()>0?stored.getJSONObject(0):null;
            if(metadata==null || !file.isFile() || System.currentTimeMillis()-file.lastModified()>3600000) {
                HttpURLConnection request=(HttpURLConnection)new URL(value).openConnection();
                request.setInstanceFollowRedirects(false);request.setConnectTimeout(10000);request.setReadTimeout(20000);
                if(origin.equals(hostedOrigin())) {
                    String cookies=CookieManager.getInstance().getCookie(origin);
                    if(cookies!=null)request.setRequestProperty("Cookie",cookies);
                }
                File part=new File(directory,id+".part");
                try {
                    if(request.getResponseCode()!=200)throw new IOException("Asset unavailable");
                    String type=String.valueOf(request.getContentType()).split(";")[0].trim();
                    if(!(type.startsWith("image/") || type.startsWith("video/") || type.startsWith("audio/") ||
                         type.startsWith("font/") || type.equals("text/css") || type.equals("application/font-woff") ||
                         type.equals("application/vnd.ms-fontobject") || type.equals("application/octet-stream")))throw new IOException("Unsupported asset type");
                    long limit=type.startsWith("video/")||type.startsWith("audio/")?30L*1024*1024:8L*1024*1024;
                    long size=0,expected=request.getContentLengthLong();if(expected>limit)throw new IOException("Asset too large");
                    try(InputStream input=request.getInputStream();FileOutputStream output=new FileOutputStream(part)) {
                        byte[] buffer=new byte[65536];int n;
                        while((n=input.read(buffer))!=-1){size+=n;if(size>limit)throw new IOException("Asset too large");output.write(buffer,0,n);}
                        output.getFD().sync();
                    }
                    if(size==0 || (expected>=0 && expected!=size) || operationGeneration!=generation.get())throw new IOException("Incomplete asset");
                    if(!part.renameTo(file))throw new IOException("Asset commit failed");
                    metadata=new JSONObject().put("type",type).put("bytes",size);
                    put("resources",id,metadata,null);
                } finally {request.disconnect();part.delete();}
            }
            JSObject result=new JSObject();result.put("path",localAssetOrigin()+"/_capacitor_file_"+file.getAbsolutePath());result.put("type",metadata.getString("type"));
            if(metadata.getString("type").equals("text/css")) {
                try(InputStream input=new FileInputStream(file)) {
                    ByteArrayOutputStream text=new ByteArrayOutputStream();byte[] buffer=new byte[8192];int n;
                    while((n=input.read(buffer))!=-1)text.write(buffer,0,n);
                    result.put("text",text.toString("UTF-8"));
                }
            }
            return result;
        });
    }
    @PluginMethod public void saveSnapshot(PluginCall call) {
        run(call,true,()-> {
            if(!get("owner").equals(call.getString("owner","")) || get("owner").isEmpty())throw new IOException("Account changed");
            String route=routeKey(call.getString("url","")),html=call.getString("html","");
            if(html.isEmpty() || html.length()>8000000)throw new IOException("Snapshot too large");
            put("snapshots",route,new JSONObject().put("html",html).put("groupProtected",call.getBoolean("groupProtected",false)).put("saved",System.currentTimeMillis()),null);
            set("snapshotRoute",route);
            JSONArray snapshots=payloads("SELECT payload FROM snapshots",null);
            // SQLite's encrypted payload size gives a bounded retention budget.
            try(Cursor rows=db.rawQuery("SELECT id,length(payload) FROM snapshots ORDER BY rowid DESC",null)) {
                long total=0;int count=0;
                while(rows.moveToNext()){total+=rows.getLong(1);if(++count>100 || total>80L*1024*1024)db.delete("snapshots","id=?",new String[]{rows.getString(0)});}
            }
            long total=0;
            try(Cursor rows=db.rawQuery("SELECT id,payload FROM resources ORDER BY rowid DESC",null)) {
                while(rows.moveToNext()) {
                    JSONObject item=new JSONObject(decrypt(rows.getString(1)));total+=item.optLong("bytes");
                    if(total>300L*1024*1024){new File(directory,rows.getString(0)+".asset").delete();db.delete("resources","id=?",new String[]{rows.getString(0)});}
                }
            }
            return new JSObject();
        });
    }
    @PluginMethod public void rememberSnapshot(PluginCall call) {
        run(call,false,()-> {
            if(get("owner").isEmpty())return new JSObject();
            String route=routeKey(call.getString("url",""));
            set("scroll:"+route,call.getString("scroll","{}"));
            return new JSObject();
        });
    }
    public String lastSnapshotRoute() {
        try {open();return revoked?"":get("snapshotRoute");}catch(Exception ignored){return "";}
    }
    public WebResourceResponse savedPage(String value) {
        try {
            open();long epoch=generation.get();if(revoked || get("owner").isEmpty())return null;
            String route=routeKey(value);
            JSONArray rows=payloads("SELECT payload FROM snapshots WHERE id=?",new String[]{route});
            if(rows.length()==0)return null;
            Map<String,String> headers=new HashMap<>();headers.put("Cache-Control","no-store");headers.put("X-Vasto-Saved-At",String.valueOf(rows.getJSONObject(0).getLong("saved")));
            headers.put("Content-Security-Policy","default-src 'none'; style-src 'self' data: 'unsafe-inline'; img-src 'self' "+localAssetOrigin()+" data: blob:; font-src 'self' "+localAssetOrigin()+" data:; media-src 'self' "+localAssetOrigin()+" blob: data:; script-src "+hostedOrigin()+"/static/js/offline-view.js "+hostedOrigin()+"/static/js/bootstrap.bundle.min.js; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-src 'none'");
            String html=rows.getJSONObject(0).getString("html");
            String position=get("scroll:"+route);
            if(!position.isEmpty())html=html.replace("</head>","<meta name=\"vasto-scroll\" content=\""+android.text.TextUtils.htmlEncode(position)+"\"></head>");
            if(revoked || epoch!=generation.get())return null;
            return new WebResourceResponse("text/html","UTF-8",200,"OK",headers,new ByteArrayInputStream(html.getBytes(StandardCharsets.UTF_8)));
        }catch(Exception ignored){return null;}
    }
    public String resourceMime(String id) {
        try {open();if(revoked || get("owner").isEmpty())return null;
            JSONArray rows=payloads("SELECT payload FROM resources WHERE id=?",new String[]{id});
            return rows.length()>0?rows.getJSONObject(0).getString("type"):null;
        }catch(Exception ignored){return null;}
    }
    public WebResourceResponse savedResource(String id) {
        try {
            open();if(revoked || get("owner").isEmpty() || !id.matches("[a-f0-9]{64}"))return null;
            JSONArray rows=payloads("SELECT payload FROM resources WHERE id=?",new String[]{id});
            File file=new File(directory,id+".asset");if(rows.length()==0 || !file.isFile())return null;
            return new WebResourceResponse(rows.getJSONObject(0).getString("type"),null,new FileInputStream(file));
        }catch(Exception ignored){return null;}
    }
    @Override protected void handleOnDestroy() { worker.shutdown(); readers.shutdown(); }
}
