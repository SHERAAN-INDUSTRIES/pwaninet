/**
 * UploadQueue - Handles batch upload of media files
 * Manages background upload progress, retry logic, and optimistic messaging
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { ImageCompressor } from './ImageCompressor.js?v=20261106-group1';
import { deviceMediaStore } from '../../core/device-media-store.js?v=20261106-group1';

export class UploadQueue {
    constructor(composer) {
        this.composer = composer;
        this.activeUploads = new Map();
        this.pendingPayloads = new Map();
        this.initialized = false;
    }
    
    /**
     * Initialize upload queue
     */
    init() {
        this.initialized = true;
        // Media retry requests (emitted when a failed_upload bubble's retry
        // button is tapped and the text-message retry path has nothing).
        eventBus.on(EVENTS.MESSAGE_UPLOAD_RETRY, (data) => {
            if (data && data.tempId) this.retry(data.tempId);
        });
        console.log('[UPLOAD_QUEUE] Upload queue initialized');
    }
    
    /**
     * Upload media items in background
     * @param {Array} mediaItems - Media items to upload
     * @param {string} globalCaption - Global caption
     * @param {number} conversationId - Conversation ID
     */
    async upload(mediaItems, globalCaption, conversationId) {
        if (!mediaItems || mediaItems.length === 0) return;
        console.log('[UPLOAD_QUEUE] Background upload started with:', mediaItems.length, 'items, conversation:', conversationId);

        // 1. Generate temp ID and emit optimistic message immediately so user sees bubble in chat right away
        const tempId = this._generateTempId();
        console.log('[UPLOAD_QUEUE] Generated tempId:', tempId);

        // Ensure all items have preview URLs
        const itemsWithPreviews = mediaItems.map(item => ({
            ...item,
            previewUrl: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : '')
        }));

        this._emitOptimisticMessage(tempId, itemsWithPreviews, globalCaption);

        // Persist sender's original blobs to device storage (Capacitor flash storage or Web IndexedDB)
        itemsWithPreviews.forEach((item, idx) => {
            if (item.file) {
                const itemKey = itemsWithPreviews.length > 1 ? `${tempId}_${idx}` : tempId;
                deviceMediaStore.saveSenderMedia(itemKey, item.file, item.file.name || 'media');
                if (idx === 0 && itemsWithPreviews.length > 1) {
                    deviceMediaStore.saveSenderMedia(tempId, item.file, item.file.name || 'media');
                }
            }
        });

        try {
            // 2. Client-side pre-compression on photos (documents/videos/audio bypass)
            const processedItems = await Promise.all(itemsWithPreviews.map(async (item) => {
                if (item.type === 'image' && item.file && !item.isDocument) {
                    try {
                        const compressedFile = await ImageCompressor.compressImage(item.file);
                        return {
                            ...item,
                            file: compressedFile,
                            size: compressedFile.size,
                            name: compressedFile.name
                        };
                    } catch (err) {
                        console.warn('[UPLOAD_QUEUE] Image compression fallback to original:', err);
                        return item;
                    }
                }
                return item;
            }));

            // 3. Prepare form data
            const formData = new FormData();
            formData.append('conversation_id', conversationId);
            formData.append('temp_id', tempId);
            formData.append('global_caption', globalCaption || '');
            
            // Add files
            processedItems.forEach((item, index) => {
                console.log('[UPLOAD_QUEUE] Adding file:', index, item.file.name, item.type, `(${item.size} bytes)`);
                formData.append('files', item.file);
            });
            
            // Add attachment metadata
            const attachmentsData = processedItems.map((item, index) => {
                const isTrimmed = item.type === 'video' && (
                    (item.trimStart && item.trimStart > 0.05) ||
                    (item.duration && item.trimEnd && (item.duration - item.trimEnd) > 0.15)
                );
                return {
                    caption: item.caption || '',
                    order: index,
                    size: item.size,
                    rotation: item.rotation || 0,
                    crop_aspect: item.cropAspect || 'free',
                    duration: item.duration || 0,
                    trim_start: item.trimStart || 0,
                    trim_end: isTrimmed ? (item.trimEnd || item.duration || 0) : (item.duration || 0),
                    is_trimmed: !!isTrimmed,
                    is_muted: !!item.isMuted
                };
            });
            formData.append('attachments_data', JSON.stringify(attachmentsData));
            
            const csrfToken = this._getCSRFToken();
            if (csrfToken) {
                formData.append('csrfmiddlewaretoken', csrfToken);
            }

            // Retain payload so failed uploads can be retried from the
            // bubble's retry button even after error/cancel/reload-less nav.
            this.pendingPayloads.set(tempId, {
                formData,
                csrfToken,
                conversationId,
                siblingIds: this._collectEmittedTempIds(tempId, mediaItems)
            });

            // 4. Execute upload via XMLHttpRequest for real-time progress events
            await this._performXHRUpload(tempId, formData, csrfToken);

        } catch (error) {
            console.error('[UPLOAD_QUEUE] Upload error:', error);
            this.activeUploads.delete(tempId);
            this._emitUploadFailed(tempId, error.message || 'Upload failed');
        }
    }

    /**
     * All optimistic bubble IDs produced for one upload() call: the base
     * tempId plus per-file siblings for docs/audio batches.
     */
    _collectEmittedTempIds(tempId, mediaItems) {
        const ids = [tempId];
        if (!mediaItems || mediaItems.length <= 1) return ids;
        const areAllDocs = mediaItems.every(item => item.type === 'document');
        const areAllAudios = mediaItems.every(item => item.type === 'audio');
        if (areAllDocs || areAllAudios) {
            mediaItems.forEach((_, index) => {
                if (index !== 0) ids.push(`${tempId}_${index}`);
            });
        }
        return ids;
    }

    /**
     * Fail every bubble from one upload so none hangs on a spinner forever.
     */
    _emitUploadFailed(tempId, errorMessage) {
        const payload = this.pendingPayloads.get(tempId);
        const ids = (payload && payload.siblingIds) || [tempId];
        ids.forEach(id => {
            eventBus.emit(EVENTS.MESSAGE_UPLOAD_FAILED, {
                tempId: id,
                error: errorMessage
            });
        });
    }

    /**
     * Perform XHR upload with progress events
     */
    _performXHRUpload(tempId, formData, csrfToken) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            this.activeUploads.set(tempId, xhr);

            xhr.open('POST', '/messaging/api/attachments/batch-upload/', true);
            if (csrfToken) {
                xhr.setRequestHeader('X-CSRFToken', csrfToken);
            }
            // Generous timeout for large videos on slow mobile links; without
            // this ontimeout is unreachable and hangs look like silence.
            try { xhr.timeout = 600000; } catch (_) {}

            // Live progress tracking
            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable && e.total > 0) {
                    const rawPercent = Math.round((e.loaded / e.total) * 100);
                    const percent = Math.min(100, Math.max(1, rawPercent));
                    const isProcessing = rawPercent >= 100;
                    this._updateProgressUI(tempId, percent, isProcessing);
                    eventBus.emit(EVENTS.MESSAGE_UPLOAD_PROGRESS, {
                        tempId,
                        progress: percent,
                        isProcessing,
                        loaded: e.loaded,
                        total: e.total
                    });
                }
            };

            xhr.onload = () => {
                this.activeUploads.delete(tempId);
                if (xhr.status >= 200 && xhr.status < 300) {
                    // Payload no longer needed; free the retained FormData.
                    this.pendingPayloads.delete(tempId);
                    try {
                        const data = JSON.parse(xhr.responseText);
                        console.log('[UPLOAD_QUEUE] Server response parsed successfully:', data);

                        if (Array.isArray(data)) {
                            data.forEach((serverMsg, idx) => {
                                const currentTempId = idx === 0 ? tempId : `${tempId}_${idx}`;
                                if (serverMsg?.id) {
                                    deviceMediaStore.rekeyMedia(currentTempId, serverMsg.id);
                                    if (serverMsg?.attachments && Array.isArray(serverMsg.attachments)) {
                                        serverMsg.attachments.forEach((att, attIdx) => {
                                            if (att?.id) {
                                                deviceMediaStore.rekeyMedia(`${currentTempId}_${attIdx}`, att.id);
                                            }
                                        });
                                    }
                                }
                                eventBus.emit(EVENTS.MESSAGE_UPLOAD_SUCCESS, {
                                    tempId: currentTempId,
                                    serverMessage: serverMsg
                                });
                            });
                        } else {
                            if (data?.id) {
                                deviceMediaStore.rekeyMedia(tempId, data.id);
                                if (data?.attachments && Array.isArray(data.attachments)) {
                                    data.attachments.forEach((att, attIdx) => {
                                        if (att?.id) {
                                            deviceMediaStore.rekeyMedia(`${tempId}_${attIdx}`, att.id);
                                            deviceMediaStore.rekeyMedia(`${data.id}_${attIdx}`, att.id);
                                        }
                                    });
                                }
                            }
                            eventBus.emit(EVENTS.MESSAGE_UPLOAD_SUCCESS, {
                                tempId,
                                serverMessage: data
                            });
                        }
                        resolve(data);
                    } catch (err) {
                        console.error('[UPLOAD_QUEUE] JSON parse error:', err);
                        reject(new Error('Invalid server response format'));
                    }
                } else {
                    let errorData = null;
                    try {
                        errorData = JSON.parse(xhr.responseText);
                    } catch (_) {}
                    const errorMsg = (errorData && (errorData.error || errorData.detail || errorData.message)) ||
                                     `Upload failed (${xhr.status})`;
                    reject(new Error(errorMsg));
                }
            };

            xhr.onerror = () => {
                this.activeUploads.delete(tempId);
                reject(new Error('Network connection error during upload'));
            };

            xhr.onabort = () => {
                this.activeUploads.delete(tempId);
                reject(new Error('Upload cancelled'));
            };

            xhr.ontimeout = () => {
                this.activeUploads.delete(tempId);
                reject(new Error('Upload request timed out'));
            };

            xhr.send(formData);
        });
    }

    /**
     * Update progress indicator directly in DOM for smooth 60fps rendering
     */
    _updateProgressUI(tempId, percent, isProcessing = false) {
        const msgEl = document.querySelector(`[data-message-id="${tempId}"]`);
        if (!msgEl) return;

        // Update voice note upload indicator only while spinner is still active
        const vnTimer = msgEl.querySelector('.vn-timer');
        const vnSpinner = msgEl.querySelector('.vn-play-btn .spinner-border');
        if (vnTimer && vnSpinner) {
            vnTimer.textContent = isProcessing ? 'Processing...' : `${percent}%`;
        }

        // Update audio track upload indicator only while spinner is still active
        const audioTime = msgEl.querySelector('.audio-track-time');
        const audioSpinner = msgEl.querySelector('.audio-track-play-btn .spinner-border');
        if (audioTime && audioSpinner) {
            audioTime.textContent = isProcessing ? 'Processing...' : `${percent}%`;
        }

        // Update document progress indicator
        const docProgress = msgEl.querySelector('.document-progress-text');
        if (docProgress) {
            docProgress.textContent = isProcessing ? 'Processing...' : `${percent}%`;
        }

        const overlay = msgEl.querySelector('.media-upload-overlay');
        if (!overlay) return;

        const circleBar = overlay.querySelector('.upload-circle-bar');
        const textEl = overlay.querySelector('.upload-progress-text');

        if (circleBar) {
            // Circumference of r=15 is 2 * PI * 15 ≈ 94.25
            const circumference = 94.25;
            const offset = circumference * (1 - percent / 100);
            circleBar.style.strokeDashoffset = Math.max(0, offset).toFixed(1);
        }

        if (textEl) {
            if (isProcessing) {
                textEl.textContent = '100%';
                textEl.title = 'Processing media on server...';
            } else {
                textEl.textContent = `${percent}%`;
            }
        }

        if (percent >= 100 && !isProcessing) {
            overlay.classList.add('upload-complete');
        }
    }
    
    /**
     * Generate temporary ID for optimistic message
     * @returns {string} Temporary ID
     */
    _generateTempId() {
        return `temp_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }
    
    /**
     * Emit optimistic message
     * @param {string} tempId - Temporary ID
     * @param {Array} mediaItems - Media items
     * @param {string} globalCaption - Global caption
     */
    _emitOptimisticMessage(tempId, mediaItems, globalCaption) {
        console.log('[UPLOAD_QUEUE] Emitting optimistic message:', tempId);

        // Check if all items are documents or audio
        const areAllDocs = mediaItems.every(item => item.type === 'document');
        const areAllAudios = mediaItems.every(item => item.type === 'audio');

        if (areAllDocs) {
            // Emit separate optimistic document message for each document
            mediaItems.forEach((item, index) => {
                const docTempId = index === 0 ? tempId : `${tempId}_${index}`;
                const optMsg = {
                    id: docTempId,
                    temp_id: docTempId,
                    message_type: 'media',
                    attachment_type: 'document',
                    content: item.caption || item.file?.name || item.name || 'Document',
                    sender: { id: null, username: 'You' },
                    metadata: {
                        type: 'document',
                        url: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : ''),
                        file_name: item.file?.name || item.name || 'Document',
                        size: item.size,
                        uploadProgress: 0
                    },
                    created_at: new Date().toISOString(),
                    status: 'uploading'
                };
                eventBus.emit(EVENTS.MESSAGE_OPTIMISTIC_ADD, optMsg);
            });
            return;
        }

        if (areAllAudios) {
            // Emit separate optimistic audio message for each audio
            mediaItems.forEach((item, index) => {
                const audioTempId = index === 0 ? tempId : `${tempId}_${index}`;
                const optMsg = {
                    id: audioTempId,
                    temp_id: audioTempId,
                    message_type: 'audio',
                    attachment_type: 'audio',
                    content: item.caption || item.file?.name || item.name || 'Audio',
                    sender: { id: null, username: 'You' },
                    metadata: {
                        type: 'audio',
                        url: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : ''),
                        file_name: item.file?.name || item.name || 'Audio',
                        size: item.size,
                        duration: item.duration || 0,
                        uploadProgress: 0
                    },
                    created_at: new Date().toISOString(),
                    status: 'uploading'
                };
                eventBus.emit(EVENTS.MESSAGE_OPTIMISTIC_ADD, optMsg);
            });
            return;
        }

        // Single photo or video
        if (mediaItems.length === 1) {
            const singleItem = mediaItems[0];
            const optimisticMessage = {
                id: tempId,
                temp_id: tempId,
                message_type: 'media',
                attachment_type: singleItem.type,
                content: singleItem.caption || globalCaption || '',
                global_caption: singleItem.caption || globalCaption || '',
                sender: { id: null, username: 'You' },
                metadata: {
                    type: singleItem.type,
                    url: singleItem.previewUrl || (singleItem.file ? URL.createObjectURL(singleItem.file) : ''),
                    thumbnail: singleItem.thumbnail || singleItem.previewUrl || '',
                    file_name: singleItem.name || singleItem.file?.name || 'Media',
                    size: singleItem.size,
                    duration: singleItem.duration || 0,
                    width: singleItem.width || 0,
                    height: singleItem.height || 0,
                    uploadProgress: 0
                },
                attachments: [{
                    id: `temp_attach_0`,
                    file_url: singleItem.previewUrl || (singleItem.file ? URL.createObjectURL(singleItem.file) : ''),
                    thumbnail: singleItem.thumbnail || singleItem.previewUrl || '',
                    file_type: singleItem.type,
                    caption: singleItem.caption || globalCaption || '',
                    order: 0,
                    size: singleItem.size,
                    duration: singleItem.duration || 0,
                    width: singleItem.width || 0,
                    height: singleItem.height || 0
                }],
                created_at: new Date().toISOString(),
                status: 'uploading'
            };
            eventBus.emit(EVENTS.MESSAGE_OPTIMISTIC_ADD, optimisticMessage);
            return;
        }

        // Standard photos & videos media group (2 or more items)
        const optimisticMessage = {
            id: tempId,
            temp_id: tempId,
            message_type: 'media_group',
            global_caption: globalCaption || '',
            content: globalCaption || '',
            sender: {
                id: null,
                username: 'You'
            },
            metadata: {
                attachments: mediaItems.map((item, index) => ({
                    id: `temp_attach_${index}`,
                    file_url: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : ''),
                    thumbnail: item.thumbnail || item.previewUrl || '',
                    file_type: item.type,
                    caption: item.caption || '',
                    order: index,
                    size: item.size,
                    duration: item.duration || 0,
                    width: item.width || 0,
                    height: item.height || 0
                })),
                global_caption: globalCaption || '',
                uploadProgress: 0
            },
            attachments: mediaItems.map((item, index) => ({
                id: `temp_attach_${index}`,
                file_url: item.previewUrl || (item.file ? URL.createObjectURL(item.file) : ''),
                thumbnail: item.thumbnail || item.previewUrl || '',
                file_type: item.type,
                caption: item.caption || '',
                order: index,
                size: item.size,
                duration: item.duration || 0,
                width: item.width || 0,
                height: item.height || 0
            })),
            created_at: new Date().toISOString(),
            status: 'uploading'
        };
        
        eventBus.emit(EVENTS.MESSAGE_OPTIMISTIC_ADD, optimisticMessage);
        console.log('[UPLOAD_QUEUE] Optimistic media group message emitted');
    }
    
    /**
     * Get CSRF token
     * @returns {string} CSRF token
     */
    _getCSRFToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.getAttribute('content')) {
            return meta.getAttribute('content');
        }
        const input = document.querySelector('[name="csrfmiddlewaretoken"]');
        if (input && input.value) {
            return input.value;
        }
        const cookies = document.cookie.split(';');
        for (const cookie of cookies) {
            const [name, value] = cookie.trim().split('=');
            if (name === 'csrftoken') {
                return decodeURIComponent(value);
            }
        }
        return '';
    }
    
    /**
     * Retry failed upload: replays the retained FormData so the user gets
     * another chance instead of watching the bubble disappear.
     * @param {string} tempId - Temporary ID of failed message (base or sibling)
     */
    async retry(tempId) {
        console.log('[UPLOAD_QUEUE] Retry upload for:', tempId);
        const payload = this.pendingPayloads.get(tempId) || this._findPayload(tempId);
        if (!payload) {
            console.warn('[UPLOAD_QUEUE] No retained payload for retry:', tempId);
            eventBus.emit(EVENTS.MESSAGE_UPLOAD_FAILED, {
                tempId,
                error: 'Original file is no longer available. Please reselect it.'
            });
            return false;
        }
        const baseTempId = payload.baseTempId;
        try {
            await this._performXHRUpload(baseTempId, payload.formData, payload.csrfToken);
            return true;
        } catch (error) {
            console.error('[UPLOAD_QUEUE] Retry error:', error);
            this.activeUploads.delete(baseTempId);
            this._emitUploadFailed(baseTempId, error.message || 'Upload failed');
            return false;
        }
    }

    /**
     * Find retained payload by base or sibling tempId.
     */
    _findPayload(tempId) {
        if (this.pendingPayloads.has(tempId)) {
            const p = this.pendingPayloads.get(tempId);
            return { ...p, baseTempId: tempId };
        }
        for (const [baseTempId, p] of this.pendingPayloads.entries()) {
            if ((p.siblingIds || []).includes(tempId)) {
                return { ...p, baseTempId };
            }
        }
        return null;
    }
    
    /**
     * Cancel upload: abort XHR and fail the bubbles so they show retry
     * instead of hanging on a spinner forever.
     */
    cancel(tempId) {
        if (tempId && this.activeUploads.has(tempId)) {
            const xhr = this.activeUploads.get(tempId);
            try { xhr.abort(); } catch (_) {}
            this.activeUploads.delete(tempId);
            console.log('[UPLOAD_QUEUE] Cancelled upload for:', tempId);
            this._emitUploadFailed(tempId, 'Upload cancelled');
        }
    }
    
    /**
     * Destroy upload queue
     */
    destroy() {
        this.activeUploads.forEach(xhr => xhr.abort());
        this.activeUploads.clear();
        this.initialized = false;
        console.log('[UPLOAD_QUEUE] Upload queue destroyed');
    }
}
