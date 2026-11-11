/** One call session; presentation is independent from signaling and media state. */
export const TERMINAL = new Set(['ended', 'cancelled', 'declined', 'missed']);
export function duration(state, now = Date.now()) {
  return state.connectedAt == null ? 0 : Math.max(0, Math.floor(((state.endedAt ?? now) - state.connectedAt) / 1000));
}
export function formatDuration(seconds) {
  const h = Math.floor(seconds / 3600), m = Math.floor(seconds / 60) % 60, s = seconds % 60;
  return `${h ? `${h}:` : ''}${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}
export class CallSession {
  constructor({ api, media, userId, changed = () => {}, now = () => Date.now() }) {
    Object.assign(this, { api, media, userId: String(userId), changed, now });
    this.state = { phase: 'idle', presentation: 'full', muted: false, speaker: false, output: 'default', connectedAt: null, endedAt: null };
    this.epoch = 0; this.finished = new Set(); this.ringing = new Set();
    this.pending = null; this.connecting = null;
  }
  get live() { return !['idle', 'ended'].includes(this.state.phase); }
  update(patch) { if (patch.phase && patch.phase !== this.state.phase) this.phaseSince = this.now(); Object.assign(this.state, patch); this.changed(this.state); }
  minimize() { this.update({ presentation: 'minimized' }); }
  restore() { this.update({ presentation: 'full' }); }
  reset(peer = {}, patch = {}) {
    this.epoch++; this.pending = null; this.connecting = null; this.media.close();
    this.state = { phase: 'initiating', presentation: 'full', muted: false, speaker: false, output: 'default',
      connectedAt: null, endedAt: null, peer, call: null, error: '', permissionBlocked: false, playbackBlocked: false, ...patch };
    this.changed(this.state);
  }
  async start(conversationId, peer) {
    if (this.live || this.pending) { this.restore(); return; }
    this.reset(peer); this.state.conversationId = conversationId; this.state.direction = 'outgoing';
    return this.initiate();
  }
  async initiate() {
    const epoch = this.epoch;
    this.pending = 'start'; this.update({ phase: 'initiating', error: '' });
    try {
      await this.api.ensureAvailable?.();
      if (epoch !== this.epoch) return;
      await this.media.prepare();
      if (epoch !== this.epoch) return;
      const call = await this.api.start(this.state.conversationId);
      if (epoch !== this.epoch) { await this.api.action(call.id, 'cancel').catch(() => {}); return; }
      this.apply(call);
    } catch (error) {
      if (epoch === this.epoch) this.failure(error);
    } finally { if (epoch === this.epoch) { this.pending = null; this.changed(this.state); } }
  }
  permissionFailure(error) { return ['NotAllowedError', 'PermissionDeniedError', 'NotFoundError', 'NotReadableError', 'SecurityError'].includes(error.name); }
  failure(error) {
    if (this.permissionFailure(error)) {
      this.update({ phase: 'permission', error: error.name === 'NotFoundError' ? 'No microphone was found. Connect one and try again.' :
        error.name === 'NotReadableError' ? 'Your microphone is busy. Close other apps using it and try again.' :
        'Allow microphone access to speak on this call. If access is blocked, change the microphone permission in your browser or device settings, then try again.',
        permissionBlocked: error.permissionBlocked ?? true });
      return;
    }
    this.endLocal(error.status === 503 ? 'unconfigured' : error.status === 409 ? 'busy' : 'failed', error.message);
  }
  async retryPermission() {
    if (this.pending || this.state.phase !== 'permission') return;
    if (!this.state.call) return this.initiate();
    if (this.state.call.status === 'active') return this.connect();
    return this.answer();
  }
  apply(call, { recover = false } = {}) {
    if (!call?.id || this.finished.has(call.id)) return;
    const current = this.state.call;
    if (current && current.id !== call.id && this.live) return;
    if (current?.id === call.id && call.revision <= current.revision) return;
    const me = call.participants.find(p => String(p.user_id) === this.userId);
    if (!me) return;
    const incoming = String(call.caller_id) !== this.userId;
    if (!current || current.id !== call.id) {
      const ownStart = !current && this.pending === 'start' && !incoming &&
        String(call.conversation_id) === String(this.state.conversationId);
      if (TERMINAL.has(call.status) || (call.status === 'active' && !recover && !ownStart)) return;
      // The server publishes on commit, so its socket hint can beat the start
      // response. Attach it to the pending attempt without resetting its epoch,
      // microphone or lock; otherwise initiate() cancels its own invitation.
      if (!ownStart) {
        if (!current && this.pending === 'start') return;
        this.reset({}, { phase: incoming ? 'incoming' : 'initiating', presentation: incoming && !recover ? 'minimized' : 'full' });
        this.state.presentation = incoming && !recover ? 'minimized' : 'full';
      }
      this.state.direction = incoming ? 'incoming' : 'outgoing';
    }
    const peer = call.participants.find(p => String(p.user_id) !== this.userId);
    this.update({ call, conversationId: call.conversation_id,
      peer: { name: peer?.display_name || this.state.peer?.name || 'PwaniNet member', avatar: peer?.avatar_url || this.state.peer?.avatar || '' } });
    if (TERMINAL.has(call.status)) { this.endLocal(call.status === 'missed' ? 'no_answer' : call.status); return; }
    if (call.status === 'active') {
      if (!this.connecting && !['connected', 'reconnecting'].includes(this.state.phase)) this.connect();
    } else if (incoming) {
      this.update({ phase: 'incoming' });
    } else {
      this.update({ phase: call.participants.some(p => String(p.user_id) !== this.userId && p.status === 'ringing') ? 'ringing' : 'initiating' });
    }
  }
  async acknowledge() {
    const call = this.state.call;
    if (!call || this.state.phase !== 'incoming' || this.ringing.has(call.id)) return;
    this.ringing.add(call.id);
    try { this.apply(await this.api.action(call.id, 'ringing')); }
    catch (_) { this.ringing.delete(call.id); }
  }
  async answer() {
    if (this.pending || !['incoming', 'permission'].includes(this.state.phase) || !this.state.call) return;
    const epoch = this.epoch, id = this.state.call.id;
    this.pending = 'answer'; this.update({ phase: 'connecting', presentation: 'full', error: '' });
    try {
      await this.media.prepare();
      if (epoch !== this.epoch) return;
      const call = await this.api.action(id, 'accept');
      if (epoch === this.epoch) this.apply(call);
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (error.status === 409 && error.code === 'answered_elsewhere') this.endLocal('elsewhere');
      else if (error.status === 409) await this.reconcileConflict(id);
      else this.failure(error);
    } finally { if (epoch === this.epoch) { this.pending = null; this.changed(this.state); } }
  }
  async connect() {
    const id = this.state.call?.id, epoch = this.epoch;
    if (!id || this.connecting) return;
    this.connecting = id; this.update({ phase: 'connecting' });
    try {
      const details = await this.api.connection(id);
      if (epoch !== this.epoch) return;
      await this.media.connect(details, {
        ready: () => { if (epoch === this.epoch) this.update({ phase: 'connected', connectedAt: this.state.connectedAt ?? this.now() }); },
        reconnecting: () => { if (epoch === this.epoch) this.update({ phase: 'reconnecting' }); },
        reconnected: () => { if (epoch === this.epoch) this.update({ phase: this.state.connectedAt == null ? 'connecting' : 'connected' }); },
        disconnected: () => { if (epoch === this.epoch) this.end('failed'); },
        routing: state => { if (epoch === this.epoch) this.update(state); },
        playback: blocked => { if (epoch === this.epoch) this.update({ playbackBlocked: blocked }); },
      }, { muted: this.state.muted, output: this.state.output, speaker: this.state.speaker });
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (error.status === 409 && error.code === 'answered_elsewhere') this.endLocal(this.state.direction === 'incoming' ? 'elsewhere' : 'other_session');
      else if (error.status === 409) await this.reconcileConflict(id);
      else if (this.permissionFailure(error)) { this.media.close(); this.failure(error); }
      else await this.end('failed');
    } finally { if (epoch === this.epoch) this.connecting = null; }
  }
  restoreSaved(call, hint) {
    // Fetch/authorize the snapshot before this method; never restore stored media tokens.
    if (call.id !== hint.id || TERMINAL.has(call.status)) return;
    this.apply(call, { recover: true });
    this.update({ connectedAt: hint.connectedAt ?? null, muted: !!hint.muted,
      output: hint.output || 'default', speaker: !!hint.speaker,
      presentation: hint.presentation === 'full' ? 'full' : 'minimized' });
  }
  async reconcileConflict(id) {
    try {
      const latest = await this.api.get(id);
      if (TERMINAL.has(latest.status)) this.apply(latest);
      else this.endLocal('failed', 'The call could not be joined.');
    } catch (_) { this.endLocal('failed', 'The call could not be joined.'); }
  }
  async decline() {
    if (this.pending || !this.state.call || !['incoming', 'permission'].includes(this.state.phase)) return;
    this.pending = 'decline'; this.changed(this.state);
    try { this.apply(await this.api.action(this.state.call.id, 'decline')); }
    catch (error) { this.update({ error: 'Could not decline. Check your connection and try again.' }); }
    finally { this.pending = null; this.changed(this.state); }
  }
  async end(outcome) {
    if (this.state.phase === 'ending' || !this.live) return;
    const call = this.state.call;
    const action = this.state.direction === 'incoming' && call?.status === 'inviting' ? 'decline' : call?.status === 'inviting' ? 'cancel' : 'leave';
    const epoch = ++this.epoch; this.connecting = null; this.pending = 'end'; this.media.close();
    this.update({ phase: 'ending', endedAt: this.now(), error: '' });
    try {
      if (call) {
        const result = await this.api.action(call.id, action);
        if (epoch !== this.epoch) return;
        // A failed local connect must not end a call that belongs to another session.
        this.state.call = result;
      }
      this.endLocal(outcome || (call?.status === 'inviting' ? 'cancelled' : 'ended'));
    } catch (error) {
      if (epoch !== this.epoch) return;
      if (error.status === 409 && call) {
        // Acceptance may win the race against cancel/decline. Leave only if this
        // authenticated session owns the accepted participant (enforced by API).
        try {
          const latest = await this.api.get(call.id);
          if (latest.status === 'active') await this.api.action(call.id, 'leave');
          if (epoch !== this.epoch) return;
          this.endLocal(outcome || 'ended');
        } catch (retryError) {
          if ([409, 404].includes(retryError.status)) this.endLocal('ended');
          else this.update({ phase: 'end_failed', error: 'Audio is stopped. Reconnect and tap End call again.' });
        }
      } else if (error.status === 404) this.endLocal('ended');
      else this.update({ phase: 'end_failed', error: 'Audio is stopped on this device. Reconnect and tap End call again to finish the invitation.' });
    } finally { if (epoch === this.epoch) { this.pending = null; this.changed(this.state); } }
  }
  endLocal(outcome, error = '') {
    const id = this.state.call?.id;
    if (id) { this.finished.add(id); if (this.finished.size > 100) this.finished.delete(this.finished.values().next().value); }
    this.epoch++; this.pending = null; this.connecting = null; this.media.close();
    this.update({ phase: 'ended', outcome, error, endedAt: this.state.endedAt ?? this.now(), playbackBlocked: false });
  }
  async mute() {
    if (this.muting || !['connected', 'reconnecting'].includes(this.state.phase)) return;
    this.muting = true; const epoch = this.epoch;
    try { const muted = !this.state.muted; await this.media.mute(muted); if (epoch === this.epoch) this.update({ muted }); }
    catch (_) { this.update({ error: 'Could not change microphone. Try again.' }); }
    finally { this.muting = false; }
  }
  tick() {
    const { call, phase } = this.state;
    if (['connecting', 'reconnecting'].includes(phase) && this.now() - this.phaseSince > 45000) { this.end('failed'); return; }
    if (call?.status === 'inviting' && !['idle', 'ended', 'ending', 'end_failed'].includes(phase) && Date.parse(call.expires_at) <= this.now()) {
      this.endLocal('no_answer'); this.api.get(call.id).catch(() => {});
    }
  }
  dismiss() {
    if (this.live) return;
    this.epoch++; this.media.close(); this.update({ phase: 'idle' });
  }
}
