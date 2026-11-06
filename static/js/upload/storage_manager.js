/**
 * Storage Manager
 * 
 * Manages browser storage estimation and display.
 * Uses navigator.storage.estimate() where supported.
 * Displays browser storage used, downloads, cache, temporary data, and offline content.
 */

import { cacheManager } from './cache_manager.js?v=20261106-group1';
import { downloadsMetadataManager } from './indexeddb_manager.js?v=20261106-group1';
import { tempDataManager } from './indexeddb_manager.js?v=20261106-group1';

class StorageManager {
    constructor() {
        this.isInitialized = false;
        this.storageInfo = {
            usage: 0,
            quota: 0,
            percentage: 0,
        };
    }

    /**
     * Initialize the storage manager
     */
    async initialize() {
        if (this.isInitialized) return;
        
        await this.updateStorageInfo();
        this.isInitialized = true;
    }

    /**
     * Check if Storage Estimate API is supported
     * @returns {boolean}
     */
    static isStorageEstimateSupported() {
        return 'storage' in navigator && 'estimate' in navigator.storage;
    }

    /**
     * Update storage information
     * @returns {Promise<object>}
     */
    async updateStorageInfo() {
        if (!StorageManager.isStorageEstimateSupported()) {
            return {
                usage: 0,
                quota: 0,
                percentage: 0,
                error: 'Storage Estimate API not supported',
            };
        }

        try {
            const estimate = await navigator.storage.estimate();
            this.storageInfo = {
                usage: estimate.usage || 0,
                quota: estimate.quota || 0,
                percentage: estimate.quota > 0 ? (estimate.usage / estimate.quota) * 100 : 0,
            };
            return this.storageInfo;
        } catch (error) {
            console.error('Failed to get storage estimate:', error);
            return {
                usage: 0,
                quota: 0,
                percentage: 0,
                error: error.message,
            };
        }
    }

    /**
     * Get storage information
     * @returns {object}
     */
    getStorageInfo() {
        return this.storageInfo;
    }

    /**
     * Get detailed storage breakdown
     * @returns {Promise<object>}
     */
    async getStorageBreakdown() {
        const breakdown = {
            total: this.storageInfo,
            downloads: 0,
            cache: 0,
            tempData: 0,
            indexedDB: 0,
            other: 0,
        };

        try {
            // Get downloads size
            const downloads = await downloadsMetadataManager.getAllDownloads();
            breakdown.downloads = downloads.reduce((sum, d) => sum + (d.size || 0), 0);

            // Get cache breakdown
            const cacheBreakdown = await cacheManager.getCacheBreakdown();
            if (cacheBreakdown.error) {
                breakdown.cache = 0;
            } else {
                breakdown.cache = cacheBreakdown.images + cacheBreakdown.videos + cacheBreakdown.api + cacheBreakdown.other;
            }

            // Get temp data size (estimated)
            const tempData = await tempDataManager.getAllTempData();
            breakdown.tempData = tempData.length * 1024; // Rough estimate

            // IndexedDB usage is included in storage estimate
            breakdown.indexedDB = breakdown.downloads + breakdown.tempData;

            // Calculate other
            breakdown.other = Math.max(0, this.storageInfo.usage - breakdown.cache - breakdown.indexedDB);

        } catch (error) {
            console.error('Failed to get storage breakdown:', error);
        }

        return breakdown;
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

    /**
     * Get storage status (low, medium, high, critical)
     * @returns {string}
     */
    getStorageStatus() {
        const percentage = this.storageInfo.percentage;

        if (percentage >= 90) return 'critical';
        if (percentage >= 75) return 'high';
        if (percentage >= 50) return 'medium';
        return 'low';
    }

    /**
     * Check if storage is low
     * @returns {boolean}
     */
    isStorageLow() {
        return this.storageInfo.percentage >= 75;
    }

    /**
     * Check if storage is critical
     * @returns {boolean}
     */
    isStorageCritical() {
        return this.storageInfo.percentage >= 90;
    }

    /**
     * Get available storage
     * @returns {number} Available bytes
     */
    getAvailableStorage() {
        return Math.max(0, this.storageInfo.quota - this.storageInfo.usage);
    }

    /**
     * Request persistent storage
     * @returns {Promise<{success: boolean, persisted: boolean}>}
     */
    async requestPersistentStorage() {
        if (!('storage' in navigator) || !navigator.storage.persist) {
            return { success: false, persisted: false, error: 'Persistent storage not supported' };
        }

        try {
            const persisted = await navigator.storage.persist();
            return { success: true, persisted };
        } catch (error) {
            console.error('Failed to request persistent storage:', error);
            return { success: false, persisted: false, error: error.message };
        }
    }

    /**
     * Check if storage is persistent
     * @returns {Promise<boolean>}
     */
    async isStoragePersistent() {
        if (!('storage' in navigator) || !navigator.storage.persisted) {
            return false;
        }

        try {
            return await navigator.storage.persisted();
        } catch (error) {
            console.error('Failed to check storage persistence:', error);
            return false;
        }
    }
}

// Global storage manager instance
export const storageManager = new StorageManager();

// Initialize on load
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', () => storageManager.initialize());
} else {
    storageManager.initialize();
}

export {
    StorageManager,
};

export default StorageManager;
