/**
 * Messaging Orchestrator
 * 
 * Centralizes queue management, upload lifecycle, and sync state.
 * Coordinates all messaging services for scalability and maintainability.
 */

import MessageQueueService from './message-queue-service.js?v=20261106-group1';
import ReconnectionSyncEngine from './reconnection-sync-engine.js?v=20261106-group1';
import AudioRecordingService from './audio-recording-service.js?v=20261106-group1';
import connectionManager from './connection-manager.js?v=20261106-group1';
import { messageSoundManager } from '../../chat/shared/message-sound.js?v=20261106-group1';

class MessagingOrchestrator {
    constructor() {
        this.messageQueueService = new MessageQueueService();
        this.reconnectionSyncEngine = new ReconnectionSyncEngine(this.messageQueueService);
        this.audioRecordingService = new AudioRecordingService(this.messageQueueService);
        this.connectionManager = connectionManager;
        
        this.isInitialized = false;
        this.servicesReady = {
            queue: false,
            sync: false,
            audio: false,
            connection: false
        };
        
        // Orchestrator state
        this.state = {
            isOnline: navigator.onLine,
            activeTab: false,
            queueStats: null,
            syncState: null,
            audioState: null
        };
    }
    
    /**
     * Initialize all messaging services
     */
    async init() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing services...');
        
        try {
            // Initialize connection manager first
            await this.initConnectionManager();
            
            // Initialize message queue
            await this.initMessageQueue();
            
            // Initialize sync engine
            await this.initSyncEngine();
            
            // Initialize audio recording
            await this.initAudioRecording();
            
            // Initialize message sound manager
            await this.initMessageSound();
            
            // Setup event listeners
            this.setupEventListeners();
            
            this.isInitialized = true;
            console.log('[MESSAGING_ORCHESTRATOR] All services initialized');
            
            this.emitInitialized();
            
        } catch (error) {
            console.error('[MESSAGING_ORCHESTRATOR] Initialization failed:', error);
            this.emitInitializationError(error);
            throw error;
        }
    }
    
    /**
     * Initialize connection manager
     */
    async initConnectionManager() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing connection manager...');
        
        this.connectionManager.onConnectionGranted(() => {
            this.state.activeTab = true;
            this.emitStateChange('activeTab', true);
        });
        
        this.connectionManager.onConnectionDenied(() => {
            this.state.activeTab = false;
            this.emitStateChange('activeTab', false);
        });
        
        this.connectionManager.onStateChange((state) => {
            this.state.connectionState = state;
            this.emitStateChange('connection', state);
        });
        
        this.servicesReady.connection = true;
        console.log('[MESSAGING_ORCHESTRATOR] Connection manager ready');
    }
    
    /**
     * Initialize message queue
     */
    async initMessageQueue() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing message queue...');
        
        await this.messageQueueService.init();
        
        // Listen for state changes
        this.messageQueueService.onStateChange((detail) => {
            this.handleMessageStateChange(detail);
        });
        
        // Update queue stats periodically
        this.updateQueueStats();
        setInterval(() => this.updateQueueStats(), 5000);
        
        this.servicesReady.queue = true;
        console.log('[MESSAGING_ORCHESTRATOR] Message queue ready');
    }
    
    /**
     * Initialize sync engine
     */
    async initSyncEngine() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing sync engine...');
        
        await this.reconnectionSyncEngine.init();
        
        // Listen for sync events
        this.reconnectionSyncEngine.onSyncProgress((detail) => {
            this.state.syncState = detail;
            this.emitSyncProgress(detail);
        });
        
        this.reconnectionSyncEngine.onSyncComplete((detail) => {
            this.state.syncState = { status: 'complete', ...detail };
            this.emitSyncComplete(detail);
        });
        
        this.reconnectionSyncEngine.onSyncError((detail) => {
            this.state.syncState = { status: 'error', ...detail };
            this.emitSyncError(detail);
        });
        
        this.reconnectionSyncEngine.onMessagesFetched((detail) => {
            this.emitMessagesFetched(detail);
        });
        
        this.servicesReady.sync = true;
        console.log('[MESSAGING_ORCHESTRATOR] Sync engine ready');
    }
    
    /**
     * Initialize audio recording
     */
    async initAudioRecording() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing audio recording...');
        
        // Listen for audio events
        this.audioRecordingService.onRecordingStateChange((detail) => {
            this.state.audioState = detail;
            this.emitAudioStateChange(detail);
        });
        
        this.audioRecordingService.onDurationUpdate((detail) => {
            this.emitAudioDurationUpdate(detail);
        });
        
        this.audioRecordingService.onPreviewStateChange((detail) => {
            this.emitAudioPreviewStateChange(detail);
        });
        
        this.audioRecordingService.onRecordingError((detail) => {
            this.emitAudioError(detail);
        });
        
        this.servicesReady.audio = true;
        console.log('[MESSAGING_ORCHESTRATOR] Audio recording ready');
    }
    
    /**
     * Initialize message sound manager
     */
    async initMessageSound() {
        console.log('[MESSAGING_ORCHESTRATOR] Initializing message sound manager...');
        
        try {
            await messageSoundManager.init();
            console.log('[MESSAGING_ORCHESTRATOR] Message sound manager ready');
        } catch (error) {
            console.warn('[MESSAGING_ORCHESTRATOR] Message sound manager initialization failed:', error);
        }
    }
    
    /**
     * Setup event listeners
     */
    setupEventListeners() {
        // Online/offline events
        window.addEventListener('online', () => {
            this.state.isOnline = true;
            this.emitStateChange('online', true);
            this.handleOnline();
        });
        
        window.addEventListener('offline', () => {
            this.state.isOnline = false;
            this.emitStateChange('online', false);
            this.handleOffline();
        });
        
        // WebSocket events (from existing system)
        window.addEventListener('websocket:connected', () => {
            this.handleWebSocketConnected();
        });
        
        window.addEventListener('websocket:disconnected', () => {
            this.handleWebSocketDisconnected();
        });
    }
    
    /**
     * Handle online event
     */
    handleOnline() {
        console.log('[MESSAGING_ORCHESTRATOR] Device online');
        
        // Retry failed uploads
        if (this.servicesReady.queue) {
            this.messageQueueService.retryFailedMessages();
        }
    }
    
    /**
     * Handle offline event
     */
    handleOffline() {
        console.log('[MESSAGING_ORCHESTRATOR] Device offline');
        
        // Pause upload queue
        // Messages will be queued and retried when online
    }
    
    /**
     * Handle WebSocket connected
     */
    handleWebSocketConnected() {
        console.log('[MESSAGING_ORCHESTRATOR] WebSocket connected');
        
        // Sync engine will handle reconnection sync automatically
    }
    
    /**
     * Handle WebSocket disconnected
     */
    handleWebSocketDisconnected() {
        console.log('[MESSAGING_ORCHESTRATOR] WebSocket disconnected');
    }
    
    /**
     * Handle message state changes
     */
    handleMessageStateChange(detail) {
        const { tempId, oldStatus, newStatus, message } = detail;
        
        console.log(`[MESSAGING_ORCHESTRATOR] Message ${tempId}: ${oldStatus} -> ${newStatus}`);
        
        // Play sound for queued/sending → sent transitions
        if (messageSoundManager.shouldPlaySound(oldStatus, newStatus)) {
            messageSoundManager.playSendSound();
        }
        
        // Update queue stats
        this.updateQueueStats();
        
        // Emit message state change
        this.emitMessageStateChange(detail);
    }
    
    /**
     * Update queue statistics
     */
    updateQueueStats() {
        if (!this.servicesReady.queue) return;
        
        this.state.queueStats = this.messageQueueService.getStats();
        this.emitQueueStatsUpdate(this.state.queueStats);
    }
    
    /**
     * Create a new message
     */
    async createMessage(messageData) {
        if (!this.servicesReady.queue) {
            throw new Error('Message queue not ready');
        }
        
        return await this.messageQueueService.createMessage(messageData);
    }
    
    /**
     * Queue message for upload
     */
    async queueMessage(tempId) {
        if (!this.servicesReady.queue) {
            throw new Error('Message queue not ready');
        }
        
        return await this.messageQueueService.queueForUpload(tempId);
    }
    
    /**
     * Get message by temp ID
     */
    getMessage(tempId) {
        if (!this.servicesReady.queue) return null;
        return this.messageQueueService.getMessage(tempId);
    }
    
    /**
     * Get conversation messages
     */
    getConversationMessages(conversationId) {
        if (!this.servicesReady.queue) return [];
        return this.messageQueueService.getConversationMessages(conversationId);
    }
    
    /**
     * Update message status
     */
    async updateMessageStatus(tempId, status) {
        if (!this.servicesReady.queue) return;
        await this.messageQueueService.updateMessageStatus(tempId, status);
    }
    
    /**
     * Delete message
     */
    async deleteMessage(tempId) {
        if (!this.servicesReady.queue) return;
        await this.messageQueueService.deleteMessage(tempId);
    }
    
    /**
     * Audio recording methods
     */
    async startAudioRecording() {
        if (!this.servicesReady.audio) {
            throw new Error('Audio recording not ready');
        }
        return await this.audioRecordingService.startRecording();
    }
    
    pauseAudioRecording() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.pauseRecording();
    }
    
    resumeAudioRecording() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.resumeRecording();
    }
    
    stopAudioRecording() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.stopRecording();
    }
    
    cancelAudioRecording() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.cancelRecording();
    }
    
    previewAudio() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.previewAudio();
    }
    
    pauseAudioPreview() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.pausePreview();
    }
    
    resumeAudioPreview() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.resumeAudioPreview();
    }
    
    stopAudioPreview() {
        if (!this.servicesReady.audio) return;
        this.audioRecordingService.stopPreview();
    }
    
    async sendAudioMessage(conversationId, caption) {
        if (!this.servicesReady.audio) {
            throw new Error('Audio recording not ready');
        }
        return await this.audioRecordingService.sendAudioMessage(conversationId, caption);
    }
    
    /**
     * Sync methods
     */
    async forceSync() {
        if (!this.servicesReady.sync) {
            throw new Error('Sync engine not ready');
        }
        return await this.reconnectionSyncEngine.forceSync();
    }
    
    /**
     * Connection methods
     */
    isTabActive() {
        return this.connectionManager.isTabActive();
    }
    
    getConnectionState() {
        return this.connectionManager.getState();
    }
    
    /**
     * Get orchestrator state
     */
    getState() {
        return {
            isInitialized: this.isInitialized,
            servicesReady: this.servicesReady,
            ...this.state
        };
    }
    
    /**
     * Emit events
     */
    emitInitialized() {
        const event = new CustomEvent('messagingOrchestratorInitialized', {
            detail: this.getState()
        });
        window.dispatchEvent(event);
    }
    
    emitInitializationError(error) {
        const event = new CustomEvent('messagingOrchestratorError', {
            detail: { error }
        });
        window.dispatchEvent(event);
    }
    
    emitStateChange(type, data) {
        const event = new CustomEvent('messagingStateChange', {
            detail: { type, data, timestamp: Date.now() }
        });
        window.dispatchEvent(event);
    }
    
    emitSyncProgress(detail) {
        const event = new CustomEvent('syncProgress', detail);
        window.dispatchEvent(event);
    }
    
    emitSyncComplete(detail) {
        const event = new CustomEvent('syncComplete', detail);
        window.dispatchEvent(event);
    }
    
    emitSyncError(detail) {
        const event = new CustomEvent('syncError', detail);
        window.dispatchEvent(event);
    }
    
    emitMessagesFetched(detail) {
        const event = new CustomEvent('messagesFetched', detail);
        window.dispatchEvent(event);
    }
    
    emitQueueStatsUpdate(stats) {
        const event = new CustomEvent('queueStatsUpdate', { detail: stats });
        window.dispatchEvent(event);
    }
    
    emitMessageStateChange(detail) {
        const event = new CustomEvent('messageStateChange', { detail });
        window.dispatchEvent(event);
    }
    
    emitAudioStateChange(detail) {
        const event = new CustomEvent('audioStateChange', { detail });
        window.dispatchEvent(event);
    }
    
    emitAudioDurationUpdate(detail) {
        const event = new CustomEvent('audioDurationUpdate', { detail });
        window.dispatchEvent(event);
    }
    
    emitAudioPreviewStateChange(detail) {
        const event = new CustomEvent('audioPreviewStateChange', { detail });
        window.dispatchEvent(event);
    }
    
    emitAudioError(detail) {
        const event = new CustomEvent('audioError', { detail });
        window.dispatchEvent(event);
    }
    
    /**
     * Event listener methods
     */
    onInitialized(callback) {
        window.addEventListener('messagingOrchestratorInitialized', (event) => {
            callback(event.detail);
        });
    }
    
    onError(callback) {
        window.addEventListener('messagingOrchestratorError', (event) => {
            callback(event.detail);
        });
    }
    
    onStateChange(callback) {
        window.addEventListener('messagingStateChange', (event) => {
            callback(event.detail);
        });
    }
    
    onSyncProgress(callback) {
        window.addEventListener('syncProgress', (event) => {
            callback(event.detail);
        });
    }
    
    onSyncComplete(callback) {
        window.addEventListener('syncComplete', (event) => {
            callback(event.detail);
        });
    }
    
    onSyncError(callback) {
        window.addEventListener('syncError', (event) => {
            callback(event.detail);
        });
    }
    
    onMessagesFetched(callback) {
        window.addEventListener('messagesFetched', (event) => {
            callback(event.detail);
        });
    }
    
    onQueueStatsUpdate(callback) {
        window.addEventListener('queueStatsUpdate', (event) => {
            callback(event.detail);
        });
    }
    
    onMessageStateChange(callback) {
        window.addEventListener('messageStateChange', (event) => {
            callback(event.detail);
        });
    }
    
    /**
     * Cleanup
     */
    cleanup() {
        console.log('[MESSAGING_ORCHESTRATOR] Cleaning up...');
        
        this.audioRecordingService.cleanup();
        
        this.isInitialized = false;
        console.log('[MESSAGING_ORCHESTRATOR] Cleanup complete');
    }
}

// Export singleton instance
const messagingOrchestrator = new MessagingOrchestrator();
export default messagingOrchestrator;
