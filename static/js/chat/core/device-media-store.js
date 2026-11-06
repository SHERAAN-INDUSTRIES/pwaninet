/**
 * DeviceMediaStore - Unified Local Storage for Chat Media
 * Coordinates:
 *   - Native Capacitor direct-to-disk storage (@capacitor/filesystem on Android / iOS)
 *   - Web / Desktop IndexedDB binary Blob storage (vasto_downloads_v1)
 *   - Persistent storage protection via navigator.storage.persist()
 *   - Local source resolution (Capacitor.convertFileSrc vs URL.createObjectURL)
 *   - In-memory key tracking synchronized with IndexedDB
 *   - Event-driven cache eviction on media deletion
 *   - Browser blob downloads to disk for desktop users
 */

import { downloadStorage } from '../../downloads/download_storage.js?v=20261106-group1';

class DeviceMediaStore {
    constructor() {
        this.storage = downloadStorage;
        this.isInitialized = false;
        this.blobUrlCache = new Map(); // messageId -> objectUrl
        this.downloadedKeys = new Set(); // in-memory set of stored keys ('chat_123', etc.)
        this._setupEventListeners();
    }

    /**
     * Listen for storage deletion events to evict stale memory/cache
     */
    _setupEventListeners() {
        if (typeof window === 'undefined') return;

        window.addEventListener('vasto:download-deleted', (e) => {
            const rawId = e.detail?.id;
            if (rawId) {
                const messageId = String(rawId).replace(/^chat_/, '');
                this.evict(messageId);
            }
        });

        window.addEventListener('vasto:media-deleted', (e) => {
            const rawId = e.detail?.id || e.detail?.messageId;
            if (rawId) {
                const messageId = String(rawId).replace(/^chat_/, '');
                this.evict(messageId);
            }
        });
    }

    /**
     * Check if running in native Capacitor environment with Filesystem plugin
     */
    isNative() {
        return typeof window !== 'undefined' &&
               Boolean(window.Capacitor && 
                       typeof window.Capacitor.isNativePlatform === 'function' &&
                       window.Capacitor.isNativePlatform() &&
                       window.Capacitor.Plugins &&
                       window.Capacitor.Plugins.Filesystem);
    }

    /**
     * Initialize media storage, populate stored keys, and request persistent quota on Web
     */
    async init() {
        if (this.isInitialized) return;

        // Request persistent storage on Web/PWA to prevent browser eviction
        if (typeof navigator !== 'undefined' && navigator.storage && typeof navigator.storage.persist === 'function') {
            try {
                const isPersisted = await navigator.storage.persisted();
                if (!isPersisted) {
                    const granted = await navigator.storage.persist();
                    console.log('[DEVICE_MEDIA_STORE] Persistent storage requested, granted:', granted);
                }
            } catch (e) {
                console.warn('[DEVICE_MEDIA_STORE] Storage persist request failed:', e);
            }
        }

        try {
            await this.storage.init();
            
            // Populate in-memory downloaded keys from IndexedDB metadata
            const allItems = await this.storage.getAllMetadata();
            this.downloadedKeys.clear();
            if (Array.isArray(allItems)) {
                allItems.forEach(item => {
                    if (item && item.id) {
                        this.downloadedKeys.add(String(item.id));
                        // Also index without chat_ prefix for direct lookup
                        const plainId = String(item.id).replace(/^chat_/, '');
                        this.downloadedKeys.add(plainId);
                    }
                });
            }

            this.isInitialized = true;
            console.log('[DEVICE_MEDIA_STORE] Initialized. Stored items:', allItems.length, 'Native:', this.isNative());
        } catch (err) {
            console.error('[DEVICE_MEDIA_STORE] Init failed:', err);
        }
    }

    /**
     * Evict message from in-memory cache and localStorage, and notify app
     */
    evict(messageId) {
        if (!messageId) return;
        const strId = String(messageId);
        const safeKey = `chat_${strId}`;

        this.downloadedKeys.delete(safeKey);
        this.downloadedKeys.delete(strId);

        if (this.blobUrlCache.has(strId)) {
            try { URL.revokeObjectURL(this.blobUrlCache.get(strId)); } catch (_) {}
            this.blobUrlCache.delete(strId);
        }
        if (this.blobUrlCache.has(safeKey)) {
            try { URL.revokeObjectURL(this.blobUrlCache.get(safeKey)); } catch (_) {}
            this.blobUrlCache.delete(safeKey);
        }

        try {
            localStorage.removeItem(`media_dl_${strId}`);
            localStorage.removeItem(`media_dl_${safeKey}`);
        } catch (_) {}

        console.log('[DEVICE_MEDIA_STORE] Evicted media:', strId);
        if (typeof window !== 'undefined') {
            window.dispatchEvent(new CustomEvent('vasto:media-evicted', { detail: { messageId: strId } }));
        }
    }

    /**
     * Check if a message's media is already downloaded or stored locally in IndexedDB / Filesystem
     */
    hasMedia(messageId) {
        if (!messageId) return false;
        const strId = String(messageId);
        const safeKey = `chat_${strId}`;

        if (this.downloadedKeys.has(safeKey) || this.downloadedKeys.has(strId)) {
            return true;
        }

        // Group bundles are stored per-tile (chat_<id>_0, ...); bundle counts
        // as downloaded when the first tile key exists.
        if (this.downloadedKeys.has(`${safeKey}_0`) || this.downloadedKeys.has(`${strId}_0`)) {
            return true;
        }

        // localStorage persists across reloads; always consult it, not just pre-init.
        try {
            if (localStorage.getItem(`media_dl_${strId}`) === 'true') return true;
            if (localStorage.getItem(`media_dl_${safeKey}`) === 'true') return true;
        } catch (_) {}

        return false;
    }

    /**
     * Save sender's original media blob locally so they never have to download what they sent
     */
    async saveSenderMedia(tempId, blob, filename = 'media') {
        if (!tempId || !blob) return null;
        await this.init();

        const strId = String(tempId);
        const safeKey = `chat_${strId}`;

        try {
            if (this.isNative()) {
                // On Capacitor, write directly to native app data directory
                const { Filesystem } = window.Capacitor.Plugins;
                const Directory = window.Capacitor.Plugins.Filesystem.Directory || { Data: 'DATA' };
                const safeName = (filename || 'media').replace(/[^a-zA-Z0-9._-]/g, '_');
                const relativePath = `vasto_chat_media/${strId}_${safeName}`;

                try {
                    await Filesystem.mkdir({
                        path: 'vasto_chat_media',
                        directory: Directory.Data,
                        recursive: true
                    });
                } catch (_) {}

                // Convert blob to base64 for native write
                const reader = new FileReader();
                const base64Data = await new Promise((resolve, reject) => {
                    reader.onloadend = () => {
                        const res = reader.result;
                        const base64 = typeof res === 'string' ? res.split(',')[1] : '';
                        resolve(base64);
                    };
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });

                await Filesystem.writeFile({
                    path: relativePath,
                    data: base64Data,
                    directory: Directory.Data,
                    recursive: true
                });

                const uriRes = await Filesystem.getUri({
                    path: relativePath,
                    directory: Directory.Data
                });

                await this.storage.saveMetadata({
                    id: safeKey,
                    isNative: true,
                    nativePath: relativePath,
                    nativeUri: uriRes.uri,
                    filename: safeName,
                    size: blob.size,
                    downloadedAt: Date.now()
                });

                this.downloadedKeys.add(safeKey);
                this.downloadedKeys.add(strId);
                localStorage.setItem(`media_dl_${strId}`, 'true');

                const convertedSrc = window.Capacitor.convertFileSrc(uriRes.uri);
                this.blobUrlCache.set(strId, convertedSrc);
                return convertedSrc;
            } else {
                // On Web, save binary blob directly into IndexedDB
                await this.storage.saveBlob(safeKey, blob);
                await this.storage.saveMetadata({
                    id: safeKey,
                    isNative: false,
                    filename,
                    size: blob.size,
                    downloadedAt: Date.now()
                });

                this.downloadedKeys.add(safeKey);
                this.downloadedKeys.add(strId);

                const objUrl = URL.createObjectURL(blob);
                this.blobUrlCache.set(strId, objUrl);
                localStorage.setItem(`media_dl_${strId}`, 'true');
                return objUrl;
            }
        } catch (err) {
            console.error('[DEVICE_MEDIA_STORE] Save sender media error:', err);
            return null;
        }
    }

    /**
     * Transfer stored media from optimistic tempId to permanent messageId
     */
    async rekeyMedia(tempId, messageId) {
        if (!tempId || !messageId || tempId === messageId) return;
        await this.init();

        const oldStrId = String(tempId);
        const newStrId = String(messageId);
        const oldKey = `chat_${oldStrId}`;
        const newKey = `chat_${newStrId}`;

        try {
            const meta = await this.storage.getMetadata(oldKey);
            if (meta) {
                meta.id = newKey;
                await this.storage.saveMetadata(meta);
            }

            const blob = await this.storage.getBlob(oldKey);
            if (blob) {
                await this.storage.saveBlob(newKey, blob);
            }

            this.downloadedKeys.add(newKey);
            this.downloadedKeys.add(newStrId);

            if (this.blobUrlCache.has(oldStrId)) {
                this.blobUrlCache.set(newStrId, this.blobUrlCache.get(oldStrId));
            }

            localStorage.setItem(`media_dl_${newStrId}`, 'true');
            console.log('[DEVICE_MEDIA_STORE] Rekeyed media:', oldStrId, '->', newStrId);
        } catch (err) {
            console.warn('[DEVICE_MEDIA_STORE] Rekey media error:', err);
        }
    }

    /**
     * Fetch media blob using direct fetch, proxy, or relative media path
     * @private
    /**
     * Download blob with progress callback using XMLHttpRequest
     */
    _fetchBlobWithProgress(fetchUrl, onProgress = null) {
        return new Promise((resolve) => {
            try {
                const xhr = new XMLHttpRequest();
                xhr.open('GET', fetchUrl, true);
                xhr.responseType = 'blob';
                if (onProgress) {
                    xhr.onprogress = (e) => {
                        if (e.lengthComputable && e.total > 0) {
                            const percent = Math.min(100, Math.max(1, Math.round((e.loaded / e.total) * 100)));
                            onProgress(percent, e.loaded, e.total);
                        }
                    };
                }
                xhr.onload = () => {
                    if (xhr.status >= 200 && xhr.status < 300 && xhr.response) {
                        if (onProgress) onProgress(100);
                        resolve(xhr.response);
                    } else {
                        resolve(null);
                    }
                };
                xhr.onerror = () => resolve(null);
                xhr.ontimeout = () => resolve(null);
                xhr.send();
            } catch (err) {
                console.warn('[DEVICE_MEDIA_STORE] XHR fetch error:', err);
                resolve(null);
            }
        });
    }

    /**
     * Fetch media blob safely with fallback support and real progress callback
     */
    async _fetchMediaBlob(url, onProgress = null) {
        if (!url) return null;
        let blob = null;

        // If URL is from CDN (cdn.pwaninet.app) or cross-origin, browsers block direct fetch
        // with CORS when accessed from localhost/LAN. Prioritize same-origin proxy.
        const isCdn = url.includes('cdn.pwaninet.app') || (url.startsWith('http') && typeof window !== 'undefined' && !url.startsWith(window.location.origin));

        if (isCdn) {
            // 1. Fetch via same-origin media proxy first
            try {
                const proxyUrl = `/messaging/api/media-proxy/?url=${encodeURIComponent(url)}`;
                blob = await this._fetchBlobWithProgress(proxyUrl, onProgress);
                if (blob) {
                    console.log('[DEVICE_MEDIA_STORE] Media fetched via proxy, size:', blob.size);
                    return blob;
                }
            } catch (proxyErr) {
                console.warn('[DEVICE_MEDIA_STORE] Proxy media fetch failed:', proxyErr);
            }
        }

        // 2. Direct fetch with progress
        if (!blob) {
            try {
                blob = await this._fetchBlobWithProgress(url, onProgress);
                if (blob) return blob;
            } catch (err) {
                console.warn('[DEVICE_MEDIA_STORE] Direct fetch failed:', err);
            }
        }

        // 3. Fallback: try proxy if not already attempted
        if (!blob && !isCdn) {
            try {
                const proxyUrl = `/messaging/api/media-proxy/?url=${encodeURIComponent(url)}`;
                blob = await this._fetchBlobWithProgress(proxyUrl, onProgress);
                if (blob) return blob;
            } catch (proxyErr) {
                console.warn('[DEVICE_MEDIA_STORE] Proxy media fetch fallback failed:', proxyErr);
            }
        }

        // 4. Fallback: try same-origin relative /media/ path
        if (!blob && url.includes('/media/')) {
            const relativeUrl = url.substring(url.indexOf('/media/'));
            try {
                blob = await this._fetchBlobWithProgress(relativeUrl, onProgress);
                if (blob) return blob;
            } catch (relErr) {
                console.warn('[DEVICE_MEDIA_STORE] Relative media fetch failed:', relErr);
            }
        }

        return blob;
    }

    /**
     * Download media from CDN/server and save permanently to local device storage (IndexedDB or Capacitor Filesystem)
     * Strictly fails with Error if media cannot be downloaded into local storage (NO fallback to streaming from CDN).
     */
    async downloadMedia(messageId, url, filename = 'download', onProgress = null) {
        if (!url) throw new Error('Download URL is required');
        await this.init();

        const strId = String(messageId);
        const safeKey = `chat_${strId}`;
        const safeFilename = (filename || 'download').replace(/[^a-zA-Z0-9._-]/g, '_');

        if (this.isNative()) {
            // Native Capacitor direct-to-disk background download
            const { Filesystem } = window.Capacitor.Plugins;
            const Directory = window.Capacitor.Plugins.Filesystem.Directory || { Data: 'DATA' };
            const relativePath = `vasto_chat_media/${strId}_${safeFilename}`;

            try {
                await Filesystem.mkdir({
                    path: 'vasto_chat_media',
                    directory: Directory.Data,
                    recursive: true
                });
            } catch (_) {}

            let fileDownloaded = false;

            // Try Filesystem.downloadFile if supported
            if (typeof Filesystem.downloadFile === 'function') {
                try {
                    const absoluteUrl = new URL(url, window.location.href).href;
                    await Filesystem.downloadFile({
                        url: absoluteUrl,
                        path: relativePath,
                        directory: Directory.Data,
                        recursive: true
                    });
                    fileDownloaded = true;
                } catch (dlErr) {
                    console.warn('[DEVICE_MEDIA_STORE] Native downloadFile failed, trying fetch+writeFile fallback:', dlErr);
                }
            }

            // Fallback: fetch blob and write to native filesystem
            if (!fileDownloaded) {
                const blob = await this._fetchMediaBlob(url, onProgress);
                if (!blob) {
                    throw new Error(`Failed to download media from server (${url})`);
                }

                const reader = new FileReader();
                const base64Data = await new Promise((resolve, reject) => {
                    reader.onloadend = () => {
                        const res = reader.result;
                        const base64 = typeof res === 'string' ? res.split(',')[1] : '';
                        resolve(base64);
                    };
                    reader.onerror = reject;
                    reader.readAsDataURL(blob);
                });

                await Filesystem.writeFile({
                    path: relativePath,
                    data: base64Data,
                    directory: Directory.Data,
                    recursive: true
                });
            }

            const uriRes = await Filesystem.getUri({
                path: relativePath,
                directory: Directory.Data
            });

            await this.storage.saveMetadata({
                id: safeKey,
                isNative: true,
                nativePath: relativePath,
                nativeUri: uriRes.uri,
                url,
                filename: safeFilename,
                downloadedAt: Date.now()
            });

            this.downloadedKeys.add(safeKey);
            this.downloadedKeys.add(strId);
            localStorage.setItem(`media_dl_${strId}`, 'true');

            const convertedSrc = window.Capacitor.convertFileSrc(uriRes.uri);
            this.blobUrlCache.set(strId, convertedSrc);
            return convertedSrc;
        } else {
            // Web / Desktop IndexedDB blob download
            const blob = await this._fetchMediaBlob(url, onProgress);
            if (!blob) {
                throw new Error(`Failed to download media blob from server (${url})`);
            }

            await this.storage.saveBlob(safeKey, blob);
            await this.storage.saveMetadata({
                id: safeKey,
                isNative: false,
                url,
                filename: safeFilename,
                size: blob.size,
                downloadedAt: Date.now()
            });

            this.downloadedKeys.add(safeKey);
            this.downloadedKeys.add(strId);

            const objUrl = URL.createObjectURL(blob);
            this.blobUrlCache.set(strId, objUrl);
            localStorage.setItem(`media_dl_${strId}`, 'true');
            console.log('[DEVICE_MEDIA_STORE] Media saved to IndexedDB:', strId, 'size:', blob.size);
            return objUrl;
        }
    }

    /**
     * Get local source URL for a media item stored in IndexedDB or Filesystem.
     * If the item was deleted from IndexedDB/disk, returns null and cleans up stale cache.
     */
    async getLocalMediaUrl(messageId) {
        if (!messageId) return null;
        const strId = String(messageId);

        // Check active in-memory cache first
        if (this.blobUrlCache.has(strId)) {
            return this.blobUrlCache.get(strId);
        }

        await this.init();
        const safeKey = `chat_${strId}`;

        try {
            const meta = await this.storage.getMetadata(safeKey);
            if (meta && meta.isNative && meta.nativeUri) {
                // Verify native file actually still exists on disk
                if (this.isNative() && meta.nativePath) {
                    try {
                        const { Filesystem } = window.Capacitor.Plugins;
                        const Directory = window.Capacitor.Plugins.Filesystem.Directory || { Data: 'DATA' };
                        await Filesystem.stat({
                            path: meta.nativePath,
                            directory: Directory.Data
                        });
                    } catch (statErr) {
                        // File was deleted from disk!
                        console.warn('[DEVICE_MEDIA_STORE] Native file missing from disk:', meta.nativePath);
                        this.evict(strId);
                        return null;
                    }
                }
                if (window.Capacitor && typeof window.Capacitor.convertFileSrc === 'function') {
                    const converted = window.Capacitor.convertFileSrc(meta.nativeUri);
                    this.blobUrlCache.set(strId, converted);
                    return converted;
                }
                return meta.nativeUri;
            }

            // Retrieve binary blob from IndexedDB
            const blob = await this.storage.getBlob(safeKey);
            if (blob) {
                const objUrl = URL.createObjectURL(blob);
                this.blobUrlCache.set(strId, objUrl);
                this.downloadedKeys.add(safeKey);
                this.downloadedKeys.add(strId);
                return objUrl;
            } else {
                // Blob was deleted from IndexedDB! Evict to bring back download overlay!
                console.log('[DEVICE_MEDIA_STORE] Media not found in IndexedDB, evicting:', strId);
                this.evict(strId);
                return null;
            }
        } catch (e) {
            console.warn('[DEVICE_MEDIA_STORE] Get local media error:', e);
            this.evict(strId);
            return null;
        }
    }

    /**
     * Delete media record and blob from IndexedDB / Filesystem
     */
    async deleteMedia(messageId) {
        if (!messageId) return;
        const strId = String(messageId);
        const safeKey = `chat_${strId}`;
        await this.init();

        try {
            await this.storage.deleteDownload(safeKey);
        } catch (e) {
            console.warn('[DEVICE_MEDIA_STORE] Delete download error:', e);
        }

        this.evict(strId);
    }

    /**
     * Download to Desktop / Mobile Device Disk (Chrome blob download or native share)
     * Triggers file download straight into the user's Downloads folder
     */
    async downloadToDevice(blobOrUrl, filename = 'download') {
        const safeFilename = filename.replace(/[^a-zA-Z0-9._-]/g, '_');

        if (this.isNative()) {
            if (window.Capacitor.Plugins && window.Capacitor.Plugins.Share) {
                try {
                    await window.Capacitor.Plugins.Share.share({
                        title: safeFilename,
                        url: typeof blobOrUrl === 'string' ? blobOrUrl : undefined
                    });
                    return;
                } catch (e) {
                    console.warn('[DEVICE_MEDIA_STORE] Native share failed:', e);
                }
            }
        }

        // Desktop / Web: Chrome blob download to user's PC Downloads folder
        try {
            let blobUrl = '';
            let shouldRevoke = false;

            if (blobOrUrl instanceof Blob) {
                blobUrl = URL.createObjectURL(blobOrUrl);
                shouldRevoke = true;
            } else if (typeof blobOrUrl === 'string') {
                if (blobOrUrl.startsWith('blob:')) {
                    blobUrl = blobOrUrl;
                } else {
                    const res = await fetch(blobOrUrl, { mode: 'cors' });
                    const blob = await res.blob();
                    blobUrl = URL.createObjectURL(blob);
                    shouldRevoke = true;
                }
            }

            if (!blobUrl) return;

            const a = document.createElement('a');
            a.style.display = 'none';
            a.href = blobUrl;
            a.download = safeFilename;
            document.body.appendChild(a);
            a.click();
            document.body.removeChild(a);

            if (shouldRevoke) {
                setTimeout(() => URL.revokeObjectURL(blobUrl), 2000);
            }
        } catch (err) {
            console.error('[DEVICE_MEDIA_STORE] Download to device failed:', err);
            if (typeof blobOrUrl === 'string') {
                window.open(blobOrUrl, '_blank');
            }
        }
    }
}

export const deviceMediaStore = new DeviceMediaStore();
