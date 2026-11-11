/**
 * UIController - Read-only subscriber to store
 * Subscribes to store changes only, NEVER writes or mutates state
 * Coordinates UI rendering based on store state
 */

import { store } from '../core/store.js?v=20261106-group1';
import { messageService } from '../core/message-service.js?v=20261106-group1';
import { MessageRenderer } from './renderer.js?v=20261106-group1';
import { contextMenuService } from '../features/context-menu/context-menu.service.js?v=20261106-group1';
import { messageSoundManager } from '../shared/message-sound.js?v=20261106-group1';
import { eventBus } from '../core/event-bus.js?v=20261106-group1';
import { EVENTS } from '../shared/constants.js?v=20261106-group1';
import { escapeHtml, getCSRFToken } from '../shared/utils.js?v=20261106-group1';

export class UIController {
    constructor() {
        this.renderer = null;
        this.unsubscribe = null;
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
        this.lastState = null;
        this.audioInitialized = false;
        this.pendingDeleteMessageId = null;
        this.pendingForwardMessageId = null;
    }

    /**
     * Initialize UI controller (read-only consumer)
     */
    init() {
        this._log('UI_CONTROLLER_INIT');

        if (this.unsubscribe) {
            try { this.unsubscribe(); } catch (_) {}
            this.unsubscribe = null;
        }
        this.lastState = null;

        // Get state FIRST
        const initialState = store.getState();
        this.currentUserId = initialState.currentUserId;

        // Initialize renderer with valid currentUserId
        this.renderer = new MessageRenderer();
        this.renderer.init(this.currentUserId);
        window.uiController = this;
        window.renderer = this.renderer;
        window.__store = store;

        // Setup read receipt observer
        this._setupReadReceiptObserver();

        // Subscribe to store changes (read-only consumer)
        this.unsubscribe = store.subscribe((state) => {
            this._handleStateChange(state);
        });

        // Setup UI event handlers (user interactions only)
        this._setupUIEventHandlers();

        // Initial render
        this._handleStateChange(initialState);

        this._log('UI_CONTROLLER_INITIALIZED');
        console.log("STATE UPDATE:", initialState.messages.length);
    }

    /**
     * Handle state change from store (read-only)
     * @param {Object} state - Current state from store
     */
    _handleStateChange(state) {
        if (!state) return;

        const last = this.lastState;

        this._log('STATE_CHANGE_RECEIVED', {
            messagesCount: state.messages.length,
            connectionState: state.connectionState,
            typingUsers: state.typingUsers.size,
            peerOnlineStatus: state.peerOnlineStatus.size
        });

        // 1. Connection status change -> update connection status UI only
        if (!last || state.connectionState !== last.connectionState) {
            this._updateConnectionStatus(state.connectionState);
        }

        // 2. Typing indicators / Peer online status / Recording change -> update header status & typing indicator only (NEVER re-render messages!)
        const typingListStr = Array.from(state.typingUsers || []).join(',');
        const typingChanged = !last || (state.typingUsers.size !== last.typingUsersSize) || (typingListStr !== last.typingUsersList);
        const recordingChanged = !last || ((state.recordingUsers?.size || 0) !== (last.recordingUsersSize || 0));
        const peerChecksum = this._computePeerOnlineStatusChecksum(state.peerOnlineStatus);
        const peerChanged = !last || (state.peerOnlineStatus.size !== last.peerOnlineStatusSize) || (peerChecksum !== (last.peerOnlineStatusChecksum || ''));

        if (typingChanged || peerChanged || recordingChanged) {
            this._updateTypingIndicators(state.typingUsers, state.peerOnlineStatus);
        }

        // 3. UI State (loading, error, etc.)
        if (!last || state.uiState !== last.uiState) {
            this._updateUIState(state.uiState);
        }

        // 4. Theme
        if (state.currentTheme && (!last || JSON.stringify(state.currentTheme) !== JSON.stringify(last.currentTheme))) {
            this._applyTheme(state.currentTheme, state.themeMode);
        }

        // 5. Messages: added, removed, status-changed, edited, deleted, or initial load
        const currentMessages = state.messages || [];
        const prevLength = last?.messagesLength ?? -1;
        const newChecksum = currentMessages.length > 0 ? this._computeMessagesChecksum(currentMessages) : null;
        const prevChecksum = last?.messagesChecksum ?? null;
        const newContentChecksum = currentMessages.length > 0 ? this._computeContentChecksum(currentMessages) : null;
        const prevContentChecksum = last?.messagesContentChecksum ?? null;

        const countChanged = currentMessages.length !== prevLength;
        const contentChanged = newContentChecksum !== prevContentChecksum;
        const checksumChanged = newChecksum !== prevChecksum;

        if (countChanged || contentChanged) {
            // New message added, removed, edited, deleted, or initial load -> Reconcile messages in DOM smoothly
            this.renderer.render(currentMessages);

            setTimeout(() => {
                contextMenuService.attachToMessages();
            }, 50);

            setTimeout(() => {
                this._observeReceivedMessages();
            }, 100);
        } else if (checksumChanged) {
            // ONLY status changed (sending -> sent -> delivered -> read)
            // Update message status checkmarks / read receipt avatars in-place WITHOUT touching audio or video elements!
            this.renderer.updateMessageStatuses(currentMessages);

            setTimeout(() => {
                this._observeReceivedMessages();
            }, 100);
        }

        this.lastState = this._createStateSnapshot(state);
    }

    /**
     * Setup UI event handlers (user interactions only)
     */
    _setupUIEventHandlers() {
        this._log('SETUP_UI_EVENT_HANDLERS');

        // Message retry handler
        window.addEventListener('messageRetry', (event) => {
            this._log('MESSAGE_RETRY_EVENT', event.detail);
            this._handleMessageRetry(event.detail);
        });

        // Message input handler
        const messageInput = document.getElementById('messageInput');
        const sendButton = document.getElementById('sendBtn');

        if (messageInput) {
            // Unlock audio context on first user interaction (browser autoplay policy)
            const unlockAudioOnInteraction = () => {
                if (!this.audioInitialized) {
                    messageSoundManager.unlock().catch(err => {
                        console.warn('[UI_CONTROLLER] Failed to unlock audio:', err);
                    });
                    this.audioInitialized = true;
                }
            };

            // Keyboard events
            messageInput.addEventListener('keypress', (e) => {
                unlockAudioOnInteraction();
                if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault();
                    this._handleSendMessage(messageInput.value);
                }
                this._handleTyping();
            });

            messageInput.addEventListener('input', () => {
                unlockAudioOnInteraction();
                // Auto-expand textarea (pure UI behavior)
                messageInput.style.height = 'auto';
                const newHeight = Math.min(messageInput.scrollHeight, 120);
                messageInput.style.height = `${newHeight}px`;

                // Toggle voice/send button icon (pure UI behavior)
                this._toggleVoiceSendButton(messageInput.value);

                // Send typing indicator
                this._handleTyping();
            });

            // Touch events for mobile devices
            messageInput.addEventListener('touchstart', () => {
                unlockAudioOnInteraction();
                this._handleTyping();
            });

            messageInput.addEventListener('focus', () => {
                unlockAudioOnInteraction();
                this._handleTyping();
            });
        }
        
        if (sendButton) {
            sendButton.addEventListener('click', () => {
                this._handleSendClick(messageInput);
            });
        }

        // Combined Voice / Send button logic
        const voiceBtn = document.getElementById('voiceBtn');
        if (voiceBtn) {
            voiceBtn.addEventListener('click', (e) => {
                const isSendMode = voiceBtn.classList.contains('send-mode');
                if (isSendMode) {
                    // Sending clears the input and switches this same button back
                    // to a microphone. Consume this tap before voice handlers see it.
                    e.preventDefault();
                    e.stopImmediatePropagation();
                    this._handleSendClick(messageInput);
                }
            }, { capture: true });
        }

        // Reply & Edit cancel button handlers
        const cancelReplyBtn = document.getElementById('cancelReplyBtn');
        if (cancelReplyBtn) {
            cancelReplyBtn.addEventListener('click', () => {
                this._clearReplyContext();
                this._refreshComposerButton();
            });
        }

        const cancelEditBtn = document.getElementById('cancelEditBtn');
        if (cancelEditBtn) {
            cancelEditBtn.addEventListener('click', () => {
                this.editingMessageId = null;
                const bar = document.getElementById('editPreviewBar');
                if (bar) bar.classList.add('d-none');
                const messageInput = document.getElementById('messageInput');
                if (messageInput) {
                    messageInput.value = '';
                    messageInput.style.height = 'auto';
                }
                // Restore mic button now that the composer is empty
                this._refreshComposerButton();
            });
        }

        // Context menu action handler (Reply, Edit, Copy, Delete, Forward)
        eventBus.on(EVENTS.CONTEXT_MENU_ACTION, (detail) => {
            this._handleContextMenuAction(detail);
        });

        // Delete Confirmation Modal button bindings
        const deleteForEveryoneBtn = document.getElementById('deleteForEveryoneBtn');
        if (deleteForEveryoneBtn) {
            deleteForEveryoneBtn.addEventListener('click', () => {
                const messageId = this.pendingDeleteMessageId;
                if (!messageId) return;

                import('../core/websocket.js?v=20261106-group1').then(({ webSocketManager }) => {
                    webSocketManager.send({
                        type: 'delete_message',
                        message_id: parseInt(messageId, 10)
                    });
                });
                store.updateMessage(messageId, {
                    isDeleted: true,
                    content: 'This message was deleted'
                });
                if (window.offlineCache && typeof window.offlineCache.saveMessages === 'function') {
                    const existing = store.getMessageById(messageId);
                    window.offlineCache.saveMessages([{
                        ...(existing || {}),
                        id: messageId,
                        is_deleted: true,
                        content: 'This message was deleted'
                    }]).catch(() => {});
                }

                const modalEl = document.getElementById('deleteConfirmModal');
                if (window.bootstrap?.Modal) {
                    window.bootstrap.Modal.getInstance(modalEl)?.hide();
                } else if (modalEl) {
                    modalEl.classList.remove('show');
                    modalEl.style.display = 'none';
                }
                this._showToast('Message deleted for everyone');
                this.pendingDeleteMessageId = null;
            });
        }

        const deleteForMeBtn = document.getElementById('deleteForMeBtn');
        if (deleteForMeBtn) {
            deleteForMeBtn.addEventListener('click', () => {
                const messageId = this.pendingDeleteMessageId;
                if (!messageId) return;

                const convId = store.getState().conversationId;
                if (convId) {
                    try {
                        const key = `deleted_for_me_${convId}`;
                        const raw = localStorage.getItem(key);
                        const list = raw ? JSON.parse(raw) : [];
                        if (!list.includes(String(messageId))) {
                            list.push(String(messageId));
                            localStorage.setItem(key, JSON.stringify(list));
                        }
                    } catch (_) {}
                }
                store.removeMessage(messageId);

                const modalEl = document.getElementById('deleteConfirmModal');
                if (window.bootstrap?.Modal) {
                    window.bootstrap.Modal.getInstance(modalEl)?.hide();
                } else if (modalEl) {
                    modalEl.classList.remove('show');
                    modalEl.style.display = 'none';
                }
                this._showToast('Message deleted for you');
                this.pendingDeleteMessageId = null;
            });
        }

        // Forward Message Modal bindings
        const forwardSearchInput = document.getElementById('forwardSearchInput');
        if (forwardSearchInput) {
            forwardSearchInput.addEventListener('input', () => {
                const query = forwardSearchInput.value.toLowerCase().trim();
                const items = document.querySelectorAll('#forwardConversationsList .forward-chat-item');
                items.forEach(item => {
                    const name = item.getAttribute('data-chat-name') || '';
                    item.style.display = name.includes(query) ? 'flex' : 'none';
                });
            });
        }

        const sendForwardBtn = document.getElementById('sendForwardBtn');
        if (sendForwardBtn) {
            sendForwardBtn.addEventListener('click', () => {
                const messageId = this.pendingForwardMessageId;
                if (!messageId) return;

                const checked = document.querySelectorAll('#forwardConversationsList .forward-checkbox:checked');
                const targetConvIds = Array.from(checked).map(cb => parseInt(cb.value, 10)).filter(Boolean);
                if (targetConvIds.length === 0) return;

                import('../core/websocket.js?v=20261106-group1').then(({ webSocketManager }) => {
                    webSocketManager.send({
                        type: 'forward_message',
                        message_id: parseInt(messageId, 10),
                        conversation_ids: targetConvIds
                    });
                });

                const modalEl = document.getElementById('forwardModal');
                if (window.bootstrap?.Modal) {
                    window.bootstrap.Modal.getInstance(modalEl)?.hide();
                } else if (modalEl) {
                    modalEl.classList.remove('show');
                    modalEl.style.display = 'none';
                }
                this._showToast(`Forwarded to ${targetConvIds.length} chat${targetConvIds.length > 1 ? 's' : ''}`);
                this.pendingForwardMessageId = null;
            });
        }

        // History pagination and quote tap-to-scroll
        const messagesContainer = document.getElementById('messagesContainer');
        if (messagesContainer) {
            // Tap-to-scroll on quoted reply box
            messagesContainer.addEventListener('click', (e) => {
                const quoteBox = e.target.closest('.quoted-reply-box');
                if (quoteBox) {
                    e.preventDefault();
                    e.stopPropagation();
                    const replyId = quoteBox.getAttribute('data-reply-id');
                    if (replyId) {
                        const targetEl = document.querySelector(`.message-wrapper[data-message-id="${replyId}"] .message-bubble, .message-bubble[data-message-id="${replyId}"]`);
                        if (targetEl) {
                            if (this.renderer) {
                                this.renderer.isGroundedToBottom = false;
                                this.renderer.isUserScrolledUp = true;
                            }
                            targetEl.scrollIntoView({ behavior: 'smooth', block: 'center' });
                            targetEl.classList.remove('highlight-flash');
                            void targetEl.offsetWidth;
                            targetEl.classList.add('highlight-flash');
                            setTimeout(() => targetEl.classList.remove('highlight-flash'), 1800);
                        } else {
                            this._showToast('Original message is earlier in history');
                        }
                    }
                }
            });

            let scrollDebounceTimer = null;
            messagesContainer.addEventListener('scroll', () => {
                if (this.renderer && typeof this.renderer.updateScrollToBottomButton === 'function') {
                    this.renderer.updateScrollToBottomButton();
                }
                // ONLY trigger loading older messages if the user has willingly chosen to scroll up to top!
                if (this.renderer && this.renderer.isUserScrolledUp && messagesContainer.scrollTop <= 80) {
                    if (scrollDebounceTimer) return;
                    scrollDebounceTimer = setTimeout(async () => {
                        scrollDebounceTimer = null;
                        if (store.hasMoreOlderMessages()) {
                            const state = store.getState();
                            if (state.conversationId && state.isInitialHistoryLoaded) {
                                this.renderer.setPreserveScrollOnPrepend(true);
                                const loaded = await messageService.loadOlderMessages(state.conversationId);
                                if (loaded === 0) {
                                    this.renderer.setPreserveScrollOnPrepend(false);
                                }
                            }
                        }
                    }, 150);
                }
            }, { passive: true });
        }

        // Floating scroll to bottom button tap
        document.addEventListener('click', (e) => {
            const scrollBtn = e.target.closest('#chatScrollToBottomBtn');
            if (scrollBtn) {
                e.preventDefault();
                e.stopPropagation();
                if (this.renderer) {
                    this.renderer.isGroundedToBottom = true;
                    this.renderer.isUserScrolledUp = false;
                    this.renderer._scrollToBottom(true);
                } else {
                    const container = document.getElementById('messagesContainer');
                    if (container) {
                        container.scrollTo({ top: container.scrollHeight, behavior: 'smooth' });
                    }
                }
                scrollBtn.classList.add('d-none');
                const badge = document.getElementById('chatScrollToBottomBadge');
                if (badge) {
                    badge.textContent = '0';
                    badge.classList.add('d-none');
                }
                if (this.renderer) {
                    this.renderer.unreadScrolledCount = 0;
                }
            }
        });

        const themeBtn = document.getElementById('themeBtn');
        if (themeBtn) {
            themeBtn.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                eventBus.emit(EVENTS.THEME_SELECTOR_SHOW);
                
                if (window.chatThemeHandler && typeof window.chatThemeHandler.showThemePanel === 'function') {
                    window.chatThemeHandler.showThemePanel();
                } else {
                    const themeModal = document.getElementById('themeModal');
                    if (themeModal) themeModal.classList.add('show');
                }
            });
        }

        const cameraBtn = document.getElementById('cameraBtn');
        if (cameraBtn) {
            cameraBtn.addEventListener('click', () => {
                const cameraView = document.getElementById('cameraView');
                if (cameraView) cameraView.classList.add('show');
            });
        }
    }

    _showToast(text) {
        const toast = document.createElement('div');
        toast.className = 'position-fixed bottom-0 start-50 translate-middle-x bg-dark text-white px-3 py-2 rounded-pill shadow';
        toast.style.zIndex = '9999';
        toast.style.marginBottom = '84px';
        toast.style.fontSize = '0.85rem';
        toast.style.fontWeight = '500';
        toast.style.letterSpacing = '0.2px';
        toast.style.boxShadow = '0 4px 14px rgba(0,0,0,0.3)';
        toast.textContent = text;
        document.body.appendChild(toast);
        setTimeout(() => toast.remove(), 2200);
    }

    _handleContextMenuAction(detail) {
        const { action, messageId } = detail;
        const message = store.getMessageById(messageId);
        if (!message) return;

        if (action === 'reply') {
            const wasEditing = Boolean(this.editingMessageId);
            this.replyToMessageId = messageId;
            this.editingMessageId = null;
            const editBar = document.getElementById('editPreviewBar');
            if (editBar) editBar.classList.add('d-none');

            const replyBar = document.getElementById('replyPreviewBar');
            const replySender = document.getElementById('replySenderName');
            const replyText = document.getElementById('replyTextSnippet');
            if (replyBar && replySender && replyText) {
                const currentUserId = store.getState().currentUserId;
                const isOwn = message.isOwn || Number(message.senderId) === Number(currentUserId);
                replySender.textContent = isOwn ? 'Replying to yourself' : 'Replying to message';

                let snippet = message.content;
                if (!snippet) {
                    const t = message.type || message.metadata?.type;
                    if (t === 'voice_note' || message.is_voice_note || message.metadata?.is_voice_note) snippet = '🎙️ Voice note';
                    else if (t === 'audio') snippet = '🎵 Audio track';
                    else if (t === 'media' || t === 'image') snippet = '📷 Photo';
                    else if (t === 'video') snippet = '🎥 Video';
                    else if (t === 'document') snippet = '📄 Document';
                    else snippet = 'Attachment';
                }
                replyText.textContent = snippet;
                replyBar.classList.remove('d-none');
            }
            document.getElementById('messaging-composer-form')?.classList.add('has-reply-context');

            const input = document.getElementById('messageInput');
            if (input) {
                // Leftover text from a cancelled edit would keep the button in send mode
                if (wasEditing) {
                    input.value = '';
                    input.style.height = 'auto';
                }
                input.focus();
            }
            // Keep the mic available while replying (voice replies) unless text is typed
            this._refreshComposerButton();

        } else if (action === 'edit') {
            const currentUserId = store.getState().currentUserId;
            const isOwn = message.isOwn || Number(message.senderId) === Number(currentUserId);
            if (!isOwn) {
                this._showToast('You can only edit your own messages.');
                return;
            }

            const msgTime = message?.timestamp ? new Date(message.timestamp).getTime() : Date.now();
            if ((Date.now() - msgTime) > 15 * 60 * 1000) {
                this._showToast('Messages can only be edited within 15 minutes of sending.');
                return;
            }

            this.editingMessageId = messageId;
            this._clearReplyContext();

            const editBar = document.getElementById('editPreviewBar');
            const editText = document.getElementById('editTextSnippet');
            if (editBar && editText) {
                editText.textContent = message.content || '';
                editBar.classList.remove('d-none');
            }
            const input = document.getElementById('messageInput');
            if (input) {
                input.value = message.content || '';
                input.focus();
                this._toggleVoiceSendButton(input.value);
            }

        } else if (action === 'copy') {
            const copyText = message.content || message.metadata?.global_caption || message.global_caption;
            if (copyText) {
                navigator.clipboard.writeText(copyText).then(() => {
                    this._showToast('Message copied to clipboard');
                }).catch(() => {
                    this._showToast('Failed to copy');
                });
            } else {
                this._showToast('No text to copy');
            }

        } else if (action === 'delete') {
            this.pendingDeleteMessageId = messageId;
            const modalEl = document.getElementById('deleteConfirmModal');
            if (modalEl) {
                const currentUserId = store.getState().currentUserId;
                const isOwn = message.isOwn || Number(message.senderId) === Number(currentUserId);
                const msgTime = message?.timestamp ? new Date(message.timestamp).getTime() : Date.now();
                const isWithin48Hours = (Date.now() - msgTime) <= 48 * 60 * 60 * 1000;

                const deleteForEveryoneBtn = document.getElementById('deleteForEveryoneBtn');
                if (deleteForEveryoneBtn) {
                    if (isOwn && isWithin48Hours) {
                        deleteForEveryoneBtn.classList.remove('d-none');
                    } else {
                        deleteForEveryoneBtn.classList.add('d-none');
                    }
                }

                if (window.bootstrap?.Modal) {
                    const bsModal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
                    bsModal.show();
                } else {
                    modalEl.classList.add('show');
                    modalEl.style.display = 'block';
                }
            }

        } else if (action === 'forward') {
            this.pendingForwardMessageId = messageId;
            this._openForwardModal();
        }
    }

    _openForwardModal() {
        const modalEl = document.getElementById('forwardModal');
        if (!modalEl) return;

        const listContainer = document.getElementById('forwardConversationsList');
        const searchInput = document.getElementById('forwardSearchInput');
        const countSpan = document.getElementById('forwardSelectedCount');
        const sendBtn = document.getElementById('sendForwardBtn');

        if (searchInput) searchInput.value = '';
        if (countSpan) countSpan.textContent = '0 selected';
        if (sendBtn) sendBtn.disabled = true;

        if (listContainer) {
            listContainer.innerHTML = '';
            
            // Collect conversations from the rail list
            const railItems = document.querySelectorAll('#whatsappRailChatsList .whatsapp-chat-item');
            const convs = [];
            
            railItems.forEach(item => {
                const id = item.getAttribute('data-conversation-id');
                const nameEl = item.querySelector('.whatsapp-chat-name');
                const name = nameEl ? nameEl.textContent.trim() : `Chat #${id}`;
                const avatarEl = item.querySelector('.chat-avatar');
                const avatarHtml = avatarEl ? avatarEl.outerHTML : `<div class="rounded-circle bg-secondary text-white d-flex align-items-center justify-content-center" style="width: 38px; height: 38px;"><i class="bi bi-person-fill"></i></div>`;
                if (id) {
                    convs.push({ id, name, avatarHtml });
                }
            });

            if (convs.length > 0) {
                this._renderForwardConversationList(convs, listContainer);
            } else {
                listContainer.innerHTML = `<div class="p-4 text-center text-muted small"><span class="spinner-border spinner-border-sm me-2"></span>Loading chats...</div>`;
                fetch('/messaging/v1/conversations/', {
                    headers: { 'X-CSRFToken': getCSRFToken() }
                })
                .then(r => r.json())
                .then(data => {
                    const items = Array.isArray(data) ? data : (data.results || []);
                    const currentUserId = store.getState().currentUserId;
                    const fetchedConvs = items.map(c => {
                        let name = c.name;
                        if (!name && c.members) {
                            const other = c.members.find(m => (m.user?.id || m.user) !== currentUserId);
                            name = other?.user?.username || other?.user?.get_full_name || `Chat #${c.id}`;
                        }
                        return {
                            id: c.id,
                            name: name || `Chat #${c.id}`,
                            avatarHtml: `<div class="rounded-circle bg-primary text-white d-flex align-items-center justify-content-center" style="width: 38px; height: 38px;"><i class="bi bi-chat-dots-fill"></i></div>`
                        };
                    });
                    this._renderForwardConversationList(fetchedConvs, listContainer);
                })
                .catch(() => {
                    listContainer.innerHTML = `<div class="p-4 text-center text-muted small">No other chats found</div>`;
                });
            }
        }

        if (window.bootstrap?.Modal) {
            const bsModal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
            bsModal.show();
        } else {
            modalEl.classList.add('show');
            modalEl.style.display = 'block';
        }
    }

    _renderForwardConversationList(convs, container) {
        if (!convs.length) {
            container.innerHTML = `<div class="p-4 text-center text-muted small">No conversations found</div>`;
            return;
        }

        container.innerHTML = convs.map(c => `
            <label class="d-flex align-items-center gap-3 px-3 py-2 forward-chat-item m-0" style="cursor: pointer; user-select: none;" data-chat-name="${escapeHtml(c.name.toLowerCase())}">
                <input type="checkbox" class="form-check-input flex-shrink-0 forward-checkbox" value="${c.id}" style="margin: 0; width: 1.15rem; height: 1.15rem; cursor: pointer;">
                <div class="flex-shrink-0" style="width: 38px; height: 38px;">
                    ${c.avatarHtml}
                </div>
                <div class="flex-grow-1 overflow-hidden">
                    <div class="fw-semibold text-truncate" style="font-size: 0.9rem;">${escapeHtml(c.name)}</div>
                </div>
            </label>
        `).join('');

        const updateSelection = () => {
            const checkedBoxes = container.querySelectorAll('.forward-checkbox:checked');
            const count = checkedBoxes.length;
            const countSpan = document.getElementById('forwardSelectedCount');
            const sendBtn = document.getElementById('sendForwardBtn');
            if (countSpan) countSpan.textContent = `${count} selected`;
            if (sendBtn) sendBtn.disabled = count === 0;
        };

        container.querySelectorAll('.forward-checkbox').forEach(cb => {
            cb.addEventListener('change', updateSelection);
        });
    }

    /**
     * Handle send click with audio unlock
     */
    _handleSendClick(messageInput) {
        if (!this.audioInitialized) {
            messageSoundManager.unlock().catch(err => {
                console.warn('[UI_CONTROLLER] Failed to unlock audio:', err);
            });
            this.audioInitialized = true;
        }
        if (messageInput) {
            this._handleSendMessage(messageInput.value);
            this._toggleVoiceSendButton('');
        }
    }

    /**
     * Re-sync the mic/send button with the current composer text
     */
    _refreshComposerButton() {
        const input = document.getElementById('messageInput');
        this._toggleVoiceSendButton(input ? input.value : '');
    }

    /**
     * Hide reply banner and forget the reply target
     */
    _clearReplyContext() {
        this.replyToMessageId = null;
        const bar = document.getElementById('replyPreviewBar');
        if (bar) bar.classList.add('d-none');
        document.getElementById('messaging-composer-form')?.classList.remove('has-reply-context');
    }

    /**
     * Build reply_to_details payload for an original message
     * @param {string|number} replyId
     * @returns {Object|null}
     */
    _buildReplyDetails(replyId) {
        if (!replyId) return null;
        const originalMsg = store.getMessageById(replyId);
        if (!originalMsg) return null;
        const currentUserId = store.getState().currentUserId;
        const isOwnOrig = originalMsg.isOwn || Number(originalMsg.senderId) === Number(currentUserId);
        return {
            id: originalMsg.id,
            senderId: originalMsg.senderId,
            sender: {
                username: isOwnOrig ? 'You' : (originalMsg.sender?.username || originalMsg.senderUsername || 'User')
            },
            content: originalMsg.content,
            type: originalMsg.type || originalMsg.message_type,
            attachment_type: originalMsg.metadata?.type || originalMsg.attachment_type,
            is_voice_note: Boolean(originalMsg.metadata?.is_voice_note || originalMsg.is_voice_note)
        };
    }

    /**
     * Read and clear the active reply context (used by text and voice sends)
     * @returns {{reply_to_id: number|null, reply_to_details: Object|null}}
     */
    consumeReplyContext() {
        const replyId = this.replyToMessageId;
        const details = this._buildReplyDetails(replyId);
        this._clearReplyContext();
        const parsedId = replyId ? parseInt(replyId, 10) : null;
        return {
            reply_to_id: Number.isFinite(parsedId) ? parsedId : null,
            reply_to_details: details
        };
    }

    /**
     * Toggle voice/send button icon
     * @param {string} content - Input content
     */
    _toggleVoiceSendButton(content) {
        const voiceBtn = document.getElementById('voiceBtn');
        const sendBtn = document.getElementById('sendBtn');
        const micWrapper = document.getElementById('micIconWrapper');
        const sendWrapper = document.getElementById('sendIconWrapper');
        const hasText = content && content.trim().length > 0;

        if (sendBtn) {
            if (hasText) {
                sendBtn.classList.add('is-active');
                sendBtn.removeAttribute('disabled');
            } else {
                sendBtn.classList.remove('is-active');
            }
        }

        if (voiceBtn) {
            if (hasText) {
                if (micWrapper) micWrapper.classList.add('d-none');
                if (sendWrapper) sendWrapper.classList.remove('d-none');
                voiceBtn.title = 'Send message';
                voiceBtn.setAttribute('aria-label', 'Send message');
                voiceBtn.classList.add('send-mode', 'is-active');
            } else {
                if (micWrapper) micWrapper.classList.remove('d-none');
                if (sendWrapper) sendWrapper.classList.add('d-none');
                voiceBtn.title = 'Voice message';
                voiceBtn.setAttribute('aria-label', 'Voice message');
                voiceBtn.classList.remove('send-mode', 'is-active');
            }
        }
    }
    
    /**
     * Handle message retry
     * @param {Object} detail - Event detail with messageId
     */
    async _handleMessageRetry(detail) {
        const { messageId } = detail;
        this._log('HANDLE_MESSAGE_RETRY', { messageId });

        try {
            const ok = await messageService.retryMessage(messageId);
            if (ok) return;
        } catch (error) {
            console.error('[UI_CONTROLLER] Retry failed:', error);
        }

        // Media path: the text retry has no state for uploads, so hand the
        // failed_upload bubble to the UploadQueue replay instead of dropping it.
        try {
            const msg = store.getMessageById(messageId);
            if (msg && (msg.status === 'failed_upload' || msg.status === 'failed_send')) {
                store.updateMessage(messageId, { status: 'retrying' });
                eventBus.emit(EVENTS.MESSAGE_UPLOAD_RETRY, { tempId: messageId });
            }
        } catch (error) {
            console.error('[UI_CONTROLLER] Media retry handoff failed:', error);
        }
    }

    /**
     * Handle send message (UI → MessageService)
     * @param {string} content - Message content
     */
    async _handleSendMessage(content) {
        this._log('SEND_MESSAGE_CLICKED', { content });

        if (!content || !content.trim()) {
            return;
        }

        const clean = content.trim();

        // Clear input
        const messageInput = document.getElementById('messageInput');
        if (messageInput) {
            messageInput.value = '';
            messageInput.style.height = 'auto';
        }

        // Immediately remove empty state container without waiting for network
        const emptyEl = document.getElementById('messagingEmptyState') || document.querySelector('.messaging-empty-state, .pwanimate-empty-state');
        if (emptyEl) {
            emptyEl.remove();
        }

        // Handle edit mode
        if (this.editingMessageId) {
            const editId = this.editingMessageId;
            this.editingMessageId = null;
            const editBar = document.getElementById('editPreviewBar');
            if (editBar) editBar.classList.add('d-none');

            import('../core/websocket.js?v=20261106-group1').then(({ webSocketManager }) => {
                webSocketManager.send({
                    type: 'edit_message',
                    message_id: parseInt(editId, 10),
                    content: clean
                });
            });
            const nowIso = new Date().toISOString();
            store.updateMessage(editId, {
                content: clean,
                editedAt: nowIso
            });
            if (window.offlineCache && typeof window.offlineCache.saveMessages === 'function') {
                const existing = store.getMessageById(editId);
                window.offlineCache.saveMessages([{
                    ...(existing || {}),
                    id: parseInt(editId, 10),
                    content: clean,
                    edited_at: nowIso
                }]).catch(() => {});
            }
            return;
        }

        // Handle reply mode
        const { reply_to_id, reply_to_details } = this.consumeReplyContext();

        await messageService.sendMessage(clean, {
            reply_to_id,
            reply_to_details
        });
    }

    /**
     * Handle typing indicator (UI interaction only)
     */
    _handleTyping() {
        messageService.sendTypingIndicator(true);

        if (this.typingTimeout) {
            clearTimeout(this.typingTimeout);
        }

        this.typingTimeout = setTimeout(() => {
            messageService.sendTypingIndicator(false);
        }, 1500);

        this._log('TYPING_INDICATOR_SENT');
    }

    /**
     * Handle reconnect request (UI → AppController)
     */
    _handleReconnect() {
        this._log('RECONNECT_REQUESTED');
        if (window.appController) {
            window.appController.handleReconnect();
        }
    }

    /**
     * Update connection status in UI (pure UI update)
     * @param {string} connectionState - Connection state
     */
    _updateConnectionStatus(connectionState) {
        const statusElement = document.getElementById('chatStatus');
        if (!statusElement) return;

        const state = store.getState();
        if (connectionState === 'connected') {
            // Socket is connected: immediately reflect peer presence
            this._updateTypingIndicators(state.typingUsers, state.peerOnlineStatus);
        } else if (connectionState === 'connecting' || connectionState === 'reconnecting') {
            statusElement.textContent = 'Connecting...';
            statusElement.className = 'chat-status connecting text-muted';
        } else if (connectionState === 'disconnected') {
            // Only indicate waiting for network if client device is genuinely offline
            if (typeof navigator !== 'undefined' && !navigator.onLine) {
                statusElement.textContent = 'Waiting for network...';
                statusElement.className = 'chat-status disconnected text-muted';
            } else {
                statusElement.textContent = 'Connecting...';
                statusElement.className = 'chat-status connecting text-muted';
            }
        } else {
            statusElement.textContent = 'Connecting...';
            statusElement.className = 'chat-status text-muted';
        }
    }

    /**
     * Update typing indicators and peer online status (pure UI update)
     * @param {Map} typingUsers - Typing users map
     * @param {Map} peerOnlineStatus - Peer online status map
     */
    _updateTypingIndicators(typingUsers, peerOnlineStatus) {
        // Synchronize status dots and avatars across DOM (left rail, conversation list, suggested friends)
        if (peerOnlineStatus && peerOnlineStatus.size > 0) {
            peerOnlineStatus.forEach((statusObj, userId) => {
                const isOnline = statusObj ? Boolean(statusObj.isOnline) : false;
                const dots = document.querySelectorAll(`[data-user-status-id="${userId}"]`);
                dots.forEach(dot => {
                    if (isOnline) {
                        dot.classList.add('online');
                        dot.style.display = '';
                    } else {
                        dot.classList.remove('online');
                        dot.style.display = 'none';
                    }
                });
                const avatars = document.querySelectorAll(`.chat-avatar[data-user-id="${userId}"]`);
                avatars.forEach(av => {
                    if (isOnline) {
                        av.classList.add('online', 'avatar-online');
                    } else {
                        av.classList.remove('online', 'avatar-online');
                    }
                });
            });
        }

        const chatStatus = document.getElementById('chatStatus');
        const chatAvatar = document.querySelector('.chat-avatar');

        if (!chatStatus) return;

        const typingEntries = Array.from(typingUsers.entries());
        const peerArray = Array.from(peerOnlineStatus.entries());
        const state = store.getState();
        const recordingUsers = state.recordingUsers || new Map();
        const recordingArray = Array.from(recordingUsers.values());

        const peer = peerArray.length > 0 ? peerArray[0][1] : null;
        const isPeerOnline = peer ? peer.isOnline : false;
        const lastSeen = peer ? peer.lastSeen : null;

        // 1. Manage chat stream typing and recording bubbles
        if (recordingArray.length > 0) {
            const recordingEntries = Array.from(recordingUsers.entries());
            const [userId, username] = recordingEntries[0];
            this.renderer.showRecordingIndicator(username, userId);
        } else if (typingEntries.length > 0) {
            const [userId, username] = typingEntries[0];
            this.renderer.showTypingIndicator(username, userId);
        } else {
            this.renderer.hideTypingIndicator();
        }

        // 2. Header ALWAYS shows peer presence (Active now / Last seen) - NEVER overwritten by typing/recording
        if (state.isGroupChat) {
            chatStatus.textContent = recordingArray.length ? `${recordingArray.join(', ')} recording…`
                : typingEntries.length ? `${typingEntries.map(([, name]) => name).join(', ')} typing…`
                : `${document.querySelector('.chat-container')?.dataset.memberCount || ''} members`;
            chatStatus.className = 'chat-status small text-muted';
            return;
        }
        if (isPeerOnline) {
            chatStatus.textContent = 'Active now';
            chatStatus.className = 'chat-status text-success fw-semibold';
            if (chatAvatar) chatAvatar.classList.add('avatar-online');
        } else if (lastSeen) {
            chatStatus.textContent = this._formatLastSeen(lastSeen);
            chatStatus.className = 'chat-status text-muted';
            if (chatAvatar) chatAvatar.classList.remove('avatar-online');
        } else {
            chatStatus.textContent = 'Offline';
            chatStatus.className = 'chat-status text-muted';
            if (chatAvatar) chatAvatar.classList.remove('avatar-online');
        }
    }

    /**
     * Format last seen timestamp to human readable string
     * @param {number|string} timestamp - Last seen timestamp
     * @returns {string} Formatted string
     */
    _formatLastSeen(timestamp) {
        if (!timestamp) return '';
        const now = new Date();
        const lastSeen = new Date(timestamp);
        if (isNaN(lastSeen.getTime())) return '';
        const diffMs = now - lastSeen;
        const diffSeconds = Math.floor(diffMs / 1000);
        const diffMinutes = Math.floor(diffSeconds / 60);

        if (diffSeconds < 60) return 'last seen just now';
        if (diffMinutes < 60) return `last seen ${diffMinutes}m ago`;

        // Check if same day
        const today = new Date();
        today.setHours(0, 0, 0, 0);
        const yesterday = new Date(today);
        yesterday.setDate(yesterday.getDate() - 1);

        const lastSeenDate = new Date(lastSeen);
        lastSeenDate.setHours(0, 0, 0, 0);

        if (lastSeenDate.getTime() === today.getTime()) {
            const timeStr = lastSeen.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
            return `last seen today at ${timeStr}`;
        }

        if (lastSeenDate.getTime() === yesterday.getTime()) {
            const timeStr = lastSeen.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
            return `last seen yesterday at ${timeStr}`;
        }

        const day = lastSeen.getDate();
        const month = lastSeen.toLocaleDateString('en-US', { month: 'short' });
        const timeStr = lastSeen.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', hour12: true });
        return `last seen ${day} ${month} at ${timeStr}`;
    }

    /**
     * Update UI state (pure UI update)
     * @param {string} uiState - UI state
     */
    _updateUIState(uiState) {
        // Update UI based on state (pure UI behavior)
        const loadingElement = document.getElementById('chatLoading');
        if (loadingElement) {
            loadingElement.style.display = uiState === 'loading' ? 'block' : 'none';
        }
    }

    /**
     * Apply theme (pure UI update)
     * @param {Object} theme - Theme object
     * @param {string} mode - Theme mode
     */
    _applyTheme(theme, mode) {
        if (theme) {
            Object.entries(theme).forEach(([property, value]) => {
                document.documentElement.style.setProperty(property, value);
            });
        }
    }

    /**
     * Setup read receipt observer to detect when received messages become visible
     */
    _setupReadReceiptObserver() {
        this._log('SETUP_READ_RECEIPT_OBSERVER');

        // Cleanup existing observer
        if (this.readReceiptObserver) {
            this.readReceiptObserver.disconnect();
        }

        // Track already-read messages to avoid duplicate receipts
        this.readMessageIds = new Set();

        // Create intersection observer
        this.readReceiptObserver = new IntersectionObserver((entries) => {
            // Truthful check: If tab is hidden or window minimized, do not mark as read
            if (document.hidden) {
                return;
            }

            const messageIdsToMark = [];

            entries.forEach(entry => {
                if (entry.isIntersecting) {
                    const messageId = entry.target.getAttribute('data-message-id');
                    const senderId = entry.target.getAttribute('data-sender-id');
                    const status = entry.target.getAttribute('data-status');

                    // Only mark received messages (not own messages) that haven't been read yet
                    if (messageId &&
                        senderId &&
                        String(senderId) !== String(this.currentUserId) &&
                        status !== 'read' &&
                        !this.readMessageIds.has(messageId)) {

                        messageIdsToMark.push(messageId);
                        this.readMessageIds.add(messageId);

                        // Stop observing this message
                        this.readReceiptObserver.unobserve(entry.target);
                    }
                }
            });

            // Send read receipts for visible messages in batch
            if (messageIdsToMark.length > 0) {
                this._log('MESSAGES_BECAME_VISIBLE', { count: messageIdsToMark.length });
                messageService.markMessagesAsRead(messageIdsToMark);
            }
        }, {
            root: document.getElementById('messagesContainer'),
            threshold: 0.5 // Message must be 50% visible
        });

        // Add listeners for tab visibility and window focus to check visible messages when user returns
        if (!this._visibilityHandlersAttached) {
            this._visibilityHandlersAttached = true;
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) {
                    this._observeReceivedMessages();
                }
            });
            window.addEventListener('focus', () => {
                this._observeReceivedMessages();
            });
        }

        this._log('READ_RECEIPT_OBSERVER_SETUP');
    }

    /**
     * Observe a message element for read receipt
     * @param {HTMLElement} element - Message element
     */
    _observeMessageForReadReceipt(element) {
        if (this.readReceiptObserver && element) {
            this.readReceiptObserver.observe(element);
        }
    }

    /**
     * Observe all received (non-own) messages in the container for read receipts
     * Also immediately marks visible messages as read (for real-time updates)
     */
    _observeReceivedMessages() {
        const container = document.getElementById('messagesContainer');
        if (!container || !this.readReceiptObserver) return;

        // Truthful check: do not mark messages as read if the tab is currently in background/hidden
        const canMarkImmediately = !document.hidden;

        // Find all received message bubbles (not sent by current user)
        const receivedMessages = container.querySelectorAll('.message-bubble.received[data-message-id]');
        const immediatelyVisibleIds = [];

        receivedMessages.forEach(messageEl => {
            const messageId = messageEl.getAttribute('data-message-id');
            const status = messageEl.getAttribute('data-status');

            // Skip if already marked read in DOM or previously handled
            if (status === 'read') {
                this.readMessageIds.add(messageId);
                return;
            }

            // Only observe if not already processed
            if (messageId && !this.readMessageIds.has(messageId)) {
                this.readReceiptObserver.observe(messageEl);

                // Check if message is already visible in viewport
                if (canMarkImmediately && this._isElementVisible(messageEl, container)) {
                    immediatelyVisibleIds.push(messageId);
                    this.readMessageIds.add(messageId);
                    this.readReceiptObserver.unobserve(messageEl);
                }
            }
        });

        // Immediately mark visible messages as read (don't wait for scroll)
        if (immediatelyVisibleIds.length > 0) {
            this._log('MESSAGES_ALREADY_VISIBLE', { count: immediatelyVisibleIds.length });
            messageService.markMessagesAsRead(immediatelyVisibleIds);
        }

        this._log('OBSERVING_RECEIVED_MESSAGES', {
            total: receivedMessages.length,
            newlyObserved: receivedMessages.length - immediatelyVisibleIds.length,
            immediatelyRead: immediatelyVisibleIds.length
        });
    }

    /**
     * Check if an element is visible in its container's viewport
     * @param {HTMLElement} element - Element to check
     * @param {HTMLElement} container - Container element
     * @returns {boolean} True if element is visible
     */
    _isElementVisible(element, container) {
        const containerRect = container.getBoundingClientRect();
        const elementRect = element.getBoundingClientRect();

        // More lenient visibility check - element should be at least partially visible
        // and not completely above or below the container
        const isVisible = (
            elementRect.bottom > containerRect.top &&  // Not completely above
            elementRect.top < containerRect.bottom &&   // Not completely below
            elementRect.height > 0
        );

        // Debug logging
        if (element.getAttribute('data-message-id')) {
            console.log('[VISIBILITY_CHECK]', {
                messageId: element.getAttribute('data-message-id'),
                isVisible,
                elementTop: elementRect.top,
                elementBottom: elementRect.bottom,
                containerTop: containerRect.top,
                containerBottom: containerRect.bottom
            });
        }

        return isVisible;
    }

    /**
     * Check if state changed (pure comparison)
     * @param {Object} newState - New state
     * @param {Object} lastState - Last state
     * @returns {boolean} State changed
     */
    _stateChanged(newState, lastState) {
        if (!lastState) return true;

        // Check message count
        if (newState.messages.length !== lastState.messagesLength) {
            console.log('[UI_CONTROLLER] State changed: message count', lastState.messagesLength, '->', newState.messages.length);
            return true;
        }

        // Check message statuses (for read receipts)
        if (newState.messages.length > 0) {
            const newChecksum = this._computeMessagesChecksum(newState.messages);
            if (lastState.messagesChecksum && newChecksum !== lastState.messagesChecksum) {
                console.log('[UI_CONTROLLER] State changed: message checksum', lastState.messagesChecksum, '->', newChecksum);
                return true;
            }
            const newContentChecksum = this._computeContentChecksum(newState.messages);
            if (lastState.messagesContentChecksum && newContentChecksum !== lastState.messagesContentChecksum) {
                console.log('[UI_CONTROLLER] State changed: message content/edit/delete checksum changed');
                return true;
            }
            if (!lastState.messagesContentChecksum && newState.messages.length > 0) {
                return true;
            }
            // If no previous checksum but we have messages now, state changed
            if (!lastState.messagesChecksum && newState.messages.length > 0) {
                console.log('[UI_CONTROLLER] State changed: new messages appeared');
                return true;
            }
        }

        const newPeerChecksum = this._computePeerOnlineStatusChecksum(newState.peerOnlineStatus);
        const changed = (
            newState.connectionState !== lastState.connectionState ||
            newState.typingUsers.size !== lastState.typingUsersSize ||
            (newState.recordingUsers?.size || 0) !== (lastState.recordingUsersSize || 0) ||
            newState.peerOnlineStatus.size !== lastState.peerOnlineStatusSize ||
            newPeerChecksum !== (lastState.peerOnlineStatusChecksum || '') ||
            newState.uiState !== lastState.uiState ||
            JSON.stringify(newState.currentTheme) !== JSON.stringify(lastState.currentTheme)
        );

        if (changed) {
            console.log('[UI_CONTROLLER] State changed: other properties');
        }

        return changed;
    }

    /**
     * Compute a checksum of peer online statuses
     * @param {Map} peerOnlineStatus - Peer online status map
     * @returns {string} Checksum string
     */
    _computePeerOnlineStatusChecksum(peerOnlineStatus) {
        if (!peerOnlineStatus || peerOnlineStatus.size === 0) return '';
        return Array.from(peerOnlineStatus.entries())
            .map(([id, status]) => `${id}:${status?.isOnline ? 1 : 0}:${status?.lastSeen || ''}`)
            .sort()
            .join('|');
    }

    /**
     * Compute a checksum of message statuses for detecting read receipt updates
     * @param {Array} messages - Messages array
     * @returns {string} Checksum string
     */
    _computeMessagesChecksum(messages) {
        // Create a checksum based on message IDs, statuses, optimistic state, media URL and read avatar
        // (avatar-only receipt updates must still trigger a tick repaint).
        return messages.map(m => `${m.id}:${m.status || 'sent'}:${m.isOptimistic ? '1' : '0'}:${m.metadata?.url || ''}:${m.read_avatar || m.metadata?.read_avatar || ''}`).join('|');
    }

    /**
     * Compute a checksum of message content, editedAt, isDeleted, type, and media URL
     * @param {Array} messages - Messages array
     * @returns {string} Checksum string
     */
    _computeContentChecksum(messages) {
        return messages.map(m => `${m.id}:${m.status || 'sent'}:${m.isOptimistic ? '1' : '0'}:${m.type || ''}:${m.metadata?.url || ''}:${m.editedAt || m.edited_at || ''}:${m.isDeleted ? '1' : '0'}:${m.content || ''}`).join('|');
    }

    /**
     * Create state snapshot for comparison (pure data transformation)
     * @param {Object} state - State to snapshot
     * @returns {Object} State snapshot
     */
    _createStateSnapshot(state) {
        return {
            messagesLength: state.messages.length,
            messagesChecksum: state.messages.length > 0
                ? this._computeMessagesChecksum(state.messages)
                : null,
            messagesContentChecksum: state.messages.length > 0
                ? this._computeContentChecksum(state.messages)
                : null,
            connectionState: state.connectionState,
            typingUsersSize: state.typingUsers.size,
            typingUsersList: Array.from(state.typingUsers || []).join(','),
            recordingUsersSize: state.recordingUsers?.size || 0,
            peerOnlineStatusSize: state.peerOnlineStatus.size,
            peerOnlineStatusChecksum: this._computePeerOnlineStatusChecksum(state.peerOnlineStatus),
            uiState: state.uiState,
            currentTheme: state.currentTheme ? JSON.parse(JSON.stringify(state.currentTheme)) : null
        };
    }

    /**
     * Get UI controller status (read-only)
     * @returns {Object} Status
     */
    getStatus() {
        return {
            initialized: !!this.unsubscribe,
            rendererInitialized: !!this.renderer,
            subscribed: !!this.unsubscribe,
            lastState: this.lastState,
            isReadOnly: true // Explicitly mark as read-only
        };
    }

    /**
     * Destroy UI controller
     */
    destroy() {
        this._log('UI_CONTROLLER_DESTROY');

        if (this.unsubscribe) {
            this.unsubscribe();
            this.unsubscribe = null;
        }

        if (this.renderer) {
            this.renderer.destroy();
            this.renderer = null;
        }

        // Clean up read receipt observer
        if (this.readReceiptObserver) {
            this.readReceiptObserver.disconnect();
            this.readReceiptObserver = null;
        }

        if (this.readMessageIds) {
            this.readMessageIds.clear();
            this.readMessageIds = null;
        }

        this.lastState = null;
    }

    /**
     * Log debug information
     * @param {string} action - Action type
     * @param {*} data - Action data
     */
    _log(action, data) {
        if (this.debugMode) {
            console.log(`[UI_CONTROLLER] ${action}:`, data);
        }
    }
}

// Create and export singleton instance
export const uiController = new UIController();
