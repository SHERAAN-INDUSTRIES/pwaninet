/**
 * ContextMenuService - Handles message context menu (right-click + long-press)
 * Pure service layer for context menu logic
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { store } from '../../core/store.js?v=20261106-group1';

export class ContextMenuService {
  constructor() {
    this.contextMenu = null;
    this.selectedMessageId = null;
    this.selectedMessageElement = null;
    this.longPressTimer = null;
    this.longPressThreshold = 500; // 500ms for long press
    this.isLongPress = false;
    this.isSwiping = false;
    this.touchStartX = 0;
    this.touchStartY = 0;
    this.activeSwipeBubble = null;
    this.isInitialized = false;
  }

  /**
   * Initialize context menu service
   */
  init() {
    console.log('[CONTEXT_MENU_SERVICE] Context menu service initializing...');
    this.contextMenu = document.getElementById('contextMenu');
    
    if (!this.contextMenu) {
      console.error('[CONTEXT_MENU_SERVICE] Context menu element not found');
      return;
    }

    this._setupEventListeners();
    this.isInitialized = true;
    console.log('[CONTEXT_MENU_SERVICE] Context menu service initialized');
  }

  /**
   * Setup event listeners
   */
  _setupEventListeners() {
    // Close menu when clicking outside
    document.addEventListener('click', (e) => this._handleDocumentClick(e));
    
    // Close menu on escape key
    document.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') {
        this.hideContextMenu();
      }
    });

    // Handle context menu item clicks
    const menuItems = this.contextMenu.querySelectorAll('.context-menu-item');
    menuItems.forEach(item => {
      item.addEventListener('click', (e) => {
        e.preventDefault();
        e.stopPropagation();
        const action = item.dataset.action;
        this._handleMenuAction(action);
      });
    });
  }

  /**
   * Attach context menu listeners to message bubbles
   * Call this after messages are rendered
   */
  attachToMessages() {
    console.log('[CONTEXT_MENU_SERVICE] Attaching to messages...');

    // Attach to all unattached message bubbles safely without destructive DOM cloning
    const messageBubbles = document.querySelectorAll('.message-bubble');
    let attachedCount = 0;
    messageBubbles.forEach(bubble => {
      if (bubble.dataset.hasContextMenu === 'true') return;
      this._attachToBubble(bubble);
      bubble.dataset.hasContextMenu = 'true';
      attachedCount++;
    });

    console.log(`[CONTEXT_MENU_SERVICE] Attached to ${attachedCount} new message bubbles (${messageBubbles.length} total)`);
  }

  /**
   * Detach listeners from message bubbles
   */
  _detachFromMessages() {
    // Non-destructive: do NOT clone nodes, as cloning destroys all child event listeners
    // (such as voice note speed buttons, play/pause buttons, waveforms, audio elements, etc.)
    const messageBubbles = document.querySelectorAll('.message-bubble');
    messageBubbles.forEach(bubble => {
      delete bubble.dataset.hasContextMenu;
    });
  }

  /**
   * Attach listeners to a single message bubble
   * @param {HTMLElement} bubble - Message bubble element
   */
  _attachToBubble(bubble) {
    // Desktop: right-click
    bubble.addEventListener('contextmenu', (e) => {
      // Ignore right-clicks on interactive controls
      if (e.target.closest('button, a, input, audio, video, .vn-speed-btn, .vn-play-btn, .vn-waveform')) {
        return;
      }
      e.preventDefault();
      this._handleRightClick(e, bubble);
    });

    // Mobile: long-press detection
    bubble.addEventListener('touchstart', (e) => {
      // Ignore touches on interactive controls (speed buttons, play buttons, waveform scrubbers)
      if (e.target.closest('button, a, input, audio, video, .vn-speed-btn, .vn-play-btn, .vn-waveform')) {
        return;
      }
      this._handleTouchStart(e, bubble);
    }, { passive: true });

    bubble.addEventListener('touchmove', (e) => {
      this._handleTouchMove(e);
    }, { passive: true });

    bubble.addEventListener('touchend', (e) => {
      this._handleTouchEnd(e, bubble);
    });

    // Also handle touchcancel
    bubble.addEventListener('touchcancel', (e) => {
      this._handleTouchEnd(e, bubble);
    });
  }

  /**
   * Handle right-click on message bubble
   * @param {Event} e - Context menu event
   * @param {HTMLElement} bubble - Message bubble element
   */
  _handleRightClick(e, bubble) {
    if (e.target.closest('button, a, input, audio, video, .vn-speed-btn, .vn-play-btn, .vn-waveform')) {
      return;
    }
    console.log('[CONTEXT_MENU_SERVICE] Right-click on message:', bubble.dataset.messageId);
    
    this.selectedMessageId = bubble.dataset.messageId;
    this.selectedMessageElement = bubble;
    
    this.showContextMenu(e.clientX, e.clientY);
  }

  /**
   * Handle touch start (long-press detection)
   * @param {Event} e - Touch event
   * @param {HTMLElement} bubble - Message bubble element
   */
  _handleTouchStart(e, bubble) {
    if (e.target.closest('button, a, input, audio, video, .vn-speed-btn, .vn-play-btn, .vn-waveform')) {
      return;
    }
    this.isLongPress = false;
    this.isSwiping = false;
    this.activeSwipeBubble = bubble;
    this.touchStartX = e.touches[0].clientX;
    this.touchStartY = e.touches[0].clientY;
    
    // Clear existing timer
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer);
    }
    
    // Start long-press timer
    this.longPressTimer = setTimeout(() => {
      this.isLongPress = true;
      this.selectedMessageId = bubble.dataset.messageId;
      this.selectedMessageElement = bubble;
      
      // Use touch position for menu
      this.showContextMenu(this.touchStartX, this.touchStartY);
      
      // Provide crisp tactile haptic feedback
      if (window.Haptics && typeof window.Haptics.impactMedium === 'function') {
        window.Haptics.impactMedium();
      } else if (navigator.vibrate) {
        navigator.vibrate(25);
      }
    }, this.longPressThreshold);
  }

  /**
   * Handle touch move (cancel long-press if moved too much, track swipe-to-reply)
   * @param {Event} e - Touch event
   */
  _handleTouchMove(e) {
    const touch = e.touches[0];
    const deltaX = touch.clientX - this.touchStartX;
    const deltaY = touch.clientY - this.touchStartY;
    
    // Horizontal right swipe on bubble
    if (deltaX > 20 && Math.abs(deltaY) < 30 && this.activeSwipeBubble) {
      if (this.longPressTimer) {
        clearTimeout(this.longPressTimer);
        this.longPressTimer = null;
      }
      this.isSwiping = true;
      const clampedX = Math.min(Math.max(0, deltaX), 65);
      this.activeSwipeBubble.style.transform = `translateX(${clampedX}px)`;
      this.activeSwipeBubble.style.transition = 'none';
    } else if (Math.abs(deltaX) > 10 || Math.abs(deltaY) > 10) {
      // Cancel long-press if moved more than 10px
      if (this.longPressTimer) {
        clearTimeout(this.longPressTimer);
        this.longPressTimer = null;
      }
    }
  }

  /**
   * Handle touch end
   * @param {Event} e - Touch event
   * @param {HTMLElement} bubble - Message bubble element
   */
  _handleTouchEnd(e, bubble) {
    // Clear long-press timer
    if (this.longPressTimer) {
      clearTimeout(this.longPressTimer);
      this.longPressTimer = null;
    }

    if (this.isSwiping && bubble) {
      const changedTouch = e.changedTouches ? e.changedTouches[0] : null;
      const finalDeltaX = changedTouch ? (changedTouch.clientX - this.touchStartX) : 0;
      
      bubble.style.transition = 'transform 0.22s cubic-bezier(0.2, 0.9, 0.3, 1)';
      bubble.style.transform = '';
      
      if (finalDeltaX >= 50) {
        if (window.Haptics && typeof window.Haptics.impactLight === 'function') {
          window.Haptics.impactLight();
        } else if (navigator.vibrate) {
          navigator.vibrate(20);
        }
        eventBus.emit(EVENTS.CONTEXT_MENU_ACTION, {
          action: 'reply',
          messageId: bubble.dataset.messageId,
          messageElement: bubble
        });
      }
      this.isSwiping = false;
    }
    this.activeSwipeBubble = null;
    
    // If it was a long-press, prevent default click behavior
    if (this.isLongPress) {
      if (e.cancelable) {
        e.preventDefault();
      }
      this.isLongPress = false;
    }
  }

  /**
   * Show context menu at position
   * @param {number} x - X coordinate
   * @param {number} y - Y coordinate
   */
  showContextMenu(x, y) {
    if (!this.contextMenu) return;

    console.log('[CONTEXT_MENU_SERVICE] Showing context menu at:', x, y);

    // Dynamic menu item filtering
    const message = store.getMessageById(this.selectedMessageId);
    const currentUserId = store.getState().currentUserId;
    const isOwn = message && (message.isOwn || Number(message.senderId) === Number(currentUserId));
    const isNotDeleted = message && !message.isDeleted;
    const isTextMessage = message && (message.type === 'text' || Boolean(message.content));
    const msgTime = message?.timestamp ? new Date(message.timestamp).getTime() : Date.now();
    const isWithin15Min = (Date.now() - msgTime) <= 15 * 60 * 1000;

    // Edit option: only sender, under 15 min, text/caption, not deleted
    const editItem = this.contextMenu.querySelector('[data-action="edit"]');
    if (editItem) {
      if (isOwn && isNotDeleted && isTextMessage && isWithin15Min) {
        editItem.classList.remove('d-none');
      } else {
        editItem.classList.add('d-none');
      }
    }

    // Copy option: has text and not deleted
    const copyItem = this.contextMenu.querySelector('[data-action="copy"]');
    if (copyItem) {
      const hasText = message && Boolean(message.content && message.content.trim()) && isNotDeleted;
      if (hasText) {
        copyItem.classList.remove('d-none');
      } else {
        copyItem.classList.add('d-none');
      }
    }

    // Forward option: not deleted
    const forwardItem = this.contextMenu.querySelector('[data-action="forward"]');
    if (forwardItem) {
      if (message && isNotDeleted) {
        forwardItem.classList.remove('d-none');
      } else {
        forwardItem.classList.add('d-none');
      }
    }

    // Delete option: not already deleted
    const deleteItem = this.contextMenu.querySelector('[data-action="delete"]');
    if (deleteItem) {
      if (message && message.isDeleted) {
        deleteItem.classList.add('d-none');
      } else {
        deleteItem.classList.remove('d-none');
      }
    }
    
    // Position menu
    this._positionMenu(x, y);
    
    // Show menu
    this.contextMenu.classList.add('show');
    
    // Emit event for other components
    eventBus.emit(EVENTS.CONTEXT_MENU_SHOWN, {
      messageId: this.selectedMessageId,
      x,
      y
    });
  }

  /**
   * Hide context menu
   */
  hideContextMenu() {
    if (!this.contextMenu) return;

    console.log('[CONTEXT_MENU_SERVICE] Hiding context menu');
    
    this.contextMenu.classList.remove('show');
    
    // Clear selection
    this.selectedMessageId = null;
    this.selectedMessageElement = null;
    
    // Emit event for other components
    eventBus.emit(EVENTS.CONTEXT_MENU_HIDDEN);
  }

  /**
   * Position context menu near the click
   * @param {number} x - X coordinate
   * @param {number} y - Y coordinate
   */
  _positionMenu(x, y) {
    const menuWidth = this.contextMenu.offsetWidth;
    const menuHeight = this.contextMenu.offsetHeight;
    const windowWidth = window.innerWidth;
    const windowHeight = window.innerHeight;
    
    // Calculate position (prevent menu from going off-screen)
    let posX = x;
    let posY = y;
    
    // Adjust horizontal position
    if (x + menuWidth > windowWidth) {
      posX = x - menuWidth;
    }
    
    // Adjust vertical position
    if (y + menuHeight > windowHeight) {
      posY = y - menuHeight;
    }
    
    // Add some padding
    const padding = 10;
    posX = Math.max(padding, Math.min(posX, windowWidth - menuWidth - padding));
    posY = Math.max(padding, Math.min(posY, windowHeight - menuHeight - padding));
    
    // Set position
    this.contextMenu.style.left = `${posX}px`;
    this.contextMenu.style.top = `${posY}px`;
  }

  /**
   * Handle click outside menu
   * @param {Event} e - Click event
   */
  _handleDocumentClick(e) {
    // Check if click is outside context menu
    if (this.contextMenu && !this.contextMenu.contains(e.target)) {
      this.hideContextMenu();
    }
  }

  /**
   * Handle context menu item click
   * @param {string} action - Action type (reply, copy, forward, delete)
   */
  _handleMenuAction(action) {
    console.log('[CONTEXT_MENU_SERVICE] Menu action clicked:', action);
    
    if (!this.selectedMessageId) {
      console.error('[CONTEXT_MENU_SERVICE] No message selected');
      return;
    }

    const targetMessageId = this.selectedMessageId;
    const targetElement = this.selectedMessageElement;
    
    // Hide menu
    this.hideContextMenu();
    
    // Emit event for action with preserved ID and element
    eventBus.emit(EVENTS.CONTEXT_MENU_ACTION, {
      action,
      messageId: targetMessageId,
      messageElement: targetElement
    });
  }

  /**
   * Get selected message ID
   * @returns {string|null} Message ID
   */
  getSelectedMessageId() {
    return this.selectedMessageId;
  }

  /**
   * Get selected message element
   * @returns {HTMLElement|null} Message element
   */
  getSelectedMessageElement() {
    return this.selectedMessageElement;
  }

  /**
   * Destroy context menu service
   */
  destroy() {
    console.log('[CONTEXT_MENU_SERVICE] Destroying context menu service');
    this._detachFromMessages();
    this.isInitialized = false;
  }
}

// Create global instance
export const contextMenuService = new ContextMenuService();
