/**
 * AttachmentService - File attachment logic
 * Pure service layer, no DOM manipulation
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';
import { isImageFile, isVideoFile, isAudioFile } from '../../shared/utils.js?v=20261106-group1';
import { messageService } from '../../core/message-service.js?v=20261106-group1';

export class AttachmentService {
  constructor() {
    this.selectedFile = null;
    
    // Unified message state machine (sync with message-service.js)
    this.MESSAGE_STATES = {
      DRAFT: 'draft',
      QUEUED: 'queued',
      SENDING: 'sending',
      SENT: 'sent',
      DELIVERED: 'delivered',
      READ: 'read',
      FAILED: 'failed',
      RETRYING: 'retrying'
    };
  }

  /**
   * Initialize attachment service
   */
  init() {
    this.setupEventListeners();
  }

  /**
   * Setup event listeners
   */
  setupEventListeners() {
    eventBus.on(EVENTS.ATTACHMENT_SELECTED, (file) => {
      this.handleFileSelection(file);
    });

    eventBus.on(EVENTS.ATTACHMENT_UPLOAD, (file) => {
      this.handleFileUpload(file);
    });

    eventBus.on(EVENTS.ATTACHMENT_MODAL_TOGGLE, (show) => {
      eventBus.emit(EVENTS.ATTACHMENT_MODAL_TOGGLE, show);
    });
  }

  /**
   * Handle file selection
   * @param {File} file - Selected file
   */
  handleFileSelection(file) {
    this.selectedFile = file;

    const fileType = this.getFileType(file);
    eventBus.emit(EVENTS.ATTACHMENT_SELECTED, {
      file,
      type: fileType,
    });
  }

  /**
   * Handle file upload
   * @param {File} file - File to upload
   * @param {number} conversationId - Conversation ID
   */
  async handleFileUpload(file, conversationId, options = {}) {
    // Validate file
    if (!this.validateFile(file)) {
      eventBus.emit(EVENTS.ATTACHMENT_ERROR, { message: 'Invalid file' });
      return;
    }

    if (!conversationId) {
      eventBus.emit(EVENTS.ATTACHMENT_ERROR, { message: 'Conversation ID is required' });
      return;
    }

    // Create form data
    const formData = new FormData();
    formData.append('file', file);
    formData.append('conversation_id', conversationId);
    if (options.isVoiceNote || file.name.startsWith('voice_')) {
      formData.append('is_voice_note', 'true');
    }
    if (options.tempId) {
      formData.append('temp_id', options.tempId);
    }
    if (options.replyToId) {
      formData.append('reply_to_id', options.replyToId);
    }

    const csrfToken = this.getCSRFToken();
    if (csrfToken) {
      formData.append('csrfmiddlewaretoken', csrfToken);
    }

    try {
      // Save sender file locally to IndexedDB/Filesystem
      if (file && options.tempId) {
        try {
          const { deviceMediaStore } = await import('../../core/device-media-store.js?v=20261106-group1');
          await deviceMediaStore.saveSenderMedia(options.tempId, file, file.name || 'attachment');
        } catch (_) {}
      }

      // Emit upload start event (QUEUED state)
      eventBus.emit(EVENTS.ATTACHMENT_UPLOAD_START, { file, status: this.MESSAGE_STATES.QUEUED });

      // Upload file to messaging app endpoint with real progress tracking via XMLHttpRequest
      const data = await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', '/messaging/api/attachments/upload/', true);
        if (csrfToken) {
          xhr.setRequestHeader('X-CSRFToken', csrfToken);
        }
        // Without this, ontimeout is unreachable and a hung request leaves
        // the voice composer spinning forever (await never settles).
        try { xhr.timeout = 300000; } catch (_) {}

        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const percent = Math.min(99, Math.round((e.loaded / e.total) * 100));
            eventBus.emit(EVENTS.ATTACHMENT_PROGRESS, {
              tempId: options.tempId,
              loaded: e.loaded,
              total: e.total,
              progress: percent
            });
            // Update UI directly if message exists in DOM
            if (options.tempId) {
              const msgEl = document.querySelector(`[data-message-id="${options.tempId}"]`);
              if (msgEl) {
                const vnTimer = msgEl.querySelector('.vn-timer');
                if (vnTimer) vnTimer.textContent = `${percent}%`;
                const audioTime = msgEl.querySelector('.audio-track-time');
                if (audioTime) audioTime.textContent = `${percent}%`;
                const docProgress = msgEl.querySelector('.document-progress-text');
                if (docProgress) docProgress.textContent = `${percent}%`;
              }
            }
          }
        };

        xhr.onload = () => {
          if (xhr.status >= 200 && xhr.status < 300) {
            try {
              const parsed = JSON.parse(xhr.responseText);
              resolve(parsed);
            } catch (err) {
              reject(new Error('Failed to parse server response'));
            }
          } else {
            let errorMsg = `Upload failed (${xhr.status})`;
            try {
              const errObj = JSON.parse(xhr.responseText);
              errorMsg = errObj.error || errObj.detail || errObj.message || errorMsg;
            } catch (_) {}
            reject(new Error(errorMsg));
          }
        };

        xhr.onerror = () => reject(new Error('Network error during upload'));
        xhr.ontimeout = () => reject(new Error('Upload request timed out'));

        xhr.send(formData);
      });

      if (data) {

        // Rekey local media store from tempId to permanent message id
        if (options.tempId && data?.id) {
          try {
            const { deviceMediaStore } = await import('../../core/device-media-store.js?v=20261106-group1');
            await deviceMediaStore.rekeyMedia(options.tempId, data.id);
          } catch (_) {}
        }

        eventBus.emit(EVENTS.ATTACHMENT_UPLOAD_SUCCESS, { ...data, status: this.MESSAGE_STATES.SENT });
        // Emit MESSAGE_UPLOAD_SUCCESS so MessageService immediately resolves optimistic message and updates store
        eventBus.emit(EVENTS.MESSAGE_UPLOAD_SUCCESS, {
          tempId: options.tempId || `temp_upload_${data.id || Date.now()}`,
          serverMessage: data
        });
      } else {
        let errorData = null;
        try {
          errorData = await response.json();
        } catch (_) {
          errorData = { error: `Server returned HTTP ${response.status}` };
        }
        console.error('[ATTACHMENT_UPLOAD] Server error response:', response.status, errorData);
        const errorMsg = (errorData && (errorData.error || errorData.detail || errorData.message)) ||
                         (typeof errorData === 'string' ? errorData : `Upload failed (${response.status})`);
        throw new Error(errorMsg);
      }
    } catch (error) {
      console.error('Attachment upload error:', error);
      if (options.tempId) {
        eventBus.emit(EVENTS.MESSAGE_UPLOAD_FAILED, {
          tempId: options.tempId,
          error: error.message || 'Upload failed'
        });
      }
      eventBus.emit(EVENTS.ATTACHMENT_ERROR, {
        message: error.message || 'Upload failed',
        error,
        status: this.MESSAGE_STATES.FAILED
      });
      throw error;
    }
  }

  /**
   * Get file type
   * @param {File} file - File object
   * @returns {string} File type
   */
  getFileType(file) {
    if (isImageFile(file.name)) return 'image';
    if (isVideoFile(file.name)) return 'video';
    if (isAudioFile(file.name)) return 'audio';
    return 'document';
  }

  /**
   * Validate file
   * @param {File} file - File to validate
   * @returns {boolean} Is valid
   */
  validateFile(file) {
    if (!file) return false;
    // Size limit: 100MB
    const maxSize = 100 * 1024 * 1024;
    if (file.size > maxSize) {
      return false;
    }
    return true;
  }

  /**
   * Get CSRF token
   * @returns {string} CSRF token
   */
  getCSRFToken() {
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
      if (name === 'csrftoken') return decodeURIComponent(value);
    }
    return '';
  }

  /**
   * Clear selected file
   */
  clearSelection() {
    this.selectedFile = null;
  }

  /**
   * Get selected file
   * @returns {File|null} Selected file
   */
  getSelectedFile() {
    return this.selectedFile;
  }
}

// Create global instance
export const attachmentService = new AttachmentService();
