/**
 * AppController - Orchestration ONLY
 * Coordinates module communication following strict SOT data flow
 * MUST NOT compute or mutate state
 * MUST NOT bypass message-service or store
 */

import { store } from './store.js?v=20261106-group1';
import { messageService } from './message-service.js?v=20261106-group1';
import { webSocketManager } from './websocket.js?v=20261106-group1';
import { uiController } from '../ui/ui-controller.js?v=20261106-group1';
import { networkHealthTracker } from '../shared/network-health-tracker.js?v=20261106-group1';
import { eventBus } from './event-bus.js?v=20261106-group1';
import { messageSoundManager } from '../shared/message-sound.js?v=20261106-group1';

export class AppController {
    constructor() {
        this.initialized = false;
        this.config = null;
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.connectionUnsubscribe = null;
    }

    /**
     * Initialize application with strict SOT flow
     * @param {Object} config - Configuration object
     */
    async init(config) {
        if (this.initialized) return;

        this._log('APP_CONTROLLER_INIT', config);
        this.config = config;
        window.__store = store;

        try {
            // Initialize network health tracker
            networkHealthTracker.init();
            
            this._validateSOTInitialization();
            this._setupDataFlowConnections();
            this._setupConnectionMonitoring();

            // We block until history is loaded
            await this._loadInitialMessages();

            this.initialized = true;
            this._log('APP_CONTROLLER_INITIALIZED');

        } catch (error) {
            this._log('APP_CONTROLLER_INIT_ERROR', error);
            console.error(error);
        }
    }

    /**
     * Handle connection change from websocket (orchestration only)
     * @param {boolean} isConnected - Connection status
     */
    handleConnectionChange(isConnected) {
        this._log('CONNECTION_CHANGE', { isConnected });

        // Update store connection state (ONLY store can mutate)
        if (isConnected) {
            store.setConnectionState('connected');
            // Request peer presence immediately upon connection
            webSocketManager.send({ type: 'get_peer_presence' });
            // Process queued messages through message service
            messageService.processMessageQueue();
            // Send any queued read receipts
            messageService.sendQueuedReadReceipts();
            // Refetch missed history after reconnect (covers messages sent while CLOSED)
            try {
                if (typeof messageService.syncMissedMessages === 'function') {
                    messageService.syncMissedMessages();
                } else if (typeof messageService.loadMoreMessages === 'function') {
                    messageService.refreshConversation?.();
                }
            } catch (e) { console.warn('[APP] missed-sync failed', e); }
        } else {
            store.setConnectionState('disconnected');
        }
    }

    /**
     * Handle reconnect request (orchestration only)
     */
    handleReconnect() {
        this._log('RECONNECT_REQUESTED');

        // Trigger websocket reconnection (transport ONLY)
        webSocketManager.connect();
    }

    /**
     * Validate SOT initialization order
     */
    _validateSOTInitialization() {
        // Store must be initialized first (SOT)
        if (!store || !store.getState()) {
            throw new Error('Store not initialized - SOT violation');
        }

        // Message service must be initialized (ONLY ingestion layer)
        if (!messageService || !messageService.getStatus().initialized) {
            throw new Error('Message service not initialized - ingestion layer violation');
        }

        // WebSocket must be initialized (transport ONLY)
        if (!webSocketManager) {
            throw new Error('WebSocket not initialized - transport layer violation');
        }

        // UI controller must be initialized (read-only consumer)
        if (!uiController || !uiController.getStatus().initialized) {
            throw new Error('UI controller not initialized - consumer layer violation');
        }

        this._log('SOT_INITIALIZATION_VALIDATED');
    }

    /**
     * Setup mandatory data flow connections
     */
    _setupDataFlowConnections() {
        // Configure callbacks on the already-initialized WebSocketManager
        webSocketManager.setMessageCallback((data) => {
            messageService.processIncomingMessage(data);
        });

        webSocketManager.setConnectionCallback((isConnected) => {
            this.handleConnectionChange(isConnected);
        });

        // Initialize Notification & Push Handlers once
        this._setupNotificationHandlers();

        this._log('DATA_FLOW_CONNECTIONS_SETUP');
    }

    /**
     * Setup notification listeners and Web Push integration
     */
    _setupNotificationHandlers() {
        if (this._notificationHandlersBound) return;
        this._notificationHandlersBound = true;

        // Sync active Web Push subscription if permission already granted
        if (typeof window.PushSubscriptionManager !== 'undefined') {
            try {
                const pushManager = new window.PushSubscriptionManager();
                if (pushManager.isSupported() && pushManager.hasPermission()) {
                    pushManager.syncActiveSubscription().catch(e => console.warn('[APP_CONTROLLER] Push sync:', e));
                }
            } catch (e) {
                console.warn('[APP_CONTROLLER] Push manager init error:', e);
            }
        }

        // Global manual registration trigger
        window.registerMessagingNotifications = async () => {
            if (!('Notification' in window)) {
                if (window.showNotification) window.showNotification('Notifications are not supported in this browser.', 'warning');
                return false;
            }
            try {
                const perm = await Notification.requestPermission();
                if (perm === 'granted') {
                    if (window.PushSubscriptionManager) {
                        const pm = new window.PushSubscriptionManager();
                        await pm.subscribe();
                    }
                    if (window.showNotification) {
                        window.showNotification('Push notifications enabled successfully!', 'success');
                    }
                    return true;
                } else {
                    if (window.showNotification) {
                        window.showNotification('Notification permission was not granted.', 'info');
                    }
                }
            } catch (err) {
                console.warn('[APP_CONTROLLER] Error subscribing to push:', err);
            }
            return false;
        };

        // Wire dropdown button if clicked
        document.addEventListener('click', (e) => {
            const btn = e.target.closest('#enableNotificationsBtn');
            if (btn) {
                e.preventDefault();
                window.registerMessagingNotifications();
            }
        });

        // Listen for incoming message notifications
        eventBus.on('notification_received', (message) => {
            if (!message) return;
            const state = store.getState();
            if (message.senderId === state.currentUserId) return;

            // In-app audio chime
            if (messageSoundManager && typeof messageSoundManager.playReceiveSound === 'function') {
                messageSoundManager.playReceiveSound(message.id);
            }

            // Browser Notification API
            if (document.hidden || Number(message.conversationId) !== Number(state.conversationId)) {
                if ('Notification' in window && Notification.permission === 'granted') {
                    try {
                        const senderName = message.senderName || message.senderUsername || (document.body.dataset.receiverName || 'New Message');
                        const bodySnippet = message.content || (message.attachments?.length ? 'Sent an attachment' : 'New message');
                        const avatar = message.senderAvatar || document.body.dataset.receiverAvatar || '/static/images/vastomonochrome.png';
                        const notif = new Notification(senderName, {
                            body: bodySnippet,
                            icon: avatar,
                            badge: '/static/images/favicon-96x96.png',
                            tag: `chat-${message.conversationId}`,
                            renotify: true,
                            data: {
                                conversationId: message.conversationId,
                                url: `/messaging/conversation/${message.conversationId}/`
                            }
                        });
                        notif.onclick = function() {
                            window.focus();
                            if (message.conversationId) {
                                const chatItem = document.querySelector(`.whatsapp-chat-item[data-conversation-id="${message.conversationId}"]`);
                                if (chatItem) chatItem.click();
                            }
                            this.close();
                        };
                    } catch (e) {
                        console.warn('[APP_CONTROLLER] Notification error:', e);
                    }
                }
            }
        });
    }

    /**
     * Load initial messages through message service
     */
    async _loadInitialMessages() {
        this._log('LOADING_INITIAL_MESSAGES');

        try {
            // 1. stop interference
            console.log('[APP_CONTROLLER] Pausing WebSocket before loading messages');
            webSocketManager.pause();

            // 2. load history
            console.log('[APP_CONTROLLER] Loading conversation history for ID:', this.config.conversationId);
            await messageService.loadConversationHistory(
                this.config.conversationId
            );
            console.log('[APP_CONTROLLER] Messages loaded successfully');
            console.log("STATE SNAPSHOT:", store.getState().messages);

            // 4. resume live stream
            console.log('[APP_CONTROLLER] Resuming WebSocket after loading messages');
            webSocketManager.resume();

            this._log('INITIAL_MESSAGES_LOADED');

        } catch (error) {
            console.error('[APP_CONTROLLER] ERROR loading initial messages:', error);
            console.error('[APP_CONTROLLER] Error stack:', error.stack);
            // Still try to resume WebSocket even if loading fails
            console.log('[APP_CONTROLLER] Attempting to resume WebSocket despite error');
            webSocketManager.resume();
        }
    }

    /**
     * Setup connection monitoring (orchestration only)
     */
    _setupConnectionMonitoring() {
        // Monitor connection state changes from store
        this.connectionUnsubscribe = store.subscribe((state) => {
            this._log('STORE_STATE_UPDATE', {
                connectionState: state.connectionState,
                messagesCount: state.messages.length
            });
        });

        this._log('CONNECTION_MONITORING_SETUP');
    }

    /**
     * Get application status (orchestration only)
     * @returns {Object} Application status
     */
    getStatus() {
        return {
            initialized: this.initialized,
            config: this.config,
            sotValidation: this._validateSOTArchitecture(),
            dataFlow: this._validateDataFlow()
        };
    }

    /**
     * Validate SOT architecture (orchestration only)
     * @returns {Object} Validation results
     */
    _validateSOTArchitecture() {
        const results = {
            store: !!store && !!store.getState(),
            messageService: !!messageService && messageService.getStatus().initialized,
            websocket: !!webSocketManager,
            uiController: !!uiController && uiController.getStatus().initialized,
            dataFlowValid: false
        };

        results.dataFlowValid = Object.values(results).every(Boolean);
        return results;
    }

    /**
     * Validate data flow (orchestration only)
     * @returns {Object} Data flow validation
     */
    _validateDataFlow() {
        return {
            websocketToMessageService: !!webSocketManager && typeof webSocketManager.getMessageCallback() === 'function',
            messageServiceToStore: !!messageService && !!store,
            storeToUIController: !!store && !!uiController && !!uiController.getStatus().subscribed,
            uiControllerToRenderer: !!uiController && !!uiController.renderer
        };
    }

    /**
     * Get store (for debugging only)
     * @returns {Object} Store instance
     */
    getStore() {
        return store;
    }

    /**
     * Get message service (for debugging only)
     * @returns {Object} Message service instance
     */
    getMessageService() {
        return messageService;
    }

    /**
     * Get websocket manager (for debugging only)
     * @returns {Object} WebSocket manager instance
     */
    getWebSocketManager() {
        return webSocketManager;
    }

    /**
     * Get UI controller (for debugging only)
     * @returns {Object} UI controller instance
     */
    getUIController() {
        return uiController;
    }

    /**
     * Validate message consistency (orchestration only)
     * @returns {Object} Validation results
     */
    validateMessageConsistency() {
        return messageService.validateConsistency();
    }

    /**
     * Destroy application (orchestration only)
     */
    destroy() {
        this._log('APP_CONTROLLER_DESTROY');

        if (this.connectionUnsubscribe) {
            this.connectionUnsubscribe();
            this.connectionUnsubscribe = null;
        }

        // Destroy components in reverse order
        if (uiController) {
            uiController.destroy();
        }

        if (webSocketManager) {
            webSocketManager.destroy();
        }

        this.initialized = false;
        this.config = null;

        this._log('APP_CONTROLLER_DESTROYED');
    }

    /**
     * Log debug information
     * @param {string} action - Action type
     * @param {*} data - Action data
     */
    _log(action, data) {
        if (this.debugMode) {
            console.log(`[APP_CONTROLLER] ${action}:`, data);
        }
    }
}

// Create and export singleton instance
export const appController = new AppController();
