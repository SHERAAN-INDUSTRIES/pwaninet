/**
 * Cache Manager
 * 
 * Manages browser cache clearing operations.
 * Implements Clear Image Cache, Clear Video Cache, Clear API Cache, and Clear All Cache.
 * Downloaded media is never removed when clearing cache.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261106-group1';
import { downloadsMetadataManager } from './indexeddb_manager.js?v=20261106-group1';

class CacheManager {
    constructor() {
        this.isInitialized = false;
    }

    /**
     * Initialize the cache manager
     */
    initialize() {
        if (this.isInitialized) return;
        this.isInitialized = true;
    }

    /**
     * Check if Cache API is supported
     * @returns {boolean}
     */
    static isCacheAPISupported() {
        return 'caches' in window;
    }

    /**
     * Clear image cache
     * @returns {Promise<{success: boolean, cleared: number}>}
     */
    async clearImageCache() {
        if (!CacheManager.isCacheAPISupported()) {
            return { success: false, cleared: 0, error: 'Cache API not supported' };
        }

        try {
            const cacheNames = await caches.keys();
            let cleared = 0;

            for (const cacheName of cacheNames) {
                if (cacheName.includes('image') || cacheName.includes('media')) {
                    const cache = await caches.open(cacheName);
                    const keys = await cache.keys();
                    
                    for (const request of keys) {
                        // Only clear image URLs, not downloaded media
                        const isDownloaded = await this.isDownloadedMedia(request.url);
                        if (!isDownloaded) {
                            await cache.delete(request);
                            cleared++;
                        }
                    }
                }
            }

            uploadEvents.emit(UploadEventNames.CACHE_CLEARED, {
                type: 'image',
                cleared,
            });

            return { success: true, cleared };
        } catch (error) {
            console.error('Failed to clear image cache:', error);
            return { success: false, cleared: 0, error: error.message };
        }
    }

    /**
     * Clear video cache
     * @returns {Promise<{success: boolean, cleared: number}>}
     */
    async clearVideoCache() {
        if (!CacheManager.isCacheAPISupported()) {
            return { success: false, cleared: 0, error: 'Cache API not supported' };
        }

        try {
            const cacheNames = await caches.keys();
            let cleared = 0;

            for (const cacheName of cacheNames) {
                if (cacheName.includes('video') || cacheName.includes('media')) {
                    const cache = await caches.open(cacheName);
                    const keys = await cache.keys();
                    
                    for (const request of keys) {
                        // Only clear video URLs, not downloaded media
                        const isDownloaded = await this.isDownloadedMedia(request.url);
                        if (!isDownloaded) {
                            await cache.delete(request);
                            cleared++;
                        }
                    }
                }
            }

            uploadEvents.emit(UploadEventNames.CACHE_CLEARED, {
                type: 'video',
                cleared,
            });

            return { success: true, cleared };
        } catch (error) {
            console.error('Failed to clear video cache:', error);
            return { success: false, cleared: 0, error: error.message };
        }
    }

    /**
     * Clear API cache
     * @returns {Promise<{success: boolean, cleared: number}>}
     */
    async clearAPICache() {
        if (!CacheManager.isCacheAPISupported()) {
            return { success: false, cleared: 0, error: 'Cache API not supported' };
        }

        try {
            const cacheNames = await caches.keys();
            let cleared = 0;

            for (const cacheName of cacheNames) {
                if (cacheName.includes('api') || cacheName.includes('data')) {
                    await caches.delete(cacheName);
                    cleared++;
                }
            }

            uploadEvents.emit(UploadEventNames.CACHE_CLEARED, {
                type: 'api',
                cleared,
            });

            return { success: true, cleared };
        } catch (error) {
            console.error('Failed to clear API cache:', error);
            return { success: false, cleared: 0, error: error.message };
        }
    }

    /**
     * Clear all cache (except downloaded media)
     * @returns {Promise<{success: boolean, cleared: number}>}
     */
    async clearAllCache() {
        if (!CacheManager.isCacheAPISupported()) {
            return { success: false, cleared: 0, error: 'Cache API not supported' };
        }

        try {
            const cacheNames = await caches.keys();
            let cleared = 0;

            for (const cacheName of cacheNames) {
                const cache = await caches.open(cacheName);
                const keys = await cache.keys();
                
                for (const request of keys) {
                    // Only clear if not downloaded media
                    const isDownloaded = await this.isDownloadedMedia(request.url);
                    if (!isDownloaded) {
                        await cache.delete(request);
                        cleared++;
                    }
                }
            }

            uploadEvents.emit(UploadEventNames.CACHE_CLEARED, {
                type: 'all',
                cleared,
            });

            return { success: true, cleared };
        } catch (error) {
            console.error('Failed to clear all cache:', error);
            return { success: false, cleared: 0, error: error.message };
        }
    }

    /**
     * Check if a URL is downloaded media
     * @param {string} url - URL to check
     * @returns {Promise<boolean>}
     */
    async isDownloadedMedia(url) {
        try {
            const downloads = await downloadsMetadataManager.getAllDownloads();
            return downloads.some(download => download.url === url);
        } catch (error) {
            return false;
        }
    }

    /**
     * Get cache size estimate
     * @returns {Promise<{usage: number, quota: number, percentage: number}>}
     */
    async getCacheSize() {
        if (!('storage' in navigator) || !navigator.storage.estimate) {
            return { usage: 0, quota: 0, percentage: 0, error: 'Storage API not supported' };
        }

        try {
            const estimate = await navigator.storage.estimate();
            const usage = estimate.usage || 0;
            const quota = estimate.quota || 0;
            const percentage = quota > 0 ? (usage / quota) * 100 : 0;

            return { usage, quota, percentage };
        } catch (error) {
            console.error('Failed to get cache size:', error);
            return { usage: 0, quota: 0, percentage: 0, error: error.message };
        }
    }

    /**
     * Get cache breakdown by type
     * @returns {Promise<{images: number, videos: number, api: number, other: number}>}
     */
    async getCacheBreakdown() {
        if (!CacheManager.isCacheAPISupported()) {
            return { images: 0, videos: 0, api: 0, other: 0, error: 'Cache API not supported' };
        }

        try {
            const cacheNames = await caches.keys();
            const breakdown = { images: 0, videos: 0, api: 0, other: 0 };

            for (const cacheName of cacheNames) {
                const cache = await caches.open(cacheName);
                const keys = await cache.keys();
                
                for (const request of keys) {
                    const response = await cache.match(request);
                    if (response) {
                        const blob = await response.blob();
                        const size = blob.size;

                        if (cacheName.includes('image')) {
                            breakdown.images += size;
                        } else if (cacheName.includes('video')) {
                            breakdown.videos += size;
                        } else if (cacheName.includes('api')) {
                            breakdown.api += size;
                        } else {
                            breakdown.other += size;
                        }
                    }
                }
            }

            return breakdown;
        } catch (error) {
            console.error('Failed to get cache breakdown:', error);
            return { images: 0, videos: 0, api: 0, other: 0, error: error.message };
        }
    }

    /**
     * Format bytes for display
     * @param {number} bytes - Bytes
     * @returns {string} Formatted size
     */
    formatBytes(bytes) {
        if (bytes === 0) return '0 Bytes';
        const k = 1024;
        const sizes = ['Bytes', 'KB', 'MB', 'GB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return Math.round(bytes / Math.pow(k, i) * 100) / 100 + ' ' + sizes[i];
    }
}

// Global cache manager instance
export const cacheManager = new CacheManager();

// Initialize on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => cacheManager.initialize());
} else {
    cacheManager.initialize();
}

export {
    CacheManager,
};

export default CacheManager;
