/**
 * CameraService - Camera capture logic
 * Pure service layer, no DOM manipulation
 */

import { EVENTS } from '../../shared/constants.js?v=20261106-group1';
import { eventBus } from '../../core/event-bus.js?v=20261106-group1';

export class CameraService {
  constructor() {
    this.stream = null;
    this.facingMode = 'environment';
  }

  /**
   * Initialize camera service
   */
  init() {
    this.setupEventListeners();
  }

  /**
   * Setup event listeners
   */
  setupEventListeners() {
    eventBus.on(EVENTS.CAMERA_OPEN, () => {
      this.openCamera();
    });

    eventBus.on(EVENTS.CAMERA_CLOSE, () => {
      this.closeCamera();
    });

    eventBus.on(EVENTS.CAMERA_CAPTURE, () => {
      this.capture();
    });
  }

  /**
   * Open camera
   */
  async openCamera() {
    try {
      this.stream = await navigator.mediaDevices.getUserMedia({
        video: { facingMode: this.facingMode },
      });
      eventBus.emit(EVENTS.CAMERA_OPEN, { stream: this.stream });
    } catch (error) {
      console.error('Camera error:', error);
      // Emit error for UI to handle fallback
      eventBus.emit(EVENTS.CAMERA_ERROR, error);
    }
  }

  /**
   * Close camera
   */
  closeCamera() {
    if (this.stream) {
      this.stream.getTracks().forEach(track => track.stop());
      this.stream = null;
    }
    eventBus.emit(EVENTS.CAMERA_CLOSE);
  }

  /**
   * Capture photo
   */
  capture() {
    if (!this.stream) return;

    // Emit capture request with stream
    // UI layer will handle the actual capture using the stream
    eventBus.emit(EVENTS.CAMERA_CAPTURE, { stream: this.stream });
  }

  /**
   * Switch camera
   */
  switchCamera() {
    this.facingMode = this.facingMode === 'environment' ? 'user' : 'environment';
    this.closeCamera();
    this.openCamera();
  }

  /**
   * Get current facing mode
   * @returns {string} Facing mode
   */
  getFacingMode() {
    return this.facingMode;
  }
}

// Create global instance
export const cameraService = new CameraService();
