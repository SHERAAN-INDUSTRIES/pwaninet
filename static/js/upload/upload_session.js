import { UploadState } from './upload_state_machine.js?v=20261011-16';

class UploadSession {
    constructor({ id, files = [], caption = '', visibility = 'public', metadata = {} }) {
        this.id = id;
        this.files = [...files];
        this.caption = caption;
        this.visibility = visibility;
        this.metadata = { ...metadata };
        this.state = UploadState.IDLE;
        this.progress = {
            overall: 0,
            stage: null,
            currentFile: null,
            filesCompleted: 0,
            filesTotal: this.files.length,
            estimatedTimeRemaining: 0,
        };
        this.previewUrls = [];
        this.retryCount = 0;
        this.maxRetries = 5; // Increased to 5 retries for better resilience
        this.cancellationToken = { cancelled: false };
        this.createdAt = Date.now();
        this.updatedAt = this.createdAt;
        this.cleanupHandlers = [];
        this.tempObjectUrls = new Set();
    }

    setState(state) {
        this.state = state;
        this.updatedAt = Date.now();
    }

    setProgress(progress = {}) {
        this.progress = { ...this.progress, ...progress };
        this.updatedAt = Date.now();
    }

    addPreviewUrl(url) {
        if (url) {
            this.previewUrls.push(url);
            this.tempObjectUrls.add(url);
        }
    }

    addTempObjectUrl(url) {
        if (url) {
            this.tempObjectUrls.add(url);
        }
    }

    incrementRetry() {
        this.retryCount += 1;
        this.updatedAt = Date.now();
    }

    canRetry() {
        return this.retryCount < this.maxRetries;
    }

    cancel() {
        this.cancellationToken.cancelled = true;
        this.setState(UploadState.CANCELLED);
    }

    isCancelled() {
        return Boolean(this.cancellationToken.cancelled);
    }

    onCleanup(handler) {
        if (typeof handler === 'function') {
            this.cleanupHandlers.push(handler);
        }
    }

    cleanup() {
        this.cleanupHandlers.forEach((handler) => {
            try {
                handler(this);
            } catch (error) {
                console.error('UploadSession cleanup failed:', error);
            }
        });
        this.cleanupHandlers = [];
        this.previewUrls = [];
        this.tempObjectUrls.clear();
    }
}

export { UploadSession };
export default UploadSession;