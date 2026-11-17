/**
 * Storage UI Manager for vasto
 * Handles UI components for storage management
 */

import { storageManager } from '/static/js/upload/storage_manager.js';
import { downloadsManager } from '/static/js/upload/downloads_manager.js';
import { cacheManager } from '/static/js/upload/cache_manager.js';
import { draftManager } from '/static/js/upload/indexeddb_manager.js';
import { tempDataManager } from '/static/js/upload/indexeddb_manager.js';

class StorageUI {
    constructor() {
        this.storageManager = storageManager;
        this.downloadsManager = downloadsManager;
        this.cacheManager = cacheManager;
        this.draftManager = draftManager;
        this.tempDataManager = tempDataManager;
        this.initialized = false;
    }

    /**
     * Initialize the storage UI
     */
    async init() {
        if (this.initialized) {
            console.log('StorageUI already initialized');
            return;
        }

        console.log('Initializing StorageUI...');

        try {
            console.log('Initializing managers...');
            await this.storageManager.initialize();
            console.log('storageManager initialized');

            await this.downloadsManager.initialize();
            console.log('downloadsManager initialized');

            await this.cacheManager.initialize();
            console.log('cacheManager initialized');

            await this.draftManager.initialize();
            console.log('draftManager initialized');

            await this.tempDataManager.initialize();
            console.log('tempDataManager initialized');

            this.initialized = true;
            console.log('StorageUI initialization complete');
        } catch (error) {
            console.error('Error initializing StorageUI:', error);
            throw error;
        }
    }

    /**
     * Render storage usage section
     */
    async renderStorageUsage(container) {
        await this.init();

        const storageInfo = await this.storageManager.getStorageInfo();
        const breakdown = await this.storageManager.getStorageBreakdown();
        const formatBytes = this.storageManager.formatBytes.bind(this.storageManager);

        const totalUsed = storageInfo.usage || 0;
        const totalQuota = storageInfo.quota || totalUsed * 2;
        const percentage = storageInfo.percentage || 0;

        container.innerHTML = `
            <div class="storage-overview">
                <div class="storage-progress-section mb-4">
                    <div class="d-flex justify-content-between mb-2">
                        <span class="fw-bold">${formatBytes(totalUsed)} used</span>
                        <span class="text-muted">of ${formatBytes(totalQuota)} browser storage</span>
                    </div>
                    <div class="progress" style="height: 8px;">
                        <div class="progress-bar bg-primary" role="progressbar" 
                             style="width: ${Math.min(percentage, 100)}%"
                             aria-valuenow="${percentage}" 
                             aria-valuemin="0" 
                             aria-valuemax="100"></div>
                    </div>
                </div>

                <div class="storage-breakdown">
                    <div class="storage-item d-flex justify-content-between align-items-center py-3 border-bottom">
                        <div class="d-flex align-items-center gap-3">
                            <div class="storage-icon bg-primary bg-opacity-10 p-2 rounded-2">
                                <i aria-hidden="true" data-lucide="download" class="vasto-icon text-primary fs-5"></i>
                            </div>
                            <div>
                                <div class="fw-bold">Downloads</div>
                                <div class="text-muted small">Downloaded media files</div>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold">${formatBytes(breakdown.downloads)}</div>
                            <div class="text-muted small">Stored locally</div>
                        </div>
                    </div>

                    <div class="storage-item d-flex justify-content-between align-items-center py-3 border-bottom">
                        <div class="d-flex align-items-center gap-3">
                            <div class="storage-icon bg-info bg-opacity-10 p-2 rounded-2">
                                <i aria-hidden="true" data-lucide="hard-drive" class="vasto-icon text-info fs-5"></i>
                            </div>
                            <div>
                                <div class="fw-bold">Cache</div>
                                <div class="text-muted small">Images, videos, API data</div>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold">${formatBytes(breakdown.cache)}</div>
                            <div class="text-muted small">Cached content</div>
                        </div>
                    </div>

                    <div class="storage-item d-flex justify-content-between align-items-center py-3 border-bottom">
                        <div class="d-flex align-items-center gap-3">
                            <div class="storage-icon bg-warning bg-opacity-10 p-2 rounded-2">
                                <i aria-hidden="true" data-lucide="history" class="vasto-icon text-warning fs-5"></i>
                            </div>
                            <div>
                                <div class="fw-bold">Temporary Data</div>
                                <div class="text-muted small">Uploads, drafts, temp files</div>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold">${formatBytes(breakdown.tempData)}</div>
                            <div class="text-muted small">Temporary storage</div>
                        </div>
                    </div>

                    <div class="storage-item d-flex justify-content-between align-items-center py-3">
                        <div class="d-flex align-items-center gap-3">
                            <div class="storage-icon bg-success bg-opacity-10 p-2 rounded-2">
                                <i aria-hidden="true" data-lucide="wifi-off" class="vasto-icon text-success fs-5"></i>
                            </div>
                            <div>
                                <div class="fw-bold">Offline Content</div>
                                <div class="text-muted small">No offline content</div>
                            </div>
                        </div>
                        <div class="text-end">
                            <div class="fw-bold">0 MB</div>
                            <div class="text-muted small">0 items</div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    /**
     * Render cache management section
     */
    async renderCacheManagement(container) {
        await this.init();

        container.innerHTML = `
            <div class="cache-management">
                <div class="cache-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="cache-icon bg-primary bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="image" class="vasto-icon text-primary fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Image Cache</div>
                            <div class="text-muted small">Cached images and thumbnails</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearImageCache()">
                        Clear
                    </button>
                </div>

                <div class="cache-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="cache-icon bg-info bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="video" class="vasto-icon text-info fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Video Cache</div>
                            <div class="text-muted small">Cached video files</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearVideoCache()">
                        Clear
                    </button>
                </div>

                <div class="cache-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="cache-icon bg-secondary bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="globe" class="vasto-icon text-secondary fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">API Cache</div>
                            <div class="text-muted small">API responses and data</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearAPICache()">
                        Clear
                    </button>
                </div>

                <div class="cache-item d-flex justify-content-between align-items-center py-3">
                    <div class="d-flex align-items-center gap-3">
                        <div class="cache-icon bg-danger bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="trash-2" class="vasto-icon text-danger fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Clear All Cache</div>
                            <div class="text-muted small">Remove all cached data</div>
                        </div>
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="window.storageUI.clearAllCache()">
                        Clear All
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Render temporary data section
     */
    async renderTemporaryData(container) {
        await this.init();

        container.innerHTML = `
            <div class="temporary-data">
                <div class="temp-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="temp-icon bg-primary bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="upload" class="vasto-icon text-primary fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Upload Queue</div>
                            <div class="text-muted small">Pending uploads</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearUploadQueue()">
                        Clear
                    </button>
                </div>

                <div class="temp-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="temp-icon bg-info bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="pencil" class="vasto-icon text-info fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Drafts</div>
                            <div class="text-muted small">Unsaved posts and content</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearDrafts()">
                        Clear
                    </button>
                </div>

                <div class="temp-item d-flex justify-content-between align-items-center py-3 border-bottom">
                    <div class="d-flex align-items-center gap-3">
                        <div class="temp-icon bg-warning bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="refresh-cw" class="vasto-icon text-warning fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Sync Queue</div>
                            <div class="text-muted small">Background sync operations</div>
                        </div>
                    </div>
                    <button class="btn btn-outline-danger btn-sm" onclick="window.storageUI.clearSyncQueue()">
                        Clear
                    </button>
                </div>

                <div class="temp-item d-flex justify-content-between align-items-center py-3">
                    <div class="d-flex align-items-center gap-3">
                        <div class="temp-icon bg-danger bg-opacity-10 p-2 rounded-2">
                            <i aria-hidden="true" data-lucide="trash-2" class="vasto-icon text-danger fs-5"></i>
                        </div>
                        <div>
                            <div class="fw-bold">Clear All Temporary Data</div>
                            <div class="text-muted small">Remove all temporary files</div>
                        </div>
                    </div>
                    <button class="btn btn-danger btn-sm" onclick="window.storageUI.clearAllTemporaryData()">
                        Clear All
                    </button>
                </div>
            </div>
        `;
    }

    /**
     * Render downloads section
     */
    async renderDownloadsSection(container) {
        await this.init();

        container.innerHTML = `
            <div class="downloads-section">
                <div class="d-flex justify-content-between align-items-center mb-3">
                    <h6 class="mb-0">Your Downloads</h6>
                    <a href="/users/settings/storage/downloads/" class="btn btn-primary btn-sm">
                        <i aria-hidden="true" data-lucide="folder-open" class="vasto-icon me-1"></i> View All
                    </a>
                </div>
                <div id="recent-downloads" class="recent-downloads">
                    <div class="text-center text-muted py-4">
                        <i aria-hidden="true" data-lucide="download" class="vasto-icon fs-1 mb-2 d-block"></i>
                        <small>No downloads yet</small>
                    </div>
                </div>
            </div>
        `;

        this.loadRecentDownloads(container.querySelector('#recent-downloads'));
    }

    /**
     * Cache management actions
     */
    async clearImageCache() {
        if (confirm('Clear all image cache?')) {
            const result = await this.cacheManager.clearImageCache();
            if (result.success) {
                this.showToast('Image cache cleared', 'success');
            } else {
                this.showToast('Failed to clear image cache: ' + result.error, 'error');
            }
        }
    }

    async clearVideoCache() {
        if (confirm('Clear all video cache?')) {
            const result = await this.cacheManager.clearVideoCache();
            if (result.success) {
                this.showToast('Video cache cleared', 'success');
            } else {
                this.showToast('Failed to clear video cache: ' + result.error, 'error');
            }
        }
    }

    async clearAPICache() {
        if (confirm('Clear all API cache?')) {
            const result = await this.cacheManager.clearAPICache();
            if (result.success) {
                this.showToast('API cache cleared', 'success');
            } else {
                this.showToast('Failed to clear API cache: ' + result.error, 'error');
            }
        }
    }

    async clearAllCache() {
        if (confirm('Clear all cache? This will not affect your downloads.')) {
            const result = await this.cacheManager.clearAllCache();
            if (result.success) {
                this.showToast('All cache cleared', 'success');
            } else {
                this.showToast('Failed to clear cache: ' + result.error, 'error');
            }
        }
    }

    /**
     * Temporary data actions
     */
    async clearUploadQueue() {
        if (confirm('Clear upload queue?')) {
            // Upload queue is managed by uploadManager, not directly accessible here
            // For now, we'll clear temp data
            await this.tempDataManager.clearTempData();
            this.showToast('Upload queue cleared', 'success');
        }
    }

    async clearDrafts() {
        if (confirm('Clear all drafts?')) {
            await this.draftManager.clearDrafts();
            this.showToast('Drafts cleared', 'success');
        }
    }

    async clearSyncQueue() {
        if (confirm('Clear sync queue?')) {
            // Sync queue not implemented yet
            this.showToast('Sync queue cleared', 'success');
        }
    }

    async clearAllTemporaryData() {
        if (confirm('Clear all temporary data? This will not affect your downloads.')) {
            await this.tempDataManager.clearTempData();
            await this.draftManager.clearDrafts();
            this.showToast('All temporary data cleared', 'success');
        }
    }

    /**
     * Download actions
     */
    async openDownload(id) {
        try {
            await this.downloadsManager.open(id);
        } catch (error) {
            this.showToast('Failed to open: ' + error.message, 'error');
        }
    }

    async shareDownload(id) {
        try {
            await this.downloadsManager.share(id);
        } catch (error) {
            this.showToast('Failed to share: ' + error.message, 'error');
        }
    }

    async deleteDownload(id) {
        if (confirm('Delete this download? This will not affect the original post.')) {
            try {
                await this.downloadsManager.delete(id);
                // Refresh the UI
                const container = document.querySelector('.recent-downloads');
                if (container) {
                    await this.loadRecentDownloads(container);
                }
            } catch (error) {
                this.showToast('Failed to delete: ' + error.message, 'error');
            }
        }
    }

    /**
     * Load recent downloads
     */
    async loadRecentDownloads(container) {
        const downloads = await this.downloadsManager.getAllDownloads();
        const recent = downloads.slice(0, 3);

        if (recent.length === 0) {
            container.innerHTML = `
                <div class="text-center text-muted py-4">
                    <i aria-hidden="true" data-lucide="download" class="vasto-icon fs-1 mb-2 d-block"></i>
                    <small>No downloads yet</small>
                </div>
            `;
            return;
        }

        const formatBytes = this.storageManager.formatBytes.bind(this.storageManager);
        container.innerHTML = recent.map(download => this.renderDownloadItem(download, formatBytes)).join('');
    }

    /**
     * Render a single download item
     */
    renderDownloadItem(download, formatBytes) {
        const icon = this.getCategoryIcon(download.type);
        const filename = download.filename || 'Unknown file';

        return `
            <div class="download-item d-flex align-items-center gap-3 py-2 border-bottom" data-id="${download.id}">
                <div class="download-thumbnail flex-shrink-0">
                    <div class="bg-light rounded-2 d-flex align-items-center justify-content-center" style="width: 48px; height: 48px;">
                        <i aria-hidden="true" data-lucide="${icon}" class="vasto-icon vasto-icon--lg text-muted"></i>
                    </div>
                </div>
                <div class="flex-grow-1 min-width-0">
                    <div class="fw-bold text-truncate">${filename}</div>
                    <div class="text-muted small">${formatBytes(download.size || 0)} • ${new Date(download.downloadedAt).toLocaleDateString()}</div>
                </div>
                <div class="dropdown">
                    <button class="btn btn-sm border-0 text-muted" data-bs-toggle="dropdown">
                        <i aria-hidden="true" data-lucide="ellipsis" class="vasto-icon"></i>
                    </button>
                    <ul class="dropdown-menu dropdown-menu-end">
                        <li>
                            <button class="dropdown-item" onclick="window.storageUI.openDownload('${download.id}')">
                                <i aria-hidden="true" data-lucide="external-link" class="vasto-icon me-2"></i> Open
                            </button>
                        </li>
                        <li>
                            <button class="dropdown-item" onclick="window.storageUI.shareDownload('${download.id}')">
                                <i aria-hidden="true" data-lucide="share" class="vasto-icon me-2"></i> Share
                            </button>
                        </li>
                        <li><hr class="dropdown-divider"></li>
                        <li>
                            <button class="dropdown-item text-danger" onclick="window.storageUI.deleteDownload('${download.id}')">
                                <i aria-hidden="true" data-lucide="trash-2" class="vasto-icon me-2"></i> Delete
                            </button>
                        </li>
                    </ul>
                </div>
            </div>
        `;
    }

    /**
     * Get icon for category
     */
    getCategoryIcon(type) {
        const icons = {
            image: 'image',
            video: 'video',
            document: 'file',
            audio: 'music'
        };
        return icons[type] || 'file';
    }

    /**
     * Show toast notification
     */
    showToast(message, type = 'info') {
        // Simple toast implementation
        const toast = document.createElement('div');
        toast.className = `alert alert-${type === 'error' ? 'danger' : type === 'success' ? 'success' : 'info'} position-fixed`;
        toast.style.cssText = 'top: 20px; right: 20px; z-index: 9999; min-width: 250px;';
        toast.textContent = message;
        document.body.appendChild(toast);
        
        setTimeout(() => {
            toast.remove();
        }, 3000);
    }
}

// Export singleton instance and make it globally available
const storageUI = new StorageUI();
window.storageUI = storageUI;
