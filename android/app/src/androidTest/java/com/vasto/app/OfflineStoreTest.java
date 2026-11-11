package com.vasto.app;

import android.content.Context;
import android.content.ContextWrapper;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import androidx.test.platform.app.InstrumentationRegistry;
import java.io.File;
import java.lang.reflect.Field;
import java.lang.reflect.Method;
import org.json.JSONObject;
import org.junit.After;
import org.junit.Before;
import org.junit.Test;
import static org.junit.Assert.*;

/** Executes real Android SQLite + Keystore operations in a separate test directory. */
public class OfflineStoreTest {
    private Context context;
    private File testRoot;
    private OfflineStorePlugin plugin;

    private Object invoke(OfflineStorePlugin instance, String name, Class<?>[] types, Object... args) throws Exception {
        Method method = OfflineStorePlugin.class.getDeclaredMethod(name, types); method.setAccessible(true);
        return method.invoke(instance, args);
    }
    private SQLiteDatabase database(OfflineStorePlugin instance) throws Exception {
        Field field = OfflineStorePlugin.class.getDeclaredField("db"); field.setAccessible(true); return (SQLiteDatabase) field.get(instance);
    }
    private OfflineStorePlugin create() throws Exception {
        OfflineStorePlugin instance = new OfflineStorePlugin() { @Override public Context getContext() { return context; } };
        invoke(instance, "open", new Class<?>[]{}); return instance;
    }
    @Before public void setup() throws Exception {
        Context target = InstrumentationRegistry.getInstrumentation().getTargetContext();
        testRoot = new File(target.getNoBackupFilesDir(), "offline-instrumentation-" + System.nanoTime());
        assertTrue(testRoot.mkdirs());
        context = new ContextWrapper(target) { @Override public File getNoBackupFilesDir() { return testRoot; } };
        plugin = create();
    }
    private void remove(File file) { File[] children=file.listFiles(); if(children!=null) for(File child:children)remove(child); file.delete(); }
    @After public void cleanup() throws Exception { if(plugin!=null) database(plugin).close(); remove(testRoot); }

    private boolean waitForView(androidx.test.core.app.ActivityScenario<MainActivity> scenario,String expression) throws Exception {
        long deadline=android.os.SystemClock.uptimeMillis()+15000;
        while(android.os.SystemClock.uptimeMillis()<deadline) {
            java.util.concurrent.CountDownLatch latch=new java.util.concurrent.CountDownLatch(1);
            java.util.concurrent.atomic.AtomicBoolean ready=new java.util.concurrent.atomic.AtomicBoolean();
            scenario.onActivity(activity->activity.getBridge().getWebView().evaluateJavascript(expression,value->{ready.set("true".equals(value));latch.countDown();}));
            latch.await(2,java.util.concurrent.TimeUnit.SECONDS);if(ready.get())return true;android.os.SystemClock.sleep(100);
        }
        return false;
    }
    private void deviceShell(String command) throws Exception {
        android.os.ParcelFileDescriptor descriptor=InstrumentationRegistry.getInstrumentation().getUiAutomation().executeShellCommand(command);
        try(java.io.InputStream input=new android.os.ParcelFileDescriptor.AutoCloseInputStream(descriptor)) {byte[] buffer=new byte[1024];while(input.read(buffer)!=-1){}}
    }
    @Test public void savedOriginalPageOpensWithoutInternetPermission() throws Exception {
        org.junit.Assume.assumeTrue(context.getPackageName().endsWith(".offlinecheck"));
        assertEquals(android.content.pm.PackageManager.PERMISSION_DENIED,context.checkSelfPermission(android.Manifest.permission.INTERNET));
        boolean wasInteractive=((android.os.PowerManager)context.getSystemService(Context.POWER_SERVICE)).isInteractive();
        deviceShell("input keyevent 224");
        try(androidx.test.core.app.ActivityScenario<MainActivity> scenario=androidx.test.core.app.ActivityScenario.launch(MainActivity.class)) {
            java.util.concurrent.atomic.AtomicReference<OfflineStorePlugin> actual=new java.util.concurrent.atomic.AtomicReference<>();
            scenario.onActivity(activity->actual.set((OfflineStorePlugin)activity.getBridge().getPlugin("OfflineStore").getInstance()));
            OfflineStorePlugin nativeStore=actual.get();invoke(nativeStore,"open",new Class<?>[]{});
            invoke(nativeStore,"set",new Class<?>[]{String.class,String.class},"owner","qa-only");
            String resource="aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa";
            File video=new File(database(nativeStore).getPath()).getParentFile();video=new File(video,resource+".asset");
            try(java.io.InputStream input=InstrumentationRegistry.getInstrumentation().getContext().getAssets().open("offline-video.mp4");java.io.FileOutputStream output=new java.io.FileOutputStream(video)) {
                byte[] buffer=new byte[8192];int n;while((n=input.read(buffer))!=-1)output.write(buffer,0,n);
            }
            invoke(nativeStore,"put",new Class<?>[]{String.class,Object.class,JSONObject.class,Long.class},"resources",resource,new JSONObject().put("type","video/mp4").put("bytes",video.length()),null);
            String html="<!doctype html><html data-theme='dark'><head><title>Saved page test</title><script src='/static/js/offline-view.js' defer></script></head><body><form id='snapshot-original-form' class='pwanimate-composer-box'><textarea id='messageInput'></textarea><button type='submit'>Send</button></form><video id='snapshot-video' controls muted src='https://localhost/_capacitor_file_"+video.getAbsolutePath()+"'></video></body></html>";
            invoke(nativeStore,"put",new Class<?>[]{String.class,Object.class,JSONObject.class,Long.class},"snapshots","/",new JSONObject().put("html",html).put("saved",System.currentTimeMillis()),null);
            scenario.onActivity(activity->{
                activity.getWindow().addFlags(android.view.WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON|android.view.WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON|android.view.WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED);
                if(android.os.Build.VERSION.SDK_INT>=27){activity.setShowWhenLocked(true);activity.setTurnScreenOn(true);}
                activity.getBridge().getWebView().getSettings().setMediaPlaybackRequiresUserGesture(false);
                activity.getBridge().getWebView().loadUrl("https://pwaninet.app/?offline_saved=1");
            });
            assertTrue("Original saved DOM must render without internet",waitForView(scenario,"Boolean(document.querySelector('#snapshot-original-form #messageInput') && document.documentElement.dataset.theme==='dark')"));
            assertTrue("Native video bytes must load without internet",waitForView(scenario,"Boolean(document.querySelector('#snapshot-video')?.readyState>=2)"));
            scenario.onActivity(activity->{activity.getBridge().getWebView().getSettings().setMediaPlaybackRequiresUserGesture(false);activity.getBridge().getWebView().evaluateJavascript("document.querySelector('#snapshot-video').muted=true;document.querySelector('#snapshot-video').play().catch(e=>window.playFailure=e.name+': '+e.message)",null);});
            boolean played=waitForView(scenario,"Boolean(document.querySelector('#snapshot-video')?.currentTime>0.1)");
            java.util.concurrent.CountDownLatch diagnosticLatch=new java.util.concurrent.CountDownLatch(1);
            java.util.concurrent.atomic.AtomicReference<String> diagnostic=new java.util.concurrent.atomic.AtomicReference<>("");
            scenario.onActivity(activity->activity.getBridge().getWebView().evaluateJavascript("JSON.stringify({error:window.playFailure,visible:document.visibilityState,focus:document.hasFocus(),paused:document.querySelector('#snapshot-video')?.paused,time:document.querySelector('#snapshot-video')?.currentTime,state:document.querySelector('#snapshot-video')?.readyState})",value->{diagnostic.set(value);diagnosticLatch.countDown();}));
            diagnosticLatch.await(2,java.util.concurrent.TimeUnit.SECONDS);
            assertTrue("Native saved video must play offline: "+diagnostic.get(),played);
            scenario.onActivity(activity->activity.getBridge().getWebView().evaluateJavascript("document.querySelector('#snapshot-video').currentTime=1.5",null));
            assertTrue("Native saved video must seek offline",waitForView(scenario,"Boolean(document.querySelector('#snapshot-video')?.currentTime>=1.5)"));
            invoke(nativeStore,"purge",new Class<?>[]{});
        } finally {if(!wasInteractive)deviceShell("input keyevent 223");}
    }
    @Test public void messagesAreEncryptedAndSurviveReopen() throws Exception {
        JSONObject message = new JSONObject().put("id", 42).put("text", "native-private-message");
        invoke(plugin, "set", new Class<?>[]{String.class,String.class}, "owner", "test-owner");
        invoke(plugin, "put", new Class<?>[]{String.class,Object.class,JSONObject.class,Long.class}, "messages", 42, message, 12L);
        try (Cursor cursor=database(plugin).rawQuery("SELECT payload FROM messages WHERE id=42",null)) {
            assertTrue(cursor.moveToFirst()); assertFalse(cursor.getString(0).contains("native-private-message"));
        }
        assertTrue(database(plugin).getPath().startsWith(testRoot.getAbsolutePath()));
        database(plugin).close(); plugin=create();
        try (Cursor cursor=database(plugin).rawQuery("SELECT payload FROM messages WHERE id=42",null)) {
            assertTrue(cursor.moveToFirst());
            String decrypted=(String)invoke(plugin,"decrypt",new Class<?>[]{String.class},cursor.getString(0));
            assertEquals("native-private-message",new JSONObject(decrypted).getString("text"));
        }
        assertEquals("test-owner", invoke(plugin,"get",new Class<?>[]{String.class},"owner"));
    }
    @Test public void interruptedTransactionDoesNotCommitMessage() throws Exception {
        SQLiteDatabase db=database(plugin); db.beginTransaction();
        try {
            invoke(plugin,"put",new Class<?>[]{String.class,Object.class,JSONObject.class,Long.class},"messages",43,new JSONObject().put("text","partial"),12L);
        } finally { db.endTransaction(); }
        try(Cursor cursor=db.rawQuery("SELECT COUNT(*) FROM messages",null)) { assertTrue(cursor.moveToFirst()); assertEquals(0,cursor.getInt(0)); }
    }
    @Test public void hiddenMessagesCannotBeRestoredByHistoryRefresh() throws Exception {
        database(plugin).execSQL("INSERT INTO hidden(id) VALUES (44)");
        invoke(plugin,"put",new Class<?>[]{String.class,Object.class,JSONObject.class,Long.class},
            "messages",44,new JSONObject().put("text","must stay hidden"),12L);
        try(Cursor cursor=database(plugin).rawQuery("SELECT COUNT(*) FROM messages WHERE id=44",null)) {
            assertTrue(cursor.moveToFirst()); assertEquals(0,cursor.getInt(0));
        }
    }
    @Test public void purgeRemovesAccountAndDownloads() throws Exception {
        invoke(plugin,"set",new Class<?>[]{String.class,String.class},"owner","test-owner");
        File media=new File(new File(testRoot,"offline-v1"),"test.mp4"); assertTrue(media.createNewFile());
        invoke(plugin,"purge",new Class<?>[]{});
        assertEquals("",invoke(plugin,"get",new Class<?>[]{String.class},"owner")); assertFalse(media.exists());
    }
}
