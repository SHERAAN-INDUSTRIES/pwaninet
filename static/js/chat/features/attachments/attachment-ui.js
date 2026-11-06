/**
 * AttachmentUI - UI event handlers for attachment functionality
 * Connects UI elements to attachment service
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { attachmentService } from './attachment.service.js?v=20261106-group1';
import { cameraService } from '../camera/camera.service.js?v=20261106-group1';
import { voiceService } from '../voice/voice.service.js?v=20261106-group1';
import { voiceModalController } from '../voice/voice-modal-controller.js?v=20261106-group1';
import { mediaComposer } from '../composer/MediaComposer.js?v=20261106-group1';

export class AttachmentUI {
    constructor() {
        this.initialized = false;
        this.attachmentModal = null;
        this.overlay = null;
        this.fileInput = null;
        this.cameraInput = null;
        this.voiceRecordingPreview = null;
    }

    /**
     * Initialize attachment UI
     */
    init() {
        console.log('[ATTACHMENT_UI] Attachment UI initializing...');
        this._initializeElements();
        this._setupEventListeners();
        if (!this.serviceListenersSetup) {
            this._setupServiceListeners();
            this.serviceListenersSetup = true;
        }

        // Initialize voice modal controller
        voiceModalController.init();

        // Initialize media composer
        mediaComposer.init();

        this.initialized = true;
        console.log('[ATTACHMENT_UI] Attachment UI initialized');
    }

    /**
     * Initialize DOM elements
     */
    _initializeElements() {
        this.attachmentModal = document.getElementById('attachmentModal');
        this.overlay = document.getElementById('overlay');
        this.fileInput = document.getElementById('fileInput');
        this.cameraInput = document.getElementById('cameraInput');
        this.voiceRecordingPreview = document.getElementById('voiceRecordingPreview');
        this.inputArea = document.querySelector('.input-area');
        this.messageInput = document.getElementById('messageInput');
    }

    /**
     * Setup UI event listeners
     */
    _setupEventListeners() {
        if (this._listenersAttached) return;
        this._listenersAttached = true;

        // Attachment button click (delegated on document)
        document.addEventListener('click', (e) => {
            const attachBtn = e.target.closest('#attachBtn');
            if (attachBtn) {
                e.preventDefault();
                e.stopPropagation();
                this.showAttachmentModal();
                return;
            }

            const cameraBtn = e.target.closest('#cameraBtn');
            if (cameraBtn) {
                e.preventDefault();
                e.stopPropagation();
                this.handleCameraCapture();
                return;
            }

            const closeBtn = e.target.closest('#closeAttachmentModal, .pwanimate-attachment-modal-close');
            if (closeBtn) {
                e.preventDefault();
                e.stopPropagation();
                this.hideAttachmentModal();
                return;
            }

            const option = e.target.closest('.attachment-option');
            if (option) {
                e.preventDefault();
                e.stopPropagation();
                const type = option.dataset.type;
                this.handleAttachmentOption(type);
                return;
            }

            // Close when clicking directly on overlay/modal backdrop
            const modal = e.target.closest('.pwanimate-attachment-modal');
            if (modal && e.target === modal) {
                this.hideAttachmentModal();
                return;
            }
            if (e.target.id === 'overlay' && document.getElementById('attachmentModal')?.classList.contains('show')) {
                this.hideAttachmentModal();
                return;
            }
        });

        // Delegated file input changes
        document.addEventListener('change', (e) => {
            if (e.target && (e.target.id === 'fileInput' || e.target.id === 'cameraInput')) {
                if (e.target.files && e.target.files.length > 0) {
                    this.handleFileSelection(e.target.files);
                }
            }
        });
        // Drag and drop support for input area
        const inputArea = document.querySelector('.input-area');
        if (inputArea) {
            this._setupDragAndDrop();
        }
    }

    /**
     * Setup service event listeners
     */
    _setupServiceListeners() {
        eventBus.on(EVENTS.ATTACHMENT_SELECTED, (data) => {
            this.handleAttachmentSelected(data);
        });

        eventBus.on(EVENTS.ATTACHMENT_UPLOAD_SUCCESS, (data) => {
            this.handleUploadSuccess(data);
        });

        eventBus.on(EVENTS.ATTACHMENT_ERROR, (error) => {
            this.handleUploadError(error);
        });

        // Voice events are now handled by voiceModalController
        // Removed old voice event listeners

        eventBus.on(EVENTS.CAMERA_ERROR, (error) => {
            console.error('Camera error:', error);
            this.fallbackToFileSelection('image');
        });
    }

    /**
     * Show attachment modal
     */
    showAttachmentModal() {
        const modal = document.getElementById('attachmentModal') || this.attachmentModal;
        const overlay = document.getElementById('overlay') || this.overlay;
        if (modal) {
            modal.classList.add('show');
            if (overlay) overlay.classList.add('show');
        }
    }

    /**
     * Hide attachment modal
     */
    hideAttachmentModal() {
        const modal = document.getElementById('attachmentModal') || this.attachmentModal;
        const overlay = document.getElementById('overlay') || this.overlay;
        if (modal) modal.classList.remove('show');
        if (overlay) overlay.classList.remove('show');
    }

    /**
     * Handle attachment option selection
     * @param {string} type - Attachment type
     */
    handleAttachmentOption(type) {
        this.hideAttachmentModal();
        const fileInput = document.getElementById('fileInput') || this.fileInput;
        const cameraInput = document.getElementById('cameraInput') || this.cameraInput;

        switch (type) {
            case 'camera':
                if (cameraInput) {
                    cameraInput.click();
                } else if (fileInput) {
                    fileInput.accept = 'image/*';
                    fileInput.click();
                } else {
                    this.handleCameraCapture();
                }
                break;
            case 'photos':
            case 'gallery':
                this.triggerFileInput('image/*,video/*');
                break;
            case 'videos':
                this.triggerFileInput('video/*');
                break;
            case 'documents':
            case 'docs':
                this.triggerFileInput('.pdf,.doc,.docx,.txt,.xls,.xlsx,.ppt,.pptx,.zip');
                break;
            case 'audio':
                this.triggerFileInput('audio/*');
                break;
        }
    }

    /**
     * Trigger file input with specific accept type
     * @param {string} accept - File accept attribute
     */
    triggerFileInput(accept) {
        const fileInput = document.getElementById('fileInput') || this.fileInput;
        if (fileInput) {
            fileInput.accept = accept;
            fileInput.click();
        }
    }

    /**
     * Handle file selection
     * @param {FileList} files - Selected files
     */
    handleFileSelection(files) {
        if (files && files.length > 0) {
            // Get conversation ID from the page
            const conversationId = this._getConversationId();
            if (!conversationId) {
                console.error('No conversation ID found');
                return;
            }

            // Open media composer with selected files
            mediaComposer.open(files, conversationId);
        }
    }

    /**
     * Setup drag and drop functionality
     */
    _setupDragAndDrop() {
        const inputArea = this.inputArea;
        
        // Prevent default drag behaviors
        ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
            inputArea.addEventListener(eventName, (e) => {
                e.preventDefault();
                e.stopPropagation();
            }, false);
        });

        // Highlight drop zone
        ['dragenter', 'dragover'].forEach(eventName => {
            inputArea.addEventListener(eventName, () => {
                inputArea.classList.add('drag-over');
            }, false);
        });

        // Remove highlight
        ['dragleave', 'drop'].forEach(eventName => {
            inputArea.addEventListener(eventName, () => {
                inputArea.classList.remove('drag-over');
            }, false);
        });

        // Handle dropped files
        inputArea.addEventListener('drop', (e) => {
            const files = e.dataTransfer.files;
            if (files && files.length > 0) {
                this.handleFileSelection(files);
            }
        }, false);
    }

    /**
     * Handle camera capture
     */
    async handleCameraCapture() {
        try {
            // Try to use camera service first
            eventBus.emit(EVENTS.CAMERA_OPEN);
        } catch (error) {
            console.error('Camera capture failed:', error);
            // Fallback to file selection
            this.fallbackToFileSelection('image/*');
        }
    }

    /**
     * Fallback to file selection
     * @param {string} accept - File accept type
     */
    fallbackToFileSelection(accept) {
        if (this.cameraInput) {
            this.cameraInput.accept = accept;
            this.cameraInput.click();
        } else if (this.fileInput) {
            this.fileInput.accept = accept;
            this.fileInput.click();
        }
    }

    /**
     * Handle attachment selected event
     * @param {Object} data - Attachment data
     */
    handleAttachmentSelected(data) {
        console.log('Attachment selected:', data);
    }

    /**
     * Handle upload success
     * @param {Object} data - Upload response data
     */
    handleUploadSuccess(data) {
        console.log('Upload successful:', data);
        // The message will be automatically added to the chat via WebSocket
    }

    /**
     * Handle upload error
     * @param {Object} error - Error data
     */
    handleUploadError(error) {
        console.error('Upload error:', error);
        // Show error message to user
        const msg = error?.message || error?.error || error?.detail || (typeof error === 'string' ? error : 'Unknown error');
        alert('Failed to upload file: ' + msg);
    }

    /**
     * Get conversation ID from the page
     * @returns {number|null} Conversation ID
     */
    _getConversationId() {
        const chatContainer = document.querySelector('.chat-container');
        if (chatContainer) {
            return parseInt(chatContainer.dataset.conversationId);
        }
        return null;
    }

    /**
     * Destroy attachment UI
     */
    destroy() {
        // Clean up event listeners
        this.initialized = false;
        console.log('[ATTACHMENT_UI] Destroyed');
    }
}

// Create and export singleton instance
export const attachmentUI = new AttachmentUI();
