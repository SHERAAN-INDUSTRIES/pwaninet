/**
 * Bootstrap - Single entry point with mandatory SOT data flow enforcement
 * Enforces: websocket → message-service → store → ui-controller → renderer
 * Supports HTMX partial swaps and continuous conversation switching without full-page reload
 */

import { appController } from './core/app-controller.js?v=20261106-group1';
import { messageService } from './core/message-service.js?v=20261106-group1';
import { webSocketManager } from './core/websocket.js?v=20261106-group1';
import { store } from './core/store.js?v=20261106-group1';
import { uiController } from './ui/ui-controller.js?v=20261106-group1';
import { attachmentService } from './features/attachments/attachment.service.js?v=20261106-group1';
import { attachmentUI } from './features/attachments/attachment-ui.js?v=20261106-group1';
import { cameraService } from './features/camera/camera.service.js?v=20261106-group1';
import { voiceService } from './features/voice/voice.service.js?v=20261106-group1';
import { voiceModalController } from './features/voice/voice-modal-controller.js?v=20261106-group1';
import { emojiService } from './features/emoji/emoji.service.js?v=20261106-group1';
import { contextMenuService } from './features/context-menu/context-menu.service.js?v=20261106-group1';

// Load encryption module if available
if (typeof E2EEncryption === 'undefined') {
    console.warn('E2EEncryption module not loaded');
}

let _currentInitConvId = null;
let _isInitializing = false;

/**
 * Initialize or re-initialize chat system for the active conversation
 */
export async function initializeChatApp() {
    const chatContainer = document.querySelector('.chat-container');
    const convId = parseInt(chatContainer?.dataset.conversationId || document.body.dataset.conversationId);
    const userId = parseInt(chatContainer?.dataset.userId || document.body.dataset.userId);

    const config = {
        conversationId: convId,
        currentUserId: userId,
        isEncrypted: (chatContainer?.dataset.isEncrypted || document.body.dataset.isEncrypted) === 'true',
        isGroupChat: chatContainer?.dataset.conversationType === 'group'
    };

    // Validate configuration (e.g. skip if on empty state)
    if (!config.conversationId || !config.currentUserId) {
        console.log('No active conversation selected yet or awaiting selection:', config);
        return;
    }

    if (_isInitializing && _currentInitConvId === config.conversationId) {
        console.log('[BOOTSTRAP] Already initializing conversation:', config.conversationId);
        return;
    }

    _isInitializing = true;
    _currentInitConvId = config.conversationId;

    try {
        console.log('🔒 Initializing chat system (SOT) for conversation:', config.conversationId);

    // 1. Store first (SOT) - ONLY mutation source
    store.init(config);

    // 2. Message service (ONLY ingestion layer)
    messageService.init();

    // 3. WebSocket (transport ONLY)
    const wsUrl = null; // All chats use canonical conversation IDs and the same transport.
    webSocketManager.init(config.conversationId, wsUrl);

    // 4. UI controller (read-only consumer)
    uiController.init();

    // 5. Feature services (context menu, voice, emoji, camera, attachment)
    contextMenuService.init();
    attachmentService.init();
    cameraService.init();
    voiceService.init();
    voiceModalController.init();
    emojiService.init();

    // 6. Attachment UI
    attachmentUI.init();

    // 7. App controller (orchestration ONLY)
    if (appController.initialized) {
        appController.initialized = false;
    }
    await appController.init(config);

    // Expose globally for debugging and DOM reconciliation
    window.appController = appController;
    window.uiController = uiController;
    window.renderer = uiController.renderer;
    window.store = store;
    window.messageService = messageService;
    window.webSocketManager = webSocketManager;

    // Load theme for this conversation
    if (window.chatThemeHandler && typeof window.chatThemeHandler.reinit === 'function') {
        window.chatThemeHandler.reinit();
    }

        console.log('🎉 SOT architecture ready for conversation:', config.conversationId);
    } finally {
        _isInitializing = false;
    }
}

// Expose globally on window
window.initializeChatApp = initializeChatApp;

// Initialize on DOM ready or immediately if already loaded
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => {
        initializeChatApp();
    });
} else {
    initializeChatApp();
}

// Re-initialize seamlessly on HTMX chat swaps
document.addEventListener('htmx:afterSwap', (evt) => {
    const target = evt.detail.target;
    if (target && (target.id === 'chatMainAreaWrapper' || target.closest('#chatMainAreaWrapper'))) {
        console.log('[HTMX] Chat swapped, re-initializing chat app...');
        initializeChatApp();
    }
});
