/**
 * Voice Modal Controller / Inline Voice Capture Controller
 * Controls the inline PwaniMate-style voice capture strip (#messagingVoiceCapture),
 * desktop single-click recording with stop button, mobile swipe-up-to-lock gestures,
 * organic rolling wave animation, and VN preview playback before sending.
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { voiceService } from './voice.service.js?v=20261106-group1';

export class VoiceModalController {
  constructor() {
    this.composerForm = null;
    this.composerStandard = null;
    this.voiceCapture = null;
    this.voiceBtn = null;
    this.voiceLockBadge = null;

    // Controls inside voiceCapture
    this.voiceCancelBtn = null;
    this.voiceStopBtn = null;
    this.voicePlayBtn = null;
    this.voicePlayIcon = null;
    this.voiceSendBtn = null;
    this.voiceTimer = null;
    this.voiceWaveform = null;
    this.bars = [];

    // Animation & State
    this.waveformAnimation = null;
    this.waveformHistory = [];
    this.recordedPeaks = [];
    this.lastSampleTime = 0;
    this.currentDuration = 0;
    this.analyserData = null;

    // Gesture / Mode State
    this.isDesktopRecording = false;
    this.isHolding = false;
    this.isLocked = false;
    this.holdStartTime = 0;
    this.holdStartY = 0;
    this.activePointerId = null;
    this.ambientNoiseFloor = 0.025;

    this.initialized = false;
  }

  /**
   * Initialize voice capture controller
   */
  init() {
    this._initializeElements();
    this._setupButtonListeners();
    this._setupVoiceGestures();
    if (!this.serviceListenersSetup) {
      this._setupServiceListeners();
      this.serviceListenersSetup = true;
    }
    this.resetToIdle();

    this.initialized = true;
    console.log('[VOICE_CONTROLLER] Inline voice capture controller initialized');
  }

  /**
   * Start hands-free voice recording
   */
  startVoiceRecording() {
    this._initializeElements();
    this.isDesktopRecording = true;
    this.isHolding = false;
    this.isLocked = true;
    voiceService.startRecording();
    this.showDesktopRecordingState();
  }

  /**
   * Initialize DOM elements
   */
  _initializeElements() {
    this.composerForm = document.getElementById('messaging-composer-form');
    this.composerStandard = document.getElementById('composerStandard');
    this.voiceCapture = document.getElementById('messagingVoiceCapture');
    this.voiceBtn = document.getElementById('voiceBtn');
    this.voiceLockBadge = document.getElementById('voiceLockBadge');

    this.voiceCancelBtn = document.getElementById('voiceCancelBtn');
    this.voiceStopBtn = document.getElementById('voiceStopBtn');
    this.voicePlayBtn = document.getElementById('voicePlayBtn');
    this.voicePlayIcon = document.getElementById('voicePlayIcon');
    this.voiceSendBtn = document.getElementById('voiceSendBtn');
    this.voiceTimer = document.getElementById('voiceTimer');
    this.voiceWaveform = document.getElementById('voiceWaveform');

    if (this.voiceWaveform) {
      this.bars = Array.from(this.voiceWaveform.querySelectorAll('.pwanimate-voice-waveform-bar'));
      this.waveformHistory = new Array(this.bars.length).fill(0);
    }
  }

  /**
   * Setup UI button click listeners (delegated on document)
   */
  _setupButtonListeners() {
    if (this._buttonsDelegated) return;
    this._buttonsDelegated = true;

    document.addEventListener('click', async (e) => {
      // Cancel / Discard (X)
      const cancelBtn = e.target.closest('#voiceCancelBtn');
      if (cancelBtn) {
        e.preventDefault();
        voiceService.discardRecording();
        this.resetToIdle();
        return;
      }

      // Stop button (locked mode on mobile or desktop recording)
      const stopBtn = e.target.closest('#voiceStopBtn');
      if (stopBtn) {
        e.preventDefault();
        voiceService.stopRecording();
        return;
      }

      // Play/Pause button (in preview mode)
      const playBtn = e.target.closest('#voicePlayBtn');
      if (playBtn) {
        e.preventDefault();
        voiceService.togglePlayPause();
        return;
      }

      // Send button
      const sendBtn = e.target.closest('#voiceSendBtn');
      if (sendBtn) {
        e.preventDefault();
        if (sendBtn.disabled) return;

        sendBtn.disabled = true;
        sendBtn.classList.add('is-loading');
        const icon = sendBtn.querySelector('i');
        const spinner = sendBtn.querySelector('.spinner-border');
        if (icon) icon.classList.add('d-none');
        if (spinner) spinner.classList.remove('d-none');

        // Reset the composer to its normal state immediately: the optimistic
        // bubble already carries upload progress, so the preview must not
        // linger (and spin) for the whole upload. Failures surface on the
        // bubble with retry; they must not trap the composer.
        const sendPromise = voiceService.sendRecording();
        this.resetToIdle();
        try {
          await sendPromise;
        } catch (err) {
          console.error('[VOICE_CONTROLLER] Send error:', err);
        }
        return;
      }

      // Desktop click on voiceBtn (when NOT in send mode)
      const voiceBtn = e.target.closest('#voiceBtn');
      if (voiceBtn && !voiceBtn.classList.contains('send-mode')) {
        if (e.pointerType !== 'touch') {
          e.preventDefault();
          if (voiceService.isRecording) {
            voiceService.stopRecording();
          } else {
            this.startVoiceRecording();
          }
        }
      }
    });
  }

  /**
   * Setup desktop single-click & mobile swipe-up-to-lock gestures
   */
  _setupVoiceGestures() {
    if (!this.voiceBtn) return;

    // Detect touch input vs desktop mouse input
    const isTouchInput = (e) => {
      return e.pointerType === 'touch' || e.pointerType === 'pen';
    };

    let touchStartTime = 0;
    let touchStartY = 0;
    let touchTimer = null;
    let isTouchActive = false;

    // Pointer down (captures mobile touches for hold gesture)
    const onPointerDown = (e) => {
      if (this.voiceBtn.classList.contains('send-mode')) return;
      if (e.button !== undefined && e.button !== 0) return;

      if (isTouchInput(e)) {
        isTouchActive = true;
        touchStartY = e.clientY;
        touchStartTime = performance.now();
        this.holdStartY = e.clientY;
        this.holdStartTime = performance.now();
        this.isHolding = false;
        this.isLocked = false;
        this.isDesktopRecording = false;
        this.activePointerId = e.pointerId;

        try {
          if (this.voiceBtn.setPointerCapture && e.pointerId !== undefined) {
            this.voiceBtn.setPointerCapture(e.pointerId);
          }
        } catch (_) {}

        // Touch held past 200ms becomes a press-and-hold recording gesture
        touchTimer = setTimeout(() => {
          if (isTouchActive && !this.isLocked) {
            this.isHolding = true;
            voiceService.startRecording();
            this.showHoldingState();
          }
        }, 200);
      }
    };

    // Pointer move (for swipe up to lock while holding)
    const onPointerMove = (e) => {
      if (!isTouchActive || !isTouchInput(e)) return;

      const currentY = e.clientY;
      const deltaY = touchStartY - currentY; // Upward swipe is positive

      // Swipe up >= 35px triggers lock
      if (deltaY >= 35) {
        if (!this.isLocked) {
          this.isLocked = true;
          this.isHolding = false;
          if (touchTimer) clearTimeout(touchTimer);
          if (!voiceService.isRecording) {
            voiceService.startRecording();
          }
          voiceService.lockRecording();
          this.showLockedState();
        }
      }
    };

    // Pointer up (for mobile hold release or quick tap)
    const onPointerUp = (e) => {
      if (!isTouchActive || !isTouchInput(e)) return;
      isTouchActive = false;
      if (touchTimer) clearTimeout(touchTimer);

      try {
        if (this.voiceBtn.releasePointerCapture && this.activePointerId !== null) {
          this.voiceBtn.releasePointerCapture(this.activePointerId);
        }
      } catch (_) {}

      this.activePointerId = null;

      if (this.isLocked) {
        // Was locked: release finger safely, recording continues hands-free
        return;
      }

      const elapsed = (performance.now() - touchStartTime) / 1000;
      if (this.isHolding && elapsed >= 0.2) {
        // Was holding to record: releasing finger stops recording and enters preview
        this.isHolding = false;
        voiceService.stopRecording();
      } else {
        // Quick tap (< 200ms): start hands-free recording with Stop button!
        this.isDesktopRecording = true;
        this.isHolding = false;
        this.isLocked = true;
        voiceService.startRecording();
        this.showDesktopRecordingState();
      }
    };

    const onPointerCancel = (e) => {
      if (!isTouchActive || !isTouchInput(e)) return;
      isTouchActive = false;
      if (touchTimer) clearTimeout(touchTimer);
      this.isHolding = false;
      if (!this.isLocked) {
        voiceService.discardRecording();
        this.resetToIdle();
      }
    };

    // Desktop Click: Single click starts recording hands-free with Stop button
    const onClick = (e) => {
      if (this.voiceBtn.classList.contains('send-mode')) return;

      // Handle desktop / mouse clicks (not touch input, which is handled above)
      if (!isTouchInput(e) && e.pointerType !== 'touch') {
        e.preventDefault();

        if (voiceService.isRecording) {
          // If already recording on desktop, clicking stops recording and enters preview
          voiceService.stopRecording();
        } else {
          // Single desktop click starts recording hands-free with stop button!
          this.isDesktopRecording = true;
          this.isHolding = false;
          this.isLocked = true;
          voiceService.startRecording();
          this.showDesktopRecordingState();
        }
      }
    };

    this.voiceBtn.addEventListener('pointerdown', onPointerDown);
    this.voiceBtn.addEventListener('pointermove', onPointerMove);
    this.voiceBtn.addEventListener('pointerup', onPointerUp);
    this.voiceBtn.addEventListener('pointercancel', onPointerCancel);
    this.voiceBtn.addEventListener('click', onClick);

    this.voiceBtn.addEventListener('contextmenu', (e) => {
      if (!this.voiceBtn.classList.contains('send-mode')) {
        e.preventDefault();
      }
    });
  }

  /**
   * Setup event bus listeners
   */
  _setupServiceListeners() {
    eventBus.on(EVENTS.VOICE_START, () => {
      if (this.isHolding) {
        this.showHoldingState();
      } else {
        this.showDesktopRecordingState();
      }
    });

    eventBus.on(EVENTS.VOICE_STOP, (data) => {
      const duration = data?.duration || voiceService.duration || 0;
      this.showPreviewState(duration);
    });

    eventBus.on(EVENTS.VOICE_LOCKED, () => {
      this.showLockedState();
    });

    eventBus.on(EVENTS.VOICE_TIMER_UPDATE, (duration) => {
      if (!voiceService.isPlaying && this.voiceTimer) {
        this.voiceTimer.textContent = this._formatTime(duration);
      }
    });

    eventBus.on(EVENTS.VOICE_PLAYING, () => {
      if (this.voicePlayIcon) {
        this.voicePlayIcon.className = 'bi bi-pause-fill';
      }
      this.startPlaybackWaveformAnimation(this.currentDuration);
    });

    eventBus.on(EVENTS.VOICE_PAUSED, () => {
      if (this.voicePlayIcon) {
        this.voicePlayIcon.className = 'bi bi-play-fill';
      }
      this.renderStaticWaveformProfile(this.currentDuration);
    });

    eventBus.on(EVENTS.VOICE_PLAYBACK_ENDED, () => {
      if (this.voicePlayIcon) {
        this.voicePlayIcon.className = 'bi bi-play-fill';
      }
      this.renderStaticWaveformProfile(this.currentDuration);
    });

    eventBus.on(EVENTS.VOICE_DISCARD, () => {
      this.resetToIdle();
    });

    eventBus.on(EVENTS.VOICE_ERROR, (err) => {
      console.warn('[VOICE_CONTROLLER] Voice error encountered:', err);
      const msg = err?.message || 'Microphone access blocked or unavailable';
      this.showHint(msg);
      this.resetToIdle();
    });
  }

  /**
   * Display desktop single-click hands-free recording state with Stop button
   */
  showDesktopRecordingState() {
    this._initializeElements();
    if (this.composerForm) this.composerForm.classList.add('is-voice-recording');
    if (this.composerStandard) this.composerStandard.classList.add('d-none');
    if (this.voiceCapture) this.voiceCapture.classList.remove('d-none');

    // On desktop: lock badge not needed, stop button directly visible!
    if (this.voiceLockBadge) this.voiceLockBadge.classList.add('d-none');
    if (this.voiceCancelBtn) this.voiceCancelBtn.classList.remove('d-none');
    if (this.voiceStopBtn) this.voiceStopBtn.classList.remove('d-none');
    if (this.voicePlayBtn) this.voicePlayBtn.classList.add('d-none');
    if (this.voiceSendBtn) this.voiceSendBtn.classList.add('d-none');

    if (this.voiceTimer) this.voiceTimer.textContent = '0:00';

    this.startLiveWaveformAnimation();
  }

  /**
   * Display mobile holding-to-record state with lock badge
   */
  showHoldingState() {
    this._initializeElements();
    if (this.composerForm) this.composerForm.classList.add('is-voice-recording');
    if (this.composerStandard) this.composerStandard.classList.add('d-none');
    if (this.voiceCapture) this.voiceCapture.classList.remove('d-none');
    if (this.voiceLockBadge) this.voiceLockBadge.classList.remove('d-none');

    if (this.voiceCancelBtn) this.voiceCancelBtn.classList.remove('d-none');
    if (this.voiceStopBtn) this.voiceStopBtn.classList.add('d-none');
    if (this.voicePlayBtn) this.voicePlayBtn.classList.add('d-none');
    if (this.voiceSendBtn) this.voiceSendBtn.classList.add('d-none');

    if (this.voiceTimer) this.voiceTimer.textContent = '0:00';

    this.startLiveWaveformAnimation();
  }

  /**
   * Display mobile swipe-up locked recording state
   */
  showLockedState() {
    this._initializeElements();
    if (this.voiceLockBadge) this.voiceLockBadge.classList.add('d-none');
    if (this.voiceStopBtn) this.voiceStopBtn.classList.remove('d-none');
    if (this.voicePlayBtn) this.voicePlayBtn.classList.add('d-none');
    if (this.voiceSendBtn) this.voiceSendBtn.classList.add('d-none');
  }

  /**
   * Display recorded voice note preview state (VN preview)
   * @param {number} duration - Recorded duration in seconds
   */
  showPreviewState(duration) {
    this._initializeElements();
    this.stopWaveformAnimation();
    this.isDesktopRecording = false;
    this.currentDuration = Math.max(1, duration || 0);

    if (this.composerForm) this.composerForm.classList.add('is-voice-recording');
    if (this.composerStandard) this.composerStandard.classList.add('d-none');
    if (this.voiceCapture) {
      this.voiceCapture.classList.remove('d-none');
      this.voiceCapture.classList.remove('is-speaking');
    }
    if (this.voiceLockBadge) this.voiceLockBadge.classList.add('d-none');

    // Controls
    if (this.voiceCancelBtn) this.voiceCancelBtn.classList.remove('d-none');
    if (this.voiceStopBtn) this.voiceStopBtn.classList.add('d-none');
    if (this.voicePlayBtn) this.voicePlayBtn.classList.remove('d-none');
    if (this.voicePlayIcon) this.voicePlayIcon.className = 'bi bi-play-fill';
    if (this.voiceSendBtn) {
      this.voiceSendBtn.classList.remove('d-none', 'is-loading');
      this.voiceSendBtn.removeAttribute('disabled');
      const icon = this.voiceSendBtn.querySelector('i');
      const spinner = this.voiceSendBtn.querySelector('.spinner-border');
      if (icon) icon.classList.remove('d-none');
      if (spinner) spinner.classList.add('d-none');
    }

    if (this.voiceTimer) {
      this.voiceTimer.textContent = this._formatTime(this.currentDuration);
    }

    this.renderStaticWaveformProfile(this.currentDuration);
  }

  /**
   * Reset composer to idle standard state
   */
  resetToIdle() {
    this._initializeElements();
    this.stopWaveformAnimation();
    this.isDesktopRecording = false;
    this.isHolding = false;
    this.isLocked = false;
    this.activePointerId = null;
    this.analyserData = null;

    if (this.composerForm) {
      this.composerForm.classList.remove('is-voice-recording', 'is-voice-active');
    }
    if (this.composerStandard) this.composerStandard.classList.remove('d-none');
    if (this.voiceCapture) {
      this.voiceCapture.classList.add('d-none');
      this.voiceCapture.classList.remove('is-speaking');
    }
    if (this.voiceLockBadge) this.voiceLockBadge.classList.add('d-none');

    if (this.voiceStopBtn) this.voiceStopBtn.classList.add('d-none');
    if (this.voicePlayBtn) this.voicePlayBtn.classList.add('d-none');
    if (this.voiceSendBtn) {
      this.voiceSendBtn.classList.add('d-none');
      this.voiceSendBtn.classList.remove('is-loading');
      this.voiceSendBtn.removeAttribute('disabled');
      const icon = this.voiceSendBtn.querySelector('i');
      const spinner = this.voiceSendBtn.querySelector('.spinner-border');
      if (icon) icon.classList.remove('d-none');
      if (spinner) spinner.classList.add('d-none');
    }

    if (this.voiceTimer) this.voiceTimer.textContent = '0:00';

    this.bars.forEach((bar) => {
      bar.style.height = '3px';
      bar.style.opacity = '0.5';
    });
  }

  /**
   * PwaniMate Organic Soundwave Animation
   * Analyzes real microphone volume and continuously scrolls sound waves across the bars.
   */
  startLiveWaveformAnimation() {
    this.stopWaveformAnimation();
    if (!this.bars.length) return;

    const numBars = this.bars.length;
    this.waveformHistory = new Array(numBars).fill(0);
    this.recordedPeaks = [];
    this.lastSampleTime = performance.now();

    const draw = () => {
      const now = performance.now();
      let liveRms = 0;

      // Dynamically initialize and read byte time-domain data as soon as analyser is ready
      if (voiceService.analyser) {
        if (!this.analyserData || this.analyserData.length !== voiceService.analyser.fftSize) {
          this.analyserData = new Uint8Array(voiceService.analyser.fftSize);
        }
        voiceService.analyser.getByteTimeDomainData(this.analyserData);
        let sum = 0;
        for (let i = 0; i < this.analyserData.length; i++) {
          const amp = (this.analyserData[i] - 128) / 128;
          sum += amp * amp;
        }
        liveRms = Math.sqrt(sum / this.analyserData.length);
      }

      // Track ambient baseline noise floor during quiet moments
      if (liveRms > 0 && liveRms < 0.03) {
        this.ambientNoiseFloor = this.ambientNoiseFloor * 0.95 + liveRms * 0.05;
      }

      let normalizedVolume = 0;
      // Sensitive noise gate: adapts to room ambient noise, baseline 0.012
      const gateThreshold = Math.max(0.012, (this.ambientNoiseFloor || 0.015) * 1.15);
      if (liveRms > gateThreshold) {
        // Dynamic volume curve: scales smoothly from 0.0 to 1.0 above the gate
        normalizedVolume = Math.min(1.0, Math.pow((liveRms - gateThreshold) / 0.065, 0.72));
      } else {
        normalizedVolume = 0;
      }

      // Append sample at right edge every 50ms, scrolling trace leftward
      if (now - this.lastSampleTime >= 50) {
        this.waveformHistory.shift();
        this.waveformHistory.push(normalizedVolume);
        this.recordedPeaks.push(normalizedVolume);
        this.lastSampleTime = now;

        const isSpeaking = normalizedVolume > 0.02;
        if (this.voiceCapture) {
          this.voiceCapture.classList.toggle('is-speaking', isSpeaking);
        }

        // Render each bar height symmetrically with PwaniMate harmonic variation
        this.bars.forEach((bar, index) => {
          const sample = this.waveformHistory[index] || 0;
          if (sample > 0.015) {
            const variation = 0.75 + 0.25 * (0.5 + 0.5 * Math.sin((index + 1) * 2.37));
            const height = Math.round(3 + Math.pow(sample, 0.74) * 31 * variation);
            bar.style.height = `${Math.min(34, Math.max(3, height))}px`;
            bar.style.opacity = '0.98';
          } else {
            bar.style.height = '3px';
            bar.style.opacity = '0.45';
          }
        });
      }

      this.waveformAnimation = requestAnimationFrame(draw);
    };

    this.waveformAnimation = requestAnimationFrame(draw);
  }

  /**
   * Preview Playback Animation in sync with real audio playback
   * @param {number} duration - Total audio duration
   */
  startPlaybackWaveformAnimation(duration) {
    this.stopWaveformAnimation();
    if (!this.bars.length) return;

    const numBars = this.bars.length;
    const peaks = this.recordedPeaks.length > 0
      ? this._resamplePeaks(this.recordedPeaks, numBars)
      : this._generateRealisticProfile(numBars);

    const draw = () => {
      const audio = voiceService.audioElement;
      if (!audio || !voiceService.isPlaying) return;

      const currentTime = audio.currentTime || 0;
      const totalDuration = audio.duration || duration || 1;
      const progress = Math.min(1, Math.max(0, currentTime / totalDuration));
      const activeBarIndex = Math.floor(progress * numBars);
      const now = performance.now();

      for (let i = 0; i < numBars; i++) {
        const basePeak = peaks[i] || 0.15;
        let height;

        if (i <= activeBarIndex) {
          // Passed/playing bars: animated with dynamic audio ripple
          const ripple = 0.85 + 0.15 * Math.sin((i - activeBarIndex) * 0.75 + now * 0.012);
          height = Math.round(3 + basePeak * 31 * ripple);
          this.bars[i].style.opacity = '1';
        } else {
          // Upcoming bars: steady profile
          height = Math.round(3 + basePeak * 26);
          this.bars[i].style.opacity = '0.45';
        }
        this.bars[i].style.height = `${Math.min(34, Math.max(3, height))}px`;
      }

      if (this.voiceTimer) {
        this.voiceTimer.textContent = `${this._formatTime(currentTime)} / ${this._formatTime(totalDuration)}`;
      }

      this.waveformAnimation = requestAnimationFrame(draw);
    };

    this.waveformAnimation = requestAnimationFrame(draw);
  }

  /**
   * Render static waveform profile for recorded audio
   * @param {number} duration - Recorded duration
   */
  renderStaticWaveformProfile(duration) {
    this.stopWaveformAnimation();
    if (!this.bars.length) return;

    const numBars = this.bars.length;
    const peaks = this.recordedPeaks.length > 0
      ? this._resamplePeaks(this.recordedPeaks, numBars)
      : this._generateRealisticProfile(numBars);

    for (let i = 0; i < numBars; i++) {
      const basePeak = peaks[i] || 0.15;
      const height = Math.round(3 + basePeak * 30);
      this.bars[i].style.height = `${Math.min(34, Math.max(3, height))}px`;
      this.bars[i].style.opacity = '0.85';
    }

    if (this.voiceTimer) {
      this.voiceTimer.textContent = this._formatTime(duration);
    }
  }

  /**
   * Stop active waveform animation
   */
  stopWaveformAnimation() {
    if (this.waveformAnimation) {
      cancelAnimationFrame(this.waveformAnimation);
      this.waveformAnimation = null;
    }
  }

  /**
   * Resample recorded peaks to match bar count
   * @param {number[]} peaks
   * @param {number} targetCount
   * @returns {Float32Array}
   */
  _resamplePeaks(peaks, targetCount) {
    if (!peaks.length) return new Float32Array(targetCount).fill(0.2);
    const result = new Float32Array(targetCount);
    const step = (peaks.length - 1) / (targetCount - 1 || 1);

    for (let i = 0; i < targetCount; i++) {
      const idx = i * step;
      const low = Math.floor(idx);
      const high = Math.min(peaks.length - 1, Math.ceil(idx));
      const frac = idx - low;
      const val = (peaks[low] * (1 - frac) + peaks[high] * frac);
      result[i] = Math.min(1.0, Math.max(0.08, val));
    }
    return result;
  }

  /**
   * Generate realistic organic waveform profile fallback
   * @param {number} count
   * @returns {Float32Array}
   */
  _generateRealisticProfile(count) {
    const result = new Float32Array(count);
    for (let i = 0; i < count; i++) {
      const norm = i / count;
      const envelope = Math.sin(norm * Math.PI);
      const variation = 0.4 + 0.6 * Math.abs(Math.sin(i * 2.37) * Math.cos(i * 1.5));
      result[i] = Math.min(1.0, Math.max(0.1, envelope * variation));
    }
    return result;
  }

  /**
   * Format seconds to M:SS
   * @param {number} totalSeconds
   * @returns {string}
   */
  _formatTime(totalSeconds) {
    const s = Math.floor(totalSeconds || 0);
    const mins = Math.floor(s / 60);
    const secs = s % 60;
    return `${mins}:${secs < 10 ? '0' : ''}${secs}`;
  }

  /**
   * Show a temporary toast hint (e.g. "Hold to record or swipe up to lock")
   * @param {string} msg
   */
  showHint(msg) {
    const existing = document.querySelector('.messaging-voice-hint-toast');
    if (existing) existing.remove();

    const hint = document.createElement('div');
    hint.className = 'messaging-voice-hint-toast';
    hint.textContent = msg;
    hint.style.cssText = [
      'position: fixed',
      'bottom: 92px',
      'left: 50%',
      'transform: translateX(-50%)',
      'background: rgba(15, 23, 42, 0.92)',
      'color: #ffffff',
      'padding: 7px 18px',
      'border-radius: 999px',
      'font-size: 0.82rem',
      'font-weight: 500',
      'box-shadow: 0 4px 14px rgba(0, 0, 0, 0.22)',
      'z-index: 9999',
      'pointer-events: none',
      'transition: opacity 0.25s ease, transform 0.25s ease',
    ].join(';');

    document.body.appendChild(hint);
    setTimeout(() => {
      hint.style.opacity = '0';
      hint.style.transform = 'translateX(-50%) translateY(4px)';
      setTimeout(() => hint.remove(), 260);
    }, 2400);
  }
}

// Global singleton instance
export const voiceModalController = new VoiceModalController();
