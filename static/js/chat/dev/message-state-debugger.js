/**
 * Message State Debugger - Developer Testing Utilities
 * Provides tools for simulating message states and testing state transitions
 * ONLY for development/testing - should not be used in production
 */

import { MESSAGE_STATE, isValidStateTransition } from '../shared/constants.js?v=20261106-group1';
import { store } from '../core/store.js?v=20261106-group1';
import { messageService } from '../core/message-service.js?v=20261106-group1';

export class MessageStateDebugger {
    constructor() {
        this.enabled = window.location.hostname === 'localhost' || 
                       window.location.hostname === '127.0.0.1' ||
                       window.location.search.includes('debug=true');
        
        this.debugPanel = null;
        this.simulatedDelays = new Map();
        
        if (this.enabled) {
            this._initDebugPanel();
            this._exposeGlobalAPI();
        }
    }

    /**
     * Initialize debug panel UI
     */
    _initDebugPanel() {
        // Create debug panel container
        this.debugPanel = document.createElement('div');
        this.debugPanel.id = 'message-state-debug-panel';
        this.debugPanel.style.cssText = `
            position: fixed;
            top: 10px;
            right: 10px;
            width: 320px;
            background: rgba(0, 0, 0, 0.9);
            color: #fff;
            padding: 15px;
            border-radius: 8px;
            font-family: monospace;
            font-size: 12px;
            z-index: 10000;
            box-shadow: 0 4px 12px rgba(0,0,0,0.5);
            display: none;
        `;

        this.debugPanel.innerHTML = `
            <div style="display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px;">
                <strong>Message State Debugger</strong>
                <button id="close-debug-panel" style="background: #444; color: #fff; border: none; padding: 2px 8px; cursor: pointer;">×</button>
            </div>
            <div style="margin-bottom: 10px;">
                <label>Simulate State:</label>
                <select id="debug-state-select" style="width: 100%; margin-top: 5px; padding: 5px;">
                    <option value="">Select state...</option>
                    <option value="${MESSAGE_STATE.DRAFT}">draft</option>
                    <option value="${MESSAGE_STATE.QUEUED}">queued</option>
                    <option value="${MESSAGE_STATE.PROCESSING}">processing</option>
                    <option value="${MESSAGE_STATE.UPLOADING}">uploading</option>
                    <option value="${MESSAGE_STATE.SENDING}">sending</option>
                    <option value="${MESSAGE_STATE.SENT}">sent</option>
                    <option value="${MESSAGE_STATE.DELIVERED}">delivered</option>
                    <option value="${MESSAGE_STATE.READ}">read</option>
                    <option value="${MESSAGE_STATE.FAILED_UPLOAD}">failed_upload</option>
                    <option value="${MESSAGE_STATE.FAILED_SEND}">failed_send</option>
                    <option value="${MESSAGE_STATE.RETRYING}">retrying</option>
                    <option value="${MESSAGE_STATE.CANCELLED}">cancelled</option>
                </select>
            </div>
            <div style="margin-bottom: 10px;">
                <label>Message ID:</label>
                <input type="text" id="debug-message-id" placeholder="Enter message ID..." style="width: 100%; margin-top: 5px; padding: 5px;">
            </div>
            <div style="margin-bottom: 10px;">
                <button id="apply-debug-state" style="width: 100%; padding: 8px; background: #007bff; color: #fff; border: none; cursor: pointer; margin-top: 5px;">Apply State</button>
            </div>
            <div style="margin-bottom: 10px;">
                <label>Network Simulation:</label>
                <div style="margin-top: 5px;">
                    <button id="simulate-offline" style="padding: 5px 10px; margin-right: 5px; cursor: pointer;">Go Offline</button>
                    <button id="simulate-online" style="padding: 5px 10px; cursor: pointer;">Go Online</button>
                </div>
            </div>
            <div style="margin-bottom: 10px;">
                <label>Upload Delay (ms):</label>
                <input type="number" id="upload-delay" value="2000" style="width: 100%; margin-top: 5px; padding: 5px;">
            </div>
            <div style="margin-bottom: 10px;">
                <button id="simulate-upload-failure" style="width: 100%; padding: 8px; background: #dc3545; color: #fff; border: none; cursor: pointer; margin-top: 5px;">Simulate Upload Failure</button>
            </div>
            <div style="margin-bottom: 10px;">
                <button id="simulate-websocket-timeout" style="width: 100%; padding: 8px; background: #ffc107; color: #000; border: none; cursor: pointer; margin-top: 5px;">Simulate WebSocket Timeout</button>
            </div>
            <div style="margin-bottom: 10px;">
                <button id="list-all-messages" style="width: 100%; padding: 8px; background: #28a745; color: #fff; border: none; cursor: pointer; margin-top: 5px;">List All Messages</button>
            </div>
            <div id="debug-output" style="margin-top: 10px; padding: 10px; background: #222; max-height: 200px; overflow-y: auto; font-size: 11px;"></div>
        `;

        document.body.appendChild(this.debugPanel);

        // Add event listeners
        document.getElementById('close-debug-panel').addEventListener('click', () => {
            this.debugPanel.style.display = 'none';
        });

        document.getElementById('apply-debug-state').addEventListener('click', () => {
            this._applyDebugState();
        });

        document.getElementById('simulate-offline').addEventListener('click', () => {
            this._simulateOffline();
        });

        document.getElementById('simulate-online').addEventListener('click', () => {
            this._simulateOnline();
        });

        document.getElementById('simulate-upload-failure').addEventListener('click', () => {
            this._simulateUploadFailure();
        });

        document.getElementById('simulate-websocket-timeout').addEventListener('click', () => {
            this._simulateWebSocketTimeout();
        });

        document.getElementById('list-all-messages').addEventListener('click', () => {
            this._listAllMessages();
        });

        // Toggle debug panel with keyboard shortcut (Ctrl+Shift+D)
        document.addEventListener('keydown', (e) => {
            if (e.ctrlKey && e.shiftKey && e.key === 'D') {
                e.preventDefault();
                this.debugPanel.style.display = 
                    this.debugPanel.style.display === 'none' ? 'block' : 'none';
            }
        });

        console.log('[DEBUG] Message State Debugger initialized. Press Ctrl+Shift+D to toggle panel.');
    }

    /**
     * Expose global API for console debugging
     */
    _exposeGlobalAPI() {
        window.__messageStateDebugger = {
            setState: (messageId, state) => this.simulateState(messageId, state),
            getState: (messageId) => this.getMessageState(messageId),
            listMessages: () => this._listAllMessages(),
            simulateOffline: () => this._simulateOffline(),
            simulateOnline: () => this._simulateOnline(),
            validateTransition: (from, to) => isValidStateTransition(from, to),
            getAllStates: () => MESSAGE_STATE,
            getQueueStats: () => messageService.getStatus()
        };

        console.log('[DEBUG] Global API exposed at window.__messageStateDebugger');
    }

    /**
     * Apply debug state from panel
     */
    _applyDebugState() {
        const state = document.getElementById('debug-state-select').value;
        const messageId = document.getElementById('debug-message-id').value;

        if (!state || !messageId) {
            this._log('Error: Please select a state and enter a message ID');
            return;
        }

        this.simulateState(messageId, state);
    }

    /**
     * Simulate a message state change
     * @param {string} messageId - Message ID
     * @param {string} newState - New state
     */
    simulateState(messageId, newState) {
        const message = store.getMessageById(messageId);
        
        if (!message) {
            this._log(`Error: Message ${messageId} not found`);
            return;
        }

        const oldState = message.status;
        
        // Validate transition
        if (!isValidStateTransition(oldState, newState)) {
            this._log(`Error: Invalid transition ${oldState} -> ${newState}`);
            this._log(`Valid transitions from ${oldState}:`, this._getValidTransitions(oldState));
            return;
        }

        // Apply state change
        store.updateMessage(messageId, { status: newState });
        this._log(`State changed: ${messageId} ${oldState} -> ${newState}`);
    }

    /**
     * Get message state
     * @param {string} messageId - Message ID
     * @returns {string|null} Message state
     */
    getMessageState(messageId) {
        const message = store.getMessageById(messageId);
        return message ? message.status : null;
    }

    /**
     * Get valid transitions for a state
     * @param {string} state - Current state
     * @returns {Array} Valid transitions
     */
    _getValidTransitions(state) {
        const transitions = {
            [MESSAGE_STATE.DRAFT]: [MESSAGE_STATE.QUEUED, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.QUEUED]: [MESSAGE_STATE.PROCESSING, MESSAGE_STATE.SENDING, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.PROCESSING]: [MESSAGE_STATE.UPLOADING, MESSAGE_STATE.SENDING, MESSAGE_STATE.FAILED_UPLOAD, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.UPLOADING]: [MESSAGE_STATE.SENDING, MESSAGE_STATE.FAILED_UPLOAD, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.SENDING]: [MESSAGE_STATE.SENT, MESSAGE_STATE.FAILED_SEND, MESSAGE_STATE.RETRYING],
            [MESSAGE_STATE.SENT]: [MESSAGE_STATE.DELIVERED, MESSAGE_STATE.READ, MESSAGE_STATE.FAILED_SEND],
            [MESSAGE_STATE.DELIVERED]: [MESSAGE_STATE.READ],
            [MESSAGE_STATE.READ]: [],
            [MESSAGE_STATE.FAILED_UPLOAD]: [MESSAGE_STATE.RETRYING, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.FAILED_SEND]: [MESSAGE_STATE.RETRYING, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.RETRYING]: [MESSAGE_STATE.SENDING, MESSAGE_STATE.FAILED_UPLOAD, MESSAGE_STATE.FAILED_SEND, MESSAGE_STATE.CANCELLED],
            [MESSAGE_STATE.CANCELLED]: []
        };
        return transitions[state] || [];
    }

    /**
     * Simulate offline mode
     */
    _simulateOffline() {
        window.dispatchEvent(new Event('offline'));
        this._log('Simulated offline mode');
    }

    /**
     * Simulate online mode
     */
    _simulateOnline() {
        window.dispatchEvent(new Event('online'));
        this._log('Simulated online mode');
    }

    /**
     * Simulate upload failure
     */
    _simulateUploadFailure() {
        const uploadDelay = parseInt(document.getElementById('upload-delay').value) || 2000;
        
        this._log(`Simulating upload failure in ${uploadDelay}ms...`);
        
        setTimeout(() => {
            // Find a message in uploading state and fail it
            const messages = store.getMessages();
            const uploadingMessage = messages.find(m => m.status === MESSAGE_STATE.UPLOADING);
            
            if (uploadingMessage) {
                store.updateMessage(uploadingMessage.id, { status: MESSAGE_STATE.FAILED_UPLOAD });
                this._log(`Upload failure simulated for message ${uploadingMessage.id}`);
            } else {
                this._log('No message in uploading state found');
            }
        }, uploadDelay);
    }

    /**
     * Simulate WebSocket timeout
     */
    _simulateWebSocketTimeout() {
        this._log('Simulating WebSocket timeout...');
        
        // Dispatch custom event for WebSocket timeout
        const event = new CustomEvent('websocket:timeout', {
            detail: { simulated: true }
        });
        window.dispatchEvent(event);
        
        this._log('WebSocket timeout event dispatched');
    }

    /**
     * List all messages with their states
     */
    _listAllMessages() {
        const messages = store.getMessages();
        const output = messages.map(m => ({
            id: m.id,
            status: m.status,
            content: m.content.substring(0, 30) + (m.content.length > 30 ? '...' : '')
        }));
        
        this._log('All messages:', output);
        console.table(output);
    }

    /**
     * Log to debug panel
     * @param {...any} args - Arguments to log
     */
    _log(...args) {
        const output = document.getElementById('debug-output');
        const timestamp = new Date().toLocaleTimeString();
        const message = `[${timestamp}] ${args.join(' ')}`;
        
        output.innerHTML += `<div>${message}</div>`;
        output.scrollTop = output.scrollHeight;
        
        console.log('[DEBUG]', ...args);
    }
}

// Auto-initialize in development
let debuggerInstance = null;

if (typeof window !== 'undefined') {
    // Wait for DOM to be ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            debuggerInstance = new MessageStateDebugger();
        });
    } else {
        debuggerInstance = new MessageStateDebugger();
    }
}

export { debuggerInstance };
