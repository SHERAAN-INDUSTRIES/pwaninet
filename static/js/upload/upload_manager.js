/**
 * Upload Manager
 *
 * Single source of truth for all post uploads.
 * Owns session lifecycle, queue orchestration, progress, retries, cancellation,
 * preview cleanup, and UploadAPI communication.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261011-16';
import { UploadState, stateMachineFactory } from './upload_state_machine.js?v=20261011-16';
import { UploadValidator } from './upload_validator.js?v=20261011-16';
import { ImageCompressor } from './image_compression.js?v=20261011-16';
import { previewManager } from './preview_manager.js?v=20261011-16';
import { uploadAPI } from './upload_api.js?v=20261011-16';
import { uploadQueue } from './upload_queue.js?v=20261011-16';
import { progressManager } from './upload_progress.js?v=20261011-16';
import { UploadSession } from './upload_session.js?v=20261011-16';

class UploadManager {
    constructor() {
        this.uploads = new Map(); // uploadId -> UploadSession
        this.isInitialized = false;
    }

    initialize() {
        if (this.isInitialized) return;

        uploadEvents.on(UploadEventNames.UPLOAD_ADDED, (data) => {
            this.processUpload(data.id);
        });

        this.isInitialized = true;
    }

    createUpload(data) {
        const session = data instanceof UploadSession
            ? data
            : new UploadSession({
                id: uploadQueue.generateId(),
                files: data.files || [],
                caption: data.caption || data.content || '',
                visibility: data.visibility || 'public',
                metadata: {
                    unit: data.unit || data.metadata?.unit || null,
                    group: data.group || data.metadata?.group || null,
                    allow_downloads: data.allow_downloads ?? data.metadata?.allow_downloads ?? true,
                    gradient_class: data.gradient_class || data.metadata?.gradient_class || 'none',
                    custom_gradient_text: data.custom_gradient_text || data.metadata?.custom_gradient_text || null,
                    custom_gradient_color1: data.custom_gradient_color1 || data.metadata?.custom_gradient_color1 || null,
                    custom_gradient_color2: data.custom_gradient_color2 || data.metadata?.custom_gradient_color2 || null,
                    custom_gradient_text_color: data.custom_gradient_text_color || data.metadata?.custom_gradient_text_color || null,
                    video_width: data.video_width || data.metadata?.video_width || null,
                    video_height: data.video_height || data.metadata?.video_height || null,
                    video_duration: data.video_duration || data.metadata?.video_duration || null,
                    video_poster_blob: data.video_poster_blob || data.metadata?.video_poster_blob || null,
                },
            });

        this.uploads.set(session.id, session);
        stateMachineFactory.getMachine(session.id, UploadState.IDLE);
        progressManager.getTracker(session.id);

        uploadEvents.emit(UploadEventNames.UPLOAD_CREATED, {
            uploadId: session.id,
            session,
        });

        uploadQueue.addSession(session);
        return session.id;
    }

    async processUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) {
            // This manager does not own the session (historically: two module
            // instances of this file loaded under different import URLs, so
            // the composer registered the upload elsewhere). Never return
            // silently -- the queue item would sit at the head forever and
            // every later upload would hang at "Preparing…". Fail it so the
            // queue keeps moving and the banner reports the failure.
            const error = new Error(`Upload session ${uploadId} not found`);
            console.error('[UploadManager] processUpload: ' + error.message);
            uploadQueue.uploadFailed(uploadId, error);
            uploadEvents.emit(UploadEventNames.UPLOAD_FAILED, {
                uploadId,
                session: null,
                error: error.message,
                canRetry: false,
            });
            return;
        }

        const PREPARING_TIMEOUT_MS = 2 * 60 * 1000; // 2 minutes for preparation
        let preparingTimeout = null;

        try {
            // Everything that can throw stays inside the try: an exception
            // escaping processUpload would never reach failUpload, and the
            // queue would wait for this item forever.
            const progressTracker = progressManager.getTracker(uploadId);

            progressTracker.start();

            // Set up timeout for preparation phase
            preparingTimeout = setTimeout(() => {
                const error = new Error(`Upload ${uploadId} timed out during preparation (2 minutes)`);
                console.error('[UploadManager] Preparation timeout:', error.message);
                this.failUpload(uploadId, error);
            }, PREPARING_TIMEOUT_MS);

            this.transitionState(uploadId, UploadState.PREPARING);
            await this.prepareUpload(uploadId);

            clearTimeout(preparingTimeout);
            preparingTimeout = null;

            this.transitionState(uploadId, UploadState.VALIDATING);
            uploadEvents.emit(UploadEventNames.VALIDATION_STARTED, { uploadId });
            const validationResult = await this.validateUpload(uploadId);
            if (!validationResult.isValid) {
                throw new Error(validationResult.errors[0]?.message || 'Validation failed');
            }
            uploadEvents.emit(UploadEventNames.VALIDATION_COMPLETED, { uploadId });
            progressTracker.completeValidation();

            this.transitionState(uploadId, UploadState.READING_MEDIA);
            await this.readMedia(uploadId);

            this.transitionState(uploadId, UploadState.GENERATING_PREVIEW);
            await this.generatePreviews(uploadId);

            this.transitionState(uploadId, UploadState.COMPRESSING_IMAGES);
            await this.compressImages(uploadId);

            this.transitionState(uploadId, UploadState.READY);
            this.transitionState(uploadId, UploadState.QUEUED);

            this.waitForQueueTurn(uploadId);
        } catch (error) {
            if (preparingTimeout) clearTimeout(preparingTimeout);
            this.failUpload(uploadId, error);
        }
    }

    transitionState(uploadId, newState, metadata = {}) {
        const session = this.uploads.get(uploadId);
        const machine = stateMachineFactory.getMachine(uploadId);
        if (!session || !machine) return false;

        const transitioned = machine.transitionTo(newState, metadata);
        if (transitioned) {
            session.setState(newState);
        }
        return transitioned;
    }

    async prepareUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) return;
    }

    async validateUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) {
            throw new Error('Upload session not found');
        }

        const tracker = progressManager.getTracker(uploadId);
        tracker.updateValidationProgress(0, session.files.length);

        const validationResult = await UploadValidator.validateUpload({
            files: session.files,
            caption: session.caption,
        });

        tracker.updateValidationProgress(session.files.length, session.files.length);
        return validationResult;
    }

    async readMedia(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) return;
    }

    async generatePreviews(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) return;

        const previews = await previewManager.generatePreviews(session.files, uploadId);
        session.previewUrls = previews.map((preview) => preview.objectUrl).filter(Boolean);

        // If session has video and dimensions are not yet recorded, populate them from preview metadata
        const videoPreview = previews.find((p) => p.type === 'video');
        if (videoPreview && videoPreview.metadata) {
            if (!session.metadata.video_width && videoPreview.metadata.width) {
                session.metadata.video_width = videoPreview.metadata.width;
            }
            if (!session.metadata.video_height && videoPreview.metadata.height) {
                session.metadata.video_height = videoPreview.metadata.height;
            }
            if (!session.metadata.video_duration && videoPreview.metadata.duration) {
                session.metadata.video_duration = Math.round(videoPreview.metadata.duration);
            }
            if (!session.metadata.video_poster_blob && videoPreview.metadata.posterBlob) {
                session.metadata.video_poster_blob = videoPreview.metadata.posterBlob;
            }
        }
    }

    async compressImages(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) return;

        const tracker = progressManager.getTracker(uploadId);
        uploadEvents.emit(UploadEventNames.COMPRESSION_STARTED, { uploadId });

        const compressionResults = await ImageCompressor.compressMultiple(session.files);
        session.files = compressionResults.map((result) => result.compressedFile || result.originalFile);
        tracker.completeCompression();
    }

    waitForQueueTurn(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) return;

        const TIMEOUT_MS = 5 * 60 * 1000; // 5 minutes timeout
        let isTimedOut = false;

        const timeoutHandle = setTimeout(() => {
            isTimedOut = true;
            const error = new Error(`Upload ${uploadId} timed out waiting for queue turn (5 minutes)`);
            console.error('[UploadManager] waitForQueueTurn timeout:', error.message);
            this.failUpload(uploadId, error);
        }, TIMEOUT_MS);

        const checkQueue = () => {
            if (isTimedOut) return;

            const currentUpload = uploadQueue.getCurrentUpload();
            if (currentUpload && currentUpload.id === uploadId) {
                clearTimeout(timeoutHandle);
                this.performUpload(uploadId);
            } else if (this.uploads.has(uploadId)) {
                setTimeout(checkQueue, 100);
            } else {
                // Session was removed while waiting - fail the upload
                clearTimeout(timeoutHandle);
                const error = new Error(`Upload session ${uploadId} was removed while waiting for queue turn`);
                console.error('[UploadManager] waitForQueueTurn session removed:', error.message);
                this.failUpload(uploadId, error);
            }
        };

        checkQueue();
    }

    async performUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session) {
            // Reached the head of the queue without a session (cleanup race
            // or module-instance drift). Never return silently here: the
            // queue waits on this item forever and every later upload would
            // hang at "Preparing…".
            const error = new Error(`Upload session ${uploadId} not found at upload time`);
            console.error('[UploadManager] performUpload: ' + error.message);
            uploadQueue.uploadFailed(uploadId, error);
            uploadEvents.emit(UploadEventNames.UPLOAD_FAILED, {
                uploadId,
                session: null,
                error: error.message,
                canRetry: false,
            });
            return;
        }

        console.log('[UploadManager] performUpload called for uploadId:', uploadId, 'files:', session.files.length);

        try {
            const tracker = progressManager.getTracker(uploadId);

            this.transitionState(uploadId, UploadState.UPLOADING);
            uploadEvents.emit(UploadEventNames.UPLOAD_STARTED, { uploadId, session });

            const hasFiles = Array.isArray(session.files) && session.files.length > 0;
            let response = null;

            if (!hasFiles) {
                // Text/link/gradient posts carry no bytes: bypass R2 and
                // create directly via the posts API (link preview +
                // thumbnail jobs run server-side there).
                console.log('[UploadManager] Text-only post, using posts API...');
                response = await uploadAPI.upload(session, (p) => {
                    tracker.updateUploadProgress(p.loaded, p.total);
                });
            } else {
                // File uploads use the resumable direct-to-R2 engine
                console.log('[UploadManager] Checking resumable engine...');
                if (!window.resumableUpload || typeof window.resumableUpload.start !== 'function') {
                    console.error('[UploadManager] Resumable engine not available:', {
                        hasWindowResumable: !!window.resumableUpload,
                        hasStartFunction: typeof window.resumableUpload?.start === 'function',
                    });
                    throw new Error('Upload engine not loaded. Please refresh the page and try again.');
                }
                console.log('[UploadManager] Resumable engine available, starting upload...');

                response = await this._performResumableUpload(uploadId, session, tracker);
            }
            tracker.completeUpload();

            // Determine whether this post has a video file so we know
            // whether to wait for live HLS transcoding progress.
            const postId   = response?.post_id ?? response?.id ?? response?.share_id ?? null;
            const hasVideo = session.files?.some(
                (f) => f.type?.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi)$/i.test(f.name ?? '')
            ) ?? false;

            // SERVER_PROCESSING must precede PUBLISHED (state machine
            // forbids UPLOADING -> PUBLISHED directly).
            this.transitionState(uploadId, UploadState.SERVER_PROCESSING, { hasVideo, postId });

            // Immediately notify UI and feed that post has been created
            uploadEvents.emit(UploadEventNames.UPLOAD_PUBLISHED, { uploadId, session, postId, hasVideo });

            if (hasVideo && postId) {
                await this.waitForServerProcessing(uploadId, postId, hasVideo, response);
                tracker.completeServerProcessing();
            } else {
                tracker.updateServerProcessingProgress(100, 100, 'Published!');
                tracker.completeServerProcessing();
            }

            this.transitionState(uploadId, UploadState.PUBLISHED);
            tracker.updatePublicationProgress(100);
            tracker.completePublication();

            uploadQueue.uploadCompleted(uploadId);
            uploadEvents.emit(UploadEventNames.UPLOAD_COMPLETED, { uploadId, session, postId });

            this.cleanupUpload(uploadId);
        } catch (error) {
            console.error('[UploadManager] Upload failed:', error);
            this.failUpload(uploadId, error);
        }
    }

    /**
     * Perform upload using the resumable direct-to-R2 engine
     * @private
     */
    async _performResumableUpload(uploadId, session, tracker) {
        // Generate a proper UUID for post_group
        const postGroup = (window.crypto && window.crypto.randomUUID)
            ? window.crypto.randomUUID()
            : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
                const r = Math.random() * 16 | 0;
                const v = c === 'x' ? r : (r & 0x3 | 0x8);
                return v.toString(16);
            });

        const postPayload = {
            content: session.caption,
            visibility: (session.visibility || 'public').toUpperCase(),
            group: session.metadata?.group || null,
            unit: session.metadata?.unit || null,
            gradient_class: session.metadata?.gradient_class || 'none',
            allow_downloads: session.metadata?.allow_downloads !== false,
            video_width: session.metadata?.video_width || null,
            video_height: session.metadata?.video_height || null,
            video_duration: session.metadata?.video_duration || null,
        };

        let finalResponse = null;
        let done = 0;

        // Setup progress listener for resumable upload
        const progressListener = (e) => {
            if (e.detail && e.detail.total) {
                const totalProgress = Math.round((100 * e.detail.loaded) / e.detail.total);
                tracker.updateUploadProgress(e.detail.loaded, e.detail.total);
            }
        };
        window.addEventListener('resumable-upload-progress', progressListener);

        try {
            // Upload each file sequentially
            for (const file of session.files) {
                console.log('[UploadManager] Starting resumable upload for file:', file.name, 'size:', file.size);
                const response = await window.resumableUpload.start(file, {
                    post: postPayload,
                    postGroup,
                });
                console.log('[UploadManager] Resumable upload completed for file:', file.name, 'response:', response);
                finalResponse = response;
                done += 1;
            }
        } finally {
            window.removeEventListener('resumable-upload-progress', progressListener);
        }

        return finalResponse;
    }

    failUpload(uploadId, error) {
        const session = this.uploads.get(uploadId);
        if (!session) {
            // Session already gone (or owned by another module instance).
            // Still fail the queue item: a failure that never reaches the
            // queue freezes its head and blocks every subsequent upload.
            uploadQueue.uploadFailed(
                uploadId,
                error instanceof Error ? error : new Error(String(error))
            );
            return;
        }

        this.transitionState(uploadId, UploadState.FAILED, { error: error.message });
        uploadQueue.uploadFailed(uploadId, error);

        const errorMsg = error.message || String(error);
        const isRetryable = this.isRetryableError(errorMsg);

        uploadEvents.emit(UploadEventNames.UPLOAD_FAILED, {
            uploadId,
            session,
            error: errorMsg,
            canRetry: session.canRetry() && isRetryable,
        });
        uploadEvents.emit(UploadEventNames.ERROR_OCCURRED, {
            uploadId,
            error: errorMsg,
        });

        // Auto-retry for retryable errors if retries available
        if (isRetryable && session.canRetry()) {
            console.log(`[UploadManager] Auto-retrying upload ${uploadId} (attempt ${session.retryCount + 1}/${session.maxRetries})`);
            setTimeout(() => {
                this.retryUpload(uploadId);
            }, 2000); // Wait 2 seconds before retry
        }
    }

    /**
     * Determine if an error is retryable
     * @param {string} errorMsg - Error message
     * @returns {boolean}
     */
    isRetryableError(errorMsg) {
        const retryablePatterns = [
            /timeout/i,
            /network/i,
            /connection/i,
            /fetch/i,
            /server.*error/i,
            /5\d\d/i, // 5xx server errors
            /ECONN/i,
            /ETIMEDOUT/i,
        ];

        return retryablePatterns.some(pattern => pattern.test(errorMsg));
    }

    /**
     * Wait for the server to finish processing a post.
     *
     * For video posts we listen to the feed WebSocket for ``video_progress``
     * messages emitted by the Celery HLS transcoding task. For resilience,
     * we also run a lightweight polling fallback (/api/posts/<id>/status/)
     * every 2.5s so client-side WebSocket disconnects or drops never cause
     * the banner to lag or hang.
     *
     * @param {string} uploadId
     * @param {number|string|null} postId – returned by the API
     * @param {boolean} hasVideo
     * @param {object} [responseData={}]
     * @returns {Promise<void>}
     */
    async waitForServerProcessing(uploadId, postId, hasVideo, responseData = {}) {
        const tracker = progressManager.getTracker(uploadId);
        if (!tracker) return;

        // ----------------------------------------------------------------
        // Non-video posts: quick synthetic update, done immediately.
        // ----------------------------------------------------------------
        if (!hasVideo || !postId) {
            tracker.updateServerProcessingProgress(50, 100, 'Saving to database…');
            await new Promise((r) => setTimeout(r, 400));
            tracker.updateServerProcessingProgress(100, 100, 'Published!');
            return;
        }

        // ----------------------------------------------------------------
        // Video posts: subscribe to live Celery progress + polling fallback
        // ----------------------------------------------------------------
        const TIMEOUT_MS = 3.5 * 60 * 1000; // 3.5 minutes safety cap

        // Collect all potential post identifiers (numeric pk or UUID share_id)
        const targetIds = new Set(
            [postId, responseData?.id, responseData?.post_id, responseData?.share_id]
                .filter(Boolean)
                .map(String)
        );

        return new Promise((resolve) => {
            let resolved = false;
            let pollInterval = null;

            const finish = () => {
                if (resolved) return;
                resolved = true;
                if (pollInterval) clearInterval(pollInterval);
                clearTimeout(timeoutHandle);
                window.removeEventListener('feedVideoProgress', onProgress);
                resolve();
            };

            const timeoutHandle = setTimeout(() => {
                tracker.updateServerProcessingProgress(100, 100, 'Video ready!');
                finish();
            }, TIMEOUT_MS);

            const onProgress = (evt) => {
                const detail = evt?.detail || {};
                const evtPostId = detail.post_id != null ? String(detail.post_id) : '';
                const evtShareId = detail.share_id != null ? String(detail.share_id) : '';

                // Only handle messages matching this specific post (by integer pk or UUID share_id)
                const isMatch = (evtPostId && targetIds.has(evtPostId)) || (evtShareId && targetIds.has(evtShareId));
                if (!isMatch) return;

                const { status, progress, message } = detail;
                tracker.updateServerProcessingProgress(
                    progress ?? 0,
                    100,
                    message || 'Processing video…'
                );

                if (status === 'ready' || status === 'failed') {
                    finish();
                }
            };

            // 1. Listen for real-time Django Channels WebSocket progress
            window.addEventListener('feedVideoProgress', onProgress);

            // 2. Active polling fallback every 2.5 seconds to query /status/ endpoint
            const pollLookupId = responseData?.share_id || postId;
            if (pollLookupId) {
                const pollUrl = `/api/posts/${pollLookupId}/status/`;
                pollInterval = setInterval(async () => {
                    if (resolved) {
                        clearInterval(pollInterval);
                        return;
                    }
                    try {
                        const res = await fetch(pollUrl, {
                            headers: {
                                'Accept': 'application/json',
                                'X-Requested-With': 'XMLHttpRequest',
                            },
                            credentials: 'same-origin',
                        });
                        if (!res.ok) return;
                        const data = await res.json();
                        if (data.video_status === 'ready' || data.is_ready === true) {
                            tracker.updateServerProcessingProgress(100, 100, 'Video ready in HD!');
                            finish();
                        } else if (data.video_status === 'failed') {
                            finish();
                        }
                    } catch (e) {
                        // Ignore transient network errors during background poll
                    }
                }, 2500);
            }

            // Seed the UI immediately so the banner doesn't sit blank
            tracker.updateServerProcessingProgress(10, 100, 'Starting video transcoding…');
        });
    }

    /** @deprecated  Replaced by waitForServerProcessing — kept for safety. */
    async simulateServerProcessing(uploadId) {
        await this.waitForServerProcessing(uploadId, null, false);
    }


    cancelUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        const stateMachine = stateMachineFactory.peekMachine(uploadId);
        if (!session || !stateMachine || !stateMachine.canCancel()) {
            return false;
        }

        session.cancel();
        this.transitionState(uploadId, UploadState.CANCELLED);
        uploadQueue.cancel(uploadId);

        uploadEvents.emit(UploadEventNames.UPLOAD_CANCELLED, { uploadId, session });
        this.cleanupUpload(uploadId);
        return true;
    }

    retryUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        if (!session || !session.canRetry()) {
            return false;
        }

        session.incrementRetry();
        uploadQueue.retry(uploadId);

        uploadEvents.emit(UploadEventNames.UPLOAD_RETRIED, {
            uploadId,
            session,
            retryCount: session.retryCount,
        });

        this.transitionState(uploadId, UploadState.QUEUED);
        this.waitForQueueTurn(uploadId);
        return true;
    }

    cleanupUpload(uploadId) {
        const session = this.uploads.get(uploadId);
        previewManager.revokePreviews(uploadId);

        if (session) {
            session.cleanup();
        }

        progressManager.removeTracker(uploadId);
        stateMachineFactory.removeMachine(uploadId);

        setTimeout(() => {
            this.uploads.delete(uploadId);
        }, 5000);
    }

    async createDraftPreviews(draftId, files) {
        previewManager.revokePreviews(draftId);
        return previewManager.generatePreviews(files, draftId);
    }

    clearDraftPreviews(draftId) {
        previewManager.revokePreviews(draftId);
    }

    getUpload(uploadId) {
        return this.uploads.get(uploadId);
    }

    getAllUploads() {
        return Array.from(this.uploads.values());
    }

    getActiveUpload() {
        return this.getAllUploads().find((session) => {
            return ![UploadState.PUBLISHED, UploadState.FAILED, UploadState.CANCELLED].includes(session.state);
        }) || null;
    }

    getUploadState(uploadId) {
        const stateMachine = stateMachineFactory.peekMachine(uploadId);
        return stateMachine ? stateMachine.getState() : undefined;
    }

    getUploadProgress(uploadId) {
        const tracker = progressManager.getTracker(uploadId);
        return tracker ? tracker.getSummary() : undefined;
    }

    getOverallProgress() {
        return progressManager.getOverallProgress();
    }

    getQueueStatistics() {
        return uploadQueue.getStatistics();
    }
}

// One manager per window.
//
// Templates reach this file through the static template tag (which appends a
// content fingerprint) while other modules have imported it with their own
// version queries -- each URL used to get its own instance with its own
// empty session Map. base.html then initialized one instance while the post
// composer created uploads on another: the UPLOAD_ADDED handler could not
// find the session, returned silently, and the queue head never reached a
// terminal state, so every post hung at "Preparing…". Keying the singleton
// on window makes instance identity independent of the URL.
const UPLOAD_MANAGER_KEY = '__pwaninetUploadManager';
const uploadManagerRoot = typeof window !== 'undefined' ? window : globalThis;
export const uploadManager = uploadManagerRoot[UPLOAD_MANAGER_KEY]
    || (uploadManagerRoot[UPLOAD_MANAGER_KEY] = new UploadManager());

export default UploadManager;
