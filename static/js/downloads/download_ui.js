/**
 * vasto Unified Download UI (Floating Banner)
 * Matches the design pattern, animations, themes, and responsiveness of vasto's Upload Banner.
 * Stacks intelligently above the upload banner if both are active.
 */

import { downloadQueue, DownloadStatus } from './download_queue.js?v=20261106-group1';
import { downloadStorage } from './download_storage.js?v=20261106-group1';

class DownloadUI {
    constructor() {
        this.banner = null;
        this.isMinimized = false;
        this.activeItem = null;
        this.hideTimeout = null;
    }

    /**
     * Initialize UI and event listeners
     */
    initialize() {
        this.injectStyles();
        this.createBanner();
        this.setupEventListeners();
        console.log('[DownloadUI] Initialized');
    }

    /**
     * Create the banner DOM element
     */
    createBanner() {
        if (document.getElementById('download-banner')) {
            this.banner = document.getElementById('download-banner');
            return;
        }

        const banner = document.createElement('div');
        banner.id = 'download-banner';
        banner.className = 'download-banner';
        banner.innerHTML = `
            <!-- Header Bar -->
            <div class="download-banner-header" id="download-banner-header">
                <div class="download-banner-badge-wrapper">
                    <span class="download-header-dot" id="download-header-dot"></span>
                    <span class="download-header-title" id="download-header-title">DOWNLOADING MEDIA</span>
                    <span class="download-header-queue-pill" id="download-header-queue-pill" style="display: none;">1 in queue</span>
                </div>
                <div class="download-header-actions">
                    <button class="download-action-btn download-minimize-btn" id="download-banner-minimize" title="Minimize / Expand">
                        <i class="bi bi-dash-lg" id="download-minimize-icon"></i>
                    </button>
                    <button class="download-action-btn download-cancel-btn" id="download-banner-cancel" title="Cancel">
                        <i class="bi bi-x-lg" id="download-cancel-icon"></i>
                    </button>
                </div>
            </div>

            <!-- Expanded Body -->
            <div class="download-banner-body" id="download-banner-body">
                <div class="download-banner-thumb-box" id="download-banner-thumb-box">
                    <div class="download-banner-thumb" id="download-banner-thumb">
                        <i class="bi bi-cloud-arrow-down" id="download-thumb-fallback"></i>
                    </div>
                </div>
                <div class="download-banner-info-col">
                    <div class="download-banner-title-row">
                        <span class="download-primary-title" id="download-banner-title">Downloading file…</span>
                        <span class="download-progress-number" id="download-banner-progress">0%</span>
                    </div>
                    <div class="download-banner-sub-row" id="download-banner-sub">
                        Starting download…
                    </div>
                </div>
            </div>

            <!-- Progress Track -->
            <div class="download-banner-progress-track" id="download-banner-progress-track">
                <div class="download-banner-progress-fill" id="download-banner-progress-fill"></div>
            </div>

            <!-- Minimized View -->
            <div class="download-mini-content" id="download-mini-content" style="display: none;">
                <div class="download-mini-left">
                    <span class="download-mini-icon" id="download-mini-icon">
                        <i class="bi bi-arrow-down-circle spin-icon"></i>
                    </span>
                    <span class="download-mini-text" id="download-mini-text">Downloading (0%)</span>
                </div>
                <button class="download-action-btn download-mini-expand-btn" id="download-mini-expand" title="Expand">
                    <i class="bi bi-chevron-up"></i>
                </button>
            </div>
        `;

        document.body.appendChild(banner);
        this.banner = banner;

        // Wire click handlers
        const cancelBtn = document.getElementById('download-banner-cancel');
        if (cancelBtn) {
            cancelBtn.onclick = (e) => {
                e.stopPropagation();
                if (this.activeItem) {
                    downloadQueue.cancel(this.activeItem.id);
                }
            };
        }

        const minimizeBtn = document.getElementById('download-banner-minimize');
        if (minimizeBtn) {
            minimizeBtn.onclick = (e) => {
                e.stopPropagation();
                this.toggleMinimize();
            };
        }

        const expandBtn = document.getElementById('download-mini-expand');
        if (expandBtn) {
            expandBtn.onclick = (e) => {
                e.stopPropagation();
                this.toggleMinimize();
            };
        }

        // Tap minimized banner to expand
        const miniContent = document.getElementById('download-mini-content');
        if (miniContent) {
            miniContent.onclick = () => {
                if (this.isMinimized) this.toggleMinimize();
            };
        }
    }

    /**
     * Listen to download events
     */
    setupEventListeners() {
        window.addEventListener('vasto:download-queued', (e) => {
            this.handleItemQueued(e.detail);
        });

        window.addEventListener('vasto:download-started', (e) => {
            this.handleItemStarted(e.detail);
        });

        window.addEventListener('vasto:download-progress', (e) => {
            this.handleItemProgress(e.detail);
        });

        window.addEventListener('vasto:download-completed', (e) => {
            this.handleItemCompleted(e.detail);
        });

        window.addEventListener('vasto:download-failed', (e) => {
            this.handleItemFailed(e.detail);
        });

        window.addEventListener('vasto:download-cancelled', (e) => {
            this.handleItemCancelled(e.detail);
        });

        window.addEventListener('vasto:download-already-downloaded', (e) => {
            const item = e.detail || {};
            this.showToast(item.gallerySaved === false
                ? (item.galleryError?.includes('bridge is unavailable')
                    ? 'This app build cannot publish downloads to Gallery. Rebuild and reinstall the app.'
                    : 'Already saved for Offline Media, but the Gallery save still failed. Tap Download to retry.')
                : item.gallerySaved === true
                    ? 'Already downloaded. Find it in Offline Media and your Gallery.'
                    : 'Already downloaded. Find it in Offline Media.', item.gallerySaved === false ? 'error' : 'info');
        });
    }

    handleItemQueued(item) {
        if (this.hideTimeout) {
            clearTimeout(this.hideTimeout);
            this.hideTimeout = null;
        }

        if (!this.activeItem) {
            this.activeItem = item;
            this.updateCardInfo(item);
        }
        this.updateQueueBadge();
        this.show();
    }

    handleItemStarted(item) {
        if (this.hideTimeout) {
            clearTimeout(this.hideTimeout);
            this.hideTimeout = null;
        }

        this.activeItem = item;
        this.updateCardInfo(item);
        this.updateProgress(item.progress || 0, item);
        this.updateQueueBadge();
        this.show();
        if (item.category === 'video') this.showToast(`Saving ${item.filename || 'video'}…`, 'info');
    }

    handleItemProgress(item) {
        if (!this.activeItem || this.activeItem.id === item.id) {
            this.activeItem = item;
            this.updateProgress(item.progress, item);
        }
        this.updateQueueBadge();
        if (item.category === 'video' && item.progress > 0) {
            this.showToast(`Saving ${item.filename || 'video'}… ${item.progress}%`, 'info');
        }
    }

    getGalleryFailureMessage(item) {
        if (item.galleryError?.includes('bridge is unavailable')) {
            return 'Gallery saving is unavailable in this app build. Rebuild and reinstall the Android app.';
        }
        return 'Gallery save failed. Tap Download again to retry.';
    }

    handleItemCompleted(item) {
        this.updateProgress(100, item);
        const titleEl = document.getElementById('download-banner-title');
        const subEl = document.getElementById('download-banner-sub');
        const fillEl = document.getElementById('download-banner-progress-fill');
        const dotEl = document.getElementById('download-header-dot');

        if (titleEl) titleEl.textContent = 'Download complete!';
        if (subEl) {
            subEl.textContent = item.gallerySaved === true
                ? `${item.filename} is ready in Offline Media and your Gallery`
                : item.isNative && ['image', 'video'].includes(item.category) && item.gallerySaved === false
                    ? `${item.filename} is ready for Offline Media; ${this.getGalleryFailureMessage(item)}`
                    : `${item.filename} is ready for offline use`;
        }
        if (item.category === 'video') {
            this.showToast(item.gallerySaved === true
                ? `${item.filename || 'Video'} saved to Gallery and Offline Media.`
                : item.isNative && item.gallerySaved === false
                    ? `${item.filename || 'Video'} saved offline; ${this.getGalleryFailureMessage(item)}`
                    : `${item.filename || 'Video'} saved for offline use.`, item.gallerySaved === false ? 'error' : 'success');
        }
        if (fillEl) fillEl.classList.add('phase-completed');
        if (dotEl) dotEl.classList.add('phase-completed');

        // Check if there are remaining active/queued items
        const activeItems = downloadQueue.getActiveDownloads();
        const queuedItems = downloadQueue.getQueuedDownloads();

        if (activeItems.length > 0) {
            // Next active item becomes primary
            setTimeout(() => {
                this.activeItem = activeItems[0];
                this.updateCardInfo(this.activeItem);
                this.updateProgress(this.activeItem.progress || 0, this.activeItem);
                this.updateQueueBadge();
            }, 1200);
        } else if (queuedItems.length > 0) {
            this.updateQueueBadge();
        } else {
            // All done, hide after 2.5s
            this.activeItem = null;
            this.hideTimeout = setTimeout(() => {
                this.hide();
            }, 2500);
        }
    }

    handleItemFailed(item) {
        const titleEl = document.getElementById('download-banner-title');
        const subEl = document.getElementById('download-banner-sub');
        const fillEl = document.getElementById('download-banner-progress-fill');
        const dotEl = document.getElementById('download-header-dot');

        if (titleEl) titleEl.textContent = 'Download failed';
        if (subEl) subEl.textContent = item.error || 'Network error';
        if (item.category === 'video') this.showToast(`Could not save ${item.filename || 'video'}: ${item.error || 'Download failed.'}`, 'error');
        if (fillEl) fillEl.classList.add('phase-error');
        if (dotEl) dotEl.classList.add('phase-error');

        const activeItems = downloadQueue.getActiveDownloads();
        if (activeItems.length > 0) {
            setTimeout(() => {
                this.activeItem = activeItems[0];
                this.updateCardInfo(this.activeItem);
                this.updateProgress(this.activeItem.progress || 0, this.activeItem);
            }, 2000);
        } else {
            this.hideTimeout = setTimeout(() => {
                this.hide();
            }, 3000);
        }
    }

    handleItemCancelled(item) {
        const titleEl = document.getElementById('download-banner-title');
        const subEl = document.getElementById('download-banner-sub');
        if (titleEl) titleEl.textContent = 'Download cancelled';
        if (subEl) subEl.textContent = '';

        const activeItems = downloadQueue.getActiveDownloads();
        if (activeItems.length > 0) {
            this.activeItem = activeItems[0];
            this.updateCardInfo(this.activeItem);
        } else {
            this.hide();
        }
    }

    updateCardInfo(item) {
        const titleEl = document.getElementById('download-banner-title');
        const thumbBox = document.getElementById('download-banner-thumb');
        const fillEl = document.getElementById('download-banner-progress-fill');
        const dotEl = document.getElementById('download-header-dot');

        if (titleEl) titleEl.textContent = item.filename;
        if (fillEl) fillEl.className = 'download-banner-progress-fill';
        if (dotEl) dotEl.className = 'download-header-dot';

        if (thumbBox) {
            if (item.thumbnail) {
                thumbBox.innerHTML = `<img src="${item.thumbnail}" alt="" style="width: 100%; height: 100%; object-fit: cover; border-radius: 8px;">`;
            } else {
                let icon = 'bi-file-earmark';
                if (item.category === 'video') icon = 'bi-camera-video';
                else if (item.category === 'audio') icon = 'bi-music-note-beamed';
                else if (item.category === 'image') icon = 'bi-image';
                thumbBox.innerHTML = `<i class="bi ${icon}"></i>`;
            }
        }
    }

    updateProgress(progress, item) {
        const pct = Math.min(100, Math.max(0, Math.round(progress)));
        const progressEl = document.getElementById('download-banner-progress');
        const fillEl = document.getElementById('download-banner-progress-fill');
        const subEl = document.getElementById('download-banner-sub');
        const miniText = document.getElementById('download-mini-text');

        if (progressEl) progressEl.textContent = `${pct}%`;
        if (fillEl) fillEl.style.width = `${pct}%`;

        if (subEl && item) {
            const format = downloadStorage.formatBytes;
            if (item.totalBytes > 0) {
                subEl.textContent = `${format(item.bytesDownloaded || 0)} of ${format(item.totalBytes)}`;
            } else {
                subEl.textContent = `Downloading ${format(item.bytesDownloaded || 0)}…`;
            }
        }

        if (miniText) miniText.textContent = `Downloading (${pct}%)`;
    }

    updateQueueBadge() {
        const queuePill = document.getElementById('download-header-queue-pill');
        const queued = downloadQueue.getQueuedDownloads().length;
        if (queuePill) {
            if (queued > 0) {
                queuePill.textContent = `+${queued} queued`;
                queuePill.style.display = 'inline-block';
            } else {
                queuePill.style.display = 'none';
            }
        }
    }

    toggleMinimize() {
        this.isMinimized = !this.isMinimized;
        const body = document.getElementById('download-banner-body');
        const header = document.getElementById('download-banner-header');
        const miniContent = document.getElementById('download-mini-content');

        if (this.isMinimized) {
            this.banner.classList.add('is-minimized');
            if (body) body.style.display = 'none';
            if (header) header.style.display = 'none';
            if (miniContent) miniContent.style.display = 'flex';
        } else {
            this.banner.classList.remove('is-minimized');
            if (body) body.style.display = 'flex';
            if (header) header.style.display = 'flex';
            if (miniContent) miniContent.style.display = 'none';
        }
    }

    show() {
        if (!this.banner) this.createBanner();
        // Check if upload banner is currently visible to offset and stack cleanly
        const uploadBanner = document.getElementById('upload-banner');
        if (uploadBanner && uploadBanner.classList.contains('visible')) {
            this.banner.classList.add('stacked-above-upload');
        } else {
            this.banner.classList.remove('stacked-above-upload');
        }
        this.banner.classList.add('visible');
    }

    hide() {
        if (this.banner) {
            this.banner.classList.remove('visible');
            this.banner.classList.remove('stacked-above-upload');
        }
    }

    showToast(message, type = 'info') {
        let toast = document.getElementById('vasto-download-status-toast');
        if (!toast) {
            toast = document.createElement('div');
            toast.id = 'vasto-download-status-toast';
            toast.setAttribute('role', 'status');
            document.body.appendChild(toast);
        }
        toast.className = `download-toast toast-${type}`;
        toast.textContent = message;
        toast.style.zIndex = '10950';
        toast.style.top = 'max(20px, env(safe-area-inset-top, 20px))';
        clearTimeout(this.toastTimeout);
        setTimeout(() => toast.classList.add('show'), 10);
        this.toastTimeout = setTimeout(() => {
            toast.classList.remove('show');
        }, type === 'info' && /%$/.test(message) ? 1200 : 3800);
    }

    injectStyles() {
        if (document.getElementById('download-banner-styles')) return;

        const style = document.createElement('style');
        style.id = 'download-banner-styles';
        style.textContent = `
            .download-banner {
                position: fixed;
                z-index: 9994;
                background: var(--mobile-nav-bg, #242526);
                backdrop-filter: blur(16px);
                -webkit-backdrop-filter: blur(16px);
                border: 1px solid var(--border, #3a3b3c);
                border-radius: 20px;
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.35), 0 2px 8px rgba(0, 0, 0, 0.15);
                overflow: hidden;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.28s cubic-bezier(0.16, 1, 0.3, 1),
                            transform 0.28s cubic-bezier(0.16, 1, 0.3, 1),
                            bottom 0.25s ease,
                            width 0.25s ease;
                font-family: inherit;
                color: var(--text-dark, #e4e6eb);
            }

            [data-theme="light"] .download-banner {
                background: var(--mobile-nav-bg, #ffffff);
                border-color: var(--border, #e2e8f0);
                color: var(--text-dark, #0f172a);
                box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12), 0 2px 6px rgba(0, 0, 0, 0.04);
            }

            [data-theme="dark"] .download-banner {
                background: var(--mobile-nav-bg, #242526);
                border-color: var(--border, #3a3b3c);
                color: var(--text-dark, #e4e6eb);
                box-shadow: 0 8px 32px rgba(0, 0, 0, 0.45), 0 2px 8px rgba(0, 0, 0, 0.2);
            }

            /* Mobile positioning */
            @media (max-width: 767.98px) {
                .download-banner {
                    bottom: calc(80px + var(--vasto-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
                    left: 50%;
                    transform: translateX(-50%) translateY(24px) scale(0.96);
                    width: 95%;
                    max-width: calc(100vw - 24px - var(--vasto-safe-area-left, env(safe-area-inset-left, 0px)) - var(--vasto-safe-area-right, env(safe-area-inset-right, 0px)));
                    border-radius: 20px;
                }
                .download-banner.visible {
                    opacity: 1;
                    pointer-events: auto;
                    transform: translateX(-50%) translateY(0) scale(1);
                }
                .download-banner.stacked-above-upload {
                    bottom: calc(180px + var(--vasto-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
                }
            }

            /* Desktop positioning */
            @media (min-width: 768px) {
                .download-banner {
                    bottom: calc(24px + var(--vasto-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
                    right: calc(28px + var(--vasto-safe-area-right, env(safe-area-inset-right, 0px)));
                    left: auto;
                    transform: translateY(24px) scale(0.96);
                    width: 380px;
                    max-width: 380px;
                    border-radius: 20px;
                }
                .download-banner.visible {
                    opacity: 1;
                    pointer-events: auto;
                    transform: translateY(0) scale(1);
                }
                .download-banner.stacked-above-upload {
                    bottom: calc(140px + var(--vasto-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
                }
                .download-banner.visible.is-minimized {
                    width: 290px;
                }
            }

            /* Header */
            .download-banner-header {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 10px 14px 8px;
            }

            .download-banner-badge-wrapper {
                display: flex;
                align-items: center;
                gap: 8px;
            }

            .download-header-dot {
                width: 8px;
                height: 8px;
                border-radius: 50%;
                background: #0284c7;
                box-shadow: 0 0 8px #0284c7;
                animation: pulse-download 1.8s infinite ease-in-out;
            }

            .download-header-dot.phase-completed {
                background: #10b981;
                box-shadow: 0 0 8px #10b981;
                animation: none;
            }

            .download-header-dot.phase-error {
                background: #ef4444;
                box-shadow: 0 0 8px #ef4444;
                animation: none;
            }

            @keyframes pulse-download {
                0%, 100% { opacity: 1; transform: scale(1); }
                50% { opacity: 0.4; transform: scale(0.85); }
            }

            .download-header-title {
                font-size: 11px;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: #0284c7;
            }

            .download-header-queue-pill {
                font-size: 10px;
                font-weight: 600;
                padding: 2px 6px;
                border-radius: 10px;
                background: rgba(2, 132, 199, 0.15);
                color: #0284c7;
            }

            .download-header-actions {
                display: flex;
                align-items: center;
                gap: 4px;
            }

            .download-action-btn {
                background: none;
                border: none;
                color: inherit;
                opacity: 0.6;
                cursor: pointer;
                padding: 4px;
                border-radius: 6px;
                display: flex;
                align-items: center;
                justify-content: center;
                transition: opacity 0.15s, background 0.15s;
            }

            .download-action-btn:hover {
                opacity: 1;
                background: rgba(128, 128, 128, 0.15);
            }

            /* Body */
            .download-banner-body {
                display: flex;
                align-items: center;
                gap: 12px;
                padding: 6px 14px 12px;
            }

            .download-banner-thumb-box {
                width: 44px;
                height: 44px;
                border-radius: 10px;
                background: rgba(2, 132, 199, 0.1);
                display: flex;
                align-items: center;
                justify-content: center;
                flex-shrink: 0;
                overflow: hidden;
            }

            .download-banner-thumb {
                width: 100%;
                height: 100%;
                display: flex;
                align-items: center;
                justify-content: center;
                font-size: 20px;
                color: #0284c7;
            }

            .download-banner-info-col {
                flex-grow: 1;
                min-width: 0;
            }

            .download-banner-title-row {
                display: flex;
                align-items: center;
                justify-content: space-between;
                gap: 8px;
            }

            .download-primary-title {
                font-size: 13px;
                font-weight: 600;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            .download-progress-number {
                font-size: 13px;
                font-weight: 700;
                color: #0284c7;
                flex-shrink: 0;
            }

            .download-banner-sub-row {
                font-size: 11px;
                opacity: 0.65;
                margin-top: 2px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }

            /* Keep the download notice compact over fullscreen reels. */
            body.reels-active .download-banner {
                border-radius: 16px;
            }

            body.reels-active .download-banner-header {
                position: absolute;
                top: 50%;
                right: 6px;
                z-index: 2;
                padding: 0;
                transform: translateY(-50%);
            }

            body.reels-active .download-banner-badge-wrapper,
            body.reels-active .download-banner-sub-row {
                display: none;
            }

            body.reels-active .download-header-actions {
                gap: 2px;
            }

            body.reels-active .download-action-btn {
                width: 24px;
                height: 24px;
                padding: 4px;
            }

            body.reels-active .download-banner-body {
                gap: 8px;
                padding: 7px 64px 7px 12px;
            }

            body.reels-active .download-banner-thumb-box {
                width: 28px;
                height: 28px;
                flex: 0 0 28px;
                border-radius: 8px;
            }

            body.reels-active .download-banner-thumb {
                font-size: 16px;
            }

            body.reels-active .download-banner-info-col,
            body.reels-active .download-banner-title-row {
                min-width: 0;
                width: 100%;
            }

            body.reels-active .download-banner-info-col {
                flex: 1 1 0%;
            }

            body.reels-active .download-banner-title-row {
                justify-content: flex-start;
                gap: 6px;
                line-height: 1.2;
            }

            body.reels-active .download-primary-title {
                flex: 1 1 auto;
                min-width: 0;
                line-height: 1.2;
            }

            body.reels-active .download-progress-number {
                flex: 0 0 3ch;
                text-align: right;
                line-height: 1.2;
            }

            /* Progress Track */
            .download-banner-progress-track {
                width: 100%;
                height: 4px;
                background: rgba(128, 128, 128, 0.18);
                overflow: hidden;
            }

            body.reels-active .download-banner-progress-track {
                height: 3px;
            }

            .download-banner-progress-fill {
                height: 100%;
                width: 0%;
                background: linear-gradient(90deg, #0284c7, #38bdf8);
                transition: width 0.25s ease-out;
            }

            .download-banner-progress-fill.phase-completed {
                background: #10b981;
            }

            .download-banner-progress-fill.phase-error {
                background: #ef4444;
            }

            /* Minimized */
            .download-mini-content {
                display: flex;
                align-items: center;
                justify-content: space-between;
                padding: 10px 14px;
                cursor: pointer;
            }

            .download-mini-left {
                display: flex;
                align-items: center;
                gap: 8px;
                font-size: 12px;
                font-weight: 600;
            }

            .download-mini-icon {
                color: #0284c7;
                display: flex;
            }

            .spin-icon {
                animation: spin-download 1s linear infinite;
            }

            @keyframes spin-download {
                100% { transform: rotate(360deg); }
            }

            /* Toast */
            .download-toast {
                position: fixed;
                top: 20px;
                left: 50%;
                transform: translateX(-50%) translateY(-20px);
                background: var(--mobile-nav-bg, #242526);
                color: var(--text-dark, #fff);
                border: 1px solid var(--border, #3a3b3c);
                padding: 10px 18px;
                border-radius: 50px;
                box-shadow: 0 8px 24px rgba(0,0,0,0.25);
                font-size: 13px;
                font-weight: 500;
                z-index: 10005;
                opacity: 0;
                pointer-events: none;
                transition: transform 0.2s, opacity 0.2s;
            }

            .download-toast.show {
                opacity: 1;
                transform: translateX(-50%) translateY(0);
            }
            .download-toast.toast-error { border-color: #dc3545; }
            .download-toast.toast-success { border-color: #198754; }
        `;
        document.head.appendChild(style);
    }
}

export const downloadUI = new DownloadUI();
