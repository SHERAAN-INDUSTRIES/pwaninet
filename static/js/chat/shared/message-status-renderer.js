/**
 * Message Status Renderer
 * Centralized component for rendering message status indicators
 * Single source of truth for all status UI rendering
 */

import { MESSAGE_STATE, SHOW_QUEUE_ICON_AFTER_MS } from './constants.js?v=20261106-group1';
import { networkHealthTracker } from './network-health-tracker.js?v=20261106-group1';

export class MessageStatusRenderer {
    /**
     * Get status icon HTML for a given message state
     * @param {string} state - Message state from MESSAGE_STATE enum
     * @param {string} messageId - Message ID for retry button (optional)
     * @param {string} avatarUrl - Avatar URL for read state (optional)
     * @param {Object} context - Additional context for rendering decisions
     * @param {number} context.queuedAt - Timestamp when message was queued (optional)
     * @param {boolean} context.forceShowQueued - Force show queued icon regardless of network (optional)
     * @returns {string} Icon HTML
     */
    static getStatusIcon(state, messageId = null, avatarUrl = null, context = {}) {
        const retryButton = messageId
            ? `<button class="retry-button" data-message-id="${messageId}" title="Retry">&#8635;</button>`
            : '';

        switch (state) {
            case MESSAGE_STATE.DRAFT:
                return this._getClockIcon();

            case MESSAGE_STATE.QUEUED:
                // Check if queued icon should be visible based on duration and network health
                if (!this._shouldShowQueuedIcon(context)) {
                    // Hide queued icon for fast transitions on healthy network
                    // Return empty string or a minimal placeholder
                    return '';
                }
                return this._getClockIcon();

            case MESSAGE_STATE.PROCESSING:
                return this._getClockIcon();

            case MESSAGE_STATE.UPLOADING:
                return this._getUploadingIcon();

            case MESSAGE_STATE.SENDING:
                return this._getClockIcon();

            case MESSAGE_STATE.SENT:
                return this._getSingleCheckIcon();

            case MESSAGE_STATE.DELIVERED:
                return this._getDoubleCheckIcon('grey');

            case MESSAGE_STATE.READ:
                return this._getDoubleCheckIcon('blue');

            case MESSAGE_STATE.FAILED_UPLOAD:
                return this._getErrorIcon(retryButton);

            case MESSAGE_STATE.FAILED_SEND:
                return this._getErrorIcon(retryButton);

            case MESSAGE_STATE.RETRYING:
                return this._getSpinnerIcon();

            case MESSAGE_STATE.CANCELLED:
                return this._getCancelledIcon();

            default:
                return this._getSingleCheckIcon('grey');
        }
    }

    /**
     * Get status description for accessibility
     * @param {string} state - Message state
     * @returns {string} Status description
     */
    static getStatusDescription(state) {
        const descriptions = {
            [MESSAGE_STATE.DRAFT]: 'Draft - not yet sent',
            [MESSAGE_STATE.QUEUED]: 'Queued - waiting to send',
            [MESSAGE_STATE.PROCESSING]: 'Processing - preparing to send',
            [MESSAGE_STATE.UPLOADING]: 'Uploading - uploading attachment',
            [MESSAGE_STATE.SENDING]: 'Sending - message being sent',
            [MESSAGE_STATE.SENT]: 'Sent - delivered to server',
            [MESSAGE_STATE.DELIVERED]: 'Delivered - received by recipient',
            [MESSAGE_STATE.READ]: 'Read - opened by recipient',
            [MESSAGE_STATE.FAILED_UPLOAD]: 'Upload failed - tap to retry',
            [MESSAGE_STATE.FAILED_SEND]: 'Send failed - tap to retry',
            [MESSAGE_STATE.RETRYING]: 'Retrying - attempting to resend',
            [MESSAGE_STATE.CANCELLED]: 'Cancelled - message not sent'
        };

        return descriptions[state] || 'Unknown status';
    }

    /**
     * Check if state is a failure state
     * @param {string} state - Message state
     * @returns {boolean} True if failure state
     */
    static isFailureState(state) {
        return state === MESSAGE_STATE.FAILED_UPLOAD || 
               state === MESSAGE_STATE.FAILED_SEND;
    }

    /**
     * Check if state is a retryable failure
     * @param {string} state - Message state
     * @returns {boolean} True if retryable
     */
    static isRetryable(state) {
        return this.isFailureState(state);
    }

    /**
     * Check if state shows progress indicator
     * @param {string} state - Message state
     * @returns {boolean} True if showing progress
     */
    static isProgressState(state) {
        return [
            MESSAGE_STATE.DRAFT,
            MESSAGE_STATE.QUEUED,
            MESSAGE_STATE.PROCESSING,
            MESSAGE_STATE.UPLOADING,
            MESSAGE_STATE.SENDING,
            MESSAGE_STATE.RETRYING
        ].includes(state);
    }

    /**
     * Check if state is terminal (no further transitions)
     * @param {string} state - Message state
     * @returns {boolean} True if terminal
     */
    static isTerminalState(state) {
        return state === MESSAGE_STATE.READ || 
               state === MESSAGE_STATE.CANCELLED;
    }

    // === Private icon helpers ===

    static _getClockIcon() {
        // Thin circular clock icon - clean, minimal, open-feeling
        return `<svg class="check-circle pending" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#9CA3AF" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-label="Queued">
            <circle cx="12" cy="12" r="10"></circle>
            <polyline points="12 6 12 12 16 14"></polyline>
        </svg>`;
    }

    static _getUploadingIcon() {
        // Use same clock icon for uploading state
        return this._getClockIcon();
    }

    static _getSingleCheckIcon() {
        return `<svg class="check-marks sent" width="13" height="11" viewBox="0 0 13 11" fill="none" style="vertical-align: middle;">
            <path d="M1.5 5.5L4.5 8.5L11.5 1.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>`;
    }

    static _getDoubleCheckIcon(color = 'grey') {
        const label = color === 'blue' ? 'Read' : 'Delivered';
        const className = color === 'blue' ? 'check-marks read' : 'check-marks delivered';
        return `<svg class="${className}" width="17" height="11" viewBox="0 0 17 11" fill="none" aria-label="${label}" style="vertical-align: middle;">
            <path d="M1 5.5L4 8.5L11 1.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
            <path d="M5.5 5.5L8.5 8.5L15.5 1.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>`;
    }

    static _getErrorIcon(retryButton) {
        // Red X inside red circle - clean, minimal
        return `<svg class="check-circle failed" width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#EF4444" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round" aria-label="Failed">
            <circle cx="12" cy="12" r="10"></circle>
            <path d="M15 9l-6 6"></path>
            <path d="M9 9l6 6"></path>
        </svg>${retryButton}`;
    }

    static _getSpinnerIcon() {
        return '<span class="check-circle retrying" aria-label="Retrying"><span class="spinner"></span></span>';
    }

    static _getCancelledIcon() {
        return '<span class="check-circle cancelled" aria-label="Cancelled">&#10005;</span>';
    }

    static _getReadAvatar(avatarUrl) {
        // Read state is avatar-only, no checkmarks
        if (!avatarUrl) {
            return `<span class="check-circle read-avatar" aria-label="Read">
                <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="#2196F3" stroke-width="2">
                    <circle cx="12" cy="12" r="10"></circle>
                    <path d="M12 8v4l3 3"></path>
                </svg>
            </span>`;
        }
        return `<span class="check-circle read-avatar" aria-label="Read">
            <img src="${avatarUrl}" alt="Read" width="18" height="18" style="border-radius: 50%;">
        </span>`;
    }

    /**
     * Determine if queued icon should be visible based on duration and network health
     * @param {Object} context - Context object with queuedAt timestamp
     * @returns {boolean} True if queued icon should be visible
     */
    static _shouldShowQueuedIcon(context) {
        const { queuedAt, forceShowQueued } = context;

        // If force show is set, always show queued icon
        if (forceShowQueued) {
            return true;
        }

        // If network is unhealthy, always show queued icon
        if (!networkHealthTracker.isNetworkHealthy()) {
            return true;
        }

        // If no queuedAt timestamp, show queued icon (fallback)
        if (!queuedAt) {
            return true;
        }

        // Calculate queued duration
        const queuedDuration = Date.now() - queuedAt;

        // Only show queued icon if duration exceeds threshold
        return queuedDuration >= SHOW_QUEUE_ICON_AFTER_MS;
    }
}

/**
 * Convenience function for direct usage
 * @param {string} state - Message state
 * @param {string} messageId - Message ID (optional)
 * @param {string} avatarUrl - Avatar URL for read state (optional)
 * @param {Object} context - Additional context for rendering decisions (optional)
 * @returns {string} Icon HTML
 */
export function renderMessageStatus(state, messageId = null, avatarUrl = null, context = {}) {
    return MessageStatusRenderer.getStatusIcon(state, messageId, avatarUrl, context);
}
