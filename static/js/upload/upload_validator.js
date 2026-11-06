/**
 * Upload Validator
 * 
 * Validates media files and captions before upload.
 * Returns structured validation results without UI manipulation.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';
import { previewManager } from './preview_manager.js?v=20261106-group1';

// Validation result structure
class ValidationResult {
    constructor(isValid = true, errors = [], warnings = []) {
        this.isValid = isValid;
        this.errors = errors;
        this.warnings = warnings;
    }

    addError(field, message) {
        this.errors.push({ field, message });
        this.isValid = false;
    }

    addWarning(field, message) {
        this.warnings.push({ field, message });
    }

    merge(otherResult) {
        this.errors.push(...otherResult.errors);
        this.warnings.push(...otherResult.warnings);
        this.isValid = this.isValid && otherResult.isValid;
    }
}

// Validation configuration based on investigation findings
const VALIDATION_CONFIG = {
    image: {
        maxSize: 5 * 1024 * 1024, // 5MB
        allowedTypes: ['image/jpeg', 'image/jpg', 'image/png', 'image/webp', 'image/gif', 'image/heic', 'image/heif', 'image/bmp'],
        maxDimensions: { width: 10000, height: 10000 },
        minDimensions: { width: 1, height: 1 },
    },
    video: {
        maxSize: 150 * 1024 * 1024, // 150MB — must match server posts/validators.py
        allowedTypes: ['video/mp4', 'video/webm', 'video/ogg', 'video/quicktime', 'video/3gpp', 'video/x-matroska', 'video/x-m4v', 'video/3gpp2'],
        maxDuration: 3600, // 1 hour in seconds
    },
    document: {
        maxSize: 50 * 1024 * 1024, // 50MB
        allowedTypes: ['application/pdf', 'text/plain', 'text/csv', 'text/markdown', 'application/msword', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document', 'application/vnd.ms-excel', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet', 'application/vnd.ms-powerpoint', 'application/vnd.openxmlformats-officedocument.presentationml.presentation', 'application/zip', 'application/json'],
    },
    audio: {
        maxSize: 20 * 1024 * 1024, // 20MB — must match server posts/validators.py
        allowedTypes: ['audio/mpeg', 'audio/mp3', 'audio/wav', 'audio/ogg', 'audio/webm', 'audio/aac', 'audio/mp4', 'audio/x-m4a', 'audio/3gpp', 'audio/flac', 'application/ogg'],
    },
    caption: {
        maxLength: 2500,
        minLength: 0,
    },
};

class ImageValidator {
    /**
     * Validate an image file
     * @param {File} file - Image file to validate
     * @returns {Promise<ValidationResult>}
     */
    static async validate(file) {
        const result = new ValidationResult();

        // Check file type (with filename extension fallback for mobile/Capacitor pickers)
        const isAllowedMime = file.type && VALIDATION_CONFIG.image.allowedTypes.includes(file.type);
        const hasImageExt = /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(file.name || '');
        if (!isAllowedMime && !hasImageExt) {
            result.addError('type', `Invalid image type. Allowed: ${VALIDATION_CONFIG.image.allowedTypes.join(', ')}`);
        }

        // Check file size
        if (file.size > VALIDATION_CONFIG.image.maxSize) {
            result.addError('size', `Image too large. Maximum size: ${this.formatSize(VALIDATION_CONFIG.image.maxSize)}`);
        }

        // Check dimensions by loading the image
        try {
            const dimensions = await this.getImageDimensions(file);
            
            if (dimensions.width > VALIDATION_CONFIG.image.maxDimensions.width) {
                result.addError('dimensions', `Image width too large. Maximum: ${VALIDATION_CONFIG.image.maxDimensions.width}px`);
            }
            
            if (dimensions.height > VALIDATION_CONFIG.image.maxDimensions.height) {
                result.addError('dimensions', `Image height too large. Maximum: ${VALIDATION_CONFIG.image.maxDimensions.height}px`);
            }

            if (dimensions.width < VALIDATION_CONFIG.image.minDimensions.width ||
                dimensions.height < VALIDATION_CONFIG.image.minDimensions.height) {
                result.addError('dimensions', 'Image dimensions too small');
            }
        } catch (error) {
            console.warn('[upload_validator] Could not read image dimensions client-side, deferring to server:', error);
            // Non-fatal: do not block upload if browser cannot decode dimensions locally
        }

        return result;
    }

    /**
     * Get image dimensions
     * @param {File} file - Image file
     * @returns {Promise<{width: number, height: number}>}
     */
    static getImageDimensions(file) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = previewManager.createObjectUrl(file);
            
            img.onload = () => {
                previewManager.revokeObjectUrl(url);
                resolve({ width: img.width, height: img.height });
            };
            
            img.onerror = () => {
                previewManager.revokeObjectUrl(url);
                reject(new Error('Failed to load image'));
            };
            
            img.src = url;
        });
    }

    /**
     * Format file size for display
     * @param {number} bytes - Size in bytes
     * @returns {string} Formatted size
     */
    static formatSize(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }
}

class VideoValidator {
    /**
     * Validate a video file
     * @param {File} file - Video file to validate
     * @returns {Promise<ValidationResult>}
     */
    static async validate(file) {
        const result = new ValidationResult();

        // Check file type (with extension fallback for mobile pickers sending octet-stream)
        const isAllowedMime = file.type && VALIDATION_CONFIG.video.allowedTypes.includes(file.type);
        const hasVideoExt = /\.(mp4|m4v|webm|mov|mkv|ogv|ogg|3gp|3g2)$/i.test(file.name || '');
        const isGeneric = !file.type || file.type === 'application/octet-stream';
        if (!isAllowedMime && !(isGeneric && hasVideoExt)) {
            result.addError('type', `Invalid video type. Allowed: ${VALIDATION_CONFIG.video.allowedTypes.join(', ')}`);
        }

        // Check file size
        if (file.size > VALIDATION_CONFIG.video.maxSize) {
            result.addError('size', `Video too large. Maximum size: ${ImageValidator.formatSize(VALIDATION_CONFIG.video.maxSize)}`);
        }

        // Check duration by loading the video
        try {
            const duration = await this.getVideoDuration(file);
            
            if (duration > VALIDATION_CONFIG.video.maxDuration) {
                result.addError('duration', `Video too long. Maximum duration: ${Math.floor(VALIDATION_CONFIG.video.maxDuration / 60)} minutes`);
            }
        } catch (error) {
            result.addWarning('duration', 'Could not verify video duration');
        }

        return result;
    }

    /**
     * Get video duration
     * @param {File} file - Video file
     * @returns {Promise<number>} Duration in seconds
     */
    static getVideoDuration(file) {
        return new Promise((resolve, reject) => {
            const video = document.createElement('video');
            const url = previewManager.createObjectUrl(file);
            
            video.onloadedmetadata = () => {
                previewManager.revokeObjectUrl(url);
                resolve(video.duration);
            };
            
            video.onerror = () => {
                previewManager.revokeObjectUrl(url);
                reject(new Error('Failed to load video'));
            };
            
            video.src = url;
        });
    }
}

class DocumentValidator {
    /**
     * Validate a document file
     * @param {File} file - Document file to validate
     * @returns {Promise<ValidationResult>}
     */
    static async validate(file) {
        const result = new ValidationResult();

        // Check file type
        if (!VALIDATION_CONFIG.document.allowedTypes.includes(file.type)) {
            result.addError('type', `Invalid document type. Allowed: ${VALIDATION_CONFIG.document.allowedTypes.join(', ')}`);
        }

        // Check file size
        if (file.size > VALIDATION_CONFIG.document.maxSize) {
            result.addError('size', `Document too large. Maximum size: ${ImageValidator.formatSize(VALIDATION_CONFIG.document.maxSize)}`);
        }

        return result;
    }
}

class AudioValidator {
    /**
     * Validate an audio file
     * @param {File} file - Audio file to validate
     * @returns {Promise<ValidationResult>}
     */
    static async validate(file) {
        const result = new ValidationResult();

        // Check file type
        if (!VALIDATION_CONFIG.audio.allowedTypes.includes(file.type)) {
            result.addError('type', `Invalid audio type. Allowed: ${VALIDATION_CONFIG.audio.allowedTypes.join(', ')}`);
        }

        // Check file size
        if (file.size > VALIDATION_CONFIG.audio.maxSize) {
            result.addError('size', `Audio too large. Maximum size: ${ImageValidator.formatSize(VALIDATION_CONFIG.audio.maxSize)}`);
        }

        return result;
    }
}

class CaptionValidator {
    /**
     * Validate caption text
     * @param {string} caption - Caption text
     * @returns {ValidationResult}
     */
    static validate(caption) {
        const result = new ValidationResult();

        if (caption.length > VALIDATION_CONFIG.caption.maxLength) {
            result.addError('length', `Caption too long. Maximum: ${VALIDATION_CONFIG.caption.maxLength} characters`);
        }

        if (caption.length < VALIDATION_CONFIG.caption.minLength) {
            result.addError('length', `Caption too short. Minimum: ${VALIDATION_CONFIG.caption.minLength} characters`);
        }

        return result;
    }
}

class UploadValidator {
    /**
     * Validate a media file based on its type
     * @param {File} file - File to validate
     * @returns {Promise<ValidationResult>}
     */
    static async validateFile(file) {
        const fileType = file.type || '';
        const fileName = file.name || '';

        const isImage = fileType.startsWith('image/') || /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(fileName);
        const isVideo = fileType.startsWith('video/') || /\.(mp4|mov|webm|mkv|avi|3gp)$/i.test(fileName);
        const isDoc   = fileType === 'application/pdf' || /\.pdf$/i.test(fileName);
        const isAudio = fileType.startsWith('audio/') || /\.(mp3|wav|ogg|m4a|aac)$/i.test(fileName);

        if (isImage) {
            return await ImageValidator.validate(file);
        } else if (isVideo) {
            return await VideoValidator.validate(file);
        } else if (isDoc) {
            return await DocumentValidator.validate(file);
        } else if (isAudio) {
            return await AudioValidator.validate(file);
        } else {
            const result = new ValidationResult();
            result.addError('type', 'Unsupported file type');
            return result;
        }
    }

    /**
     * Validate multiple files
     * @param {File[]} files - Files to validate
     * @returns {Promise<{valid: File[], invalid: Array<{file: File, result: ValidationResult}>}>}
     */
    static async validateFiles(files) {
        const valid = [];
        const invalid = [];

        for (const file of files) {
            const result = await this.validateFile(file);
            if (result.isValid) {
                valid.push(file);
            } else {
                invalid.push({ file, result });
            }
        }

        return { valid, invalid };
    }

    /**
     * Validate complete upload data (files + caption)
     * @param {object} uploadData - Upload data with files and caption
     * @returns {Promise<ValidationResult>}
     */
    static async validateUpload(uploadData) {
        const result = new ValidationResult();
        const { files, caption } = uploadData;

        // Validate files
        const fileValidation = await this.validateFiles(files);
        
        if (fileValidation.invalid.length > 0) {
            fileValidation.invalid.forEach(({ file, result: fileResult }) => {
                result.merge(fileResult);
            });
        }

        // Validate caption
        if (caption !== undefined && caption !== null) {
            const captionResult = CaptionValidator.validate(caption);
            result.merge(captionResult);
        }

        // Check if at least one file or caption is present
        if (files.length === 0 && (!caption || caption.trim() === '')) {
            result.addError('content', 'Post must contain at least one file or caption');
        }

        return result;
    }
}

export {
    ValidationResult,
    ImageValidator,
    VideoValidator,
    DocumentValidator,
    AudioValidator,
    CaptionValidator,
    UploadValidator,
    VALIDATION_CONFIG,
};

export default UploadValidator;
