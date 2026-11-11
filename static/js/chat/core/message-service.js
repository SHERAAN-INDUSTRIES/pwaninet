/**
 * MessageService - ONLY ingestion pipeline for all messages
 * Validates, normalizes, and forwards to store using canonical schema
 * NO direct state mutations, NO UI updates, NO business logic
 */

import { store } from './store.js?v=20261106-group1';
import { webSocketManager } from './websocket.js?v=20261106-group1';
import { eventBus } from './event-bus.js?v=20261106-group1';
import { getCSRFToken } from '../shared/utils.js?v=20261106-group1';
import { EVENTS, MESSAGE_STATE, CONNECTION_STATE, isValidStateTransition } from '../shared/constants.js?v=20261106-group1';
import { messageSoundManager } from '../shared/message-sound.js?v=20261106-group1';

export class MessageService {
    constructor() {
        this.e2eEncryption = null;
        this.initialized = false;
        this.recipientPublicKey = null;
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.messageQueue = []; // Local queue for transport only
        
        // Track message states by temp_id for transition validation
        this.messageStates = new Map();
        this.pendingAckTimers = new Map();
        this.restFallbacksInFlight = new Set();
        this._isLoadingOlder = false;
        const refreshCallHistory = event => {
            const call = event.detail;
            if (!call || (event.type === 'pwani:call-update' && !['ended', 'cancelled', 'declined', 'missed'].includes(call.status))) return;
            const state = store.getState();
            if (Number(state.conversationId || state.activeConversationId) === Number(call.conversation_id)) {
                this.syncMissedMessages();
            }
        };
        window.addEventListener('pwani:call-update', refreshCallHistory);
        window.addEventListener('pwani:call-history-ready', refreshCallHistory);
    }

    /**
     * Start a watchdog timer for WebSocket message ACK
     * @param {string} tempId - Optimistic message ID
     * @param {Function} onTimeout - Callback when ACK does not arrive in time
     * @param {number} timeoutMs - Timeout in ms
     */
    _startAckTimer(tempId, onTimeout, timeoutMs = 4000) {
        this._clearAckTimer(tempId);
        const timer = setTimeout(() => {
            this.pendingAckTimers.delete(tempId);
            if (typeof onTimeout === 'function') {
                onTimeout();
            }
        }, timeoutMs);
        this.pendingAckTimers.set(tempId, timer);
    }

    /**
     * Clear pending ACK timer for a tempId
     * @param {string} tempId - Optimistic message ID
     */
    _clearAckTimer(tempId) {
        if (!tempId) return;
        const timer = this.pendingAckTimers.get(tempId);
        if (timer) {
            clearTimeout(timer);
            this.pendingAckTimers.delete(tempId);
        }
    }

    /**
     * Initialize message service
     */
    init() {
        this._log('MESSAGE_SERVICE_INIT');
        
        // Setup encryption if available
        if (typeof E2EEncryption !== 'undefined') {
            this.e2eEncryption = new E2EEncryption();
        }

        // Setup optimistic messaging event listeners
        this._setupOptimisticMessagingListeners();

        // Wire up direct media sending from media picker (stickers & GIFs)
        window.chatSendDirectMedia = (media) => this.sendDirectMedia(media);
        window.addEventListener('chatSendDirectMedia', (e) => {
            if (e.detail) this.sendDirectMedia(e.detail);
        });

        // Wire up voice recording indicator events
        eventBus.on(EVENTS.VOICE_START, () => this.sendRecordingIndicator(true));
        eventBus.on(EVENTS.VOICE_STOP, () => this.sendRecordingIndicator(false));
        eventBus.on(EVENTS.VOICE_DISCARD, () => this.sendRecordingIndicator(false));
        eventBus.on(EVENTS.VOICE_ERROR, () => this.sendRecordingIndicator(false));
        eventBus.on(EVENTS.VOICE_SEND, () => this.sendRecordingIndicator(false));

        this._log('MESSAGE_SERVICE_INITIALIZED');
        this.initialized = true;
    }

    /**
     * Setup optimistic messaging event listeners
     */
    _setupOptimisticMessagingListeners() {
        // Handle optimistic message add (show temporary message while uploading)
        eventBus.on(EVENTS.MESSAGE_OPTIMISTIC_ADD, (message) => {
            this._handleOptimisticAdd(message);
        });

        // Handle upload success (replace optimistic message with server message)
        eventBus.on(EVENTS.MESSAGE_UPLOAD_SUCCESS, (data) => {
            this._handleUploadSuccess(data);
        });

        // Handle upload failure (mark optimistic message as failed)
        eventBus.on(EVENTS.MESSAGE_UPLOAD_FAILED, (data) => {
            this._handleUploadFailure(data);
        });
    }

    /**
     * Handle optimistic message add
     * @param {Object} message - Optimistic message
     */
    _handleOptimisticAdd(message) {
        console.log('[MESSAGE_SERVICE] Handling optimistic message add');
        console.log('[MESSAGE_SERVICE] [DEBUG] FULL INPUT MESSAGE:', JSON.stringify(message, null, 2));
        console.log('[MESSAGE_SERVICE] [DEBUG] INPUT ATTACHMENTS (top-level):', message.attachments);
        console.log('[MESSAGE_SERVICE] [DEBUG] INPUT METADATA:', message.metadata);
        console.log('[MESSAGE_SERVICE] [DEBUG] INPUT METADATA ATTACHMENTS:', message.metadata?.attachments);
        
        const state = store.getState();
        console.log('[MESSAGE_SERVICE] Current state:', state);
        
        if (!state.conversationId) {
            console.error('[MESSAGE_SERVICE] No conversation ID in state');
            return;
        }
        
        if (!state.currentUserId) {
            console.error('[MESSAGE_SERVICE] No current user ID in state');
            return;
        }
        
        const msgType = message.message_type || message.type || (message.attachments?.length > 1 ? 'media_group' : 'media');
        const canonicalMessage = this._createCanonicalMessage({
            id: message.temp_id,
            conversationId: state.conversationId,
            senderId: state.currentUserId,
            timestamp: message.created_at,
            status: MESSAGE_STATE.UPLOADING,
            content: message.content || '',
            type: msgType,
            metadata: {
                ...(message.metadata || {}),
                attachments: message.attachments || message.metadata?.attachments,
                global_caption: message.global_caption || message.metadata?.global_caption,
                url: message.metadata?.url || message.attachments?.[0]?.file_url || '',
                type: message.metadata?.type || message.attachment_type || message.attachments?.[0]?.file_type || 'image',
                size: message.metadata?.size || message.attachments?.[0]?.size || 0,
                uploadProgress: message.metadata?.uploadProgress || 0
            },
            attachments: message.attachments || message.metadata?.attachments || [],
            global_caption: message.global_caption || message.metadata?.global_caption || '',
            isOptimistic: true,
            sortOrder: Date.now()
        });

        console.log('[MESSAGE_SERVICE] [DEBUG] CANONICAL MESSAGE CREATED:', JSON.stringify(canonicalMessage, null, 2));
        console.log('[MESSAGE_SERVICE] [DEBUG] CANONICAL ATTACHMENTS (top-level):', canonicalMessage.attachments);
        console.log('[MESSAGE_SERVICE] [DEBUG] CANONICAL METADATA:', canonicalMessage.metadata);
        console.log('[MESSAGE_SERVICE] [DEBUG] CANONICAL METADATA ATTACHMENTS:', canonicalMessage.metadata?.attachments);
        console.log('[MESSAGE_SERVICE] Adding canonical message to store');
        store.addMessage(canonicalMessage);
    }

    /**
     * Handle upload success
     * @param {Object} data - Upload success data with tempId and serverMessage
     */
    _handleUploadSuccess(data) {
        console.log('[MESSAGE_SERVICE] Handling upload success:', data);
        
        const { tempId, serverMessage } = data;
        
        if (!tempId) {
            console.error('[MESSAGE_SERVICE] No tempId in upload success data');
            return;
        }
        
        if (!serverMessage) {
            console.error('[MESSAGE_SERVICE] No serverMessage in upload success data');
            return;
        }
        
        this._clearAckTimer(tempId);

        const serverMsg = Array.isArray(serverMessage) ? serverMessage[0] : serverMessage;
        const normalizedMessage = this.normalizeServerMessage(serverMsg);
        normalizedMessage.status = normalizedMessage.status && normalizedMessage.status !== 'uploading' && normalizedMessage.status !== 'sending'
            ? normalizedMessage.status
            : 'sent';
        normalizedMessage.isOptimistic = false;
        
        if (store.getMessageById(tempId)) {
            console.log('[MESSAGE_SERVICE] Rekeying optimistic message in-place:', tempId, '->', normalizedMessage.id);
            store.updateMessageIdAndStatus(
                tempId,
                normalizedMessage.id,
                normalizedMessage.status,
                normalizedMessage.timestamp,
                normalizedMessage
            );
        } else if (store.getMessageById(normalizedMessage.id)) {
            console.log('[MESSAGE_SERVICE] Updating already-rekeyed server message in store:', normalizedMessage.id);
            store.updateMessage(normalizedMessage.id, normalizedMessage);
        } else {
            console.log('[MESSAGE_SERVICE] Adding server message to store:', normalizedMessage.id);
            store.addMessage(normalizedMessage);
        }
    }

    /**
     * Handle upload failure
     * @param {Object} data - Upload failure data with tempId and error
     */
    _handleUploadFailure(data) {
        console.log('[MESSAGE_SERVICE] Handling upload failure:', data);
        
        const { tempId, error } = data;
        
        if (!tempId) {
            console.error('[MESSAGE_SERVICE] No tempId in upload failure data');
            return;
        }
        
        // Update optimistic message status to failed
        console.log('[MESSAGE_SERVICE] Updating message status to failed:', tempId);
        store.updateMessageStatus(tempId, MESSAGE_STATE.FAILED_UPLOAD, {
            error: error
        });
    }

    mapStatus(status) {
        // Map legacy/alternative status names to unified MESSAGE_STATE
        const allowed = {
            draft: MESSAGE_STATE.DRAFT,
            queued: MESSAGE_STATE.QUEUED,
            processing: MESSAGE_STATE.PROCESSING,
            uploading: MESSAGE_STATE.UPLOADING,
            sending: MESSAGE_STATE.SENDING,
            sent: MESSAGE_STATE.SENT,
            delivered: MESSAGE_STATE.DELIVERED,
            read: MESSAGE_STATE.READ,
            seen: MESSAGE_STATE.READ,
            received: MESSAGE_STATE.DELIVERED,
            pending: MESSAGE_STATE.QUEUED,
            failed: MESSAGE_STATE.FAILED_SEND, // Default failed to failed_send
            failed_upload: MESSAGE_STATE.FAILED_UPLOAD,
            failed_send: MESSAGE_STATE.FAILED_SEND,
            retrying: MESSAGE_STATE.RETRYING,
            cancelled: MESSAGE_STATE.CANCELLED
        };

        const mapped = allowed[status];
        if (mapped) {
            console.log('[MESSAGE_SERVICE] mapStatus:', status, '->', mapped);
            return mapped;
        }
        console.log('[MESSAGE_SERVICE] mapStatus: unknown status', status, 'defaulting to SENT');
        return MESSAGE_STATE.SENT;
    }

    mapType(type) {
        const media = ['image', 'video', 'audio', 'voice', 'file', 'sticker', 'gif'];
        if (media.includes(type)) return 'media';
        if (type === 'media_group') return 'media_group';
        if (type === 'emoji') return 'emoji';
        if (type === 'system') return 'system';
        if (type === 'voice_call') return 'voice_call';
        if (type === 'link') return 'link';
        return 'text';
    }

    normalizeServerMessage(raw) {
        const mappedStatus = this.mapStatus(raw.read_status || raw.status);
        console.log(`[MESSAGE_SERVICE] Normalizing message ${raw.id}: raw_status='${raw.read_status || raw.status}', mapped='${mappedStatus}'`);
        console.log(`[MESSAGE_SERVICE] Raw data - link_url:`, raw.link_url, 'link_type:', raw.link_type, 'attachment_url:', raw.attachment_url);
        
        // Build metadata with attachment information if present
        const metadata = { ...(raw.metadata || {}) };
        if (raw.voice_call) metadata.voice_call = raw.voice_call;
        if (raw.attachment_url) {
            metadata.url = raw.attachment_url;
            metadata.type = raw.attachment_type || 'file';
        } else if (raw.attachment && typeof raw.attachment === 'string') {
            metadata.url = raw.attachment;
            metadata.type = raw.attachment_type || 'file';
        }
        if (!metadata.url && raw.attachments && raw.attachments.length === 1) {
            metadata.url = raw.attachments[0].file || raw.attachments[0].file_url || raw.attachments[0].url;
            metadata.type = raw.attachments[0].file_type || raw.attachment_type || 'file';
        }
        if (raw.is_voice_note !== undefined) {
            metadata.is_voice_note = raw.is_voice_note;
        } else if (raw.attachment_type === 'voice_note' || raw.message_type === 'voice_note') {
            metadata.is_voice_note = true;
        }
        if (raw.file_size) {
            metadata.size = raw.file_size;
        }
        if (raw.file_name) {
            metadata.file_name = raw.file_name;
        }
        
        // Add link metadata if present
        if (raw.link_url) {
            console.log('[MESSAGE_SERVICE] Link metadata found:', raw.link_url);
            metadata.link_url = raw.link_url;
            metadata.link_title = raw.link_title;
            metadata.link_description = raw.link_description;
            metadata.link_image = raw.link_image;
            metadata.link_type = raw.link_type;
        }
        
        // Determine message type based on attachment, media_group, or link
        let messageType = this.mapType(raw.message_type || raw.type);
        if (raw.message_type === 'media_group') {
            messageType = 'media_group';
        } else if (raw.attachment_type === 'sticker' || raw.message_type === 'sticker' || raw.metadata?.is_sticker || raw.metadata?.type === 'sticker') {
            messageType = 'media';
            metadata.type = 'sticker';
            metadata.is_sticker = true;
            metadata.url = raw.attachment_url || raw.link_image || raw.metadata?.url || metadata.url || '';
        } else if (raw.attachment_type === 'gif' || raw.message_type === 'gif' || raw.metadata?.is_gif || raw.metadata?.type === 'gif') {
            messageType = 'media';
            metadata.type = 'gif';
            metadata.is_gif = true;
            metadata.url = raw.attachment_url || raw.link_image || raw.metadata?.url || metadata.url || '';
        } else if (metadata.is_voice_note || raw.attachment_type === 'voice_note' || raw.message_type === 'voice_note') {
            messageType = 'media';
            metadata.type = 'voice_note';
            metadata.is_voice_note = true;
        } else if (raw.attachment_type === 'audio' || raw.message_type === 'audio') {
            messageType = 'media';
            metadata.type = 'audio';
        } else if (raw.attachment_type === 'document' || raw.message_type === 'document') {
            messageType = 'media';
            metadata.type = 'document';
        } else if (raw.attachment_type || metadata.url || (raw.attachments && raw.attachments.length === 1)) {
            messageType = 'media';
        } else if (raw.link_url) {
            console.log('[MESSAGE_SERVICE] Setting message type to link');
            messageType = 'link';
        }
        
        // Add attachments to metadata if present
        if (raw.attachments && Array.isArray(raw.attachments)) {
            metadata.attachments = raw.attachments;
        }
        
        // Add global caption to metadata if present
        if (raw.global_caption) {
            metadata.global_caption = raw.global_caption;
        }

        // Add reply, reaction, edit, and deletion metadata
        if (raw.reply_to_details) {
            metadata.reply_to_details = raw.reply_to_details;
        }
        if (raw.reply_to) {
            metadata.reply_to = raw.reply_to;
        }
        if (raw.reactions && Array.isArray(raw.reactions)) {
            metadata.reactions = raw.reactions;
        }
        if (raw.edited_at) {
            metadata.edited_at = raw.edited_at;
        }
        if (raw.is_deleted) {
            metadata.is_deleted = raw.is_deleted;
        }
        if (raw.is_forwarded || raw.isForwarded || raw.metadata?.is_forwarded || raw.metadata?.isForwarded) {
            metadata.is_forwarded = true;
        }
        const isFwd = Boolean(raw.is_forwarded || raw.isForwarded || metadata.is_forwarded);
        
        metadata.sender = raw.sender || {};
        return this._createCanonicalMessage({
            id: String(raw.id),
            conversationId: Number(raw.conversation_id ?? raw.conversation ?? raw.conversationId ?? 0),
            senderId: Number(raw.sender?.id ?? raw.sender_id ?? raw.senderId ?? 0),
            timestamp: new Date(raw.created_at || raw.timestamp || Date.now()).toISOString(),
            status: mappedStatus,
            content: raw.is_deleted ? 'This message was deleted' : (raw.content || raw.body || ""),
            type: messageType,
            metadata: metadata,
            isOptimistic: false,
            isDeleted: Boolean(raw.is_deleted),
            isForwarded: isFwd,
            is_forwarded: isFwd,
            editedAt: raw.edited_at || null,
            replyToId: raw.reply_to || raw.reply_to_id || null,
            replyToDetails: raw.reply_to_details || null,
            sortOrder: new Date(raw.created_at || raw.timestamp || Date.now()).getTime()
        });
    }

    async restoreSavedHistory(conversationId) {
        // Both direct and group chats use globally unique Conversation IDs.
        if (!window.offlineCache) return;
        try {
            const cached = await window.offlineCache.getMessages(conversationId, 1000);
            if (!cached.length) return;
            const response = await fetch('/offline/message-access/', {
                method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': getCSRFToken() },
                body: JSON.stringify({ conversation: conversationId, ids: cached.map(message => message.id) })
            });
            if (!response.ok) return; // Network failure leaves the existing view intact.
            const messages = (await response.json()).messages;
            const present = new Set(messages.map(message => String(message.id)));
            await window.offlineCache.deleteMessages(cached.filter(message => !present.has(String(message.id))).map(message => message.id));
            await window.offlineCache.saveMessages(messages);
            store.addMessages(this._filterDeletedForMe(messages, conversationId).map(message => this.normalizeServerMessage(message)));
        } catch (error) {
            console.warn('[OFFLINE_CACHE] Saved history refresh deferred:', error);
        }
    }

    async loadConversationHistory(conversationId) {
        console.log('[MESSAGE_SERVICE] Loading conversation history for:', conversationId);
        try {
            const INITIAL_BATCH_LIMIT = 40;
            // 1. Instant 0ms Paint from fresh server-serialized DOM script if present
            const inlineScript = document.getElementById('initialConversationMessages');
            let serverHydrated = false;
            let cacheLoaded = false;
            if (inlineScript && inlineScript.textContent) {
                try {
                    const rawInitial = JSON.parse(inlineScript.textContent.trim());
                    // Remove after consuming so subsequent programmatic calls don't read stale DOM scripts
                    inlineScript.remove();
                    if (Array.isArray(rawInitial)) {
                        const targetId = Number(conversationId);
                        const convMatches = rawInitial.filter(
                            m => Number(m.conversation_id ?? m.conversation ?? m.conversationId) === targetId
                        );
                        if (rawInitial.length === 0 || convMatches.length > 0) {
                            const filteredInitial = this._filterDeletedForMe(convMatches, conversationId);
                            const normalized = filteredInitial.map(m => this.normalizeServerMessage(m));
                            store.setHasMoreOlderMessages(convMatches.length >= INITIAL_BATCH_LIMIT);
                            store.addMessages(normalized);
                            store.setInitialHistoryLoaded(true);
                            serverHydrated = true;
                            if (window.offlineCache && convMatches.length > 0) {
                                await window.offlineCache.saveMessages(convMatches);
                                await this.restoreSavedHistory(conversationId);
                            }
                            return;
                        }
                    }
                } catch (jsonErr) {
                    console.warn('[MESSAGE_SERVICE] Error parsing inline initial messages:', jsonErr);
                }
            }

            // 2. Instant Paint from Offline Cache if not hydrated from server DOM
            if (!serverHydrated && window.offlineCache) {
                try {
                    if (!window.offlineCache.db) {
                        await window.offlineCache.init();
                    }
                    const cachedMessages = await window.offlineCache.getMessages(conversationId, 1000);
                    if (cachedMessages && cachedMessages.length > 0) {
                        const filteredCache = this._filterDeletedForMe(cachedMessages, conversationId);
                        const normalizedCache = filteredCache.map(m => this.normalizeServerMessage(m));
                        store.addMessages(normalizedCache);
                        store.setInitialHistoryLoaded(true);
                        cacheLoaded = true;
                    }
                } catch (cacheErr) {
                    console.warn('[MESSAGE_SERVICE] Error reading offline cache:', cacheErr);
                }
            }

            // 3. Server fetch routine (only when not already hydrated by fresh server HTML)
            const syncPromise = (async () => {
                const apiUrl = `/messaging/v1/messages/`;
                const queryParams = `?conversation=${conversationId}&limit=${INITIAL_BATCH_LIMIT}`;

                const res = await fetch(`${apiUrl}${queryParams}`, {
                    headers: {
                        'X-CSRFToken': getCSRFToken()
                    }
                });

                if (!res.ok) throw new Error('HTTP error ' + res.status);

                const data = await res.json();
                const messages = Array.isArray(data) ? data : (data.results || data.messages || []);

                if (window.offlineCache && messages.length > 0) {
                    window.offlineCache.saveMessages(messages).catch(e => console.warn('[OFFLINE_CACHE] Error caching messages:', e));
                }

                store.setHasMoreOlderMessages(messages.length >= INITIAL_BATCH_LIMIT);

                const filteredMessages = this._filterDeletedForMe(messages, conversationId);
                const normalizedList = filteredMessages.map(raw => this.normalizeServerMessage(raw));
                store.addMessages(normalizedList);
            })();

            if (!cacheLoaded) {
                await syncPromise;
            } else {
                syncPromise.catch(err => console.warn('[MESSAGE_SERVICE] Background sync error:', err));
            }

        }
        catch (error) {
            console.warn('[MESSAGE_SERVICE] Network history load failed (possibly offline):', error);
            const currentMessages = store.getState()?.messages || [];
            if (currentMessages.length > 0) {
                console.log(`[MESSAGE_SERVICE] Gracefully displaying ${currentMessages.length} cached offline messages.`);
            } else {
                throw error;
            }
        }
        finally {
            store.setInitialHistoryLoaded(true);
            console.log('[MESSAGE_SERVICE] loadConversationHistory completed and marked loaded in store');
        }
    }

    /**
     * Load older messages for cursor pagination (scroll-to-top)
     * @param {number|string} conversationId
     * @returns {Promise<number>} Number of older messages loaded
     */
    async loadOlderMessages(conversationId) {
        if (this._isLoadingOlder) return 0;
        if (!store.hasMoreOlderMessages()) return 0;

        const oldestId = store.getOldestMessageId();
        if (!oldestId) return 0;

        this._isLoadingOlder = true;
        console.log('[MESSAGE_SERVICE] Loading older messages before id:', oldestId);

        try {
            const PAGE_LIMIT = 30;
            const apiUrl = `/messaging/v1/messages/`;
            const queryParams = `?conversation=${conversationId}&limit=${PAGE_LIMIT}&before_id=${oldestId}`;

            const res = await fetch(`${apiUrl}${queryParams}`, {
                headers: {
                    'X-CSRFToken': getCSRFToken()
                }
            });

            if (!res.ok) throw new Error('HTTP error loading older messages');

            const data = await res.json();
            const messages = Array.isArray(data) ? data : (data.results || data.messages || []);
            console.log('[MESSAGE_SERVICE] Received older messages count:', messages.length);

            if (messages.length < PAGE_LIMIT) {
                store.setHasMoreOlderMessages(false);
            }

            if (messages.length > 0) {
                if (window.offlineCache) {
                    window.offlineCache.saveMessages(messages).catch(e => console.warn('[OFFLINE_CACHE] Error caching messages:', e));
                }
                const filteredMessages = this._filterDeletedForMe(messages, conversationId);
                const normalizedList = filteredMessages.map(raw => this.normalizeServerMessage(raw));
                store.addMessages(normalizedList);
            }

            return messages.length;
        } catch (error) {
            console.error('[MESSAGE_SERVICE] Failed to load older messages:', error);
            return 0;
        } finally {
            this._isLoadingOlder = false;
        }
    }


    /**
     * Send outgoing message (UI → MessageService → WebSocket/HTTP → Store)
     * @param {string} content - Message content
     * @param {Object} options - Additional options
     */
    async sendMessage(content, options = {}) {
        this._log('SEND_MESSAGE', { content, options });

        const state = store.getState();
        
        if (!content || !content.trim()) {
            this._log('SEND_MESSAGE_REJECTED', 'Empty content');
            return false;
        }

        const cleanContent = content.trim();
        const tempId = `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;

        this.messageStates.set(tempId, {
            status: MESSAGE_STATE.SENDING,
            retryCount: 0,
            lastError: null,
            createdAt: Date.now()
        });

        // Instant optimistic message render (0ms - never blocked by link metadata fetch)
        const optimisticMessage = this._createCanonicalMessage({
            id: tempId,
            conversationId: state.conversationId,
            senderId: state.currentUserId,
            timestamp: new Date().toISOString(),
            status: MESSAGE_STATE.SENDING,
            content: cleanContent,
            type: options.type || 'text',
            replyToId: options.reply_to_id || null,
            replyToDetails: options.reply_to_details || null,
            metadata: {
                ...(options.metadata || {}),
                reply_to_id: options.reply_to_id || null,
                reply_to_details: options.reply_to_details || null,
                temp_id: tempId
            },
            isOptimistic: true,
            sortOrder: Date.now()
        });

        // Add to store immediately
        store.addMessage(optimisticMessage);

        // Detect URLs in content and fetch metadata with a short timeout
        const urls = this.extractUrls(cleanContent);
        let linkMetadata = null;
        if (urls.length > 0) {
            try {
                linkMetadata = await Promise.race([
                    this.fetchLinkMetadata(urls[0]),
                    new Promise((_, reject) => setTimeout(() => reject(new Error('Link metadata timeout')), 2000))
                ]);
                if (linkMetadata && store.getMessageById(tempId)) {
                    store.updateMessage(tempId, {
                        type: 'link',
                        metadata: {
                            link_url: linkMetadata.url,
                            link_title: linkMetadata.title,
                            link_description: linkMetadata.description,
                            link_image: linkMetadata.image,
                            link_type: linkMetadata.type
                        }
                    });
                }
            } catch (error) {
                console.debug('Failed to fetch link metadata:', error);
            }
        }

        // Prepare message payload
        let messageData = {
            type: 'chat_message',
            temp_id: tempId,
            content: cleanContent,
            reply_to_id: options.reply_to_id || null,
            message_type: options.type || (linkMetadata ? 'link' : 'text'),
            metadata: {
                ...(options.metadata || {}),
                temp_id: tempId
            }
        };

        if (linkMetadata) {
            messageData.link_url = linkMetadata.url;
            messageData.link_title = linkMetadata.title;
            messageData.link_description = linkMetadata.description;
            messageData.link_image = linkMetadata.image;
            messageData.link_type = linkMetadata.type;
        }

        // Send immediately over WebSocket (synchronous boolean return)
        const sent = webSocketManager.send(messageData);
        if (sent) {
            // Start watchdog timer: if server ACK or broadcast hasn't arrived within 4s, fallback to HTTP POST
            this._startAckTimer(tempId, () => {
                const msg = store.getMessageById(tempId);
                if (msg && (msg.isOptimistic || msg.status === MESSAGE_STATE.SENDING)) {
                    console.warn('[MESSAGE_SERVICE] WebSocket ACK timed out for', tempId, '— falling back to REST API');
                    this._sendMessageViaRestFallback(tempId, state, cleanContent, options, linkMetadata, messageData);
                }
            }, 4000);
        } else {
            // WebSocket not open: trigger background reconnect and immediately send via HTTP REST fallback
            if (!webSocketManager.isConnected() && !webSocketManager.paused) {
                webSocketManager.connect();
            }
            this._sendMessageViaRestFallback(tempId, state, cleanContent, options, linkMetadata, messageData);
        }

        return tempId;
    }

    /**
     * Fallback HTTP POST to guarantee message delivery when WebSocket is disconnected or unacknowledged
     */
    async _sendMessageViaRestFallback(tempId, state, cleanContent, options = {}, linkMetadata = null, messageData = null) {
        if (this.restFallbacksInFlight.has(tempId)) return false;

        const existing = store.getMessageById(tempId);
        if (!existing) {
            // Already reconciled by WebSocket ACK/broadcast
            return true;
        }

        this.restFallbacksInFlight.add(tempId);
        try {
            const payload = {
                conversation: state.conversationId,
                content: cleanContent,
                reply_to: options.reply_to_id || null,
                temp_id: tempId,
                message_type: options.type || (linkMetadata ? 'link' : 'text')
            };
            if (linkMetadata) {
                payload.link_url = linkMetadata.url;
                payload.link_title = linkMetadata.title;
                payload.link_description = linkMetadata.description;
                payload.link_image = linkMetadata.image;
                payload.link_type = linkMetadata.type;
            }

            const res = await fetch('/messaging/v1/messages/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify(payload)
            });

            if (!res.ok) {
                throw new Error(`REST fallback failed with status ${res.status}`);
            }

            const saved = await res.json();
            this._clearAckTimer(tempId);
            this._transitionMessageState(tempId, MESSAGE_STATE.SENT);

            const savedId = saved.id || saved.message_id;
            const normalized = saved.sender ? this.normalizeServerMessage(saved) : null;
            if (normalized) {
                normalized.status = normalized.status && normalized.status !== 'sending' ? normalized.status : 'sent';
                normalized.isOptimistic = false;
            }

            if (savedId && store.getMessageById(tempId)) {
                store.updateMessageIdAndStatus(
                    tempId,
                    savedId,
                    saved.status || 'sent',
                    saved.created_at || normalized?.timestamp,
                    normalized
                );
            } else if ( store.getMessageById(tempId) ) {
                store.updateMessageStatus(tempId, 'sent');
            }

            this.messageQueue = this.messageQueue.filter(m => m.tempId !== tempId);
            return true;
        } catch (err) {
            console.error('[MESSAGE_SERVICE] REST fallback error:', err);
            if (store.getMessageById(tempId)) {
                this._transitionMessageState(tempId, MESSAGE_STATE.FAILED_SEND, err.message);
                store.updateMessageStatus(tempId, MESSAGE_STATE.FAILED_SEND);
                if (messageData && !this.messageQueue.some(m => m.tempId === tempId)) {
                    this.messageQueue.push({ ...messageData, tempId, options, linkMetadata });
                }
            }
            return false;
        } finally {
            this.restFallbacksInFlight.delete(tempId);
        }
    }

    /**
     * Send direct media message (sticker or GIF) with 1-tap instant dispatch
     * @param {Object} media - { type, url, previewUrl, meta }
     */
    async sendDirectMedia(media) {
        if (!media || !media.url) return false;

        const state = store.getState();
        const tempId = `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
        const isSticker = media.type === 'sticker';
        const isGif = media.type === 'gif';

        const optimisticMessage = this._createCanonicalMessage({
            id: tempId,
            conversationId: state.conversationId,
            senderId: state.currentUserId,
            timestamp: new Date().toISOString(),
            status: MESSAGE_STATE.SENDING,
            content: '',
            type: 'media',
            metadata: {
                type: media.type,
                url: media.url,
                preview_url: media.previewUrl || media.url,
                title: media.meta?.title || media.title || '',
                width: media.meta?.width,
                height: media.meta?.height,
                is_sticker: isSticker,
                is_gif: isGif,
                temp_id: tempId
            },
            isOptimistic: true,
            sortOrder: Date.now()
        });

        // Add to store immediately (Telegram instant feel)
        store.addMessage(optimisticMessage);

        const messageData = {
            type: 'chat_message',
            temp_id: tempId,
            content: '',
            message_type: media.type,
            attachment_type: media.type,
            attachment_url: media.url,
            metadata: {
                type: media.type,
                url: media.url,
                preview_url: media.previewUrl || media.url,
                title: media.meta?.title || media.title || '',
                width: media.meta?.width,
                height: media.meta?.height,
                is_sticker: isSticker,
                is_gif: isGif,
                temp_id: tempId
            }
        };

        const sent = webSocketManager.send(messageData);
        if (!sent) {
            if (!webSocketManager.isConnected() && !webSocketManager.paused) {
                webSocketManager.connect();
            }
            store.updateMessage(tempId, { status: MESSAGE_STATE.FAILED_SEND });
            return false;
        }

        return tempId;
    }

    /**
     * Process incoming WebSocket message (WebSocket → MessageService → Store)
     * @param {Object} data - WebSocket message data
     */
    async processIncomingMessage(data) {
        this._log('PROCESS_INCOMING_MESSAGE', data);

        try {
            switch (data.type) {
                case 'message_ack':
                    // Instant server ACK: clear watchdog and convert temp_id to real server ID with sent tick
                    if (data.temp_id) {
                        this._clearAckTimer(data.temp_id);
                        this._transitionMessageState(data.temp_id, data.status || MESSAGE_STATE.SENT);
                    }
                    store.updateMessageIdAndStatus(data.temp_id, data.message_id, data.status || 'sent', data.created_at);
                    break;

                case 'chat_message':
                case 'message':
                    const msgData = data.message || data.data || data;
                    let incomingTempId = data.temp_id || msgData?.temp_id || msgData?.metadata?.temp_id;
                    const currentUserId = store.getState().currentUserId;
                    const isOwnMessage = (msgData.sender && Number(msgData.sender.id) === Number(currentUserId)) ||
                                          Number(msgData.sender_id) === Number(currentUserId) ||
                                          Number(msgData.senderId) === Number(currentUserId);

                    // Fallback reconciliation for own messages if temp_id is missing in WS payload
                    if (!incomingTempId && isOwnMessage) {
                        const existingMsgs = store.getState().messages;
                        const incomingContent = (msgData.content || '').trim();
                        const matchingOpt = existingMsgs.find(m => 
                            m.isOptimistic && (
                                (incomingContent && (m.content || '').trim() === incomingContent) ||
                                m.type === 'voice_note' || 
                                m.metadata?.is_voice_note || 
                                m.type === 'audio' ||
                                m.type === 'media' || 
                                m.type === 'media_group' ||
                                m.type === 'file' ||
                                m.type === msgData.message_type
                            )
                        );
                        if (matchingOpt) {
                            incomingTempId = matchingOpt.id;
                        }
                    }

                    if (incomingTempId) {
                        this._clearAckTimer(incomingTempId);
                        this._transitionMessageState(incomingTempId, msgData.status || MESSAGE_STATE.SENT);
                    }

                    const canonical = this.normalizeServerMessage(msgData);
                    if (isOwnMessage && (!canonical.status || canonical.status === 'sending' || canonical.status === 'uploading')) {
                        canonical.status = 'sent';
                    }
                    canonical.isOptimistic = false;

                    if (incomingTempId && store.getMessageById(incomingTempId)) {
                        store.updateMessageIdAndStatus(
                            incomingTempId,
                            canonical.id,
                            canonical.status || 'sent',
                            canonical.timestamp,
                            canonical
                        );
                    } else if (store.getMessageById(String(msgData.id))) {
                        // Already stored (e.g., from ACK or HTTP response), update in-place with full canonical payload
                        store.updateMessage(String(msgData.id), canonical);
                    } else {
                        store.addMessage(canonical);
                        // If sent by peer, send delivery ACK & play audio
                        if (Number(canonical.senderId) !== Number(currentUserId)) {
                            webSocketManager.send({
                                type: 'message_delivered',
                                message_id: canonical.id
                            });
                            messageSoundManager.playMessageReceived();
                        }
                    }
                    break;

                case 'message_status':
                    // data: { message_id, status }
                    store.updateMessageStatus(String(data.message_id), data.status);
                    break;

                case 'message_delivered':
                    store.updateMessageStatus(String(data.message_id), 'delivered');
                    break;

                case 'read_receipt':
                    // data: { last_read_message_id, user_id, read_avatar }
                    if (data.last_read_message_id) {
                        store.markMessagesAsReadUpTo(data.last_read_message_id, data.read_avatar);
                    } else if (data.message_id) {
                        store.updateMessageStatus(String(data.message_id), 'read');
                    }
                    break;

                case 'video_ready':
                    // Swap stale pre-transcode URL for processed file
                    store.updateMessage(String(data.message_id), {
                        attachment_url: data.file_url,
                        duration: data.duration,
                    });
                    try {
                        if (window.offlineCache && typeof window.offlineCache.saveMessages === 'function') {
                            const existing = store.getMessageById(String(data.message_id));
                            window.offlineCache.saveMessages([{...(existing || {}),
                                id: data.message_id, attachment_url: data.file_url}]).catch(() => {});
                        }
                    } catch (e) {}
                    break;

                case 'typing':
                case 'typing_indicator':
                    const activeUserId = store.getState().currentUserId;
                    if (Number(data.user_id) === Number(activeUserId)) {
                        break;
                    }
                    const isTyping = data.is_typing !== undefined ? Boolean(data.is_typing) : Boolean(data.typing);
                    store.setTypingIndicator(data.user_id, data.username, isTyping);
                    break;

                case 'recording_audio':
                    const activeUserIdForRec = store.getState().currentUserId;
                    if (Number(data.user_id) === Number(activeUserIdForRec)) {
                        break;
                    }
                    store.setRecordingIndicator(data.user_id, data.username, Boolean(data.is_recording));
                    break;

                case 'peer_status':
                case 'user_status':
                    store.setPeerOnlineStatus(data.user_id, data.is_online, data.last_seen);
                    break;

                case 'reaction_update':
                    store.updateMessage(String(data.message_id), { reactions: data.reactions });
                    break;

                case 'message_edited':
                    store.updateMessage(String(data.message_id), {
                        content: data.content,
                        editedAt: data.edited_at
                    });
                    if (window.offlineCache && typeof window.offlineCache.saveMessages === 'function') {
                        const existing = store.getMessageById(data.message_id);
                        window.offlineCache.saveMessages([{
                            ...(existing || {}),
                            id: data.message_id,
                            content: data.content,
                            edited_at: data.edited_at
                        }]).catch(() => {});
                    }
                    break;

                case 'message_deleted':
                    store.updateMessage(String(data.message_id), {
                        isDeleted: true,
                        content: 'This message was deleted'
                    });
                    if (window.offlineCache && typeof window.offlineCache.saveMessages === 'function') {
                        const existing = store.getMessageById(data.message_id);
                        window.offlineCache.saveMessages([{
                            ...(existing || {}),
                            id: data.message_id,
                            is_deleted: true,
                            content: 'This message was deleted'
                        }]).catch(() => {});
                    }
                    break;

                case 'pong':
                    break;

                default:
                    this._log('UNKNOWN_MESSAGE_TYPE', data.type);
            }
        } catch (error) {
            this._log('INCOMING_MESSAGE_ERROR', { error, data });
            console.error('MessageService: Error processing incoming message:', error, data);
        }
    }

    /**
     * Process chat message with canonical schema validation
     * @param {Object} messageData - Message data
     */
    async _processChatMessage(messageData) {
        console.group('[MESSAGE_SERVICE] ========== PROCESS CHAT MESSAGE ==========');
        console.log('[MESSAGE_SERVICE] Message ID:', messageData.id);
        console.log('[MESSAGE_SERVICE] Sender ID:', messageData.sender_id);
        console.log('[MESSAGE_SERVICE] Content:', messageData.content);
        this._log('PROCESS_CHAT_MESSAGE', messageData);

        try {
            // Decrypt message if encrypted
            let content = messageData.content;

            if (messageData.is_encrypted && this.e2eEncryption) {
                try {
                    content = await this.e2eEncryption.decryptMessage(messageData.encrypted_content);
                    this._log('MESSAGE_DECRYPTED', { messageId: messageData.id });
                } catch (error) {
                    this._log('DECRYPTION_FAILED', error);
                    console.error('MessageService: Failed to decrypt message:', error);
                    content = '[Encrypted message - unable to decrypt]';
                }
            }

            // Normalize to canonical schema
            const canonicalMessage = this.normalizeServerMessage({
                ...messageData,
                content
            });

            console.log('[MESSAGE_SERVICE] Canonical message type:', canonicalMessage.type, 'Metadata:', canonicalMessage.metadata);

            // === PRIMARY FLOW - MUST NEVER FAIL ===
            // This is the critical path that must always succeed
            const tempId = messageData.temp_id;

            if (tempId) {
                console.log('[MESSAGE_SERVICE] Handling message confirmation for temp_id:', tempId);
                this._handleMessageConfirmation(canonicalMessage, tempId);
            } else {
                console.log('[MESSAGE_SERVICE] Adding new message to store:', canonicalMessage.id);
                store.addMessage(canonicalMessage);
                console.log('[MESSAGE_SERVICE] Message added to store. Current message count:', store.getMessages().length);
            }

            // Emit event for secondary operations (non-blocking)
            eventBus.emit('message_received', canonicalMessage);

            // === SECONDARY SIDE EFFECTS - NON-BLOCKING, FAULT-ISOLATED ===
            // These operations run asynchronously and must never fail the primary flow
            queueMicrotask(() => {
                this._safeSendDeliveryAck(canonicalMessage);
            });

            queueMicrotask(() => {
                this._safeTriggerNotifications(canonicalMessage);
            });

            queueMicrotask(() => {
                this._safePlayMessageSound(canonicalMessage);
            });

        } catch (error) {
            console.error('[MESSAGE_SERVICE] CRITICAL ERROR in primary message flow:', error);
            console.error('[MESSAGE_SERVICE] Error stack:', error.stack);
            // Even if primary flow fails, we must not crash the websocket pipeline
        } finally {
            console.groupEnd();
        }
    }

    /**
     * Handle message confirmation for optimistic updates
     * @param {Object} confirmedMessage - Confirmed message from server
     */
    _handleMessageConfirmation(confirmedMessage, tempId) {
        this._log('HANDLE_MESSAGE_CONFIRMATION', confirmedMessage);

        const messages = store.getMessages();

        // Find optimistic message to replace
        const optimisticMessage = messages.find(m => m.id === tempId);
        
        if (optimisticMessage) {
            this._log('REPLACING_OPTIMISTIC_MESSAGE', {
                tempId: optimisticMessage.id,
                actualId: confirmedMessage.id
            });
            
            // Replace optimistic message with confirmed message (ONLY store mutates)
            store.replaceMessage(optimisticMessage.id, confirmedMessage);
        } else {
            // No optimistic message found, just add the confirmed one
            store.addMessage(confirmedMessage);
        }
    }

    /**
     * Process typing indicator
     * @param {Object} data - Typing data
     */
    _processTypingIndicator(data) {
        this._log('PROCESS_TYPING_INDICATOR', data);

        const state = store.getState();
        
        if (data.user_id !== state.currentUserId) {
            // Update typing indicator in store (ONLY store mutates)
            store.setTypingIndicator(data.user_id, data.username, data.is_typing);
        }
    }

    /**
     * Process read receipt from WebSocket
     * @param {Object} data - Read receipt data
     */
    _processReadReceipt(data) {
        console.log('[MESSAGE_SERVICE] Processing read receipt:', data);
        this._log('PROCESS_READ_RECEIPT', data);

        // Update message read status in store (ONLY store mutates)
        store.updateMessage(data.message_id, {
            status: MESSAGE_STATE.READ,
            metadata: {
                read_avatar: data.read_avatar
            }
        });
        console.log('[MESSAGE_SERVICE] Updated message', data.message_id, 'to read status with avatar:', data.read_avatar);
    }

    /**
     * Process message delivered status from WebSocket
     * @param {Object} data - Message delivered data
     */
    _processMessageDelivered(data) {
        console.log('[MESSAGE_SERVICE] Processing message delivered:', data);
        this._log('PROCESS_MESSAGE_DELIVERED', data);

        const state = store.getState();
        const message = store.getMessageById(data.message_id);

        // Only update if message hasn't been read yet (keep read status if it exists)
        if (message && message.status === 'sent') {
            store.updateMessage(data.message_id, {
                status: 'delivered'
            });
            console.log('[MESSAGE_SERVICE] Updated message', data.message_id, 'to delivered status');
        }
    }

    /**
     * Process user online/offline status
     * @param {Object} data - User status data
     */
    _processUserStatus(data) {
        this._log('PROCESS_USER_STATUS', data);

        const state = store.getState();

        console.log('[MESSAGE SERVICE] Received user_status', data);

        // Only process if it's not the current user
        if (data.user_id !== state.currentUserId) {
            // Update peer online status in store (ONLY store mutates)
            console.log('[MESSAGE SERVICE] Updating peer status', data.user_id, data.is_online, data.last_seen);
            store.setPeerOnlineStatus(data.user_id, data.is_online, data.last_seen);
        }
    }

    /**
     * Process message queue (resend queued messages)
     */
    async processMessageQueue() {
        this._log('PROCESS_MESSAGE_QUEUE');

        const queue = [...this.messageQueue];
        this.messageQueue = []; // Clear queue
        const state = store.getState();
        
        for (const queuedMessage of queue) {
            try {
                const tempId = queuedMessage.tempId;
                if (!store.getMessageById(tempId)) {
                    continue;
                }
                
                // Transition to RETRYING
                this._transitionMessageState(tempId, MESSAGE_STATE.RETRYING);
                store.updateMessage(tempId, { status: MESSAGE_STATE.RETRYING });
                
                const messageData = {
                    type: queuedMessage.type || 'chat_message',
                    temp_id: queuedMessage.tempId,
                    content: queuedMessage.content,
                    reply_to_id: queuedMessage.reply_to_id || queuedMessage.options?.reply_to_id || null,
                    encrypted_content: queuedMessage.encrypted_content,
                    is_encrypted: queuedMessage.is_encrypted,
                    message_type: queuedMessage.message_type,
                    metadata: queuedMessage.metadata
                };

                const sent = webSocketManager.send(messageData);

                if (sent) {
                    this._log('QUEUED_MESSAGE_SENT', { tempId });
                    this._startAckTimer(tempId, () => {
                        const msg = store.getMessageById(tempId);
                        if (msg && (msg.isOptimistic || msg.status === MESSAGE_STATE.RETRYING || msg.status === MESSAGE_STATE.SENDING)) {
                            this._sendMessageViaRestFallback(
                                tempId,
                                state,
                                queuedMessage.content,
                                queuedMessage.options || {},
                                queuedMessage.linkMetadata || null,
                                queuedMessage
                            );
                        }
                    }, 4000);
                } else {
                    await this._sendMessageViaRestFallback(
                        tempId,
                        state,
                        queuedMessage.content,
                        queuedMessage.options || {},
                        queuedMessage.linkMetadata || null,
                        queuedMessage
                    );
                }
            } catch (error) {
                this._log('QUEUE_SEND_ERROR', { error, queuedMessage });
                // Re-queue on error
                this.messageQueue.push(queuedMessage);
            }
        }
    }
    
    /**
     * Transition message state with validation
     * @param {string} tempId - Temporary message ID
     * @param {string} newState - New state
     * @param {string} error - Error message if transitioning to FAILED state
     */
    _transitionMessageState(tempId, newState, error = null) {
        const currentState = this.messageStates.get(tempId);
        if (!currentState) {
            console.warn('[MESSAGE_SERVICE] No state found for tempId:', tempId);
            return;
        }
        
        const oldState = currentState.status;

        // Retry is user-initiated: always allow entering RETRYING from any
        // non-terminal state (strict map lookups here blocked legitimate
        // user taps, e.g. retry-while-stalled-upload).
        if (newState === MESSAGE_STATE.RETRYING &&
            ![MESSAGE_STATE.SENT, MESSAGE_STATE.DELIVERED, MESSAGE_STATE.READ].includes(oldState)) {
            currentState.status = newState;
            this.messageStates.set(tempId, currentState);
            this._log('STATE_TRANSITION', { tempId, from: oldState, to: newState, error });
            return;
        }

        // Validate transition
        if (!isValidStateTransition(oldState, newState)) {
            console.error('[MESSAGE_SERVICE] Invalid state transition:', {
                tempId,
                from: oldState,
                to: newState,
                error
            });
            // Reject invalid transition
            return;
        }
        
        // Update state
        currentState.status = newState;
        if (error) {
            currentState.lastError = error;
            currentState.retryCount++;
        }
        
        this.messageStates.set(tempId, currentState);
        this._log('STATE_TRANSITION', { tempId, from: oldState, to: newState, error });
        
        // Play sound for queued/sending → sent transitions
        if (messageSoundManager.shouldPlaySound(oldState, newState)) {
            messageSoundManager.playSendSound(tempId);
        }
        
        // Clean up old states for sent/delivered/read messages
        if ([MESSAGE_STATE.SENT, MESSAGE_STATE.DELIVERED, MESSAGE_STATE.READ].includes(newState)) {
            // Keep state for a while, then clean up
            setTimeout(() => {
                this.messageStates.delete(tempId);
            }, 60000); // 1 minute
        }
    }
    
    /**
     * Retry a failed message
     * @param {string} tempId - Temporary message ID
     */
    async retryMessage(tempId) {
        this._log('RETRY_MESSAGE', { tempId });
        
        const messageState = this.messageStates.get(tempId);
        if (!messageState) {
            console.warn('[MESSAGE_SERVICE] No state found for retry:', tempId);
            return false;
        }
        
        if (messageState.status !== MESSAGE_STATE.FAILED_SEND && messageState.status !== MESSAGE_STATE.FAILED_UPLOAD) {
            console.warn('[MESSAGE_SERVICE] Cannot retry message in state:', messageState.status);
            return false;
        }
        
        // Find the message in the queue
        const queuedMessage = this.messageQueue.find(m => m.tempId === tempId);
        if (!queuedMessage) {
            console.warn('[MESSAGE_SERVICE] No queued message found for retry:', tempId);
            return false;
        }
        
        // Transition to RETRYING
        this._transitionMessageState(tempId, MESSAGE_STATE.RETRYING);
        store.updateMessage(tempId, { status: MESSAGE_STATE.RETRYING });
        
        try {
            const state = store.getState();
            const messageData = {
                type: queuedMessage.type || 'chat_message',
                temp_id: queuedMessage.tempId,
                content: queuedMessage.content,
                reply_to_id: queuedMessage.reply_to_id || queuedMessage.options?.reply_to_id || null,
                encrypted_content: queuedMessage.encrypted_content,
                is_encrypted: queuedMessage.is_encrypted,
                message_type: queuedMessage.message_type,
                metadata: queuedMessage.metadata
            };
            
            const sent = webSocketManager.send(messageData);
            
            if (sent) {
                this.messageQueue = this.messageQueue.filter(m => m.tempId !== tempId);
                this._startAckTimer(tempId, () => {
                    const msg = store.getMessageById(tempId);
                    if (msg && (msg.isOptimistic || msg.status === MESSAGE_STATE.RETRYING || msg.status === MESSAGE_STATE.SENDING)) {
                        this._sendMessageViaRestFallback(
                            tempId,
                            state,
                            queuedMessage.content,
                            queuedMessage.options || {},
                            queuedMessage.linkMetadata || null,
                            queuedMessage
                        );
                    }
                }, 4000);
                this._log('RETRY_SUCCESS', { tempId });
                return true;
            } else {
                return await this._sendMessageViaRestFallback(
                    tempId,
                    state,
                    queuedMessage.content,
                    queuedMessage.options || {},
                    queuedMessage.linkMetadata || null,
                    queuedMessage
                );
            }
        } catch (error) {
            // Transition back to FAILED_SEND
            this._transitionMessageState(tempId, MESSAGE_STATE.FAILED_SEND, error.message);
            store.updateMessage(tempId, { status: MESSAGE_STATE.FAILED_SEND });
            this._log('RETRY_ERROR', { tempId, error });
            return false;
        }
    }

    /**
     * Create canonical message object
     * @param {Object} data - Message data
     * @returns {Object} Canonical message
     */
    _createCanonicalMessage(data) {
        const isFwd = Boolean(
            data.isForwarded || 
            data.is_forwarded || 
            data.forwarded ||
            data.metadata?.is_forwarded || 
            data.metadata?.isForwarded || 
            data.metadata?.forwarded
        );
        return {
            id: data.id,
            conversationId: data.conversationId,
            senderId: data.senderId,
            timestamp: data.timestamp,
            status: data.status,
            content: data.content,
            type: data.type,
            metadata: data.metadata || {},
            attachments: data.attachments || data.metadata?.attachments || [],
            global_caption: data.global_caption || data.metadata?.global_caption || '',
            isOptimistic: data.isOptimistic || false,
            isDeleted: Boolean(data.isDeleted || data.metadata?.is_deleted),
            isForwarded: isFwd,
            is_forwarded: isFwd,
            editedAt: data.editedAt || data.metadata?.edited_at || null,
            replyToId: data.replyToId || data.metadata?.reply_to_id || data.metadata?.reply_to || null,
            replyToDetails: data.replyToDetails || data.metadata?.reply_to_details || null,
            sortOrder: data.sortOrder ?? Date.now()
        };
    }

    /**
     * Filter out messages deleted locally for the current user
     */
    _filterDeletedForMe(messages, conversationId) {
        if (!conversationId || !messages || !messages.length) return messages;
        try {
            const raw = localStorage.getItem(`deleted_for_me_${conversationId}`);
            if (!raw) return messages;
            const deletedSet = new Set(JSON.parse(raw).map(id => String(id)));
            return messages.filter(m => !deletedSet.has(String(m.id)));
        } catch (_) {
            return messages;
        }
    }

    /**
     * Set recipient public key for encryption
     * @param {string} publicKey - Recipient's public key
     */
    setRecipientPublicKey(publicKey) {
        this._log('SET_RECIPIENT_PUBLIC_KEY');
        this.recipientPublicKey = publicKey;
    }

    /**
     * Send typing indicator to backend
     * @param {boolean} isTyping - Whether user is typing
     */
    sendTypingIndicator(isTyping) {
        this._log('SEND_TYPING_INDICATOR', { isTyping });
        console.log('[MESSAGE_SERVICE] Sending typing indicator:', isTyping);

        // Send via WebSocket (transport ONLY)
        const sent = webSocketManager.send({
            type: 'typing',
            is_typing: isTyping,
            typing: isTyping
        });

        console.log('[MESSAGE_SERVICE] Typing indicator sent:', sent);
    }

    /**
     * Send recording audio indicator to backend
     * @param {boolean} isRecording - Whether user is recording audio
     */
    sendRecordingIndicator(isRecording) {
        this._log('SEND_RECORDING_INDICATOR', { isRecording });
        console.log('[MESSAGE_SERVICE] Sending recording indicator:', isRecording);

        const sent = webSocketManager.send({
            type: 'recording_audio',
            is_recording: Boolean(isRecording)
        });

        console.log('[MESSAGE_SERVICE] Recording indicator sent:', sent);
    }

    /**
     * Send read receipt for a message
     * @param {string} messageId - Message ID to mark as read
     */
    sendReadReceipt(messageId) {
        console.log('[MESSAGE_SERVICE] Sending read receipt for message:', messageId);
        this._log('SEND_READ_RECEIPT', { messageId });

        if (!messageId) {
            console.log('[MESSAGE_SERVICE] No messageId provided, skipping read receipt');
            return false;
        }

        // Send via WebSocket (transport ONLY)
        const sent = webSocketManager.send({
            type: 'read_receipt',
            message_id: messageId
        });

        if (sent) {
            console.log('[MESSAGE_SERVICE] Read receipt sent successfully for:', messageId);
            this._log('READ_RECEIPT_SENT', { messageId });
        } else {
            console.log('[MESSAGE_SERVICE] Read receipt FAILED for:', messageId, '- WebSocket not connected, queuing...');
            this._log('READ_RECEIPT_FAILED', { messageId });
            
            // Queue the read receipt for when WebSocket reconnects
            this.queueReadReceipt(messageId);
        }

        return sent;
    }

    /**
     * Queue a read receipt to send later when WebSocket reconnects.
     * Persisted to localStorage so a reload/swap can't lose it.
     * @param {string} messageId - Message ID to queue
     */
    queueReadReceipt(messageId) {
        // Add to read receipt queue (avoid duplicates)
        if (!this.readReceiptQueue) {
            this.readReceiptQueue = new Set();
            try {
                const saved = JSON.parse(localStorage.getItem('pwani_read_receipt_queue') || '[]');
                saved.forEach(id => this.readReceiptQueue.add(String(id)));
            } catch (_) {}
        }
        this.readReceiptQueue.add(String(messageId));
        try {
            localStorage.setItem('pwani_read_receipt_queue', JSON.stringify(Array.from(this.readReceiptQueue)));
        } catch (_) {}
        console.log('[MESSAGE_SERVICE] Queued read receipt for:', messageId, 'Queue size:', this.readReceiptQueue.size);
    }

    /**
     * Send all queued read receipts when WebSocket reconnects.
     * Sends one batch; anything unsent stays queued instead of being lost.
     */
    sendQueuedReadReceipts() {
        if (!this.readReceiptQueue) {
            try {
                const saved = JSON.parse(localStorage.getItem('pwani_read_receipt_queue') || '[]');
                if (saved.length) this.readReceiptQueue = new Set(saved.map(String));
            } catch (_) {}
        }
        if (!this.readReceiptQueue || this.readReceiptQueue.size === 0) {
            return;
        }
        if (!webSocketManager.isConnected()) return;

        console.log('[MESSAGE_SERVICE] Sending', this.readReceiptQueue.size, 'queued read receipts');
        const messageIds = Array.from(this.readReceiptQueue);
        const numeric = messageIds.map(id => parseInt(id, 10)).filter(id => !isNaN(id));
        if (!numeric.length) {
            this.readReceiptQueue.clear();
            try { localStorage.removeItem('pwani_read_receipt_queue'); } catch (_) {}
            return;
        }
        const sent = webSocketManager.send({
            type: 'read_receipt',
            message_id: Math.max(...numeric),
            message_ids: numeric
        });
        if (sent) {
            this.readReceiptQueue.clear();
            try { localStorage.removeItem('pwani_read_receipt_queue'); } catch (_) {}
        }
        // On failure the set is left intact for the next reconnect.
    }

    /**
     * Mark multiple messages as read (batch operation)
     * @param {Array} messageIds - Array of message IDs to mark as read
     */
    markMessagesAsRead(messageIds) {
        if (!Array.isArray(messageIds) || messageIds.length === 0) {
            return;
        }

        const validNumericIds = messageIds
            .map(id => parseInt(id, 10))
            .filter(id => !isNaN(id));

        if (validNumericIds.length === 0) return;

        const maxId = Math.max(...validNumericIds);
        this._log('MARK_MESSAGES_AS_READ', { count: validNumericIds.length, maxId });

        if (webSocketManager.isConnected()) {
            const sent = webSocketManager.send({
                type: 'read_receipt',
                message_id: maxId,
                message_ids: validNumericIds
            });
            if (!sent) validNumericIds.forEach(id => this.queueReadReceipt(id));
        } else {
            // Queue the FULL batch, not just maxId, so no receipt is lost.
            validNumericIds.forEach(id => this.queueReadReceipt(id));
        }
    }

    /**
     * Get service status
     * @returns {Object} Service status
     */
    getStatus() {
        return {
            initialized: this.initialized,
            encryptionEnabled: !!this.e2eEncryption,
            hasRecipientKey: !!this.recipientPublicKey,
            queueSize: this.messageQueue.length
        };
    }

    /**
     * Validate message consistency
     * @returns {Object} Validation results
     */
    validateConsistency() {
        return store.validateMessageConsistency();
    }

    /**
     * Safe wrapper for delivery acknowledgement
     * @param {Object} message - Message object
     */
    _safeSendDeliveryAck(message) {
        try {
            const state = store.getState();
            // Only send ACK for incoming messages (not from current user)
            if (Number(message.senderId) !== Number(state.currentUserId)) {
                webSocketManager.send({ type: 'message_delivered', message_id: message.id });
            }
        } catch (error) {
            console.error('[MESSAGE_SERVICE] Delivery ACK failed:', error);
            // Fail silently - never break primary flow
        }
    }

    /**
     * Refetch history after reconnect to cover messages missed while socket was CLOSED.
     */
    async syncMissedMessages() {
        try {
            const state = store.getState();
            const convId = state.conversationId || state.activeConversationId || webSocketManager.conversationId;
            if (!convId) return;
            const res = await fetch(`/messaging/v1/messages/?conversation=${convId}&limit=20`);
            if (!res.ok) return;
            const data = await res.json();
            const msgs = data.results || data.messages || data;
            if (!Array.isArray(msgs)) return;
            for (const m of msgs) {
                const canonical = this.normalizeServerMessage(m);
                if (!store.getMessageById(String(canonical.id))) {
                    store.addMessage(canonical);
                    this._safeSendDeliveryAck(canonical);
                }
            }
        } catch (e) {
            console.warn('[MESSAGE_SERVICE] syncMissedMessages failed', e);
        }
    }

    /**
     * Safe wrapper for triggering notifications
     * @param {Object} message - Message object
     */
    _safeTriggerNotifications(message) {
        try {
            const state = store.getState();
            // Only trigger notifications for incoming messages (not from current user)
            if (message.senderId !== state.currentUserId) {
                // Emit notification event
                eventBus.emit('notification_received', message);
            }
        } catch (error) {
            console.error('[MESSAGE_SERVICE] Notification trigger failed:', error);
            // Fail silently - never break primary flow
        }
    }

    /**
     * Safe wrapper for playing message sound
     * @param {Object} message - Message object
     */
    _safePlayMessageSound(message) {
        try {
            const state = store.getState();
            // Only play sound for incoming messages (not from current user)
            if (message.senderId !== state.currentUserId) {
                if (messageSoundManager && typeof messageSoundManager.playReceiveSound === 'function') {
                    messageSoundManager.playReceiveSound(message.id);
                }
            }
        } catch (error) {
            console.error('[MESSAGE_SERVICE] Message sound playback failed:', error);
            // Fail silently - never break primary flow
        }
    }

    /**
     * Log debug information
     * @param {string} action - Action type
     * @param {*} data - Action data
     */
    _log(action, data) {
        if (this.debugMode) {
            console.log(`[MESSAGE_SERVICE] ${action}:`, data);
        }
    }

    /**
     * Extract URLs from text
     * @param {string} text - Text to search for URLs
     * @returns {Array} Array of URLs found
     */
    extractUrls(text) {
        const urlPattern = /(https?:\/\/[^\s]+)/g;
        const matches = text.match(urlPattern);
        return matches || [];
    }

    /**
     * Fetch link metadata from backend
     * @param {string} url - URL to fetch metadata for
     * @returns {Object} Link metadata
     */
    async fetchLinkMetadata(url) {
        try {
            const response = await fetch('/messaging/api/links/fetch-metadata/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCSRFToken()
                },
                body: JSON.stringify({ url })
            });

            if (!response.ok) {
                throw new Error('Failed to fetch link metadata');
            }

            const data = await response.json();
            return data;
        } catch (error) {
            console.error('Error fetching link metadata:', error);
            throw error;
        }
    }
}

// Create and export singleton instance
export const messageService = new MessageService();
