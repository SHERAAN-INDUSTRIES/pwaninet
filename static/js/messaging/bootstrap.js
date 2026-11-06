/**
 * Messaging Bootstrap
 * Single entry point for messaging domain initialization
 * Replaces inline JavaScript in templates
 */

import { themeService } from './services/theme-service.js?v=20261106-group1';
import { emojiService } from './services/emoji-service.js?v=20261106-group1';
import { wallpaperService } from './services/wallpaper-service.js?v=20261106-group1';
import { showNotification } from '../../shared/utils/notification.js?v=20261106-group1';

/**
 * Initialize messaging services
 * @param {number} conversationId - Conversation ID
 */
export function initMessaging(conversationId) {
  console.log('🚀 Initializing messaging services...');

  // Initialize wallpaper service first
  wallpaperService.init(conversationId);
  console.log('✅ Wallpaper service initialized');

  // Initialize theme service
  themeService.init(conversationId);
  console.log('✅ Theme service initialized');

  // Initialize emoji service
  emojiService.init();
  console.log('✅ Emoji service initialized');

  // Button listeners are now handled by UIController to prevent conflicts
  // setupEmojiPickerButton();
  // setupThemeModalButton();
  // setupOverlayHandler();

  console.log('🎉 Messaging services initialized successfully');
}

/**
 * Setup emoji picker button
 */
function setupEmojiPickerButton() {
  const emojiBtn = document.getElementById('emojiBtn');
  const closeEmojiPicker = document.getElementById('closeEmojiPicker');

  if (emojiBtn) {
    const openEmojiPicker = (e) => {
      e.preventDefault();
      e.stopPropagation();
      emojiService.openPicker();
    };

    emojiBtn.addEventListener('click', openEmojiPicker);
    emojiBtn.addEventListener('touchstart', openEmojiPicker, { passive: false });
  }

  if (closeEmojiPicker) {
    closeEmojiPicker.addEventListener('click', () => {
      emojiService.closePicker();
    });
  }
}

/**
 * Setup theme modal button
 */
function setupThemeModalButton() {
  const themeBtn = document.getElementById('themeBtn');
  const closeThemeModal = document.getElementById('closeThemeModal');
  const themeModal = document.getElementById('themeModal');
  const overlay = document.getElementById('overlay');

  if (themeBtn) {
    const openThemeModal = (e) => {
      e.preventDefault();
      e.stopPropagation();
      if (window.chatThemeHandler && typeof window.chatThemeHandler.showThemePanel === 'function') {
        window.chatThemeHandler.showThemePanel();
      } else if (themeModal) {
        themeModal.classList.add('show');
      }
    };

    themeBtn.addEventListener('click', openThemeModal);
    themeBtn.addEventListener('touchstart', openThemeModal, { passive: false });
  }

  if (closeThemeModal) {
    closeThemeModal.addEventListener('click', () => {
      if (themeModal) themeModal.classList.remove('show');
      if (overlay) overlay.classList.remove('show');
    });
  }
}

/**
 * Setup overlay click handler to close all modals
 */
function setupOverlayHandler() {
  const overlay = document.getElementById('overlay');
  const emojiPicker = document.getElementById('emojiPicker');
  const themeModal = document.getElementById('themeModal');
  const attachmentModal = document.getElementById('attachmentModal');

  if (overlay) {
    overlay.addEventListener('click', () => {
      emojiPicker?.classList.remove('show');
      themeModal?.classList.remove('show');
      attachmentModal?.classList.remove('show');
      overlay.classList.remove('show');
    });
  }
}

/**
 * Initialize on DOM ready (for use in templates)
 */
document.addEventListener('DOMContentLoaded', () => {
  const chatContainer = document.querySelector('.chat-container');
  const conversationId = parseInt(
    chatContainer?.dataset.conversationId || 
    document.body.dataset.conversationId
  );

  if (conversationId) {
    initMessaging(conversationId);
  }
});

// Export for global access (for debugging)
window.messagingServices = {
  themeService,
  emojiService,
  wallpaperService,
  initMessaging
};
