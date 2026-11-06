/**
 * vasto Unified Download Storage (IndexedDB)
 * Database: vasto_downloads_v1
 * Stores:
 *   - metadata: Record metadata for downloaded items (both native filesystem references and web blobs)
 *   - blobs: Binary Blob storage for PWA / Web offline persistence
 */

class DownloadStorage {
    constructor() {
        const owner = window.vastoUserId || document.body?.dataset.userId;
        this.dbName = owner ? `vasto_downloads_v2_${owner}` : 'vasto_downloads_v2_signed_out';
        this.dbVersion = 1;
        this.db = null;
        this.initPromise = null;
    }

    /**
     * Open or initialize IndexedDB connection
     */
    async init() {
        if (this.db) return this.db;
        if (this.initPromise) return this.initPromise;

        this.initPromise = new Promise((resolve, reject) => {
            if (!('indexedDB' in window)) {
                reject(new Error('IndexedDB is not supported in this browser.'));
                return;
            }

            const request = indexedDB.open(this.dbName, this.dbVersion);

            request.onupgradeneeded = (event) => {
                const db = event.target.result;

                // 1. Metadata store
                if (!db.objectStoreNames.contains('metadata')) {
                    const metaStore = db.createObjectStore('metadata', { keyPath: 'id' });
                    metaStore.createIndex('url', 'url', { unique: false });
                    metaStore.createIndex('category', 'category', { unique: false });
                    metaStore.createIndex('downloadedAt', 'downloadedAt', { unique: false });
                    metaStore.createIndex('postId', 'postId', { unique: false });
                }

                // 2. Blobs store (used for Web / PWA binary payload)
                if (!db.objectStoreNames.contains('blobs')) {
                    db.createObjectStore('blobs', { keyPath: 'id' });
                }
            };

            request.onsuccess = () => {
                this.db = request.result;
                resolve(this.db);
            };

            request.onerror = () => {
                console.error('[DownloadStorage] Failed to open IndexedDB:', request.error);
                reject(request.error);
            };
        });

        return this.initPromise;
    }

    /**
     * Save or update download metadata
     * @param {Object} metadata 
     */
    async saveMetadata(metadata) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['metadata'], 'readwrite');
            const store = tx.objectStore('metadata');
            const req = store.put(metadata);

            req.onsuccess = () => resolve(metadata.id);
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Retrieve metadata by ID
     * @param {string} id 
     */
    async getMetadata(id) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['metadata'], 'readonly');
            const store = tx.objectStore('metadata');
            const req = store.get(id);

            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Check if a download exists by its source URL
     * @param {string} url 
     */
    async getMetadataByUrl(url) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['metadata'], 'readonly');
            const store = tx.objectStore('metadata');
            const index = store.index('url');
            const req = index.get(url);

            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Get all downloads, optionally filtered by category
     * @param {string|null} category ('video', 'image', 'audio', 'document', or null for all)
     */
    async getAllMetadata(category = null) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['metadata'], 'readonly');
            const store = tx.objectStore('metadata');

            let req;
            if (category && category !== 'all') {
                const index = store.index('category');
                req = index.getAll(category);
            } else {
                req = store.getAll();
            }

            req.onsuccess = () => {
                const items = req.result || [];
                // Default sort descending by downloadedAt
                items.sort((a, b) => (b.downloadedAt || 0) - (a.downloadedAt || 0));
                resolve(items);
            };
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Save binary blob into IndexedDB
     * @param {string} id 
     * @param {Blob} blob 
     */
    async saveBlob(id, blob) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['blobs'], 'readwrite');
            const store = tx.objectStore('blobs');
            const req = store.put({ id, blob });

            req.onsuccess = () => resolve(id);
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Retrieve binary blob from IndexedDB
     * @param {string} id 
     * @returns {Promise<Blob|null>}
     */
    async getBlob(id) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['blobs'], 'readonly');
            const store = tx.objectStore('blobs');
            const req = store.get(id);

            req.onsuccess = () => resolve(req.result ? req.result.blob : null);
            req.onerror = () => reject(req.error);
        });
    }

    /**
     * Delete a download (metadata and associated blob)
     * @param {string} id 
     */
    async deleteDownload(id) {
        const db = await this.init();
        return new Promise((resolve, reject) => {
            const tx = db.transaction(['metadata', 'blobs'], 'readwrite');
            tx.objectStore('metadata').delete(id);
            tx.objectStore('blobs').delete(id);

            tx.oncomplete = () => {
                try {
                    window.dispatchEvent(new CustomEvent('vasto:download-deleted', { detail: { id } }));
                } catch (_) {}
                resolve(true);
            };
            tx.onerror = () => reject(tx.error);
        });
    }

    /**
     * Calculate storage stats (counts & total bytes)
     */
    async getStorageStats() {
        const items = await this.getAllMetadata();
        const stats = {
            totalCount: items.length,
            totalBytes: 0,
            counts: {
                video: 0,
                image: 0,
                audio: 0,
                document: 0
            },
            bytes: {
                video: 0,
                image: 0,
                audio: 0,
                document: 0
            }
        };

        for (const item of items) {
            const size = item.size || 0;
            const cat = item.category || 'document';
            stats.totalBytes += size;
            if (stats.counts[cat] !== undefined) {
                stats.counts[cat] += 1;
                stats.bytes[cat] += size;
            } else {
                stats.counts.document += 1;
                stats.bytes.document += size;
            }
        }

        return stats;
    }

    /**
     * Format bytes to human readable format
     */
    formatBytes(bytes, decimals = 1) {
        if (!bytes || bytes === 0) return '0 B';
        const k = 1024;
        const dm = decimals < 0 ? 0 : decimals;
        const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
        const i = Math.floor(Math.log(bytes) / Math.log(k));
        return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + ' ' + sizes[i];
    }
}

export const downloadStorage = new DownloadStorage();
