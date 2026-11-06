/**
 * MessageRenderer - Pure rendering only
 * NO logic, NO state ownership, NO business decisions
 * Pure DOM manipulation based on provided data
 */

import { formatDateLabel, formatTime, formatPreciseTime, escapeHtml } from '../shared/utils.js?v=20261106-group1';
import { renderMessageStatus } from '../shared/message-status-renderer.js?v=20261106-group1';
import { linkPreviewRenderer } from '../features/link-preview/link-preview-renderer.js?v=20261106-group1';
import { EVENTS } from '../shared/constants.js?v=20261106-group1';
import { eventBus } from '../core/event-bus.js?v=20261106-group1';
import { deviceMediaStore } from '../core/device-media-store.js?v=20261106-group1';

export class MessageRenderer {
    constructor() {
        this.container = null;
        this.currentUserId = null;
        this.lastRenderedCount = 0;
        this.isRendering = false;
        this.typingIndicatorElement = null;
        this.downloadedMediaIds = new Set();
        this.preserveScrollOnPrepend = false;
        this.isGroundedToBottom = true;
        this.isUserScrolledUp = false;
        this.unreadScrolledCount = 0;
        this._scrollGroundingListenersAttached = false;
        this._containerResizeObserver = null;
        this.debugMode = window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1';
    }

    /**
     * Initialize renderer (pure setup)
     */
    init(currentUserId) {
        this._log('RENDERER_INIT');

        this.container = document.getElementById('messagesContainer');
        this.currentUserId = currentUserId;
        this.isGroundedToBottom = true;
        this.isUserScrolledUp = false;
        this.lastRenderedCount = 0;
        this.unreadScrolledCount = 0;

        // Initialize device media store (Capacitor filesystem or IndexedDB)
        deviceMediaStore.init().catch(err => console.warn('[RENDERER] DeviceMediaStore init error:', err));

        // Hydrate in-memory download flags from persisted localStorage so
        // downloaded groups don't revert to the overlay after reload.
        try {
            for (let i = 0; i < localStorage.length; i++) {
                const k = localStorage.key(i);
                if (k && k.indexOf('media_dl_') === 0) {
                    this.downloadedMediaIds.add(k.slice('media_dl_'.length));
                }
            }
        } catch (_) {}

        if (!this.container) {
            console.error('MessageRenderer: Messages container not found');
            return;
        }

        this._attachEmptyStateListeners();
        this._setupDelegatedEventListeners();
        this._setupEvictionListeners();
        this._setupSingleMediaPlaybackCoordinator();
        this._setupScrollGroundingListeners();

        // Listen for live upload progress updates
        eventBus.on(EVENTS.MESSAGE_UPLOAD_PROGRESS, (data) => {
            if (data?.tempId) {
                this._updateUploadProgress(data.tempId, data.progress);
            }
        });

        this._log('RENDERER_INITIALIZED');
    }

    /**
     * Setup user scroll intent detection and automatic bottom grounding
     */
    _setupScrollGroundingListeners() {
        if (!this.container) return;
        if (this._scrollGroundingListenersAttached) return;
        this._scrollGroundingListenersAttached = true;

        let userInteractionActive = false;
        let interactionEndTimeout = null;

        const markUserInteracting = () => {
            userInteractionActive = true;
            if (interactionEndTimeout) clearTimeout(interactionEndTimeout);
            interactionEndTimeout = setTimeout(() => {
                userInteractionActive = false;
            }, 1200);
        };

        // Track intentional user scroll gestures
        this.container.addEventListener('wheel', markUserInteracting, { passive: true });
        this.container.addEventListener('touchstart', markUserInteracting, { passive: true });
        this.container.addEventListener('touchmove', markUserInteracting, { passive: true });
        this.container.addEventListener('pointerdown', markUserInteracting, { passive: true });
        this.container.addEventListener('keydown', (e) => {
            if (['ArrowUp', 'PageUp', 'Home', 'ArrowDown', 'PageDown', 'End'].includes(e.key)) {
                markUserInteracting();
            }
        }, { passive: true });

        // Scroll event: maintain isGroundedToBottom vs isUserScrolledUp
        this.container.addEventListener('scroll', () => {
            const threshold = 180;
            const distanceFromBottom = this.container.scrollHeight - this.container.scrollTop - this.container.clientHeight;

            if (distanceFromBottom <= threshold) {
                // User reached bottom: ground view to bottom!
                this.isGroundedToBottom = true;
                this.isUserScrolledUp = false;
            } else if (userInteractionActive) {
                // User willingly scrolled up: un-ground and preserve reading position
                this.isGroundedToBottom = false;
                this.isUserScrolledUp = true;
            }
            this.updateScrollToBottomButton();
        }, { passive: true });

        // Maintain bottom grounding as images or videos load
        this.container.addEventListener('load', (e) => {
            if (e.target && (e.target.tagName === 'IMG' || e.target.tagName === 'VIDEO')) {
                if (this.isGroundedToBottom && !this.isUserScrolledUp) {
                    this._scrollToBottom(true);
                }
            }
        }, true);

        // Keep pinned to bottom or smoothly anchor visible messages on container resizing (keyboard opening, composer growth)
        if (window.ResizeObserver) {
            try {
                if (this._containerResizeObserver) {
                    this._containerResizeObserver.disconnect();
                }
                let lastClientHeight = this.container.clientHeight;
                this._containerResizeObserver = new ResizeObserver(() => {
                    if (!this.container) return;
                    const newClientHeight = this.container.clientHeight;
                    const deltaH = lastClientHeight - newClientHeight;
                    lastClientHeight = newClientHeight;

                    if (this.isGroundedToBottom && !this.isUserScrolledUp) {
                        this.container.scrollTop = this.container.scrollHeight;
                        this.updateScrollToBottomButton();
                    } else if (deltaH > 0) {
                        // Container shrank (keyboard opened or composer expanded):
                        // Shift scrollTop by deltaH so messages right above the composer glide upward with it
                        this.container.scrollTop += deltaH;
                        this.updateScrollToBottomButton();
                    }
                });
                this._containerResizeObserver.observe(this.container);
            } catch (_) {}
        }
    }

    /**
     * Coordinate single media playback: guarantees only one audio or video can play at a time in the chat
     */
    _setupSingleMediaPlaybackCoordinator() {
        if (this._singleMediaCoordinatorAttached) return;
        this._singleMediaCoordinatorAttached = true;

        document.addEventListener('play', (e) => {
            const activeMedia = e.target;
            if (!(activeMedia instanceof HTMLMediaElement)) return;

            // Pause all other playing audio/video elements across document
            document.querySelectorAll('audio, video').forEach(other => {
                if (other !== activeMedia && !other.paused) {
                    try {
                        other.pause();
                    } catch (_) {}

                    const bubble = other.closest('.voice-note-bubble, .audio-track-bubble, .media-bubble');
                    if (bubble) {
                        const vnIcon = bubble.querySelector('.vn-play-icon');
                        if (vnIcon) vnIcon.classList.replace('bi-pause-fill', 'bi-play-fill');
                        const trackIcon = bubble.querySelector('.track-play-icon');
                        if (trackIcon) trackIcon.classList.replace('bi-pause-fill', 'bi-play-fill');
                    }
                }
            });
        }, true); // Capture phase guarantees early interception
    }

    /**
     * Setup eviction listeners to bring back the download overlay when media is deleted
     */
    _setupEvictionListeners() {
        if (this._evictionListenersAttached) return;
        this._evictionListenersAttached = true;

        const handleEviction = (e) => {
            const rawId = e.detail?.messageId || e.detail?.id;
            if (!rawId || !this.container) return;
            const messageId = String(rawId).replace(/^chat_/, '');
            this.downloadedMediaIds.delete(messageId);
            try {
                localStorage.removeItem(`media_dl_${messageId}`);
            } catch (_) {}

            const msgEl = this.container.querySelector(`[data-message-id="${messageId}"]`);
            if (msgEl) {
                const bubble = msgEl.querySelector('.media-bubble, .voice-note-bubble, .audio-track-bubble') || msgEl;
                if (bubble) {
                    this._restoreDownloadOverlay({ id: messageId }, bubble);
                }
            }
        };

        window.addEventListener('vasto:media-evicted', handleEviction);
        window.addEventListener('vasto:download-deleted', handleEviction);
    }

    /**
     * Set up container-level delegated event listeners for resilient control handling
     */
    _setupDelegatedEventListeners() {
        if (!this.container || this._delegatedListenersAttached) return;
        this._delegatedListenersAttached = true;

        this.container.addEventListener('click', (e) => {
            // Retry button delegation: works for buttons rendered by full
            // render() AND by in-place updateMessageStatuses(), which never
            // re-attaches per-button listeners.
            const retryBtn = e.target.closest('.retry-button');
            if (retryBtn) {
                e.preventDefault();
                e.stopPropagation();
                const messageId = retryBtn.getAttribute('data-message-id');
                this._log('RETRY_BUTTON_CLICKED', { messageId });
                window.dispatchEvent(new CustomEvent('messageRetry', {
                    detail: { messageId }
                }));
                return;
            }

            // Speed button handler delegation
            const speedBtn = e.target.closest('.vn-speed-btn');
            if (speedBtn) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = speedBtn.closest('.voice-note-bubble');
                const audio = bubble?.querySelector('.vn-audio-el');
                const cur = parseFloat(speedBtn.dataset.speed || '1');
                const next = cur === 1 ? 1.5 : (cur === 1.5 ? 2 : 1);
                speedBtn.dataset.speed = String(next);
                speedBtn.textContent = `${next}x`;
                if (audio) {
                    audio.defaultPlaybackRate = next;
                    audio.playbackRate = next;
                }
                return;
            }

            // Play button handler delegation (Voice Note)
            const playBtn = e.target.closest('.vn-play-btn');
            if (playBtn) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = playBtn.closest('.voice-note-bubble');
                const messageId = bubble?.getAttribute('data-message-id') || bubble?.closest('[data-message-id]')?.getAttribute('data-message-id');

                if (bubble && bubble.classList.contains('not-downloaded')) {
                    if (messageId) {
                        this._handleMediaDownload(messageId, bubble);
                    }
                    return;
                }

                const audio = bubble?.querySelector('.vn-audio-el');
                const playIcon = bubble?.querySelector('.vn-play-icon');
                if (!audio) return;

                if (audio.paused) {
                    document.querySelectorAll('.vn-audio-el, .track-audio-el').forEach(other => {
                        if (other !== audio && !other.paused) {
                            other.pause();
                            other.currentTime = 0;
                            const otherBubble = other.closest('.voice-note-bubble, .audio-track-bubble');
                            otherBubble?.querySelector('.vn-play-icon, .track-play-icon')?.classList.replace('bi-pause-fill', 'bi-play-fill');
                            otherBubble?.querySelectorAll('.vn-bar')?.forEach(b => b.classList.remove('is-played'));
                        }
                    });

                    const currentSpeed = parseFloat(bubble?.querySelector('.vn-speed-btn')?.dataset.speed || '1');
                    audio.defaultPlaybackRate = currentSpeed;
                    audio.playbackRate = currentSpeed;

                    audio.play().then(() => {
                        playIcon?.classList.replace('bi-play-fill', 'bi-pause-fill');
                    }).catch(err => console.warn('[VOICE_NOTE] Delegated play blocked:', err));
                } else {
                    audio.pause();
                    playIcon?.classList.replace('bi-pause-fill', 'bi-play-fill');
                }
                return;
            }

            // Waveform scrubber delegation (Voice Note)
            const waveform = e.target.closest('.vn-waveform');
            if (waveform) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = waveform.closest('.voice-note-bubble');
                const messageId = bubble?.getAttribute('data-message-id') || bubble?.closest('[data-message-id]')?.getAttribute('data-message-id');

                if (bubble && bubble.classList.contains('not-downloaded')) {
                    if (messageId) {
                        this._handleMediaDownload(messageId, bubble);
                    }
                    return;
                }

                const audio = bubble?.querySelector('.vn-audio-el');
                if (!audio || !audio.duration) return;

                const rect = waveform.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                audio.currentTime = ratio * audio.duration;
                return;
            }

            // Audio track play button delegation
            const trackPlayBtn = e.target.closest('.audio-track-play-btn');
            if (trackPlayBtn) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = trackPlayBtn.closest('.audio-track-bubble');
                const messageId = bubble?.getAttribute('data-message-id') || bubble?.closest('[data-message-id]')?.getAttribute('data-message-id');

                if (bubble && bubble.classList.contains('not-downloaded')) {
                    if (messageId) {
                        this._handleMediaDownload(messageId, bubble);
                    }
                    return;
                }

                const audio = bubble?.querySelector('.track-audio-el');
                const playIcon = bubble?.querySelector('.track-play-icon');
                if (!audio) return;

                if (audio.paused) {
                    document.querySelectorAll('.vn-audio-el, .track-audio-el').forEach(other => {
                        if (other !== audio && !other.paused) {
                            other.pause();
                            other.currentTime = 0;
                            const otherBubble = other.closest('.voice-note-bubble, .audio-track-bubble');
                            otherBubble?.querySelector('.vn-play-icon, .track-play-icon')?.classList.replace('bi-pause-fill', 'bi-play-fill');
                        }
                    });

                    audio.play().then(() => {
                        playIcon?.classList.replace('bi-play-fill', 'bi-pause-fill');
                    }).catch(err => console.warn('[AUDIO_TRACK] Delegated play blocked:', err));
                } else {
                    audio.pause();
                    playIcon?.classList.replace('bi-pause-fill', 'bi-play-fill');
                }
                return;
            }

            // Audio track scrubber track delegation
            const scrubberTrack = e.target.closest('.audio-track-scrubber-track');
            if (scrubberTrack) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = scrubberTrack.closest('.audio-track-bubble');
                const messageId = bubble?.getAttribute('data-message-id') || bubble?.closest('[data-message-id]')?.getAttribute('data-message-id');

                if (bubble && bubble.classList.contains('not-downloaded')) {
                    if (messageId) {
                        this._handleMediaDownload(messageId, bubble);
                    }
                    return;
                }

                const audio = bubble?.querySelector('.track-audio-el');
                if (!audio || !audio.duration) return;

                const rect = scrubberTrack.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                audio.currentTime = ratio * audio.duration;
                return;
            }

            // Media on-demand download button delegation
            const dlBtn = e.target.closest('.media-download-pill, .media-download-overlay');
            if (dlBtn) {
                e.preventDefault();
                e.stopPropagation();
                const bubble = dlBtn.closest('.media-bubble, .voice-note-bubble, .audio-track-bubble');
                const messageId = dlBtn.getAttribute('data-message-id') || bubble?.getAttribute('data-message-id') || bubble?.closest('[data-message-id]')?.getAttribute('data-message-id');
                if (messageId && bubble) {
                    this._handleMediaDownload(messageId, bubble);
                }
                return;
            }
        });
    }

    /**
     * Check if media is downloaded locally in IndexedDB / Filesystem
     */
    _isMediaDownloaded(messageId) {
        if (!messageId) return false;
        if (this.downloadedMediaIds.has(String(messageId))) return true;
        try {
            if (localStorage.getItem(`media_dl_${messageId}`) === 'true') {
                this.downloadedMediaIds.add(String(messageId));
                return true;
            }
        } catch (_) {}
        return deviceMediaStore.hasMedia(messageId);
    }

    /**
     * Update live upload progress in DOM
     */
    _updateUploadProgress(tempId, progress) {
        if (!this.container) return;
        const msgEl = this.container.querySelector(`[data-message-id="${tempId}"]`);
        if (!msgEl) return;
        const overlay = msgEl.querySelector('.media-upload-overlay');
        if (!overlay) return;
        const circleBar = overlay.querySelector('.upload-circle-bar');
        const textEl = overlay.querySelector('.upload-progress-text');
        if (circleBar) {
            const circumference = 94.25;
            const offset = Math.max(0, circumference * (1 - progress / 100));
            circleBar.style.strokeDashoffset = offset.toFixed(1);
        }
        if (textEl) {
            textEl.textContent = `${progress}%`;
        }
        if (progress >= 100) {
            overlay.classList.add('upload-complete');
        }
    }

    /**
     * Restore download overlay and not-downloaded state if media was deleted from IndexedDB/Filesystem
     */
    _restoreDownloadOverlay(message, bubble) {
        if (!bubble) return;
        // Allow sender bubbles too so evicted media restores consistently; sender just skips blur via CSS.
        const isOwn = message?.isOwn || bubble.classList.contains('sent');
        const messageId = message?.id || bubble.getAttribute('data-message-id');
        if (messageId) {
            this.downloadedMediaIds.delete(String(messageId));
            try {
                localStorage.removeItem(`media_dl_${messageId}`);
            } catch (_) {}
        }

        bubble.classList.add('not-downloaded');
        bubble.classList.remove('is-downloading');
        delete bubble.dataset.isDownloading;

        const isVoiceNote = bubble.classList.contains('voice-note-bubble');
        const isAudioTrack = bubble.classList.contains('audio-track-bubble');
        const isMediaGroup = bubble.classList.contains('media-group');

        if (isVoiceNote) {
            const audio = bubble.querySelector('.vn-audio-el');
            if (audio) {
                const curSrc = audio.getAttribute('src');
                if (curSrc && !curSrc.startsWith('blob:') && !audio.getAttribute('data-src')) {
                    audio.setAttribute('data-src', curSrc);
                }
                audio.removeAttribute('src');
                try { audio.pause(); audio.currentTime = 0; } catch (_) {}
            }
            const playBtn = bubble.querySelector('.vn-play-btn');
            if (playBtn) {
                playBtn.classList.add('vn-download-mode');
                playBtn.setAttribute('aria-label', 'Download voice note');
                playBtn.setAttribute('title', 'Download voice note');
                playBtn.innerHTML = `<i class="bi bi-arrow-down vn-play-icon"></i>`;
            }
            const timer = bubble.querySelector('.vn-timer');
            if (timer) timer.textContent = '0:00';
            const bars = bubble.querySelectorAll('.vn-bar');
            bars.forEach(b => b.classList.remove('is-played'));
            return;
        }

        if (isAudioTrack) {
            const audio = bubble.querySelector('.track-audio-el');
            if (audio) {
                const curSrc = audio.getAttribute('src');
                if (curSrc && !curSrc.startsWith('blob:') && !audio.getAttribute('data-src')) {
                    audio.setAttribute('data-src', curSrc);
                }
                audio.removeAttribute('src');
                try { audio.pause(); audio.currentTime = 0; } catch (_) {}
            }
            const playBtn = bubble.querySelector('.audio-track-play-btn');
            if (playBtn) {
                playBtn.classList.add('audio-download-mode');
                playBtn.setAttribute('aria-label', 'Download track');
                playBtn.setAttribute('title', 'Download track');
                playBtn.innerHTML = `<i class="bi bi-arrow-down track-play-icon"></i>`;
            }
            const fill = bubble.querySelector('.audio-track-scrubber-fill');
            if (fill) fill.style.width = '0%';
            const timer = bubble.querySelector('.audio-track-time');
            if (timer) timer.textContent = '0:00';
            const metaRow = bubble.querySelector('.audio-track-meta-row');
            if (metaRow && !bubble.querySelector('.audio-track-status')) {
                const statusSpan = document.createElement('span');
                statusSpan.className = 'audio-track-status text-primary ms-1';
                statusSpan.textContent = '• Tap to download';
                metaRow.appendChild(statusSpan);
            }
            return;
        }

        if (isMediaGroup) {
            bubble.querySelectorAll('.media-tile').forEach(tile => {
                const img = tile.querySelector('img');
                const vid = tile.querySelector('video');
                if (img) {
                    const curSrc = img.getAttribute('src');
                    if (curSrc && !curSrc.startsWith('blob:') && !img.getAttribute('data-src')) {
                        img.setAttribute('data-src', curSrc);
                    }
                    img.style.filter = 'blur(14px) brightness(0.72)';
                    img.style.transform = 'scale(1.08)';
                    img.style.borderRadius = '8px';
                    img.classList.remove('d-none');
                    tile.querySelector('.tile-placeholder-wrap')?.remove();
                }
                if (vid) {
                    const curSrc = vid.getAttribute('src');
                    if (curSrc && !curSrc.startsWith('blob:') && !vid.getAttribute('data-src')) {
                        vid.setAttribute('data-src', curSrc);
                    }
                    const cleanSrc = vid.getAttribute('data-src') || curSrc;
                    if (cleanSrc) {
                        vid.src = `${cleanSrc.split('#')[0]}#t=0.001`;
                    }
                    vid.classList.remove('d-none');
                    vid.style.filter = 'blur(14px) brightness(0.72)';
                    vid.style.transform = 'scale(1.08)';
                    vid.style.borderRadius = '8px';
                    try { vid.pause(); } catch (_) {}
                }
            });

            if (!bubble.querySelector('.group-download-overlay')) {
                const downloadOverlay = document.createElement('div');
                downloadOverlay.className = 'media-download-overlay group-download-overlay';
                downloadOverlay.setAttribute('data-message-id', messageId);
                downloadOverlay.innerHTML = `
                    <button type="button" class="media-download-pill group-download-pill" title="Download bundle">
                        <div class="download-icon-wrap">
                            <svg class="dl-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                                <polyline points="7 10 12 15 17 10"></polyline>
                                <line x1="12" y1="15" x2="12" y2="3"></line>
                            </svg>
                            <svg class="download-spinner d-none" width="22" height="22" viewBox="0 0 38 38">
                                <circle cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"/>
                                <circle class="dl-spinner-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="3" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="60"/>
                            </svg>
                        </div>
                        <div class="group-dl-text">
                            <span class="group-dl-label">Download all</span>
                            <span class="media-download-size">Media</span>
                        </div>
                    </button>
                `;
                const targetFrame = bubble.querySelector('.media-card-frame') || bubble;
                targetFrame.appendChild(downloadOverlay);
            }
            return;
        }

        // Single media (image/video)
        const targetFrame = bubble.querySelector('.media-card-frame') || bubble;
        const img = bubble.querySelector('img');
        const vid = bubble.querySelector('video');
        if (img) {
            const curSrc = img.getAttribute('src');
            if (curSrc && !curSrc.startsWith('blob:') && !img.getAttribute('data-src')) {
                img.setAttribute('data-src', curSrc);
            }
            img.style.filter = 'blur(14px) brightness(0.72)';
            img.style.transform = 'scale(1.04)';
            img.style.borderRadius = '12px';
            img.classList.remove('d-none');
            targetFrame.querySelector('.media-image-placeholder')?.remove();
        }
        if (vid) {
            const curSrc = vid.getAttribute('src');
            if (curSrc && !curSrc.startsWith('blob:') && !vid.getAttribute('data-src')) {
                vid.setAttribute('data-src', curSrc);
            }
            const cleanSrc = vid.getAttribute('data-src') || curSrc;
            if (cleanSrc) {
                vid.src = `${cleanSrc.split('#')[0]}#t=0.001`;
            }
            vid.classList.remove('d-none');
            vid.style.filter = 'blur(14px) brightness(0.72)';
            vid.style.transform = 'scale(1.08)';
            vid.style.borderRadius = '12px';
            try { vid.pause(); } catch (_) {}
        }

        if (!bubble.querySelector('.media-download-overlay')) {
            const downloadOverlay = document.createElement('div');
            downloadOverlay.className = 'media-download-overlay';
            downloadOverlay.setAttribute('data-message-id', messageId);
            downloadOverlay.innerHTML = `
                <button type="button" class="media-download-pill" title="Download media">
                    <div class="download-icon-wrap">
                        <svg class="dl-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        <svg class="download-spinner d-none" width="20" height="20" viewBox="0 0 38 38">
                            <circle cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"/>
                            <circle class="dl-spinner-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="3" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="60"/>
                        </svg>
                    </div>
                    <span class="media-download-size">Download</span>
                </button>
            `;
            targetFrame.appendChild(downloadOverlay);
        }
    }

    /**
     * Handle on-demand download for received media
     */
    async _handleMediaDownload(messageId, bubble) {
        if (!bubble || bubble.dataset.isDownloading === 'true') return;
        bubble.dataset.isDownloading = 'true';

        const isVoiceNote = bubble.classList.contains('voice-note-bubble');
        const isAudioTrack = bubble.classList.contains('audio-track-bubble');

        let pill = null;
        let dlIcon = null;
        let spinner = null;
        let sizeText = null;

        if (isVoiceNote) {
            const playBtn = bubble.querySelector('.vn-play-btn');
            if (playBtn) {
                playBtn.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" style="width: 1rem; height: 1rem; border-width: 2px;"></span>`;
            }
            const timer = bubble.querySelector('.vn-timer');
            if (timer) timer.textContent = 'Saving...';
        } else if (isAudioTrack) {
            const playBtn = bubble.querySelector('.audio-track-play-btn');
            if (playBtn) {
                playBtn.innerHTML = `<span class="spinner-border spinner-border-sm" role="status" style="width: 1rem; height: 1rem; border-width: 2px;"></span>`;
            }
            const timeEl = bubble.querySelector('.audio-track-time');
            if (timeEl) timeEl.textContent = 'Saving...';
        } else {
            pill = bubble.querySelector('.media-download-pill');
            dlIcon = pill?.querySelector('.dl-icon');
            spinner = pill?.querySelector('.download-spinner');
            sizeText = pill?.querySelector('.media-download-size');

            if (dlIcon) dlIcon.classList.add('d-none');
            if (spinner) spinner.classList.remove('d-none');
            if (sizeText) sizeText.textContent = '0%';
        }

        const onProgress = (percent) => {
            if (isVoiceNote) {
                const timer = bubble.querySelector('.vn-timer');
                if (timer) timer.textContent = `${percent}%`;
            } else if (isAudioTrack) {
                const timeEl = bubble.querySelector('.audio-track-time');
                if (timeEl) timeEl.textContent = `${percent}%`;
            } else {
                if (sizeText) sizeText.textContent = `${percent}%`;
                if (spinner) {
                    const spinnerBar = spinner.querySelector('.dl-spinner-bar');
                    if (spinnerBar) {
                        const circumference = 94.25;
                        const offset = circumference * (1 - percent / 100);
                        spinnerBar.style.strokeDashoffset = Math.max(0, offset).toFixed(1);
                    }
                }
            }
        };

        try {
            const imgs = Array.from(bubble.querySelectorAll('img:not(.read-avatar-img)'));
            const vids = Array.from(bubble.querySelectorAll('video'));
            const auds = Array.from(bubble.querySelectorAll('audio'));
            const mediaEls = [...imgs, ...vids, ...auds];

            if (mediaEls.length === 0) {
                throw new Error('No media elements found in bubble');
            }

            // Save all media elements permanently to IndexedDB / Capacitor storage.
            // Each tile is independent: already-local tiles are skipped, a
            // single tile failure no longer aborts the rest, and tapping
            // again resumes only what is still missing.
            let failedCount = 0;
            for (let i = 0; i < mediaEls.length; i++) {
                const el = mediaEls[i];
                const src = el.getAttribute('data-full-src') || el.getAttribute('data-src') || el.getAttribute('src');
                if (!src) continue;

                const cleanSrc = src.split('#')[0];
                if (!cleanSrc.startsWith('blob:') && !cleanSrc.startsWith('capacitor:') && !cleanSrc.startsWith('http://localhost/_capacitor_file_')) {
                    const filename = cleanSrc.split('/').pop()?.split('?')[0] || `media_${messageId}`;
                    const mediaKey = mediaEls.length > 1 ? `${messageId}_${i}` : messageId;

                    // Resume: skip tiles already stored locally.
                    try {
                        if (deviceMediaStore.hasMedia(mediaKey)) {
                            const existing = await deviceMediaStore.getLocalMediaUrl(mediaKey);
                            if (existing) {
                                el.src = existing;
                                el.removeAttribute('data-full-src');
                                el.removeAttribute('data-src');
                                continue;
                            }
                        }
                    } catch (_) {}

                    try {
                        const localUrl = await deviceMediaStore.downloadMedia(mediaKey, cleanSrc, filename, onProgress);
                        if (!localUrl) {
                            throw new Error('Failed to obtain local media URL from IndexedDB');
                        }

                        el.src = localUrl;
                        el.removeAttribute('data-full-src');
                        el.removeAttribute('data-src');
                    } catch (tileErr) {
                        console.error('[MEDIA_DOWNLOAD] Tile failed, continuing with rest:', tileErr);
                        failedCount++;
                        continue;
                    }

                    if (el.tagName === 'IMG') {
                        el.style.filter = 'none';
                        el.style.transform = 'none';
                        el.classList.remove('not-downloaded-thumb');
                        el.classList.remove('d-none');
                        const placeholder = bubble.querySelector('.media-image-placeholder, .tile-placeholder-wrap');
                        if (placeholder) placeholder.remove();
                    }
                    if (el.tagName === 'AUDIO') {
                        el.load();
                    }
                    if (el.tagName === 'VIDEO') {
                        el.classList.remove('d-none');
                        el.style.filter = 'none';
                        el.style.transform = 'none';
                        el.load();
                    }
                }
            }

            // Partial failure: keep the bundle overlay as the retry affordance
            // for the remaining tiles instead of leaving a dead spinner.
            // Tapping again resumes only what is still missing (see skip above).
            if (failedCount > 0) {
                delete bubble.dataset.isDownloading;
                try {
                    if (dlIcon) dlIcon.classList.remove('d-none');
                    if (spinner) spinner.classList.add('d-none');
                    if (sizeText) sizeText.textContent = `Tap to retry (${failedCount} left)`;
                    const groupLabel = bubble.querySelector('.group-dl-label');
                    if (groupLabel) groupLabel.textContent = `Download remaining (${failedCount})`;
                } catch (_) {}
                return;
            }

            // Clean up any remaining blurred thumbnail classes without duplicating elements
            const thumbImgs = bubble.querySelectorAll('.not-downloaded-thumb, .tile-image-thumb, .single-media-video-thumb, .tile-video-thumb');
            thumbImgs.forEach(t => {
                t.style.filter = 'none';
                t.style.transform = 'none';
                t.classList.remove('not-downloaded-thumb');
            });
            const videoEls = bubble.querySelectorAll('video');
            videoEls.forEach(v => {
                v.classList.remove('d-none');
                v.style.filter = 'none';
                v.style.transform = 'none';
            });

            // Mark as downloaded locally
            this.downloadedMediaIds.add(String(messageId));
            try {
                localStorage.setItem(`media_dl_${messageId}`, 'true');
            } catch (_) {}

            // Remove download overlay immediately
            const overlay = bubble.querySelector('.media-download-overlay, .group-download-overlay');
            if (overlay) {
                overlay.remove();
            }

            // Smooth reveal
            bubble.classList.remove('not-downloaded');
            delete bubble.dataset.isDownloading;

            if (isVoiceNote) {
                const playBtn = bubble.querySelector('.vn-play-btn');
                if (playBtn) {
                    playBtn.classList.remove('vn-download-mode');
                    playBtn.setAttribute('aria-label', 'Play voice note');
                    playBtn.setAttribute('title', 'Play voice note');
                    playBtn.innerHTML = `<i class="bi bi-play-fill vn-play-icon"></i>`;
                }
                const timer = bubble.querySelector('.vn-timer');
                if (timer) timer.textContent = '0:00';
                const sizeIndicator = bubble.querySelector('.vn-size-indicator');
                const vnDot = bubble.querySelector('.vn-dot');
                if (sizeIndicator) sizeIndicator.remove();
                if (vnDot) vnDot.remove();

                // Automatically start playback from IndexedDB
                const audio = bubble.querySelector('.vn-audio-el');
                if (audio) {
                    try {
                        const playIcon = playBtn?.querySelector('.vn-play-icon');
                        audio.play().then(() => {
                            playIcon?.classList.replace('bi-play-fill', 'bi-pause-fill');
                        }).catch(e => console.warn('[VOICE_NOTE] Autoplay blocked after download:', e));
                    } catch (_) {}
                }
            } else if (isAudioTrack) {
                const playBtn = bubble.querySelector('.audio-track-play-btn');
                if (playBtn) {
                    playBtn.classList.remove('audio-download-mode');
                    playBtn.setAttribute('aria-label', 'Play track');
                    playBtn.setAttribute('title', 'Play track');
                    playBtn.innerHTML = `<i class="bi bi-play-fill track-play-icon"></i>`;
                }
                const timeEl = bubble.querySelector('.audio-track-time');
                if (timeEl) timeEl.textContent = '0:00';
                const statusEl = bubble.querySelector('.audio-track-status');
                if (statusEl) statusEl.remove();

                // Automatically start playback from IndexedDB
                const audio = bubble.querySelector('.track-audio-el');
                if (audio) {
                    try {
                        const playIcon = playBtn?.querySelector('.track-play-icon');
                        audio.play().then(() => {
                            playIcon?.classList.replace('bi-play-fill', 'bi-pause-fill');
                        }).catch(e => console.warn('[AUDIO_TRACK] Autoplay blocked after download:', e));
                    } catch (_) {}
                }
            } else {
                const overlay = bubble.querySelector('.media-download-overlay');
                if (overlay) {
                    overlay.style.opacity = '0';
                    setTimeout(() => overlay.remove(), 250);
                }
            }
        } catch (err) {
            console.error('[MEDIA_DOWNLOAD] Failed:', err);
            delete bubble.dataset.isDownloading;

            if (isVoiceNote) {
                const playBtn = bubble.querySelector('.vn-play-btn');
                if (playBtn) {
                    playBtn.innerHTML = `<i class="bi bi-arrow-clockwise vn-play-icon"></i>`;
                }
                const timer = bubble.querySelector('.vn-timer');
                if (timer) timer.textContent = 'Retry';
            } else if (isAudioTrack) {
                const playBtn = bubble.querySelector('.audio-track-play-btn');
                if (playBtn) {
                    playBtn.innerHTML = `<i class="bi bi-arrow-clockwise track-play-icon"></i>`;
                }
                const timeEl = bubble.querySelector('.audio-track-time');
                if (timeEl) timeEl.textContent = 'Retry';
            } else {
                if (dlIcon) {
                    dlIcon.innerHTML = `<i class="bi bi-arrow-clockwise" style="font-size: 1.1rem;"></i>`;
                    dlIcon.classList.remove('d-none');
                }
                if (spinner) spinner.classList.add('d-none');
                if (sizeText) sizeText.textContent = 'Retry';
            }
        }
    }

    /**
     * Show WhatsApp-style typing indicator bubble with avatar and pulsing dots
     * @param {string} username - Username of person typing
     * @param {number|string} userId - User ID of person typing
     */
    showTypingIndicator(username, userId = null) {
        this._log('SHOW_TYPING_INDICATOR', { username, userId });

        if (!this.container) return;

        // If indicator already exists in DOM, verify type or reuse
        let existing = document.getElementById('typingIndicator');
        if (existing) {
            if (existing.querySelector('.typing-bubble')) {
                this.typingIndicatorElement = existing;
                if (this.container.lastElementChild !== existing) {
                    this.container.appendChild(existing);
                }
                return;
            }
            // Was recording bubble, remove to replace with typing bubble
            existing.remove();
        }

        // Create WhatsApp-style typing indicator wrapper
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = 'message-wrapper received-wrapper typing-indicator-wrapper';
        wrapperDiv.id = 'typingIndicator';

        // Avatar container matching received bubble architecture
        const avatarContainer = document.createElement('div');
        avatarContainer.className = 'message-avatar-container';
        const avatarUrl = this._getSenderAvatar(userId);
        const avatarImg = document.createElement('img');
        avatarImg.className = 'message-avatar';
        avatarImg.src = avatarUrl;
        avatarImg.alt = username || 'Typing';
        avatarContainer.appendChild(avatarImg);
        wrapperDiv.appendChild(avatarContainer);

        // Content wrapper containing typing bubble
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble received typing-bubble';

        // Three pulsing / bouncing WhatsApp dots
        bubble.innerHTML = `
            <div class="typing-dots">
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
                <span class="typing-dot"></span>
            </div>
        `;

        contentWrapper.appendChild(bubble);
        wrapperDiv.appendChild(contentWrapper);

        this.typingIndicatorElement = wrapperDiv;

        // Add to container at the end
        this.container.appendChild(this.typingIndicatorElement);

        // Auto-scroll if grounded to bottom and user hasn't scrolled up
        if (this.isGroundedToBottom && !this.isUserScrolledUp) {
            this.container.scrollTop = this.container.scrollHeight;
        }
    }

    /**
     * Show WhatsApp-style recording indicator bubble with avatar, pulsing mic, and pulsing text
     * @param {string} username - Username of person recording
     * @param {number|string} userId - User ID of person recording
     */
    showRecordingIndicator(username, userId = null) {
        this._log('SHOW_RECORDING_INDICATOR', { username, userId });

        if (!this.container) return;

        // If indicator already exists in DOM, verify type or reuse
        let existing = document.getElementById('typingIndicator');
        if (existing) {
            if (existing.querySelector('.recording-bubble')) {
                this.typingIndicatorElement = existing;
                if (this.container.lastElementChild !== existing) {
                    this.container.appendChild(existing);
                }
                return;
            }
            // Was typing bubble, remove to replace with recording bubble
            existing.remove();
        }

        // Create WhatsApp-style recording indicator wrapper
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = 'message-wrapper received-wrapper typing-indicator-wrapper';
        wrapperDiv.id = 'typingIndicator';

        // Avatar container matching received bubble architecture
        const avatarContainer = document.createElement('div');
        avatarContainer.className = 'message-avatar-container';
        const avatarUrl = this._getSenderAvatar(userId);
        const avatarImg = document.createElement('img');
        avatarImg.className = 'message-avatar';
        avatarImg.src = avatarUrl;
        avatarImg.alt = username || 'Recording';
        avatarContainer.appendChild(avatarImg);
        wrapperDiv.appendChild(avatarContainer);

        // Content wrapper containing recording bubble
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        const bubble = document.createElement('div');
        bubble.className = 'message-bubble received recording-bubble';

        // Pulsing red mic + pulsing text
        bubble.innerHTML = `
            <div class="recording-indicator-content">
                <span class="recording-mic-pulse"><i class="bi bi-mic-fill"></i></span>
                <span class="recording-text-pulse">Recording voice note...</span>
            </div>
        `;

        contentWrapper.appendChild(bubble);
        wrapperDiv.appendChild(contentWrapper);

        this.typingIndicatorElement = wrapperDiv;

        // Add to container at the end
        this.container.appendChild(this.typingIndicatorElement);

        // Auto-scroll if grounded to bottom and user hasn't scrolled up
        if (this.isGroundedToBottom && !this.isUserScrolledUp) {
            this.container.scrollTop = this.container.scrollHeight;
        }
    }

    /**
     * Hide typing indicator
     */
    hideTypingIndicator() {
        this._log('HIDE_TYPING_INDICATOR');

        if (this.typingIndicatorElement) {
            this.typingIndicatorElement.remove();
            this.typingIndicatorElement = null;
        }

        // Also remove any existing indicator from DOM
        const existing = document.getElementById('typingIndicator');
        if (existing) {
            existing.remove();
        }
    }

    /**
     * Render messages (pure rendering only)
     * @param {Array} messages - Messages array from store (canonical schema)
     */
    render(messages) {
        this.senderProfiles = new Map(messages.map(message => [Number(message.senderId), message.metadata?.sender || {}]));
        console.log("[RENDERER] RENDER CALLED - messages count:", messages?.length);
        
        if (this.isRendering) {
            console.log("[RENDERER] Already rendering, queueing latest messages");
            this._pendingRenderMessages = messages;
            return;
        }

        this.isRendering = true;

        try {
            if (!this.container) {
                console.error('[RENDERER] ERROR: Container not initialized');
                return;
            }

            console.log("[RENDERER] Starting render with", messages?.length || 0, "messages");

            if (!messages || messages.length === 0) {
                const storeState = window.__store?.getState?.();
                if (storeState && storeState.isInitialHistoryLoaded === false) {
                    console.log("[RENDERER] Initial history loading in progress, deferring empty state");
                    return;
                }
                this.container.innerHTML = '';
                this._renderEmptyState();
                this.lastRenderedCount = 0;
                return;
            }

            // Remove empty state if present
            const emptyEl = this.container.querySelector('.empty-chat-placeholder, .empty-state-container, #messagingEmptyState, .messaging-empty-state, .pwanimate-empty-state');
            if (emptyEl) {
                emptyEl.remove();
            }

            // Record scroll anchor metrics before DOM changes
            const prevScrollHeight = this.container.scrollHeight;
            const prevScrollTop = this.container.scrollTop;
            const threshold = 180;
            const wasNearBottom = (prevScrollHeight - prevScrollTop - this.container.clientHeight) < threshold;

            const viewItems = this._buildView(messages || []);
            console.log("[RENDERER] Built view items:", viewItems?.length);

            // Index existing elements in container by their unique identity key
            const existingElements = new Map();
            for (const child of Array.from(this.container.children)) {
                if (child.id === 'typingIndicator' || child.classList.contains('typing-indicator-container')) {
                    continue; // Skip typing indicator, it's managed separately
                }
                const msgId = child.getAttribute('data-message-id');
                if (msgId) {
                    existingElements.set(`msg_${msgId}`, child);
                } else if (child.classList.contains('date-separator')) {
                    const label = child.querySelector('span')?.textContent?.trim() || child.textContent?.trim();
                    if (label) existingElements.set(`date_${label}`, child);
                } else if (child.classList.contains('time-separator')) {
                    const label = child.querySelector('.time-label')?.textContent?.trim() || child.textContent?.trim();
                    if (label) existingElements.set(`time_${label}`, child);
                }
            }

            const targetElements = [];
            for (const item of viewItems || []) {
                const viewType = item?.viewType;
                let key = null;
                if (viewType === 'date') {
                    key = `date_${item.label}`;
                } else if (viewType === 'time') {
                    key = `time_${item.label}`;
                } else if (viewType === 'message') {
                    key = `msg_${item.id}`;
                }

                let el = key ? existingElements.get(key) : null;
                if (!el && viewType === 'message' && item.metadata?.temp_id) {
                    const tempKey = `msg_${item.metadata.temp_id}`;
                    const tempEl = existingElements.get(tempKey);
                    if (tempEl) {
                        existingElements.delete(tempKey);
                        this.rekeyMessageElement(item.metadata.temp_id, item.id);
                        el = tempEl;
                    }
                }

                if (el) {
                    existingElements.delete(key);
                    // Update existing element attributes/grouping in-place without rebuilding
                    if (viewType === 'message') {
                        const updatedEl = this._updateExistingMessageElement(el, item);
                        if (updatedEl) el = updatedEl;
                    }
                } else {
                    // Create new element
                    if (viewType === 'date') {
                        el = this._createDateElement(item.label);
                    } else if (viewType === 'time') {
                        el = this._createTimeElement(item.label, item.preciseLabel);
                    } else if (viewType === 'message') {
                        try {
                            el = this._createMessageElement(item);
                        } catch (err) {
                            console.error("[RENDERER] ERROR creating message:", err, item);
                        }
                    }
                }

                if (el) {
                    targetElements.push(el);
                }
            }

            // Remove elements that are no longer part of viewItems (e.g., deleted messages)
            for (const [_, staleEl] of existingElements) {
                staleEl.remove();
            }

            // Reconcile DOM child order with minimal mutations:
            // Only move nodes if their current position in container differs!
            let cursor = this.container.firstElementChild;
            for (const targetEl of targetElements) {
                while (cursor && (cursor.id === 'typingIndicator' || cursor.classList.contains('typing-indicator-container'))) {
                    cursor = cursor.nextElementSibling;
                }

                if (cursor === targetEl) {
                    cursor = cursor.nextElementSibling;
                } else {
                    this.container.insertBefore(targetEl, cursor);
                }
            }

            console.log("[RENDERER] Reconciliation complete. Container children:", this.container?.children?.length);

            // If typing indicator is present in DOM, ensure it stays at the very bottom
            const typingEl = document.getElementById('typingIndicator');
            if (typingEl && this.container.lastElementChild !== typingEl) {
                this.container.appendChild(typingEl);
            }

            if (this.preserveScrollOnPrepend) {
                const heightDiff = this.container.scrollHeight - prevScrollHeight;
                this.container.scrollTop = prevScrollTop + heightDiff;
                this.preserveScrollOnPrepend = false;
            } else if (this.isGroundedToBottom || !this.isUserScrolledUp || this.lastRenderedCount === 0) {
                // Default grounded state: keep bottom pinned unless user willingly scrolled up
                this._scrollToBottom(true);
                this.updateScrollToBottomButton();
            } else {
                // User has deliberately scrolled up reading earlier messages
                const isNewCount = messages.length > this.lastRenderedCount;
                if (isNewCount) {
                    const lastMsg = messages[messages.length - 1];
                    const isFromMe = (lastMsg && (lastMsg.sender_id === this.currentUserId || lastMsg.senderId === this.currentUserId || lastMsg.isOptimistic));
                    if (isFromMe) {
                        this._scrollToBottom(true);
                    } else {
                        this.handleIncomingMessageScroll(false);
                    }
                } else {
                    this.container.scrollTop = prevScrollTop;
                    this.updateScrollToBottomButton();
                }
            }
            this.lastRenderedCount = messages.length;

            // NOTE: retry buttons are handled by the container-level delegated
            // listener in _setupDelegatedEventListeners (single attach in
            // init). Do NOT per-button attach here: it double-fires retries
            // and misses buttons added by in-place updateMessageStatuses.

        } finally {
            this.isRendering = false;
            if (this._pendingRenderMessages) {
                const nextMessages = this._pendingRenderMessages;
                this._pendingRenderMessages = null;
                this.render(nextMessages);
            }
        }
    }

    /**
     * Enable scroll preservation anchor for older message prepending
     * @param {boolean} preserve
     */
    setPreserveScrollOnPrepend(preserve = true) {
        this.preserveScrollOnPrepend = preserve;
    }

    /**
     * Transition media, voice note, audio track, and document bubbles out of uploading spinner state in-place
     */
    _reconcileMediaUploadCompletion(wrapperEl, message) {
        if (!wrapperEl || !message) return;
        const isUploading = message.status === 'uploading' || message.status === 'sending' || Boolean(message.isOptimistic);
        if (isUploading) return;

        const bubble = wrapperEl.querySelector('.message-bubble') || wrapperEl;
        const mediaUrl = message.metadata?.url || message.attachments?.[0]?.file_url || message.metadata?.localBlobUrl || '';

        // 1. Voice Note bubble: replace spinner with play button icon and restore duration timer
        if (bubble.classList.contains('voice-note-bubble') || wrapperEl.querySelector('.voice-note-bubble')) {
            const vnBubble = bubble.classList.contains('voice-note-bubble') ? bubble : wrapperEl.querySelector('.voice-note-bubble');
            const playBtn = vnBubble.querySelector('.vn-play-btn');
            if (playBtn && (playBtn.querySelector('.spinner-border') || !playBtn.querySelector('.vn-play-icon'))) {
                playBtn.innerHTML = `<i class="bi bi-play-fill vn-play-icon"></i>`;
            }
            const audioEl = vnBubble.querySelector('.vn-audio-el');
            if (audioEl) {
                const currentSrc = audioEl.getAttribute('src') || '';
                if ((!currentSrc || (!currentSrc.startsWith('blob:') && mediaUrl && currentSrc !== mediaUrl)) && mediaUrl) {
                    audioEl.setAttribute('src', mediaUrl);
                    audioEl.removeAttribute('data-src');
                }
                if (message.id && !String(message.id).startsWith('temp_')) {
                    deviceMediaStore.getLocalMediaUrl(message.id).then(localUrl => {
                        if (localUrl && audioEl.src !== localUrl) {
                            audioEl.src = localUrl;
                            audioEl.removeAttribute('data-src');
                        }
                    }).catch(() => {});
                }
            }
            const timerEl = vnBubble.querySelector('.vn-timer');
            if (timerEl && timerEl.textContent.includes('%')) {
                if (audioEl && isFinite(audioEl.duration) && audioEl.duration > 0) {
                    timerEl.textContent = this._formatAudioDuration(audioEl.duration);
                } else if (message.metadata?.duration && isFinite(Number(message.metadata.duration))) {
                    timerEl.textContent = this._formatAudioDuration(Number(message.metadata.duration));
                } else {
                    timerEl.textContent = '0:00';
                }
            }
        }

        // 2. Audio Track bubble: replace spinner with play button icon and restore duration timer
        if (bubble.classList.contains('audio-track-bubble') || wrapperEl.querySelector('.audio-track-bubble')) {
            const trackBubble = bubble.classList.contains('audio-track-bubble') ? bubble : wrapperEl.querySelector('.audio-track-bubble');
            const playBtn = trackBubble.querySelector('.audio-track-play-btn');
            if (playBtn && (playBtn.querySelector('.spinner-border') || !playBtn.querySelector('.track-play-icon'))) {
                playBtn.innerHTML = `<i class="bi bi-play-fill track-play-icon"></i>`;
            }
            const audioEl = trackBubble.querySelector('.track-audio-el');
            if (audioEl) {
                const currentSrc = audioEl.getAttribute('src') || '';
                if ((!currentSrc || (!currentSrc.startsWith('blob:') && mediaUrl && currentSrc !== mediaUrl)) && mediaUrl) {
                    audioEl.setAttribute('src', mediaUrl);
                    audioEl.removeAttribute('data-src');
                }
                if (message.id && !String(message.id).startsWith('temp_')) {
                    deviceMediaStore.getLocalMediaUrl(message.id).then(localUrl => {
                        if (localUrl && audioEl.src !== localUrl) {
                            audioEl.src = localUrl;
                            audioEl.removeAttribute('data-src');
                        }
                    }).catch(() => {});
                }
            }
            const timerEl = trackBubble.querySelector('.audio-track-time');
            if (timerEl && timerEl.textContent.includes('%')) {
                if (audioEl && isFinite(audioEl.duration) && audioEl.duration > 0) {
                    timerEl.textContent = this._formatAudioDuration(audioEl.duration);
                } else if (message.metadata?.duration && isFinite(Number(message.metadata.duration))) {
                    timerEl.textContent = this._formatAudioDuration(Number(message.metadata.duration));
                } else {
                    timerEl.textContent = '0:00';
                }
            }
        }

        // 3. Document bubble: replace uploading spinner div with download anchor and remove progress text
        if (bubble.classList.contains('document-bubble') || wrapperEl.querySelector('.document-bubble')) {
            const docBubble = bubble.classList.contains('document-bubble') ? bubble : wrapperEl.querySelector('.document-bubble');
            const progressText = docBubble.querySelector('.document-progress-text');
            if (progressText) {
                const prevDot = progressText.previousElementSibling;
                if (prevDot && prevDot.classList.contains('document-dot')) {
                    prevDot.remove();
                }
                progressText.remove();
            }
            const uploadingBtn = docBubble.querySelector('.document-download-btn.uploading');
            if (uploadingBtn) {
                const rawName = message.metadata?.file_name || message.content || 'Document';
                const fileName = rawName.split('/').pop().split('\\').pop();
                const link = document.createElement('a');
                link.href = mediaUrl || '#';
                link.download = fileName;
                link.target = '_blank';
                link.rel = 'noopener';
                link.className = 'document-download-btn';
                link.title = `Download ${fileName}`;
                link.setAttribute('aria-label', 'Download');
                link.innerHTML = `<i class="bi bi-arrow-down"></i>`;
                uploadingBtn.replaceWith(link);
            }
        }

        // 4. Single / Group Media overlays: remove upload progress overlay once upload is complete
        const uploadOverlays = wrapperEl.querySelectorAll('.media-upload-overlay');
        uploadOverlays.forEach(overlay => overlay.remove());
    }

    /**
     * Update an existing message element in-place to avoid re-rendering and media playback interruption
     */
    _updateExistingMessageElement(wrapperEl, message) {
        if (!wrapperEl || !message) return wrapperEl;
        this._decorateGroupSender(wrapperEl, message);

        // If message was deleted, replace with deleted tombstone
        if (message.isDeleted) {
            if (!wrapperEl.querySelector('.deleted-message-bubble')) {
                const newEl = this._createDeletedMessage(message);
                wrapperEl.replaceWith(newEl);
                return newEl;
            }
            return wrapperEl;
        }

        const bubble = wrapperEl.querySelector('.message-bubble');

        // Transition any voice note, audio track, document, or media upload spinner to completed state
        this._reconcileMediaUploadCompletion(wrapperEl, message);

        // If message was edited, cleanly refresh text content and spacer, and ensure 'Edited' indicator
        const isEdited = Boolean(message.editedAt || message.is_edited || message.edited_at || message.metadata?.edited_at);
        if (isEdited) {
            if (bubble) {
                bubble.classList.add('is-edited-bubble');
            }
            const contentEl = wrapperEl.querySelector('.message-content');
            if (contentEl && message.content) {
                contentEl.innerHTML = `${escapeHtml(message.content)}${this._getSpacerHtml(message)}`;
            }
            const metaDiv = wrapperEl.querySelector('.message-meta');
            if (metaDiv && !metaDiv.querySelector('.edited-indicator')) {
                const editedSpan = document.createElement('span');
                editedSpan.className = 'edited-indicator me-1';
                editedSpan.textContent = 'Edited';
                const timeEl = metaDiv.querySelector('.timestamp');
                if (timeEl) {
                    metaDiv.insertBefore(editedSpan, timeEl);
                } else {
                    metaDiv.prepend(editedSpan);
                }
            }
        }

        // If message is forwarded and element doesn't have forwarded badge yet, inject it in-place
        if (!wrapperEl.querySelector('.message-forwarded-badge')) {
            const forwardedHtml = this._buildForwardedBadgeHtml(message);
            if (forwardedHtml && bubble) {
                const topMeta = bubble.querySelector('.media-bubble-top-meta');
                if (topMeta) {
                    topMeta.insertAdjacentHTML('afterbegin', forwardedHtml);
                } else {
                    bubble.insertAdjacentHTML('afterbegin', forwardedHtml);
                }
            }
        }

        // If message has reply quote and element doesn't have it yet, inject it in-place
        if (!wrapperEl.querySelector('.quoted-reply-box')) {
            const replyHtml = this._buildReplyQuoteHtml(message);
            if (replyHtml && bubble) {
                const tempWrap = document.createElement('div');
                tempWrap.innerHTML = replyHtml;
                const replyBox = tempWrap.firstElementChild;
                if (replyBox) {
                    const forwardedBadge = bubble.querySelector('.message-forwarded-badge');
                    if (forwardedBadge) {
                        forwardedBadge.after(replyBox);
                    } else {
                        bubble.prepend(replyBox);
                    }
                }
            }
        }

        // Update status and group position attributes
        if (wrapperEl.getAttribute('data-status') !== message.status) {
            wrapperEl.setAttribute('data-status', message.status);
        }
        if (wrapperEl.getAttribute('data-group-position') !== message.groupPosition) {
            wrapperEl.setAttribute('data-group-position', message.groupPosition);
            wrapperEl.classList.remove('group-single', 'group-first', 'group-middle', 'group-last');
            wrapperEl.classList.add(`group-${message.groupPosition}`);
        }

        if (bubble) {
            if (bubble.getAttribute('data-status') !== message.status) {
                bubble.setAttribute('data-status', message.status);
            }
            if (bubble.getAttribute('data-group-position') !== message.groupPosition) {
                bubble.setAttribute('data-group-position', message.groupPosition);
                bubble.classList.remove('group-single', 'group-first', 'group-middle', 'group-last');
                bubble.classList.add(`group-${message.groupPosition}`);
            }
        }

        // Update avatar visibility on received messages
        if (!message.isOwn) {
            const avatarContainer = wrapperEl.querySelector('.message-avatar-container');
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            if (avatarContainer) {
                if (shouldShowAvatar && avatarContainer.classList.contains('hidden')) {
                    avatarContainer.classList.remove('hidden');
                    if (!avatarContainer.querySelector('.message-avatar')) {
                        const avatarUrl = this._getSenderAvatar(message.senderId);
                        const avatarImg = document.createElement('img');
                        avatarImg.className = 'message-avatar';
                        avatarImg.src = avatarUrl;
                        avatarImg.alt = 'Avatar';
                        avatarContainer.appendChild(avatarImg);
                    }
                } else if (!shouldShowAvatar && !avatarContainer.classList.contains('hidden')) {
                    avatarContainer.classList.add('hidden');
                }
            }
        }

        // Update read receipt / tick icon on sent messages
        const isOwn = message.isOwn || wrapperEl.classList.contains('sent-wrapper') || (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));
        if (isOwn) {
            const metaDiv = wrapperEl.querySelector('.message-meta, .jumboji-meta-pill, .sticker-meta-pill, .media-meta-overlay');
            if (metaDiv) {
                let receiptSpan = metaDiv.querySelector('.message-read-receipt');
                const status = message.status || 'sent';
                if (!receiptSpan) {
                    receiptSpan = document.createElement('span');
                    metaDiv.appendChild(receiptSpan);
                }
                receiptSpan.className = `message-read-receipt status-${status}`;
                const receiverAvatar = document.body.dataset.receiverAvatar;
                const avatarUrl = message.metadata?.read_avatar || receiverAvatar || '/static/images/default_pic1.jpg';
                if (status === 'read' && (message.isLastRead || message.groupPosition === 'single' || message.groupPosition === 'last')) {
                    receiptSpan.classList.add('read-avatar-only');
                    receiptSpan.innerHTML = `<img src="${escapeHtml(avatarUrl)}" alt="Read" class="read-avatar-img">`;
                } else {
                    receiptSpan.classList.remove('read-avatar-only');
                    receiptSpan.innerHTML = this._getStatusIcon(status, message.id, message);
                }
            }
        }
        return wrapperEl;
    }

    /**
     * Rekey an existing message element in DOM from temporary ID to canonical server ID
     * Prevents DOM destruction, duplicates, and visual flicker.
     */
    rekeyMessageElement(tempId, actualId) {
        if (!this.container || !tempId || !actualId) return;
        const selector = `[data-message-id="${tempId}"]`;
        const elements = this.container.querySelectorAll(selector);
        elements.forEach(el => {
            el.setAttribute('data-message-id', String(actualId));
        });
        const uploadOverlays = this.container.querySelectorAll(`[data-upload-id="${tempId}"]`);
        uploadOverlays.forEach(el => {
            el.setAttribute('data-upload-id', String(actualId));
        });
    }

    /**
     * Efficiently update message statuses (sent -> delivered -> read) in-place without touching bubbles or interrupting media
     */
    updateMessageStatuses(messages) {
        if (!this.container || !messages) return;

        // Find the highest ID among sent messages that are marked as 'read'
        let lastReadId = null;
        for (const msg of messages) {
            const isSent = (this.currentUserId !== null && Number(msg.senderId) === Number(this.currentUserId)) ||
                String(msg.senderId) === String(this.currentUserId) ||
                msg.isOwn;
            if (isSent && msg.status === 'read') {
                const numId = parseInt(msg.id, 10);
                if (!isNaN(numId) && (lastReadId === null || numId > lastReadId)) {
                    lastReadId = numId;
                }
            }
        }

        for (const msg of messages) {
            let wrapper = this.container.querySelector(`.message-wrapper[data-message-id="${msg.id}"]`);
            if (!wrapper && msg.metadata?.temp_id) {
                wrapper = this.container.querySelector(`.message-wrapper[data-message-id="${msg.metadata.temp_id}"]`);
                if (wrapper) {
                    this.rekeyMessageElement(msg.metadata.temp_id, msg.id);
                }
            }
            if (!wrapper) continue;

            // Ensure any completed voice note, audio track, document, or media upload removes its spinner immediately
            this._reconcileMediaUploadCompletion(wrapper, msg);

            const currentStatus = wrapper.getAttribute('data-status');
            const isSent = wrapper.classList.contains('sent-wrapper') ||
                (this.currentUserId !== null && Number(msg.senderId) === Number(this.currentUserId)) ||
                String(msg.senderId) === String(this.currentUserId) ||
                msg.isOwn;

            const existingReceipt = wrapper.querySelector('.message-read-receipt');
            const receiptOutOfSync = isSent && (!existingReceipt || !existingReceipt.classList.contains(`status-${msg.status}`));

            if (currentStatus !== msg.status || receiptOutOfSync || (isSent && msg.status === 'read')) {
                wrapper.setAttribute('data-status', msg.status);
                const bubble = wrapper.querySelector('.message-bubble');
                if (bubble) bubble.setAttribute('data-status', msg.status);

                if (isSent) {
                    const metaDiv = wrapper.querySelector('.message-meta, .jumboji-meta-pill, .sticker-meta-pill, .media-meta-overlay');
                    if (metaDiv) {
                        let receiptSpan = metaDiv.querySelector('.message-read-receipt');
                        if (!receiptSpan) {
                            receiptSpan = document.createElement('span');
                            metaDiv.appendChild(receiptSpan);
                        }
                        const status = msg.status || 'sent';
                        receiptSpan.className = `message-read-receipt status-${status}`;
                        const receiverAvatar = document.body.dataset.receiverAvatar;
                        const avatarUrl = msg.read_avatar || msg.metadata?.read_avatar || receiverAvatar;
                        const numId = parseInt(msg.id, 10);
                        const isLastRead = (numId === lastReadId);

                        if (status === 'read' && isLastRead && avatarUrl) {
                            receiptSpan.classList.add('read-avatar-only');
                            receiptSpan.innerHTML = `<img src="${escapeHtml(avatarUrl)}" alt="Read" class="read-avatar-img">`;
                        } else {
                            receiptSpan.classList.remove('read-avatar-only');
                            receiptSpan.innerHTML = this._getStatusIcon(status, msg.id, msg);
                        }

                        const spacer = wrapper.querySelector('.bubble-meta-spacer');
                        if (spacer) {
                            if (status === 'read') {
                                spacer.classList.add('has-receipt');
                            } else {
                                spacer.classList.remove('has-receipt');
                            }
                        }
                    }
                }
            }
        }
    }
    
    /**
     * Attach event listeners for retry buttons
     */
    _attachRetryButtonListeners() {
        const retryButtons = this.container.querySelectorAll('.retry-button');
        retryButtons.forEach(button => {
            button.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const messageId = button.getAttribute('data-message-id');
                this._log('RETRY_BUTTON_CLICKED', { messageId });
                
                // Emit event for UI controller to handle retry
                const event = new CustomEvent('messageRetry', {
                    detail: { messageId }
                });
                window.dispatchEvent(event);
            });
        });
    }

    /**
     * Render empty state if template exists
     */
    _renderEmptyState() {
        const template = document.getElementById('emptyStateTemplate');
        if (template && this.container) {
            const clone = template.content.cloneNode(true);
            this.container.appendChild(clone);
            this._attachEmptyStateListeners();
        }
    }

    /**
     * Attach click listeners to greeting chips
     */
    _attachEmptyStateListeners() {
        if (!this.container) return;
        const chips = this.container.querySelectorAll('.empty-greeting-chip');
        chips.forEach(chip => {
            chip.addEventListener('click', (e) => {
                e.preventDefault();
                const text = chip.getAttribute('data-text') || chip.textContent.trim();
                const input = document.getElementById('messageInput');
                if (input) {
                    input.value = text;
                    input.focus();
                    input.dispatchEvent(new Event('input', { bubbles: true }));
                }
            });
        });
    }

    /**
     * Build view from messages (pure data transformation)
     * @param {Array} messages - Messages array (canonical schema)
     * @returns {Array} View items
     */
    _buildView(messages) {
        const viewItems = [];
        let lastDate = null;
        let lastSenderId = null;
        let lastTimestamp = null;

        const ownMessages = messages.filter(
            m => m.isOwn || (this.currentUserId !== null && String(m.senderId) === String(this.currentUserId))
        );

        const lastOwnMessageId =
            ownMessages.length > 0
                ? ownMessages[ownMessages.length - 1].id
                : null;

        // Find last message that was READ by the receiver (for avatar display)
        const readMessages = ownMessages.filter(m => m.status === 'read');
        const lastReadMessageId =
            readMessages.length > 0
                ? readMessages[readMessages.length - 1].id
                : null;

        for (let i = 0; i < messages.length; i++) {
            const message = messages[i];
            const messageDate = new Date(message.timestamp).toDateString();
            const messageTimestamp = new Date(message.timestamp).getTime();
            const isOwn = message.isOwn !== undefined
                ? Boolean(message.isOwn)
                : (this.currentUserId !== null && String(message.senderId) === String(this.currentUserId));

            if (messageDate !== lastDate) {
                viewItems.push({
                    viewType: 'date',
                    label: formatDateLabel(message.timestamp)
                });
                lastDate = messageDate;
                // Reset sender tracking - date separator breaks the group
                lastSenderId = null;
                // Reset timestamp tracking - date separator resets time grouping
                lastTimestamp = null;
            }

            // Check for micro time separator (15-minute gap)
            // Only insert if not the first message after a date separator
            if (lastTimestamp !== null) {
                const timeGapMinutes = (messageTimestamp - lastTimestamp) / (1000 * 60);
                if (timeGapMinutes >= 15) {
                    viewItems.push({
                        viewType: 'time',
                        label: formatTime(message.timestamp),
                        preciseLabel: formatPreciseTime(message.timestamp)
                    });
                    // Reset sender tracking - time separator breaks the group
                    lastSenderId = null;
                }
            }

            // Check if messages should be grouped (same sender, within 5 minutes)
            let shouldGroup = false;
            if (lastSenderId !== null && String(lastSenderId) === String(message.senderId) && lastTimestamp !== null) {
                const timeGapMinutes = (messageTimestamp - lastTimestamp) / (1000 * 60);
                shouldGroup = timeGapMinutes < 5;
            }

            const isConsecutive = lastSenderId !== null && String(lastSenderId) === String(message.senderId) && shouldGroup;
            lastSenderId = message.senderId;
            lastTimestamp = messageTimestamp;

            // Determine group position
            const nextMessage = messages[i + 1];
            // Only count next message as same group if same sender AND same date AND within 5 minutes
            let isNextFromSameSender = false;
            if (nextMessage) {
                const nextMessageDate = new Date(nextMessage.timestamp).toDateString();
                const nextMessageTimestamp = new Date(nextMessage.timestamp).getTime();
                const isNextSameDate = nextMessageDate === messageDate;
                const timeGapToNextMinutes = (nextMessageTimestamp - messageTimestamp) / (1000 * 60);
                isNextFromSameSender = isNextSameDate && 
                                      String(nextMessage.senderId) === String(message.senderId) && 
                                      timeGapToNextMinutes < 5;
            }

            let groupPosition = 'single';
            if (isConsecutive && isNextFromSameSender) {
                groupPosition = 'middle'; // Has prev and next from same sender
            } else if (isConsecutive && !isNextFromSameSender) {
                groupPosition = 'last'; // Has prev but no next from same sender
            } else if (!isConsecutive && isNextFromSameSender) {
                groupPosition = 'first'; // No prev but has next from same sender
            }

            const isLastRead = isOwn && message.id === lastReadMessageId;

            viewItems.push({
                viewType: 'message',
                ...message,
                isOwn,
                isConsecutive,
                isLastSent: isOwn && message.id === lastOwnMessageId,
                isLastRead,
                hasFloatingReadReceipt: false,
                hideReadReceipt: false,
                groupPosition
            });
        }

        return viewItems;
    }

    /**
     * Create date separator element (pure DOM creation)
     * @param {string} label - Date label
     * @returns {HTMLElement} Date element
     */
    _createDateElement(label) {
        const element = document.createElement('div');
        element.className = 'date-separator';
        element.innerHTML = `<span>${escapeHtml(label)}</span>`;
        return element;
    }

    /**
     * Create micro time separator element (pure DOM creation)
     * @param {string} label - Time label (e.g., "2:31 PM")
     * @param {string} preciseLabel - Precise time label for hover (e.g., "Today at 2:31:44 PM")
     * @returns {HTMLElement} Time separator element
     */
    _createTimeElement(label, preciseLabel) {
        const element = document.createElement('div');
        element.className = 'time-separator';
        element.setAttribute('data-precise-time', escapeHtml(preciseLabel));
        element.innerHTML = `<span class="time-label">${escapeHtml(label)}</span>`;
        return element;
    }

    _buildForwardedBadgeHtml(message) {
        if (!message) return '';
        const isForwarded = Boolean(
            message.is_forwarded || 
            message.isForwarded || 
            message.forwarded ||
            message.metadata?.is_forwarded || 
            message.metadata?.isForwarded || 
            message.metadata?.forwarded
        );
        if (!isForwarded) return '';
        return `
            <div class="message-forwarded-badge">
                <i class="bi bi-reply-fill forward-icon" style="transform: scaleX(-1); display: inline-block;"></i>
                <span>Forwarded</span>
            </div>
        `;
    }

    _buildReplyQuoteHtml(message) {
        let replyDetails = message.replyToDetails || message.reply_to_details || message.metadata?.reply_to_details;
        const repId = message.replyToId || message.reply_to_id || message.reply_to || message.replyTo || message.metadata?.reply_to_id || message.metadata?.reply_to;
        
        if (!replyDetails && repId) {
            const originalMsg = typeof store !== 'undefined' && store.getMessageById ? store.getMessageById(repId) : null;
            if (originalMsg) {
                const currentUserId = this.currentUserId;
                const isOwnOrig = originalMsg.isOwn || (currentUserId !== null && Number(originalMsg.senderId) === Number(currentUserId));
                replyDetails = {
                    id: originalMsg.id,
                    senderId: originalMsg.senderId,
                    sender: { username: isOwnOrig ? 'You' : (originalMsg.sender?.username || originalMsg.senderUsername || 'User') },
                    content: originalMsg.content,
                    type: originalMsg.type || originalMsg.messageType || originalMsg.message_type,
                    attachment_type: originalMsg.metadata?.type || originalMsg.attachment_type,
                    is_voice_note: Boolean(originalMsg.metadata?.is_voice_note || originalMsg.is_voice_note)
                };
            }
        }
        if (!replyDetails) return '';

        const currentUserId = this.currentUserId;
        const isOwn = replyDetails.senderId === currentUserId || replyDetails.isOwn;
        const senderName = isOwn ? 'You' : (replyDetails.sender?.username || replyDetails.senderUsername || 'User');
        
        let snippet = replyDetails.content;
        const attType = replyDetails.attachment_type || replyDetails.type;
        const isVn = replyDetails.is_voice_note || attType === 'voice_note';
        
        if (!snippet || !snippet.trim()) {
            if (isVn) snippet = '🎙️ Voice note';
            else if (attType === 'audio') snippet = '🎵 Audio track';
            else if (attType === 'image') snippet = '📷 Photo';
            else if (attType === 'video') snippet = '🎥 Video';
            else if (attType === 'document') snippet = '📄 Document';
            else if (attType === 'sticker') snippet = 'Sticker';
            else if (attType === 'gif') snippet = 'GIF';
            else snippet = 'Message';
        } else {
            if (isVn) snippet = `🎙️ ${snippet}`;
            else if (attType === 'image') snippet = `📷 ${snippet}`;
            else if (attType === 'video') snippet = `🎥 ${snippet}`;
            else if (attType === 'audio') snippet = `🎵 ${snippet}`;
            else if (attType === 'document') snippet = `📄 ${snippet}`;
        }

        return `
            <div class="quoted-reply-box" data-reply-id="${replyDetails.id}">
                <div class="quoted-reply-sender">${escapeHtml(senderName)}</div>
                <div class="quoted-reply-snippet">${escapeHtml(snippet)}</div>
            </div>
        `;
    }

    _createDeletedMessage(message) {
        const isOwn = message.isOwn ?? (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));
        const groupPos = message.groupPosition || 'single';

        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${groupPos}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status || 'sent');
        wrapperDiv.setAttribute('data-group-position', groupPos);

        // Add avatar for received messages to match regular message alignment (only on last/single in group)
        if (!isOwn) {
            const shouldShowAvatar = groupPos === 'single' || groupPos === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;

            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }

            wrapperDiv.appendChild(avatarContainer);
        }

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        const messageDiv = document.createElement('div');
        messageDiv.className = `message-bubble ${isOwn ? 'sent' : 'received'} deleted-message-bubble group-${groupPos}`;
        messageDiv.setAttribute('data-message-id', message.id);

        this._applyBubbleStyle(messageDiv);

        messageDiv.innerHTML = `
            <div class="deleted-message-inner">
                <i class="bi bi-slash-circle me-1"></i>
                <span>${isOwn ? 'You deleted this message' : 'This message was deleted'}</span>
                ${this._getSpacerHtml(message)}
            </div>
        `;

        const metaDiv = this._createMetaElement(message, false);
        messageDiv.appendChild(metaDiv);
        contentWrapper.appendChild(messageDiv);
        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Create message element (pure DOM creation)
     * @param {Object} message - Message object with view properties (canonical schema)
     * @returns {HTMLElement} Message element
     */
    _createMessageElement(message) {
        const element = this._createMessageBody(message);
        this._decorateGroupSender(element, message);
        return element;
    }

    _decorateGroupSender(element, message) {
        if (document.querySelector('.chat-container')?.dataset.conversationType !== 'group' || message.isOwn) return;
        const target = element.querySelector('.message-content-wrapper') || element;
        let label = target.querySelector('.group-message-sender');
        if (!label) {
            label = document.createElement('div');
            label.className = 'group-message-sender small fw-bold text-primary mb-1';
            target.prepend(label);
        }
        const sender = message.metadata?.sender || {};
        label.textContent = [sender.first_name, sender.last_name].filter(Boolean).join(' ') || sender.username || 'Member';
    }

    _createMessageBody(message) {
        // DEBUG: Log message type detection
        console.log('[RENDERER] Creating message element. ID:', message.id, 'Type:', message.type, 'Metadata:', message.metadata);

        if (message.is_deleted || message.isDeleted) {
            return this._createDeletedMessage(message);
        }

        if (message.type === 'system') {
            return this._createSystemMessage(message);
        }

        // Check for 1 or 2 emojis (Jumboji) - floating without bubble background
        const emojiInfo = this._getEmojiOnlyInfo(message.content);
        if (emojiInfo) {
            return this._createJumbojiMessage(message, emojiInfo);
        }

        if (message.type === 'emoji') {
        return this._createEmojiMessage(message);
        }

        if (message.type === 'media_group' || (message.attachments && message.attachments.length > 1) || (message.metadata?.attachments && message.metadata.attachments.length > 1)) {
            return this._createMediaGroupMessage(message);
        }

        if (
            message.type === 'media' ||
            message.type === 'sticker' ||
            message.type === 'gif' ||
            message.type === 'audio' ||
            message.type === 'voice_note' ||
            message.type === 'document' ||
            Boolean(message.metadata?.url) ||
            Boolean(message.attachment_url) ||
            Boolean(message.attachment) ||
            message.metadata?.is_sticker ||
            message.metadata?.is_gif ||
            message.attachment_type === 'sticker' ||
            message.attachment_type === 'gif'
        ) {
            return this._createMediaMessage(message);
        }

        if (message.type === 'link') {
        console.log('[RENDERER] Creating link message for:', message.id);
        return this._createLinkMessage(message);
        }
        // Build wrapper containing avatar, bubble and meta (timestamp + read receipt)
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;
            
            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }
            
            wrapperDiv.appendChild(avatarContainer);
        }

        // Content wrapper for bubble and meta
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        // Create bubble (no timestamp/read receipt inside)
        const isEdited = Boolean(message.edited_at || message.editedAt || message.is_edited || message.isEdited || message.metadata?.edited_at);
        const messageDiv = document.createElement('div');
        messageDiv.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} group-${message.groupPosition} ${message.isLastSent ? 'last-sent' : ''} ${isEdited ? 'is-edited-bubble' : ''}`;
        messageDiv.setAttribute('data-message-id', message.id);
        messageDiv.setAttribute('data-sender-id', message.senderId);
        messageDiv.setAttribute('data-status', message.status);
        messageDiv.setAttribute('data-group-position', message.groupPosition);

        // Apply custom bubble style if set
        this._applyBubbleStyle(messageDiv);

        // Use canonical schema fields
        const status = message.status || 'sent';

        // Render forwarded badge and reply quote if present
        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        // Render reactions if present
        let reactionsHtml = '';
        const reactions = message.metadata?.reactions;
        if (reactions && Array.isArray(reactions) && reactions.length > 0) {
            const counts = {};
            reactions.forEach(r => {
                const em = r.emoji || (typeof r === 'string' ? r : null);
                if (em) counts[em] = (counts[em] || 0) + (r.count || 1);
            });
            const badges = Object.entries(counts).map(([em, cnt]) => `
                <span class="reaction-badge badge bg-light text-dark border rounded-pill px-2 py-1 me-1 shadow-sm" style="font-size: 0.75rem;">
                    ${em} ${cnt > 1 ? cnt : ''}
                </span>
            `).join('');
            reactionsHtml = `
                <div class="message-reactions-container d-flex flex-wrap mt-1">
                    ${badges}
                </div>
            `;
        }

        // Bubble content
        messageDiv.innerHTML = `
            ${forwardedHtml}
            ${replyHtml}
            <p class="message-content">${escapeHtml(message.content || '')}${this._getSpacerHtml(message)}</p>
            ${reactionsHtml}
        `;

        // Render link preview if available
        if (message.link_preview && message.link_preview.url) {
            console.log("[LINK_PREVIEW] Rendering preview for message:", message.id);
            const previewCard = linkPreviewRenderer.render(message.link_preview);
            if (previewCard) {
                messageDiv.appendChild(previewCard);
                messageDiv.classList.add('link-message');
            }
        }

        // Meta (timestamp + receipt) inside bubble at bottom right
        const metaDiv = this._createMetaElement(message, false);
        messageDiv.appendChild(metaDiv);

        contentWrapper.appendChild(messageDiv);
        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Get CSS class for bubble meta spacer based on whether an avatar read receipt is present
     * @param {Object} message - Canonical message
     * @returns {string} Spacer class name
     */
    _getSpacerClass(message) {
        if (!message) return 'bubble-meta-spacer';
        const hasAvatarReceipt = message.isOwn && message.status === 'read' && (message.isLastRead || message.groupPosition === 'single' || message.groupPosition === 'last');
        const isEdited = Boolean(message.edited_at || message.editedAt || message.is_edited || message.isEdited || message.metadata?.edited_at);
        let cls = 'bubble-meta-spacer';
        if (hasAvatarReceipt) cls += ' has-receipt';
        if (isEdited) cls += ' is-edited';
        return cls;
    }

    /**
     * Build the inline meta spacer. It carries the actual timestamp text (rendered
     * invisibly via CSS ::before, so it's not copyable/selectable) so the reserved
     * space matches the real timestamp width instead of a worst-case fixed width.
     * @param {Object} message - Canonical message
     * @returns {string} Spacer HTML
     */
    _getSpacerHtml(message) {
        const time = message?.timestamp ? formatTime(message.timestamp) : '';
        return `<span class="${this._getSpacerClass(message)}" data-time="${escapeHtml(time)}" aria-hidden="true"></span>`;
    }

    /**
     * Create inside-bubble meta element (timestamp + status tick / read avatar)
     * @param {Object} message - Canonical message
     * @param {boolean} isMediaBadge - Whether this is a floating glass badge inside image/video
     * @returns {HTMLElement} Meta element
     */
    _createMetaElement(message, isMediaBadge = false) {
        const metaDiv = document.createElement('div');
        metaDiv.className = isMediaBadge 
            ? 'message-meta media-meta-badge' 
            : 'message-meta';

        const isEdited = Boolean(message.edited_at || message.editedAt || message.is_edited || message.isEdited || message.metadata?.edited_at);
        if (isEdited) {
            const editedSpan = document.createElement('span');
            editedSpan.className = 'edited-indicator me-1';
            editedSpan.textContent = 'Edited';
            metaDiv.appendChild(editedSpan);
        }

        const timeSpan = document.createElement('span');
        timeSpan.className = 'timestamp';
        timeSpan.textContent = formatTime(message.timestamp);
        metaDiv.appendChild(timeSpan);

        const isOwn = message.isOwn ?? (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));
        if (isOwn) {
            const status = message.status || 'sent';
            const receiptSpan = document.createElement('span');
            receiptSpan.className = `message-read-receipt status-${status}`;

            const receiverAvatar = document.body.dataset.receiverAvatar;
            const avatarUrl = message.read_avatar || message.metadata?.read_avatar || receiverAvatar;

            if (status === 'read' && message.isLastRead && avatarUrl) {
                receiptSpan.classList.add('read-avatar-only');
                receiptSpan.innerHTML = `<img src="${escapeHtml(avatarUrl)}" alt="Read" class="read-avatar-img">`;
            } else {
                receiptSpan.classList.remove('read-avatar-only');
                receiptSpan.innerHTML = this._getStatusIcon(status, message.id, message);
            }
            metaDiv.appendChild(receiptSpan);
        }

        return metaDiv;
    }

    /**
     * Get status icon (pure data transformation)
     * @param {string} status - Message status from canonical schema
     * @param {string} messageId - Message ID for retry button
     * @param {Object} message - Full message object for context (optional)
     * @returns {string} Icon HTML - checkmark inside circle
     * @deprecated Use renderMessageStatus from message-status-renderer.js instead
     */
    _getStatusIcon(status, messageId, message = null) {
        const context = {};
        
        // Pass queuedAt timestamp if available for delayed visibility logic
        if (message && message.metadata) {
            if (message.metadata.queuedAt) {
                context.queuedAt = message.metadata.queuedAt;
            }
        }
        
        const receiverAvatar = message?.metadata?.read_avatar || document.body?.dataset?.receiverAvatar || '/static/images/default_pic1.jpg';
        return renderMessageStatus(status, messageId, receiverAvatar, context);
    }

    /**
     * Apply custom bubble style from user preferences
     * @param {HTMLElement} bubble - Message bubble element
     */
    _applyBubbleStyle(bubble) {
        const currentStyle = document.body?.dataset?.bubbleStyle;
        // CSS handles the actual styling via data-bubble-shape attribute
        // We just need to ensure the document has the attribute set
        if (currentStyle && currentStyle !== 'default') {
            document.documentElement.dataset.bubbleShape = currentStyle;
        }
    }

    /**
     * Create system message element (pure DOM creation)
     * @param {Object} message - System message object
     * @returns {HTMLElement} System message element
     */
    _createSystemMessage(message) {
        const element = document.createElement('div');
        element.className = 'system-message';
        element.innerHTML = `<span class="system-text">${escapeHtml(message.content || '')}</span>`;
        return element;
    }

    /**
     * Check if text consists exclusively of 1 or 2 emojis (Jumboji)
     * @param {string} text - Message content
     * @returns {Object|null} { count, sizeClass } or null
     */
    _getEmojiOnlyInfo(text) {
        if (!text || typeof text !== 'string') return null;
        const trimmed = text.trim();
        if (!trimmed) return null;

        // Split text into grapheme clusters using Intl.Segmenter if available
        let clusters = [];
        if (typeof Intl !== 'undefined' && Intl.Segmenter) {
            const segmenter = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
            clusters = Array.from(segmenter.segment(trimmed), s => s.segment.trim()).filter(Boolean);
        } else {
            // Unicode-aware emoji cluster matching fallback
            const emojiPattern = /(\p{Extended_Pictographic}(?:\u200D\p{Extended_Pictographic})*)/gu;
            clusters = trimmed.match(emojiPattern) || [];
            const remainder = trimmed.replace(emojiPattern, '').trim();
            if (remainder.length > 0) return null;
        }

        if (clusters.length === 0 || clusters.length > 2) return null;

        // Verify that every cluster starts with an emoji character (Extended_Pictographic or Regional_Indicator flags)
        const emojiCheck = /^(\p{Extended_Pictographic}|\p{Regional_Indicator})/u;
        const allAreEmojis = clusters.every(c => emojiCheck.test(c));
        if (!allAreEmojis) return null;

        // Verify there is no leftover non-whitespace text
        let remaining = trimmed;
        for (const c of clusters) {
            remaining = remaining.replace(c, '');
        }
        if (remaining.trim().length > 0) return null;

        if (clusters.length === 1) {
            return { count: 1, sizeClass: 'jumboji-single' };
        }
        if (clusters.length === 2) {
            return { count: 2, sizeClass: 'jumboji-double' };
        }
        return null;
    }

    /**
     * Create floating Jumboji message (1 or 2 large emojis without bubble background, with frosted meta pill)
     * @param {Object} message - Message object
     * @param {Object} emojiInfo - { count, sizeClass }
     * @returns {HTMLElement} Message wrapper element
     */
    _createJumbojiMessage(message, emojiInfo) {
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} jumboji-wrapper group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;
            
            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }
            
            wrapperDiv.appendChild(avatarContainer);
        }

        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper jumboji-content-wrapper';

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} jumboji-bubble ${emojiInfo.sizeClass} group-${message.groupPosition}`;
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);

        const timeStr = this._formatMessageTime(message.timestamp);
        const isOwn = message.isOwn || wrapperDiv.classList.contains('sent-wrapper') ||
            (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));

        bubble.innerHTML = `
            <div class="jumboji-content">${escapeHtml(message.content || '')}</div>
            <div class="jumboji-meta-pill">
                <span class="message-time">${timeStr}</span>
                ${isOwn ? `<span class="message-read-receipt status-${message.status || 'sent'}">${this._getStatusIcon(message.status, message.id, message)}</span>` : ''}
            </div>
        `;

        contentWrapper.appendChild(bubble);
        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Create emoji message element (pure DOM creation)
     * @param {Object} message - Emoji message object
     * @returns {HTMLElement} Emoji message element
     */
    _createEmojiMessage(message) {
        // Build wrapper containing avatar, emoji and meta (timestamp + read receipt)
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;
            
            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }
            
            wrapperDiv.appendChild(avatarContainer);
        }

        // Content wrapper for emoji and meta
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        // Create emoji element (no bubble styling)
        const emojiDiv = document.createElement('div');
        emojiDiv.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} emoji-message group-${message.groupPosition}`;
        emojiDiv.setAttribute('data-message-id', message.id);
        emojiDiv.setAttribute('data-sender-id', message.senderId);
        emojiDiv.setAttribute('data-status', message.status);
        emojiDiv.setAttribute('data-group-position', message.groupPosition);
        
        emojiDiv.innerHTML = `<div class="emoji-content">${escapeHtml(message.content || '')}</div>`;

        contentWrapper.appendChild(emojiDiv);

        // Determine whether to render meta (timestamp + receipt): single messages or last in group
        const shouldRenderMeta = message.groupPosition === 'single' || message.groupPosition === 'last';
        if (shouldRenderMeta) {
            const metaDiv = this._createMetaElement(message, false);
            contentWrapper.appendChild(metaDiv);
        }

        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Preload mode for attachment videos. Local sources (blob:, Capacitor
     * file, data:) cost nothing to fetch, so load full frames — WebView
     * decoders often never paint from metadata-only preload, leaving the
     * grey/dark box where the thumbnail should be. Remote URLs stay on
     * metadata to save bandwidth.
     */
    _videoPreload(url) {
        const u = String(url || '');
        if (u.startsWith('blob:') || u.startsWith('capacitor:') ||
            u.startsWith('data:') || u.indexOf('/_capacitor_file_') !== -1) {
            return 'auto';
        }
        return 'metadata';
    }

    /**
     * Render single media bubble (Telegram-style)
     * Media itself becomes the bubble - NOT reusing text bubbles
     * @param {Object} message - Media message object
     * @returns {HTMLElement} Single media bubble element
     */
    renderSingleMediaBubble(message) {
        console.log('[MEDIA_BUBBLE] Rendering single media bubble for:', message.id);

        const metadata = message.metadata || {};
        const mediaUrl = metadata.url || message.attachment_url || (typeof message.attachment === 'string' ? message.attachment : '') || (metadata.attachments && metadata.attachments[0] ? (metadata.attachments[0].file_url || metadata.attachments[0].file || metadata.attachments[0].url) : '') || '';
        const attachmentType = metadata.type || message.attachment_type || (metadata.attachments && metadata.attachments[0] ? metadata.attachments[0].file_type : '') || (mediaUrl.match(/\.(mp4|webm|mov|ogg)$/i) ? 'video' : 'image');
        const caption = message.content || '';
        const isUploading = message.status === 'uploading' || message.status === 'sending' || message.isOptimistic;
        const isOwnMsg = this._isOwnMessage(message);
        const isNotDownloaded = !isOwnMsg && !isUploading && !this._isMediaDownloaded(message.id);

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${isOwnMsg ? 'sent' : 'received'} media-bubble single-media media-card-bubble group-${message.groupPosition}`;
        if (isNotDownloaded) {
            bubble.classList.add('not-downloaded');
        }
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);

        const mediaFrame = document.createElement('div');
        mediaFrame.className = 'media-card-frame';

        const applyMediaDimensions = (w, h) => {
            if (!w || !h) return;
            const isPortrait = h > w;
            bubble.classList.remove('media-portrait', 'media-landscape', 'media-square');
            if (isPortrait) {
                bubble.classList.add('media-portrait');
            } else if (w > h * 1.1) {
                bubble.classList.add('media-landscape');
            } else {
                bubble.classList.add('media-square');
            }
            mediaFrame.style.aspectRatio = `${w} / ${h}`;
            const MAX_MEDIA_HEIGHT = 380;
            const baseWidth = isPortrait ? 260 : 330;
            const fitWidth = Math.floor(Math.min(baseWidth, MAX_MEDIA_HEIGHT * (w / h)));
            mediaFrame.style.width = `min(${fitWidth}px, calc(100vw - 72px))`;
        };

        if (attachmentType === 'video') {
            bubble.classList.add('video-bubble');
            const initialW = metadata.width || metadata.file_width || message.width || (metadata.attachments && metadata.attachments[0] && (metadata.attachments[0].width || metadata.attachments[0].file_width));
            const initialH = metadata.height || metadata.file_height || message.height || (metadata.attachments && metadata.attachments[0] && (metadata.attachments[0].height || metadata.attachments[0].file_height));
            if (initialW && initialH) {
                applyMediaDimensions(initialW, initialH);
            } else {
                bubble.classList.add('media-landscape');
                mediaFrame.style.aspectRatio = '16 / 9';
                mediaFrame.style.width = 'min(330px, calc(100vw - 72px))';
            }
        } else if (attachmentType === 'image') {
            const w = metadata.width || metadata.file_width || message.width;
            const h = metadata.height || metadata.file_height || message.height;
            if (w && h) {
                applyMediaDimensions(w, h);
            }
        }

        let mediaContent = '';

        if (attachmentType === 'image' && mediaUrl) {
            if (isNotDownloaded) {
                // When not downloaded: render blurred preview thumbnail underneath download overlay
                const previewSrc = metadata.thumbnail || metadata.previewUrl || mediaUrl;
                mediaContent = `
                    <img src="${escapeHtml(previewSrc)}" data-full-src="${escapeHtml(mediaUrl)}" alt="Image preview" loading="lazy" class="single-media-img not-downloaded-thumb" style="filter: blur(14px) brightness(0.72); transform: scale(1.04); display: block; width: 100%; height: auto; max-height: 380px; object-fit: cover; border-radius: 12px !important;">
                `;
            } else {
                mediaContent = `<img src="${escapeHtml(mediaUrl)}" alt="Image" loading="lazy" class="single-media-img" style="border-radius: 12px !important;">`;
            }
        } else if (attachmentType === 'video' && mediaUrl) {
            const thumbUrl = metadata.thumbnail || metadata.previewUrl || message.thumbnail || (metadata.attachments && metadata.attachments[0] && (metadata.attachments[0].thumbnail || metadata.attachments[0].previewUrl)) || '';
            const posterAttr = thumbUrl ? `poster="${escapeHtml(thumbUrl)}"` : '';

            if (isNotDownloaded) {
                // When not downloaded: render blurred video thumbnail underneath download overlay
                const videoSource = `${escapeHtml(mediaUrl)}#t=0.001`;

                mediaContent = `
                    <div class="video-play-overlay">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <polygon points="5,3 19,12 5,21"></polygon>
                        </svg>
                    </div>
                    ${thumbUrl ? `
                        <img src="${escapeHtml(thumbUrl)}" data-full-src="${escapeHtml(mediaUrl)}" alt="Video preview" loading="lazy" class="single-media-video-thumb not-downloaded-thumb" style="width: 100%; height: 100%; object-fit: cover; filter: blur(14px) brightness(0.72); transform: scale(1.08); pointer-events: none; display: block; border-radius: 12px !important;">
                    ` : `
                        <video src="${videoSource}" preload="${this._videoPreload(videoSource)}" ${posterAttr} data-full-src="${escapeHtml(mediaUrl)}" data-no-inline="true" data-autoplay="false" data-chat-media="true" muted playsinline tabindex="-1" class="single-media-video-thumb not-downloaded-thumb" style="width: 100%; height: 100%; object-fit: cover; filter: blur(14px) brightness(0.72); transform: scale(1.08); pointer-events: none; display: block; border-radius: 12px !important;"></video>
                    `}
                `;
            } else {
                mediaContent = `
                    <div class="video-play-overlay">
                        <svg viewBox="0 0 24 24" fill="currentColor">
                            <polygon points="5,3 19,12 5,21"></polygon>
                        </svg>
                    </div>
                    ${thumbUrl ? `
                        <img src="${escapeHtml(thumbUrl)}" alt="Video preview" loading="lazy" class="single-media-video-poster" style="width: 100%; height: 100%; object-fit: cover; pointer-events: none; display: block; border-radius: 12px !important;">
                        <video src="${escapeHtml(mediaUrl)}" preload="${this._videoPreload(mediaUrl)}" ${posterAttr} data-no-inline="true" data-autoplay="false" data-chat-media="true" playsinline tabindex="-1" style="display: none;"></video>
                    ` : `
                        <video src="${escapeHtml(mediaUrl)}" preload="${this._videoPreload(mediaUrl)}" data-no-inline="true" data-autoplay="false" data-chat-media="true" playsinline tabindex="-1" style="width: 100%; height: 100%; object-fit: cover; pointer-events: none; display: block; border-radius: 12px !important;"></video>
                    `}
                `;
            }
        }

        mediaFrame.innerHTML = mediaContent;

        // Auto-detect and adapt bubble shape to actual video / thumbnail dimensions
        const posterEl = mediaFrame.querySelector('.single-media-video-poster, .single-media-video-thumb');
        if (posterEl) {
            if (posterEl.complete && posterEl.naturalWidth && posterEl.naturalHeight) {
                applyMediaDimensions(posterEl.naturalWidth, posterEl.naturalHeight);
            } else {
                posterEl.addEventListener('load', () => {
                    if (posterEl.naturalWidth && posterEl.naturalHeight) {
                        applyMediaDimensions(posterEl.naturalWidth, posterEl.naturalHeight);
                    }
                }, { once: true });
            }
        }

        // Strictly prevent inline playback in chat bubbles and generate canvas frame fallback on mobile/Capacitor
        mediaFrame.querySelectorAll('video').forEach(vid => {
            vid.addEventListener('play', (e) => {
                e.preventDefault();
                try { vid.pause(); } catch (_) {}
            });
            vid.addEventListener('loadedmetadata', () => {
                if (vid.videoWidth && vid.videoHeight) {
                    applyMediaDimensions(vid.videoWidth, vid.videoHeight);
                }
            }, { once: true });
            if (!vid.getAttribute('poster') && vid.src && !vid.src.endsWith('#t=0.001')) {
                vid.addEventListener('loadeddata', () => {
                    try {
                        if (vid.videoWidth && vid.videoHeight) {
                            applyMediaDimensions(vid.videoWidth, vid.videoHeight);
                            const c = document.createElement('canvas');
                            c.width = Math.min(vid.videoWidth, 480);
                            c.height = Math.max(1, Math.round(c.width * (vid.videoHeight / vid.videoWidth)));
                            c.getContext('2d').drawImage(vid, 0, 0, c.width, c.height);
                            const poster = c.toDataURL('image/jpeg', 0.7);
                            vid.setAttribute('poster', poster);
                        }
                    } catch (_) {}
                }, { once: true });
            }
        });

        // Asynchronously check and use locally stored device media if available
        if (!isNotDownloaded && !isUploading) {
            deviceMediaStore.getLocalMediaUrl(message.id).then(localUrl => {
                if (localUrl) {
                    const el = mediaFrame.querySelector('img, video');
                    if (el && el.src !== localUrl) {
                        el.src = localUrl;
                        el.removeAttribute('data-src');
                        el.classList.remove('d-none');
                        mediaFrame.querySelector('.media-image-placeholder')?.remove();
                    }
                }
            }).catch(() => {});
        }

        // 1. Sender optimistic upload progress overlay
        if (isUploading) {
            const initialProgress = metadata.uploadProgress || 0;
            const circumference = 94.25;
            const initialOffset = Math.max(0, circumference * (1 - initialProgress / 100)).toFixed(1);
            const uploadOverlay = document.createElement('div');
            uploadOverlay.className = 'media-upload-overlay';
            uploadOverlay.setAttribute('data-upload-id', message.id);
            uploadOverlay.innerHTML = `
                <div class="upload-progress-widget">
                    <svg class="upload-progress-circle" width="44" height="44" viewBox="0 0 38 38">
                        <circle class="upload-circle-bg" cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.25)" stroke-width="2.5" fill="none"/>
                        <circle class="upload-circle-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="${initialOffset}"/>
                    </svg>
                    <span class="upload-progress-text">${initialProgress}%</span>
                </div>
            `;
            mediaFrame.appendChild(uploadOverlay);
        }

        // 2. Receiver on-demand download overlay with actual file size
        if (isNotDownloaded) {
            const fileSize = this._formatFileSize(metadata.size || 0);
            const downloadOverlay = document.createElement('div');
            downloadOverlay.className = 'media-download-overlay';
            downloadOverlay.setAttribute('data-message-id', message.id);
            downloadOverlay.innerHTML = `
                <button type="button" class="media-download-pill" title="Download media">
                    <div class="download-icon-wrap">
                        <svg class="dl-icon" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        <svg class="download-spinner d-none" width="20" height="20" viewBox="0 0 38 38">
                            <circle cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"/>
                            <circle class="dl-spinner-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="3" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="60"/>
                        </svg>
                    </div>
                    <span class="media-download-size">${fileSize || 'Download'}</span>
                </button>
            `;
            mediaFrame.appendChild(downloadOverlay);
        }

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);
        if (forwardedHtml || replyHtml) {
            const topDiv = document.createElement('div');
            topDiv.className = 'media-bubble-top-meta px-2 pt-1 pb-1';
            topDiv.innerHTML = `${forwardedHtml}${replyHtml}`;
            bubble.appendChild(topDiv);
        }

        bubble.appendChild(mediaFrame);

        // Footer / caption area: has timestamp on floating overlay if no caption, or caption text + timestamp if has caption
        const hasCaption = Boolean(caption && caption.trim());
        if (hasCaption) {
            bubble.classList.add('has-caption');
            const footerDiv = document.createElement('div');
            footerDiv.className = 'media-card-footer has-caption';
            footerDiv.innerHTML = `<p class="message-content">${escapeHtml(caption)}${this._getSpacerHtml(message)}</p>`;
            const metaDiv = this._createMetaElement(message, false);
            footerDiv.appendChild(metaDiv);
            bubble.appendChild(footerDiv);
        } else {
            bubble.classList.add('no-caption');
            const metaDiv = this._createMetaElement(message, true);
            mediaFrame.appendChild(metaDiv);
        }

        // Click handler: if not downloaded, trigger download; if downloaded, open fullscreen viewer
        bubble.addEventListener('click', async (e) => {
            const currentMsgId = bubble.getAttribute('data-message-id') || message.id;
            if (bubble.classList.contains('not-downloaded')) {
                e.stopPropagation();
                this._handleMediaDownload(currentMsgId, bubble);
                return;
            }
            const activeUpload = bubble.querySelector('.media-upload-overlay:not(.upload-complete)');
            const currentStatus = bubble.getAttribute('data-status');
            if (activeUpload || currentStatus === 'uploading' || currentStatus === 'sending') return;
            if (e.target.closest('.media-card-footer.has-caption')) {
                return; // Let user select or click caption text
            }
            console.log('[MEDIA_VIEWER] Opening attachment:', currentMsgId);
            let viewerUrl = mediaUrl;
            try {
                const localUrl = await deviceMediaStore.getLocalMediaUrl(currentMsgId);
                if (localUrl) {
                    viewerUrl = localUrl;
                }
            } catch (_) {}

            this.renderFullscreenMediaViewer([{
                id: currentMsgId,
                type: attachmentType,
                url: viewerUrl,
                caption: caption
            }], 0);
        });

        console.log('[MEDIA_BUBBLE] Single media bubble created');
        return bubble;
    }

    /**
     * Render media group bubble (Telegram-style)
     * Dedicated media group bubble - NOT reusing text bubbles
     * @param {Object} message - Media group message object
     * @returns {HTMLElement} Media group bubble element
     */
    renderMediaGroupBubble(message) {
        console.log('[MEDIA_GROUP] Rendering media group bubble for:', message.id);

        const attachments = message.attachments || message.metadata?.attachments || [];
        const globalCaption = message.global_caption || message.metadata?.global_caption || '';
        const attachmentCount = attachments.length;
        const isUploading = message.status === 'uploading' || message.status === 'sending' || message.isOptimistic;
        const isOwnMsg = this._isOwnMessage(message);
        const isNotDownloaded = !isOwnMsg && !isUploading && !this._isMediaDownloaded(message.id);

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${isOwnMsg ? 'sent' : 'received'} media-bubble media-group media-card-bubble group-${message.groupPosition}`;
        if (isNotDownloaded) {
            bubble.classList.add('not-downloaded');
        }
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);

        const mediaFrame = document.createElement('div');
        mediaFrame.className = 'media-card-frame';

        // Create media content grid
        const contentDiv = document.createElement('div');
        contentDiv.className = `media-content ${this._getTelegramLayoutClass(attachmentCount)}`;

        // Determine visible attachments (max 4 for chat view)
        const visibleLimit = 4;
        const visibleAttachments = attachments.slice(0, visibleLimit);
        const hiddenCount = attachmentCount - visibleLimit;

        // Render each media tile
        visibleAttachments.forEach((attachment, index) => {
            const tile = this._renderMediaTile(attachment, index, attachmentCount, hiddenCount, isNotDownloaded);
            contentDiv.appendChild(tile);
        });

        mediaFrame.appendChild(contentDiv);

        // Check and use local media URLs for tiles if already downloaded.
        // Key order matters: per-tile keys first (that's how downloads are
        // stored), then the bundle key. hasMedia() never evicts, so probing
        // a never-stored server attachment id can't fire a spurious
        // media-evicted event that would flash the overlay back.
        if (!isNotDownloaded && !isUploading) {
            Promise.all(attachments.map(async (att, idx) => {
                const keysToTry = [`${message.id}_${idx}`, String(message.id)];
                if (att?.id) keysToTry.push(String(att.id));
                let localUrl = null;
                for (const k of keysToTry) {
                    try {
                        if (deviceMediaStore.hasMedia(k)) {
                            localUrl = await deviceMediaStore.getLocalMediaUrl(k);
                            if (localUrl) break;
                        }
                    } catch (_) {}
                }
                if (localUrl) {
                    const tiles = mediaFrame.querySelectorAll('.media-tile');
                    const tile = tiles[idx];
                    const el = tile?.querySelector('img, video');
                    if (el && el.src !== localUrl) {
                        el.src = localUrl;
                        el.removeAttribute('data-src');
                        el.classList.remove('d-none');
                        tile.querySelector('.tile-placeholder-wrap')?.remove();
                    }
                }
            })).catch(() => {});
        }

        // 1. Sender optimistic upload progress overlay
        if (isUploading) {
            const initialProgress = message.metadata?.uploadProgress || 0;
            const circumference = 94.25;
            const initialOffset = Math.max(0, circumference * (1 - initialProgress / 100)).toFixed(1);
            const uploadOverlay = document.createElement('div');
            uploadOverlay.className = 'media-upload-overlay group-upload-overlay';
            uploadOverlay.setAttribute('data-upload-id', message.id);
            uploadOverlay.innerHTML = `
                <div class="upload-progress-widget">
                    <svg class="upload-progress-circle" width="48" height="48" viewBox="0 0 38 38">
                        <circle class="upload-circle-bg" cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.25)" stroke-width="2.5" fill="none"/>
                        <circle class="upload-circle-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="2.5" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="${initialOffset}"/>
                    </svg>
                    <span class="upload-progress-text">${initialProgress}%</span>
                </div>
            `;
            mediaFrame.appendChild(uploadOverlay);
        }

        // 2. Receiver bundle download overlay with combined bundle size
        if (isNotDownloaded) {
            const bundleSize = attachments.reduce((acc, att) => acc + (att.size || 0), 0);
            const formattedBundleSize = this._formatFileSize(bundleSize);
            const downloadOverlay = document.createElement('div');
            downloadOverlay.className = 'media-download-overlay group-download-overlay';
            downloadOverlay.setAttribute('data-message-id', message.id);
            downloadOverlay.innerHTML = `
                <button type="button" class="media-download-pill group-download-pill" title="Download bundle">
                    <div class="download-icon-wrap">
                        <svg class="dl-icon" width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
                            <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                            <polyline points="7 10 12 15 17 10"></polyline>
                            <line x1="12" y1="15" x2="12" y2="3"></line>
                        </svg>
                        <svg class="download-spinner d-none" width="22" height="22" viewBox="0 0 38 38">
                            <circle cx="19" cy="19" r="15" stroke="rgba(255,255,255,0.3)" stroke-width="3" fill="none"/>
                            <circle class="dl-spinner-bar" cx="19" cy="19" r="15" stroke="#ffffff" stroke-width="3" fill="none" stroke-linecap="round" stroke-dasharray="94.25" stroke-dashoffset="60"/>
                        </svg>
                    </div>
                    <div class="group-dl-text">
                        <span class="group-dl-label">Download all</span>
                        <span class="media-download-size">${formattedBundleSize || ''}</span>
                    </div>
                </button>
            `;
            mediaFrame.appendChild(downloadOverlay);
        }

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);
        if (forwardedHtml || replyHtml) {
            const topDiv = document.createElement('div');
            topDiv.className = 'media-bubble-top-meta px-2 pt-1 pb-1';
            topDiv.innerHTML = `${forwardedHtml}${replyHtml}`;
            bubble.appendChild(topDiv);
        }

        bubble.appendChild(mediaFrame);

        // Footer / caption area: has timestamp on floating overlay if no caption, or global caption text + timestamp if has caption
        const hasGlobalCaption = Boolean(globalCaption && globalCaption.trim());
        if (hasGlobalCaption) {
            bubble.classList.add('has-caption');
            const footerDiv = document.createElement('div');
            footerDiv.className = 'media-card-footer has-caption';
            footerDiv.innerHTML = `<p class="message-content">${escapeHtml(globalCaption)}${this._getSpacerHtml(message)}</p>`;
            const metaDiv = this._createMetaElement(message, false);
            footerDiv.appendChild(metaDiv);
            bubble.appendChild(footerDiv);
        } else {
            bubble.classList.add('no-caption');
            const metaDiv = this._createMetaElement(message, true);
            mediaFrame.appendChild(metaDiv);
        }

        // Click handler: if not downloaded, trigger download; if downloaded, open fullscreen viewer
        bubble.addEventListener('click', async (e) => {
            const currentMsgId = bubble.getAttribute('data-message-id') || message.id;
            if (bubble.classList.contains('not-downloaded')) {
                e.stopPropagation();
                this._handleMediaDownload(currentMsgId, bubble);
                return;
            }
            const activeUpload = bubble.querySelector('.media-upload-overlay:not(.upload-complete)');
            const currentStatus = bubble.getAttribute('data-status');
            if (activeUpload || currentStatus === 'uploading' || currentStatus === 'sending') return;
            if (e.target.closest('.media-card-footer.has-caption')) {
                return; // Let user select or click caption text
            }
            const clickedTile = e.target.closest('.media-tile');
            let startIndex = 0;
            if (clickedTile) {
                const tiles = Array.from(bubble.querySelectorAll('.media-tile'));
                const idx = tiles.indexOf(clickedTile);
                if (idx !== -1) startIndex = idx;
            }
            console.log('[MEDIA_VIEWER] Opening media group:', currentMsgId, 'startIndex:', startIndex);

            // Fetch local offline URLs for attachments if available.
            // Same key order as render rehydration: per-tile, bundle, then
            // server id — never probe a never-stored id first (evict spam).
            const preparedAttachments = await this._prepareGroupViewerUrls(message, attachments);

            this.renderFullscreenMediaViewer(preparedAttachments, startIndex);
        });

        console.log('[MEDIA_GROUP] Media group bubble created');
        return bubble;
    }

    /**
     * Resolve viewer URLs for group attachments, preferring local downloads.
     */
    async _prepareGroupViewerUrls(message, attachments) {
        return Promise.all(attachments.map(async (att, idx) => {
            const keysToTry = [`${message.id}_${idx}`, String(message.id)];
            if (att.id) keysToTry.push(String(att.id));
            let url = att.file_url || att.file || att.url || '';
            try {
                for (const k of keysToTry) {
                    if (deviceMediaStore.hasMedia(k)) {
                        const localUrl = await deviceMediaStore.getLocalMediaUrl(k);
                        if (localUrl) { url = localUrl; break; }
                    }
                }
            } catch (_) {}
            return {
                ...att,
                url: url,
                file_url: url
            };
        }));
    }

    /**
     * Render media tile for group
     * @param {Object} attachment - Attachment object
     * @param {number} index - Attachment index
     * @param {number} total - Total attachments
     * @param {number} hiddenCount - Number of hidden attachments
     * @param {boolean} isNotDownloaded - Whether bundle is pending download
     * @returns {HTMLElement} Media tile element
     */
    _renderMediaTile(attachment, index, total, hiddenCount, isNotDownloaded = false) {
        console.log('[MEDIA_TILE] Rendering tile', index, 'of', total);

        const tile = document.createElement('div');
        tile.className = 'media-tile';

        const fileType = attachment.file_type || attachment.type || 'image';
        const fileUrl = attachment.file_url || attachment.file || attachment.url || '';
        const caption = attachment.caption || '';

        // Add video class if applicable
        if (fileType === 'video') {
            tile.classList.add('video-tile');
        }

        let tileContent = '';

        switch (fileType) {
            case 'image':
                if (isNotDownloaded) {
                    const tileThumb = attachment.thumbnail || attachment.previewUrl || fileUrl;
                    tileContent = `
                        ${tileThumb ? `
                            <img src="${escapeHtml(tileThumb)}" data-full-src="${escapeHtml(fileUrl)}" alt="Image preview" loading="lazy" class="tile-image-thumb not-downloaded-thumb" style="width: 100%; height: 100%; object-fit: cover; filter: blur(14px) brightness(0.72); transform: scale(1.08); pointer-events: none; display: block; border-radius: 8px !important;">
                        ` : `
                            <div class="tile-placeholder-wrap" style="border-radius: 8px !important;">
                                <svg class="tile-placeholder-icon" width="28" height="28" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
                                    <rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect>
                                    <circle cx="8.5" cy="8.5" r="1.5"></circle>
                                    <polyline points="21 15 16 10 5 21"></polyline>
                                </svg>
                            </div>
                            <img data-full-src="${escapeHtml(fileUrl)}" alt="Image" class="d-none">
                        `}
                    `;
                } else {
                    tileContent = `<img src="${escapeHtml(fileUrl)}" alt="Image" loading="lazy" style="border-radius: 8px !important;">`;
                }
                break;
            case 'video':
                const tileThumb = attachment.thumbnail || attachment.previewUrl || '';
                const tilePosterAttr = tileThumb ? `poster="${escapeHtml(tileThumb)}"` : '';
                if (isNotDownloaded) {
                    const videoSrc = `${escapeHtml(fileUrl)}#t=0.001`;
                    tileContent = `
                        <div class="tile-play-icon">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <polygon points="5,3 19,12 5,21"></polygon>
                            </svg>
                        </div>
                        ${tileThumb ? `
                            <img src="${escapeHtml(tileThumb)}" data-full-src="${escapeHtml(fileUrl)}" alt="Video preview" loading="lazy" class="tile-video-thumb not-downloaded-thumb" style="width: 100%; height: 100%; object-fit: cover; filter: blur(14px) brightness(0.72); transform: scale(1.08); pointer-events: none; display: block; border-radius: 8px !important;">
                        ` : `
                            <video src="${videoSrc}" preload="${this._videoPreload(videoSrc)}" ${tilePosterAttr} data-full-src="${escapeHtml(fileUrl)}" data-no-inline="true" data-autoplay="false" data-chat-media="true" muted playsinline tabindex="-1" class="not-downloaded-thumb" style="width: 100%; height: 100%; object-fit: cover; filter: blur(14px) brightness(0.72); transform: scale(1.08); pointer-events: none; display: block; border-radius: 8px !important;"></video>
                        `}
                    `;
                } else {
                    tileContent = `
                        <div class="tile-play-icon">
                            <svg viewBox="0 0 24 24" fill="currentColor">
                                <polygon points="5,3 19,12 5,21"></polygon>
                            </svg>
                        </div>
                        ${tileThumb ? `
                            <img src="${escapeHtml(tileThumb)}" alt="Video preview" loading="lazy" style="width: 100%; height: 100%; object-fit: cover; pointer-events: none; display: block; border-radius: 8px !important;">
                            <video src="${escapeHtml(fileUrl)}" preload="${this._videoPreload(fileUrl)}" ${tilePosterAttr} data-no-inline="true" data-autoplay="false" data-chat-media="true" playsinline tabindex="-1" style="display: none;"></video>
                        ` : `
                            <video src="${escapeHtml(fileUrl)}" preload="${this._videoPreload(fileUrl)}" data-no-inline="true" data-autoplay="false" data-chat-media="true" playsinline tabindex="-1" style="width: 100%; height: 100%; object-fit: cover; pointer-events: none; display: block; border-radius: 8px !important;"></video>
                        `}
                    `;
                }
                break;
            default:
                tileContent = `<div class="tile-placeholder" style="border-radius: 8px !important;">${fileType.toUpperCase()}</div>`;
        }

        tile.innerHTML = tileContent;

        // Strictly prevent inline playback in media tile and add canvas frame fallback
        tile.querySelectorAll('video').forEach(vid => {
            vid.addEventListener('play', (e) => {
                e.preventDefault();
                try { vid.pause(); } catch (_) {}
            });
            if (!vid.getAttribute('poster') && vid.src && !vid.src.endsWith('#t=0.001')) {
                vid.addEventListener('loadeddata', () => {
                    try {
                        if (vid.videoWidth && vid.videoHeight) {
                            const c = document.createElement('canvas');
                            c.width = Math.min(vid.videoWidth, 480);
                            c.height = Math.max(1, Math.round(c.width * (vid.videoHeight / vid.videoWidth)));
                            c.getContext('2d').drawImage(vid, 0, 0, c.width, c.height);
                            const poster = c.toDataURL('image/jpeg', 0.7);
                            vid.setAttribute('poster', poster);
                        }
                    } catch (_) {}
                }, { once: true });
            }
        });

        // Add overlay count for last visible tile if there are hidden items
        if (hiddenCount > 0 && index === 3) {
            console.log('[MEDIA_OVERLAY] Adding overlay count:', hiddenCount);
            const overlay = document.createElement('div');
            overlay.className = 'media-overlay';
            overlay.textContent = `+${hiddenCount}`;
            tile.appendChild(overlay);
        }

        return tile;
    }

    /**
     * Get Telegram-style layout class based on attachment count
     * @param {number} count - Number of attachments
     * @returns {string} Layout class
     */
    _getTelegramLayoutClass(count) {
        if (count === 1) return 'media-group--1';
        if (count === 2) return 'media-group--2';
        if (count === 3) return 'media-group--3';
        if (count === 4) return 'media-group--4';
        return 'media-group--many';
    }

    /**
     * Render fullscreen immersive media viewer
     * @param {Array} attachments - Array of attachment objects
     * @param {number} startIndex - Index of attachment to show first
     */
    renderFullscreenMediaViewer(attachments, startIndex = 0) {
        console.log('[MEDIA_VIEWER] Opening fullscreen viewer');
        console.log('[MEDIA_VIEWER] Total attachments:', attachments.length);
        console.log('[MEDIA_VIEWER] Starting at index:', startIndex);

        // Remove existing viewer if present
        const existingViewer = document.querySelector('.media-viewer-overlay');
        if (existingViewer) {
            existingViewer.remove();
        }

        // Create viewer overlay
        const overlay = document.createElement('div');
        overlay.className = 'media-viewer-overlay';

        // Create viewer container
        const container = document.createElement('div');
        container.className = 'media-viewer-container';

        let currentIndex = startIndex;

        // Create header with download and close button
        const header = document.createElement('div');
        header.className = 'media-viewer-header';

        const downloadButton = document.createElement('button');
        downloadButton.className = 'media-viewer-btn media-viewer-download';
        downloadButton.title = 'Save to device / Download';
        downloadButton.innerHTML = `
            <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">
                <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
                <polyline points="7 10 12 15 17 10"></polyline>
                <line x1="12" y1="15" x2="12" y2="3"></line>
            </svg>
        `;
        downloadButton.addEventListener('click', (e) => {
            e.stopPropagation();
            const currentItem = attachments[currentIndex];
            if (currentItem) {
                const url = currentItem.file_url || currentItem.file || currentItem.url || '';
                const filename = currentItem.filename || currentItem.name || url.split('/').pop()?.split('?')[0] || `media_${Date.now()}`;
                deviceMediaStore.downloadToDevice(url, filename);
            }
        });

        const closeViewer = () => {
            const vids = overlay.querySelectorAll('video');
            vids.forEach(v => {
                try { v.pause(); v.src = ''; } catch (_) {}
            });
            overlay.classList.remove('show');
            setTimeout(() => overlay.remove(), 300);
            document.removeEventListener('keydown', handleKeydown);
        };

        const closeButton = document.createElement('button');
        closeButton.className = 'media-viewer-close';
        closeButton.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <line x1="18" y1="6" x2="6" y2="18"></line>
                <line x1="6" y1="6" x2="18" y2="18"></line>
            </svg>
        `;
        closeButton.addEventListener('click', closeViewer);

        header.appendChild(downloadButton);
        header.appendChild(closeButton);

        // Create main content area
        const main = document.createElement('div');
        main.className = 'media-viewer-main';

        // Create navigation buttons
        const prevButton = document.createElement('button');
        prevButton.className = 'media-viewer-nav prev';
        prevButton.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="15,18 9,12 15,6"></polyline>
            </svg>
        `;

        const nextButton = document.createElement('button');
        nextButton.className = 'media-viewer-nav next';
        nextButton.innerHTML = `
            <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
                <polyline points="9,18 15,12 9,6"></polyline>
            </svg>
        `;

        // Create content element
        const content = document.createElement('div');
        content.className = 'media-viewer-content';

        // Create footer with counter and caption
        const footer = document.createElement('div');
        footer.className = 'media-viewer-footer';

        const counter = document.createElement('div');
        counter.className = 'media-viewer-counter';
        counter.textContent = `${startIndex + 1} / ${attachments.length}`;

        const caption = document.createElement('div');
        caption.className = 'media-viewer-caption';
        caption.textContent = attachments[startIndex]?.caption || '';

        footer.appendChild(counter);
        footer.appendChild(caption);

        // Assemble viewer
        main.appendChild(prevButton);
        main.appendChild(content);
        main.appendChild(nextButton);
        container.appendChild(header);
        container.appendChild(main);
        container.appendChild(footer);
        overlay.appendChild(container);

        // Add to DOM
        document.body.appendChild(overlay);

        // Trigger animation
        requestAnimationFrame(() => {
            overlay.classList.add('show');
        });

        // Current index state
        currentIndex = startIndex;

        // Function to render current attachment
        const renderAttachment = (index) => {
            console.log('[MEDIA_VIEWER] Rendering attachment at index:', index);
            const attachment = attachments[index];
            if (!attachment) return;

            const fileType = attachment.file_type || attachment.type || 'image';
            const fileUrl = attachment.file_url || attachment.url || '';

            if (fileType === 'video') {
                content.innerHTML = `
                    <div class="media-viewer-video-container">
                        <video class="media-viewer-video"
                               src="${escapeHtml(fileUrl)}"
                               autoplay
                               playsinline
                               loop
                               preload="auto"
                               data-chat-media="true">
                            Your browser does not support the video tag.
                        </video>

                        <!-- Tap Hitbox for play / pause -->
                        <button type="button" class="media-viewer-tap-hitbox" aria-label="Play or pause video"></button>

                        <!-- Transient Play/Pause HUD Indicator -->
                        <div class="media-viewer-play-hud" aria-hidden="true">
                            <i class="bi bi-play-fill"></i>
                        </div>

                        <!-- Floating Mute / Unmute Toggle Button -->
                        <button type="button" class="media-viewer-floating-mute" title="Sound" aria-label="Toggle mute">
                            <i class="bi bi-volume-up-fill"></i>
                        </button>

                        <!-- Draggable Micro-Scrubber / Progress Bar -->
                        <div class="media-viewer-progress-container" aria-label="Video scrubber">
                            <div class="media-viewer-progress-bar"></div>
                        </div>
                    </div>
                `;

                const video = content.querySelector('.media-viewer-video');
                const tapHitbox = content.querySelector('.media-viewer-tap-hitbox');
                const playHud = content.querySelector('.media-viewer-play-hud');
                const muteBtn = content.querySelector('.media-viewer-floating-mute');
                const progressContainer = content.querySelector('.media-viewer-progress-container');
                const progressBar = content.querySelector('.media-viewer-progress-bar');

                if (video) {
                    // Chat full media videos are NOT muted by default
                    video.muted = false;
                    video.volume = 1.0;

                    const updateMuteUi = () => {
                        if (!muteBtn || !video) return;
                        const isMuted = Boolean(video.muted || video.volume === 0);
                        muteBtn.innerHTML = isMuted
                            ? '<i class="bi bi-volume-mute-fill"></i>'
                            : '<i class="bi bi-volume-up-fill"></i>';
                        muteBtn.title = isMuted ? 'Unmute' : 'Mute';
                        muteBtn.setAttribute('aria-label', isMuted ? 'Unmute video' : 'Mute video');
                    };

                    video.addEventListener('volumechange', updateMuteUi);
                    updateMuteUi();

                    // Play attempt
                    const playPromise = video.play();
                    if (playPromise !== undefined) {
                        playPromise.then(() => {
                            updateMuteUi();
                        }).catch(() => {
                            // If browser autoplay policy with audio fails, fallback to muted then user can tap unmute
                            video.muted = true;
                            updateMuteUi();
                            video.play().catch(() => {});
                        });
                    }

                    // Transient HUD trigger
                    const showHud = (isPlay) => {
                        if (!playHud) return;
                        playHud.innerHTML = isPlay ? '<i class="bi bi-play-fill"></i>' : '<i class="bi bi-pause-fill"></i>';
                        playHud.classList.add('show');
                        clearTimeout(playHud._hudTimeout);
                        playHud._hudTimeout = setTimeout(() => {
                            playHud.classList.remove('show');
                        }, 500);
                    };

                    // Tap to play / pause
                    if (tapHitbox) {
                        tapHitbox.addEventListener('click', (e) => {
                            e.stopPropagation();
                            if (video.paused) {
                                video.play().then(() => {
                                    showHud(true);
                                    updateMuteUi();
                                }).catch(() => {});
                            } else {
                                video.pause();
                                showHud(false);
                            }
                        });
                    }

                    // Floating mute / unmute button
                    if (muteBtn) {
                        muteBtn.addEventListener('click', (e) => {
                            e.stopPropagation();
                            video.muted = !video.muted;
                            if (!video.muted && video.volume === 0) {
                                video.volume = 1.0;
                            }
                            updateMuteUi();
                        });
                    }

                    // Progress update
                    let isDragging = false;
                    video.addEventListener('timeupdate', () => {
                        if (!isDragging && progressBar && video.duration) {
                            const pct = Math.min(100, Math.max(0, (video.currentTime / video.duration) * 100));
                            progressBar.style.width = `${pct}%`;
                        }
                    });

                    // Draggable Progress Scrubber
                    if (progressContainer && progressBar) {
                        const seekToPosition = (clientX) => {
                            const rect = progressContainer.getBoundingClientRect();
                            if (rect.width <= 0) return;
                            const offsetX = Math.max(0, Math.min(clientX - rect.left, rect.width));
                            const fraction = offsetX / rect.width;
                            const pct = fraction * 100;
                            progressBar.style.width = `${pct}%`;
                            if (video.duration) {
                                video.currentTime = fraction * video.duration;
                            }
                        };

                        const handleDragStart = (e) => {
                            e.stopPropagation();
                            e.preventDefault();
                            isDragging = true;
                            progressContainer.classList.add('is-dragging');
                            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
                            seekToPosition(clientX);

                            const handleDragMove = (moveEvt) => {
                                moveEvt.preventDefault();
                                const currentX = moveEvt.touches ? moveEvt.touches[0].clientX : moveEvt.clientX;
                                seekToPosition(currentX);
                            };

                            const handleDragEnd = () => {
                                isDragging = false;
                                progressContainer.classList.remove('is-dragging');
                                window.removeEventListener('mousemove', handleDragMove);
                                window.removeEventListener('mouseup', handleDragEnd);
                                window.removeEventListener('touchmove', handleDragMove);
                                window.removeEventListener('touchend', handleDragEnd);
                            };

                            window.addEventListener('mousemove', handleDragMove, { passive: false });
                            window.addEventListener('mouseup', handleDragEnd);
                            window.addEventListener('touchmove', handleDragMove, { passive: false });
                            window.addEventListener('touchend', handleDragEnd);
                        };

                        progressContainer.addEventListener('mousedown', handleDragStart);
                        progressContainer.addEventListener('touchstart', handleDragStart, { passive: false });
                    }
                }
            } else {
                content.innerHTML = `<img src="${escapeHtml(fileUrl)}" alt="Media" class="media-viewer-content">`;
            }

            // Update counter and caption
            counter.textContent = `${index + 1} / ${attachments.length}`;
            caption.textContent = attachment.caption || '';

            // Update navigation buttons visibility
            prevButton.style.display = index > 0 ? 'flex' : 'none';
            nextButton.style.display = index < attachments.length - 1 ? 'flex' : 'none';
        };

        // Navigation handlers
        prevButton.addEventListener('click', () => {
            if (currentIndex > 0) {
                currentIndex--;
                renderAttachment(currentIndex);
            }
        });

        nextButton.addEventListener('click', () => {
            if (currentIndex < attachments.length - 1) {
                currentIndex++;
                renderAttachment(currentIndex);
            }
        });

        // Keyboard navigation
        const handleKeydown = (e) => {
            if (e.key === 'Escape') {
                closeViewer();
            } else if (e.key === 'ArrowLeft' && currentIndex > 0) {
                currentIndex--;
                renderAttachment(currentIndex);
            } else if (e.key === 'ArrowRight' && currentIndex < attachments.length - 1) {
                currentIndex++;
                renderAttachment(currentIndex);
            }
        };

        document.addEventListener('keydown', handleKeydown);

        // Initial render
        renderAttachment(currentIndex);

        console.log('[MEDIA_VIEWER] Fullscreen viewer opened successfully');
    }

    /**
     * Create media group message element (pure DOM creation)
     * @param {Object} message - Media group message object
     * @returns {HTMLElement} Media group message element
     */
    _createMediaGroupMessage(message) {
        console.log('[RENDERER] [MEDIA_GROUP] Creating media group message for:', message.id);
        console.log('[RENDERER] [MEDIA_GROUP] [DEBUG] FULL MESSAGE:', JSON.stringify(message, null, 2));
        console.log('[RENDERER] [MEDIA_GROUP] [DEBUG] ATTACHMENTS (top-level):', message.attachments);
        console.log('[RENDERER] [MEDIA_GROUP] [DEBUG] METADATA:', message.metadata);
        console.log('[RENDERER] [MEDIA_GROUP] [DEBUG] METADATA ATTACHMENTS:', message.metadata?.attachments);

        // Build wrapper containing avatar, bubble and meta (timestamp + read receipt)
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;

            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }

            wrapperDiv.appendChild(avatarContainer);
        }

        // Content wrapper for media grid and meta
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        // Use new Telegram-style media group bubble (meta is already inside)
        const mediaBubble = this.renderMediaGroupBubble(message);
        contentWrapper.appendChild(mediaBubble);

        console.log('[RENDERER] [MEDIA_GROUP] Message bubble created with Telegram-style architecture');

        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Create media message element (pure DOM creation)
     * @param {Object} message - Media message object
     * @returns {HTMLElement} Media message element
     */
    _createMediaMessage(message) {
        console.log('[RENDERER] [MEDIA] Creating media message for:', message.id);

        const metadata = message.metadata || {};
        if (!metadata.url) {
            metadata.url = message.attachment_url || (typeof message.attachment === 'string' ? message.attachment : '') || (metadata.attachments && metadata.attachments[0] ? (metadata.attachments[0].file_url || metadata.attachments[0].file || metadata.attachments[0].url) : '') || '';
        }
        if (!metadata.type) {
            metadata.type = message.attachment_type || (metadata.attachments && metadata.attachments[0] ? metadata.attachments[0].file_type : '') || (metadata.url.match(/\.(mp4|webm|mov|ogg)$/i) ? 'video' : (metadata.url.match(/\.(jpg|jpeg|png|webp|gif)$/i) ? 'image' : 'file'));
        }
        const attachmentType = metadata.type || 'file';
        const isImageOrVideo = attachmentType === 'image' || attachmentType === 'video';

        // Build wrapper containing avatar, bubble and meta (timestamp + read receipt)
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;

            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }

            wrapperDiv.appendChild(avatarContainer);
        }

        // Content wrapper for media and meta
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        // For stickers, use floating transparent card
        // For GIFs, use auto-playing looping video/img with GIF badge
        // For images/videos, use Telegram-style single media bubble
        // For other types, use dedicated bubble
        if (metadata.is_sticker || metadata.type === 'sticker' || attachmentType === 'sticker' || message.type === 'sticker' || message.attachment_type === 'sticker') {
            console.log('[RENDERER] [STICKER] Using Sticker bubble');
            const stickerBubble = this.renderStickerBubble(message, metadata);
            contentWrapper.appendChild(stickerBubble);
        } else if (metadata.is_gif || metadata.type === 'gif' || attachmentType === 'gif' || message.type === 'gif' || message.attachment_type === 'gif' || (metadata.url && metadata.url.toLowerCase().includes('.gif'))) {
            console.log('[RENDERER] [GIF] Using GIF autoplay bubble');
            const gifBubble = this.renderGifBubble(message, metadata);
            contentWrapper.appendChild(gifBubble);
        } else if (isImageOrVideo && metadata.url) {
            console.log('[RENDERER] [MEDIA] Using Telegram-style single media bubble');
            const mediaBubble = this.renderSingleMediaBubble(message);
            contentWrapper.appendChild(mediaBubble);
        } else if (metadata.is_voice_note || attachmentType === 'voice_note' || message.is_voice_note) {
            console.log('[RENDERER] [VOICE_NOTE] Using Voice Note bubble with waveform');
            const voiceBubble = this.renderVoiceNoteBubble(message, metadata);
            contentWrapper.appendChild(voiceBubble);
        } else if (attachmentType === 'audio') {
            console.log('[RENDERER] [AUDIO] Using Audio Track bubble');
            const audioBubble = this.renderAudioTrackBubble(message, metadata);
            contentWrapper.appendChild(audioBubble);
        } else {
            console.log('[RENDERER] [DOC] Using Document card bubble');
            const docBubble = this.renderDocumentBubble(message, metadata);
            contentWrapper.appendChild(docBubble);
        }

        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Render distinct document card bubble
     */
    renderDocumentBubble(message, metadata = {}) {
        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} document-bubble group-${message.groupPosition}`;
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);
        this._applyBubbleStyle(bubble);

        const fileUrl = metadata.url || '';
        const rawName = metadata.file_name || message.content || 'Document';
        const fileName = rawName.split('/').pop().split('\\').pop();
        const ext = (fileName.split('.').pop() || 'doc').toLowerCase();
        const fileSizeFormatted = this._formatFileSize(metadata.size);
        const docStyles = this._getDocumentTypeInfo(ext);

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        const isUploading = message.status === 'uploading' || message.status === 'sending' || message.isOptimistic;

        bubble.innerHTML = `
            ${forwardedHtml}
            ${replyHtml}
            <div class="document-card-inner">
                <div class="document-badge ${docStyles.badgeClass}">
                    <i class="bi ${docStyles.icon}"></i>
                    <span class="document-ext-pill">${ext.toUpperCase().slice(0, 4)}</span>
                </div>
                <div class="document-details">
                    <div class="document-filename" title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</div>
                    <div class="document-meta-row">
                        ${fileSizeFormatted ? `<span class="document-size-label">${fileSizeFormatted}</span><span class="document-dot">•</span>` : ''}
                        <span class="document-type-label">${docStyles.label}</span>
                        ${isUploading ? `<span class="document-dot">•</span><span class="document-progress-text text-primary">0%</span>` : ''}
                    </div>
                </div>
                ${isUploading ? `
                    <div class="document-download-btn uploading" title="Uploading...">
                        <span class="spinner-border spinner-border-sm" role="status" style="width: 1rem; height: 1rem; border-width: 2px;"></span>
                    </div>
                ` : `
                    <a href="${escapeHtml(fileUrl)}" download="${escapeHtml(fileName)}" target="_blank" rel="noopener" class="document-download-btn" title="Download ${escapeHtml(fileName)}" aria-label="Download">
                        <i class="bi bi-arrow-down"></i>
                    </a>
                `}
            </div>
        `;

        const metaDiv = this._createMetaElement(message, false);
        bubble.appendChild(metaDiv);
        return bubble;
    }

    /**
     * Render distinct sticker bubble (transparent, floating, borderless with frosted meta pill)
     */
    renderStickerBubble(message, metadata = {}) {
        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} sticker-bubble group-${message.groupPosition}`;
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);

        const stickerUrl = metadata.url || message.attachment_url || message.link_image || metadata.preview_url || '';
        const timeStr = this._formatMessageTime(message.timestamp);
        const isOwn = message.isOwn || (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        bubble.innerHTML = `
            ${forwardedHtml}
            ${replyHtml}
            <div class="sticker-media-container">
                <img src="${escapeHtml(stickerUrl)}" alt="Sticker" class="sticker-img" loading="lazy" />
            </div>
            <div class="sticker-meta-pill">
                <span class="message-time">${timeStr}</span>
                ${isOwn ? `<span class="message-read-receipt status-${message.status || 'sent'}">${this._getStatusIcon(message.status, message.id, message)}</span>` : ''}
            </div>
        `;

        return bubble;
    }

    /**
     * Render distinct GIF bubble (auto-playing, looping, with persistent GIF badge & frosted meta)
     */
    renderGifBubble(message, metadata = {}) {
        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} gif-bubble group-${message.groupPosition}`;
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);

        const gifUrl = metadata.url || message.attachment_url || message.link_image || metadata.preview_url || '';
        const timeStr = this._formatMessageTime(message.timestamp);
        const isOwn = message.isOwn || (this.currentUserId !== null && Number(message.senderId) === Number(this.currentUserId));
        const isVideo = gifUrl.toLowerCase().includes('.mp4') || gifUrl.toLowerCase().includes('.webm');

        const w = Number(metadata.width || metadata.file_width || 0);
        const h = Number(metadata.height || metadata.file_height || 0);
        let containerStyle = 'width: min(280px, calc(100vw - 80px)); ';
        if (w > 0 && h > 0) {
            containerStyle += `aspect-ratio: ${w} / ${h}; `;
        } else {
            containerStyle += 'aspect-ratio: 4 / 3; ';
        }

        const mediaTag = isVideo
            ? `<video src="${escapeHtml(gifUrl)}" class="gif-media" autoplay loop muted playsinline preload="auto" disablepictureinpicture onloadedmetadata="if(this.videoWidth && this.videoHeight && this.parentElement) { this.parentElement.style.aspectRatio = this.videoWidth + '/' + this.videoHeight; }"></video>`
            : `<img src="${escapeHtml(gifUrl)}" alt="GIF" class="gif-media" loading="lazy" onload="if(this.naturalWidth && this.naturalHeight && this.parentElement) { this.parentElement.style.aspectRatio = this.naturalWidth + '/' + this.naturalHeight; }" />`;

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        bubble.innerHTML = `
            ${forwardedHtml}
            ${replyHtml}
            <div class="gif-media-container" style="${containerStyle}">
                ${mediaTag}
                <span class="gif-badge">GIF</span>
                <div class="media-meta-overlay">
                    <span class="message-time">${timeStr}</span>
                    ${isOwn ? `<span class="message-read-receipt status-${message.status || 'sent'}">${this._getStatusIcon(message.status, message.id, message)}</span>` : ''}
                </div>
            </div>
        `;

        bubble.addEventListener('click', () => {
            this.renderFullscreenMediaViewer([{
                id: message.id,
                type: isVideo ? 'video' : 'image',
                url: gifUrl,
                caption: ''
            }], 0);
        });

        return bubble;
    }

    /**
     * Ownership with sender fallback. Store validation normally stamps
     * isOwn, but any message arriving without it (history edge cases,
     * direct WS payloads) must still classify the sender correctly —
     * otherwise your own bubbles render as downloadable/received.
     */
    _isOwnMessage(message) {
        if (!message) return false;
        if (message.isOwn !== undefined) return Boolean(message.isOwn);
        if (this.currentUserId === null || this.currentUserId === undefined) return false;
        return Number(message.senderId) === Number(this.currentUserId);
    }

    /**
     * Render distinct voice note bubble with interactive waveform and speed toggle
     */
    renderVoiceNoteBubble(message, metadata = {}) {
        const isUploading = message.status === 'uploading' || message.status === 'sending' || message.isOptimistic;
        const isOwnMsg = this._isOwnMessage(message);
        const isNotDownloaded = !isOwnMsg && !isUploading && !this._isMediaDownloaded(message.id);

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${isOwnMsg ? 'sent' : 'received'} voice-note-bubble group-${message.groupPosition}`;
        if (isNotDownloaded) {
            bubble.classList.add('not-downloaded');
        }
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);
        this._applyBubbleStyle(bubble);

        const audioUrl = metadata.url || '';
        const sizeStr = this._formatFileSize(metadata.size || message.file_size || 0);

        // Generate 26 balanced waveform bars
        const barHeights = [25, 45, 70, 50, 85, 60, 95, 75, 40, 70, 90, 35, 55, 80, 100, 65, 45, 85, 60, 75, 40, 65, 50, 80, 55, 30];
        const barsHtml = barHeights.map((h, i) => `<span class="vn-bar" data-idx="${i}" style="height: ${h}%;"></span>`).join('');

        const playBtnClass = isNotDownloaded ? 'vn-play-btn vn-download-mode' : 'vn-play-btn';
        let playIconHtml = isNotDownloaded
            ? `<i class="bi bi-arrow-down vn-play-icon"></i>`
            : `<i class="bi bi-play-fill vn-play-icon"></i>`;
        if (isUploading) {
            playIconHtml = `<span class="spinner-border spinner-border-sm" role="status" style="width: 1rem; height: 1rem; border-width: 2px;"></span>`;
        }

        const audioTag = isNotDownloaded
            ? `<audio data-src="${escapeHtml(audioUrl)}" preload="none" class="d-none vn-audio-el" style="display:none!important;position:absolute!important;width:0!important;height:0!important;opacity:0!important;pointer-events:none!important;"></audio>`
            : `<audio src="${escapeHtml(audioUrl)}" preload="metadata" class="d-none vn-audio-el" style="display:none!important;position:absolute!important;width:0!important;height:0!important;opacity:0!important;pointer-events:none!important;"></audio>`;

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        if (replyHtml) {
            bubble.classList.add('has-reply-quote');
        }

        const initialTimer = isUploading
            ? '0%'
            : (metadata.duration && isFinite(Number(metadata.duration)) ? this._formatAudioDuration(Number(metadata.duration)) : '0:00');
        bubble.innerHTML = `${forwardedHtml}${replyHtml}<div class="voice-note-inner"><button type="button" class="${playBtnClass}" aria-label="${isNotDownloaded ? 'Download voice note' : 'Play voice note'}" title="${isNotDownloaded ? 'Download voice note' : 'Play voice note'}">${playIconHtml}</button><div class="vn-content"><div class="vn-waveform-container" role="progressbar" aria-valuenow="0" aria-valuemin="0" aria-valuemax="100"><div class="vn-waveform">${barsHtml}</div><div class="vn-progress-track"><div class="vn-progress-fill"></div><div class="vn-progress-thumb"></div></div></div><div class="vn-meta-row"><span class="vn-timer">${initialTimer}</span>${isNotDownloaded && sizeStr ? `<span class="vn-dot">•</span><span class="vn-size-indicator">${sizeStr}</span>` : ''}</div></div><button type="button" class="vn-speed-btn" title="Playback speed" data-speed="1">1x</button></div>${audioTag}`;

        this._bindVoiceNoteEvents(bubble);

        // Asynchronously check and use locally stored device media if already downloaded
        if (!isNotDownloaded && !isUploading) {
            deviceMediaStore.getLocalMediaUrl(message.id).then(localUrl => {
                if (localUrl) {
                    const audioEl = bubble.querySelector('.vn-audio-el');
                    if (audioEl && audioEl.src !== localUrl) {
                        audioEl.src = localUrl;
                        audioEl.removeAttribute('data-src');
                    }
                }
            }).catch(() => {});
        }

        const metaDiv = this._createMetaElement(message, false);
        bubble.appendChild(metaDiv);
        return bubble;
    }

    /**
     * Bind interactive voice note events (play/pause, seek, 1x/1.5x/2x speed)
     */
    _bindVoiceNoteEvents(bubble) {
        const playBtn = bubble.querySelector('.vn-play-btn');
        const getPlayIcon = () => bubble.querySelector('.vn-play-icon');
        const audio = bubble.querySelector('.vn-audio-el');
        const timer = bubble.querySelector('.vn-timer');
        const speedBtn = bubble.querySelector('.vn-speed-btn');
        const waveformContainer = bubble.querySelector('.vn-waveform-container');
        const progressFill = bubble.querySelector('.vn-progress-fill');
        const progressThumb = bubble.querySelector('.vn-progress-thumb');
        const bars = bubble.querySelectorAll('.vn-bar');

        if (!playBtn || !audio) return;

        const syncIdleDuration = () => {
            if (!timer || !audio.paused || audio.currentTime > 0) return;
            if (playBtn.querySelector('.spinner-border') || timer.textContent.includes('%')) return;
            if (isFinite(audio.duration) && audio.duration > 0) {
                timer.textContent = this._formatAudioDuration(audio.duration);
            }
        };

        audio.addEventListener('loadedmetadata', syncIdleDuration);
        audio.addEventListener('durationchange', syncIdleDuration);

        playBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (playBtn.querySelector('.spinner-border')) {
                return;
            }
            if (bubble.classList.contains('not-downloaded')) {
                const messageId = bubble.getAttribute('data-message-id');
                if (messageId) {
                    this._handleMediaDownload(messageId, bubble);
                }
                return;
            }

            const playIcon = getPlayIcon();
            if (audio.paused) {
                // Pause all other playing audio on the page
                document.querySelectorAll('.vn-audio-el, .track-audio-el').forEach(other => {
                    if (other !== audio && !other.paused) {
                        other.pause();
                        other.currentTime = 0;
                        const otherBubble = other.closest('.voice-note-bubble, .audio-track-bubble');
                        otherBubble?.querySelector('.vn-play-icon, .track-play-icon')?.classList.replace('bi-pause-fill', 'bi-play-fill');
                        otherBubble?.querySelectorAll('.vn-bar')?.forEach(b => b.classList.remove('is-played'));
                    }
                });

                const currentSpeed = parseFloat(speedBtn?.dataset.speed || '1');
                audio.defaultPlaybackRate = currentSpeed;
                audio.playbackRate = currentSpeed;

                audio.play().then(() => {
                    getPlayIcon()?.classList.replace('bi-play-fill', 'bi-pause-fill');
                }).catch(err => console.warn('[VOICE_NOTE] Play blocked:', err));
            } else {
                audio.pause();
                playIcon?.classList.replace('bi-pause-fill', 'bi-play-fill');
            }
        });

        audio.addEventListener('play', () => {
            const currentSpeed = parseFloat(speedBtn?.dataset.speed || '1');
            audio.defaultPlaybackRate = currentSpeed;
            audio.playbackRate = currentSpeed;
        });

        audio.addEventListener('playing', () => {
            const currentSpeed = parseFloat(speedBtn?.dataset.speed || '1');
            audio.defaultPlaybackRate = currentSpeed;
            audio.playbackRate = currentSpeed;
        });

        audio.addEventListener('timeupdate', () => {
            if (timer) timer.textContent = this._formatAudioDuration(audio.currentTime);
            const duration = audio.duration || 1;
            const pct = audio.currentTime / duration;
            const activeCount = Math.floor(pct * bars.length);
            bars.forEach((bar, idx) => {
                bar.classList.toggle('is-played', idx <= activeCount);
            });
            if (progressFill) progressFill.style.width = `${pct * 100}%`;
            if (progressThumb) progressThumb.style.left = `${pct * 100}%`;
        });

        audio.addEventListener('ended', () => {
            getPlayIcon()?.classList.replace('bi-pause-fill', 'bi-play-fill');
            bars.forEach(b => b.classList.remove('is-played'));
            if (progressFill) progressFill.style.width = '0%';
            if (progressThumb) progressThumb.style.left = '0%';
            if (timer) {
                timer.textContent = (isFinite(audio.duration) && audio.duration > 0)
                    ? this._formatAudioDuration(audio.duration)
                    : '0:00';
            }
            audio.currentTime = 0;
        });

        if (speedBtn) {
            speedBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const cur = parseFloat(speedBtn.dataset.speed || '1');
                const next = cur === 1 ? 1.5 : (cur === 1.5 ? 2 : 1);
                speedBtn.dataset.speed = String(next);
                speedBtn.textContent = `${next}x`;
                audio.defaultPlaybackRate = next;
                audio.playbackRate = next;
            });
        }

        // Pointer Drag & Seeking support for waveform container
        if (waveformContainer) {
            let isDragging = false;

            const updateScrub = (clientX) => {
                const rect = waveformContainer.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
                const pct = ratio * 100;
                if (progressFill) progressFill.style.width = `${pct}%`;
                if (progressThumb) progressThumb.style.left = `${pct}%`;
                const activeCount = Math.floor(ratio * bars.length);
                bars.forEach((bar, idx) => {
                    bar.classList.toggle('is-played', idx <= activeCount);
                });
                if (timer && audio.duration) {
                    timer.textContent = this._formatAudioDuration(ratio * audio.duration);
                }
                return ratio;
            };

            waveformContainer.addEventListener('pointerdown', (e) => {
                if (bubble.classList.contains('not-downloaded')) {
                    const messageId = bubble.getAttribute('data-message-id');
                    if (messageId) this._handleMediaDownload(messageId, bubble);
                    return;
                }
                if (!audio.duration) return;
                e.preventDefault();
                e.stopPropagation();
                isDragging = true;
                waveformContainer.classList.add('is-scrubbing');
                try { waveformContainer.setPointerCapture(e.pointerId); } catch (_) {}
                updateScrub(e.clientX);
            });

            waveformContainer.addEventListener('pointermove', (e) => {
                if (!isDragging) return;
                e.preventDefault();
                updateScrub(e.clientX);
            });

            const onPointerUp = (e) => {
                if (!isDragging) return;
                isDragging = false;
                waveformContainer.classList.remove('is-scrubbing');
                const ratio = updateScrub(e.clientX);
                if (audio.duration && typeof ratio === 'number') {
                    audio.currentTime = ratio * audio.duration;
                }
                try { waveformContainer.releasePointerCapture(e.pointerId); } catch (_) {}
            };

            waveformContainer.addEventListener('pointerup', onPointerUp);
            waveformContainer.addEventListener('pointercancel', onPointerUp);
        }
    }

    /**
     * Render distinct audio track card bubble
     */
    renderAudioTrackBubble(message, metadata = {}) {
        const isUploading = message.status === 'uploading' || message.status === 'sending' || message.isOptimistic;
        const isOwnMsg = this._isOwnMessage(message);
        const isNotDownloaded = !isOwnMsg && !isUploading && !this._isMediaDownloaded(message.id);

        const bubble = document.createElement('div');
        bubble.className = `message-bubble ${isOwnMsg ? 'sent' : 'received'} audio-track-bubble group-${message.groupPosition}`;
        if (isNotDownloaded) {
            bubble.classList.add('not-downloaded');
        }
        bubble.setAttribute('data-message-id', message.id);
        bubble.setAttribute('data-sender-id', message.senderId);
        bubble.setAttribute('data-status', message.status);
        bubble.setAttribute('data-group-position', message.groupPosition);
        this._applyBubbleStyle(bubble);

        const audioUrl = metadata.url || '';
        const rawName = metadata.file_name || message.content || 'Audio Track';
        const fileName = rawName.split('/').pop().split('\\').pop();
        const sizeStr = this._formatFileSize(metadata.size || message.file_size || 0);

        const playBtnClass = isNotDownloaded ? 'audio-track-play-btn audio-download-mode' : 'audio-track-play-btn';
        let playIconHtml = isNotDownloaded
            ? `<i class="bi bi-arrow-down track-play-icon"></i>`
            : `<i class="bi bi-play-fill track-play-icon"></i>`;
        if (isUploading) {
            playIconHtml = `<span class="spinner-border spinner-border-sm" role="status" style="width: 1rem; height: 1rem; border-width: 2px;"></span>`;
        }
        const audioTag = isNotDownloaded
            ? `<audio data-src="${escapeHtml(audioUrl)}" preload="none" class="d-none track-audio-el" style="display:none!important;position:absolute!important;width:0!important;height:0!important;opacity:0!important;pointer-events:none!important;"></audio>`
            : `<audio src="${escapeHtml(audioUrl)}" preload="metadata" class="d-none track-audio-el" style="display:none!important;position:absolute!important;width:0!important;height:0!important;opacity:0!important;pointer-events:none!important;"></audio>`;

        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        if (replyHtml) {
            bubble.classList.add('has-reply-quote');
        }

        const initialAudioTime = isUploading
            ? '0%'
            : (metadata.duration && isFinite(Number(metadata.duration)) ? this._formatAudioDuration(Number(metadata.duration)) : '0:00');
        bubble.innerHTML = `${forwardedHtml}${replyHtml}<div class="audio-track-inner"><button type="button" class="${playBtnClass}" aria-label="${isNotDownloaded ? 'Download track' : 'Play track'}" title="${isNotDownloaded ? 'Download track' : 'Play track'}">${playIconHtml}</button><div class="audio-track-details"><div class="audio-track-title" title="${escapeHtml(fileName)}">${escapeHtml(fileName)}</div><div class="audio-track-scrubber-track"><div class="audio-track-scrubber-fill" style="width: 0%;"></div></div><div class="audio-track-meta-row"><span class="audio-track-time">${initialAudioTime}</span>${sizeStr ? `<span class="audio-track-dot">•</span><span class="audio-track-size">${sizeStr}</span>` : ''}${isNotDownloaded ? `<span class="audio-track-status opacity-75 ms-1">• Tap to download</span>` : ''}</div></div><div class="audio-track-badge"><i class="bi bi-music-note-beamed"></i></div></div>${audioTag}`;

        this._bindAudioTrackEvents(bubble);

        // Asynchronously check and use locally stored device media if already downloaded
        if (!isNotDownloaded && !isUploading) {
            deviceMediaStore.getLocalMediaUrl(message.id).then(localUrl => {
                if (localUrl) {
                    const audioEl = bubble.querySelector('.track-audio-el');
                    if (audioEl && audioEl.src !== localUrl) {
                        audioEl.src = localUrl;
                        audioEl.removeAttribute('data-src');
                    }
                }
            }).catch(() => {});
        }

        const metaDiv = this._createMetaElement(message, false);
        bubble.appendChild(metaDiv);
        return bubble;
    }

    /**
     * Bind audio track playback and scrubber events
     */
    _bindAudioTrackEvents(bubble) {
        const playBtn = bubble.querySelector('.audio-track-play-btn');
        const getPlayIcon = () => bubble.querySelector('.track-play-icon');
        const audio = bubble.querySelector('.track-audio-el');
        const timer = bubble.querySelector('.audio-track-time');
        const fill = bubble.querySelector('.audio-track-scrubber-fill');
        const track = bubble.querySelector('.audio-track-scrubber-track');

        if (!playBtn || !audio) return;

        const syncIdleDuration = () => {
            if (!timer || !audio.paused || audio.currentTime > 0) return;
            if (playBtn.querySelector('.spinner-border') || timer.textContent.includes('%')) return;
            if (isFinite(audio.duration) && audio.duration > 0) {
                timer.textContent = this._formatAudioDuration(audio.duration);
            }
        };

        audio.addEventListener('loadedmetadata', syncIdleDuration);
        audio.addEventListener('durationchange', syncIdleDuration);

        playBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            if (playBtn.querySelector('.spinner-border')) {
                return;
            }
            if (bubble.classList.contains('not-downloaded')) {
                const messageId = bubble.getAttribute('data-message-id');
                if (messageId) {
                    this._handleMediaDownload(messageId, bubble);
                }
                return;
            }

            if (audio.paused) {
                // Pause all other audio
                document.querySelectorAll('.vn-audio-el, .track-audio-el').forEach(other => {
                    if (other !== audio && !other.paused) {
                        other.pause();
                        other.currentTime = 0;
                        const otherBubble = other.closest('.voice-note-bubble, .audio-track-bubble');
                        otherBubble?.querySelector('.vn-play-icon, .track-play-icon')?.classList.replace('bi-pause-fill', 'bi-play-fill');
                    }
                });

                audio.play().then(() => {
                    getPlayIcon()?.classList.replace('bi-play-fill', 'bi-pause-fill');
                }).catch(err => console.warn('[AUDIO_TRACK] Play blocked:', err));
            } else {
                audio.pause();
                getPlayIcon()?.classList.replace('bi-pause-fill', 'bi-play-fill');
            }
        });

        audio.addEventListener('timeupdate', () => {
            if (timer) timer.textContent = this._formatAudioDuration(audio.currentTime);
            const duration = audio.duration || 1;
            const pct = Math.min(100, (audio.currentTime / duration) * 100);
            if (fill) fill.style.width = `${pct}%`;
        });

        audio.addEventListener('ended', () => {
            getPlayIcon()?.classList.replace('bi-pause-fill', 'bi-play-fill');
            if (fill) fill.style.width = '0%';
            if (timer) {
                timer.textContent = (isFinite(audio.duration) && audio.duration > 0)
                    ? this._formatAudioDuration(audio.duration)
                    : '0:00';
            }
            audio.currentTime = 0;
        });

        if (track) {
            track.addEventListener('click', (e) => {
                e.stopPropagation();
                if (bubble.classList.contains('not-downloaded')) {
                    const messageId = bubble.getAttribute('data-message-id');
                    if (messageId) {
                        this._handleMediaDownload(messageId, bubble);
                    }
                    return;
                }
                if (!audio.duration) return;
                const rect = track.getBoundingClientRect();
                const ratio = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
                audio.currentTime = ratio * audio.duration;
            });
        }
    }

    _formatAudioDuration(seconds) {
        if (isNaN(seconds) || seconds < 0) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    _formatFileSize(bytes) {
        if (!bytes || isNaN(bytes) || bytes <= 0) return '';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
    }

    _formatMessageTime(timestamp) {
        if (!timestamp) return '';
        try {
            return formatTime(timestamp);
        } catch (_) {
            return '';
        }
    }

    _getDocumentTypeInfo(ext) {
        const cleanExt = (ext || '').toLowerCase().replace('.', '');
        switch (cleanExt) {
            case 'pdf':
                return { icon: 'bi-file-earmark-pdf-fill', badgeClass: 'doc-badge-pdf', label: 'PDF Document' };
            case 'doc':
            case 'docx':
                return { icon: 'bi-file-earmark-word-fill', badgeClass: 'doc-badge-word', label: 'Word Document' };
            case 'xls':
            case 'xlsx':
            case 'csv':
                return { icon: 'bi-file-earmark-excel-fill', badgeClass: 'doc-badge-excel', label: 'Spreadsheet' };
            case 'ppt':
            case 'pptx':
                return { icon: 'bi-file-earmark-ppt-fill', badgeClass: 'doc-badge-ppt', label: 'Presentation' };
            case 'zip':
            case 'rar':
            case '7z':
            case 'tar':
            case 'gz':
                return { icon: 'bi-file-earmark-zip-fill', badgeClass: 'doc-badge-zip', label: 'Archive' };
            case 'txt':
            case 'py':
            case 'js':
            case 'html':
            case 'css':
            case 'json':
                return { icon: 'bi-file-earmark-code-fill', badgeClass: 'doc-badge-code', label: 'Code File' };
            default:
                return { icon: 'bi-file-earmark-fill', badgeClass: 'doc-badge-generic', label: 'Document' };
        }
    }

    /**
     * Get file icon based on attachment type
     * @param {string} type - Attachment type
     * @returns {string} Icon HTML
     */
    _getFileIcon(type) {
        const icons = {
            'document': '📄',
            'pdf': '📕',
            'file': '📎'
        };
        return icons[type] || icons['file'];
    }

    /**
     * Create link message element (pure DOM creation)
     * @param {Object} message - Link message object
     * @returns {HTMLElement} Link message element
     */
    _createLinkMessage(message) {
        console.log('[RENDERER] _createLinkMessage called with:', message);
        
        // Build wrapper containing avatar, bubble and meta (timestamp + read receipt)
        const wrapperDiv = document.createElement('div');
        wrapperDiv.className = `message-wrapper ${message.isOwn ? 'sent-wrapper' : 'received-wrapper'} group-${message.groupPosition}`;
        wrapperDiv.setAttribute('data-message-id', message.id);
        wrapperDiv.setAttribute('data-sender-id', message.senderId);
        wrapperDiv.setAttribute('data-status', message.status);
        wrapperDiv.setAttribute('data-group-position', message.groupPosition);

        // Add avatar for received messages (only on last/single in group)
        if (!message.isOwn) {
            const shouldShowAvatar = message.groupPosition === 'single' || message.groupPosition === 'last';
            const avatarContainer = document.createElement('div');
            avatarContainer.className = `message-avatar-container ${shouldShowAvatar ? '' : 'hidden'}`;
            
            if (shouldShowAvatar) {
                const avatarUrl = this._getSenderAvatar(message.senderId);
                const avatarImg = document.createElement('img');
                avatarImg.className = 'message-avatar';
                avatarImg.src = avatarUrl;
                avatarImg.alt = 'Avatar';
                avatarContainer.appendChild(avatarImg);
            }
            
            wrapperDiv.appendChild(avatarContainer);
        }

        // Content wrapper for link and meta
        const contentWrapper = document.createElement('div');
        contentWrapper.className = 'message-content-wrapper';

        // Create bubble
        const messageDiv = document.createElement('div');
        messageDiv.className = `message-bubble ${message.isOwn ? 'sent' : 'received'} link-message group-${message.groupPosition}`;
        messageDiv.setAttribute('data-message-id', message.id);
        messageDiv.setAttribute('data-sender-id', message.senderId);
        messageDiv.setAttribute('data-status', message.status);
        messageDiv.setAttribute('data-group-position', message.groupPosition);
        this._applyBubbleStyle(messageDiv);
        
        const metadata = message.metadata || {};
        const linkUrl = metadata.link_url;
        const linkTitle = metadata.link_title;
        const linkDescription = metadata.link_description;
        const linkImage = metadata.link_image;
        const linkType = metadata.link_type || 'link';
        
        let linkContent = '';
        
        // Check if this is an embed type (Facebook, YouTube, etc.)
        if (linkType === 'youtube') {
            linkContent = this._createYouTubeEmbed(linkUrl);
        } else if (linkType === 'facebook') {
            linkContent = this._createFacebookEmbed(linkUrl);
        } else {
            // Rich link preview card
            linkContent = `
                <a href="${escapeHtml(linkUrl)}" target="_blank" class="link-preview-card" rel="noopener noreferrer">
                    ${linkImage ? `<img src="${escapeHtml(linkImage)}" alt="Link preview" class="link-preview-image" loading="lazy">` : ''}
                    <div class="link-preview-content">
                        <div class="link-preview-title">${escapeHtml(linkTitle || linkUrl)}</div>
                        ${linkDescription ? `<div class="link-preview-description">${escapeHtml(linkDescription)}</div>` : ''}
                        <div class="link-preview-domain">${this._extractDomain(linkUrl)}</div>
                    </div>
                </a>
            `;
        }
        
        const forwardedHtml = this._buildForwardedBadgeHtml(message);
        const replyHtml = this._buildReplyQuoteHtml(message);

        // Add text content if present
        const textContent = message.content 
            ? `<p class="message-content">${escapeHtml(message.content)}${this._getSpacerHtml(message)}</p>` 
            : '';
        
        messageDiv.innerHTML = `${forwardedHtml}${replyHtml}${textContent}${linkContent}`;

        // Inside-bubble meta
        const metaDiv = this._createMetaElement(message, false);
        messageDiv.appendChild(metaDiv);

        contentWrapper.appendChild(messageDiv);
        wrapperDiv.appendChild(contentWrapper);
        return wrapperDiv;
    }

    /**
     * Create YouTube embed
     * @param {string} url - YouTube URL
     * @returns {string} Embed HTML
     */
    _createYouTubeEmbed(url) {
        const videoId = this._extractYouTubeId(url);
        if (!videoId) {
            // Fallback to link preview if we can't extract video ID
            return `<a href="${escapeHtml(url)}" target="_blank" class="link-preview-card">${escapeHtml(url)}</a>`;
        }
        
        return `
            <div class="video-embed">
                <iframe
                    src="https://www.youtube.com/embed/${videoId}"
                    frameborder="0"
                    allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture"
                    allowfullscreen
                    class="youtube-embed">
                </iframe>
            </div>
        `;
    }

    /**
     * Create Facebook embed
     * @param {string} url - Facebook URL
     * @returns {string} Embed HTML
     */
    _createFacebookEmbed(url) {
        // Facebook requires their embed SDK, so we'll use a link preview for now
        return `
            <a href="${escapeHtml(url)}" target="_blank" class="link-preview-card facebook-link" rel="noopener noreferrer">
                <div class="facebook-embed-placeholder">
                    <span class="facebook-icon">📘</span>
                    <span class="facebook-text">View on Facebook</span>
                </div>
            </a>
        `;
    }

    /**
     * Extract YouTube video ID from URL
     * @param {string} url - YouTube URL
     * @returns {string|null} Video ID
     */
    _extractYouTubeId(url) {
        const patterns = [
            /(?:youtube\.com\/watch\?v=|youtu\.be\/|youtube\.com\/embed\/)([^&\n?#]+)/,
            /youtube\.com\/shorts\/([^&\n?#]+)/
        ];
        
        for (const pattern of patterns) {
            const match = url.match(pattern);
            if (match) return match[1];
        }
        
        return null;
    }

    /**
     * Extract domain from URL
     * @param {string} url - URL
     * @returns {string} Domain
     */
    _extractDomain(url) {
        try {
            const urlObj = new URL(url);
            return urlObj.hostname;
        } catch (e) {
            return url;
        }
    }

    /**
     * Scroll to bottom of container (pure DOM manipulation)
     */
    _scrollToBottom(force = false) {
        if (!this.container) {
            this.container = document.getElementById('messagesContainer');
        }
        if (!this.container) return;
        const threshold = 180;
        const distanceFromBottom = this.container.scrollHeight - this.container.scrollTop - this.container.clientHeight;
        const isNearBottom = distanceFromBottom < threshold;

        if (force || isNearBottom || (this.isGroundedToBottom && !this.isUserScrolledUp)) {
            this.isGroundedToBottom = true;
            this.isUserScrolledUp = false;
            const applyScroll = () => {
                if (this.container && (this.isGroundedToBottom || force)) {
                    this.container.scrollTop = this.container.scrollHeight;
                }
                this.updateScrollToBottomButton();
            };
            applyScroll();
            if (this._scrollBottomRaf) cancelAnimationFrame(this._scrollBottomRaf);
            if (this._scrollBottomTimer) clearTimeout(this._scrollBottomTimer);
            this._scrollBottomRaf = requestAnimationFrame(() => {
                this._scrollBottomRaf = null;
                applyScroll();
                this._scrollBottomTimer = setTimeout(() => {
                    this._scrollBottomTimer = null;
                    applyScroll();
                }, 60);
            });
        } else {
            this.updateScrollToBottomButton();
        }
    }

    /**
     * Update floating scroll-to-bottom button visibility & state
     */
    updateScrollToBottomButton() {
        if (!this.container) {
            this.container = document.getElementById('messagesContainer');
        }
        if (!this.container) return;

        const btn = document.getElementById('chatScrollToBottomBtn');
        const badge = document.getElementById('chatScrollToBottomBadge');
        if (!btn) return;

        const threshold = 180;
        const distanceFromBottom = this.container.scrollHeight - this.container.scrollTop - this.container.clientHeight;
        const isNearBottom = distanceFromBottom < threshold;

        if (isNearBottom || (this.isGroundedToBottom && !this.isUserScrolledUp)) {
            btn.classList.add('d-none', 'is-hidden');
            btn.style.setProperty('display', 'none', 'important');
            this.unreadScrolledCount = 0;
            if (badge) {
                badge.textContent = '0';
                badge.classList.add('d-none');
            }
        } else {
            btn.classList.remove('d-none', 'is-hidden');
            btn.style.removeProperty('display');
        }
    }

    /**
     * Handle new incoming message arrival when scrolled up
     */
    handleIncomingMessageScroll(isFromMe = false) {
        if (!this.container) {
            this.container = document.getElementById('messagesContainer');
        }
        if (!this.container) return;

        if (isFromMe || this.isGroundedToBottom || !this.isUserScrolledUp) {
            this._scrollToBottom(true);
            this.updateScrollToBottomButton();
        } else {
            // User is willingly scrolled up reading earlier messages!
            this.unreadScrolledCount = (this.unreadScrolledCount || 0) + 1;
            const btn = document.getElementById('chatScrollToBottomBtn');
            const badge = document.getElementById('chatScrollToBottomBadge');
            if (btn) {
                btn.classList.remove('d-none', 'is-hidden');
                btn.style.removeProperty('display');
            }
            if (badge) {
                badge.textContent = String(this.unreadScrolledCount);
                badge.classList.remove('d-none');
            }
        }
    }

    /**
     * Update current user ID (if store changes)
     * @param {number} userId - User ID
     */
    updateCurrentUserId(userId) {
        this.currentUserId = userId;
        this._log('CURRENT_USER_ID_UPDATED', { userId });
    }

    /**
     * Get renderer status (read-only)
     * @returns {Object} Status
     */
    getStatus() {
        return {
            initialized: !!this.container,
            currentUserId: this.currentUserId,
            isRendering: this.isRendering,
            containerExists: !!this.container,
            isPureRendering: true // Explicitly mark as pure rendering
        };
    }

    /**
     * Destroy renderer
     */
    destroy() {
        this._log('RENDERER_DESTROY');
        
        this.container = null;
        this.currentUserId = null;
        this.isRendering = false;
    }

    /**
     * Get sender avatar URL
     * @param {number} senderId - Sender user ID
     * @returns {string} Avatar URL
     */
    _getSenderAvatar(senderId) {
        if (document.querySelector('.chat-container')?.dataset.conversationType === 'group') {
            const sender = this.senderProfiles?.get(Number(senderId)) || {};
            return sender.profile_pic_url || sender.profile_pic || '/static/images/default_pic1.jpg';
        }
        // Try to get avatar from dataset (set by template)
        const receiverAvatar = document.body.dataset.receiverAvatar;
        if (receiverAvatar && receiverAvatar !== 'undefined' && receiverAvatar !== 'null') {
            return receiverAvatar;
        }
        // Try avatar image from header trigger
        const headerImg = document.querySelector('#headerProfileTrigger img.chat-avatar, .chat-avatar img, img.chat-avatar');
        if (headerImg && headerImg.src) {
            return headerImg.src;
        }
        // Fallback to default avatar
        return '/static/images/default_pic1.jpg';
    }

    /**
     * Log debug information
     * @param {string} action - Action type
     * @param {*} data - Action data
     */
    _log(action, data) {
        if (this.debugMode) {
            console.log(`[RENDERER] ${action}:`, data);
        }
    }
}
