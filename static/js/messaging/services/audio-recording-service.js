/**
 * Audio Recording Service
 * 
 * Decouples audio recording from WebSocket.
 * Records locally, allows preview, and uploads via HTTP.
 * WebSocket only sends metadata after upload.
 * 
 * Compatible with existing voice.service.js API and eventBus events.
 */

import { EVENTS } from '../../../chat/shared/constants.js?v=20261106-group1';
import { eventBus } from '../../../chat/core/event-bus.js?v=20261106-group1';

class AudioRecordingService {
    constructor() {
        this.mediaRecorder = null;
        this.audioChunks = [];
        this.audioBlob = null;
        this.audioUrl = null;
        this.isRecording = false;
        this.isLocked = false; // For compatibility with existing voice service
        this.isPaused = false;
        this.recordingStartTime = null;
        this.duration = 0;
        this.durationInterval = null;
        
        // Touch/swipe state (for compatibility)
        this.touchStartY = 0;
        this.touchCurrentY = 0;
        this.isSwipingUp = false;
        
        // Audio constraints
        this.MAX_DURATION = 300; // 5 minutes max
        this.PREFERRED_SAMPLE_RATE = 44100;
        
        // Preview state
        this.previewAudio = null;
        this.isPreviewPlaying = false;
    }
    
    /**
     * Check if browser supports audio recording
     */
    isSupported() {
        return !!(navigator.mediaDevices && navigator.mediaDevices.getUserMedia && MediaRecorder.isTypeSupported);
    }
    
    /**
     * Request microphone access
     */
    async requestMicrophoneAccess() {
        try {
            const stream = await navigator.mediaDevices.getUserMedia({
                audio: {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true,
                    sampleRate: this.PREFERRED_SAMPLE_RATE
                }
            });
            return stream;
        } catch (error) {
            console.error('[AUDIO_RECORDING] Failed to access microphone:', error);
            throw new Error('Microphone access denied or not available');
        }
    }
    
    /**
     * Start recording
     */
    async startRecording() {
        if (this.isRecording) {
            console.warn('[AUDIO_RECORDING] Already recording');
            return;
        }
        
        try {
            const stream = await this.requestMicrophoneAccess();
            
            // Create MediaRecorder
            const mimeType = this.getSupportedMimeType();
            this.mediaRecorder = new MediaRecorder(stream, { mimeType });
            
            this.audioChunks = [];
            this.recordingStartTime = Date.now();
            this.isRecording = true;
            this.isPaused = false;
            this.isLocked = false;
            this.duration = 0;
            
            // Collect audio chunks
            this.mediaRecorder.ondataavailable = (event) => {
                if (event.data.size > 0) {
                    this.audioChunks.push(event.data);
                }
            };
            
            // Handle recording stop
            this.mediaRecorder.onstop = () => {
                this.onRecordingStopped();
            };
            
            // Start recording
            this.mediaRecorder.start(100); // Collect chunks every 100ms
            
            // Start duration timer
            this.startTimer();
            
            console.log('[AUDIO_RECORDING] Recording started');
            eventBus.emit(EVENTS.VOICE_START);
            
        } catch (error) {
            console.error('[AUDIO_RECORDING] Failed to start recording:', error);
            eventBus.emit(EVENTS.VOICE_ERROR, error);
            throw error;
        }
    }
    
    /**
     * Pause recording
     */
    pauseRecording() {
        if (!this.isRecording || this.isPaused) {
            return;
        }
        
        this.mediaRecorder.pause();
        this.isPaused = true;
        this.stopTimer();
        
        console.log('[AUDIO_RECORDING] Recording paused');
    }
    
    /**
     * Resume recording
     */
    resumeRecording() {
        if (!this.isRecording || !this.isPaused) {
            return;
        }
        
        this.mediaRecorder.resume();
        this.isPaused = false;
        this.startTimer();
        
        console.log('[AUDIO_RECORDING] Recording resumed');
    }
    
    /**
     * Stop recording
     */
    stopRecording() {
        if (!this.isRecording) {
            return;
        }
        
        this.mediaRecorder.stop();
        this.stopTimer();
        
        // Stop all tracks
        this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
        
        this.isRecording = false;
        this.isPaused = false;
        this.isLocked = false;
        
        console.log('[AUDIO_RECORDING] Recording stopped');
    }
    
    /**
     * Handle recording stopped
     */
    onRecordingStopped() {
        // Create audio blob
        const mimeType = this.getSupportedMimeType();
        this.audioBlob = new Blob(this.audioChunks, { type: mimeType });
        
        // Create audio URL for preview
        this.audioUrl = URL.createObjectURL(this.audioBlob);
        
        console.log(`[AUDIO_RECORDING] Recording stopped. Duration: ${this.duration}s, Size: ${this.audioBlob.size} bytes`);
        
        // Emit VOICE_STOP event with blob data (compatible with existing voice.service.js)
        eventBus.emit(EVENTS.VOICE_STOP, {
            blob: this.audioBlob,
            url: this.audioUrl,
            duration: this.duration,
        });
    }
    
    /**
     * Cancel recording
     */
    cancelRecording() {
        if (this.isRecording) {
            this.stopRecording();
        }
        
        // Cleanup
        this.audioChunks = [];
        this.audioBlob = null;
        if (this.audioUrl) {
            URL.revokeObjectURL(this.audioUrl);
            this.audioUrl = null;
        }
        this.duration = 0;
        
        console.log('[AUDIO_RECORDING] Recording cancelled');
    }
    
    /**
     * Discard current recording
     */
    discardRecording() {
        this.stopPlayback();
        this.audioBlob = null;
        this.audioChunks = [];
        if (this.audioUrl) {
            URL.revokeObjectURL(this.audioUrl);
            this.audioUrl = null;
        }
        this.duration = 0;
        this.isLocked = false;
        this.isSwipingUp = false;
        
        console.log('[AUDIO_RECORDING] Recording discarded');
        eventBus.emit(EVENTS.VOICE_DISCARD);
    }
    
    /**
     * Lock recording (continue recording without holding)
     */
    lockRecording() {
        if (this.isRecording) {
            this.isLocked = true;
            eventBus.emit(EVENTS.VOICE_LOCKED);
        }
    }
    
    /**
     * Stop locked recording
     */
    stopLockedRecording() {
        if (this.mediaRecorder && this.isRecording && this.isLocked) {
            this.mediaRecorder.stop();
            this.mediaRecorder.stream.getTracks().forEach(track => track.stop());
            this.isRecording = false;
            this.isLocked = false;
            this.stopTimer();
        }
    }
    
    /**
     * Handle touch start for swipe detection
     */
    handleTouchStart(y) {
        this.touchStartY = y;
        this.touchCurrentY = y;
        this.isSwipingUp = false;
    }
    
    /**
     * Handle touch move for swipe detection
     */
    handleTouchMove(y) {
        this.touchCurrentY = y;
        const deltaY = this.touchStartY - this.touchCurrentY;
        
        // Swipe up detection (threshold of 50px)
        if (deltaY > 50 && !this.isSwipingUp && this.isRecording) {
            this.isSwipingUp = true;
            this.lockRecording();
        }
    }
    
    /**
     * Re-record (discard and start new)
     */
    async reRecord() {
        this.discardRecording();
        await this.startRecording();
    }
    
    /**
     * Get supported MIME type
     */
    getSupportedMimeType() {
        const types = [
            'audio/webm;codecs=opus',
            'audio/webm',
            'audio/ogg;codecs=opus',
            'audio/ogg',
            'audio/mp4',
            'audio/wav'
        ];
        
        for (const type of types) {
            if (MediaRecorder.isTypeSupported(type)) {
                return type;
            }
        }
        
        return '';
    }
    
    /**
     * Start timer
     */
    startTimer() {
        this.durationInterval = setInterval(() => {
            this.duration++;
            eventBus.emit(EVENTS.VOICE_TIMER_UPDATE, this.duration);
        }, 1000);
    }
    
    /**
     * Stop timer
     */
    stopTimer() {
        if (this.durationInterval) {
            clearInterval(this.durationInterval);
            this.durationInterval = null;
        }
    }
    
    /**
     * Preview audio (toggle play/pause)
     */
    togglePlayPause() {
        if (!this.audioUrl) return;

        if (!this.previewAudio) {
            this.previewAudio = new Audio(this.audioUrl);
            this.previewAudio.addEventListener('ended', () => {
                this.isPreviewPlaying = false;
                eventBus.emit(EVENTS.VOICE_PLAYBACK_ENDED);
            });
        }

        if (this.isPreviewPlaying) {
            this.previewAudio.pause();
            this.isPreviewPlaying = false;
            eventBus.emit(EVENTS.VOICE_PAUSED);
        } else {
            this.previewAudio.play();
            this.isPreviewPlaying = true;
            eventBus.emit(EVENTS.VOICE_PLAYING);
        }
    }
    
    /**
     * Stop audio playback
     */
    stopPlayback() {
        if (this.previewAudio && this.isPreviewPlaying) {
            this.previewAudio.pause();
            this.previewAudio.currentTime = 0;
            this.isPreviewPlaying = false;
            eventBus.emit(EVENTS.VOICE_PLAYBACK_STOPPED);
        }
    }
    
    /**
     * Get audio duration
     */
    async getAudioDuration() {
        if (!this.audioBlob) return 0;
        
        return new Promise((resolve) => {
            const audio = new Audio(this.audioUrl);
            audio.onloadedmetadata = () => {
                resolve(audio.duration);
            };
            audio.onerror = () => resolve(0);
        });
    }
    
    /**
     * Generate waveform data (simplified version)
     */
    async generateWaveform() {
        if (!this.audioBlob) return null;
        
        try {
            const audioContext = new (window.AudioContext || window.webkitAudioContext)();
            const arrayBuffer = await this.audioBlob.arrayBuffer();
            const audioBuffer = await audioContext.decodeAudioData(arrayBuffer);
            
            const channelData = audioBuffer.getChannelData(0);
            const samples = 100; // Number of waveform points
            const blockSize = Math.floor(channelData.length / samples);
            const waveform = [];
            
            for (let i = 0; i < samples; i++) {
                const start = i * blockSize;
                const end = start + blockSize;
                let sum = 0;
                
                for (let j = start; j < end; j++) {
                    sum += Math.abs(channelData[j]);
                }
                
                waveform.push(sum / blockSize);
            }
            
            return waveform;
            
        } catch (error) {
            console.error('[AUDIO_RECORDING] Failed to generate waveform:', error);
            return null;
        }
    }
    
    /**
     * Send recording (compatible with existing voice.service.js)
     */
    sendRecording() {
        if (this.audioBlob) {
            const file = new File([this.audioBlob], 'voice.webm', { type: 'audio/webm' });
            
            // Get conversation ID and upload through attachment service
            const conversationId = this._getConversationId();
            if (conversationId) {
                // Import attachment service to avoid circular dependency
                import('../../chat/features/attachments/attachment.service.js?v=20261106-group1').then(({ attachmentService }) => {
                    attachmentService.handleFileUpload(file, conversationId);
                });
            }
            
            this.discardRecording();
        }
    }
    
    /**
     * Get conversation ID from the page
     * @returns {number|null} Conversation ID
     */
    _getConversationId() {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            return parseInt(chatContainer.dataset.conversationId);
        }
        return null;
    }
    
    /**
     * Get recording state (compatible with existing voice.service.js)
     * @returns {Object} Recording state
     */
    getState() {
        return {
            isRecording: this.isRecording,
            isLocked: this.isLocked,
            isPlaying: this.isPreviewPlaying,
            duration: this.duration,
            hasRecording: !!this.audioBlob,
        };
    }
    
    /**
     * Emit recording state change event
     */
    emitRecordingStateChange(state, data = {}) {
        const event = new CustomEvent('audioRecordingStateChange', {
            detail: { state, data, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    /**
     * Emit duration update event
     */
    emitDurationUpdate(duration) {
        const event = new CustomEvent('audioRecordingDurationUpdate', {
            detail: { duration, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    /**
     * Emit preview state change event
     */
    emitPreviewStateChange(state) {
        const event = new CustomEvent('audioPreviewStateChange', {
            detail: { state, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    /**
     * Emit recording error event
     */
    emitRecordingError(error) {
        const event = new CustomEvent('audioRecordingError', {
            detail: { error, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    /**
     * Emit preview error event
     */
    emitPreviewError(error) {
        const event = new CustomEvent('audioPreviewError', {
            detail: { error, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    /**
     * Listen for recording state changes
     */
    onRecordingStateChange(callback) {
        window.addEventListener('audioRecordingStateChange', (event) => {
            callback(event.detail);
        });
    }
    
    /**
     * Listen for duration updates
     */
    onDurationUpdate(callback) {
        window.addEventListener('audioRecordingDurationUpdate', (event) => {
            callback(event.detail);
        });
    }
    
    /**
     * Listen for preview state changes
     */
    onPreviewStateChange(callback) {
        window.addEventListener('audioPreviewStateChange', (event) => {
            callback(event.detail);
        });
    }
    
    /**
     * Listen for recording errors
     */
    onRecordingError(callback) {
        window.addEventListener('audioRecordingError', (event) => {
            callback(event.detail);
        });
    }
    
    /**
     * Initialize audio recording service
     */
    init() {
        console.log('[AUDIO_RECORDING] Audio recording service initializing...');
        this.setupEventListeners();
        console.log('[AUDIO_RECORDING] Audio recording service initialized');
    }
    
    /**
     * Setup event listeners (compatible with existing voice.service.js)
     */
    setupEventListeners() {
        console.log('[AUDIO_RECORDING] Setting up event listeners');
        
        eventBus.on(EVENTS.VOICE_START, () => {
            console.log('[AUDIO_RECORDING] VOICE_START event received');
            this.startRecording();
        });

        eventBus.on(EVENTS.VOICE_STOP, () => {
            console.log('[AUDIO_RECORDING] VOICE_STOP event received');
            this.stopRecording();
        });

        eventBus.on(EVENTS.VOICE_SEND, () => {
            console.log('[AUDIO_RECORDING] VOICE_SEND event received');
            this.sendRecording();
        });

        eventBus.on(EVENTS.VOICE_LOCK, () => {
            console.log('[AUDIO_RECORDING] VOICE_LOCK event received');
            this.lockRecording();
        });

        eventBus.on(EVENTS.VOICE_PLAY_PAUSE, () => {
            console.log('[AUDIO_RECORDING] VOICE_PLAY_PAUSE event received');
            this.togglePlayPause();
        });
    }
    
    /**
     * Cleanup resources
     */
    cleanup() {
        this.stopRecording();
        this.stopPlayback();
        
        if (this.audioUrl) {
            URL.revokeObjectURL(this.audioUrl);
            this.audioUrl = null;
        }
        
        this.audioChunks = [];
        this.audioBlob = null;
        this.previewAudio = null;
        
        console.log('[AUDIO_RECORDING] Cleanup complete');
    }
}

// Create global instance (compatible with existing voice.service.js naming)
export const voiceService = new AudioRecordingService();
export default voiceService;
