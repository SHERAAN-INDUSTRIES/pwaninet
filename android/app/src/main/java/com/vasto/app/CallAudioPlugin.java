package com.vasto.app;

import android.content.Context;
import android.media.AudioDeviceInfo;
import android.media.AudioManager;
import android.os.Build;
import com.getcapacitor.JSObject;
import com.getcapacitor.Plugin;
import com.getcapacitor.PluginCall;
import com.getcapacitor.PluginMethod;
import com.getcapacitor.annotation.CapacitorPlugin;

/** Foreground VoIP audio routing only; no incoming push/background-call handling. */
@CapacitorPlugin(name = "CallAudio")
public class CallAudioPlugin extends Plugin {
    private Integer previousMode;
    private boolean previousSpeaker;
    private AudioManager audio() { return (AudioManager) getContext().getSystemService(Context.AUDIO_SERVICE); }

    @PluginMethod public void setSpeaker(PluginCall call) {
        getBridge().executeOnMainThread(() -> {
            AudioManager manager = audio();
            boolean enabled = call.getBoolean("enabled", false);
            try {
                if (previousMode == null) { previousMode = manager.getMode(); previousSpeaker = manager.isSpeakerphoneOn(); }
                manager.setMode(AudioManager.MODE_IN_COMMUNICATION);
                if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
                    AudioDeviceInfo target = null;
                    for (AudioDeviceInfo device : manager.getAvailableCommunicationDevices()) {
                        if (device.getType() == (enabled ? AudioDeviceInfo.TYPE_BUILTIN_SPEAKER : AudioDeviceInfo.TYPE_BUILTIN_EARPIECE)) { target = device; break; }
                    }
                    if (target != null) {
                        if (!manager.setCommunicationDevice(target)) { call.reject("Audio route was rejected by the device."); return; }
                    } else if (!enabled) manager.clearCommunicationDevice();
                    else { call.reject("No built-in speaker route is available."); return; }
                } else manager.setSpeakerphoneOn(enabled);
                JSObject result = new JSObject(); result.put("speaker", enabled); call.resolve(result);
            } catch (Exception error) { call.reject("Could not change call audio routing.", error); }
        });
    }
    @PluginMethod public void reset(PluginCall call) {
        getBridge().executeOnMainThread(() -> { restore(); call.resolve(); });
    }
    private void restore() {
        if (previousMode == null) return;
        AudioManager manager = audio();
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) manager.clearCommunicationDevice();
        else manager.setSpeakerphoneOn(previousSpeaker);
        manager.setMode(previousMode); previousMode = null;
    }
    @Override protected void handleOnDestroy() { restore(); }
}
