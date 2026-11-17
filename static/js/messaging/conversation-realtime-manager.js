/**
 * vasto Conversation Realtime & Pinning Manager
 * Provides:
 * 1. Bi-directional real-time conversation list updates (sidebar rail & mobile list)
 * 2. Telegram/WhatsApp-style instant re-ordering with pinning (max 3 pinned chats)
 * 3. Rich snippet formatting (🎤 Voice, 📷 Photo, 🎥 Video, 📄 Document)
 * 4. Solid unread badge recalculation & active conversation sync
 * 5. Right-click custom context menu + 3-dot dropdown menu for Pin/Unpin
 */

(function() {
    'use strict';

    if (window.__pwaniConvRealtimeManagerLoaded) {
        if (typeof window.cacheDOMConversations === 'function') {
            window.cacheDOMConversations();
        }
        return;
    }
    window.__pwaniConvRealtimeManagerLoaded = true;

    // Helper to get CSRF token
    function getCsrfToken() {
        const input = document.querySelector('[name="csrfmiddlewaretoken"]');
        if (input && input.value) return input.value;
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const cookie = document.cookie.split('; ').find(row => row.startsWith('csrftoken='));
        return cookie ? cookie.split('=')[1] : '';
    }

    // Helper to show modern floating toast
    function showToast(message, type = 'info') {
        const existing = document.getElementById('chatToastContainer');
        let container = existing;
        if (!container) {
            container = document.createElement('div');
            container.id = 'chatToastContainer';
            container.style.cssText = 'position: fixed; bottom: 24px; right: 24px; z-index: 10550; display: flex; flex-direction: column; gap: 8px; pointer-events: none;';
            document.body.appendChild(container);
        }

        const toast = document.createElement('div');
        toast.className = `alert alert-${type === 'error' ? 'danger' : (type === 'success' ? 'success' : 'primary')} shadow-sm border-0 py-2 px-3 m-0 d-flex align-items-center gap-2 fade show`;
        toast.style.cssText = 'pointer-events: auto; border-radius: 12px; font-size: 0.88rem; min-width: 260px; max-width: 360px;';
        
        let icon = 'info';
        if (type === 'success') icon = 'circle-check-big';
        if (type === 'error') icon = 'triangle-alert';

        toast.innerHTML = `
            <i aria-hidden="true" data-lucide="${icon}" class="vasto-icon fs-5"></i>
            <div class="flex-grow-1">${message}</div>
            <button type="button" class="btn-close ms-auto" style="font-size: 0.7rem;"></button>
        `;

        toast.querySelector('.btn-close').addEventListener('click', () => toast.remove());
        container.appendChild(toast);
        setTimeout(() => {
            toast.classList.remove('show');
            setTimeout(() => toast.remove(), 250);
        }, 3500);
    }

    // ─────────────────────────────────────────────────────────────
    // 1. PIN / UNPIN CONTROLLER (MAX 3)
    // ─────────────────────────────────────────────────────────────
    async function togglePin(conversationId, targetAction) {
        if (!conversationId) return;
        const action = targetAction || 'pin';
        const csrfToken = getCsrfToken();

        try {
            const response = await fetch(`/messaging/v1/conversations/${conversationId}/${action}/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': csrfToken
                }
            });

            if (!response.ok) {
                let errorMsg = 'Could not update pin status';
                try {
                    const errData = await response.json();
                    errorMsg = errData.error || errData.detail || errorMsg;
                } catch (_) {}
                showToast(errorMsg, 'error');
                return;
            }

            const data = await response.json();
            const isPinned = Boolean(data.is_pinned);
            updateConversationPinState(conversationId, isPinned);

            if (isPinned) {
                showToast('Chat pinned to top', 'success');
            } else {
                showToast('Chat unpinned', 'info');
            }
        } catch (err) {
            console.error('[PIN] Failed to toggle pin:', err);
            showToast('Network error while pinning chat', 'error');
        }
    }

    function updateConversationPinState(conversationId, isPinned) {
        const items = document.querySelectorAll(`[data-conversation-id="${conversationId}"]`);
        items.forEach(item => {
            item.setAttribute('data-is-pinned', isPinned ? 'true' : 'false');
            item.dataset.isPinned = isPinned ? 'true' : 'false';

            // Pin badge in header/time area
            let pinIcon = item.querySelector('.chat-pin-icon');
            if (isPinned) {
                if (!pinIcon) {
                    pinIcon = document.createElement('i');
                    pinIcon.setAttribute('aria-hidden', 'true');
                    pinIcon.setAttribute('data-lucide', 'pin');
                    pinIcon.className = 'vasto-icon vasto-icon--filled text-primary ms-1 chat-pin-icon';
                    pinIcon.title = 'Pinned';
                    pinIcon.style.fontSize = '0.78rem';
                    const targetContainer = item.querySelector('.whatsapp-chat-name, .chat-name, .chat-top-line');
                    if (targetContainer) {
                        targetContainer.appendChild(pinIcon);
                        if (window.VastoIcons) VastoIcons.render(pinIcon);
                    }
                }
            } else {
                if (pinIcon) pinIcon.remove();
            }

            // Update dropdown / context menu button text
            const pinBtn = item.querySelector('.pin-toggle-btn');
            if (pinBtn) {
                pinBtn.dataset.action = isPinned ? 'unpin' : 'pin';
                pinBtn.innerHTML = `<i aria-hidden="true" data-lucide="pin" class="vasto-icon${isPinned ? ' vasto-icon--filled' : ''} me-2 text-primary"></i> ${isPinned ? 'Unpin chat' : 'Pin chat'}`;
            }

            // Immediately re-order the conversation within its parent list
            reorderConversationElement(item, isPinned);
        });
    }

    function reorderConversationElement(element, isPinned) {
        // Resolve wrapper if inside .whatsapp-chat-item-wrapper
        const row = element.closest('.whatsapp-chat-item-wrapper') || element;
        const container = row.parentElement;
        if (!container) return;

        if (isPinned) {
            // Pinned chats go to the very top
            if (container.firstChild !== row) {
                container.insertBefore(row, container.firstChild);
            }
        } else {
            // Find all pinned sibling rows in this container
            const pinnedRows = Array.from(container.children).filter(child => {
                if (child === row) return false;
                const inner = child.querySelector('[data-is-pinned="true"]') || (child.getAttribute('data-is-pinned') === 'true' ? child : null);
                return !!inner;
            });

            if (pinnedRows.length > 0) {
                const lastPinned = pinnedRows[pinnedRows.length - 1];
                if (lastPinned.nextSibling !== row) {
                    container.insertBefore(row, lastPinned.nextSibling);
                }
            } else {
                if (container.firstChild !== row) {
                    container.insertBefore(row, container.firstChild);
                }
            }
        }
    }

    // ─────────────────────────────────────────────────────────────
    // 2. REALTIME CONVERSATION UPDATE HANDLER
    // ─────────────────────────────────────────────────────────────
    function buildSnippetHtml(data) {
        const pType = data.preview_type || 'text';
        const rawPreview = data.message_preview || '';
        const cleanText = data.clean_preview || rawPreview.replace(/^[🎤📷🎥📄🔗😊🎬🚫🔒↪️]+\s*/, '').trim() || 'New message';

        const iconHtml = (name, cls) => `<i aria-hidden="true" data-lucide="${name}" class="vasto-icon ${cls}"></i>`;
        if (pType === 'deleted') {
            return `${iconHtml('circle-slash', 'text-muted me-1')} <em>${escapeHtml(cleanText)}</em>`;
        }
        if (pType === 'audio' || rawPreview.includes('🎤') || rawPreview.includes('🎵')) {
            const iconClass = data.preview_icon || 'mic';
            return `${iconHtml(iconClass, 'text-primary me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'image' || rawPreview.includes('📷')) {
            const iconClass = data.preview_icon || 'camera';
            return `${iconHtml(iconClass, 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'video' || rawPreview.includes('🎥')) {
            return `${iconHtml('video', 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'document' || rawPreview.includes('📄')) {
            return `${iconHtml('file-text', 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'link' || rawPreview.includes('🔗')) {
            return `${iconHtml('link', 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'sticker' || rawPreview.includes('😊')) {
            return `${iconHtml('smile', 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'gif' || rawPreview.includes('🎬')) {
            return `${iconHtml('clapperboard', 'text-muted me-1')} ${escapeHtml(cleanText)}`;
        }
        if (pType === 'reaction') {
            return `${iconHtml('heart', 'vasto-icon--filled text-danger me-1')} ${escapeHtml(cleanText)}`;
        }
        return escapeHtml(rawPreview || cleanText);
    }

    function createDynamicConversationNodes(data, isCurrentActive) {
        const convId = String(data.conversation_id);
        const convName = data.conversation_name || data.sender_display_name || data.sender_name || `Chat #${convId}`;
        const convAvatar = data.conversation_avatar || data.sender_avatar || '/static/images/web-app-manifest-192x192-rounded.png';
        const isGroup = data.conversation_type === 'group';
        const unreadCount = isCurrentActive ? 0 : Number(data.unread_count || 0);
        const displayCount = unreadCount > 99 ? '99+' : unreadCount;
        const snippetHtml = buildSnippetHtml(data);
        const targetUrl = data.target_url || `/messaging/conversation/${convId}/`;

        // 1. Desktop / Refactored Left Rail (`#whatsappRailChatsList`)
        const railList = document.getElementById('whatsappRailChatsList');
        if (railList && !railList.querySelector(`[data-conversation-id="${convId}"]`)) {
            const emptyState = railList.querySelector('.text-center.text-muted, .sidebar-empty');
            if (emptyState) emptyState.remove();

            const wrapper = document.createElement('div');
            wrapper.className = 'whatsapp-chat-item-wrapper position-relative mb-1';
            wrapper.innerHTML = `
                <a href="${escapeHtml(targetUrl)}"
                   class="whatsapp-chat-item d-flex align-items-center gap-2 px-2 py-2 text-decoration-none rounded-3"
                   data-conversation-id="${escapeHtml(convId)}"
                   data-is-pinned="false"
                   hx-get="${escapeHtml(targetUrl)}"
                   hx-target="#chatMainAreaWrapper"
                   hx-swap="innerHTML"
                   hx-push-url="true"
                   hx-indicator="#chatSwitchProgress">
                    <div class="position-relative flex-shrink-0">
                        ${isGroup
                            ? `<div class="chat-avatar bg-primary text-white d-flex align-items-center justify-content-center rounded-circle" style="width: 44px; height: 44px;"><i aria-hidden="true" data-lucide="users" class="vasto-icon fs-5"></i></div>`
                            : `<img src="${escapeHtml(convAvatar)}" class="chat-avatar rounded-circle border" style="width: 44px; height: 44px; object-fit: cover;" alt="${escapeHtml(convName)}" onerror="this.src='/static/images/web-app-manifest-192x192-rounded.png'">`
                        }
                    </div>
                    <div class="flex-grow-1 overflow-hidden d-flex flex-column justify-content-center" style="min-width: 0;">
                        <div class="d-flex align-items-center justify-content-between mb-1">
                            <span class="whatsapp-chat-name fw-semibold text-truncate text-body" style="font-size: 0.9rem;">${escapeHtml(convName)}</span>
                            <div class="chat-meta d-flex align-items-center gap-1 flex-shrink-0 ms-2" style="padding-right: 22px;">
                                <span class="whatsapp-chat-time text-muted" style="font-size: 0.72rem;">now</span>
                            </div>
                        </div>
                        <div class="d-flex align-items-center justify-content-between" style="padding-right: 22px;">
                            <span class="whatsapp-chat-snippet text-truncate d-block ${unreadCount > 0 ? 'fw-bold text-body unread' : 'text-muted fw-normal'}" style="font-size: 0.8rem;">
                                ${snippetHtml}
                            </span>
                            <span class="whatsapp-chat-unread-badge ms-2 flex-shrink-0 ${unreadCount > 0 ? '' : 'd-none'}">${displayCount}</span>
                        </div>
                    </div>
                </a>
            `;
            railList.insertBefore(wrapper, railList.firstChild);
            if (window.htmx && typeof window.htmx.process === 'function') {
                window.htmx.process(wrapper);
            }
        }

        // 2. Conversation List Page (`.chats-scroll`)
        const chatsScroll = document.querySelector('.chats-scroll');
        if (chatsScroll && !chatsScroll.querySelector(`[data-conversation-id="${convId}"]`)) {
            const emptyState = chatsScroll.querySelector('.sidebar-empty');
            if (emptyState) emptyState.remove();

            const wrapper = document.createElement('div');
            wrapper.className = 'chat-row-wrapper position-relative mb-1';
            wrapper.innerHTML = `
                <a href="${escapeHtml(targetUrl)}" class="chat-row" data-conversation-id="${escapeHtml(convId)}" data-is-pinned="false">
                    ${isGroup
                        ? `<div class="chat-icon"><i aria-hidden="true" data-lucide="users" class="vasto-icon"></i></div>`
                        : `<div class="position-relative d-inline-block flex-shrink-0">
                             <img src="${escapeHtml(convAvatar)}" class="chat-avatar" alt="${escapeHtml(convName)}" onerror="this.src='/static/images/web-app-manifest-192x192-rounded.png'">
                           </div>`
                    }
                    <div class="chat-info flex-grow-1 overflow-hidden" style="min-width: 0;">
                        <div class="chat-top-line d-flex align-items-center justify-content-between mb-1">
                            <h4 class="chat-name fw-semibold text-truncate me-2 mb-0" style="font-size: 0.95rem;">
                                <span title="${escapeHtml(convName)}">${escapeHtml(convName)}</span>
                            </h4>
                            <div class="chat-meta d-flex align-items-center gap-1 flex-shrink-0 ms-auto" style="padding-right: 26px;">
                                <span class="chat-time text-muted small" style="font-size: 0.72rem;">now</span>
                            </div>
                        </div>
                        <div class="chat-bottom-line d-flex align-items-center justify-content-between" style="padding-right: 26px;">
                            <div class="chat-preview flex-grow-1 overflow-hidden" style="min-width: 0;">
                                <span class="chat-preview-text text-truncate d-block ${unreadCount > 0 ? 'fw-bold text-body unread' : 'text-muted fw-normal'}">
                                    ${snippetHtml}
                                </span>
                            </div>
                            <span class="unread-badge ms-2 flex-shrink-0 ${unreadCount > 0 ? '' : 'd-none'}">${displayCount}</span>
                        </div>
                    </div>
                </a>
            `;
            chatsScroll.insertBefore(wrapper, chatsScroll.firstChild);
        }
    }

    function updateConversationInList(data) {
        if (!data || !data.conversation_id) return;
        const convId = String(data.conversation_id);
        const activeContainer = document.getElementById('chatMainArea');
        const activeConvId = activeContainer?.dataset?.conversationId;
        const onThisChatUrl = window.location.pathname.indexOf('/messaging/conversation/' + convId) === 0;
        const isCurrentActive = Boolean((activeConvId && String(activeConvId) === convId) || onThisChatUrl);
        const isReadReceiptOnly = data.event_kind === 'read_receipt' || (!data.message_preview && !data.clean_preview);

        // Read receipts also arrive here (sender's notifications_* channel) when
        // the chat-room socket is down or paused. Mirror them into the chat
        // store so ticks update without waiting for a history refetch.
        if (data.event_kind === 'read_receipt' && data.last_read_message_id) {
            try {
                if (window.store && typeof window.store.markMessagesAsReadUpTo === 'function') {
                    window.store.markMessagesAsReadUpTo(data.last_read_message_id, data.read_avatar || null);
                }
            } catch (_) {}
        }

        // Query conversation items in sidebar, mobile list, or cards
        let items = document.querySelectorAll(`[data-conversation-id="${convId}"]`);
        if ((!items || items.length === 0) && !isReadReceiptOnly) {
            createDynamicConversationNodes(data, isCurrentActive);
            items = document.querySelectorAll(`[data-conversation-id="${convId}"]`);
        }
        if (!items || items.length === 0) return;

        items.forEach(item => {
            const unreadCount = isCurrentActive ? 0 : Number(data.unread_count || 0);

            // 1. Format snippet with rich icons (only if this event carries a message preview)
            const snippetEl = item.querySelector('.whatsapp-chat-snippet, .chat-preview-text, .conversation-card-preview');
            if (snippetEl) {
                if (!isReadReceiptOnly) {
                    snippetEl.innerHTML = buildSnippetHtml(data);
                }

                if (unreadCount > 0) {
                    snippetEl.classList.remove('text-muted', 'fw-normal');
                    snippetEl.classList.add('fw-bold', 'text-body', 'unread');
                } else {
                    snippetEl.classList.remove('fw-bold', 'text-body', 'unread');
                    snippetEl.classList.add('text-muted', 'fw-normal');
                }
            }

            // 2. Update Timestamp (only on new message / edit / reaction / member_added)
            if (!isReadReceiptOnly && data.event_kind !== 'delete') {
                const timeEl = item.querySelector('.whatsapp-chat-time, .chat-time, .conversation-card-time');
                if (timeEl) {
                    timeEl.textContent = 'now';
                }
            }

            // 3. Update Unread Badge accurately
            const badgeEl = item.querySelector('.whatsapp-chat-unread-badge, .unread-badge, .conversation-card-badge, .messaging-rail-unread-dot');
            if (unreadCount > 0) {
                const displayCount = unreadCount > 99 ? '99+' : unreadCount;
                if (badgeEl) {
                    if (!badgeEl.classList.contains('messaging-rail-unread-dot')) {
                        badgeEl.textContent = displayCount;
                    }
                    badgeEl.style.display = '';
                    badgeEl.classList.remove('d-none');
                } else {
                    const badgeContainer = item.querySelector('.chat-bottom-line') || item.querySelector('.chat-meta') || item.querySelector('.d-flex.align-items-center.justify-content-between:last-child');
                    if (badgeContainer) {
                        const newBadge = document.createElement('span');
                        newBadge.className = 'whatsapp-chat-unread-badge unread-badge ms-2 flex-shrink-0';
                        newBadge.textContent = displayCount;
                        badgeContainer.appendChild(newBadge);
                    }
                }
            } else if (badgeEl) {
                badgeEl.style.display = 'none';
                badgeEl.classList.add('d-none');
            }

            // 4. Reorder element with pinning priority on new messages
            if (!isReadReceiptOnly && data.event_kind !== 'delete' && data.event_kind !== 'edit') {
                const isPinned = item.getAttribute('data-is-pinned') === 'true';
                reorderConversationElement(item, isPinned);

                item.classList.add('chat-item-updated');
                setTimeout(() => item.classList.remove('chat-item-updated'), 1200);
            }
        });
    }

    function escapeHtml(text) {
        if (!text) return '';
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    // ─────────────────────────────────────────────────────────────
    // 3. CONTEXT MENU & THREE-DOT DROPDOWN INTERACTION
    // ─────────────────────────────────────────────────────────────
    let activeContextMenu = null;

    function hideContextMenu() {
        if (activeContextMenu) {
            activeContextMenu.remove();
            activeContextMenu = null;
        }
    }

    function showCustomContextMenu(e, item) {
        e.preventDefault();
        e.stopPropagation();
        hideContextMenu();

        const convId = item.dataset.conversationId;
        if (!convId) return;

        const isPinned = item.getAttribute('data-is-pinned') === 'true';
        const href = item.getAttribute('href') || `/messaging/conversation/${convId}/`;

        const menu = document.createElement('div');
        menu.className = 'chat-custom-context-menu shadow-lg bg-body rounded-3 py-1';
        menu.style.cssText = `
            position: fixed;
            top: ${Math.min(e.clientY, window.innerHeight - 150)}px;
            left: ${Math.min(e.clientX, window.innerWidth - 180)}px;
            z-index: 10500;
            min-width: 170px;
            border: 1px solid var(--border-color, rgba(0,0,0,0.12));
            backdrop-filter: blur(12px);
        `;

        menu.innerHTML = `
            <a href="${href}" class="dropdown-item py-2 px-3 small d-flex align-items-center gap-2">
                <i aria-hidden="true" data-lucide="external-link" class="vasto-icon text-muted"></i> Open chat
            </a>
            <button type="button" class="dropdown-item py-2 px-3 small d-flex align-items-center gap-2 context-pin-btn">
                <i aria-hidden="true" data-lucide="pin" class="vasto-icon${isPinned ? ' vasto-icon--filled' : ''} text-primary"></i> ${isPinned ? 'Unpin chat' : 'Pin to top'}
            </button>
            <button type="button" class="dropdown-item py-2 px-3 small d-flex align-items-center gap-2 context-read-btn">
                <i aria-hidden="true" data-lucide="check-check" class="vasto-icon text-info"></i> Mark as read
            </button>
        `;

        menu.querySelector('.context-pin-btn').addEventListener('click', (ev) => {
            ev.stopPropagation();
            hideContextMenu();
            togglePin(convId, isPinned ? 'unpin' : 'pin');
        });

        menu.querySelector('.context-read-btn').addEventListener('click', async (ev) => {
            ev.stopPropagation();
            hideContextMenu();
            try {
                await fetch(`/messaging/v1/conversations/${convId}/mark_read/`, {
                    method: 'POST',
                    headers: { 'X-CSRFToken': getCsrfToken() }
                });
                const badge = item.querySelector('.whatsapp-chat-unread-badge, .unread-badge');
                if (badge) badge.style.display = 'none';
                const snippet = item.querySelector('.whatsapp-chat-snippet, .chat-preview-text');
                if (snippet) snippet.classList.remove('unread');
                showToast('Marked as read', 'success');
            } catch (err) {
                console.error('Error marking as read:', err);
            }
        });

        document.body.appendChild(menu);
        activeContextMenu = menu;
    }

    // Mark conversation as read helper
    async function markConversationAsRead(conversationId) {
        if (!conversationId) return;
        const convStr = String(conversationId);

        // Optimistic DOM update across all items representing this conversation
        const items = document.querySelectorAll(`[data-conversation-id="${convStr}"]`);
        items.forEach(item => {
            const wrapper = item.closest('.whatsapp-chat-item-wrapper, .chat-row-wrapper') || item;
            const badge = wrapper.querySelector('.whatsapp-chat-unread-badge, .unread-badge, .conversation-card-badge, .messaging-rail-unread-dot');
            if (badge) {
                badge.style.display = 'none';
                badge.classList.add('d-none');
            }
            const snippet = wrapper.querySelector('.whatsapp-chat-snippet, .chat-preview-text, .conversation-card-preview');
            if (snippet) {
                snippet.classList.remove('fw-bold', 'unread', 'text-body');
                snippet.classList.add('text-muted', 'fw-normal');
            }
        });

        try {
            await fetch(`/messaging/v1/conversations/${convStr}/mark_read/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'X-CSRFToken': getCsrfToken()
                }
            });
            showToast('Marked as read', 'success');
        } catch (err) {
            console.error('[MARK_READ] Error marking conversation as read:', err);
        }
    }

    // Capture-phase listener: intercept pin and mark-read before any anchor clicks or navigation
    function handleItemActionClick(e) {
        const pinBtn = e.target.closest('.pin-toggle-btn, .context-pin-btn');
        if (pinBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
            const convId = pinBtn.dataset.conversationId ||
                           pinBtn.closest('[data-conversation-id]')?.dataset?.conversationId ||
                           pinBtn.closest('.whatsapp-chat-item-wrapper, .chat-row-wrapper')?.querySelector('[data-conversation-id]')?.dataset?.conversationId;
            const action = pinBtn.dataset.action || 'pin';
            togglePin(convId, action);
            hideContextMenu();
            return false;
        }

        const markReadBtn = e.target.closest('.mark-read-btn, .context-read-btn');
        if (markReadBtn) {
            e.preventDefault();
            e.stopPropagation();
            if (typeof e.stopImmediatePropagation === 'function') e.stopImmediatePropagation();
            const convId = markReadBtn.dataset.conversationId ||
                           markReadBtn.closest('[data-conversation-id]')?.dataset?.conversationId ||
                           markReadBtn.closest('.whatsapp-chat-item-wrapper, .chat-row-wrapper')?.querySelector('[data-conversation-id]')?.dataset?.conversationId;
            if (convId) {
                markConversationAsRead(convId);
            }
            hideContextMenu();
            return false;
        }
    }

    // Attach in capture phase (true) so it intercepts ahead of any bubbling links
    document.addEventListener('click', handleItemActionClick, true);

    // Attach global click listeners in bubble phase
    document.addEventListener('click', (e) => {
        handleItemActionClick(e);
        hideContextMenu();
    });

    document.addEventListener('contextmenu', (e) => {
        const item = e.target.closest('.whatsapp-chat-item, .chat-row');
        if (item) {
            showCustomContextMenu(e, item);
        } else {
            hideContextMenu();
        }
    });

    // Cache DOM conversations into offline cache
    async function cacheDOMConversations() {
        if (!window.offlineCache) return;
        try {
            if (!window.offlineCache.db) {
                await window.offlineCache.init();
            }
            const items = document.querySelectorAll('.whatsapp-chat-item, .chat-row');
            const convsToSave = [];
            items.forEach(item => {
                const id = item.dataset.conversationId;
                if (!id) return;
                const name = item.querySelector('.whatsapp-chat-name, .chat-name')?.textContent?.trim() || '';
                const snippet = item.querySelector('.whatsapp-chat-snippet, .chat-preview-text')?.textContent?.trim() || '';
                const isPinned = item.getAttribute('data-is-pinned') === 'true';
                convsToSave.push({
                    id: Number(id) || id,
                    name: name,
                    preview: { text: snippet },
                    is_pinned: isPinned,
                    cached_at: new Date().toISOString()
                });
            });
            if (convsToSave.length > 0 && typeof window.offlineCache.saveConversations === 'function') {
                await window.offlineCache.saveConversations(convsToSave);
            }
        } catch (e) {
            console.warn('[OFFLINE_CACHE] Error auto-caching conversations from list:', e);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', cacheDOMConversations);
    } else {
        cacheDOMConversations();
    }

    // Mark conversation as active, reset unread state to muted, and dismiss OS notifications
    function markConversationAsActive(convId) {
        if (!convId) return;
        const convStr = String(convId);

        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.ready.then(reg => {
                if (reg && reg.active) {
                    reg.active.postMessage({
                        type: 'CLOSE_CHAT_NOTIFICATIONS',
                        payload: {
                            tag: `vasto-chat-${convStr}`,
                            conversation_id: convStr
                        }
                    });
                }
            }).catch(() => {});
        }

        document.querySelectorAll(`[data-conversation-id]`).forEach(item => {
            const rowConvId = String(item.dataset.conversationId || '');
            if (rowConvId === convStr) {
                item.classList.add('active');
                const badge = item.querySelector('.whatsapp-chat-unread-badge, .unread-badge, .messaging-rail-unread-dot');
                if (badge) {
                    badge.style.display = 'none';
                    badge.classList.add('d-none');
                }
                const snippet = item.querySelector('.whatsapp-chat-snippet, .chat-preview-text, .conversation-card-preview');
                if (snippet) {
                    snippet.classList.remove('fw-bold', 'unread', 'text-body');
                    snippet.classList.add('text-muted', 'fw-normal');
                }
            } else if (item.classList.contains('whatsapp-chat-item') || item.classList.contains('chat-row') || item.classList.contains('messaging-rail-avatar-link')) {
                item.classList.remove('active');
            }
        });
    }

    // Dropdown open/close event listeners to elevate wrapper z-index and prevent clipping
    document.addEventListener('show.bs.dropdown', function(e) {
        const row = e.target.closest('.whatsapp-chat-item-wrapper, .whatsapp-chat-item, .chat-row');
        if (row) row.classList.add('dropdown-open');
    });

    document.addEventListener('hidden.bs.dropdown', function(e) {
        const row = e.target.closest('.whatsapp-chat-item-wrapper, .whatsapp-chat-item, .chat-row');
        if (row) row.classList.remove('dropdown-open');
    });

    // Immediate click interaction on conversation items in rail
    document.addEventListener('click', (e) => {
        const chatItem = e.target.closest('.whatsapp-chat-item, .chat-row, .messaging-rail-avatar-link');
        if (chatItem && !e.target.closest('.dropdown, .chat-item-dropdown, button, .pin-toggle-btn, .mark-read-btn')) {
            const convId = chatItem.dataset.conversationId;
            if (convId) {
                markConversationAsActive(convId);
            }
        }

        // Dedicated Media button in desktop/mobile header, or 3-dots dropdown View Media
        const mediaBtn = e.target.closest('#headerMediaBtn, #headerMobileMediaBtn, #whatsappMediaRailToggleBtn, #viewMediaBtn, #headerProfileTrigger');
        if (mediaBtn) {
            e.preventDefault();
            e.stopPropagation();
            toggleMediaRail('tab-media-btn');
            return;
        }

        const infoBtn = e.target.closest('#headerInfoTrigger');
        if (infoBtn) {
            e.preventDefault();
            e.stopPropagation();
            toggleMediaRail('tab-media-btn');
            return;
        }

        const closeBtn = e.target.closest('#closeRightRailBtn');
        if (closeBtn) {
            e.preventDefault();
            e.stopPropagation();
            closeMediaRail();
            return;
        }
    });

    let lastMediaRailToggleTime = 0;

    function openMediaRail(tabId) {
        const rightRail = document.getElementById('whatsappRightRail');
        const layout = document.getElementById('whatsappLayout');
        if (!rightRail) return;

        rightRail.classList.remove('d-none');
        rightRail.classList.add('open');
        rightRail.style.setProperty('display', 'flex', 'important');
        if (layout) layout.classList.add('whatsapp-media-rail-open');
        document.body.classList.add('whatsapp-media-rail-open');

        const targetTabId = tabId || 'tab-media-btn';
        const tabEl = document.getElementById(targetTabId);
        if (tabEl) {
            try {
                if (window.bootstrap && bootstrap.Tab) {
                    const tab = bootstrap.Tab.getOrCreateInstance(tabEl);
                    tab.show();
                } else {
                    tabEl.click();
                }
            } catch (e) {
                tabEl.click();
            }
        }

        const mediaType = targetTabId.replace('tab-', '').replace('-btn', '') || 'media';
        loadMediaTab(mediaType);
    }

    function closeMediaRail() {
        const rightRail = document.getElementById('whatsappRightRail');
        const layout = document.getElementById('whatsappLayout');
        if (!rightRail) return;

        rightRail.classList.remove('open');
        rightRail.classList.add('d-none');
        rightRail.style.setProperty('display', 'none', 'important');
        if (layout) layout.classList.remove('whatsapp-media-rail-open');
        document.body.classList.remove('whatsapp-media-rail-open');
    }

    function toggleMediaRail(tabId) {
        const now = Date.now();
        if (now - lastMediaRailToggleTime < 300) {
            return; // Debounce double-clicks and bubbling events
        }
        lastMediaRailToggleTime = now;

        const rightRail = document.getElementById('whatsappRightRail');
        if (!rightRail) return;

        const isOpen = rightRail.classList.contains('open') &&
                       !rightRail.classList.contains('d-none') &&
                       rightRail.style.display !== 'none';
        if (isOpen) {
            closeMediaRail();
        } else {
            openMediaRail(tabId);
        }
    }

    function loadMediaTab(type, force = false) {
        const pane = document.getElementById(`tab-${type}-content`);
        if (!pane) return;

        if (!force && pane.dataset.loaded === 'true') {
            return; // Don't re-fetch already loaded media
        }

        const chatContainer = document.getElementById('chatMainArea') || document.querySelector('[data-conversation-id]');
        const convId = chatContainer?.dataset?.conversationId || document.body.dataset.conversationId;
        if (!convId) return;

        pane.innerHTML = `
            <div class="text-center py-5 text-muted small media-tab-placeholder">
                <div class="spinner-border spinner-border-sm text-primary mb-2" role="status">
                    <span class="visually-hidden">Loading...</span>
                </div>
                <div>Loading ${type}...</div>
            </div>`;

        fetch(`/messaging/conversation/${convId}/media/?type=${type}&page=1`, {
            headers: {
                'X-Requested-With': 'XMLHttpRequest'
            }
        })
        .then(response => {
            if (!response.ok) throw new Error('HTTP error ' + response.status);
            return response.text();
        })
        .then(html => {
            pane.innerHTML = html;
            pane.dataset.loaded = 'true';
            if (window.htmx) {
                window.htmx.process(pane);
            }
        })
        .catch(err => {
            console.error('[MEDIA_RAIL] Failed to load tab ' + type + ':', err);
            pane.innerHTML = `
                <div class="text-center py-5 text-muted small">
                    <i aria-hidden="true" data-lucide="circle-alert" class="vasto-icon text-warning fs-3 d-block mb-2"></i>
                    Failed to load ${type}. <br>
                    <button class="btn btn-sm btn-outline-primary mt-2 rounded-pill px-3" onclick="window.loadMediaTab('${type}', true)">Retry</button>
                </div>`;
        });
    }

    // Lazy load when tabs are switched
    document.addEventListener('shown.bs.tab', function(e) {
        const target = e.target;
        if (!target) return;
        const mediaType = target.dataset?.mediaType || (target.id ? target.id.replace('tab-', '').replace('-btn', '') : null);
        if (mediaType && ['media', 'docs', 'audio', 'links'].includes(mediaType)) {
            loadMediaTab(mediaType);
        }
    });

    // Synchronize active conversation on HTMX swap
    document.addEventListener('htmx:afterSwap', (evt) => {
        const target = evt.detail.target;
        if (target && (target.id === 'chatMainAreaWrapper' || target.closest('#chatMainAreaWrapper'))) {
            const chatMainArea = document.getElementById('chatMainArea');
            const newConvId = chatMainArea?.dataset?.conversationId;
            if (newConvId) {
                markConversationAsActive(newConvId);
            }

            // Reset media tabs for the new conversation
            const rightRail = document.getElementById('whatsappRightRail');
            if (rightRail) {
                rightRail.querySelectorAll('.tab-pane[data-loaded="true"]').forEach(pane => {
                    pane.dataset.loaded = 'false';
                });
                if (rightRail.classList.contains('open')) {
                    const activeTabBtn = rightRail.querySelector('.whatsapp-media-tabs .nav-link.active');
                    const mediaType = activeTabBtn?.dataset?.mediaType || 'media';
                    loadMediaTab(mediaType, true);
                }
            }
        }
    });

    // Reconnection and online retry handler
    window.addEventListener('online', () => {
        console.log('[CONV_REALTIME] Network restored: reconnecting and retrying pending messages');
        if (window.webSocketManager && typeof window.webSocketManager.reconnect === 'function' && !window.webSocketManager.isConnected()) {
            window.webSocketManager.reconnect();
        }
        if (window.messageService && typeof window.messageService.processMessageQueue === 'function') {
            window.messageService.processMessageQueue();
        }
    });

    // Expose functions globally on window
    window.cacheDOMConversations = cacheDOMConversations;
    window.updateConversationInList = updateConversationInList;
    window.togglePinConversation = togglePin;
    window.markConversationAsActive = markConversationAsActive;
    window.openMediaRail = openMediaRail;
    window.closeMediaRail = closeMediaRail;
    window.toggleMediaRail = toggleMediaRail;
    window.loadMediaTab = loadMediaTab;

})();
