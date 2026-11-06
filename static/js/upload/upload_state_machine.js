/**
 * Upload State Machine
 * 
 * Manages upload state transitions and ensures every upload
 * exists in exactly one state at any given time.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';

// Upload state constants
export const UploadState = {
    IDLE: 'IDLE',
    PREPARING: 'PREPARING',
    VALIDATING: 'VALIDATING',
    READING_MEDIA: 'READING_MEDIA',
    GENERATING_PREVIEW: 'GENERATING_PREVIEW',
    COMPRESSING_IMAGES: 'COMPRESSING_IMAGES',
    READY: 'READY',
    QUEUED: 'QUEUED',
    UPLOADING: 'UPLOADING',
    SERVER_PROCESSING: 'SERVER_PROCESSING',
    PUBLISHED: 'PUBLISHED',
    FAILED: 'FAILED',
    CANCELLED: 'CANCELLED',
};

// Valid state transitions
const STATE_TRANSITIONS = {
    [UploadState.IDLE]: [UploadState.PREPARING, UploadState.CANCELLED],
    [UploadState.PREPARING]: [UploadState.VALIDATING, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.VALIDATING]: [UploadState.READING_MEDIA, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.READING_MEDIA]: [UploadState.GENERATING_PREVIEW, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.GENERATING_PREVIEW]: [UploadState.COMPRESSING_IMAGES, UploadState.READY, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.COMPRESSING_IMAGES]: [UploadState.READY, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.READY]: [UploadState.QUEUED, UploadState.CANCELLED],
    [UploadState.QUEUED]: [UploadState.UPLOADING, UploadState.CANCELLED],
    [UploadState.UPLOADING]: [UploadState.SERVER_PROCESSING, UploadState.FAILED, UploadState.CANCELLED],
    // CANCELLED must stay reachable while transcoding: otherwise the banner
    // ✕ does nothing mid-transcode and the donut sticks with no way out.
    [UploadState.SERVER_PROCESSING]: [UploadState.PUBLISHED, UploadState.FAILED, UploadState.CANCELLED],
    [UploadState.PUBLISHED]: [], // Terminal state
    [UploadState.FAILED]: [UploadState.QUEUED], // Can retry from failed
    [UploadState.CANCELLED]: [], // Terminal state
};

// Terminal states (no further transitions)
const TERMINAL_STATES = [UploadState.PUBLISHED, UploadState.CANCELLED];

// Active states (upload is in progress)
const ACTIVE_STATES = [
    UploadState.PREPARING,
    UploadState.VALIDATING,
    UploadState.READING_MEDIA,
    UploadState.GENERATING_PREVIEW,
    UploadState.COMPRESSING_IMAGES,
    UploadState.QUEUED,
    UploadState.UPLOADING,
    UploadState.SERVER_PROCESSING,
];

class UploadStateMachine {
    constructor(uploadId, initialState = UploadState.IDLE) {
        this.uploadId = uploadId;
        this.currentState = initialState;
        this.stateHistory = [initialState];
        this.timestamp = Date.now();
    }

    /**
     * Get current state
     * @returns {string} Current state
     */
    getState() {
        return this.currentState;
    }

    /**
     * Check if transition is valid
     * @param {string} newState - Target state
     * @returns {boolean} Whether transition is valid
     */
    canTransitionTo(newState) {
        if (this.currentState === newState) return false;
        
        const validTransitions = STATE_TRANSITIONS[this.currentState] || [];
        return validTransitions.includes(newState);
    }

    /**
     * Transition to a new state
     * @param {string} newState - Target state
     * @param {object} metadata - Optional metadata about the transition
     * @returns {boolean} Whether transition succeeded
     */
    transitionTo(newState, metadata = {}) {
        if (!this.canTransitionTo(newState)) {
            console.error(`Invalid state transition from ${this.currentState} to ${newState}`);
            return false;
        }

        const previousState = this.currentState;
        this.currentState = newState;
        this.stateHistory.push(newState);
        this.timestamp = Date.now();

        // Emit state change event
        uploadEvents.emit(UploadEventNames.STATE_CHANGED, {
            uploadId: this.uploadId,
            previousState,
            newState,
            timestamp: this.timestamp,
            metadata,
        });

        return true;
    }

    /**
     * Check if current state is terminal
     * @returns {boolean}
     */
    isTerminal() {
        return TERMINAL_STATES.includes(this.currentState);
    }

    /**
     * Check if current state is active (in progress)
     * @returns {boolean}
     */
    isActive() {
        return ACTIVE_STATES.includes(this.currentState);
    }

    /**
     * Check if upload can be cancelled
     * @returns {boolean}
     */
    canCancel() {
        return this.canTransitionTo(UploadState.CANCELLED);
    }

    /**
     * Check if upload can be retried
     * @returns {boolean}
     */
    canRetry() {
        return this.currentState === UploadState.FAILED;
    }

    /**
     * Get state history
     * @returns {Array} State history
     */
    getHistory() {
        return [...this.stateHistory];
    }

    /**
     * Get time spent in current state
     * @returns {number} Milliseconds in current state
     */
    getTimeInCurrentState() {
        return Date.now() - this.timestamp;
    }

    /**
     * Reset state machine to initial state
     */
    reset() {
        this.currentState = UploadState.IDLE;
        this.stateHistory = [UploadState.IDLE];
        this.timestamp = Date.now();
    }
}

/**
 * State machine factory for managing multiple upload state machines
 */
class StateMachineFactory {
    constructor() {
        this.machines = new Map();
    }

    /**
     * Create or get state machine for an upload
     * @param {string} uploadId - Upload ID
     * @param {string} initialState - Initial state
     * @returns {UploadStateMachine}
     */
    getMachine(uploadId, initialState = UploadState.IDLE) {
        if (!this.machines.has(uploadId)) {
            this.machines.set(uploadId, new UploadStateMachine(uploadId, initialState));
        }
        return this.machines.get(uploadId);
    }

    /**
     * Remove state machine for an upload
     * @param {string} uploadId - Upload ID
     */
    removeMachine(uploadId) {
        this.machines.delete(uploadId);
    }

    /**
     * Get state machine for an upload without creating
     * @param {string} uploadId - Upload ID
     * @returns {UploadStateMachine|undefined}
     */
    peekMachine(uploadId) {
        return this.machines.get(uploadId);
    }

    /**
     * Get all state machines
     * @returns {Map} All state machines
     */
    getAllMachines() {
        return this.machines;
    }

    /**
     * Get uploads in a specific state
     * @param {string} state - State to filter by
     * @returns {Array} Upload IDs in state
     */
    getUploadsInState(state) {
        const uploads = [];
        this.machines.forEach((machine, uploadId) => {
            if (machine.getState() === state) {
                uploads.push(uploadId);
            }
        });
        return uploads;
    }

    /**
     * Get all active uploads
     * @returns {Array} Upload IDs that are active
     */
    getActiveUploads() {
        const activeUploads = [];
        this.machines.forEach((machine, uploadId) => {
            if (machine.isActive()) {
                activeUploads.push(uploadId);
            }
        });
        return activeUploads;
    }

    /**
     * Clear all state machines
     */
    clear() {
        this.machines.clear();
    }
}

// Global state machine factory instance
export const stateMachineFactory = new StateMachineFactory();

export default UploadStateMachine;
