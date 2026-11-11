/** Foreground ringtone/vibration; browser autoplay and silent-mode policies apply. */
export class CallAlerts {
  constructor() { this.key = ''; this.timer = null; this.context = null; this.tones = new Set(); }
  async unlock() {
    const Audio = window.AudioContext || window.webkitAudioContext;
    if (!Audio) return;
    this.context ??= new Audio();
    try { await this.context.resume(); } catch (_) {}
  }
  update(state) {
    const mode = !state.call ? '' : state.phase === 'incoming' ? 'incoming' : ['initiating', 'ringing'].includes(state.phase) ? 'outgoing' : '';
    const key = mode ? `${state.call.id}:${mode}` : '';
    if (key === this.key) return;
    this.stop(); this.key = key;
    if (!mode) return;
    const pulse = () => {
      if (mode === 'incoming') navigator.vibrate?.([250, 120, 250]);
      if (this.context?.state !== 'running') return;
      const start = this.context.currentTime;
      for (const offset of [0, .3]) {
        const oscillator = this.context.createOscillator(), gain = this.context.createGain();
        oscillator.frequency.value = mode === 'incoming' ? 660 : 440;
        gain.gain.setValueAtTime(0, start + offset);
        gain.gain.linearRampToValueAtTime(.08, start + offset + .025);
        gain.gain.setValueAtTime(.08, start + offset + .18);
        gain.gain.linearRampToValueAtTime(0, start + offset + .23);
        this.tones.add(oscillator); oscillator.onended = () => { this.tones.delete(oscillator); gain.disconnect(); };
        oscillator.connect(gain); gain.connect(this.context.destination);
        oscillator.start(start + offset); oscillator.stop(start + offset + .25);
      }
    };
    pulse(); this.timer = setInterval(pulse, mode === 'incoming' ? 2200 : 3000);
  }
  stop() { for (const tone of this.tones) { try { tone.stop(); } catch (_) {} } this.tones.clear(); clearInterval(this.timer); this.timer = null; this.key = ''; navigator.vibrate?.(0); }
}
