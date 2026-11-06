/**
 * pwaninet Unified Download Manager
 * Coordinates:
 *   - Native Capacitor direct-to-disk downloads (no JS memory overhead)
 *   - Web/PWA streaming downloads to IndexedDB
 *   - Concurrency queue handling
 *   - Offline media streaming source resolver (Capacitor.convertFileSrc vs Blob URL)
 */

import { downloadStorage } from './download_storage.js?v=20261106-group1';
import { downloadQueue, DownloadStatus } from './download_queue.js?v=20261106-group1';

class DownloadManager {
    constructor() {
        this.storage = downloadStorage;
        this.queue = downloadQueue;
        this.isInitialized = false;
    }

    /**
     * Check if running in Capacitor native app environment with Filesystem plugin
     */
    isNativeEnvironment() {
        return typeof window !== 'undefined' &&
               Boolean(window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Filesystem);
    }

    /**
     * Initialize download manager, storage, and queue runner
     */
    async initialize() {
        if (this.isInitialized) return;

        await this.storage.init();
        this.queue.setRunner(this.executeDownload.bind(this));
        this.isInitialized = true;
        console.log('[DownloadManager] Initialized. Native:', this.isNativeEnvironment());
    }

    /**
     * Request a download
     * Accepts either download(optionsObject) or download(url, metadataObject)
     * @param {Object|string} optionsOrUrl
     * @param {Object} [optionalMeta]
     */
    async download(optionsOrUrl, optionalMeta = {}) {
        await this.initialize();

        let options = {};
        if (typeof optionsOrUrl === 'string') {
            options = { url: optionsOrUrl, ...optionalMeta };
        } else if (typeof optionsOrUrl === 'object' && optionsOrUrl !== null) {
            options = { ...optionsOrUrl };
        }

        if (!options.url) {
            throw new Error('Download URL is required');
        }

        // Capacitor's native downloader requires an absolute URL. Django template
        // tags commonly emit protected endpoints as root-relative paths, which
        // browsers accept but native URLConnection rejects with "no protocol".
        try {
            options.url = new URL(options.url, window.location.href).href;
        } catch (error) {
            throw new Error(`Invalid download URL: ${options.url}`);
        }

        if (!options.filename) {
            options.filename = options.url.split('/').pop()?.split('?')[0] || `download_${Date.now()}`;
        }
        if (!options.category && options.mediaType) {
            options.category = options.mediaType;
        }

        // Check if file has already been downloaded
        const existing = await this.storage.getMetadataByUrl(options.url);
        if (existing) {
            // Let users retry a failed gallery publish without downloading the
            // media a second time; the private offline copy is already present.
            const existingCategory = existing.category || options.mediaType || options.category;
            const nativeBridge = window.AndroidBridge || window.pwaninetBridge;
            if (existing.isNative && existing.gallerySaved === false && existing.nativeUri &&
                ['image', 'video'].includes(existingCategory) && nativeBridge?.saveMediaToGallery) {
                try {
                    existing.gallerySaved = nativeBridge.saveMediaToGallery(
                        existing.nativeUri,
                        existing.filename,
                        existing.mimeType || (existingCategory === 'video' ? 'video/mp4' : 'image/jpeg'),
                        existingCategory
                    ) === true;
                    existing.galleryError = existing.gallerySaved ? null : 'The Android app could not publish the offline file to Gallery.';
                    await this.storage.saveMetadata(existing);
                } catch (galleryError) {
                    existing.galleryError = galleryError.message || 'The Android app could not publish the offline file to Gallery.';
                    console.warn('[DownloadManager] Gallery retry failed:', galleryError);
                }
            }
            window.dispatchEvent(new CustomEvent('pwaninet:download-already-downloaded', { detail: existing }));
            if (!this.isNativeEnvironment()) {
                await this.exportToDevice(existing.id);
            }
            return existing;
        }

        // Add to queue
        return this.queue.enqueue(options);
    }

    /**
     * Executor called by DownloadQueue for each active slot
     * @param {DownloadQueueItem} item 
     */
    async executeDownload(item) {
        if (this.isNativeEnvironment()) {
            return await this.executeNativeDownload(item);
        } else {
            return await this.executeWebDownload(item);
        }
    }

    /**
     * Native Capacitor Download: Downloads directly to disk via native background engine
     */
    async executeNativeDownload(item) {
        const { Filesystem } = window.Capacitor.Plugins;
        const Directory = window.Capacitor.Plugins.Filesystem.Directory || { Data: 'DATA' };

        const safeFilename = item.filename.replace(/[^a-zA-Z0-9._-]/g, '_');
        const relativePath = `pwaninet_downloads/${item.id}_${safeFilename}`;

        // Ensure downloads directory exists
        try {
            await Filesystem.mkdir({
                path: 'pwaninet_downloads',
                directory: Directory.Data,
                recursive: true
            });
        } catch (e) {
            // Already exists or ignore
        }

        let progressListener = null;

        try {
            // Capacitor Filesystem downloadFile
            const downloadOptions = {
                url: item.url,
                path: relativePath,
                directory: Directory.Data,
                progress: true,
                recursive: true
            };

            // Setup progress listener
            if (typeof Filesystem.addListener === 'function') {
                progressListener = await Filesystem.addListener('progress', (status) => {
                    if (status.url === item.url && status.contentLength > 0) {
                        const pct = (status.bytes / status.contentLength) * 100;
                        this.queue.updateProgress(item.id, {
                            progress: pct,
                            bytesDownloaded: status.bytes,
                            totalBytes: status.contentLength
                        });
                    }
                });
            }

            const downloadResult = await Filesystem.downloadFile(downloadOptions);

            if (progressListener && typeof progressListener.remove === 'function') {
                progressListener.remove();
            }

            // Get absolute file URI
            const uriResult = await Filesystem.getUri({
                path: relativePath,
                directory: Directory.Data
            });

            // Capacitor keeps its offline copy privately; also publish media to
            // Android's shared Photos/Gallery collection when the native bridge supports it.
            let gallerySaved = null;
            let galleryError = null;
            const mediaCategory = item.mediaType || item.category;
            const nativeBridge = window.AndroidBridge || window.pwaninetBridge;
            if (['image', 'video'].includes(mediaCategory)) {
                gallerySaved = false;
                if (nativeBridge?.saveMediaToGallery) {
                    try {
                        gallerySaved = nativeBridge.saveMediaToGallery(
                            uriResult.uri,
                            item.filename,
                            item.mimeType || (mediaCategory === 'video' ? 'video/mp4' : 'image/jpeg'),
                            mediaCategory
                        ) === true;
                        if (!gallerySaved) galleryError = 'The Android app could not publish the offline file to Gallery.';
                    } catch (error) {
                        galleryError = error.message || 'The Android app could not publish the offline file to Gallery.';
                        console.warn('[DownloadManager] Could not publish media to gallery:', error);
                    }
                } else {
                    galleryError = 'The Android Gallery bridge is unavailable. Rebuild and reinstall the app, then retry.';
                    console.error('[DownloadManager] AndroidBridge.saveMediaToGallery is unavailable for this native media download.');
                }
            }

            // Get file stat for true byte size
            let fileSize = item.totalBytes || 0;
            try {
                const stat = await Filesystem.stat({
                    path: relativePath,
                    directory: Directory.Data
                });
                fileSize = stat.size || fileSize;
            } catch (statErr) {
                console.warn('[DownloadManager] Could not stat file:', statErr);
            }

            // Cache thumbnail as Base64 Data URL so it is fully offline-accessible
            let cachedNativeThumbnail = item.thumbnail || '';
            if (cachedNativeThumbnail && !cachedNativeThumbnail.startsWith('data:') && !cachedNativeThumbnail.startsWith('blob:')) {
                try {
                    cachedNativeThumbnail = await this._resolveThumbnailDataUrl(cachedNativeThumbnail);
                } catch (thumbErr) {
                    console.warn('[DownloadManager] Native thumbnail caching failed:', thumbErr);
                }
            }

            const metadata = {
                id: item.id,
                url: item.url,
                filename: item.filename,
                category: item.category,
                mimeType: item.mimeType,
                size: fileSize,
                downloadedAt: Date.now(),
                postId: item.postId,
                mediaId: item.mediaId,
                thumbnail: cachedNativeThumbnail,
                isNative: true,
                nativePath: relativePath,
                nativeUri: uriResult.uri,
                gallerySaved,
                galleryError
            };

            await this.storage.saveMetadata(metadata);
            this.queue.markCompleted(item.id, metadata);
            return metadata;

        } catch (err) {
            if (progressListener && typeof progressListener.remove === 'function') {
                progressListener.remove();
            }
            console.error('[DownloadManager] Native download failed:', err);
            this.queue.markFailed(item.id, err.message || 'Native download failed');
            throw err;
        }
    }

    /**
     * Web / PWA Download: Fetches via streams and saves Blob into IndexedDB
     */
    async executeWebDownload(item) {
        try {
            const response = await fetch(item.url, {
                signal: item.cancelController.signal
            });

            if (!response.ok) {
                throw new Error(`HTTP error ${response.status}: ${response.statusText}`);
            }

            const contentLength = parseInt(response.headers.get('content-length') || '0', 10);
            const contentType = response.headers.get('content-type') || item.mimeType;

            let blob;

            // Stream reading for accurate progress updates
            if (response.body && typeof response.body.getReader === 'function' && contentLength > 0) {
                const reader = response.body.getReader();
                let receivedBytes = 0;
                const chunks = [];

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;

                    chunks.push(value);
                    receivedBytes += value.length;

                    const pct = (receivedBytes / contentLength) * 100;
                    this.queue.updateProgress(item.id, {
                        progress: pct,
                        bytesDownloaded: receivedBytes,
                        totalBytes: contentLength
                    });
                }

                blob = new Blob(chunks, { type: contentType });
            } else {
                // Fallback for responses without content-length
                this.queue.updateProgress(item.id, { progress: 50 });
                blob = await response.blob();
            }

            // Save binary blob to IndexedDB
            await this.storage.saveBlob(item.id, blob);

            // Cache thumbnail as Base64 Data URL so it is fully offline-accessible
            let cachedWebThumbnail = item.thumbnail || '';
            if (cachedWebThumbnail && !cachedWebThumbnail.startsWith('data:') && !cachedWebThumbnail.startsWith('blob:')) {
                try {
                    cachedWebThumbnail = await this._resolveThumbnailDataUrl(cachedWebThumbnail);
                } catch (thumbErr) {
                    console.warn('[DownloadManager] Web thumbnail caching failed:', thumbErr);
                }
            }

            const metadata = {
                id: item.id,
                url: item.url,
                filename: item.filename,
                category: item.category,
                mimeType: blob.type || contentType || item.mimeType,
                size: blob.size,
                downloadedAt: Date.now(),
                postId: item.postId,
                mediaId: item.mediaId,
                thumbnail: cachedWebThumbnail,
                isNative: false,
                nativePath: null,
                nativeUri: null
            };

            // Save metadata
            await this.storage.saveMetadata(metadata);
            this.queue.markCompleted(item.id, metadata);

            // Trigger native browser download to user's device Downloads
            try {
                const blobUrl = URL.createObjectURL(blob);
                const a = document.createElement('a');
                a.style.display = 'none';
                a.href = blobUrl;
                a.download = item.filename || 'download';
                document.body.appendChild(a);
                a.click();
                setTimeout(() => {
                    if (a.parentNode) {
                        a.parentNode.removeChild(a);
                    }
                    URL.revokeObjectURL(blobUrl);
                }, 5000);
            } catch (downloadErr) {
                console.warn('[DownloadManager] Native browser file download trigger failed:', downloadErr);
            }

            return metadata;

        } catch (err) {
            if (err.name === 'AbortError') {
                console.log('[DownloadManager] Download aborted by user:', item.id);
                return;
            }
            console.error('[DownloadManager] Web download failed:', err);
            this.queue.markFailed(item.id, err.message || 'Web download failed');
            throw err;
        }
    }

    /**
     * Get streamable or viewable media URL for offline playback
     * Handles Capacitor native convertFileSrc vs Web Blob URL
     * @param {Object|string} itemOrId 
     * @returns {Promise<string>}
     */
    async getMediaSrc(itemOrId) {
        await this.initialize();
        const metadata = typeof itemOrId === 'string' ? await this.storage.getMetadata(itemOrId) : itemOrId;
        if (!metadata) return null;

        // 1. Native Capacitor file
        if (metadata.isNative && metadata.nativeUri) {
            if (window.Capacitor && typeof window.Capacitor.convertFileSrc === 'function') {
                return window.Capacitor.convertFileSrc(metadata.nativeUri);
            }
            return metadata.nativeUri;
        }

        // 2. Web / PWA IndexedDB Blob
        const blob = await this.storage.getBlob(metadata.id);
        if (blob) {
            return URL.createObjectURL(blob);
        }

        // 3. Fallback to original online URL if available
        return metadata.url;
    }

    /**
     * Delete downloaded media
     */
    async delete(id) {
        await this.initialize();
        const metadata = await this.storage.getMetadata(id);

        if (metadata && metadata.isNative && metadata.nativePath && this.isNativeEnvironment()) {
            try {
                const { Filesystem } = window.Capacitor.Plugins;
                const Directory = window.Capacitor.Plugins.Filesystem.Directory || { Data: 'DATA' };
                await Filesystem.deleteFile({
                    path: metadata.nativePath,
                    directory: Directory.Data
                });
            } catch (e) {
                console.warn('[DownloadManager] Error deleting native file:', e);
            }
        }

        await this.storage.deleteDownload(id);
        window.dispatchEvent(new CustomEvent('pwaninet:download-deleted', { detail: { id } }));
        return true;
    }

    /**
     * Get all downloaded items
     */
    async getAllDownloads(category = null) {
        await this.initialize();
        return await this.storage.getAllMetadata(category);
    }

    /**
     * Search downloads by query
     */
    searchDownloads(downloads, query) {
        if (!query) return downloads;
        const q = query.toLowerCase().trim();
        return downloads.filter(d => 
            (d.filename && d.filename.toLowerCase().includes(q)) ||
            (d.category && d.category.toLowerCase().includes(q))
        );
    }

    /**
     * Sort downloads
     */
    sortDownloads(downloads, sortBy = 'newest') {
        const sorted = [...downloads];
        switch (sortBy) {
            case 'oldest':
                return sorted.sort((a, b) => (a.downloadedAt || 0) - (b.downloadedAt || 0));
            case 'largest':
                return sorted.sort((a, b) => (b.size || 0) - (a.size || 0));
            case 'smallest':
                return sorted.sort((a, b) => (a.size || 0) - (b.size || 0));
            case 'az':
                return sorted.sort((a, b) => (a.filename || '').localeCompare(b.filename || ''));
            case 'newest':
            default:
                return sorted.sort((a, b) => (b.downloadedAt || 0) - (a.downloadedAt || 0));
        }
    }

    /**
     * Open / play a downloaded file
     */
    async open(id) {
        await this.initialize();
        const metadata = await this.storage.getMetadata(id);
        if (!metadata) throw new Error('Download metadata not found');

        const src = await this.getMediaSrc(metadata);
        if (!src) throw new Error('Could not resolve media source');

        if (metadata.category === 'video' || metadata.category === 'audio' || metadata.category === 'image') {
            // Dispatch event to open in viewer modal
            window.dispatchEvent(new CustomEvent('pwaninet:open-media-viewer', {
                detail: { metadata, src }
            }));
        } else {
            // Open document in new tab or external viewer
            const link = document.createElement('a');
            link.href = src;
            link.download = metadata.filename;
            link.target = '_blank';
            document.body.appendChild(link);
            link.click();
            setTimeout(() => link.remove(), 200);
        }
    }

    /**
     * Export a downloaded file to the phone's Downloads/Public storage or save dialog
     */
    async exportToDevice(id) {
        await this.initialize();
        const metadata = await this.storage.getMetadata(id);
        if (!metadata) throw new Error('Download metadata not found');

        // 1. If Native Capacitor
        if (metadata.isNative && metadata.nativeUri) {
            if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Share) {
                try {
                    await window.Capacitor.Plugins.Share.share({
                        title: metadata.filename,
                        url: metadata.nativeUri,
                        dialogTitle: `Save or open ${metadata.filename}`
                    });
                    return;
                } catch (e) {
                    if (e.message !== 'Share canceled') {
                        console.warn('[DownloadManager] Capacitor export share failed:', e);
                    } else {
                        return;
                    }
                }
            }
        }

        // 2. Web / PWA or fallback: Trigger standard browser file save to device
        const blob = await this.storage.getBlob(metadata.id);
        const url = blob ? URL.createObjectURL(blob) : metadata.url;
        const a = document.createElement('a');
        a.href = url;
        a.download = metadata.filename || 'download';
        document.body.appendChild(a);
        a.click();
        setTimeout(() => {
            a.remove();
            if (blob) URL.revokeObjectURL(url);
        }, 1000);
    }

    /**
     * Share a downloaded file
     */
    async share(id) {
        await this.initialize();
        const metadata = await this.storage.getMetadata(id);
        if (!metadata) throw new Error('Download not found');

        // Try native Capacitor Share
        if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Share) {
            try {
                await window.Capacitor.Plugins.Share.share({
                    title: metadata.filename,
                    text: `Shared from pwaninet: ${metadata.filename}`,
                    url: metadata.nativeUri || metadata.url,
                    dialogTitle: `Share ${metadata.filename}`
                });
                return;
            } catch (e) {
                if (e.message !== 'Share canceled') {
                    console.warn('[DownloadManager] Capacitor share failed, falling back:', e);
                } else {
                    return;
                }
            }
        }

        // Web Share API
        if (navigator.share) {
            try {
                await navigator.share({
                    title: metadata.filename,
                    text: `Shared from pwaninet: ${metadata.filename}`,
                    url: metadata.url
                });
                return;
            } catch (e) {
                if (e.name !== 'AbortError') {
                    console.warn('[DownloadManager] Web Share failed:', e);
                } else {
                    return;
                }
            }
        }

        // Fallback: copy link
        if (navigator.clipboard) {
            await navigator.clipboard.writeText(metadata.url);
            window.dispatchEvent(new CustomEvent('pwaninet:show-toast', {
                detail: { message: 'Link copied to clipboard!' }
            }));
        }
    }

    /**
     * Resolves a thumbnail URL to a Base64 Data URL so it is fully accessible offline
     * @param {string} thumbUrl
     * @returns {Promise<string>}
     */
    async _resolveThumbnailDataUrl(thumbUrl) {
        if (!thumbUrl || typeof thumbUrl !== 'string') return '';
        if (thumbUrl.startsWith('data:') || thumbUrl.startsWith('blob:')) return thumbUrl;
        try {
            const resp = await fetch(thumbUrl);
            if (!resp.ok) return thumbUrl;
            const blob = await resp.blob();
            return new Promise((resolve) => {
                const reader = new FileReader();
                reader.onloadend = () => resolve(reader.result || thumbUrl);
                reader.onerror = () => resolve(thumbUrl);
                reader.readAsDataURL(blob);
            });
        } catch (e) {
            console.warn('[DownloadManager] Could not convert thumbnail to data URL:', e);
            return thumbUrl;
        }
    }
}

export const downloadManager = new DownloadManager();
