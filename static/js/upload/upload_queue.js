/**
 * Upload Queue
 * 
 * Manages the upload queue with sequential processing.
 * Supports queue, cancel, retry, and remove failed upload operations.
 * Current upload behavior remains sequential (no parallel uploads yet).
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261011-16';
import { UploadState } from './upload_state_machine.js?v=20261011-16';

class UploadItem {
    constructor(id, data) {
        this.id = id;
        this.data = data;
        this.state = UploadState.IDLE;
        this.progress = 0;
        this.errors = [];
        this.timestamps = {
            created: Date.now(),
            started: null,
            completed: null,
            failed: null,
        };
        this.retryCount = 0;
        this.maxRetries = 5; // Increased to 5 retries for better resilience
    }

    updateProgress(value) {
        this.progress = Math.min(100, Math.max(0, value));
    }

    addError(error) {
        this.errors.push(error);
    }

    markStarted() {
        this.timestamps.started = Date.now();
    }

    markCompleted() {
        this.timestamps.completed = Date.now();
    }

    markFailed() {
        this.timestamps.failed = Date.now();
    }

    canRetry() {
        return this.retryCount < this.maxRetries;
    }

    incrementRetry() {
        this.retryCount++;
    }

    getDuration() {
        if (this.timestamps.completed) {
            return this.timestamps.completed - this.timestamps.started;
        }
        if (this.timestamps.failed) {
            return this.timestamps.failed - this.timestamps.started;
        }
        if (this.timestamps.started) {
            return Date.now() - this.timestamps.started;
        }
        return 0;
    }
}

class UploadQueue {
    constructor() {
        this.queue = [];
        this.currentUpload = null;
        this.isProcessing = false;
        this.isPaused = false;
        this.maxConcurrent = 1; // Sequential uploads only for now
    }

    /**
     * Add an upload to the queue
     * @param {object} data - Upload data
     * @returns {string} Upload ID
     */
    add(data, id = null) {
        const uploadId = id || this.generateId();
        const item = new UploadItem(uploadId, data);
        this.queue.push(item);

        uploadEvents.emit(UploadEventNames.UPLOAD_ADDED, {
            id: uploadId,
            data,
            queuePosition: this.queue.length,
        });

        // Start processing if not already processing
        if (!this.isProcessing) {
            this.processQueue();
        }

        return uploadId;
    }

    addSession(session) {
        return this.add(session, session.id);
    }

    /**
     * Remove an upload from the queue
     * @param {string} id - Upload ID
     * @returns {boolean}
     */
    remove(id) {
        const index = this.queue.findIndex(item => item.id === id);
        
        if (index === -1) {
            return false;
        }

        // Cannot remove currently processing upload
        if (this.currentUpload && this.currentUpload.id === id) {
            return false;
        }

        this.queue.splice(index, 1);
        
        uploadEvents.emit(UploadEventNames.UPLOAD_REMOVED, {
            id,
            queuePosition: index,
        });

        return true;
    }

    /**
     * Cancel an upload
     * @param {string} id - Upload ID
     * @returns {boolean}
     */
    cancel(id) {
        const item = this.queue.find(item => item.id === id);
        
        if (!item) {
            return false;
        }

        // If currently processing, mark as cancelled
        if (this.currentUpload && this.currentUpload.id === id) {
            item.state = UploadState.CANCELLED;
            item.markFailed();
            
            // Move to next upload
            this.currentUpload = null;
            this.processQueue();
            
            return true;
        }

        // Remove from queue if not processing
        return this.remove(id);
    }

    /**
     * Retry a failed upload
     * @param {string} id - Upload ID
     * @returns {boolean}
     */
    retry(id) {
        const item = this.queue.find(item => item.id === id);
        
        if (!item || !item.canRetry()) {
            return false;
        }

        item.incrementRetry();
        item.state = UploadState.QUEUED;
        item.progress = 0;
        item.errors = [];
        item.timestamps.started = null;
        item.timestamps.failed = null;

        // Move to end of queue
        this.queue = this.queue.filter(i => i.id !== id);
        this.queue.push(item);

        // Start processing if not already processing
        if (!this.isProcessing) {
            this.processQueue();
        }

        return true;
    }

    /**
     * Clear all uploads from queue
     */
    clear() {
        // Cannot clear if currently processing
        if (this.currentUpload) {
            return false;
        }

        this.queue = [];
        
        uploadEvents.emit(UploadEventNames.QUEUE_CLEARED, {});
        
        return true;
    }

    /**
     * Pause queue processing
     */
    pause() {
        this.isPaused = true;
        uploadEvents.emit(UploadEventNames.QUEUE_PAUSED, {});
    }

    /**
     * Resume queue processing
     */
    resume() {
        this.isPaused = false;
        uploadEvents.emit(UploadEventNames.QUEUE_RESUMED, {});
        
        if (!this.isProcessing) {
            this.processQueue();
        }
    }

    /**
     * Process the queue
     */
    async processQueue() {
        if (this.isProcessing || this.isPaused) {
            return;
        }

        this.isProcessing = true;

        while (this.queue.length > 0 && !this.isPaused) {
            const item = this.queue[0];

            // Skip items that already reached a terminal state without ever
            // becoming current: completed/cancelled ones, and failed ones --
            // an upload can fail while it is still waiting its turn (its
            // processUpload stages run immediately). Picking a FAILED item
            // back up here would set it to UPLOADING and wait for a
            // completion event that can never arrive, freezing the queue.
            if (item.state === UploadState.PUBLISHED ||
                item.state === UploadState.CANCELLED) {
                this.queue.shift();
                continue;
            }

            // For failed items, auto-retry if possible, otherwise remove
            if (item.state === UploadState.FAILED) {
                if (item.canRetry()) {
                    console.log(`[UploadQueue] Auto-retrying failed upload ${item.id} (attempt ${item.retryCount + 1}/${item.maxRetries})`);
                    item.incrementRetry();
                    item.state = UploadState.QUEUED;
                    item.progress = 0;
                    item.timestamps.started = null;
                    item.timestamps.failed = null;

                    // Move to end of queue to give other uploads a chance
                    this.queue.shift();
                    this.queue.push(item);

                    // Emit retry event
                    uploadEvents.emit(UploadEventNames.UPLOAD_RETRIED, {
                        id: item.id,
                        retryCount: item.retryCount,
                    });

                    continue;
                } else {
                    console.warn(`[UploadQueue] Removing failed upload ${item.id} - max retries (${item.maxRetries}) exceeded`);
                    this.queue.shift();
                    continue;
                }
            }

            // Process the upload
            this.currentUpload = item;
            item.state = UploadState.UPLOADING;
            item.markStarted();

            // The actual upload will be handled by UploadManager
            // This method just manages the queue state
            // UploadManager will call uploadCompleted or uploadFailed
            // when the upload is done

            // Wait for upload to complete (handled by UploadManager callbacks)
            await this.waitForUploadCompletion(item);

            this.currentUpload = null;
            this.queue.shift();
        }

        this.isProcessing = false;
    }

    /**
     * Wait for upload completion
     * @param {UploadItem} item - Upload item
     * @returns {Promise<void>}
     */
    waitForUploadCompletion(item) {
        return new Promise((resolve) => {
            const TIMEOUT_MS = 10 * 60 * 1000; // 10 minutes timeout for actual upload
            let isTimedOut = false;

            const timeoutHandle = setTimeout(() => {
                isTimedOut = true;
                item.state = UploadState.FAILED;
                item.addError(new Error(`Upload ${item.id} timed out during upload (10 minutes)`));
                item.markFailed();
                console.error('[UploadQueue] Upload timeout:', item.id);
                uploadEvents.emit(UploadEventNames.UPLOAD_FAILED, {
                    id: item.id,
                    session: null,
                    error: 'Upload timed out - please check your connection and retry',
                    canRetry: true,
                });
                resolve();
            }, TIMEOUT_MS);

            const checkCompletion = () => {
                if (isTimedOut) {
                    clearTimeout(timeoutHandle);
                    resolve();
                    return;
                }

                if (item.state === UploadState.PUBLISHED ||
                    item.state === UploadState.FAILED ||
                    item.state === UploadState.CANCELLED) {
                    clearTimeout(timeoutHandle);
                    resolve();
                } else {
                    setTimeout(checkCompletion, 100);
                }
            };
            checkCompletion();
        });
    }

    /**
     * Mark upload as completed
     * @param {string} id - Upload ID
     */
    uploadCompleted(id) {
        const item = this.queue.find(item => item.id === id);
        if (item) {
            item.state = UploadState.PUBLISHED;
            item.progress = 100;
            item.markCompleted();
        }
    }

    /**
     * Mark upload as failed
     * @param {string} id - Upload ID
     * @param {Error} error - Error that caused failure
     */
    uploadFailed(id, error) {
        const item = this.queue.find(item => item.id === id);
        if (item) {
            item.state = UploadState.FAILED;
            item.addError(error);
            item.markFailed();
        }
    }

    /**
     * Get upload by ID
     * @param {string} id - Upload ID
     * @returns {UploadItem|undefined}
     */
    getUpload(id) {
        return this.queue.find(item => item.id === id);
    }

    /**
     * Get all uploads
     * @returns {UploadItem[]}
     */
    getAllUploads() {
        return [...this.queue];
    }

    /**
     * Get pending uploads
     * @returns {UploadItem[]}
     */
    getPendingUploads() {
        return this.queue.filter(item => 
            item.state === UploadState.IDLE || 
            item.state === UploadState.QUEUED
        );
    }

    /**
     * Get failed uploads
     * @returns {UploadItem[]}
     */
    getFailedUploads() {
        return this.queue.filter(item => item.state === UploadState.FAILED);
    }

    /**
     * Get completed uploads
     * @returns {UploadItem[]}
     */
    getCompletedUploads() {
        return this.queue.filter(item => item.state === UploadState.PUBLISHED);
    }

    /**
     * Get queue statistics
     * @returns {object}
     */
    getStatistics() {
        return {
            total: this.queue.length,
            pending: this.getPendingUploads().length,
            failed: this.getFailedUploads().length,
            completed: this.getCompletedUploads().length,
            processing: this.currentUpload ? 1 : 0,
            isProcessing: this.isProcessing,
            isPaused: this.isPaused,
        };
    }

    /**
     * Generate unique upload ID
     * @returns {string}
     */
    generateId() {
        return `upload_${Date.now()}_${Math.random().toString(36).substr(2, 9)}`;
    }

    /**
     * Get current upload
     * @returns {UploadItem|null}
     */
    getCurrentUpload() {
        return this.currentUpload;
    }

    /**
     * Check if queue is empty
     * @returns {boolean}
     */
    isEmpty() {
        return this.queue.length === 0;
    }

    /**
     * Get estimated time remaining
     * @returns {number} Estimated milliseconds
     */
    getEstimatedTimeRemaining() {
        if (!this.currentUpload || this.queue.length === 0) {
            return 0;
        }

        // Calculate average upload time from completed uploads
        const completedUploads = this.getCompletedUploads();
        if (completedUploads.length === 0) {
            return 0;
        }

        const avgDuration = completedUploads.reduce((sum, item) => sum + item.getDuration(), 0) / completedUploads.length;
        const remainingUploads = this.getPendingUploads().length + (this.currentUpload ? 1 : 0);

        return avgDuration * remainingUploads;
    }
}

// Global upload queue instance
export const uploadQueue = new UploadQueue();

export {
    UploadQueue,
    UploadItem,
};

export default UploadQueue;
