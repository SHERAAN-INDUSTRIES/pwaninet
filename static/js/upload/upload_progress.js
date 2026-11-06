/**
 * Upload Progress
 * 
 * Tracks progress across all upload stages:
 * - validation
 * - preview generation
 * - compression
 * - upload progress
 * - server processing
 * - publication
 * 
 * Exposes progress events for UI to consume.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';

class ProgressTracker {
    constructor(uploadId) {
        this.uploadId = uploadId;
        this.stages = {
            validation: { current: 0, total: 0, completed: false },
            preview: { current: 0, total: 0, completed: false },
            compression: { current: 0, total: 0, completed: false },
            upload: { current: 0, total: 0, completed: false },
            serverProcessing: { current: 0, total: 0, completed: false },
            publication: { current: 0, total: 0, completed: false },
        };
        this.startTime = null;
        this.endTime = null;
        this.currentStage = null;
    }

    /**
     * Start tracking
     */
    start() {
        this.startTime = Date.now();
    }

    /**
     * Stop tracking
     */
    stop() {
        this.endTime = Date.now();
    }

    /**
     * Update validation progress
     * @param {number} current - Current item
     * @param {number} total - Total items
     */
    updateValidationProgress(current, total) {
        this.stages.validation.current = current;
        this.stages.validation.total = total;
        this.currentStage = 'validation';
        
        uploadEvents.emit(UploadEventNames.VALIDATION_PROGRESS, {
            uploadId: this.uploadId,
            current,
            total,
            percent: total > 0 ? (current / total) * 100 : 0,
        });
    }

    /**
     * Mark validation as complete
     */
    completeValidation() {
        this.stages.validation.completed = true;
        this.stages.validation.current = this.stages.validation.total;
    }

    /**
     * Update compression progress
     * @param {number} current - Current item
     * @param {number} total - Total items
     * @param {string} fileName - Current file name
     */
    updateCompressionProgress(current, total, fileName) {
        this.stages.compression.current = current;
        this.stages.compression.total = total;
        this.currentStage = 'compression';
        
        uploadEvents.emit(UploadEventNames.COMPRESSION_PROGRESS, {
            uploadId: this.uploadId,
            current,
            total,
            fileName,
            percent: total > 0 ? (current / total) * 100 : 0,
        });
    }

    /**
     * Mark compression as complete
     */
    completeCompression() {
        this.stages.compression.completed = true;
        this.stages.compression.current = this.stages.compression.total;
    }

    /**
     * Update upload progress
     * @param {number} loaded - Bytes uploaded
     * @param {number} total - Total bytes
     */
    updateUploadProgress(loaded, total) {
        this.stages.upload.current = loaded;
        this.stages.upload.total = total;
        this.currentStage = 'upload';
        
        uploadEvents.emit(UploadEventNames.UPLOAD_PROGRESS, {
            uploadId: this.uploadId,
            loaded,
            total,
            percent: total > 0 ? (loaded / total) * 100 : 0,
        });
    }

    /**
     * Mark upload as complete
     */
    completeUpload() {
        this.stages.upload.completed = true;
        this.stages.upload.current = this.stages.upload.total;
    }

    /**
     * Update server processing progress
     * @param {number} current - Current step
     * @param {number} total - Total steps
     * @param {string} status - Status message
     */
    updateServerProcessingProgress(current, total, status) {
        this.stages.serverProcessing.current = current;
        this.stages.serverProcessing.total = total;
        this.currentStage = 'serverProcessing';
        
        uploadEvents.emit(UploadEventNames.PROGRESS_UPDATED, {
            uploadId: this.uploadId,
            stage: 'serverProcessing',
            current,
            total,
            status,
            percent: total > 0 ? (current / total) * 100 : 0,
        });
    }

    /**
     * Mark server processing as complete
     */
    completeServerProcessing() {
        this.stages.serverProcessing.completed = true;
        this.stages.serverProcessing.current = this.stages.serverProcessing.total;
    }

    /**
     * Update publication progress
     * @param {number} percent - Percentage complete
     */
    updatePublicationProgress(percent) {
        this.stages.publication.current = percent;
        this.stages.publication.total = 100;
        this.currentStage = 'publication';
        
        uploadEvents.emit(UploadEventNames.PROGRESS_UPDATED, {
            uploadId: this.uploadId,
            stage: 'publication',
            current: percent,
            total: 100,
            percent,
        });
    }

    /**
     * Mark publication as complete
     */
    completePublication() {
        this.stages.publication.completed = true;
        this.stages.publication.current = 100;
        this.stop();
    }

    /**
     * Get overall progress percentage
     * @returns {number} Overall progress (0-100)
     */
    getOverallProgress() {
        const stages = Object.values(this.stages);
        const totalStages = stages.length;
        const completedStages = stages.filter(stage => stage.completed).length;
        
        // Calculate progress for current stage
        let currentStageProgress = 0;
        if (this.currentStage && this.stages[this.currentStage]) {
            const stage = this.stages[this.currentStage];
            if (stage.total > 0) {
                currentStageProgress = (stage.current / stage.total) * 100;
            }
        }
        
        // Overall progress = (completed stages * 100) + current stage progress
        const overall = ((completedStages * 100) + currentStageProgress) / totalStages;
        return Math.min(100, Math.max(0, overall));
    }

    /**
     * Get progress for a specific stage
     * @param {string} stage - Stage name
     * @returns {number} Progress percentage (0-100)
     */
    getStageProgress(stage) {
        const stageData = this.stages[stage];
        if (!stageData || stageData.total === 0) {
            return stageData.completed ? 100 : 0;
        }
        return (stageData.current / stageData.total) * 100;
    }

    /**
     * Get current stage name
     * @returns {string|null}
     */
    getCurrentStage() {
        return this.currentStage;
    }

    /**
     * Get elapsed time
     * @returns {number} Elapsed milliseconds
     */
    getElapsedTime() {
        if (!this.startTime) return 0;
        const endTime = this.endTime || Date.now();
        return endTime - this.startTime;
    }

    /**
     * Get estimated time remaining
     * @returns {number} Estimated milliseconds
     */
    getEstimatedTimeRemaining() {
        const elapsed = this.getElapsedTime();
        const progress = this.getOverallProgress();
        
        if (progress === 0 || elapsed === 0) {
            return 0;
        }
        
        const estimatedTotal = (elapsed / progress) * 100;
        return Math.max(0, estimatedTotal - elapsed);
    }

    /**
     * Get progress summary
     * @returns {object}
     */
    getSummary() {
        return {
            uploadId: this.uploadId,
            overallProgress: this.getOverallProgress(),
            currentStage: this.currentStage,
            stages: { ...this.stages },
            elapsedTime: this.getElapsedTime(),
            estimatedTimeRemaining: this.getEstimatedTimeRemaining(),
            isComplete: this.stages.publication.completed,
        };
    }

    /**
     * Reset tracker
     */
    reset() {
        this.stages = {
            validation: { current: 0, total: 0, completed: false },
            preview: { current: 0, total: 0, completed: false },
            compression: { current: 0, total: 0, completed: false },
            upload: { current: 0, total: 0, completed: false },
            serverProcessing: { current: 0, total: 0, completed: false },
            publication: { current: 0, total: 0, completed: false },
        };
        this.startTime = null;
        this.endTime = null;
        this.currentStage = null;
    }
}

class ProgressManager {
    constructor() {
        this.trackers = new Map();
    }

    /**
     * Create or get progress tracker for an upload
     * @param {string} uploadId - Upload ID
     * @returns {ProgressTracker}
     */
    getTracker(uploadId) {
        if (!this.trackers.has(uploadId)) {
            this.trackers.set(uploadId, new ProgressTracker(uploadId));
        }
        return this.trackers.get(uploadId);
    }

    /**
     * Remove progress tracker for an upload
     * @param {string} uploadId - Upload ID
     */
    removeTracker(uploadId) {
        this.trackers.delete(uploadId);
    }

    /**
     * Get all progress trackers
     * @returns {Map}
     */
    getAllTrackers() {
        return this.trackers;
    }

    /**
     * Get overall progress across all uploads
     * @returns {object}
     */
    getOverallProgress() {
        const trackers = Array.from(this.trackers.values());
        
        if (trackers.length === 0) {
            return {
                totalUploads: 0,
                overallProgress: 0,
                completedUploads: 0,
                currentStage: null,
            };
        }

        const totalProgress = trackers.reduce((sum, tracker) => sum + tracker.getOverallProgress(), 0);
        const overallProgress = totalProgress / trackers.length;
        const completedUploads = trackers.filter(t => t.stages.publication.completed).length;
        
        // Get current stage from first active upload
        const activeTracker = trackers.find(t => !t.stages.publication.completed && t.currentStage);
        const currentStage = activeTracker ? activeTracker.getCurrentStage() : null;

        return {
            totalUploads: trackers.length,
            overallProgress,
            completedUploads,
            currentStage,
        };
    }

    /**
     * Clear all trackers
     */
    clear() {
        this.trackers.clear();
    }
}

// Global progress manager instance
export const progressManager = new ProgressManager();

export {
    ProgressTracker,
    ProgressManager,
};

export default ProgressManager;
