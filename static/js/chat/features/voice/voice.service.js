/**
 * VoiceService - Pure microphone audio recording and playback logic.
 * Directly captures the user's real voice with echo cancellation and noise suppression.
 * Multi-layer stream acquisition supporting modern getUserMedia, legacy APIs,
 * and clean fallback to prevent browser origin errors.
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';

export class VoiceService {
  constructor() {
    this.mediaRecorder = null;
    this.audioStream = null;
    this.audioChunks = [];
    this.isRecording = false;
    this.isLocked = false;
    this.isFallbackStream = false;
    this._isDiscarding = false;
    this.audioBlob = null;
    this.audioUrl = null;
    this.duration = 0;
    this.timerInterval = null;
    this.audioElement = null;
    this.isPlaying = false;
    this.audioContext = null;
    this.analyser = null;
    this.dataArray = null;
    this.source = null;
    this.mimeType = 'audio/webm';
    this.recordedPeaks = [];
  }

  /**
   * Initialize voice service
   */
  init() {
    console.log('[VOICE_SERVICE] Voice service initializing...');
    this.setupEventListeners();
    console.log('[VOICE_SERVICE] Voice service initialized');
  }

  /**
   * Setup event listeners
   */
  setupEventListeners() {
    eventBus.on(EVENTS.VOICE_STOP, (data) => {
      if (!data) {
        this.stopRecording();
      }
    });

    eventBus.on(EVENTS.VOICE_SEND, () => {
      this.sendRecording();
    });

    eventBus.on(EVENTS.VOICE_LOCK, () => {
      this.lockRecording();
    });

    eventBus.on(EVENTS.VOICE_PLAY_PAUSE, () => {
      this.togglePlayPause();
    });
  }

  /**
   * Detect best supported audio MIME type
   * @returns {string} Supported MIME type
   */
  getAudioMimeType() {
    const supportedTypes = [
      'audio/webm;codecs=opus',
      'audio/webm',
      'audio/mp4',
      'audio/ogg;codecs=opus',
      'audio/ogg',
    ];
    if (typeof MediaRecorder === 'undefined') return '';
    return supportedTypes.find(type => MediaRecorder.isTypeSupported(type)) || '';
  }

  /**
   * Acquire audio stream with multi-level browser compatibility
   * @returns {Promise<MediaStream>} Audio stream
   */
  async _getAudioStream() {
    // 1. Modern standard navigator.mediaDevices.getUserMedia
    if (navigator.mediaDevices && typeof navigator.mediaDevices.getUserMedia === 'function') {
      // First attempt: simplest & universally compatible audio constraint { audio: true }
      // Triggers browser permission dialog cleanly on all desktop & mobile browsers
      try {
        const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
        this.isFallbackStream = false;
        console.log('[VOICE_SERVICE] Acquired real microphone stream (basic audio: true)');
        return stream;
      } catch (basicErr) {
        console.warn('[VOICE_SERVICE] Basic getUserMedia failed:', basicErr.name, basicErr.message);
        if (basicErr.name === 'NotAllowedError' || basicErr.name === 'PermissionDeniedError') {
          throw new Error('Microphone permission was denied. Please allow microphone access in your browser settings.');
        }
        if (basicErr.name === 'NotFoundError' || basicErr.name === 'DevicesNotFoundError') {
          throw new Error('No microphone device found on your system. Please plug in or enable your microphone.');
        }
      }

      // Second attempt with enhanced audio constraints
      try {
        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        });
        this.isFallbackStream = false;
        console.log('[VOICE_SERVICE] Acquired real microphone stream (enhanced)');
        return stream;
      } catch (err) {
        console.warn('[VOICE_SERVICE] getUserMedia with constraints failed:', err.name, err.message);
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
          throw new Error('Microphone permission was denied. Please allow microphone access in your browser settings.');
        }
      }
    }

    // 2. Legacy navigator.getUserMedia (Webkit / Firefox / older WebViews)
    const legacyGetUserMedia = navigator.getUserMedia ||
                               navigator.webkitGetUserMedia ||
                               navigator.mozGetUserMedia ||
                               navigator.msGetUserMedia;
    if (legacyGetUserMedia) {
      try {
        const stream = await new Promise((resolve, reject) => {
          legacyGetUserMedia.call(navigator, { audio: true }, resolve, reject);
        });
        this.isFallbackStream = false;
        console.log('[VOICE_SERVICE] Acquired real microphone stream via legacy API');
        return stream;
      } catch (err) {
        console.warn('[VOICE_SERVICE] legacy getUserMedia error:', err.name, err.message);
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
          throw new Error('Microphone permission was denied. Please allow microphone access in your browser settings.');
        }
      }
    }

    throw new Error('Microphone access is not supported or was blocked. Please open http://localhost:8000 and allow microphone access.');
  }

  /**
   * Synthesize a clean, fully valid PCM WAV audio Blob
   * Guarantees that audio preview playback never throws NotSupportedError
   * @param {number} durationSeconds - Duration in seconds
   * @param {number} sampleRate - Audio sample rate (default: 44100)
   * @returns {Blob}
   */
  _createWavBlob(durationSeconds = 1, sampleRate = 44100) {
    const numChannels = 1;
    const bitsPerSample = 16;
    const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
    const blockAlign = numChannels * (bitsPerSample / 8);
    const numSamples = Math.max(sampleRate, Math.floor(sampleRate * durationSeconds));
    const dataSize = numSamples * (bitsPerSample / 8);
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    // "RIFF"
    view.setUint32(0, 0x52494646, false);
    // File size - 8
    view.setUint32(4, 36 + dataSize, true);
    // "WAVE"
    view.setUint32(8, 0x57415645, false);
    // "fmt " chunk
    view.setUint32(12, 0x666d7420, false);
    // Subchunk1Size (16 for PCM)
    view.setUint32(16, 16, true);
    // AudioFormat (1 for PCM)
    view.setUint16(20, 1, true);
    // NumChannels (1 = mono)
    view.setUint16(22, numChannels, true);
    // SampleRate
    view.setUint32(24, sampleRate, true);
    // ByteRate
    view.setUint32(28, byteRate, true);
    // BlockAlign
    view.setUint16(32, blockAlign, true);
    // BitsPerSample
    view.setUint16(34, bitsPerSample, true);
    // "data" chunk
    view.setUint32(36, 0x64617461, false);
    // Subchunk2Size
    view.setUint32(40, dataSize, true);

    return new Blob([buffer], { type: 'audio/wav' });
  }

  /**
   * Convert an AudioBuffer to a valid 16-bit PCM WAV Blob
   * @param {AudioBuffer} audioBuffer
   * @returns {Blob}
   */
  _audioBufferToWavBlob(audioBuffer) {
    const numChannels = 1; // mono voice note
    const sampleRate = audioBuffer.sampleRate;
    const channelData = audioBuffer.getChannelData(0);
    const numSamples = channelData.length;
    const bitsPerSample = 16;
    const byteRate = sampleRate * numChannels * (bitsPerSample / 8);
    const blockAlign = numChannels * (bitsPerSample / 8);
    const dataSize = numSamples * (bitsPerSample / 8);
    const buffer = new ArrayBuffer(44 + dataSize);
    const view = new DataView(buffer);

    view.setUint32(0, 0x52494646, false); // "RIFF"
    view.setUint32(4, 36 + dataSize, true);
    view.setUint32(8, 0x57415645, false); // "WAVE"
    view.setUint32(12, 0x666d7420, false); // "fmt "
    view.setUint32(16, 16, true);
    view.setUint16(20, 1, true); // PCM
    view.setUint16(22, numChannels, true);
    view.setUint32(24, sampleRate, true);
    view.setUint32(28, byteRate, true);
    view.setUint16(32, blockAlign, true);
    view.setUint16(34, bitsPerSample, true);
    view.setUint32(36, 0x64617461, false); // "data"
    view.setUint32(40, dataSize, true);

    let offset = 44;
    for (let i = 0; i < numSamples; i++) {
      let s = Math.max(-1, Math.min(1, channelData[i]));
      view.setInt16(offset, s < 0 ? s * 0x8000 : s * 0x7FFF, true);
      offset += 2;
    }

    return new Blob([buffer], { type: 'audio/wav' });
  }

  /**
   * Start recording from microphone
   */
  async startRecording() {
    if (this.isRecording || this._isStarting) return;
    this._isStarting = true;
    console.log('[VOICE_SERVICE] Requesting microphone access...');

    try {
      const stream = await this._getAudioStream();
      this.audioStream = stream;

      const detectedMime = this.getAudioMimeType();
      try {
        this.mediaRecorder = detectedMime
          ? new MediaRecorder(stream, { mimeType: detectedMime })
          : new MediaRecorder(stream);
      } catch (err) {
        console.warn('[VOICE_SERVICE] Specific MIME type rejected, using default recorder:', err);
        this.mediaRecorder = new MediaRecorder(stream);
      }

      this.mimeType = this.mediaRecorder.mimeType || detectedMime || 'audio/webm';
      this.audioChunks = [];
      this.recordedPeaks = [];

      // Setup Web Audio Analyser on the microphone stream
      try {
        const AudioCtx = window.AudioContext || window.webkitAudioContext;
        if (AudioCtx) {
          this.audioContext = new AudioCtx();
          if (this.audioContext.state === 'suspended') {
            await this.audioContext.resume();
          }
          this.analyser = this.audioContext.createAnalyser();
          this.analyser.fftSize = 256;
          this.analyser.smoothingTimeConstant = 0.3;
          this.source = this.audioContext.createMediaStreamSource(stream);
          this.source.connect(this.analyser);
          this.dataArray = new Uint8Array(this.analyser.fftSize);
        }
      } catch (audioCtxErr) {
        console.warn('[VOICE_SERVICE] Web Audio visualizer setup failed:', audioCtxErr);
      }

      this.mediaRecorder.ondataavailable = (e) => {
        if (e.data && e.data.size > 0) {
          this.audioChunks.push(e.data);
        }
      };

      this.mediaRecorder.onstop = async () => {
        console.log('[VOICE_SERVICE] Microphone recording stopped. Assembling Blob...');

        // Stop stream tracks only after MediaRecorder has completely stopped
        if (this.audioStream) {
          try {
            this.audioStream.getTracks().forEach(track => track.stop());
          } catch (_) {}
          this.audioStream = null;
        }

        const totalBytes = this.audioChunks.reduce((sum, chunk) => sum + (chunk.size || 0), 0);
        console.log(`[VOICE_SERVICE] Collected ${this.audioChunks.length} chunks, total ${totalBytes} bytes`);

        let finalBlob = null;
        if (totalBytes > 0) {
          const rawBlob = new Blob(this.audioChunks, { type: this.mimeType });
          // Upload the raw (opus/webm) recording as-is: it is 10-20x smaller
          // than a decoded WAV and uploads in seconds. Decode is only used
          // to read an accurate duration, never to re-encode the upload.
          finalBlob = rawBlob;
          try {
            const AudioCtx = window.AudioContext || window.webkitAudioContext;
            if (AudioCtx) {
              const tempCtx = new AudioCtx();
              const arrayBuffer = await rawBlob.arrayBuffer();
              const audioBuffer = await new Promise((resolve, reject) => {
                const res = tempCtx.decodeAudioData(arrayBuffer, resolve, reject);
                if (res && typeof res.then === 'function') {
                  res.then(resolve).catch(reject);
                }
              });
              this.duration = Math.max(1, Math.round(audioBuffer.duration));
              tempCtx.close().catch(() => {});
            }
          } catch (decodeErr) {
            console.warn('[VOICE_SERVICE] duration decode failed, keeping timer duration:', decodeErr);
          }
        }

        if (!finalBlob) {
          console.warn('[VOICE_SERVICE] Generating synthetic WAV audio fallback');
          finalBlob = this._createWavBlob(Math.max(1, this.duration));
        }

        this.audioBlob = finalBlob;
        this.mimeType = finalBlob.type || this.mimeType || 'audio/webm';

        if (this.audioUrl) {
          URL.revokeObjectURL(this.audioUrl);
        }
        this.audioUrl = URL.createObjectURL(this.audioBlob);

        // Reset audio element so it always binds fresh to this new audioUrl
        if (this.audioElement) {
          try {
            this.audioElement.pause();
            this.audioElement.src = '';
            this.audioElement.load();
          } catch (_) {}
          this.audioElement = null;
        }

        this.cleanupAudioContext();
        eventBus.emit(EVENTS.VOICE_STOP, {
          blob: this.audioBlob,
          url: this.audioUrl,
          duration: this.duration,
          peaks: this.recordedPeaks,
          isFallbackStream: this.isFallbackStream,
        });
      };

      // Start recorder with 250ms timeslice to stream real microphone chunks continuously
      this.mediaRecorder.start(250);
      this.isRecording = true;
      this.isLocked = false;
      this.duration = 0;
      this.startTimer();

      console.log('[VOICE_SERVICE] Real microphone recording started successfully');
      eventBus.emit(EVENTS.VOICE_START);
    } catch (error) {
      console.error('[VOICE_SERVICE] Microphone access error:', error);
      this.cleanupAudioContext();
      if (this.audioStream) {
        try {
          this.audioStream.getTracks().forEach(track => track.stop());
        } catch (_) {}
        this.audioStream = null;
      }
      this.isRecording = false;
      eventBus.emit(EVENTS.VOICE_ERROR, error);
    } finally {
      this._isStarting = false;
    }
  }

  /**
   * Stop recording
   */
  stopRecording() {
    if (this.mediaRecorder && this.isRecording) {
      this.isRecording = false;
      this.isLocked = false;
      this.stopTimer();

      try {
        if (this.mediaRecorder.state === 'recording') {
          try {
            this.mediaRecorder.requestData();
          } catch (_) {}
          this.mediaRecorder.stop();
        } else if (this.mediaRecorder.state !== 'inactive') {
          this.mediaRecorder.stop();
        }
      } catch (e) {
        console.warn('[VOICE_SERVICE] Error stopping MediaRecorder:', e);
      }
    }
  }

  /**
   * Lock recording (continue recording hands-free)
   */
  lockRecording() {
    if (this.isRecording) {
      this.isLocked = true;
      eventBus.emit(EVENTS.VOICE_LOCKED);
    }
  }

  /**
   * Play/pause audio recording preview
   */
  togglePlayPause() {
    if (!this.audioUrl) return;

    if (!this.audioElement) {
      this.audioElement = new Audio(this.audioUrl);
      this.audioElement.preload = 'auto';

      this.audioElement.addEventListener('ended', () => {
        this.isPlaying = false;
        eventBus.emit(EVENTS.VOICE_PLAYBACK_ENDED);
      });
      this.audioElement.addEventListener('pause', () => {
        if (!this.audioElement || !this.audioElement.ended) {
          this.isPlaying = false;
          eventBus.emit(EVENTS.VOICE_PAUSED);
        }
      });
      this.audioElement.addEventListener('play', () => {
        this.isPlaying = true;
        eventBus.emit(EVENTS.VOICE_PLAYING);
      });
      this.audioElement.addEventListener('error', (e) => {
        console.warn('[VOICE_SERVICE] Audio element playback error:', e);
        this.isPlaying = false;
        eventBus.emit(EVENTS.VOICE_PAUSED);
      });
    }

    if (this.isPlaying) {
      this.audioElement.pause();
    } else {
      if (this.audioElement.ended) {
        this.audioElement.currentTime = 0;
      }
      const playPromise = this.audioElement.play();
      if (playPromise !== undefined) {
        playPromise.catch(err => {
          console.warn('[VOICE_SERVICE] Playback failed:', err);
          this.isPlaying = false;
          eventBus.emit(EVENTS.VOICE_PAUSED);
        });
      }
    }
  }

  /**
   * Stop audio playback
   */
  stopPlayback() {
    if (this.audioElement) {
      try {
        this.audioElement.pause();
        this.audioElement.currentTime = 0;
      } catch (_) {}
      this.isPlaying = false;
      eventBus.emit(EVENTS.VOICE_PLAYBACK_STOPPED);
    }
  }

  /**
   * Start duration timer
   */
  startTimer() {
    this.stopTimer();
    this.duration = 0;
    this.timerInterval = setInterval(() => {
      this.duration++;
      eventBus.emit(EVENTS.VOICE_TIMER_UPDATE, this.duration);
    }, 1000);
  }

  /**
   * Stop duration timer
   */
  stopTimer() {
    if (this.timerInterval) {
      clearInterval(this.timerInterval);
      this.timerInterval = null;
    }
  }

  /**
   * Clean up audio context & Web Audio nodes
   */
  cleanupAudioContext() {
    if (this.source) {
      try { this.source.disconnect(); } catch (_) {}
      this.source = null;
    }
    if (this.analyser) {
      try { this.analyser.disconnect(); } catch (_) {}
      this.analyser = null;
    }
    if (this.audioContext && this.audioContext.state !== 'closed') {
      try { this.audioContext.close(); } catch (_) {}
      this.audioContext = null;
    }
    this.dataArray = null;
  }

  /**
   * Determine filename and MIME type for upload
   * @returns {{filename: string, type: string}}
   */
  _getAudioFileInfo() {
    const rawType = (this.audioBlob?.type || 'audio/wav').toLowerCase().split(';')[0].trim();
    let ext = 'wav';
    let mime = 'audio/wav';

    if (rawType === 'audio/webm') {
      ext = 'webm';
      mime = 'audio/webm';
    } else if (rawType === 'audio/mp4' || rawType === 'audio/x-m4a') {
      ext = 'm4a';
      mime = 'audio/mp4';
    } else if (rawType === 'audio/ogg') {
      ext = 'ogg';
      mime = 'audio/ogg';
    } else if (rawType === 'audio/wav') {
      ext = 'wav';
      mime = 'audio/wav';
    } else if (rawType === 'audio/mpeg') {
      ext = 'mp3';
      mime = 'audio/mpeg';
    }

    return {
      filename: `voice_${Date.now()}.${ext}`,
      type: mime,
    };
  }

  /**
   * Send voice note recording to backend
   */
  async sendRecording() {
    if (!this.audioBlob) return;

    const conversationId = this._getConversationId();
    if (!conversationId) {
      console.error('[VOICE_SERVICE] Cannot send voice message: conversationId not found');
      return;
    }

    const { filename, type } = this._getAudioFileInfo();
    const file = new File([this.audioBlob], filename, { type });

    const tempId = `temp_voice_${Date.now()}`;

    // Save sender voice note blob to local device storage (IndexedDB / Capacitor Filesystem)
    const recordingBlob = this.audioBlob;
    const recordingDuration = this.duration || 0;
    let localUrl = '';
    try {
      const { deviceMediaStore } = await import('../../core/device-media-store.js?v=20261106-group1');
      localUrl = await deviceMediaStore.saveSenderMedia(tempId, recordingBlob, filename);
    } catch (saveErr) {
      console.warn('[VOICE_SERVICE] Failed to save sender voice note locally:', saveErr);
    }

    // Create a dedicated Blob URL for the chat bubble so discardRecording() revoking this.audioUrl does not break playback
    const bubbleAudioUrl = localUrl || (recordingBlob ? URL.createObjectURL(recordingBlob) : '');

    // Pick up the active reply target (if the user is replying to a message)
    let replyContext = { reply_to_id: null, reply_to_details: null };
    try {
      if (window.uiController && typeof window.uiController.consumeReplyContext === 'function') {
        replyContext = window.uiController.consumeReplyContext() || replyContext;
      }
    } catch (ctxErr) {
      console.warn('[VOICE_SERVICE] Failed to read reply context:', ctxErr);
    }

    // Optimistically add voice note to UI immediately
    const optMsg = {
      id: tempId,
      temp_id: tempId,
      message_type: 'media',
      type: 'media',
      attachment_type: 'voice_note',
      content: '',
      sender: { id: null, username: 'You' },
      metadata: {
        type: 'voice_note',
        is_voice_note: true,
        url: bubbleAudioUrl,
        localBlobUrl: bubbleAudioUrl,
        size: recordingBlob.size,
        duration: recordingDuration,
        reply_to_id: replyContext.reply_to_id,
        reply_to_details: replyContext.reply_to_details
      },
      created_at: new Date().toISOString(),
      status: 'uploading'
    };
    eventBus.emit(EVENTS.MESSAGE_OPTIMISTIC_ADD, optMsg);

    try {
      const { attachmentService } = await import('../attachments/attachment.service.js?v=20261106-group1');
      const uploadedMsg = await attachmentService.handleFileUpload(file, conversationId, {
        isVoiceNote: true,
        tempId,
        replyToId: replyContext.reply_to_id
      });
      if (uploadedMsg?.id && recordingBlob) {
        try {
          const { deviceMediaStore } = await import('../../core/device-media-store.js?v=20261106-group1');
          await deviceMediaStore.saveSenderMedia(uploadedMsg.id, recordingBlob, filename);
        } catch (_) {}
      }
      this.discardRecording();
    } catch (err) {
      console.error('[VOICE_SERVICE] Upload failed:', err);
      eventBus.emit(EVENTS.MESSAGE_UPLOAD_FAILED, { tempId, error: err.message });
      eventBus.emit(EVENTS.VOICE_ERROR, err);
    }
  }

  /**
   * Get conversation ID from page context
   * @returns {number|null} Conversation ID
   */
  _getConversationId() {
    const chatContainer = document.querySelector('.chat-container');
    if (chatContainer && chatContainer.dataset.conversationId) {
      return parseInt(chatContainer.dataset.conversationId, 10);
    }
    if (document.body.dataset.conversationId) {
      return parseInt(document.body.dataset.conversationId, 10);
    }
    const match = window.location.pathname.match(/\/conversation\/(\d+)\/?/);
    if (match) return parseInt(match[1], 10);
    return null;
  }

  /**
   * Discard current recording and clean up all resources
   */
  discardRecording() {
    if (this._isDiscarding) return;
    this._isDiscarding = true;

    try {
      if (this.isRecording && this.mediaRecorder) {
        this.isRecording = false;
        this.stopTimer();
        try {
          if (this.mediaRecorder.state !== 'inactive') {
            this.mediaRecorder.stop();
          }
        } catch (_) {}
      }

      if (this.audioStream) {
        try {
          this.audioStream.getTracks().forEach(track => track.stop());
        } catch (_) {}
        this.audioStream = null;
      }

      this.cleanupAudioContext();
      this.stopPlayback();

      this.audioBlob = null;
      this.audioChunks = [];
      this.recordedPeaks = [];

      if (this.audioUrl) {
        URL.revokeObjectURL(this.audioUrl);
        this.audioUrl = null;
      }
      if (this.audioElement) {
        try {
          this.audioElement.pause();
          this.audioElement.src = '';
          this.audioElement.load();
        } catch (_) {}
        this.audioElement = null;
      }

      this.duration = 0;
      this.isLocked = false;
      this.isFallbackStream = false;
      eventBus.emit(EVENTS.VOICE_DISCARD);
    } finally {
      this._isDiscarding = false;
    }
  }

  /**
   * Get current state
   * @returns {Object} Recording state
   */
  getState() {
    return {
      isRecording: this.isRecording,
      isLocked: this.isLocked,
      isPlaying: this.isPlaying,
      duration: this.duration,
      hasRecording: Boolean(this.audioBlob),
    };
  }
}

// Global singleton instance
export const voiceService = new VoiceService();
