/**
 * MediaComposer - Modern Fullscreen Media Composition & Playground Interface
 * Inspired by WhatsApp, Telegram, Full-screen Reel Player, and Instagram
 * Features:
 * - Floating round semi-transparent circular controls
 * - Full bleed edge-to-edge media stage (0 padding, full mobile viewport)
 * - Animated "Closing…" exit state matching Fullscreen Reel Player
 * - Floating Crop Aspect Ratio Dropdown (Free, 1:1, 4:5, 16:9)
 * - Full-width interactive video timeline trimmer with highlighted cropping & mask fading
 * - Video playback strictly bounded to trimmed segment
 * - Floating video size & duration badge below close button
 * - Semi-transparent floating caption input & send button
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { PreviewCanvas } from './PreviewCanvas.js?v=20261106-group1';
import { ThumbnailStrip } from './ThumbnailStrip.js?v=20261106-group1';
import { CaptionInput } from './CaptionInput.js?v=20261106-group1';
import { UploadQueue } from './UploadQueue.js?v=20261106-group1';
import { CroppingEngine } from './CroppingEngine.js?v=20261106-group1';

export class MediaComposer {
    constructor() {
        this.overlay = null;
        this.isOpen = false;
        this.isClosing = false;

        // State
        this.mediaItems = [];
        this.activeMediaIndex = 0;
        this.globalCaption = '';
        this.conversationId = null;

        // Sub-components
        this.previewCanvas = null;
        this.thumbnailStrip = null;
        this.captionInput = null;
        this.uploadQueue = null;
        this.croppingEngine = null;

        // DOM elements - layout & actions
        this.header = null;
        this.main = null;
        this.footer = null;
        this.sendBtn = null;
        this.closeBtn = null;
        this.addMoreBtn = null;

        // Top left size badge & center item counter
        this.sizeBadge = null;
        this.counterWrap = null;
        this.counterBadge = null;

        // Header floating action buttons
        this.rotateBtn = null;
        this.cropBtn = null;
        this.cropDropdown = null;
        this.cropOptions = [];
        this.resetBtn = null;
        this.muteBtn = null;
        this.muteIcon = null;
        this.deleteBtn = null;

        // Video trimmer controls
        this.trimmerBar = null;
        this.trimmerStartLabel = null;
        this.trimmerDurationLabel = null;
        this.trimmerEndLabel = null;
        this.trimmerTrack = null;
        this.trimmerFilmstrip = null;
        this.trimmerMaskLeft = null;
        this.trimmerMaskRight = null;
        this.trimmerSelection = null;
        this.trimmerHandleLeft = null;
        this.trimmerHandleRight = null;
        this.trimmerPlayhead = null;

        // Caption controls
        this.emojiBtn = null;

        this._videoPlaybackRaf = null;
        this.initialized = false;
    }

    /**
     * Initialize the media composer
     */
    init() {
        if (this.initialized) return;

        this._initializeElements();
        this._setupEventListeners();
        this._initializeComponents();

        this.initialized = true;
        console.log('[MEDIA_COMPOSER] Media composer initialized');
    }

    /**
     * Initialize DOM elements
     */
    _initializeElements() {
        this.overlay = document.getElementById('mediaComposerOverlay');
        this.header = document.getElementById('mediaComposerHeader');
        this.main = document.getElementById('mediaComposerMain');
        this.footer = document.getElementById('mediaComposerFooter');
        this.sendBtn = document.getElementById('mediaComposerSend');
        this.closeBtn = document.getElementById('mediaComposerClose');
        this.addMoreBtn = document.getElementById('mediaComposerAddMore');

        // Top left size badge & center counter
        this.sizeBadge = document.getElementById('mediaComposerSizeBadge');
        this.counterWrap = document.getElementById('mediaComposerCounterWrap');
        this.counterBadge = document.getElementById('mediaComposerCounter');

        // Floating action buttons
        this.rotateBtn = document.getElementById('mediaRotateBtn');
        this.cropBtn = document.getElementById('mediaCropBtn');
        this.cropDropdown = document.getElementById('mediaCropDropdown');
        this.cropOptions = this.cropDropdown ? this.cropDropdown.querySelectorAll('.crop-option-btn') : [];
        this.resetBtn = document.getElementById('mediaResetBtn');
        this.muteBtn = document.getElementById('mediaMuteBtn');
        this.muteIcon = document.getElementById('mediaMuteIcon');
        this.deleteBtn = document.getElementById('mediaDeleteCurrentBtn');

        // Video trimmer
        this.trimmerBar = document.getElementById('mediaVideoTrimmerBar');
        this.trimmerStartLabel = document.getElementById('trimmerStartLabel');
        this.trimmerDurationLabel = document.getElementById('trimmerDurationLabel');
        this.trimmerEndLabel = document.getElementById('trimmerEndLabel');
        this.trimmerTrack = document.getElementById('trimmerTrack');
        this.trimmerFilmstrip = document.getElementById('trimmerFilmstrip');
        this.trimmerMaskLeft = document.getElementById('trimmerMaskLeft');
        this.trimmerMaskRight = document.getElementById('trimmerMaskRight');
        this.trimmerSelection = document.getElementById('trimmerSelection');
        this.trimmerHandleLeft = document.getElementById('trimmerHandleLeft');
        this.trimmerHandleRight = document.getElementById('trimmerHandleRight');
        this.trimmerPlayhead = document.getElementById('trimmerPlayhead');

        // Emoji
        this.emojiBtn = document.getElementById('mediaCaptionEmojiBtn');
    }

    /**
     * Setup event listeners
     */
    _setupEventListeners() {
        if (this.closeBtn) {
            this.closeBtn.addEventListener('click', () => this.close());
        }

        if (this.sendBtn) {
            this.sendBtn.addEventListener('click', () => this.send());
        }

        if (this.addMoreBtn) {
            this.addMoreBtn.addEventListener('click', () => this.triggerFileInput());
        }

        if (this.rotateBtn) {
            this.rotateBtn.addEventListener('click', () => this.rotateCurrentMedia());
        }

        if (this.cropBtn) {
            this.cropBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                const item = this.mediaItems[this.activeMediaIndex];
                if (item && item.type === 'image') {
                    if (this.croppingEngine?.isActive) {
                        this.croppingEngine.closeCrop();
                    } else {
                        this.croppingEngine.openCrop(item);
                    }
                } else {
                    this.toggleCropDropdown();
                }
            });
        }

        this.cropOptions?.forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const aspect = btn.dataset.aspect;
                const item = this.mediaItems[this.activeMediaIndex];
                if (item) item.cropAspect = aspect;
                this._updateCropDropdownUI(aspect);
                this.closeCropDropdown();
                if (item && item.type === 'image' && this.croppingEngine) {
                    this.croppingEngine.openCrop(item);
                    this.croppingEngine.setAspectRatio(aspect);
                }
            });
        });

        // Click outside to dismiss crop dropdown
        document.addEventListener('click', (e) => {
            if (this.isOpen && this.cropDropdown && !this.cropDropdown.classList.contains('d-none')) {
                if (!this.cropDropdown.contains(e.target) && !this.cropBtn?.contains(e.target)) {
                    this.closeCropDropdown();
                }
            }
        });

        if (this.resetBtn) {
            this.resetBtn.addEventListener('click', () => {
                this.croppingEngine?.resetAllEdits();
            });
        }

        if (this.deleteBtn) {
            this.deleteBtn.addEventListener('click', () => this.removeMediaItem(this.activeMediaIndex));
        }

        if (this.muteBtn) {
            this.muteBtn.addEventListener('click', () => this.toggleVideoMute());
        }

        // Synchronize with navbar global mute button
        const globalMuteBtn = document.getElementById('global-mute-button');
        if (globalMuteBtn) {
            globalMuteBtn.addEventListener('click', () => {
                setTimeout(() => {
                    const isMuted = this._getGlobalMuteState();
                    const item = this.mediaItems[this.activeMediaIndex];
                    if (item && item.type === 'video') {
                        item.isMuted = isMuted;
                        this._updateMuteUI(item);
                        const videoEl = this.previewCanvas.container?.querySelector('video');
                        if (videoEl) videoEl.muted = isMuted;
                    }
                }, 50);
            });
        }

        if (this.emojiBtn) {
            this.emojiBtn.addEventListener('click', () => {
                eventBus.emit(EVENTS.EMOJI_PICKER_TOGGLE);
            });
        }

        this._setupTrimmerDrag();

        // Keyboard navigation
        document.addEventListener('keydown', (e) => this._handleKeydown(e));
    }

    /**
     * Initialize sub-components
     */
    _initializeComponents() {
        this.previewCanvas = new PreviewCanvas(this);
        this.thumbnailStrip = new ThumbnailStrip(this);
        this.captionInput = new CaptionInput(this);
        this.uploadQueue = new UploadQueue(this);
        this.croppingEngine = new CroppingEngine(this);

        this.previewCanvas.init();
        this.thumbnailStrip.init();
        this.captionInput.init();
        this.uploadQueue.init();
        this.croppingEngine.init();
    }

    /**
     * Open the media composer
     */
    open(files, conversationId) {
        if (!files || files.length === 0) return;

        this.conversationId = conversationId;
        this.mediaItems = this._processFiles(files);
        this.activeMediaIndex = 0;
        this.globalCaption = '';
        this.isClosing = false;

        this._restoreDraft();

        if (this.overlay) {
            this.overlay.classList.add('show');
        }

        this.isOpen = true;

        this._setSendButtonLoading(false);

        // Update header & views
        this.setActiveMedia(0);
        this.thumbnailStrip.render(this.mediaItems);
        this.captionInput.setGlobalCaption(this.globalCaption);

        this._saveDraft();
        console.log('[MEDIA_COMPOSER] Composer opened with', this.mediaItems.length, 'items');
    }

    /**
     * Close composer with animated "Closing…" feedback matching Fullscreen Reel Player
     */
    /**
     * Close composer with animated "Closing…" feedback matching Fullscreen Reel Player
     */
    close(isSent = false) {
        if (this.isClosing) return;
        this.isClosing = true;

        if (this.closeBtn) {
            this.closeBtn.disabled = true;
            this.closeBtn.classList.add('is-closing');
            this.closeBtn.querySelector('.close-icon')?.classList.add('d-none');
            this.closeBtn.querySelector('.close-spinner')?.classList.remove('d-none');
            this.closeBtn.querySelector('.close-progress-label')?.classList.remove('d-none');
        }

        // Allow spinner and "Closing…" to paint, then finish smoothly
        requestAnimationFrame(() => {
            setTimeout(() => {
                this._finishClose(isSent);
            }, 100);
        });
    }

    _finishClose(isSent = false) {
        if (this._videoPlaybackRaf) {
            cancelAnimationFrame(this._videoPlaybackRaf);
            this._videoPlaybackRaf = null;
        }
        if (this.croppingEngine) {
            this.croppingEngine.closeCrop();
        }
        const videoEl = this.previewCanvas?.container?.querySelector('video');
        if (videoEl) {
            videoEl.pause();
        }

        if (isSent) {
            this._clearDraft();
        } else {
            this._saveDraft();
        }

        if (!isSent) {
            this.mediaItems.forEach(item => {
                if (item.previewUrl) {
                    URL.revokeObjectURL(item.previewUrl);
                }
            });
        }

        if (this.overlay) {
            this.overlay.classList.remove('show');
        }

        this.closeCropDropdown();

        if (this.closeBtn) {
            this.closeBtn.disabled = false;
            this.closeBtn.classList.remove('is-closing');
            this.closeBtn.querySelector('.close-icon')?.classList.remove('d-none');
            this.closeBtn.querySelector('.close-spinner')?.classList.add('d-none');
            this.closeBtn.querySelector('.close-progress-label')?.classList.add('d-none');
        }

        this._setSendButtonLoading(false);

        // Reset and clean session completely for a fresh start next time
        this.isClosing = false;
        this.isOpen = false;
        this.mediaItems = [];
        this.activeMediaIndex = 0;
        this.globalCaption = '';

        if (this.captionInput) {
            this.captionInput.clear();
        }
        if (this.thumbnailStrip) {
            this.thumbnailStrip.clear();
        }
        if (this.previewCanvas) {
            this.previewCanvas.clear();
        }

        console.log('[MEDIA_COMPOSER] Composer closed and session destroyed');
    }

    /**
     * Process files into media items
     */
    _processFiles(files) {
        const items = [];
        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            const fileType = this._getFileType(file);
            const previewUrl = this._createPreviewUrl(file);

            const item = {
                file: file,
                type: fileType,
                previewUrl: previewUrl,
                thumbnail: '',
                caption: '',
                size: file.size,
                name: file.name,
                rotation: 0,
                isMuted: fileType === 'video' ? this._getGlobalMuteState() : false,
                cropAspect: 'free',
                trimStart: 0,
                trimEnd: 0,
                duration: 0
            };

            if (fileType === 'image') {
                const img = new Image();
                img.onload = () => {
                    item.width = img.naturalWidth;
                    item.height = img.naturalHeight;
                };
                img.src = previewUrl;
            } else if (fileType === 'video') {
                this._generateVideoThumbnail(file, previewUrl).then(result => {
                    if (result && result.thumbnail) {
                        item.thumbnail = result.thumbnail;
                        if (result.width && result.height) {
                            item.width = result.width;
                            item.height = result.height;
                        }
                        if (result.duration) {
                            item.duration = result.duration;
                        }
                        if (this.thumbnailStrip) {
                            this.thumbnailStrip.render(this.mediaItems);
                        }
                    }
                }).catch(() => {});
            }

            items.push(item);
        }
        return items;
    }

    _generateVideoThumbnail(file, previewUrl) {
        return new Promise((resolve) => {
            const video = document.createElement('video');
            // 'auto' + a real (muted) play is required: WebView decoders
            // often never produce a seekable frame from metadata alone,
            // which is what left Capacitor uploads with a grey/dark box.
            video.preload = 'auto';
            video.muted = true;
            video.setAttribute('muted', '');
            video.playsInline = true;
            video.setAttribute('playsinline', '');
            const src = previewUrl || (file ? URL.createObjectURL(file) : '');
            if (!src) return resolve({ thumbnail: '', width: 0, height: 0, duration: 0 });

            let finished = false;
            const done = (dataUrl) => {
                if (finished) return;
                finished = true;
                try { video.pause(); } catch (_) {}
                const width = video.videoWidth || 0;
                const height = video.videoHeight || 0;
                const duration = video.duration || 0;
                video.removeAttribute('src');
                try { video.load(); } catch (_) {}
                video.remove();
                resolve({
                    thumbnail: dataUrl || '',
                    width: width,
                    height: height,
                    duration: duration
                });
            };

            const capture = () => {
                try {
                    const w = Math.min(video.videoWidth || 360, 480);
                    if (!w || !video.videoHeight) return false;
                    const aspect = video.videoHeight / video.videoWidth;
                    const h = Math.max(1, Math.round(w * aspect));
                    const canvas = document.createElement('canvas');
                    canvas.width = w;
                    canvas.height = h;
                    canvas.getContext('2d').drawImage(video, 0, 0, w, h);
                    const dataUrl = canvas.toDataURL('image/jpeg', 0.75);
                    // Reject blank captures (all-black first frames).
                    if (dataUrl && dataUrl.length > 2000) {
                        done(dataUrl);
                        return true;
                    }
                } catch (_) {}
                return false;
            };

            const seekTo = (t) => {
                try {
                    video.currentTime = Math.min(Math.max(0.1, t), Math.max(0.1, (video.duration || 1) - 0.1));
                } catch (_) {
                    done('');
                }
            };

            video.onloadeddata = () => {
                // Nudge a real play so the decoder produces frames, then
                // capture from a seeked position. Falls back to plain seek
                // when autoplay is denied.
                let played = false;
                try {
                    const p = video.play();
                    if (p && typeof p.then === 'function') {
                        p.then(() => { played = true; seekTo(0.5); }).catch(() => seekTo(0.5));
                    } else {
                        seekTo(0.5);
                    }
                } catch (_) {
                    seekTo(0.5);
                }
                // If play stalls without seek events, capture the playing frame.
                setTimeout(() => { if (!finished && played) capture(); }, 1200);
            };

            video.onseeked = () => {
                if (!capture()) {
                    // Blank frame: try the other end of the clip once.
                    seekTo(Math.max(0.1, (video.duration || 1) - 0.2));
                    setTimeout(() => { if (!finished && !capture()) done(''); }, 1500);
                }
            };

            video.onerror = () => done('');
            video.src = src;
            // Large phone clips need more than the old 3s window.
            setTimeout(() => done(''), 8000);
        });
    }

    _getFileType(file) {
        if (file.type.startsWith('image/')) return 'image';
        if (file.type.startsWith('video/')) return 'video';
        if (file.type.startsWith('audio/')) return 'audio';
        return 'document';
    }

    _createPreviewUrl(file) {
        if (file.type.startsWith('image/') || file.type.startsWith('video/')) {
            return URL.createObjectURL(file);
        }
        return null;
    }

    /**
     * Set active media item
     */
    setActiveMedia(index) {
        if (index < 0 || index >= this.mediaItems.length) return;

        if (this.croppingEngine?.isActive) {
            this.croppingEngine.closeCrop();
        }

        this.activeMediaIndex = index;
        const currentItem = this.mediaItems[index];

        if (currentItem.type === 'video') {
            if (currentItem.isMuted === undefined) {
                currentItem.isMuted = this._getGlobalMuteState();
            }
            this._updateMuteUI(currentItem);
        }

        this.previewCanvas.render(currentItem);
        this.thumbnailStrip.setActive(index);
        this.captionInput.setMediaCaption(currentItem.caption || '');

        this.updateHeaderContext();
        this._updateToolsForMedia(currentItem);
        this._updateSizeBadge(currentItem);
        this.updateEditsState();

        // Bind video strictly bounded playback if active item is video
        if (currentItem.type === 'video') {
            const videoEl = this.previewCanvas.container?.querySelector('video');
            if (videoEl) {
                this._bindVideoPlayback(videoEl, currentItem);
            }
        }
    }

    /**
     * Update center item counter (e.g. 1 / 3)
     */
    updateHeaderContext() {
        const total = this.mediaItems.length;
        const currentIdx = this.activeMediaIndex + 1;

        if (this.counterBadge && this.counterWrap) {
            if (total > 1) {
                this.counterBadge.textContent = `${currentIdx} / ${total}`;
                this.counterWrap.classList.remove('d-none');
            } else {
                this.counterWrap.classList.add('d-none');
            }
        }
    }

    /**
     * Contextual visibility for tools depending on media type
     */
    _updateToolsForMedia(item) {
        if (!item) return;

        this.closeCropDropdown();

        if (item.type === 'image') {
            this.rotateBtn?.classList.remove('d-none');
            this.cropBtn?.classList.remove('d-none');
            this.muteBtn?.classList.add('d-none');
            this.trimmerBar?.classList.add('d-none');
            this._updateCropDropdownUI(item.cropAspect || 'free');
        } else if (item.type === 'video') {
            this.rotateBtn?.classList.add('d-none');
            this.cropBtn?.classList.add('d-none');
            this.muteBtn?.classList.remove('d-none');
            this.trimmerBar?.classList.remove('d-none');
            this._updateMuteUI(item);
            this._updateTrimmerUI(item);
        } else {
            this.rotateBtn?.classList.add('d-none');
            this.cropBtn?.classList.add('d-none');
            this.muteBtn?.classList.add('d-none');
            this.trimmerBar?.classList.add('d-none');
        }

        this.updateEditsState();
    }

    /**
     * Rotate current image clockwise by 90 deg
     */
    rotateCurrentMedia() {
        const item = this.mediaItems[this.activeMediaIndex];
        if (!item || item.type !== 'image') return;
        item.rotation = ((item.rotation || 0) + 90) % 360;
        this.previewCanvas.render(item);
        this.updateEditsState();
    }

    /**
     * Check if active item has any pending edits and update Reset button
     */
    updateEditsState() {
        const item = this.mediaItems[this.activeMediaIndex];
        if (!this.resetBtn) return;
        if (!item) {
            this.resetBtn.classList.add('d-none');
            this.resetBtn.classList.remove('has-edits');
            return;
        }

        const isRotated = !!(item.rotation && (item.rotation % 360) !== 0);
        const isCropped = !!(item.isEdited || item.cropRect);
        const isTrimmed = !!(item.type === 'video' && ((item.trimStart && item.trimStart > 0.05) || (item.duration && item.trimEnd && (item.duration - item.trimEnd) > 0.2)));

        if (isRotated || isCropped || isTrimmed) {
            this.resetBtn.classList.remove('d-none');
            this.resetBtn.classList.add('has-edits');
        } else {
            this.resetBtn.classList.add('d-none');
            this.resetBtn.classList.remove('has-edits');
        }
    }

    /**
     * Toggle Crop Aspect Ratio Dropdown
     */
    toggleCropDropdown() {
        if (!this.cropDropdown) return;
        const isHidden = this.cropDropdown.classList.contains('d-none');
        if (isHidden) {
            const item = this.mediaItems[this.activeMediaIndex];
            this._updateCropDropdownUI(item?.cropAspect || 'free');
            this.cropDropdown.classList.remove('d-none');
            this.cropBtn?.classList.add('active');
        } else {
            this.closeCropDropdown();
        }
    }

    closeCropDropdown() {
        if (this.cropDropdown) this.cropDropdown.classList.add('d-none');
        if (this.cropBtn && (!this.croppingEngine || !this.croppingEngine.isActive)) {
            this.cropBtn.classList.remove('active');
        }
    }

    _updateCropDropdownUI(selectedAspect) {
        if (!this.cropOptions) return;
        this.cropOptions.forEach(btn => {
            const isMatch = btn.dataset.aspect === selectedAspect;
            btn.classList.toggle('active', isMatch);
            const check = btn.querySelector('.check-icon');
            if (check) check.classList.toggle('d-none', !isMatch);
        });
    }

    /**
     * Global mute state synchronization
     */
    _getGlobalMuteState() {
        if (window.videoManager && typeof window.videoManager.isGlobalMuted === 'boolean') {
            return window.videoManager.isGlobalMuted;
        }
        if (window.vastoGlobalAudio && typeof window.vastoGlobalAudio.getAudioPreference === 'function') {
            return window.vastoGlobalAudio.getAudioPreference() === 'muted';
        }
        const username = window.vastoUsername || '';
        const userKey = username ? `vasto_audio_preference_${username}` : 'vasto_audio_preference';
        const pref = localStorage.getItem(userKey) || localStorage.getItem('vasto_audio_preference');
        if (pref) return pref === 'muted';
        return false;
    }

    _setGlobalMuteState(muted) {
        if (window.videoManager && typeof window.videoManager.setGlobalMute === 'function') {
            window.videoManager.setGlobalMute(muted);
        }
        if (window.vastoGlobalAudio && typeof window.vastoGlobalAudio.setAudioPreference === 'function') {
            window.vastoGlobalAudio.setAudioPreference(muted ? 'muted' : 'unmuted');
        }
        const username = window.vastoUsername || '';
        const userKey = username ? `vasto_audio_preference_${username}` : 'vasto_audio_preference';
        localStorage.setItem(userKey, muted ? 'muted' : 'unmuted');
        localStorage.setItem('vasto_audio_preference', muted ? 'muted' : 'unmuted');

        // Also update navbar mute button icon if present
        const muteButton = document.getElementById('global-mute-button');
        if (muteButton) {
            const icon = muteButton.querySelector('i');
            if (icon) {
                icon.className = muted ? 'bi bi-volume-mute' : 'bi-volume-up';
            }
            muteButton.title = muted ? 'Unmute all videos' : 'Mute all videos';
        }
    }

    /**
     * Toggle Video Mute
     */
    toggleVideoMute() {
        const item = this.mediaItems[this.activeMediaIndex];
        if (!item || item.type !== 'video') return;
        item.isMuted = !item.isMuted;
        this._setGlobalMuteState(item.isMuted);
        this._updateMuteUI(item);
        const videoEl = this.previewCanvas.container?.querySelector('video');
        if (videoEl) {
            videoEl.muted = !!item.isMuted;
            if (!item.isMuted && videoEl.paused) {
                videoEl.play().catch(() => {});
            }
        }
    }

    _updateMuteUI(item) {
        if (!this.muteBtn || !this.muteIcon) return;
        if (item?.isMuted) {
            this.muteBtn.classList.add('active');
            this.muteIcon.className = 'bi bi-volume-mute-fill';
            this.muteBtn.title = 'Unmute Video';
        } else {
            this.muteBtn.classList.remove('active');
            this.muteIcon.className = 'bi bi-volume-up-fill';
            this.muteBtn.title = 'Mute Video';
        }
    }

    /**
     * Called by PreviewCanvas when video metadata is loaded
     */
    onVideoLoaded(video, item) {
        if (!video || !item) return;
        const duration = video.duration || item.duration || 0;
        item.duration = duration;
        if (!item.trimEnd || item.trimEnd > duration) {
            item.trimEnd = duration;
        }
        item.trimStart = item.trimStart || 0;

        this._bindVideoPlayback(video, item);
        this._updateTrimmerUI(item);
        this._updateSizeBadge(item);
    }

    /**
     * Strictly bound video playback to [item.trimStart, item.trimEnd] with smooth 60fps looping
     */
    _bindVideoPlayback(video, item) {
        if (!video || !item) return;

        // Cancel previous loop timer or animation frame if any
        if (this._videoPlaybackRaf) {
            cancelAnimationFrame(this._videoPlaybackRaf);
            this._videoPlaybackRaf = null;
        }

        // Set initial playback position to start of trimmed section
        if (item.trimStart && item.trimStart > 0) {
            video.currentTime = item.trimStart;
        }

        const loopCheck = () => {
            if (this.previewCanvas.container?.querySelector('video') !== video) {
                return; // Stale video
            }

            const start = item.trimStart || 0;
            const end = item.trimEnd || item.duration || video.duration;

            if (end > start && video.currentTime >= end - 0.04) {
                video.currentTime = start;
                if (!video.paused) {
                    video.play().catch(() => {});
                }
            } else if (video.currentTime < start) {
                video.currentTime = start;
            }

            // Sync playhead indicator on filmstrip
            if (item.duration > 0 && this.trimmerPlayhead) {
                const pct = (video.currentTime / item.duration) * 100;
                this.trimmerPlayhead.style.left = `${Math.max(0, Math.min(100, pct))}%`;
            }

            if (!video.paused && !video.ended) {
                this._videoPlaybackRaf = requestAnimationFrame(loopCheck);
            }
        };

        // Fallback ontimeupdate loop
        video.ontimeupdate = () => {
            const start = item.trimStart || 0;
            const end = item.trimEnd || item.duration || video.duration;

            if (end > start && video.currentTime >= end) {
                video.currentTime = start;
                if (!video.paused) {
                    video.play().catch(() => {});
                }
            } else if (video.currentTime < start) {
                video.currentTime = start;
            }

            if (item.duration > 0 && this.trimmerPlayhead) {
                const pct = (video.currentTime / item.duration) * 100;
                this.trimmerPlayhead.style.left = `${Math.max(0, Math.min(100, pct))}%`;
            }
        };

        video.onplay = () => {
            if (this._videoPlaybackRaf) cancelAnimationFrame(this._videoPlaybackRaf);
            this._videoPlaybackRaf = requestAnimationFrame(loopCheck);
        };

        video.onpause = () => {
            if (this._videoPlaybackRaf) {
                cancelAnimationFrame(this._videoPlaybackRaf);
                this._videoPlaybackRaf = null;
            }
        };

        // Prevent seeking outside the selected trim range
        video.onseeking = () => {
            const start = item.trimStart || 0;
            const end = item.trimEnd || item.duration || video.duration;
            if (video.currentTime > end) {
                video.currentTime = end;
            } else if (video.currentTime < start) {
                video.currentTime = start;
            }
        };
    }

    _formatTime(seconds) {
        if (isNaN(seconds) || seconds < 0) return '0:00';
        const mins = Math.floor(seconds / 60);
        const secs = Math.floor(seconds % 60);
        return `${mins}:${secs.toString().padStart(2, '0')}`;
    }

    _formatFileSize(bytes) {
        if (!bytes || bytes <= 0 || isNaN(bytes)) return '0 KB';
        const k = 1024;
        const sizes = ['B', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
    }

    _updateSizeBadge(item) {
        if (!this.sizeBadge) return;
        if (!item || item.type !== 'video') {
            this.sizeBadge.classList.add('d-none');
            return;
        }

        const duration = item.duration || 1;
        const start = item.trimStart || 0;
        const end = item.trimEnd || duration;
        const selectedDuration = Math.max(0.2, end - start);

        // Estimate trimmed file size based on duration ratio
        const ratio = duration > 0 ? (selectedDuration / duration) : 1;
        const estBytes = Math.round((item.size || 0) * Math.max(0.02, Math.min(1, ratio)));

        this.sizeBadge.textContent = `${this._formatFileSize(estBytes)} • ${this._formatTime(selectedDuration)}`;
        this.sizeBadge.classList.remove('d-none');
    }

    /**
     * Update trimmer UI, highlighting cropped window and fading out unselected portions
     */
    _updateTrimmerUI(item) {
        if (!this.trimmerBar || !item || item.type !== 'video') return;
        const duration = item.duration || 0;
        const start = item.trimStart || 0;
        const end = item.trimEnd || duration;
        const selected = Math.max(0, end - start);

        if (this.trimmerStartLabel) this.trimmerStartLabel.textContent = this._formatTime(start);
        if (this.trimmerEndLabel) this.trimmerEndLabel.textContent = this._formatTime(end);
        if (this.trimmerDurationLabel) this.trimmerDurationLabel.textContent = `${this._formatTime(selected)} selected`;

        if (duration > 0) {
            const leftPercent = Math.max(0, Math.min(100, (start / duration) * 100));
            const rightPercent = Math.max(0, Math.min(100, 100 - (end / duration) * 100));

            // Highlight bounded selection window
            if (this.trimmerSelection) {
                this.trimmerSelection.style.left = `${leftPercent}%`;
                this.trimmerSelection.style.right = `${rightPercent}%`;
            }

            // Fade out outside masks
            if (this.trimmerMaskLeft) {
                this.trimmerMaskLeft.style.width = `${leftPercent}%`;
            }
            if (this.trimmerMaskRight) {
                this.trimmerMaskRight.style.width = `${rightPercent}%`;
            }
        }

        this._updateSizeBadge(item);
    }

    _setupTrimmerDrag() {
        if (!this.trimmerTrack || !this.trimmerHandleLeft || !this.trimmerHandleRight) return;

        const onDrag = (e, isLeft) => {
            const item = this.mediaItems[this.activeMediaIndex];
            if (!item || item.type !== 'video' || !item.duration) return;
            const rect = this.trimmerTrack.getBoundingClientRect();
            const clientX = e.touches ? e.touches[0].clientX : e.clientX;
            const ratio = Math.max(0, Math.min(1, (clientX - rect.left) / rect.width));
            const time = ratio * item.duration;

            const videoEl = this.previewCanvas.container?.querySelector('video');

            if (isLeft) {
                item.trimStart = Math.min(time, (item.trimEnd || item.duration) - 0.5);
                if (videoEl) videoEl.currentTime = item.trimStart;
            } else {
                item.trimEnd = Math.max(time, (item.trimStart || 0) + 0.5);
                if (videoEl) videoEl.currentTime = item.trimEnd;
            }
            this._updateTrimmerUI(item);
        };

        const attachDrag = (handle, isLeft) => {
            const handleStart = (e) => {
                e.preventDefault();
                const videoEl = this.previewCanvas.container?.querySelector('video');
                if (videoEl && !videoEl.paused) {
                    videoEl.pause();
                }

                const moveHandler = (moveEvent) => onDrag(moveEvent, isLeft);
                const upHandler = () => {
                    document.removeEventListener('mousemove', moveHandler);
                    document.removeEventListener('mouseup', upHandler);
                    document.removeEventListener('touchmove', moveHandler);
                    document.removeEventListener('touchend', upHandler);

                    // Automatically play from the specified start point when trimmed
                    const currentItem = this.mediaItems[this.activeMediaIndex];
                    if (videoEl && currentItem && currentItem.type === 'video') {
                        const start = Math.max(0, currentItem.trimStart || 0);
                        videoEl.currentTime = start;

                        if (this.previewCanvas?.hideLoading) {
                            this.previewCanvas.hideLoading();
                        }

                        const playPromise = videoEl.play();
                        if (playPromise !== undefined) {
                            playPromise.then(() => {
                                if (this.previewCanvas?.hideLoading) {
                                    this.previewCanvas.hideLoading();
                                }
                            }).catch((err) => {
                                console.warn('[MEDIA_COMPOSER] Autoplay blocked, muting and continuing playback:', err);
                                videoEl.muted = true;
                                currentItem.isMuted = true;
                                this._updateMuteUI(currentItem);
                                videoEl.play().catch(() => {});
                            }).finally(() => {
                                if (this.previewCanvas?.hideLoading) {
                                    this.previewCanvas.hideLoading();
                                }
                            });
                        }
                    }
                };
                document.addEventListener('mousemove', moveHandler);
                document.addEventListener('mouseup', upHandler);
                document.addEventListener('touchmove', moveHandler);
                document.addEventListener('touchend', upHandler);
            };

            handle.addEventListener('mousedown', handleStart);
            handle.addEventListener('touchstart', handleStart, { passive: false });
        };

        attachDrag(this.trimmerHandleLeft, true);
        attachDrag(this.trimmerHandleRight, false);
    }

    updateMediaCaption(caption) {
        if (this.mediaItems[this.activeMediaIndex]) {
            this.mediaItems[this.activeMediaIndex].caption = caption;
        }
    }

    updateGlobalCaption(caption) {
        this.globalCaption = caption;
    }

    removeMediaItem(index) {
        if (this.mediaItems.length <= 1) {
            this.close();
            return;
        }

        if (this.mediaItems[index]?.previewUrl) {
            URL.revokeObjectURL(this.mediaItems[index].previewUrl);
        }

        this.mediaItems.splice(index, 1);

        if (this.activeMediaIndex >= this.mediaItems.length) {
            this.activeMediaIndex = this.mediaItems.length - 1;
        }

        this.thumbnailStrip.render(this.mediaItems);
        this.setActiveMedia(this.activeMediaIndex);
    }

    addFiles(files) {
        const newItems = this._processFiles(files);
        this.mediaItems = [...this.mediaItems, ...newItems];
        this.thumbnailStrip.render(this.mediaItems);
        this.setActiveMedia(this.mediaItems.length - 1);
    }

    triggerFileInput() {
        const input = document.createElement('input');
        input.type = 'file';
        input.multiple = true;
        input.accept = 'image/*,video/*,audio/*,.pdf,.doc,.docx,.txt';

        input.onchange = (e) => {
            if (e.target.files.length > 0) {
                this.addFiles(e.target.files);
            }
        };

        input.click();
    }

    _setSendButtonLoading(loading) {
        if (!this.sendBtn) return;
        this.sendBtn.disabled = loading;
        const icon = this.sendBtn.querySelector('i');
        const spinner = this.sendBtn.querySelector('.spinner-border');
        if (loading) {
            if (icon) icon.classList.add('d-none');
            if (spinner) spinner.classList.remove('d-none');
        } else {
            if (icon) icon.classList.remove('d-none');
            if (spinner) spinner.classList.add('d-none');
        }
    }

    async send() {
        if (this.mediaItems.length === 0) return;

        // Ensure caption is synced from CaptionInput before dispatching
        if (this.captionInput) {
            const currentCaption = this.captionInput.getCurrentCaption();
            if (currentCaption !== undefined && currentCaption !== null) {
                this.globalCaption = currentCaption;
                if (this.mediaItems[this.activeMediaIndex]) {
                    this.mediaItems[this.activeMediaIndex].caption = currentCaption;
                }
            }
        }

        // Snapshot items and parameters for background upload
        const itemsToUpload = this.mediaItems.map(item => ({
            ...item,
            // Ensure previewUrl is preserved for optimistic bubble
            previewUrl: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : ''),
            thumbnail: item.thumbnail || '',
            width: item.width || 0,
            height: item.height || 0
        }));
        const captionToUpload = this.globalCaption || '';
        const conversationId = this.conversationId;

        // Clean up draft and caption input immediately
        this._clearDraft();
        if (this.captionInput) {
            this.captionInput.clear();
        }
        this.globalCaption = '';

        // IMMEDIATELY dismiss the media composer so the user can continue chatting and texting!
        this.close(true);

        // Kick off background upload with optimistic message and live progress tracking
        this.uploadQueue.upload(
            itemsToUpload,
            captionToUpload,
            conversationId
        ).catch(error => {
            console.error('[MEDIA_COMPOSER] Background send error:', error);
        });
    }

    _handleKeydown(e) {
        if (!this.isOpen) return;
        if (e.key === 'Escape') {
            this.close();
        } else if (e.key === 'ArrowLeft') {
            e.preventDefault();
            this.setActiveMedia(this.activeMediaIndex - 1);
        } else if (e.key === 'ArrowRight') {
            e.preventDefault();
            this.setActiveMedia(this.activeMediaIndex + 1);
        }
    }

    _saveDraft() {
        if (!this.conversationId) return;
        const draft = {
            conversationId: this.conversationId,
            globalCaption: this.globalCaption,
            mediaCount: this.mediaItems.length,
            timestamp: Date.now()
        };
        sessionStorage.setItem(`media_draft_${this.conversationId}`, JSON.stringify(draft));
    }

    _restoreDraft() {
        if (!this.conversationId) return;
        const draftKey = `media_draft_${this.conversationId}`;
        const draft = sessionStorage.getItem(draftKey);
        if (draft) {
            const parsed = JSON.parse(draft);
            const age = Date.now() - parsed.timestamp;
            if (age < 3600000) {
                this.globalCaption = parsed.globalCaption || '';
                this.captionInput?.setGlobalCaption(this.globalCaption);
            }
        }
    }

    _clearDraft() {
        if (!this.conversationId) return;
        sessionStorage.removeItem(`media_draft_${this.conversationId}`);
    }

    destroy() {
        if (this.previewCanvas) this.previewCanvas.destroy();
        if (this.thumbnailStrip) this.thumbnailStrip.destroy();
        if (this.captionInput) this.captionInput.destroy();
        if (this.uploadQueue) this.uploadQueue.destroy();

        this.mediaItems.forEach(item => {
            if (item.previewUrl) {
                URL.revokeObjectURL(item.previewUrl);
            }
        });

        this.initialized = false;
    }
}

export const mediaComposer = new MediaComposer();
