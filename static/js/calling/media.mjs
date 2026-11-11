/** LiveKit adapter. UI never owns room objects or microphone tracks. */
export class VoiceMedia {
  constructor({ sdkUrl, audioHost }) { Object.assign(this, { sdkUrl, audioHost }); this.generation = 0; this.room = null; this.track = null; }
  async sdk() { return this.library ??= await import(this.sdkUrl); }
  async prepare() {
    if (this.track) return;
    const generation = this.generation;
    const sdk = await this.sdk();
    if (!navigator.mediaDevices?.getUserMedia) throw new Error('Microphone access requires a secure connection and a supported browser.');
    let blocked = false;
    try { blocked = (await navigator.permissions.query({ name: 'microphone' })).state === 'denied'; } catch (_) {}
    try {
      const track = await sdk.createLocalAudioTrack({ echoCancellation: true, noiseSuppression: true });
      if (generation !== this.generation) { track.stop(); return; }
      this.track = track;
    } catch (error) { error.permissionBlocked = blocked || error.name === 'NotAllowedError'; throw error; }
  }
  async connect(details, callbacks, preferences = {}) {
    const generation = this.generation;
    await this.prepare();
    if (generation !== this.generation) return;
    const { Room, RoomEvent, Track } = await this.sdk();
    const room = this.room = new Room({ adaptiveStream: false, dynacast: false });
    let published = false, remote = false, ready = false;
    const check = () => { if (published && remote && !ready && generation === this.generation) { ready = true; callbacks.ready(); } };
    room.on(RoomEvent.TrackSubscribed, track => {
      if (generation !== this.generation || track.kind !== Track.Kind.Audio) return;
      this.audioHost.append(track.attach()); remote = true; check();
    });
    room.on(RoomEvent.TrackUnsubscribed, track => track.detach().forEach(el => el.remove()));
    room.on(RoomEvent.Reconnecting, callbacks.reconnecting);
    room.on(RoomEvent.SignalReconnecting, callbacks.reconnecting);
    room.on(RoomEvent.Reconnected, callbacks.reconnected);
    room.on(RoomEvent.Disconnected, callbacks.disconnected);
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => callbacks.playback(!room.canPlaybackAudio));
    room.on(RoomEvent.ParticipantDisconnected, callbacks.reconnecting);
    room.on(RoomEvent.ParticipantConnected, () => { if (ready) callbacks.reconnected(); });
    await room.connect(details.server_url, details.participant_token, { autoSubscribe: true });
    if (generation !== this.generation) { await room.disconnect(); return; }
    if (preferences.muted) await this.track.mute();
    await room.localParticipant.publishTrack(this.track, { source: Track.Source.Microphone });
    if (generation !== this.generation) { await room.disconnect(); return; }
    if (preferences.output && preferences.output !== 'default') { try { await this.output(preferences.output); } catch (_) {} }
    if (preferences.speaker) { try { await this.speaker(true); } catch (_) { callbacks.routing?.({ speaker: false, output: 'default', error: 'Speaker routing could not be restored. Use your device audio controls.' }); } }
    published = true; check();
    try { await room.startAudio(); } catch (_) { callbacks.playback(true); }
  }
  async mute(muted) { if (!this.track) throw new Error('Microphone is unavailable.'); await (muted ? this.track.mute() : this.track.unmute()); }
  async outputs() {
    if (!('setSinkId' in HTMLMediaElement.prototype)) return [];
    return (await navigator.mediaDevices.enumerateDevices()).filter(device => device.kind === 'audiooutput');
  }
  async output(id) { if (!this.room) throw new Error('Call not connected.'); await this.room.switchActiveDevice('audiooutput', id); }
  nativeAudio() {
    if (!window.Capacitor?.isNativePlatform?.()) return null;
    return window.Capacitor.Plugins?.CallAudio || (window.Capacitor.registerPlugin ? window.Capacitor.registerPlugin('CallAudio') : null);
  }
  async speaker(enabled) {
    const native = this.nativeAudio();
    if (native) return native.setSpeaker({ enabled });
    if (!enabled) { await this.output('default'); return { speaker: false, output: 'default' }; }
    const devices = await this.outputs();
    let device = devices.find(d => /speaker|loudspeaker/i.test(d.label) && !/headphone|headset/i.test(d.label));
    if (!device && navigator.mediaDevices?.selectAudioOutput) device = await navigator.mediaDevices.selectAudioOutput();
    if (!device) throw new Error('This browser cannot force the phone loudspeaker. Use your device’s audio routing controls, or the updated Android app.');
    await this.output(device.deviceId);
    return { speaker: /speaker|loudspeaker/i.test(device.label), output: device.deviceId };
  }
  async playback() { if (this.room) await this.room.startAudio(); }
  close() {
    this.generation++;
    const room = this.room; this.room = null;
    if (room) { room.removeAllListeners(); Promise.resolve(room.disconnect(true)).catch(() => {}); }
    this.track?.stop(); this.track = null; this.audioHost.replaceChildren();
    this.nativeAudio()?.reset().catch(() => {});
  }
}
