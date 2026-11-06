/**
 * Store - Single Source of Truth (SOT) with Canonical Message Schema
 * ONLY module allowed to mutate application state
 * All mutations MUST go through explicit store methods
 */

import { CONNECTION_STATE, UI_STATE, MESSAGE_STATE } from '../shared/constants.js?v=20261106-group1';

// CANONICAL MESSAGE SCHEMA - All modules MUST conform to this
export const MESSAGE_SCHEMA = {
    id: 'string',                    // Unique identifier
    conversationId: 'number',         // Conversation identifier
    senderId: 'number',              // Sender identifier
    timestamp: 'string',             // ISO timestamp
    status: 'string',                // MESSAGE_STATE enum values only
    content: 'string',               // Message content
    type: 'string',                  // text | media | system | emoji | link
    metadata: 'object',              // Additional data (media info, reactions, etc.)
    isOptimistic: 'boolean',         // Temporary optimistic state
    sortOrder: 'number'              // Deterministic ordering
};

export class Store {
    constructor() {
        // Private state - NO DIRECT ACCESS from outside
        this._state = {
            // Connection state
            connectionState: CONNECTION_STATE.DISCONNECTED,
            conversationId: null,
            currentUserId: null,
            isEncrypted: false,

            // Message state - ONLY canonical schema messages
            messages: new Map(), // messageId -> message (Map for O(1) operations)
            messageOrder: [],   // Array of messageIds for ordering
            processedMessageIds: new Set(), // For deduplication
            isInitialHistoryLoaded: false,
            hasMoreOlderMessages: true,

            // UI state
            uiState: UI_STATE.IDLE,
            typingUsers: new Map(), // userId -> username

            // Peer online status
            peerOnlineStatus: new Map(), // userId -> { isOnline: boolean, lastSeen: timestamp }

            // Theme state
            currentTheme: null,
            themeMode: 'light'
        };

        // Subscribers for state changes (UI controllers only)
        this._subscribers = new Set();

        // Debug mode flag
        this._debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';

        // Mutation tracking for validation
        this._mutationLog = [];
        this._maxMutationLogSize = 100;
    }

    /**
     * Initialize store with configuration
     * @param {Object} config - Initial configuration
     */
    init(config) {
        this._logMutation('STORE_INIT', config);
        
        const targetConvId = Number(config.conversationId);
        if (this._state.conversationId !== targetConvId) {
            this._state.messages.clear();
            this._state.messageOrder = [];
            this._state.processedMessageIds.clear();
            this._state.isInitialHistoryLoaded = false;
            this._state.hasMoreOlderMessages = true;
            this._state.typingUsers.clear();
            // Drop previous conversation's presence so the header can't show
            // the old peer's avatar/online state after a swap.
            this._state.peerOnlineStatus.clear();
        }

        this._state.conversationId = targetConvId;
        this._state.currentUserId = Number(config.currentUserId);
        this._state.isEncrypted = config.isEncrypted || false;
        this._state.isGroupChat = Boolean(config.isGroupChat);
        
        this._notifySubscribers();
    }

    /**
     * Subscribe to state changes (UI controllers only)
     * @param {Function} callback - Callback function
     * @returns {Function} Unsubscribe function
     */
    subscribe(callback) {
        this._subscribers.add(callback);
        
        // Return unsubscribe function
        return () => {
            this._subscribers.delete(callback);
        };
    }

    /**
     * Get current state (read-only snapshot)
     * @returns {Object} Current state snapshot
     */
    getState() {
        return {
            connectionState: this._state.connectionState,
            conversationId: this._state.conversationId,
            currentUserId: this._state.currentUserId,
            isEncrypted: this._state.isEncrypted,
            isGroupChat: this._state.isGroupChat,
            isInitialHistoryLoaded: Boolean(this._state.isInitialHistoryLoaded),
            messages: this._getMessagesArray(),
            messageQueue: [], // Queue handled by message service
            processedMessageIds: new Set(this._state.processedMessageIds),
            uiState: this._state.uiState,
            typingUsers: new Map(this._state.typingUsers),
            recordingUsers: new Map(this._state.recordingUsers || []),
            peerOnlineStatus: new Map(this._state.peerOnlineStatus),
            currentTheme: this._state.currentTheme,
            themeMode: this._state.themeMode
        };
    }

    /**
     * Mark initial history loading completed (or failed)
     * @param {boolean} loaded
     */
    setInitialHistoryLoaded(loaded = true) {
        this._logMutation('SET_INITIAL_HISTORY_LOADED', { loaded });
        this._state.isInitialHistoryLoaded = Boolean(loaded);
        this._notifySubscribers();
    }

    /**
     * Get messages as array (read-only, ordered)
     * @returns {Array} Messages array in order
     */
    getMessages() {
        return this._getMessagesArray();
    }

    /**
     * Get message by ID (read-only)
     * @param {string} messageId - Message ID
     * @returns {Object|null} Message or null
     */
    getMessageById(messageId) {
        if (messageId === undefined || messageId === null) return null;
        const idStr = String(messageId);
        const message = this._state.messages.get(idStr) ||
                        this._state.messages.get(Number(idStr)) ||
                        this._state.messages.get(messageId);
        return message ? { ...message } : null; // Return copy to prevent mutation
    }

    /**
     * Get connection state (read-only)
     * @returns {string} Connection state
     */
    getConnectionState() {
        return this._state.connectionState;
    }

    /**
     * Check if connected (read-only)
     * @returns {boolean} Is connected
     */
    isConnected() {
        return this._state.connectionState === CONNECTION_STATE.CONNECTED;
    }

    // === MUTATION METHODS (ONLY STORE CAN MUTATE STATE) ===

    /**
     * Set connection state
     * @param {string} state - New connection state
     */
    setConnectionState(state) {
        this._logMutation('SET_CONNECTION_STATE', { state });
        
        this._state.connectionState = state;
        this._notifySubscribers();
    }

    /**
     * Add message to store (canonical schema validation)
     * @param {Object} message - Message object
     */
    addMessage(message) {
        console.log('[STORE] ========== ADD MESSAGE ==========');
        console.log('[STORE] Input message:', JSON.stringify(message, null, 2));
        console.log('[STORE] Current message count before add:', this._state.messages.size);
        console.log('[STORE] Processed IDs before add:', Array.from(this._state.processedMessageIds));

        this._logMutation('ADD_MESSAGE', message);

        // Validate against canonical schema
        const validatedMessage = this._validateCanonicalMessage(message);
        if (!validatedMessage) {
            console.error('[STORE] Invalid message rejected - violates canonical schema', message);
            return;
        }

        // Check for duplicates
        const idStr = String(validatedMessage.id);
        if (this._state.processedMessageIds.has(idStr)) {
            console.log('[STORE] Message already in store, checking for update:', idStr);
            const existing = this._state.messages.get(idStr) || this._state.messages.get(Number(idStr));
            if (existing && (
                existing.content !== validatedMessage.content ||
                existing.editedAt !== validatedMessage.editedAt ||
                existing.isDeleted !== validatedMessage.isDeleted ||
                existing.status !== validatedMessage.status ||
                existing.isOptimistic !== validatedMessage.isOptimistic ||
                existing.type !== validatedMessage.type ||
                existing.metadata?.url !== validatedMessage.metadata?.url
            )) {
                this._state.messages.set(idStr, {
                    ...existing,
                    ...validatedMessage,
                    metadata: { ...(existing.metadata || {}), ...(validatedMessage.metadata || {}) },
                    id: idStr
                });
                this._notifySubscribers();
            }
            return;
        }

        // Add to processed IDs
        this._state.processedMessageIds.add(idStr);

        // Store message (immutable)
        this._state.messages.set(idStr, { ...validatedMessage, id: idStr });

        console.log('[STORE] Message stored successfully:', idStr);
        console.log('[STORE] Current message count after add:', this._state.messages.size);

        // Clear typing indicator for this sender if they were typing
        if (this._state.typingUsers.has(Number(validatedMessage.senderId)) || this._state.typingUsers.has(validatedMessage.senderId)) {
            const sId = Number(validatedMessage.senderId);
            if (this._typingTimeouts?.has(sId)) {
                clearTimeout(this._typingTimeouts.get(sId));
                this._typingTimeouts.delete(sId);
            }
            this._state.typingUsers.delete(sId);
            this._state.typingUsers.delete(validatedMessage.senderId);
        }

        // Update order array for deterministic sorting
        this._updateMessageOrder({ ...validatedMessage, id: idStr });

        console.log('[STORE] Notifying subscribers...');
        this._notifySubscribers();
        console.log('[STORE] Subscribers notified');
    }

    /**
     * Add multiple messages in a single batch (canonical schema validation)
     * @param {Array<Object>} messages - Array of message objects
     */
    addMessages(messages) {
        if (!Array.isArray(messages) || messages.length === 0) return;
        this._logMutation('ADD_MESSAGES_BATCH', { count: messages.length });

        let changedCount = 0;
        for (const message of messages) {
            const validated = this._validateCanonicalMessage(message);
            if (!validated) continue;
            const idStr = String(validated.id);

            if (this._state.processedMessageIds.has(idStr)) {
                const existing = this._state.messages.get(idStr) || this._state.messages.get(Number(idStr));
                const existingAtt = existing?.attachments || existing?.metadata?.attachments || [];
                const validatedAtt = validated.attachments || validated.metadata?.attachments || [];
                if (existing && (
                    existing.content !== validated.content ||
                    existing.editedAt !== validated.editedAt ||
                    existing.isDeleted !== validated.isDeleted ||
                    existing.status !== validated.status ||
                    existing.isOptimistic !== validated.isOptimistic ||
                    existing.type !== validated.type ||
                    existing.metadata?.url !== validated.metadata?.url ||
                    existingAtt.length !== validatedAtt.length
                )) {
                    this._state.messages.set(idStr, {
                        ...existing,
                        ...validated,
                        metadata: { ...(existing.metadata || {}), ...(validated.metadata || {}) },
                        id: idStr
                    });
                    changedCount++;
                }
                continue;
            }

            this._state.processedMessageIds.add(idStr);
            this._state.messages.set(idStr, { ...validated, id: idStr });
            this._updateMessageOrder({ ...validated, id: idStr });
            changedCount++;
        }

        if (changedCount > 0) {
            console.log(`[STORE] Batch processed ${changedCount} messages. Total:`, this._state.messages.size);
            this._notifySubscribers();
        }
    }

    /**
     * Get the oldest message ID currently stored
     * @returns {string|null} Oldest message ID
     */
    getOldestMessageId() {
        if (this._state.messageOrder.length === 0) return null;
        return this._state.messageOrder[0];
    }

    /**
     * Check if there are more older messages available on server
     * @returns {boolean}
     */
    hasMoreOlderMessages() {
        return this._state.hasMoreOlderMessages !== false;
    }

    /**
     * Set flag indicating if more older messages exist
     * @param {boolean} hasMore
     */
    setHasMoreOlderMessages(hasMore) {
        this._state.hasMoreOlderMessages = hasMore;
    }

    /**
     * Update existing message (canonical schema validation)
     * @param {string} messageId - Message ID
     * @param {Object} updates - Message updates
     */
    updateMessage(messageId, updates) {
        console.log('[STORE] Updating message:', messageId, 'with updates:', updates);
        this._logMutation('UPDATE_MESSAGE', { messageId, updates });

        const key = String(messageId);
        let existingMessage = this._state.messages.get(key) || this._state.messages.get(Number(messageId));

        if (!existingMessage) {
            console.error('[STORE] Message not found for update', messageId);
            return;
        }
        console.log('[STORE] Existing message before update:', existingMessage);

        const originalSortOrder = (typeof existingMessage.sortOrder === 'number' && !isNaN(existingMessage.sortOrder))
            ? existingMessage.sortOrder
            : (existingMessage.timestamp ? new Date(existingMessage.timestamp).getTime() : Date.now());

        // Validate updates against canonical schema
        const updatedMessage = this._validateCanonicalMessage({
            ...existingMessage,
            ...updates,
            sortOrder: updates.sortOrder ?? originalSortOrder,
            metadata: {
                ...existingMessage.metadata,
                ...(updates.metadata || {})
            },
            id: key
        });

        if (!updatedMessage) {
            console.error('Store: Invalid message update - violates canonical schema', updates);
            return;
        }

        // Update message (immutable, normalized string key)
        this._state.messages.delete(Number(key));
        this._state.messages.set(key, updatedMessage);

        this._notifySubscribers();
    }

    /**
     * Replace message (for optimistic updates)
     * @param {string} tempId - Temporary message ID
     * @param {Object} actualMessage - Actual message
     */
    replaceMessage(tempId, actualMessage) {
        this._logMutation('REPLACE_MESSAGE', { tempId, actualMessage });

        // Validate actual message against canonical schema
        const validatedMessage = this._validateCanonicalMessage(actualMessage);
        if (!validatedMessage) {
            console.error('Store: Invalid replacement message - violates canonical schema', actualMessage);
            return;
        }

        const optimisticMessage = this._state.messages.get(tempId);
        if (!optimisticMessage) {
            console.error('Store: Optimistic message not found for replacement', tempId);
            return;
        }

        // Remove temp ID from processed and add actual ID
        this._state.processedMessageIds.delete(tempId);
        this._state.processedMessageIds.add(validatedMessage.id);

        // ✅ Remove stale tempId from order array
        const tempIndex = this._state.messageOrder.indexOf(tempId);
        if (tempIndex !== -1) {
            this._state.messageOrder.splice(tempIndex, 1);
        }

        // Add actual message
        this._state.messages.set(validatedMessage.id, validatedMessage);

        // Update order
        this._updateMessageOrder(validatedMessage);

        this._notifySubscribers();
    }

    /**
     * Remove message
     * @param {string} messageId - Message ID
     */
    removeMessage(messageId) {
        this._logMutation('REMOVE_MESSAGE', { messageId });

        let key = messageId;
        let message = this._state.messages.get(key);
        if (!message && typeof key !== 'string') {
            message = this._state.messages.get(String(key));
            if (message) key = String(key);
        }
        if (!message && typeof key === 'string' && !isNaN(Number(key))) {
            message = this._state.messages.get(Number(key));
            if (message) key = Number(key);
        }

        if (!message) {
            console.error('Store: Message not found for removal', messageId);
            return;
        }

        this._state.messages.delete(key);
        this._state.processedMessageIds.delete(key);
        this._state.processedMessageIds.delete(message.id);

        // Remove from order array
        const orderIndex = this._state.messageOrder.indexOf(key);
        if (orderIndex !== -1) {
            this._state.messageOrder.splice(orderIndex, 1);
        }
        const orderIndexActual = this._state.messageOrder.indexOf(message.id);
        if (orderIndexActual !== -1) {
            this._state.messageOrder.splice(orderIndexActual, 1);
        }

        this._notifySubscribers();
    }

    /**
     * Update message status
     * @param {string} messageId - Message ID
     * @param {string} status - New status
     * @param {Object} metadata - Optional metadata to update
     */
    updateMessageStatus(messageId, status, metadata = {}) {
        console.log('[STORE] Updating message status:', messageId, 'to:', status);
        this._logMutation('UPDATE_MESSAGE_STATUS', { messageId, status, metadata });

        const key = String(messageId);
        const existingMessage = this._state.messages.get(key) || this._state.messages.get(messageId) || this._state.messages.get(Number(messageId));
        if (!existingMessage) {
            console.error('Store: Message not found for status update', messageId);
            return;
        }

        // Update message with new status
        const updatedMessage = {
            ...existingMessage,
            id: key,
            status: status,
            metadata: {
                ...existingMessage.metadata,
                ...metadata
            }
        };

        // Validate and store
        const validatedMessage = this._validateCanonicalMessage(updatedMessage);
        if (!validatedMessage) {
            console.error('Store: Invalid message status update - violates canonical schema', updatedMessage);
            return;
        }

        this._state.messages.delete(Number(key));
        this._state.messages.set(key, validatedMessage);
        this._notifySubscribers();
    }

    /**
     * Set typing indicator
     * @param {number} userId - User ID
     * @param {string} username - Username
     * @param {boolean} isTyping - Is typing
     */
    setTypingIndicator(userId, username, isTyping) {
        this._logMutation('SET_TYPING_INDICATOR', { userId, username, isTyping });

        if (!this._typingTimeouts) {
            this._typingTimeouts = new Map();
        }

        const numericUserId = Number(userId);

        if (isTyping) {
            this._state.typingUsers.set(numericUserId, username);
            if (this._typingTimeouts.has(numericUserId)) {
                clearTimeout(this._typingTimeouts.get(numericUserId));
            }
            // Auto-clear after 4.5s if no stop/refresh received
            const timeout = setTimeout(() => {
                if (this._state.typingUsers.has(numericUserId)) {
                    this._state.typingUsers.delete(numericUserId);
                    this._typingTimeouts.delete(numericUserId);
                    this._notifySubscribers();
                }
            }, 4500);
            this._typingTimeouts.set(numericUserId, timeout);
        } else {
            if (this._typingTimeouts.has(numericUserId)) {
                clearTimeout(this._typingTimeouts.get(numericUserId));
                this._typingTimeouts.delete(numericUserId);
            }
            this._state.typingUsers.delete(numericUserId);
            this._state.typingUsers.delete(userId);
        }

        this._notifySubscribers();
    }

    /**
     * Set peer online status
     * @param {number} userId - User ID
     * @param {boolean} isOnline - Is online
     * @param {number|null} lastSeen - Last seen timestamp
     */
    setPeerOnlineStatus(userId, isOnline, lastSeen = null) {
        this._logMutation('SET_PEER_ONLINE_STATUS', { userId, isOnline, lastSeen });

        this._state.peerOnlineStatus.set(userId, {
            isOnline,
            lastSeen: lastSeen || Date.now()
        });

        if (!isOnline) {
            const numericUserId = Number(userId);
            this._state.typingUsers.delete(numericUserId);
            this._state.typingUsers.delete(userId);
            if (this._state.recordingUsers) {
                this._state.recordingUsers.delete(numericUserId);
                this._state.recordingUsers.delete(userId);
            }
        }

        this._notifySubscribers();
    }

    /**
     * Set recording audio indicator
     * @param {number} userId - User ID
     * @param {string} username - Username
     * @param {boolean} isRecording - Is recording
     */
    setRecordingIndicator(userId, username, isRecording) {
        this._logMutation('SET_RECORDING_INDICATOR', { userId, username, isRecording });

        if (!this._state.recordingUsers) {
            this._state.recordingUsers = new Map();
        }
        if (!this._recordingTimeouts) {
            this._recordingTimeouts = new Map();
        }

        const numericUserId = Number(userId);

        if (isRecording) {
            this._state.recordingUsers.set(numericUserId, username);
            if (this._recordingTimeouts.has(numericUserId)) {
                clearTimeout(this._recordingTimeouts.get(numericUserId));
            }
            // Auto-clear after 120s safety limit if peer disconnects
            const timeout = setTimeout(() => {
                if (this._state.recordingUsers.has(numericUserId)) {
                    this._state.recordingUsers.delete(numericUserId);
                    this._recordingTimeouts.delete(numericUserId);
                    this._notifySubscribers();
                }
            }, 120000);
            this._recordingTimeouts.set(numericUserId, timeout);
        } else {
            if (this._recordingTimeouts.has(numericUserId)) {
                clearTimeout(this._recordingTimeouts.get(numericUserId));
                this._recordingTimeouts.delete(numericUserId);
            }
            this._state.recordingUsers.delete(numericUserId);
            this._state.recordingUsers.delete(userId);
        }
        this._notifySubscribers();
    }

    /**
     * Update an optimistic message with real server ID and sent status
     * @param {string} tempId - Temporary ID
     * @param {number|string} actualId - Real server ID
     * @param {string} status - New status ('sent')
     * @param {string} timestamp - Server timestamp
     * @param {Object|null} fullUpdates - Optional full canonical message fields to merge atomically
     */
    updateMessageIdAndStatus(tempId, actualId, status = 'sent', timestamp = null, fullUpdates = null) {
        const tempIdStr = String(tempId);
        const msg = this._state.messages.get(tempIdStr) || this._state.messages.get(tempId);
        if (!msg) return;

        const actualIdStr = String(actualId);

        this._state.messages.delete(tempIdStr);
        this._state.messages.delete(tempId);
        this._state.processedMessageIds.delete(tempIdStr);
        this._state.processedMessageIds.add(actualIdStr);

        const originalSortOrder = (typeof msg.sortOrder === 'number' && !isNaN(msg.sortOrder))
            ? msg.sortOrder
            : (msg.timestamp ? new Date(msg.timestamp).getTime() : Date.now());

        const mergedMetadata = {
            ...(msg.metadata || {}),
            ...(fullUpdates?.metadata || {})
        };
        // Preserve local blob URL / previewUrl if server URL is missing or keep localAudioUrl fallback
        if (msg.metadata?.url && String(msg.metadata.url).startsWith('blob:') && !mergedMetadata.localBlobUrl) {
            mergedMetadata.localBlobUrl = msg.metadata.url;
        }

        const updated = {
            ...msg,
            ...(fullUpdates || {}),
            id: actualIdStr,
            status: status || fullUpdates?.status || 'sent',
            isOptimistic: false,
            timestamp: timestamp || fullUpdates?.timestamp || msg.timestamp,
            sortOrder: originalSortOrder,
            metadata: mergedMetadata
        };

        const tempIndex = this._state.messageOrder.indexOf(tempIdStr);
        if (tempIndex !== -1) {
            this._state.messageOrder.splice(tempIndex, 1);
        }
        if (!this._state.messageOrder.includes(actualIdStr)) {
            if (tempIndex !== -1) {
                this._state.messageOrder.splice(tempIndex, 0, actualIdStr);
            } else {
                this._state.messageOrder.push(actualIdStr);
            }
        }

        this._state.messages.set(actualIdStr, updated);

        // Rekey DOM element in-place to avoid reconciliation re-creation flash
        if (window.uiController?.renderer?.rekeyMessageElement) {
            window.uiController.renderer.rekeyMessageElement(tempIdStr, actualIdStr);
        }

        this._notifySubscribers();
    }

    /**
     * Mark all sent messages up to a given ID as read
     * @param {number|string} lastReadMessageId - Highest message ID read
     * @param {string} [readAvatar] - Reader's avatar URL
     */
    markMessagesAsReadUpTo(lastReadMessageId, readAvatar = null) {
        let changed = false;
        const targetId = parseInt(lastReadMessageId, 10);
        if (isNaN(targetId)) return;

        this._state.lastReadMessageId = targetId;
        if (readAvatar) {
            this._state.lastReadAvatar = readAvatar;
        }

        for (const [id, msg] of this._state.messages.entries()) {
            const numericId = parseInt(id, 10);
            if (!isNaN(numericId) && numericId <= targetId) {
                // Only own messages carry read ticks; never flip received bubbles to read
                if (msg.isOwn && msg.status !== 'read') {
                    msg.status = 'read';
                    changed = true;
                }
                if (numericId === targetId && readAvatar && msg.isOwn) {
                    msg.read_avatar = readAvatar;
                }
            }
        }
        if (changed || readAvatar) {
            this._notifySubscribers();
        }
    }

    /**
     * Set UI state
     * @param {string} state - UI state
     */
    setUIState(state) {
        this._logMutation('SET_UI_STATE', { state });

        this._state.uiState = state;
        this._notifySubscribers();
    }

    /**
     * Set theme
     * @param {Object} theme - Theme object
     * @param {string} mode - Theme mode
     */
    setTheme(theme, mode = 'light') {
        this._logMutation('SET_THEME', { theme, mode });

        this._state.currentTheme = theme;
        this._state.themeMode = mode;
        this._notifySubscribers();
    }

    /**
     * Reset store to initial state
     */
    reset() {
        this._logMutation('RESET_STORE');

        this._state.messages.clear();
        this._state.messageOrder = [];
        this._state.processedMessageIds.clear();
        this._state.typingUsers.clear();
        this._state.peerOnlineStatus.clear();
        this._state.uiState = UI_STATE.IDLE;
        this._state.isInitialHistoryLoaded = false;

        this._notifySubscribers();
    }

    // === PRIVATE METHODS ===

    /**
     * Validate message against canonical schema
     * @param {Object} message - Message to validate
     * @returns {Object|null} Validated message or null
     */
    _validateCanonicalMessage(message) {
        console.log('[STORE] ========== VALIDATING MESSAGE ==========');
        console.log('[STORE] Input message:', JSON.stringify(message, null, 2));

        if (!message || typeof message !== 'object') {
            console.error('[STORE] Validation failed: message is not an object');
            return null;
        }

        // Required fields validation
        const required = ['id', 'conversationId', 'senderId', 'timestamp', 'status', 'content', 'type'];
        for (const field of required) {
            if (!(field in message)) {
                console.error(`[STORE] Validation failed: Missing required field: ${field}`);
                console.error('[STORE] Available fields:', Object.keys(message));
                return null;
            }
        }

        // Type validation
        const validStates = Object.values(MESSAGE_STATE);
        const validTypes = ['text', 'media', 'system', 'emoji', 'link', 'media_group', 'audio', 'voice_note', 'document'];

        const validation = {
            id: typeof message.id === 'string',
            conversationId: typeof message.conversationId === 'number',
            senderId: typeof message.senderId === 'number',
            timestamp: typeof message.timestamp === 'string',
            status: validStates.includes(message.status),
            content: typeof message.content === 'string',
            type: validTypes.includes(message.type),
            isOptimistic: typeof message.isOptimistic === 'boolean',
            sortOrder: typeof message.sortOrder === 'number'
        };

        console.log('[STORE] Validation results:', validation);

        for (const [field, isValid] of Object.entries(validation)) {
            if (!isValid) {
                console.error(`[STORE] Validation failed: Invalid field type: ${field}`, message[field]);
                return null;
            }
        }

        // Return validated message with defaults
        const myId = Number(this._state.currentUserId) || null;
        const validated = {
            id: message.id,
            conversationId: message.conversationId,
            senderId: message.senderId,
            timestamp: message.timestamp,
            status: message.status,
            content: message.content,
            type: message.type,
            isOwn: message.isOwn !== undefined
                ? Boolean(message.isOwn)
                : (myId !== null && Number(message.senderId) === myId),
            metadata: message.metadata || {},
            attachments: message.attachments || message.metadata?.attachments || [],
            global_caption: message.global_caption || message.metadata?.global_caption || '',
            isOptimistic: message.isOptimistic || false,
            isDeleted: Boolean(message.isDeleted || message.metadata?.is_deleted),
            isForwarded: Boolean(message.isForwarded || message.is_forwarded || message.metadata?.is_forwarded || message.metadata?.isForwarded),
            is_forwarded: Boolean(message.isForwarded || message.is_forwarded || message.metadata?.is_forwarded || message.metadata?.isForwarded),
            editedAt: message.editedAt || message.metadata?.edited_at || null,
            replyToId: message.replyToId || message.metadata?.reply_to_id || null,
            replyToDetails: message.replyToDetails || message.metadata?.reply_to_details || null,
            sortOrder: (typeof message.sortOrder === 'number' && !isNaN(message.sortOrder))
                ? message.sortOrder
                : (message.timestamp ? new Date(message.timestamp).getTime() : Date.now())
        };

        console.log('[STORE] Message validated successfully:', validated.id);
        return validated;
    }

    /**
     * Update message order for deterministic sorting
     * @param {Object} message - Message to add to order
     */
    _updateMessageOrder(message) {
        const idStr = String(message.id);
        const existingIndex = this._state.messageOrder.findIndex(id => String(id) === idStr);
        
        const getMsgSortOrder = (msgId, fallbackMsg) => {
            const m = this._state.messages.get(String(msgId)) || (fallbackMsg && String(fallbackMsg.id) === String(msgId) ? fallbackMsg : null);
            if (m && typeof m.sortOrder === 'number' && !isNaN(m.sortOrder)) return m.sortOrder;
            if (m && m.timestamp) return new Date(m.timestamp).getTime();
            return 0;
        };

        const targetSortOrder = (typeof message.sortOrder === 'number' && !isNaN(message.sortOrder))
            ? message.sortOrder
            : (message.timestamp ? new Date(message.timestamp).getTime() : Date.now());

        if (existingIndex === -1) {
            // New message - insert in correct position
            const insertIndex = this._state.messageOrder.findIndex(id => {
                return getMsgSortOrder(id) > targetSortOrder;
            });

            if (insertIndex === -1) {
                this._state.messageOrder.push(idStr);
            } else {
                this._state.messageOrder.splice(insertIndex, 0, idStr);
            }
        } else {
            // Existing message - reorder if needed
            this._state.messageOrder[existingIndex] = idStr;
            this._state.messageOrder.sort((a, b) => {
                return getMsgSortOrder(a, message) - getMsgSortOrder(b, message);
            });
        }
    }

    /**
     * Get messages as ordered array
     * @returns {Array} Ordered messages array
     */
    _getMessagesArray() {
        const orderedMessages = [];
        const seenIds = new Set();
        
        for (const messageId of this._state.messageOrder) {
            const idStr = String(messageId);
            if (seenIds.has(idStr)) continue;
            seenIds.add(idStr);
            const message = this._state.messages.get(idStr) || this._state.messages.get(Number(idStr));
            if (message) {
                orderedMessages.push({ ...message }); // Return copy
            }
        }

        return orderedMessages;
    }

    /**
     * Notify all subscribers of state change
     */
    _notifySubscribers() {
        const snapshot = this.getState();
        this._subscribers.forEach(callback => {
            try {
                callback(snapshot);
            } catch (error) {
                console.error('Store: Subscriber callback error', error);
            }
        });
    }

    /**
     * Log state mutation (debug mode only)
     * @param {string} action - Action type
     * @param {*} data - Action data
     */
    _logMutation(action, data) {
        const logEntry = {
            timestamp: new Date().toISOString(),
            action,
            data: structuredClone?.(data) ?? data // Deep clone
        };

        this._mutationLog.push(logEntry);

        // Keep log size manageable
        if (this._mutationLog.length > this._maxMutationLogSize) {
            this._mutationLog.shift();
        }

        if (this._debugMode) {
            console.log(`[STORE MUTATION] ${action}:`, data);
        }
    }

    /**
     * Get mutation log (for validation)
     * @returns {Array} Mutation log
     */
    getMutationLog() {
        return [...this._mutationLog];
    }

    /**
     * Validate message consistency
     * @returns {Object} Validation results
     */
    validateMessageConsistency() {
        const messages = this.getMessages();
        const messageIds = new Set();
        const duplicates = [];
        const invalidSchema = [];

        for (const message of messages) {
            // Check for duplicates
            if (messageIds.has(message.id)) {
                duplicates.push(message.id);
            } else {
                messageIds.add(message.id);
            }

            // Validate schema
            const validated = this._validateCanonicalMessage(message);
            if (!validated) {
                invalidSchema.push(message.id);
            }
        }

        return {
            totalMessages: messages.length,
            uniqueMessages: messageIds.size,
            duplicates,
            invalidSchema,
            isConsistent: duplicates.length === 0 && invalidSchema.length === 0
        };
    }
}

// Create and export singleton instance
export const store = new Store();
