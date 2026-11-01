/**
 * Upload Events System
 * 
 * Central event bus for upload platform communication.
 * All upload components communicate through this event system
 * to maintain loose coupling and enable reactive UI updates.
 */

class UploadEvents {
    constructor() {
        this.listeners = new Map();
    }

    /**
     * Subscribe to an event
     * @param {string} event - Event name
     * @param {Function} callback - Event handler
     * @returns {Function} Unsubscribe function
     */
    on(event, callback) {
        if (!this.listeners.has(event)) {
            this.listeners.set(event, []);
        }
        this.listeners.get(event).push(callback);
        
        // Return unsubscribe function
        return () => this.off(event, callback);
    }

    /**
     * Unsubscribe from an event
     * @param {string} event - Event name
     * @param {Function} callback - Event handler to remove
     */
    off(event, callback) {
        if (!this.listeners.has(event)) return;
        
        const callbacks = this.listeners.get(event);
        const index = callbacks.indexOf(callback);
        if (index > -1) {
            callbacks.splice(index, 1);
        }
        
        if (callbacks.length === 0) {
            this.listeners.delete(event);
        }
    }

    /**
     * Emit an event
     * @param {string} event - Event name
     * @param {*} data - Event data
     */
    emit(event, data) {
        if (!this.listeners.has(event)) return;
        
        const callbacks = this.listeners.get(event);
        callbacks.forEach(callback => {
            try {
                callback(data);
            } catch (error) {
                console.error(`Error in event handler for ${event}:`, error);
            }
        });
    }

    /**
     * Subscribe to an event once
     * @param {string} event - Event name
     * @param {Function} callback - Event handler
     * @returns {Function} Unsubscribe function
     */
    once(event, callback) {
        const wrappedCallback = (data) => {
            callback(data);
            this.off(event, wrappedCallback);
        };
        return this.on(event, wrappedCallback);
    }

    /**
     * Clear all listeners for an event or all events
     * @param {string} [event] - Optional event name
     */
    clear(event) {
        if (event) {
            this.listeners.delete(event);
        } else {
            this.listeners.clear();
        }
    }

    /**
     * Get listener count for an event
     * @param {string} event - Event name
     * @returns {number} Listener count
     */
    listenerCount(event) {
        return this.listeners.has(event) ? this.listeners.get(event).length : 0;
    }
}

// Event name constants
export const UploadEventNames = {
    UPLOAD_CREATED: 'upload:created',
    // Upload lifecycle events
    UPLOAD_ADDED: 'upload:added',
    UPLOAD_REMOVED: 'upload:removed',
    UPLOAD_STARTED: 'upload:started',
    UPLOAD_COMPLETED: 'upload:completed',
    UPLOAD_FAILED: 'upload:failed',
    UPLOAD_CANCELLED: 'upload:cancelled',
    UPLOAD_RETRIED: 'upload:retried',
    UPLOAD_PUBLISHED: 'upload:published',
    
    // State change events
    STATE_CHANGED: 'state:changed',
    
    // Progress events
    PROGRESS_UPDATED: 'progress:updated',
    VALIDATION_PROGRESS: 'progress:validation',
    COMPRESSION_PROGRESS: 'progress:compression',
    UPLOAD_PROGRESS: 'progress:upload',
    
    // Validation events
    VALIDATION_STARTED: 'validation:started',
    VALIDATION_COMPLETED: 'validation:completed',
    VALIDATION_FAILED: 'validation:failed',
    
    // Preview events
    PREVIEW_GENERATED: 'preview:generated',
    PREVIEW_FAILED: 'preview:failed',
    
    // Compression events
    COMPRESSION_STARTED: 'compression:started',
    COMPRESSION_COMPLETED: 'compression:completed',
    COMPRESSION_FAILED: 'compression:failed',
    COMPRESSION_SKIPPED: 'compression:skipped',
    
    // Queue events
    QUEUE_CLEARED: 'queue:cleared',
    QUEUE_PAUSED: 'queue:paused',
    QUEUE_RESUMED: 'queue:resumed',
    
    // Storage events
    STORAGE_QUOTA_EXCEEDED: 'storage:quota_exceeded',
    STORAGE_UPDATED: 'storage:updated',
    
    // Download events
    DOWNLOAD_STARTED: 'download:started',
    DOWNLOAD_COMPLETED: 'download:completed',
    DOWNLOAD_FAILED: 'download:failed',
    DOWNLOAD_DELETED: 'download:deleted',
    
    // Cache events
    CACHE_CLEARED: 'cache:cleared',
    
    // Draft events
    DRAFT_SAVED: 'draft:saved',
    DRAFT_RESTORED: 'draft:restored',
    DRAFT_CLEARED: 'draft:cleared',
    
    // Error events
    ERROR_OCCURRED: 'error:occurred',
};

// Global event bus instance.
//
// This module is reached under several different URLs: templates import it
// through the static template tag (which appends a content fingerprint),
// while the rest of static/js/upload/ imports it relatively with a pinned
// version query. A browser evaluates a module once *per URL*, so without a
// guard the page can end up with two or more buses -- the composer listens
// on one while uploadManager emits on another and every event silently
// disappears. Keying the singleton on window makes the bus identity
// independent of the URL it was first evaluated from.
const UPLOAD_EVENTS_KEY = '__pwaninetUploadEvents';
const uploadEventsRoot = typeof window !== 'undefined' ? window : globalThis;
export const uploadEvents = uploadEventsRoot[UPLOAD_EVENTS_KEY]
    || (uploadEventsRoot[UPLOAD_EVENTS_KEY] = new UploadEvents());

export default UploadEvents;
