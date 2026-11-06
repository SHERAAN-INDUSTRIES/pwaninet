/**
 * Image Compression
 * 
 * Client-side image compression for images > 2MB.
 * Maintains high visual quality while reducing file size.
 * Videos, documents, and PDFs are NOT compressed.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';
import { previewManager } from './preview_manager.js?v=20261106-group1';

// Compression configuration
const COMPRESSION_CONFIG = {
    threshold: 2 * 1024 * 1024, // 2MB - compress only if larger
    maxWidth: 1920,
    maxHeight: 1920,
    quality: 0.85, // High quality (85%)
    format: 'image/jpeg', // Convert to JPEG for better compression
    progressive: true,
};

class ImageCompressionResult {
    constructor(originalFile, compressedFile, originalSize, compressedSize) {
        this.originalFile = originalFile;
        this.compressedFile = compressedFile;
        this.originalSize = originalSize;
        this.compressedSize = compressedSize;
        this.reductionPercentage = ((originalSize - compressedSize) / originalSize * 100).toFixed(1);
        this.wasCompressed = compressedSize < originalSize;
    }
}

class ImageCompressor {
    /**
     * Check if an image should be compressed
     * @param {File} file - Image file
     * @returns {boolean}
     */
    static shouldCompress(file) {
        // Only compress images
        const isImage = (file.type && file.type.startsWith('image/')) || /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(file.name || '');
        if (!isImage) {
            return false;
        }

        // Only compress if larger than threshold
        return file.size > COMPRESSION_CONFIG.threshold;
    }

    /**
     * Compress an image file
     * @param {File} file - Image file to compress
     * @param {object} options - Compression options
     * @returns {Promise<ImageCompressionResult>}
     */
    static async compress(file, options = {}) {
        const config = { ...COMPRESSION_CONFIG, ...options };

        // Check if compression is needed
        if (!this.shouldCompress(file)) {
            return new ImageCompressionResult(file, file, file.size, file.size);
        }

        try {
            // Load image
            const image = await this.loadImage(file);
            
            // Calculate dimensions
            const dimensions = this.calculateDimensions(
                image.width,
                image.height,
                config.maxWidth,
                config.maxHeight
            );

            // Create canvas and draw image
            const canvas = document.createElement('canvas');
            canvas.width = dimensions.width;
            canvas.height = dimensions.height;
            
            const ctx = canvas.getContext('2d');
            ctx.drawImage(image, 0, 0, dimensions.width, dimensions.height);

            // Compress using canvas toBlob
            const compressedBlob = await this.canvasToBlob(
                canvas,
                config.format,
                config.quality
            );

            // Create compressed file
            const compressedFile = new File(
                [compressedBlob],
                file.name.replace(/\.[^.]+$/, '.jpg'),
                { type: config.format }
            );

            const result = new ImageCompressionResult(
                file,
                compressedFile,
                file.size,
                compressedFile.size
            );

            // Emit compression progress event
            uploadEvents.emit(UploadEventNames.COMPRESSION_COMPLETED, {
                originalFile: file,
                compressedFile,
                originalSize: file.size,
                compressedSize: compressedFile.size,
                reduction: result.reductionPercentage,
            });

            return result;
        } catch (error) {
            console.error('Image compression failed:', error);
            uploadEvents.emit(UploadEventNames.COMPRESSION_FAILED, {
                file,
                error: error.message,
            });
            throw error;
        }
    }

    /**
     * Load an image file
     * @param {File} file - Image file
     * @returns {Promise<HTMLImageElement>}
     */
    static loadImage(file) {
        return new Promise((resolve, reject) => {
            const img = new Image();
            const url = previewManager.createObjectUrl(file);

            img.onload = () => {
                previewManager.revokeObjectUrl(url);
                resolve(img);
            };

            img.onerror = () => {
                previewManager.revokeObjectUrl(url);
                reject(new Error('Failed to load image'));
            };

            img.src = url;
        });
    }

    /**
     * Calculate scaled dimensions maintaining aspect ratio
     * @param {number} width - Original width
     * @param {number} height - Original height
     * @param {number} maxWidth - Maximum width
     * @param {number} maxHeight - Maximum height
     * @returns {{width: number, height: number}}
     */
    static calculateDimensions(width, height, maxWidth, maxHeight) {
        if (width <= maxWidth && height <= maxHeight) {
            return { width, height };
        }

        const widthRatio = maxWidth / width;
        const heightRatio = maxHeight / height;
        const ratio = Math.min(widthRatio, heightRatio);

        return {
            width: Math.round(width * ratio),
            height: Math.round(height * ratio),
        };
    }

    /**
     * Convert canvas to blob
     * @param {HTMLCanvasElement} canvas - Canvas element
     * @param {string} format - Image format
     * @param {number} quality - Quality (0-1)
     * @returns {Promise<Blob>}
     */
    static canvasToBlob(canvas, format, quality) {
        return new Promise((resolve, reject) => {
            canvas.toBlob(
                (blob) => {
                    if (blob) {
                        resolve(blob);
                    } else {
                        reject(new Error('Canvas to blob conversion failed'));
                    }
                },
                format,
                quality
            );
        });
    }

    /**
     * Compress multiple images
     * @param {File[]} files - Image files to compress
     * @param {object} options - Compression options
     * @returns {Promise<ImageCompressionResult[]>}
     */
    static async compressMultiple(files, options = {}) {
        const results = [];
        const total = files.length;

        for (let i = 0; i < files.length; i++) {
            const file = files[i];
            
            // Emit progress
            uploadEvents.emit(UploadEventNames.COMPRESSION_PROGRESS, {
                current: i + 1,
                total,
                file: file.name,
            });

            // Skip non-images
            const isImage = (file.type && file.type.startsWith('image/')) || /\.(jpe?g|png|webp|gif|bmp|heic|heif)$/i.test(file.name || '');
            if (!isImage) {
                results.push(new ImageCompressionResult(file, file, file.size, file.size));
                continue;
            }

            try {
                const result = await this.compress(file, options);
                results.push(result);
            } catch (error) {
                // On error, use original file
                results.push(new ImageCompressionResult(file, file, file.size, file.size));
            }
        }

        return results;
    }

    /**
     * Get compression statistics
     * @param {ImageCompressionResult[]} results - Compression results
     * @returns {object} Statistics
     */
    static getStatistics(results) {
        const stats = {
            total: results.length,
            compressed: 0,
            skipped: 0,
            originalTotalSize: 0,
            compressedTotalSize: 0,
            totalReduction: 0,
        };

        results.forEach((result) => {
            stats.originalTotalSize += result.originalSize;
            stats.compressedTotalSize += result.compressedSize;

            if (result.wasCompressed) {
                stats.compressed++;
            } else {
                stats.skipped++;
            }
        });

        stats.totalReduction = (
            ((stats.originalTotalSize - stats.compressedTotalSize) / stats.originalTotalSize) * 100
        ).toFixed(1);

        return stats;
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

export {
    ImageCompressor,
    ImageCompressionResult,
    COMPRESSION_CONFIG,
};

export default ImageCompressor;
