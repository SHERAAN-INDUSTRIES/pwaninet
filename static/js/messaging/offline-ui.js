/**
 * Offline UI Components for vasto Messaging
 * Handles UI elements for offline messaging experience
 */

class OfflineUI {
    constructor() {
        this.pendingMessageIndicators = new Map();
        this.offlineMessageElements = new Set();
        
        this.init();
    }

    init() {
        // Listen to network status changes
        if (window.networkStatus) {
            window.networkStatus.addListener(this.handleNetworkChange.bind(this));
        }
        
        // Add offline styles
        this.addOfflineStyles();
        
        console.log('Offline UI initialized');
    }

    handleNetworkChange(event, data) {
        if (event === 'offline') {
            this.showOfflineIndicators();
        } else if (event === 'online') {
            this.hideOfflineIndicators();
        }
    }

    addOfflineStyles() {
        const style = document.createElement('style');
        style.textContent = `
            /* Pending message styles */
            .message-pending {
                opacity: 0.7;
                position: relative;
            }
            
            .message-pending::after {
                content: '';
                position: absolute;
                top: 0;
                left: 0;
                right: 0;
                bottom: 0;
                background: rgba(255, 255, 255, 0.1);
                pointer-events: none;
            }
            
            .pending-indicator {
                display: flex;
                align-items: center;
                gap: 4px;
                font-size: 11px;
                opacity: 0.7;
                font-style: italic;
            }
            
            .pending-indicator i {
                font-size: 10px;
            }
            
            /* Offline conversation list indicators */
            .conversation-offline-indicator {
                display: inline-flex;
                align-items: center;
                padding: 2px 6px;
                background: rgba(245, 158, 11, 0.1);
                color: #d97706;
                border-radius: 10px;
                font-size: 10px;
                font-weight: 500;
                margin-left: 8px;
            }
            
            [data-theme="dark"] .conversation-offline-indicator {
                background: rgba(245, 158, 11, 0.2);
                color: #f59e0b;
            }
            
            /* Offline input area */
            .offline-input-indicator {
                display: flex;
                align-items: center;
                gap: 8px;
                padding: 8px 12px;
                background: rgba(245, 158, 11, 0.1);
                border-radius: 8px;
                margin-bottom: 8px;
                font-size: 13px;
                color: #d97706;
            }
            
            [data-theme="dark"] .offline-input-indicator {
                background: rgba(245, 158, 11, 0.2);
                color: #f59e0b;
            }
            
            .offline-input-indicator i {
                font-size: 14px;
            }
            
            /* Message sending animation */
            @keyframes messageSending {
                0% { opacity: 0.7; }
                50% { opacity: 0.4; }
                100% { opacity: 0.7; }
            }
            
            .message-sending {
                animation: messageSending 1.5s ease-in-out infinite;
            }
            
            /* Sync status indicator */
            .sync-status {
                position: fixed;
                bottom: 20px;
                right: 20px;
                background: rgba(0, 0, 0, 0.8);
                color: white;
                padding: 8px 12px;
                border-radius: 20px;
                font-size: 12px;
                z-index: 1200;
                display: none;
                align-items: center;
                gap: 8px;
            }
            
            .sync-status.active {
                display: flex;
            }
            
            .sync-status i {
                font-size: 14px;
            }
            
            .sync-status.syncing i {
                animation: spin 1s linear infinite;
            }
            
            @keyframes spin {
                from { transform: rotate(0deg); }
                to { transform: rotate(360deg); }
            }
            
            /* Offline message list */
            .offline-message-list {
                padding: 16px;
                text-align: center;
                color: var(--text-secondary);
                font-size: 14px;
            }
            
            .offline-message-list i {
                font-size: 24px;
                margin-bottom: 8px;
                display: block;
                opacity: 0.5;
            }
            
            /* Enhanced offline banner variations */
            .offline-banner.expanded {
                height: auto;
                padding: 16px;
            }
            
            .offline-banner-details {
                margin-top: 8px;
                font-size: 12px;
                opacity: 0.9;
            }
            
            /* Mobile optimizations */
            @media (max-width: 768px) {
                .sync-status {
                    bottom: 16px;
                    right: 16px;
                    font-size: 11px;
                    padding: 6px 10px;
                }
                
                .offline-input-indicator {
                    font-size: 12px;
                    padding: 6px 10px;
                }
                
                .pending-indicator {
                    font-size: 10px;
                }
            }
        `;
        
        document.head.appendChild(style);
    }

    showOfflineIndicators() {
        // Add offline indicators to conversation list
        this.addConversationOfflineIndicators();
        
        // Show offline input indicator
        this.showOfflineInputIndicator();
        
        // Update send button states
        this.updateSendButtonStates();
    }

    hideOfflineIndicators() {
        // Remove offline indicators from conversation list
        this.removeConversationOfflineIndicators();
        
        // Hide offline input indicator
        this.hideOfflineInputIndicator();
        
        // Update send button states
        this.updateSendButtonStates();
    }

    addConversationOfflineIndicators() {
        const conversationItems = document.querySelectorAll('.conversation-item');
        
        conversationItems.forEach(item => {
            if (!item.querySelector('.conversation-offline-indicator')) {
                const indicator = document.createElement('span');
                indicator.className = 'conversation-offline-indicator';
                indicator.innerHTML = '<i aria-hidden="true" data-lucide="wifi-off" class="vasto-icon"></i> Offline';
                item.appendChild(indicator);
            }
        });
    }

    removeConversationOfflineIndicators() {
        const indicators = document.querySelectorAll('.conversation-offline-indicator');
        indicators.forEach(indicator => indicator.remove());
    }

    showOfflineInputIndicator() {
        const messageInput = document.querySelector('.message-input-container');
        if (!messageInput) return;
        
        if (!messageInput.querySelector('.offline-input-indicator')) {
            const indicator = document.createElement('div');
            indicator.className = 'offline-input-indicator';
            indicator.innerHTML = '<i aria-hidden="true" data-lucide="wifi-off" class="vasto-icon"></i> Messages will be sent when connection returns';
            
            // Insert before the input area
            const inputArea = messageInput.querySelector('.message-input-area');
            if (inputArea) {
                messageInput.insertBefore(indicator, inputArea);
            } else {
                messageInput.appendChild(indicator);
            }
        }
    }

    hideOfflineInputIndicator() {
        const indicator = document.querySelector('.offline-input-indicator');
        if (indicator) {
            indicator.remove();
        }
    }

    updateSendButtonStates() {
        const sendButtons = document.querySelectorAll('.send-message-btn');
        const isOffline = !window.networkStatus?.isOnline;
        
        sendButtons.forEach(button => {
            if (isOffline) {
                button.title = 'Send when connection returns';
                button.classList.add('offline-send');
            } else {
                button.title = 'Send message';
                button.classList.remove('offline-send');
            }
        });
    }

    showPendingMessage(messageData) {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;
        
        const messageElement = this.createPendingMessageElement(messageData);
        messagesArea.appendChild(messageElement);
        
        // Scroll to bottom
        messagesArea.scrollTop = messagesArea.scrollHeight;
        
        // Track for later updates
        this.offlineMessageElements.add(messageElement);
        
        return messageElement;
    }

    createPendingMessageElement(messageData) {
        const div = document.createElement('div');
        div.className = 'message sent message-pending';
        div.dataset.outboxId = messageData.id;
        div.dataset.tempId = messageData.tempId;
        
        div.innerHTML = `
            <div class="message-bubble sent">
                <div class="message-text">${this.escapeHtml(messageData.content)}</div>
            </div>
            <div class="message-meta">
                <span class="pending-indicator">
                    <i aria-hidden="true" data-lucide="clock" class="vasto-icon"></i> Sending when connection returns...
                </span>
            </div>
        `;
        
        return div;
    }

    updatePendingMessageToSent(outboxId, sentMessage) {
        const pendingElement = document.querySelector(`[data-outbox-id="${outboxId}"]`);
        if (!pendingElement) return;
        
        // Remove pending styling
        pendingElement.classList.remove('message-pending', 'message-sending');
        pendingElement.removeAttribute('data-outbox-id');
        
        // Update message ID
        pendingElement.dataset.messageId = sentMessage.id;
        
        // Update timestamp
        const timeElement = pendingElement.querySelector('.message-time');
        if (timeElement) {
            timeElement.innerHTML = this.formatTime(sentMessage.timestamp);
        }
        
        // Add success animation
        pendingElement.style.animation = 'none';
        setTimeout(() => {
            pendingElement.style.animation = 'messageSent 0.3s ease';
        }, 10);
        
        // Remove from tracking
        this.offlineMessageElements.delete(pendingElement);
    }

    markMessageAsSending(outboxId) {
        const pendingElement = document.querySelector(`[data-outbox-id="${outboxId}"]`);
        if (pendingElement) {
            pendingElement.classList.add('message-sending');
            
            const timeElement = pendingElement.querySelector('.message-time .pending-indicator');
            if (timeElement) {
                timeElement.innerHTML = '<i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon"></i> Sending...';
            }
        }
    }

    showSyncStatus(status = 'syncing') {
        let syncStatus = document.querySelector('.sync-status');
        
        if (!syncStatus) {
            syncStatus = this.createSyncStatusElement();
            document.body.appendChild(syncStatus);
        }
        
        syncStatus.className = `sync-status active ${status}`;
        
        if (status === 'syncing') {
            syncStatus.innerHTML = '<i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon"></i> Syncing...';
        } else if (status === 'success') {
            syncStatus.innerHTML = '<i aria-hidden="true" data-lucide="circle-check" class="vasto-icon"></i> Synced';
            setTimeout(() => {
                syncStatus.classList.remove('active');
            }, 3000);
        } else if (status === 'error') {
            syncStatus.innerHTML = '<i aria-hidden="true" data-lucide="triangle-alert" class="vasto-icon"></i> Sync failed';
            setTimeout(() => {
                syncStatus.classList.remove('active');
            }, 5000);
        }
    }

    createSyncStatusElement() {
        const div = document.createElement('div');
        div.className = 'sync-status';
        return div;
    }

    showOfflineMessageList() {
        const messagesArea = document.querySelector('.messages-area');
        if (!messagesArea) return;
        
        // Clear existing messages
        messagesArea.innerHTML = '';
        
        // Add offline message
        const offlineMessage = document.createElement('div');
        offlineMessage.className = 'offline-message-list';
        offlineMessage.innerHTML = `
            <i aria-hidden="true" data-lucide="wifi-off" class="vasto-icon"></i>
            <div>You're offline. Showing saved messages.</div>
            <div style="font-size: 12px; margin-top: 4px; opacity: 0.7;">
                Messages will sync when connection returns
            </div>
        `;
        
        messagesArea.appendChild(offlineMessage);
    }

    addMessageSendingAnimation(element) {
        element.classList.add('message-sending');
        
        setTimeout(() => {
            element.classList.remove('message-sending');
        }, 2000);
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

    // Enhanced offline banner interactions
    expandOfflineBanner() {
        const banner = document.getElementById('offline-banner');
        if (banner && !banner.classList.contains('expanded')) {
            banner.classList.add('expanded');
            
            const content = banner.querySelector('.offline-banner-content');
            const details = document.createElement('div');
            details.className = 'offline-banner-details';
            details.innerHTML = `
                <div>• You can still read previous messages</div>
                <div>• New messages will be queued to send</div>
                <div>• Everything will sync when connection returns</div>
            `;
            
            content.appendChild(details);
        }
    }

    collapseOfflineBanner() {
        const banner = document.getElementById('offline-banner');
        if (banner && banner.classList.contains('expanded')) {
            banner.classList.remove('expanded');
            
            const details = banner.querySelector('.offline-banner-details');
            if (details) {
                details.remove();
            }
        }
    }
}

// Global instance
window.offlineUI = new OfflineUI();
