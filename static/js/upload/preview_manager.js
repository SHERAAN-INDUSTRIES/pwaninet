/**
 * Preview Manager
 * 
 * Generates and manages media previews.
 * Prefers URL.createObjectURL() over FileReader for better performance.
 * Supports multiple images, videos, mixed media, and documents.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';

class PreviewResult {
    constructor(file, url, type, metadata = {}) {
        this.file = file;
        this.url = url;
        this.type = type;
        this.metadata = metadata;
        this.objectUrl = null;
    }
}

class PreviewManager {
    constructor() {
        this.previews = new Map(); // uploadId -> PreviewResult[]
        this.objectUrls = new Set(); // Track all created object URLs for cleanup
    }

    createObjectUrl(file) {
        const url = URL.createObjectURL(file);
        this.objectUrls.add(url);
        return url;
    }

    revokeObjectUrl(url) {
        if (!url) return;
        URL.revokeObjectURL(url);
        this.objectUrls.delete(url);
    }

    /**
     * Generate preview for a single file
     * @param {File} file - File to generate preview for
     * @returns {Promise<PreviewResult>}
     */
    async generatePreview(file) {
        const fileType = file.type || '';
        const fileName = file.name || '';

        const isImage = fileType.startsWith('image/') || /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(fileName);
        const isVideo = fileType.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi|3gp)$/i.test(fileName);
        const isDoc   = fileType === 'application/pdf' || /\.pdf$/i.test(fileName);
        const isAudio = fileType.startsWith('audio/') || /\.(mp3|wav|ogg|m4a|aac)$/i.test(fileName);

        try {
            if (isImage) {
                return await this.generateImagePreview(file);
            } else if (isVideo) {
                return await this.generateVideoPreview(file);
            } else if (isDoc) {
                return await this.generateDocumentPreview(file);
            } else if (isAudio) {
                return await this.generateAudioPreview(file);
            } else {
                return await this.generateGenericPreview(file);
            }
        } catch (error) {
            console.error('Preview generation failed:', error);
            uploadEvents.emit(UploadEventNames.PREVIEW_FAILED, {
                file,
                error: error.message,
            });
            throw error;
        }
    }

    /**
     * Generate image preview using URL.createObjectURL
     * @param {File} file - Image file
     * @returns {Promise<PreviewResult>}
     */
    async generateImagePreview(file) {
        const url = this.createObjectUrl(file);

        // Load image to get dimensions safely
        let dimensions = { width: 0, height: 0 };
        try {
            dimensions = await this.getImageDimensions(file);
        } catch (err) {
            console.warn('[preview_manager] Could not get image dimensions, falling back to defaults:', err);
        }

        const preview = new PreviewResult(file, url, 'image', {
            width: dimensions.width,
            height: dimensions.height,
        });

        preview.objectUrl = url;
        uploadEvents.emit(UploadEventNames.PREVIEW_GENERATED, {
            file,
            url,
            type: 'image',
            metadata: dimensions,
        });

        return preview;
    }

    /**
     * Generate video preview using URL.createObjectURL
     * @param {File} file - Video file
     * @returns {Promise<PreviewResult>}
     */
    async generateVideoPreview(file) {
        const url = this.createObjectUrl(file);

        // Load video to get duration and dimensions
        const metadata = await this.getVideoMetadata(file);

        const preview = new PreviewResult(file, url, 'video', {
            duration: metadata.duration,
            width: metadata.width,
            height: metadata.height,
            posterBlob: metadata.posterBlob || null,
        });

        preview.objectUrl = url;
        uploadEvents.emit(UploadEventNames.PREVIEW_GENERATED, {
            file,
            url,
            type: 'video',
            metadata,
        });

        return preview;
    }

    /**
     * Generate document preview (PDF)
     * @param {File} file - Document file
     * @returns {Promise<PreviewResult>}
     */
    async generateDocumentPreview(file) {
        // For PDFs, we use a generic icon or first page if supported
        // For now, use a generic preview
        const url = this.getDocumentIconUrl();
        
        const preview = new PreviewResult(file, url, 'document', {
            name: file.name,
            size: file.size,
        });

        uploadEvents.emit(UploadEventNames.PREVIEW_GENERATED, {
            file,
            url,
            type: 'document',
            metadata: { name: file.name, size: file.size },
        });

        return preview;
    }

    /**
     * Generate audio preview
     * @param {File} file - Audio file
     * @returns {Promise<PreviewResult>}
     */
    async generateAudioPreview(file) {
        const url = this.createObjectUrl(file);

        // Load audio to get duration
        const duration = await this.getAudioDuration(file);

        const preview = new PreviewResult(file, url, 'audio', {
            duration,
        });

        preview.objectUrl = url;
        uploadEvents.emit(UploadEventNames.PREVIEW_GENERATED, {
            file,
            url,
            type: 'audio',
            metadata: { duration },
        });

        return preview;
    }

    /**
     * Generate generic preview for unsupported types
     * @param {File} file - File
     * @returns {Promise<PreviewResult>}
     */
    async generateGenericPreview(file) {
        const url = this.getGenericIconUrl();
        
        const preview = new PreviewResult(file, url, 'generic', {
            name: file.name,
            size: file.size,
        });

        uploadEvents.emit(UploadEventNames.PREVIEW_GENERATED, {
            file,
            url,
            type: 'generic',
            metadata: { name: file.name, size: file.size },
        });

        return preview;
    }

    /**
     * Generate previews for multiple files
     * @param {File[]} files - Files to generate previews for
     * @param {string} uploadId - Upload ID to associate previews with
     * @returns {Promise<PreviewResult[]>}
     */
    async generatePreviews(files, uploadId) {
        const previews = [];
        const total = files.length;

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            
            try {
                const preview = await this.generatePreview(file);
                previews.push(preview);
            } catch (error) {
                // Continue with other files if one fails
                console.error(`Failed to generate preview for ${file.name}:`, error);
            }
        }

        // Store previews for this upload
        this.previews.set(uploadId, previews);

        return previews;
    }

    /**
     * Get previews for an upload
     * @param {string} uploadId - Upload ID
     * @returns {PreviewResult[]}
     */
    getPreviews(uploadId) {
        return this.previews.get(uploadId) || [];
    }

    /**
     * Get image dimensions
     * @param {File} file - Image file
     * @returns {Promise<{width: number, height: number}>}
     */
    getImageDimensions(file) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = this.createObjectUrl(file);

            img.onload = () => {
                this.revokeObjectUrl(url);
                resolve({ width: img.width, height: img.height });
            };

            img.onerror = () => {
                this.revokeObjectUrl(url);
                reject(new Error('Failed to load image'));
            };

            img.src = url;
        });
    }

    /**
     * Get video metadata including dimensions and optional poster frame blob
     * @param {File} file - Video file
     * @returns {Promise<{duration: number, width: number, height: number, posterBlob: Blob|null}>}
     */
    getVideoMetadata(file) {
        return new Promise((resolve) => {
            const video = document.createElement('video');
            video.preload = 'auto';
            video.muted = true;
            video.playsInline = true;
            const url = this.createObjectUrl(file);

            let isResolved = false;
            const finalize = (width, height, duration, posterBlob = null) => {
                if (isResolved) return;
                isResolved = true;
                this.revokeObjectUrl(url);
                resolve({
                    duration: duration || 0,
                    width: width || 0,
                    height: height || 0,
                    posterBlob,
                });
            };

            const capturePoster = () => {
                const width = video.videoWidth || 0;
                const height = video.videoHeight || 0;
                const duration = video.duration || 0;
                if (!width || !height) {
                    finalize(width, height, duration, null);
                    return;
                }
                try {
                    const canvas = document.createElement('canvas');
                    const targetWidth = Math.min(width, 720);
                    const targetHeight = Math.round(targetWidth * (height / width));
                    canvas.width = targetWidth;
                    canvas.height = targetHeight;
                    const ctx = canvas.getContext('2d');
                    ctx.drawImage(video, 0, 0, targetWidth, targetHeight);
                    canvas.toBlob((blob) => {
                        finalize(width, height, duration, blob);
                    }, 'image/jpeg', 0.85);
                } catch (e) {
                    finalize(width, height, duration, null);
                }
            };

            video.onloadedmetadata = () => {
                // Seek slightly to grab a clear frame if possible
                const seekTime = (video.duration && video.duration > 1) ? 0.2 : 0.0;
                video.onseeked = () => capturePoster();
                const fallbackTimeout = setTimeout(() => capturePoster(), 500);
                try {
                    video.currentTime = seekTime;
                } catch (e) {
                    clearTimeout(fallbackTimeout);
                    capturePoster();
                }
            };

            video.onerror = () => {
                finalize(0, 0, 0, null);
            };

            video.src = url;
        });
    }

    /**
     * Get audio duration
     * @param {File} file - Audio file
     * @returns {Promise<number>}
     */
    getAudioDuration(file) {
        return new Promise((resolve, reject) => {
            const audio = new Audio();
            const url = this.createObjectUrl(file);

            audio.onloadedmetadata = () => {
                this.revokeObjectUrl(url);
                resolve(audio.duration);
            };

            audio.onerror = () => {
                this.revokeObjectUrl(url);
                reject(new Error('Failed to load audio'));
            };

            audio.src = url;
        });
    }

    /**
     * Get document icon URL
     * @returns {string} Icon URL
     */
    getDocumentIconUrl() {
        // Return a generic PDF icon
        return '/static/images/icons/pdf-icon.svg';
    }

    /**
     * Get generic icon URL
     * @returns {string} Icon URL
     */
    getGenericIconUrl() {
        // Return a generic file icon
        return '/static/images/icons/file-icon.svg';
    }

    /**
     * Revoke object URL for a preview
     * @param {PreviewResult} preview - Preview to revoke
     */
    revokePreview(preview) {
        if (preview.objectUrl) {
            this.revokeObjectUrl(preview.objectUrl);
            preview.objectUrl = null;
        }
    }

    /**
     * Revoke all previews for an upload
     * @param {string} uploadId - Upload ID
     */
    revokePreviews(uploadId) {
        const previews = this.previews.get(uploadId);
        if (previews) {
            previews.forEach(preview => this.revokePreview(preview));
            this.previews.delete(uploadId);
        }
    }

    /**
     * Revoke all object URLs
     */
    revokeAll() {
        this.objectUrls.forEach(url => URL.revokeObjectURL(url));
        this.objectUrls.clear();
        this.previews.clear();
    }

    /**
     * Format duration for display
     * @param {number} seconds - Duration in seconds
     * @returns {string} Formatted duration (MM:SS or HH:MM:SS)
     */
    formatDuration(seconds) {
        if (!seconds || isNaN(seconds)) return '0:00';
        
        const hours = Math.floor(seconds / 3600);
        const minutes = Math.floor((seconds % 3600) / 60);
        const secs = Math.floor(seconds % 60);

        if (hours > 0) {
            return `${hours}:${minutes.toString().padStart(2, '0')}:${secs.toString().padStart(2, '0')}`;
        }
        return `${minutes}:${secs.toString().padStart(2, '0')}`;
    }

    /**
     * Format file size for display
     * @param {number} bytes - Size in bytes
     * @returns {string} Formatted size
     */
    formatSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }
}

// Global preview manager instance
export const previewManager = new PreviewManager();

export {
    PreviewManager,
    PreviewResult,
};

export default PreviewManager;
