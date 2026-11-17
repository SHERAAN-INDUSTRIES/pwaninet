/**
 * Sync Manager for vasto Messaging
 * Handles synchronization between offline cache and server
 */

class SyncManager {
    constructor() {
        this.isSyncing = false;
        this.syncQueue = [];
        this.lastSyncTime = null;
        this.syncInProgress = false;
        
        this.init();
    }

    init() {
        // Listen to network status changes
        if (window.networkStatus) {
            window.networkStatus.addListener(this.handleNetworkChange.bind(this));
        }
        
        console.log('Sync manager initialized');
    }

    handleNetworkChange(event) {
        if (event === 'online') {
            // Start sync when coming back online
            setTimeout(() => {
                this.syncAll();
            }, 1000); // Small delay to ensure connection is stable
        }
    }

    async syncAll() {
        if (this.isSyncing || !window.networkStatus?.isOnline) {
            return;
        }

        this.isSyncing = true;
        console.log('Starting full synchronization...');

        try {
            // Sync outbox messages first
            await this.syncOutbox();
            
            // Sync conversations
            await this.syncConversations();
            
            // Sync messages for active conversations
            await this.syncMessages();
            
            this.lastSyncTime = new Date().toISOString();
            console.log('Synchronization completed successfully');
            
        } catch (error) {
            console.error('Synchronization failed:', error);
        } finally {
            this.isSyncing = false;
        }
    }

    async syncOutbox() {
        if (!window.offlineCache) return;
        
        const outboxMessages = await window.offlineCache.getOutboxMessages();
        
        if (outboxMessages.length === 0) return;
        
        console.log(`Syncing ${outboxMessages.length} outbox messages`);
        
        for (const outboxMessage of outboxMessages) {
            try {
                await this.sendOutboxMessage(outboxMessage);
            } catch (error) {
                console.error('Failed to send outbox message:', outboxMessage.id, error);
                // Continue with other messages
            }
        }
    }

    async sendOutboxMessage(outboxMessage) {
        const { conversationId, content, type = 'text', attachments = [] } = outboxMessage;
        
        // Get CSRF token
        const csrfToken = this.getCSRFToken();
        
        const response = await fetch(`/messaging/v1/conversations/${conversationId}/messages/`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken
            },
            body: JSON.stringify({
                content: content,
                type: type,
                attachments: attachments
            })
        });

        if (!response.ok) {
            throw new Error(`HTTP ${response.status}: ${response.statusText}`);
        }

        const sentMessage = await response.json();
        
        // Remove from outbox and save to messages cache
        await window.offlineCache.removeFromOutbox(outboxMessage.id);
        await window.offlineCache.saveMessage(sentMessage);
        
        // Update UI if message is currently visible
        this.updateMessageInUI(outboxMessage, sentMessage);
        
        console.log('Outbox message sent successfully:', outboxMessage.id);
    }

    updateMessageInUI(outboxMessage, sentMessage) {
        // Find and update the pending message in the UI
        const pendingElements = document.querySelectorAll(`[data-outbox-id="${outboxMessage.id}"]`);
        
        pendingElements.forEach(element => {
            // Remove pending indicators
            element.classList.remove('message-pending');
            element.removeAttribute('data-outbox-id');
            
            // Update message ID if needed
            if (element.dataset.messageId) {
                element.dataset.messageId = sentMessage.id;
            }
            
            // Update timestamp
            const timestampElement = element.querySelector('.message-time');
            if (timestampElement) {
                timestampElement.textContent = this.formatTime(sentMessage.timestamp);
            }
        });
    }

    async syncConversations() {
        if (!window.offlineCache) return;
        
        try {
            const response = await fetch('/messaging/v1/conversations/');
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const conversations = await response.json();
            
            // Save conversations to cache
            for (const conversation of conversations) {
                await window.offlineCache.saveConversation(conversation);
            }
            
            // Update conversation list UI if visible
            this.updateConversationListUI(conversations);
            
        } catch (error) {
            console.error('Failed to sync conversations:', error);
        }
    }

    async syncMessages() {
        if (!window.offlineCache) return;
        
        // Get recent conversations to sync messages for
        const conversations = await window.offlineCache.getConversations(10);
        
        for (const conversation of conversations) {
            try {
                await this.syncConversationMessages(conversation.id);
            } catch (error) {
                console.error(`Failed to sync messages for conversation ${conversation.id}:`, error);
            }
        }
    }

    async syncConversationMessages(conversationId) {
        try {
            // Get latest cached message timestamp
            const cachedMessages = await window.offlineCache.getMessages(conversationId, 1);
            const lastCachedTime = cachedMessages.length > 0 ? cachedMessages[0].timestamp : null;
            
            // Fetch new messages
            const url = lastCachedTime 
                ? `/messaging/v1/conversations/${conversationId}/messages/?after=${lastCachedTime}`
                : `/messaging/v1/conversations/${conversationId}/messages/`;
            
            const response = await fetch(url);
            
            if (!response.ok) {
                throw new Error(`HTTP ${response.status}: ${response.statusText}`);
            }
            
            const messages = await response.json();
            
            if (messages.length > 0) {
                // Save new messages to cache
                await window.offlineCache.saveMessages(messages);
                
                // Update UI if this conversation is currently active
                if (this.isActiveConversation(conversationId)) {
                    this.addMessagesToUI(messages);
                }
            }
            
        } catch (error) {
            console.error(`Failed to sync messages for conversation ${conversationId}:`, error);
        }
    }

    isActiveConversation(conversationId) {
        // Check if this conversation is currently being viewed
        return document.body.dataset.conversationId === String(conversationId);
    }

    addMessagesToUI(messages) {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;
        
        messages.forEach(message => {
            const messageElement = this.createMessageElement(message);
            messagesArea.appendChild(messageElement);
        });
        
        // Scroll to bottom
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    createMessageElement(message) {
        // This should match the existing message rendering logic
        const div = document.createElement('div');
        div.className = `message ${message.sender === window.messagingManager?.currentUserId ? 'sent' : 'received'}`;
        div.dataset.messageId = message.id;
        
        div.innerHTML = `
            <div class="message-bubble ${isSent ? 'sent' : 'received'}">
                <div class="message-text">${this.escapeHtml(message.content)}</div>
            </div>
            <div class="message-meta">
                <span class="timestamp">${this.formatTime(message.timestamp)}</span>
            </div>
        `;
        
        return div;
    }

    updateConversationListUI(conversations) {
        const conversationList = document.querySelector('.conversation-list');
        if (!conversationList) return;
        
        // This would update the conversation list UI
        // Implementation depends on the existing conversation list structure
    }

    async saveToOutbox(message) {
        if (!window.offlineCache) return;
        
        const outboxMessage = {
            conversationId: message.conversationId,
            content: message.content,
            type: message.type || 'text',
            attachments: message.attachments || [],
            timestamp: new Date().toISOString(),
            tempId: message.tempId
        };
        
        const outboxId = await window.offlineCache.saveToOutbox(outboxMessage);
        
        // Show pending message in UI
        this.showPendingMessageInUI({ ...outboxMessage, id: outboxId });
        
        return outboxId;
    }

    showPendingMessageInUI(outboxMessage) {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;
        
        const messageElement = this.createPendingMessageElement(outboxMessage);
        messagesArea.appendChild(messageElement);
        
        // Scroll to bottom
        messagesArea.scrollTop = messagesArea.scrollHeight;
    }

    createPendingMessageElement(outboxMessage) {
        const div = document.createElement('div');
        div.className = 'message sent message-pending';
        div.dataset.outboxId = outboxMessage.id;
        div.dataset.tempId = outboxMessage.tempId;
        
        div.innerHTML = `
            <div class="message-content">
                <div class="message-text">${this.escapeHtml(outboxMessage.content)}</div>
                <div class="message-time">
                    <span class="pending-indicator">
                        <i aria-hidden="true" data-lucide="clock" class="vasto-icon"></i> Sending when connection returns...
                    </span>
                </div>
            </div>
        `;
        
        return div;
    }

    getCSRFToken() {
        const tokenFromInput = document.querySelector('[name="csrfmiddlewaretoken"]');
        const tokenFromMeta = document.querySelector('meta[name="csrf-token"]');
        
        if (tokenFromInput) return tokenFromInput.value;
        if (tokenFromMeta) return tokenFromMeta.content;
        
        const cookieValue = document.cookie
            .split('; ')
            .find(row => row.startsWith('csrftoken='))
            ?.split('=')[1];
        
        return cookieValue || '';
    }

    formatTime(timestamp) {
        const date = new Date(timestamp);
        const now = new Date();
        const diffMs = now - date;
        const diffMins = Math.floor(diffMs / 60000);
        
        if (diffMins < 1) return 'Just now';
        if (diffMins < 60) return `${diffMins}m ago`;
        
        const diffHours = Math.floor(diffMins / 60);
        if (diffHours < 24) return `${diffHours}h ago`;
        
        const diffDays = Math.floor(diffHours / 24);
        if (diffDays < 7) return `${diffDays}d ago`;
        
        return date.toLocaleDateString();
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    getSyncStatus() {
        return {
            isSyncing: this.isSyncing,
            lastSyncTime: this.lastSyncTime,
            queueLength: this.syncQueue.length
        };
    }
}

// Global instance
window.syncManager = new SyncManager();
