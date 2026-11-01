/**
 * Upload UI
 * 
 * Manages the global upload banner and UI components.
 * Displays current stage, overall progress, current file, files completed, 
 * and estimated remaining time. Smoothly animates between states.
 * No alert dialogs or blocking modals.
 */

import { uploadEvents, UploadEventNames } from './upload_events.js?v=20261011-16';
import { UploadState } from './upload_state_machine.js?v=20261011-16';

// The manager singleton is reached via window (base.html exports it as
// window.uploadManager), deliberately NOT imported here: this file is
// loaded through the fingerprinted template URL, and an import of
// upload_manager.js would go through a different module URL -- evaluating
// a second copy of the class with its own empty session Map, which is how
// the donut once ended up permanently stuck on "Preparing…".
const getUploadManager = () => (typeof window !== 'undefined' ? window.uploadManager : null);

class UploadBanner {
    constructor() {
        this.banner = null;
        this.isVisible = false;
        this.isMinimized = false;
        this.currentUploadId = null;
        this.bannerMode = 'cancel'; // 'cancel' | 'retry'
        this.autoHideTimer = null;
        this.phaseTransitionTimer = null;
        this.currentProgress = 0;
        this.activeThumbnailUrls = [];
        this.hasVideo = false;
        this.isInitialized = false;
        this.phaseClass = '';
    }

    /**
     * Initialize the upload banner
     */
    initialize() {
        if (this.isInitialized) return;
        this.createBanner();
        this.setupEventListeners();
        this.isInitialized = true;
    }

    /**
     * Create the banner DOM element - subtle donut spinner with count
     */
    createBanner() {
        if (document.getElementById('upload-banner')) {
            this.banner = document.getElementById('upload-banner');
            return;
        }

        const banner = document.createElement('div');
        banner.id = 'upload-banner';
        banner.className = 'upload-banner';
        banner.innerHTML = `
            <div class="upload-donut-container">
                <svg class="upload-donut" viewBox="0 0 32 32">
                    <circle class="upload-donut-bg" cx="16" cy="16" r="14" fill="none" />
                    <circle class="upload-donut-progress" id="upload-donut-progress" cx="16" cy="16" r="14" fill="none"
                            stroke-dasharray="88, 88" stroke-dashoffset="88" />
                </svg>
                <div class="upload-count" id="upload-count">0</div>
            </div>
            <div class="upload-meta">
                <div class="upload-phase" id="upload-phase"></div>
                <div class="upload-status" id="upload-status">Preparing…</div>
                <div class="upload-subtitle" id="upload-subtitle">
                    <span id="upload-subtitle-text"></span>
                    <span id="upload-mini-text"></span>
                </div>
            </div>
            <button class="upload-cancel-btn" id="upload-banner-cancel" title="Cancel">
                <i class="bi bi-x-lg"></i>
            </button>
        `;

        document.body.appendChild(banner);
        this.banner = banner;
    }

    /**
     * Setup event listeners
     */
    setupEventListeners() {
        // Cancel button
        const cancelBtn = document.getElementById('upload-banner-cancel');
        if (cancelBtn) {
            cancelBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.cancelCurrentUpload();
            });
        }

        // Click on banner to navigate to uploads page (HTMX: keep R2 PUT alive)
        this.banner.addEventListener('click', (e) => {
            if (e.target.closest('.upload-cancel-btn')) return; // Don't navigate if clicking cancel
            if (window.htmx) {
                window.htmx.ajax('GET', '/uploads/', {
                    target: '#page-content-target',
                    swap: 'innerHTML',
                });
                try { window.history.pushState({}, '', '/uploads/'); } catch (_) {}
                return;
            }
            window.location.href = '/uploads/';
        });

        // Upload state machine events
        uploadEvents.on(UploadEventNames.UPLOAD_CREATED, (data) => {
            this.handleUploadCreated(data);
        });

        uploadEvents.on(UploadEventNames.UPLOAD_STARTED, (data) => {
            this.handleUploadStarted(data);
        });

        uploadEvents.on(UploadEventNames.UPLOAD_PUBLISHED, (data) => {
            this.handleUploadPublished(data);
        });

        uploadEvents.on(UploadEventNames.UPLOAD_COMPLETED, (data) => {
            this.handleUploadCompleted(data);
        });

        uploadEvents.on(UploadEventNames.UPLOAD_FAILED, (data) => {
            this.handleUploadFailed(data);
        });

        uploadEvents.on(UploadEventNames.UPLOAD_CANCELLED, (data) => {
            this.handleUploadCancelled(data);
        });

        // Listen to resumable upload progress events
        window.addEventListener('resumable-upload-progress', (e) => {
            // Pages that drive several transfers at once aggregate them
            // themselves and push the merged number in; ignore the raw
            // per-file events while that mode is on.
            if (this.aggregateProgress) return;
            this.handleResumableProgress(e.detail);
        });

        // Tracker bus: upload bytes drive the donut. Transcoding and
        // later stages are NOT shown here by design — they live on the
        // Uploads manager page. The banner hides on publish.
        uploadEvents.on(UploadEventNames.UPLOAD_PROGRESS, (data) => {
            if (data && (data.uploadId === this.currentUploadId || !this.currentUploadId)) {
                this.currentUploadId = data.uploadId || this.currentUploadId;
                const percent = Math.round(data.percent || 0);
                this.updateDonutProgress(percent, `Uploading ${percent}%`);
            }
        });

        uploadEvents.on(UploadEventNames.UPLOAD_FAILED, () => {
            this.setStatus('Upload failed — see Uploads');
        });
    }

    /**
     * Handle upload created
     */
    handleUploadCreated(data) {
        console.log('[UploadUI] Upload created:', data);
        this.activeUploadCount = (this.activeUploadCount || 0) + 1;
        this.updateCount();
        this.show();
    }

    /**
     * Handle upload started
     */
    handleUploadStarted(data) {
        this.currentUploadId = data.uploadId;
        this.updateDonutProgress(0, 'Uploading 0%');
        this.show();
    }

    /**
     * Handle resumable upload progress
     */
    handleResumableProgress(detail) {
        if (detail && detail.total) {
            const percent = Math.round((100 * detail.loaded) / detail.total);
            this.updateDonutProgress(percent, `Uploading ${percent}%`);
        }
    }

    /**
     * Handle upload published: bytes are done, so the donut's job is done.
     * Transcoding/processing continues on the Uploads manager page.
     */
    handleUploadPublished(data) {
        this.hasVideo = Boolean(data.hasVideo);
        this.updateDonutProgress(100, 'Uploaded!');
        this.activeUploadCount = Math.max(0, (this.activeUploadCount || 1) - 1);
        this.updateCount();
        // Safety net: if no uploads are active, dismiss immediately
        if (!getUploadManager()?.getActiveUpload()) {
            setTimeout(() => this.hide(), 2000);
        }
    }

    /**
     * Handle upload completed
     */
    handleUploadCompleted(data) {
        this.setStatus('Ready!');
        this.updateDonutProgress(100);
        this.activeUploadCount = Math.max(0, (this.activeUploadCount || 1) - 1);
        this.updateCount();
        if (!getUploadManager()?.getActiveUpload()) {
            setTimeout(() => this.hide(), 1500);
        }
    }

    /**
     * Handle upload failed
     */
    handleUploadFailed(data) {
        this.activeUploadCount = Math.max(0, (this.activeUploadCount || 1) - 1);
        this.updateCount();
        if (!getUploadManager()?.getActiveUpload()) {
            setTimeout(() => this.hide(), 3000);
        }
    }

    /**
     * Handle upload cancelled
     */
    handleUploadCancelled(data) {
        this.activeUploadCount = Math.max(0, (this.activeUploadCount || 1) - 1);
        this.updateCount();
        if (!getUploadManager()?.getActiveUpload()) {
            this.hide();
        }
    }

    /**
     * Update donut progress
     */
    updateDonutProgress(percent, label) {
        const progressEl = document.getElementById('upload-donut-progress');
        if (progressEl) {
            // circumference ≈ 88 for radius 14
            // offset = 88 - (percent/100 * 88)
            const circumference = 88;
            const offset = circumference - (percent / 100 * circumference);
            progressEl.setAttribute('stroke-dasharray', `${circumference}, ${circumference}`);
            progressEl.setAttribute('stroke-dashoffset', offset);
        }
        if (typeof label === 'string') this.setStatus(label);
    }

    setStatus(text) {
        const el = document.getElementById('upload-status');
        if (el && typeof text === 'string' && text) el.textContent = text;
    }

    /**
     * Update upload count
     */
    updateCount() {
        const countEl = document.getElementById('upload-count');
        if (countEl) {
            countEl.textContent = this.activeUploadCount || 0;
        }
    }

    // --- Classic banner API ---------------------------------------------------
    // The donut is the current banner UI, but older upload flows (document
    // upload, new-version modal) still drive the banner imperatively through
    // the classic header/title/subtitle/progress API. These adapters map that
    // API onto the donut so both generations share one banner. Without them
    // the classic flows throw "setHeaderTitle is not a function" and abort
    // the upload before the request is ever sent.

    /**
     * Phase label shown above the status line (e.g. "UPLOADING DOCUMENT")
     */
    setHeaderTitle(text, phaseClass = '') {
        const phaseEl = document.getElementById('upload-phase');
        const title = typeof text === 'string' ? text : '';
        if (phaseEl) {
            phaseEl.textContent = title;
            phaseEl.classList.toggle('visible', Boolean(title));
        }
        if (phaseClass) this.setProgressFillClass(phaseClass);
    }

    /**
     * Main status line (file name / current state)
     */
    setTitle(text) {
        this.setStatus(text);
    }

    /**
     * Muted secondary line under the status
     */
    setSubtitle(text) {
        const subEl = document.getElementById('upload-subtitle-text');
        const title = typeof text === 'string' ? text : '';
        if (subEl) subEl.textContent = title;
        this.syncSubtitleVisibility();
    }

    /**
     * Tiny trailing note (e.g. "Uploading 45%", "Document ready! ✓")
     */
    updateMiniText(text) {
        const miniEl = document.getElementById('upload-mini-text');
        const title = typeof text === 'string' ? text : '';
        if (miniEl) miniEl.textContent = title;
        this.syncSubtitleVisibility();
    }

    /**
     * Show the subtitle line only when it has something to show
     */
    syncSubtitleVisibility() {
        const subEl = document.getElementById('upload-subtitle');
        if (!subEl) return;
        const hasText = Boolean(
            (subEl.querySelector('#upload-subtitle-text')?.textContent || '').trim() ||
            (subEl.querySelector('#upload-mini-text')?.textContent || '').trim()
        );
        subEl.classList.toggle('visible', hasText);
    }

    /**
     * Progress as a percentage (0-100) - drives the donut ring
     */
    updateProgress(percent) {
        const clamped = Math.min(100, Math.max(0, Math.round(Number(percent) || 0)));
        this.currentProgress = clamped;
        // No label: the status line belongs to the caller (file name, phase).
        this.updateDonutProgress(clamped);
    }

    /**
     * Take over progress reporting.
     *
     * A page that drives several transfers at once merges them into one
     * number and pushes it here; while this mode is on the raw per-file
     * engine events are ignored so the donut does not flicker between files.
     */
    setAggregateProgress(percent, label) {
        this.aggregateProgress = true;
        const clamped = Math.min(100, Math.max(0, Math.round(Number(percent) || 0)));
        this.currentProgress = clamped;
        this.updateDonutProgress(clamped, label);
    }

    /**
     * Hand progress reporting back to the engine's own events.
     */
    clearAggregateProgress() {
        this.aggregateProgress = false;
    }

    /**
     * Phase class - tints the donut ring (phase-upload/-transcoding/
     * -published/-error)
     */
    setProgressFillClass(className) {
        this.phaseClass = className || '';
        if (!this.banner) return;
        ['phase-upload', 'phase-transcoding', 'phase-published', 'phase-error'].forEach((cls) => {
            this.banner.classList.toggle(cls, this.phaseClass.split(/\s+/).includes(cls));
        });
    }

    /**
     * Error state - keeps the banner up with the failure reason
     */
    showError(message, canRetry = false) {
        this.setHeaderTitle('UPLOAD FAILED', 'phase-error');
        this.setTitle(message || 'Upload failed');
        this.setSubtitle(canRetry ? 'Retry to start over' : '');
        this.updateMiniText('Upload failed ✕');
        this.show();
    }

    /**
     * Success state
     */
    showCompletion() {
        this.setHeaderTitle('ALL SET', 'phase-published');
        this.setTitle('Ready!');
        this.setSubtitle('');
        this.updateProgress(100);
        this.updateMiniText('Ready ✓');
        this.show();
    }

    /**
     * Byte progress for the classic XHR flows.
     * Accepts the object form ({loaded, total, percent}) or the positional
     * form (loaded, total).
     */
    handleUploadProgress(data, total) {
        let loaded = 0;
        let size = 0;
        let percent = 0;

        if (data && typeof data === 'object') {
            loaded = Number(data.loaded) || 0;
            size = Number(data.total) || 0;
            percent = Number(data.percent) || 0;
        } else {
            loaded = Number(data) || 0;
            size = Number(total) || 0;
        }

        if (!percent && size > 0) percent = (loaded / size) * 100;

        const clamped = Math.min(100, Math.max(0, Math.round(percent)));
        this.currentProgress = clamped;
        this.updateDonutProgress(clamped);
        this.updateMiniText(`Uploading ${clamped}%`);
    }

    /**
     * Reset the banner back to its idle state
     */
    reset() {
        this.currentProgress = 0;
        this.setHeaderTitle('', '');
        this.setTitle('Preparing…');
        this.setSubtitle('');
        this.updateMiniText('');
        this.setProgressFillClass('');
        this.updateDonutProgress(0);
        this.activeUploadCount = 0;
        this.updateCount();
    }

    /**
     * Show the banner
     */
    show() {
        if (!this.banner) return;
        this.banner.classList.add('visible');
        this.isVisible = true;
    }

    /**
     * Hide the banner
     */
    hide() {
        if (!this.banner) return;
        this.banner.classList.remove('visible');
        this.isVisible = false;
        this.activeUploadCount = 0;
        this.updateCount();
        this.updateDonutProgress(0);
        this.setHeaderTitle('', '');
        this.setProgressFillClass('');
        this.setSubtitle('');
        this.updateMiniText('');
    }

    /**
     * Dismiss the banner. Active upload gets a confirm dialogue:
     * OK cancels it, Cancel keeps it running in the background.
     * The banner always hides either way — ✕ must never stick.
     */
    cancelCurrentUpload() {
        const currentUpload = getUploadManager()?.getActiveUpload() || null;
        if (!currentUpload) {
            this.hide();
            return;
        }
        const choice = window.confirm(
            `An upload is in progress (${this.currentStatusText()}).\n\n` +
            'Press OK to cancel the upload, or Cancel to keep uploading in the background.'
        );
        if (choice) {
            try {
                getUploadManager()?.cancelUpload(currentUpload.id);
            } catch (_) {}
        }
        // Banner always hides: dismissed UI only, or cancelled upload.
        // A backgrounded upload keeps flowing; /uploads/ shows its status.
        this.hide();
    }

    currentStatusText() {
        const el = document.getElementById('upload-status');
        return (el && el.textContent) || 'in progress';
    }

    isBannerVisible() {
        return this.isVisible;
    }
}

class UploadUI {
    constructor() {
        this.banner = new UploadBanner();
        this.isInitialized = false;
    }

    /**
     * Initialize the upload UI
     */
    initialize() {
        if (this.isInitialized) return;

        this.banner.initialize();
        this.injectStyles();
        this.setupGlobalListeners();
        this.restoreBannerOnLoad();
        this.isInitialized = true;
    }

    /**
     * Check for ongoing uploads on page load and restore banner
     */
    async restoreBannerOnLoad() {
        try {
            // Check if resumable engine has any active sessions
            if (window.resumableUpload && typeof window.resumableUpload.getAllSessions === 'function') {
                const sessions = await window.resumableUpload.getAllSessions();
                const activeSessions = sessions.filter(s => !s.done);
                if (activeSessions.length > 0) {
                    // Verify each session with the server before counting it.
                    // Failed/cancelled/deleted records are terminal: clean them
                    // from IndexedDB so they don't stick the donut count.
                    let trulyActive = 0;
                    for (const s of activeSessions) {
                        try {
                            const res = await fetch(`/api/uploads/${s.server_id}/`, {
                                credentials: 'same-origin',
                                headers: { 'X-Requested-With': 'XMLHttpRequest' },
                            });
                            if (!res.ok) {
                                // 410 Gone / 404 = terminal: clean up, don't count
                                await window.storeDelete('sessions', s.client_upload_id);
                                await window.storeDelete('blobs', s.client_upload_id);
                                continue;
                            }
                            const data = await res.json();
                            if (['uploading', 'verifying', 'preparing', 'queued'].includes(data.status)) {
                                trulyActive++;
                            } else {
                                // Terminal on server: clean up local state
                                await window.storeDelete('sessions', s.client_upload_id);
                                await window.storeDelete('blobs', s.client_upload_id);
                            }
                        } catch (e) {
                            // Network error: leave session alone, don't count it yet
                            console.warn('[UploadUI] Could not verify session', s.client_upload_id, e);
                        }
                    }
                    if (trulyActive > 0) {
                        this.banner.activeUploadCount = trulyActive;
                        this.banner.updateCount();
                        this.banner.show();
                    }
                }
            }
        } catch (e) {
            console.error('[UploadUI] Failed to restore banner:', e);
        }
    }

    /**
     * Inject CSS styles for the upload banner - subtle donut spinner
     */
    injectStyles() {
        if (document.getElementById('upload-banner-styles')) return;

        const style = document.createElement('style');
        style.id = 'upload-banner-styles';
        style.textContent = `
            /* ================================================================
               Subtle Donut Upload Spinner
               ================================================================ */

            .upload-banner {
                position: fixed;
                z-index: 9995;
                display: flex;
                align-items: center;
                gap: 8px;
                opacity: 0;
                pointer-events: none;
                transition: opacity 0.3s ease;
            }

            .upload-banner.visible {
                opacity: 1;
                pointer-events: auto;
            }

            /* Mobile: left side, slightly on top of bottom nav bar */
            @media (max-width: 767.98px) {
                .upload-banner {
                    bottom: calc(72px + var(--vasto-safe-area-bottom, env(safe-area-inset-bottom, 0px)));
                    left: 12px;
                }
            }

            /* Make donut clickable for navigation */
            .upload-donut-container {
                cursor: pointer;
            }

            /* Desktop: floats where current banner was (bottom-right) */
            @media (min-width: 768px) {
                .upload-banner {
                    bottom: 24px;
                    right: 28px;
                }
            }

            /* Donut Container */
            .upload-donut-container {
                position: relative;
                width: 32px;
                height: 32px;
            }

            /* SVG Donut */
            .upload-donut {
                width: 100%;
                height: 100%;
                transform: rotate(-90deg);
            }

            .upload-donut-bg {
                fill: none;
                stroke: var(--border, #3a3b3c);
                stroke-width: 2.5;
            }

            [data-theme="light"] .upload-donut-bg {
                stroke: var(--border, #e2e8f0);
            }

            .upload-donut-progress {
                fill: none;
                stroke: #3b82f6;
                stroke-width: 2.5;
                stroke-linecap: round;
                stroke-dasharray: 88, 88; /* 2 * PI * 14 ≈ 88 */
                stroke-dashoffset: 88;
                transition: stroke-dashoffset 0.3s ease;
            }

            /* Count in center */
            .upload-count {
                position: absolute;
                top: 50%;
                left: 50%;
                transform: translate(-50%, -50%);
                font-size: 11px;
                font-weight: 700;
                color: var(--text-dark, #e4e6eb);
                font-variant-numeric: tabular-nums;
            }

            /* Phase label: tells the user why a video sits on processing */
            .upload-meta {
                max-width: 220px;
                min-width: 0;
            }
            .upload-status {
                font-size: 12px;
                font-weight: 600;
                color: var(--text-dark, #e4e6eb);
                background: var(--card-bg, rgba(0,0,0,0.65));
                border: 1px solid var(--border, rgba(255,255,255,0.14));
                border-radius: 999px;
                padding: 6px 10px;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            [data-theme="light"] .upload-status {
                color: var(--text-dark, #0f172a);
            }

            /* Classic-flow lines: phase label above, subtitle below */
            .upload-phase {
                display: none;
                max-width: 220px;
                font-size: 9px;
                font-weight: 700;
                letter-spacing: 0.08em;
                text-transform: uppercase;
                color: #3b82f6;
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .upload-phase.visible {
                display: block;
            }
            .upload-subtitle {
                display: none;
                max-width: 220px;
                font-size: 10px;
                font-weight: 500;
                color: var(--text-muted, #b0b3b8);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
            }
            .upload-subtitle.visible {
                display: block;
            }
            .upload-mini-text {
                color: var(--text-muted, #b0b3b8);
            }
            .upload-mini-text:not(:empty)::before {
                content: '· ';
            }
            [data-theme="light"] .upload-subtitle,
            [data-theme="light"] .upload-mini-text {
                color: var(--text-muted, #64748b);
            }

            /* Phase tinting of the donut ring */
            .upload-banner.phase-transcoding .upload-donut-progress {
                stroke: #f59e0b;
            }
            .upload-banner.phase-published .upload-donut-progress {
                stroke: #22c55e;
            }
            .upload-banner.phase-error .upload-donut-progress {
                stroke: #ef4444;
            }

            [data-theme="light"] .upload-count {
                color: var(--text-dark, #0f172a);
            }

            /* Cancel Button */
            .upload-cancel-btn {
                background: transparent;
                border: none;
                color: var(--text-muted, #b0b3b8);
                width: 24px;
                height: 24px;
                border-radius: 6px;
                display: inline-flex;
                align-items: center;
                justify-content: center;
                cursor: pointer;
                font-size: 12px;
                transition: background 0.15s, color 0.15s;
            }

            .upload-cancel-btn:hover {
                background: var(--border, #3a3b3c);
                color: var(--text-dark, #e4e6eb);
            }

            [data-theme="light"] .upload-cancel-btn {
                color: var(--text-muted, #64748b);
            }
            [data-theme="light"] .upload-cancel-btn:hover {
                background: var(--border, #e2e8f0);
                color: var(--text-dark, #0f172a);
            }
        `;

        document.head.appendChild(style);
    }

    setupGlobalListeners() {
        // Show banner and set preview as soon as upload is registered
        uploadEvents.on(UploadEventNames.UPLOAD_ADDED, (data) => {
            this.banner.currentUploadId = data.id;
            const files = Array.isArray(data.data?.files)
                ? data.data.files
                : Array.isArray(data.session?.files)
                ? data.session.files
                : [];
            this.banner.show();
        });

        // Hide banner when queue clears
        uploadEvents.on(UploadEventNames.QUEUE_CLEARED, () => {
            this.banner.hide();
        });

        // No beforeunload warning needed - resumable engine survives refresh
    }

    /**
     * Get banner instance
     * @returns {UploadBanner}
     */
    getBanner() {
        return this.banner;
    }

    // Direct proxy methods to inner banner.
    // Optional chaining on purpose: the banner is presentation only, so a
    // missing/stale banner method must never abort the caller's upload.
    show() { return this.banner?.show?.(); }
    hide() { return this.banner?.hide?.(); }
    reset() { return this.banner?.reset?.(); }
    setHeaderTitle(text, phaseClass) { return this.banner?.setHeaderTitle?.(text, phaseClass); }
    setTitle(text) { return this.banner?.setTitle?.(text); }
    setSubtitle(text) { return this.banner?.setSubtitle?.(text); }
    updateProgress(percent) { return this.banner?.updateProgress?.(percent); }
    setAggregateProgress(percent, label) { return this.banner?.setAggregateProgress?.(percent, label); }
    clearAggregateProgress() { return this.banner?.clearAggregateProgress?.(); }
    setProgressFillClass(className) { return this.banner?.setProgressFillClass?.(className); }
    updateMiniText(text) { return this.banner?.updateMiniText?.(text); }
    showError(message, canRetry) { return this.banner?.showError?.(message, canRetry); }
    showCompletion() { return this.banner?.showCompletion?.(); }
    handleUploadProgress(data, total) { return this.banner?.handleUploadProgress?.(data, total); }
}

// Global upload UI instance
export const uploadUI = new UploadUI();

export {
    UploadBanner,
    UploadUI,
};

export default UploadUI;
