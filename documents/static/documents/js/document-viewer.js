/**
 * Document Viewer System
 * Modular, production-ready, reusable architecture for rendering multiple document types
 * across desktop, mobile, Document Detail previews, and embedded context panels.
 */

// This file is included from several templates and re-evaluated whenever an
// htmx partial that carries it is swapped in. A second `class DocumentViewer`
// declaration would throw a SyntaxError and take the whole viewer down, so
// everything lives in one self-guarding scope.
(function (root) {
    'use strict';
    if (root.DocumentViewer) {
        return; // already loaded - do not redeclare
    }

class DocumentViewer {
    /**
     * Supports both modern options-based signature:
     *   new DocumentViewer(container, options)
     * and backward-compatible positional signature:
     *   new DocumentViewer(container, fileUrl, fileType, fileName, documentId, fileId, options)
     */
    constructor(container, fileUrlOrOptions, fileType, fileName, documentId, fileId, options = {}) {
        this.container = typeof container === 'string' ? document.querySelector(container) : container;

        let opts = {};
        if (typeof fileUrlOrOptions === 'object' && fileUrlOrOptions !== null) {
            // Modern invocation: new DocumentViewer(container, options)
            opts = fileUrlOrOptions;
            this.fileUrl = opts.fileUrl || opts.mediaUrl || opts.url || '';
            this.fileName = opts.fileName || opts.title || opts.name || '';
            let rawFt = (opts.fileType || opts.type || opts.extension || '').toLowerCase().trim();
            if (rawFt.startsWith('.')) rawFt = rawFt.substring(1);
            if (!rawFt) {
                const candidate = (this.fileUrl || this.fileName || '').split('?')[0].split('#')[0];
                const match = candidate.match(/\.([a-zA-Z0-9]+)$/);
                if (match) rawFt = match[1].toLowerCase();
            }
            this.fileType = rawFt;
            this.documentId = opts.documentId || opts.documentShareId || opts.shareId || opts.id || null;
            this.fileId = opts.fileId || null;
            this.documentVersionId = opts.documentVersionId || opts.versionId || null;
            this.options = opts;
        } else {
            // Legacy positional invocation
            opts = options || {};
            this.fileUrl = fileUrlOrOptions || '';
            this.fileName = fileName || '';
            let rawFt = (fileType || '').toLowerCase().trim();
            if (rawFt.startsWith('.')) rawFt = rawFt.substring(1);
            if (!rawFt) {
                const candidate = (this.fileUrl || this.fileName || '').split('?')[0].split('#')[0];
                const match = candidate.match(/\.([a-zA-Z0-9]+)$/);
                if (match) rawFt = match[1].toLowerCase();
            }
            this.fileType = rawFt;
            this.documentId = documentId || null;
            this.fileId = fileId || null;
            this.documentVersionId = opts.documentVersionId || opts.versionId || null;
            this.options = opts;
        }

        // Thumbnail provenance
        this.thumbnail = this.options.thumbnail || 
            (this.container && this.container.dataset && this.container.dataset.thumbnail) || '';

        // Page State (first-class contract)
        const rawInitPage = this.options.initialPage;
        const parsedPage = (rawInitPage !== undefined && rawInitPage !== null && !isNaN(rawInitPage)) 
            ? Math.max(1, parseInt(rawInitPage, 10)) 
            : 1;
        this.currentPage = parsedPage;
        this.totalPages = null;
        this.isPaginated = false;

        // Lifecycle & UI State
        this.viewer = null;
        this.toolbar = null;
        this.floatingBar = null;
        this.isLoading = false;
        this.isFullscreen = false;
        this.resizeObserver = null;
        this._keydownHandler = null;
        this._fullscreenHandler = null;

        // Public Callbacks
        this.onPageChange = typeof this.options.onPageChange === 'function' ? this.options.onPageChange : null;
        this.onLoad = typeof this.options.onLoad === 'function' ? this.options.onLoad : null;
        this.onError = typeof this.options.onError === 'function' ? this.options.onError : null;
        this.onStateChange = typeof this.options.onStateChange === 'function' ? this.options.onStateChange : null;

        // Touch & Gesture state
        this.touchStartX = 0;
        this.touchStartY = 0;
        this.touchEndX = 0;
        this.touchEndY = 0;
        this.minSwipeDistance = 50;

        this.initialPinchDistance = 0;
        this.currentScale = 1.0;
        this.isPinching = false;
        this.pinchCenterX = 0;
        this.pinchCenterY = 0;
        this.translateX = 0;
        this.translateY = 0;
        this.lastTranslateX = 0;
        this.lastTranslateY = 0;
        this.lastTouchX = 0;
        this.lastTouchY = 0;
    }

    /**
     * Initializes the viewer, mounts content, builds toolbar, and hooks resize/gestures.
     */
    async initialize() {
        if (!this.container) {
            console.error('[DocumentViewer] Container element not provided or found in DOM.');
            return;
        }

        this.showLoading();
        this.isLoading = true;
        this._emitStateChange();

        try {
            this.viewer = ViewerFactory.createViewer(this.fileType, this.container, this.fileUrl, this.options);
            await this.viewer.load();

            this.totalPages = this.viewer.getTotalPages();
            this.isPaginated = this.totalPages !== null && this.totalPages !== undefined;

            if (!this.isPaginated) {
                // Non-paginated formats must have null page state
                this.currentPage = null;
                this.totalPages = null;
            } else {
                if (this.currentPage > this.totalPages) {
                    this.currentPage = this.totalPages;
                }
                if (this.currentPage < 1) {
                    this.currentPage = 1;
                }
            }

            if (this.container) {
                this.container.style.position = 'relative';
            }
            this.createToolbar();
            this.createFloatingBar();
            this.hideLoading();
            this.isLoading = false;

            if (this.isPaginated) {
                this.updatePageIndicator();
                this.updateNavigationButtons();
            }

            // Setup listeners
            this.setupResizeObserver();
            this.setupKeyboardNavigation();
            this.addTouchGestures();

            // Emit initial notifications
            this._notifyPageChange();
            if (this.onLoad) {
                try {
                    this.onLoad(this.getState());
                } catch (e) {
                    console.error('[DocumentViewer onLoad error]', e);
                }
            }
            this._emitStateChange();

        } catch (error) {
            this.isLoading = false;
            const message = error ? (error.message || String(error)) : 'Unable to load document preview.';
            this.showError(message);
            if (this.onError) {
                try {
                    this.onError(error, this.getState());
                } catch (e) {
                    console.error('[DocumentViewer onError error]', e);
                }
            }
            this._emitStateChange();
        }
    }

    /**
     * Returns an authoritative, immutable snapshot of the viewer state.
     */
    getState() {
        return {
            documentId: this.documentId,
            documentVersionId: this.documentVersionId,
            fileId: this.fileId,
            fileUrl: this.fileUrl,
            fileType: this.fileType,
            fileName: this.fileName,
            currentPage: this.currentPage,
            pageNumber: this.currentPage,
            totalPages: this.totalPages,
            isPaginated: this.isPaginated,
            isFullscreen: this.isFullscreen,
            isLoading: this.isLoading,
        };
    }

    getCurrentPage() {
        return this.currentPage;
    }

    getTotalPages() {
        return this.totalPages;
    }

    _notifyPageChange() {
        if (this.onPageChange) {
            try {
                const state = this.getState();
                // We pass (currentPage, totalPages, state).
                // If a caller expects (page, totalPages) it gets both integers.
                // If a caller expects (page, state) in a 2-arg signature, we also attach state properties.
                this.onPageChange(this.currentPage, this.totalPages, state);
            } catch (e) {
                console.error('[DocumentViewer onPageChange error]', e);
            }
        }
    }

    _emitStateChange() {
        if (this.onStateChange) {
            try {
                this.onStateChange(this.getState());
            } catch (e) {
                console.error('[DocumentViewer onStateChange error]', e);
            }
        }
    }

    showLoading() {
        const heightStyle = (this.options && this.options.fitContainer) ? 'height: 100%; min-height: 200px;' : 'height: 600px;';
        this.container.innerHTML = `
            <div class="viewer-loading" style="display: flex; flex-direction: column; align-items: center; justify-content: center; ${heightStyle} gap: 16px;">
                <div class="spinner" style="width: 44px; height: 44px; border: 3px solid var(--border, #dee2e6); border-top-color: var(--primary, #0d6efd); border-radius: 50%; animation: docViewerSpin 0.9s linear infinite;"></div>
                <p style="color: var(--text-secondary, #6c757d); font-size: 0.95rem; font-weight: 500; margin: 0;">Loading document...</p>
            </div>
            <style>
                @keyframes docViewerSpin {
                    to { transform: rotate(360deg); }
                }
            </style>
        `;
    }

    hideLoading() {
        const loading = this.container.querySelector('.viewer-loading');
        if (loading) loading.remove();
    }

    showError(message) {
        const heightStyle = (this.options && this.options.fitContainer) ? 'height: 100%; min-height: 200px;' : 'height: 600px;';
        this.container.innerHTML = `
            <div class="viewer-error" style="display: flex; flex-direction: column; align-items: center; justify-content: center; ${heightStyle} gap: 14px; text-align: center; padding: 24px;">
                <div style="width: 56px; height: 56px; border-radius: 50%; background: rgba(220, 53, 69, 0.1); display: flex; align-items: center; justify-content: center;">
                    <i class="bi bi-exclamation-triangle" style="font-size: 1.75rem; color: var(--error, #dc3545);"></i>
                </div>
                <h3 style="font-size: 1.15rem; font-weight: 600; color: var(--text-dark, #212529); margin: 0;">Preview Unavailable</h3>
                <p style="color: var(--text-secondary, #6c757d); font-size: 0.9rem; max-width: 420px; line-height: 1.5; margin: 0;">${message || 'Unable to load document preview. You can reload the viewer or download the original file.'}</p>
                <div style="display: flex; gap: 10px; align-items: center; justify-content: center; flex-wrap: wrap; margin-top: 4px;">
                    <button type="button" class="btn btn-primary btn-sm rounded-pill px-3 py-2 d-inline-flex align-items-center gap-2 viewer-retry-btn" id="viewer-retry-btn" style="cursor: pointer; font-weight: 600;">
                        <i class="bi bi-arrow-clockwise"></i> Reload Document
                    </button>
                    <button type="button" class="btn btn-outline-secondary btn-sm rounded-pill px-3 py-2 d-inline-flex align-items-center gap-2 viewer-download-btn" id="viewer-error-download-btn" style="cursor: pointer; font-weight: 500;">
                        <i class="bi bi-download"></i> Download File
                    </button>
                </div>
            </div>
        `;
        const retryBtn = this.container.querySelector('#viewer-retry-btn');
        if (retryBtn) {
            retryBtn.onclick = () => {
                if (typeof window !== 'undefined' && typeof window.reloadDocumentDetailViewer === 'function') {
                    window.reloadDocumentDetailViewer();
                } else {
                    this.initialize();
                }
            };
        }
        const downloadBtn = this.container.querySelector('#viewer-error-download-btn');
        if (downloadBtn) {
            downloadBtn.onclick = () => this.trackAndDownload();
        }
    }

    createToolbar() {
        if (!this.viewer || !this.viewer.needsToolbar()) return;

        const isCompact = Boolean(this.options && this.options.compact);
        this.toolbar = document.createElement('div');
        this.toolbar.className = `viewer-toolbar ${isCompact ? 'viewer-toolbar-compact' : ''}`;
        
        this.toolbar.style.cssText = `
            display: flex;
            align-items: center;
            justify-content: space-between;
            padding: ${isCompact ? '6px 10px' : '8px 16px'};
            background: var(--card-bg, #ffffff);
            border: 1px solid var(--border, #dee2e6);
            border-bottom: 1px solid var(--border, #dee2e6);
            border-radius: ${isCompact ? '0' : '12px 12px 0 0'};
            gap: 10px;
            flex-wrap: nowrap;
            box-shadow: 0 1px 3px rgba(0,0,0,0.02);
            position: relative;
            z-index: 10;
        `;

        // Left Controls: Format badge and Title
        const leftControls = document.createElement('div');
        leftControls.className = 'viewer-toolbar-left';
        leftControls.style.cssText = `display: flex; align-items: center; gap: ${isCompact ? '6px' : '10px'}; min-width: 0; flex: 1 1 auto; justify-content: flex-start; overflow: hidden;`;

        const formatBadge = document.createElement('span');
        formatBadge.className = 'viewer-doc-type-indicator';
        formatBadge.style.cssText = `
            font-size: ${isCompact ? '0.68rem' : '0.72rem'};
            font-weight: 700;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            padding: ${isCompact ? '2px 6px' : '3px 8px'};
            border-radius: 6px;
            background: rgba(13, 110, 253, 0.1);
            color: var(--primary, #0d6efd);
            white-space: nowrap;
            flex-shrink: 0;
            user-select: none;
        `;
        formatBadge.textContent = (this.fileType || 'DOC').toUpperCase();
        leftControls.appendChild(formatBadge);

        if (this.fileName && !isCompact) {
            const titleEl = document.createElement('span');
            titleEl.className = 'viewer-doc-title';
            titleEl.title = this.fileName;
            titleEl.style.cssText = `
                font-size: 0.85rem;
                font-weight: 600;
                color: var(--text-dark, #212529);
                white-space: nowrap;
                overflow: hidden;
                text-overflow: ellipsis;
                max-width: 320px;
                user-select: none;
            `;
            titleEl.textContent = this.fileName;
            leftControls.appendChild(titleEl);
        }

        // Right Controls: Dedicated Reload, Fullscreen, and Download
        const rightControls = document.createElement('div');
        rightControls.className = 'viewer-toolbar-right';
        rightControls.style.cssText = `display: flex; align-items: center; gap: ${isCompact ? '4px' : '8px'}; flex-shrink: 0; justify-content: flex-end;`;

        // Dedicated Reload Button
        const reloadButton = this.createButton('bi-arrow-clockwise', 'Reload Document', () => {
            if (typeof window !== 'undefined' && typeof window.reloadDocumentDetailViewer === 'function') {
                window.reloadDocumentDetailViewer();
            } else {
                this.initialize();
            }
        }, isCompact, 'viewer-reload-btn');
        reloadButton.id = 'viewer-reload-btn';
        rightControls.appendChild(reloadButton);

        const showFullscreen = this.options.showFullscreen !== false;
        if (showFullscreen) {
            const fullscreenButton = this.createButton('bi-arrows-fullscreen', 'Fullscreen', () => this.toggleFullscreen(), isCompact, 'viewer-fullscreen-btn');
            fullscreenButton.id = 'viewer-fullscreen-btn';
            rightControls.appendChild(fullscreenButton);
        }

        const showDownload = this.options.showDownload !== false;
        if (showDownload) {
            const downloadButton = document.createElement('button');
            downloadButton.className = 'viewer-btn viewer-download-btn';
            downloadButton.type = 'button';
            downloadButton.title = 'Download File';
            downloadButton.setAttribute('aria-label', 'Download File');
            if (isCompact) {
                downloadButton.style.cssText = `
                    display: inline-flex;
                    align-items: center;
                    justify-content: center;
                    width: 28px;
                    height: 28px;
                    padding: 0;
                    border: 1px solid var(--border, #dee2e6);
                    border-radius: 8px;
                    background: var(--card-bg, #ffffff);
                    color: var(--text-secondary, #6c757d);
                    cursor: pointer;
                    transition: all 0.15s ease;
                `;
                downloadButton.innerHTML = `<i class="bi bi-download" style="font-size: 0.8rem; line-height: 1;"></i>`;
            } else {
                downloadButton.style.cssText = `
                    display: inline-flex;
                    align-items: center;
                    gap: 6px;
                    height: 32px;
                    padding: 0 12px;
                    border: 1px solid rgba(13, 110, 253, 0.2);
                    border-radius: 8px;
                    background: rgba(13, 110, 253, 0.08);
                    color: var(--primary, #0d6efd);
                    font-size: 0.8rem;
                    font-weight: 600;
                    cursor: pointer;
                    transition: all 0.15s ease;
                `;
                downloadButton.innerHTML = `<i class="bi bi-download" style="font-size: 0.85rem; line-height: 1;"></i><span>Download</span>`;
            }
            downloadButton.onclick = () => this.trackAndDownload();
            downloadButton.onmouseenter = () => {
                downloadButton.style.background = 'var(--primary, #0d6efd)';
                downloadButton.style.color = '#ffffff';
                downloadButton.style.borderColor = 'var(--primary, #0d6efd)';
            };
            downloadButton.onmouseleave = () => {
                if (isCompact) {
                    downloadButton.style.background = 'var(--card-bg, #ffffff)';
                    downloadButton.style.color = 'var(--text-secondary, #6c757d)';
                    downloadButton.style.borderColor = 'var(--border, #dee2e6)';
                } else {
                    downloadButton.style.background = 'rgba(13, 110, 253, 0.08)';
                    downloadButton.style.color = 'var(--primary, #0d6efd)';
                    downloadButton.style.borderColor = 'rgba(13, 110, 253, 0.2)';
                }
            };
            rightControls.appendChild(downloadButton);
        }

        this.toolbar.appendChild(leftControls);
        this.toolbar.appendChild(rightControls);

        this.container.insertBefore(this.toolbar, this.container.firstChild);
    }

    createFloatingBar() {
        if (this.floatingBar) {
            this.floatingBar.remove();
            this.floatingBar = null;
        }

        const needsNav = Boolean(this.viewer && this.viewer.needsPageNavigation() && this.isPaginated);
        const needsZoom = Boolean(this.viewer && this.viewer.needsZoom());
        const needsScroll = Boolean(this.viewer && this.viewer.needsScroll());
        if (!needsNav && !needsZoom && !needsScroll) return;

        const isCompact = Boolean(this.options && this.options.compact);
        this.floatingBar = document.createElement('div');
        this.floatingBar.className = `viewer-floating-bar ${isCompact ? 'viewer-floating-bar-compact' : ''}`;
        this.floatingBar.style.cssText = `
            position: absolute;
            bottom: ${isCompact ? '12px' : '20px'};
            left: 50%;
            transform: translateX(-50%);
            z-index: 30;
            display: inline-flex;
            align-items: center;
            gap: ${isCompact ? '4px' : '6px'};
            background: var(--card-bg, rgba(255, 255, 255, 0.95));
            border: 1px solid var(--border, #dee2e6);
            border-radius: 9999px;
            padding: ${isCompact ? '3px 6px' : '4px 8px'};
            box-shadow: 0 4px 20px rgba(0, 0, 0, 0.16), 0 1px 4px rgba(0, 0, 0, 0.05);
            user-select: none;
            max-width: calc(100% - 24px);
            backdrop-filter: blur(12px);
            -webkit-backdrop-filter: blur(12px);
            transition: all 0.2s ease;
        `;

        if (needsNav) {
            const pagePill = document.createElement('div');
            pagePill.className = 'viewer-page-nav-pill';
            pagePill.style.cssText = `display: inline-flex; align-items: center; gap: 2px;`;

            const prevButton = this.createPillIconButton('bi-chevron-left', 'Previous Page', () => this.previousPage(), isCompact);
            prevButton.id = 'viewer-prev-btn';
            prevButton.classList.add('viewer-prev-btn');

            const nextButton = this.createPillIconButton('bi-chevron-right', 'Next Page', () => this.nextPage(), isCompact);
            nextButton.id = 'viewer-next-btn';
            nextButton.classList.add('viewer-next-btn');

            const pageIndicator = document.createElement('div');
            pageIndicator.className = 'viewer-page-indicator';
            pageIndicator.style.cssText = `
                display: flex;
                align-items: center;
                gap: 3px;
                font-size: ${isCompact ? '0.75rem' : '0.825rem'};
                font-weight: 500;
                color: var(--text-secondary, #6c757d);
                padding: 0 ${isCompact ? '4px' : '6px'};
                white-space: nowrap;
                user-select: none;
            `;
            pageIndicator.innerHTML = `
                <span style="opacity: 0.75; font-size: ${isCompact ? '0.7rem' : '0.78rem'}; margin-right: 2px;">Page</span>
                <span id="viewer-current-page" class="viewer-current-page" style="color: var(--text-dark, #212529); font-weight: 600;">${this.currentPage}</span>
                <span style="opacity: 0.6; font-size: ${isCompact ? '0.7rem' : '0.78rem'}; margin: 0 1px;">of</span>
                <span id="viewer-total-pages" class="viewer-total-pages" style="color: var(--text-dark, #212529); font-weight: 600;">${this.totalPages || '?'}</span>
            `;

            pagePill.appendChild(prevButton);
            pagePill.appendChild(pageIndicator);
            pagePill.appendChild(nextButton);
            this.floatingBar.appendChild(pagePill);
        }

        if (needsNav && needsZoom) {
            const divider = document.createElement('div');
            divider.className = 'viewer-floating-divider';
            divider.style.cssText = `
                width: 1px;
                height: 18px;
                background: var(--border, #dee2e6);
                margin: 0 2px;
                opacity: 0.7;
            `;
            this.floatingBar.appendChild(divider);
        }

        if (needsZoom) {
            const zoomPill = document.createElement('div');
            zoomPill.className = 'viewer-zoom-pill';
            zoomPill.style.cssText = `display: inline-flex; align-items: center; gap: 2px;`;

            const zoomOutButton = this.createPillIconButton('bi-dash', 'Zoom Out', async () => await this.viewer.zoomOut(), isCompact);
            const zoomInButton = this.createPillIconButton('bi-plus', 'Zoom In', async () => await this.viewer.zoomIn(), isCompact);

            const zoomIndicator = document.createElement('div');
            zoomIndicator.id = 'viewer-zoom-indicator';
            zoomIndicator.classList.add('viewer-zoom-indicator');
            zoomIndicator.style.cssText = `
                font-size: ${isCompact ? '0.72rem' : '0.8rem'};
                color: var(--text-dark, #212529);
                font-weight: 600;
                min-width: ${isCompact ? '32px' : '40px'};
                text-align: center;
                white-space: nowrap;
                user-select: none;
            `;
            zoomIndicator.textContent = '100%';

            zoomPill.appendChild(zoomOutButton);
            zoomPill.appendChild(zoomIndicator);
            zoomPill.appendChild(zoomInButton);
            this.floatingBar.appendChild(zoomPill);
        }

        if (needsScroll) {
            const scrollDivider = document.createElement('div');
            scrollDivider.className = 'viewer-floating-divider';
            scrollDivider.style.cssText = `width: 1px; height: 18px; background: var(--border, #dee2e6); margin: 0 2px; opacity: 0.7;`;
            this.floatingBar.appendChild(scrollDivider);

            const scrollUpBtn = this.createPillIconButton('bi-arrow-up', 'Scroll Up', () => this.viewer.scrollUp(), isCompact);
            const scrollDownBtn = this.createPillIconButton('bi-arrow-down', 'Scroll Down', () => this.viewer.scrollDown(), isCompact);
            this.floatingBar.appendChild(scrollUpBtn);
            this.floatingBar.appendChild(scrollDownBtn);
        }

        this.container.appendChild(this.floatingBar);
        this.updateNavigationButtons();
    }

    createPillIconButton(icon, title, onClick, isCompact = false) {
        const button = document.createElement('button');
        button.className = 'viewer-pill-btn';
        button.type = 'button';
        button.title = title;
        button.setAttribute('aria-label', title);
        const size = isCompact ? '24px' : '28px';
        const iconSize = isCompact ? '0.75rem' : '0.85rem';
        button.style.cssText = `
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: ${size};
            height: ${size};
            padding: 0;
            border: none;
            border-radius: 50%;
            background: transparent;
            color: var(--text-secondary, #6c757d);
            cursor: pointer;
            transition: all 0.15s ease;
        `;
        button.innerHTML = `<i class="bi ${icon}" style="font-size: ${iconSize}; line-height: 1;"></i>`;
        button.onclick = onClick;

        button.onmouseenter = () => {
            if (!button.disabled) {
                button.style.background = 'rgba(0, 0, 0, 0.08)';
                button.style.color = 'var(--text-dark, #212529)';
            }
        };
        button.onmouseleave = () => {
            button.style.background = 'transparent';
            button.style.color = 'var(--text-secondary, #6c757d)';
        };

        return button;
    }

    createButton(icon, title, onClick, isCompact = false, extraClass = '') {
        const button = document.createElement('button');
        button.className = `viewer-btn ${extraClass}`.trim();
        button.type = 'button';
        button.title = title;
        button.setAttribute('aria-label', title);
        const size = isCompact ? '28px' : '32px';
        const iconSize = isCompact ? '0.8rem' : '0.875rem';
        button.style.cssText = `
            display: inline-flex;
            align-items: center;
            justify-content: center;
            width: ${size};
            height: ${size};
            padding: 0;
            border: 1px solid var(--border, #dee2e6);
            border-radius: 8px;
            background: var(--card-bg, #ffffff);
            color: var(--text-secondary, #6c757d);
            cursor: pointer;
            transition: all 0.15s ease;
            box-shadow: 0 1px 2px rgba(0,0,0,0.02);
        `;
        button.innerHTML = `<i class="bi ${icon}" style="font-size: ${iconSize}; line-height: 1;"></i>`;
        button.onclick = onClick;

        button.onmouseenter = () => {
            if (!button.disabled) {
                button.style.borderColor = 'var(--primary, #0d6efd)';
                button.style.color = 'var(--primary, #0d6efd)';
                button.style.background = 'rgba(13, 110, 253, 0.06)';
            }
        };
        button.onmouseleave = () => {
            button.style.borderColor = 'var(--border, #dee2e6)';
            button.style.color = 'var(--text-secondary, #6c757d)';
            button.style.background = 'var(--card-bg, #ffffff)';
        };

        return button;
    }

    updateNavigationButtons() {
        if (!this.isPaginated) return;
        const prevBtn = (this.floatingBar && this.floatingBar.querySelector('.viewer-prev-btn')) ||
                        (this.toolbar && this.toolbar.querySelector('.viewer-prev-btn')) ||
                        (this.container && this.container.querySelector('.viewer-prev-btn')) ||
                        document.getElementById('viewer-prev-btn');
        const nextBtn = (this.floatingBar && this.floatingBar.querySelector('.viewer-next-btn')) ||
                        (this.toolbar && this.toolbar.querySelector('.viewer-next-btn')) ||
                        (this.container && this.container.querySelector('.viewer-next-btn')) ||
                        document.getElementById('viewer-next-btn');

        if (prevBtn) {
            const isAtStart = this.currentPage <= 1;
            prevBtn.disabled = isAtStart;
            prevBtn.style.opacity = isAtStart ? '0.3' : '1';
            prevBtn.style.cursor = isAtStart ? 'not-allowed' : 'pointer';
        }

        if (nextBtn) {
            const isAtEnd = Boolean(this.totalPages && this.currentPage >= this.totalPages);
            nextBtn.disabled = isAtEnd;
            nextBtn.style.opacity = isAtEnd ? '0.3' : '1';
            nextBtn.style.cursor = isAtEnd ? 'not-allowed' : 'pointer';
        }
    }

    updatePageIndicator() {
        if (!this.isPaginated) return;
        const currentPageEl = (this.floatingBar && this.floatingBar.querySelector('.viewer-current-page')) ||
                              (this.toolbar && this.toolbar.querySelector('.viewer-current-page')) ||
                              (this.container && this.container.querySelector('.viewer-current-page')) ||
                              document.getElementById('viewer-current-page');
        const totalPagesEl = (this.floatingBar && this.floatingBar.querySelector('.viewer-total-pages')) ||
                            (this.toolbar && this.toolbar.querySelector('.viewer-total-pages')) ||
                            (this.container && this.container.querySelector('.viewer-total-pages')) ||
                            document.getElementById('viewer-total-pages');

        if (currentPageEl) currentPageEl.textContent = this.currentPage;
        if (totalPagesEl) totalPagesEl.textContent = this.totalPages || '?';
    }

    /**
     * Direct page navigation with strict boundary enforcement.
     */
    async goToPage(pageNum) {
        if (!this.isPaginated || !this.totalPages) return;
        const target = parseInt(pageNum, 10);
        if (isNaN(target) || target < 1 || target > this.totalPages) return;
        if (target === this.currentPage) return;

        this.currentPage = target;
        if (this.viewer && typeof this.viewer.goToPage === 'function') {
            await this.viewer.goToPage(this.currentPage);
        }
        this.updatePageIndicator();
        this.updateNavigationButtons();
        this._notifyPageChange();
        this._emitStateChange();
    }

    async nextPage() {
        if (!this.isPaginated || !this.totalPages) return;
        if (this.currentPage >= this.totalPages) return;
        await this.goToPage(this.currentPage + 1);
    }

    async previousPage() {
        if (!this.isPaginated || !this.totalPages) return;
        if (this.currentPage <= 1) return;
        await this.goToPage(this.currentPage - 1);
    }

    setupKeyboardNavigation() {
        this._keydownHandler = (e) => {
            // Only handle if viewer is focused, container contains active element, or is fullscreen
            const isTargeted = this.isFullscreen || (this.container && (this.container === document.activeElement || this.container.contains(document.activeElement)));
            if (!isTargeted) return;

            if (e.key === 'ArrowRight' || e.key === 'PageDown') {
                if (this.isPaginated) {
                    e.preventDefault();
                    this.nextPage();
                }
            } else if (e.key === 'ArrowLeft' || e.key === 'PageUp') {
                if (this.isPaginated) {
                    e.preventDefault();
                    this.previousPage();
                }
            } else if (e.key === 'Escape' && this.isFullscreen) {
                e.preventDefault();
                this.exitFullscreen();
            }
        };
        document.addEventListener('keydown', this._keydownHandler);
    }

    toggleFullscreen() {
        if (this.isFullscreen) {
            this.exitFullscreen();
        } else {
            this.enterFullscreen();
        }
    }

    enterFullscreen() {
        this.isFullscreen = true;

        const reqFs = this.container.requestFullscreen ||
                      this.container.webkitRequestFullscreen ||
                      this.container.msRequestFullscreen;
        if (reqFs) {
            try {
                reqFs.call(this.container);
            } catch (e) {
                console.warn('[DocumentViewer] Native requestFullscreen failed, using fixed CSS fallback:', e);
            }
        }

        // Apply fullscreen CSS layout
        this.container.classList.add('viewer-fullscreen');
        this.container.style.position = 'fixed';
        this.container.style.top = '0';
        this.container.style.left = '0';
        this.container.style.width = '100vw';
        this.container.style.height = '100vh';
        this.container.style.zIndex = '99999';
        this.container.style.borderRadius = '0';
        this.container.style.background = '#323639';

        const viewerContainer = this.container.querySelector(
            '.pdf-viewer-container, .docx-viewer-container, .text-viewer-container, .image-viewer-container, .pptx-viewer-container, .unsupported-viewer-container'
        );
        if (viewerContainer) {
            viewerContainer.style.height = 'calc(100vh - 56px)';
            viewerContainer.style.maxHeight = 'calc(100vh - 56px)';
        }

        this.updateFullscreenButton();

        if (!this._fullscreenHandler) {
            this._fullscreenHandler = () => {
                const isDocFullscreen = Boolean(
                    document.fullscreenElement ||
                    document.webkitFullscreenElement ||
                    document.msFullscreenElement
                );
                if (!isDocFullscreen && this.isFullscreen) {
                    this.exitFullscreen(false);
                }
            };
            document.addEventListener('fullscreenchange', this._fullscreenHandler);
            document.addEventListener('webkitfullscreenchange', this._fullscreenHandler);
        }

        this.handleResize();
        this._emitStateChange();
    }

    exitFullscreen(callNativeExit = true) {
        this.isFullscreen = false;

        if (callNativeExit) {
            const exitFs = document.exitFullscreen ||
                           document.webkitExitFullscreen ||
                           document.msExitFullscreen;
            if (exitFs && (document.fullscreenElement || document.webkitFullscreenElement || document.msFullscreenElement)) {
                try {
                    exitFs.call(document);
                } catch (e) {}
            }
        }

        this.container.classList.remove('viewer-fullscreen');
        this.container.style.position = 'relative';
        this.container.style.top = '';
        this.container.style.left = '';
        this.container.style.width = '';
        this.container.style.height = '';
        this.container.style.zIndex = '';
        this.container.style.borderRadius = '';
        this.container.style.background = '';

        const viewerContainer = this.container.querySelector(
            '.pdf-viewer-container, .docx-viewer-container, .text-viewer-container, .image-viewer-container, .pptx-viewer-container, .unsupported-viewer-container'
        );
        if (viewerContainer) {
            if (this.options && this.options.fitContainer) {
                viewerContainer.style.height = '100%';
                viewerContainer.style.maxHeight = '';
            } else {
                viewerContainer.style.height = '70vh';
                viewerContainer.style.maxHeight = '';
            }
        }

        this.updateFullscreenButton();
        this.handleResize();
        this._emitStateChange();
    }

    updateFullscreenButton() {
        if (!this.toolbar) return;
        const btn = this.toolbar.querySelector('.viewer-fullscreen-btn');
        if (btn) {
            if (this.isFullscreen) {
                btn.innerHTML = '<i class="bi bi-arrows-angle-contract" style="font-size: 1rem; line-height: 1;"></i>';
                btn.title = 'Exit Fullscreen';
            } else {
                btn.innerHTML = '<i class="bi bi-arrows-fullscreen" style="font-size: 1rem; line-height: 1;"></i>';
                btn.title = 'Fullscreen';
            }
        }
    }

    addTouchGestures() {
        this.container.addEventListener('touchstart', (e) => {
            if (e.touches.length === 2) {
                this.isPinching = true;
                this.initialPinchDistance = this.getPinchDistance(e.touches);
                this.pinchCenterX = (e.touches[0].clientX + e.touches[1].clientX) / 2;
                this.pinchCenterY = (e.touches[0].clientY + e.touches[1].clientY) / 2;
                this.lastTranslateX = this.translateX;
                this.lastTranslateY = this.translateY;
            } else if (e.touches.length === 1) {
                this.touchStartX = e.touches[0].clientX;
                this.touchStartY = e.touches[0].clientY;
                this.lastTouchX = e.touches[0].clientX;
                this.lastTouchY = e.touches[0].clientY;
            }
        }, { passive: true });

        this.container.addEventListener('touchmove', (e) => {
            if (this.isPinching && e.touches.length === 2) {
                e.preventDefault();
                const currentDistance = this.getPinchDistance(e.touches);
                const scale = currentDistance / (this.initialPinchDistance || 1);
                this.currentScale = Math.min(Math.max(scale, 0.5), 3.0);
                this.applyTransform();
            } else if (e.touches.length === 1 && this.currentScale > 1) {
                e.preventDefault();
                const touch = e.touches[0];
                const deltaX = touch.clientX - this.lastTouchX;
                const deltaY = touch.clientY - this.lastTouchY;
                this.translateX += deltaX;
                this.translateY += deltaY;
                this.lastTouchX = touch.clientX;
                this.lastTouchY = touch.clientY;
                this.applyTransform();
            }
        }, { passive: false });

        this.container.addEventListener('touchend', (e) => {
            if (e.touches.length === 0) {
                if (this.isPinching) {
                    this.isPinching = false;
                    this.lastTranslateX = this.translateX;
                    this.lastTranslateY = this.translateY;
                } else if (e.changedTouches && e.changedTouches.length > 0) {
                    this.touchEndX = e.changedTouches[0].clientX;
                    this.touchEndY = e.changedTouches[0].clientY;
                    this.handleSwipe();
                }
            }
        }, { passive: true });
    }

    getPinchDistance(touches) {
        const dx = touches[0].clientX - touches[1].clientX;
        const dy = touches[0].clientY - touches[1].clientY;
        return Math.sqrt(dx * dx + dy * dy);
    }

    applyTransform() {
        const viewerContainer = this.container.querySelector('.image-viewer-container, .pdf-viewer-container, .docx-viewer-container, .text-viewer-container');
        if (viewerContainer) {
            const content = viewerContainer.querySelector('img, canvas, .pdf-viewer-container > div, .docx-viewer-container > div, .text-viewer-container > div');
            if (content) {
                const originX = this.isPinching ? `${this.pinchCenterX}px` : 'center';
                const originY = this.isPinching ? `${this.pinchCenterY}px` : 'center';
                content.style.transform = `translate(${this.translateX}px, ${this.translateY}px) scale(${this.currentScale})`;
                content.style.transformOrigin = `${originX} ${originY}`;
                content.style.transition = this.isPinching ? 'none' : 'transform 0.1s ease-out';
                if (this.currentScale <= 1) {
                    this.translateX = 0;
                    this.translateY = 0;
                }
            }
        }
    }

    handleSwipe() {
        const deltaX = this.touchEndX - this.touchStartX;
        const deltaY = this.touchEndY - this.touchStartY;
        if (this.currentScale <= 1) {
            if (Math.abs(deltaX) > Math.abs(deltaY) && Math.abs(deltaX) > this.minSwipeDistance) {
                if (deltaX > 0) {
                    this.previousPage();
                } else {
                    this.nextPage();
                }
            } else if (Math.abs(deltaY) > this.minSwipeDistance && this.viewer && this.viewer.needsScroll()) {
                if (deltaY > 0) {
                    this.viewer.scrollUp();
                } else {
                    this.viewer.scrollDown();
                }
            }
        }
    }

    trackAndDownload() {
        if (this.documentId && this.fileId) {
            fetch('/documents/document/' + this.documentId + '/download/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/x-www-form-urlencoded',
                    'X-CSRFToken': this.getCookie('csrftoken')
                },
                body: 'file_id=' + encodeURIComponent(this.fileId)
            })
            .then(response => response.json())
            .then(data => {
                if (data && data.success) {
                    const countEl = document.querySelector('.doc-stats div:nth-child(1) p:first-child');
                    if (countEl && data.download_count !== undefined) {
                        countEl.textContent = data.download_count;
                    }
                }
            })
            .catch(error => console.warn('[DocumentViewer] Download tracking error:', error));
        }

        if (window.downloadManager) {
            window.downloadManager.download({
                url: this.fileUrl,
                postId: this.documentId,
                mediaType: 'document',
                category: 'document',
                filename: this.fileName || `document_${Date.now()}`,
                thumbnail: this.thumbnail
            }).catch(err => {
                console.warn('[DocumentViewer] downloadManager fallback:', err);
                window.open(this.fileUrl, '_blank');
            });
        } else {
            window.open(this.fileUrl, '_blank');
        }
    }

    getCookie(name) {
        let cookieValue = null;
        if (document.cookie && document.cookie !== '') {
            const cookies = document.cookie.split(';');
            for (let i = 0; i < cookies.length; i++) {
                const cookie = cookies[i].trim();
                if (cookie.substring(0, name.length + 1) === (name + '=')) {
                    cookieValue = decodeURIComponent(cookie.substring(name.length + 1));
                    break;
                }
            }
        }
        return cookieValue;
    }

    setupResizeObserver() {
        if (typeof ResizeObserver !== 'undefined' && this.container) {
            this.resizeObserver = new ResizeObserver(() => {
                this.handleResize();
            });
            this.resizeObserver.observe(this.container);
        }
    }

    handleResize() {
        if (this.viewer && typeof this.viewer.handleResize === 'function') {
            this.viewer.handleResize();
        }
    }

    destroy() {
        if (this.isFullscreen) {
            this.exitFullscreen(false);
        }
        if (this.resizeObserver) {
            this.resizeObserver.disconnect();
            this.resizeObserver = null;
        }
        if (this._keydownHandler) {
            document.removeEventListener('keydown', this._keydownHandler);
            this._keydownHandler = null;
        }
        if (this._fullscreenHandler) {
            document.removeEventListener('fullscreenchange', this._fullscreenHandler);
            document.removeEventListener('webkitfullscreenchange', this._fullscreenHandler);
            this._fullscreenHandler = null;
        }
        if (this.viewer && typeof this.viewer.destroy === 'function') {
            this.viewer.destroy();
            this.viewer = null;
        }
        if (this.floatingBar) {
            this.floatingBar.remove();
            this.floatingBar = null;
        }
        if (this.toolbar) {
            this.toolbar.remove();
            this.toolbar = null;
        }
        if (this.container) {
            this.container.innerHTML = '';
            delete this.container._documentViewer;
        }
    }
}

/**
 * Factory for instantiating format-specific renderers
 */
class ViewerFactory {
    static normalizeFileType(fileType, fileUrl = '', options = {}) {
        let ft = (fileType || '').toLowerCase().trim();
        if (ft.startsWith('.')) {
            ft = ft.substring(1);
        }
        if (!ft) {
            const candidate = (fileUrl || (options && (options.fileName || options.title || options.name)) || '').split('?')[0].split('#')[0];
            const match = candidate.match(/\.([a-zA-Z0-9]+)$/);
            if (match) {
                ft = match[1].toLowerCase();
            }
        }
        return ft;
    }

    static createViewer(fileType, container, fileUrl, options = {}) {
        const ft = ViewerFactory.normalizeFileType(fileType, fileUrl, options);
        switch (ft) {
            case 'pdf':
                return new PDFViewer(container, fileUrl, options);
            case 'docx':
            case 'doc':
                return new DOCXViewer(container, fileUrl, options);
            case 'pptx':
            case 'ppt':
                return new PPTXViewer(container, fileUrl, options);
            case 'txt':
            case 'md':
                return new TextViewer(container, fileUrl, ft === 'md', options);
            case 'png':
            case 'jpg':
            case 'jpeg':
            case 'gif':
            case 'webp':
                return new ImageViewer(container, fileUrl, options);
            default:
                return new UnsupportedViewer(container, fileUrl, ft, options);
        }
    }
}

/**
 * PDF Viewer using PDF.js with high-DPI scaling, render task cancellation,
 * and responsive width fitting.
 */
class PDFViewer {
    constructor(container, fileUrl, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.options = options || {};
        this.pdfDoc = null;
        this.currentPage = (this.options.initialPage && !isNaN(this.options.initialPage)) ? Math.max(1, parseInt(this.options.initialPage, 10)) : 1;
        this.scale = 1.0;
        this.canvas = null;
        this.currentRenderTask = null;
        this._resizeTimeout = null;
    }

    async load() {
        if (typeof pdfjsLib === 'undefined') {
            await this.loadPDFJS();
        }
        if (typeof pdfjsLib !== 'undefined' && pdfjsLib.GlobalWorkerOptions && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
            pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
        }

        const loadingTask = pdfjsLib.getDocument(this.fileUrl);
        this.pdfDoc = await loadingTask.promise;

        if (this.currentPage > this.pdfDoc.numPages) {
            this.currentPage = this.pdfDoc.numPages;
        }
        this.createCanvas();
        await this.renderPage(this.currentPage);
    }

    async loadPDFJS() {
        if (typeof pdfjsLib !== 'undefined') {
            if (pdfjsLib.GlobalWorkerOptions && !pdfjsLib.GlobalWorkerOptions.workerSrc) {
                pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
            }
            return Promise.resolve();
        }

        return new Promise((resolve, reject) => {
            const existingScript = document.querySelector('script[src*="pdf.min.js"]');
            if (existingScript) {
                if (typeof pdfjsLib !== 'undefined') {
                    if (pdfjsLib.GlobalWorkerOptions) {
                        pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
                    }
                    return resolve();
                }
                existingScript.addEventListener('load', () => {
                    if (typeof pdfjsLib !== 'undefined' && pdfjsLib.GlobalWorkerOptions) {
                        pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
                    }
                    resolve();
                });
                let checkCount = 0;
                const checkInterval = setInterval(() => {
                    checkCount++;
                    if (typeof pdfjsLib !== 'undefined') {
                        clearInterval(checkInterval);
                        if (pdfjsLib.GlobalWorkerOptions) {
                            pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
                        }
                        resolve();
                    } else if (checkCount > 30) {
                        clearInterval(checkInterval);
                        appendScript();
                    }
                }, 50);
                return;
            }

            appendScript();

            function appendScript() {
                const script = document.createElement('script');
                script.src = '/static/vendor/pdfjs/pdf.min.js';
                script.onload = () => {
                    if (typeof pdfjsLib !== 'undefined' && pdfjsLib.GlobalWorkerOptions) {
                        pdfjsLib.GlobalWorkerOptions.workerSrc = '/static/vendor/pdfjs/pdf.worker.min.js';
                    }
                    resolve();
                };
                script.onerror = () => reject(new Error('Failed to load PDF.js library'));
                document.head.appendChild(script);
            }
        });
    }

    createCanvas() {
        this.canvas = document.createElement('canvas');
        this.canvas.style.cssText = 'width: 100%; height: auto; display: block; max-width: 100%;';

        const viewerContainer = document.createElement('div');
        viewerContainer.className = 'pdf-viewer-container';

        if (this.options && this.options.fitContainer) {
            viewerContainer.style.cssText = `
                height: 100%;
                min-height: 0;
                flex: 1 1 0;
                overflow: auto;
                background: #525659;
                display: flex;
                justify-content: center;
                align-items: flex-start;
                padding: 12px;
                border-radius: 0;
            `;
        } else {
            const viewportHeight = typeof window !== 'undefined' ? window.innerHeight : 800;
            let minHeight = 280;
            if (viewportHeight <= 400) minHeight = 200;
            else if (viewportHeight <= 500) minHeight = 220;
            else if (viewportHeight <= 600) minHeight = 250;

            viewerContainer.style.cssText = `
                height: 70vh;
                min-height: ${minHeight}px;
                overflow: auto;
                background: #525659;
                display: flex;
                justify-content: center;
                align-items: flex-start;
                padding: 20px;
                border-radius: 0 0 12px 12px;
            `;
        }

        const pageWrapper = document.createElement('div');
        pageWrapper.className = 'pdf-page-wrapper';
        pageWrapper.style.cssText = 'box-shadow: 0 4px 16px rgba(0,0,0,0.35); max-width: 100%; overflow: hidden; background: #ffffff;';
        pageWrapper.appendChild(this.canvas);

        viewerContainer.appendChild(pageWrapper);
        this.container.appendChild(viewerContainer);
    }

    async renderPage(pageNum) {
        if (!this.pdfDoc || !this.canvas) return;

        // Cancel any pending render task to prevent collision
        if (this.currentRenderTask) {
            try {
                this.currentRenderTask.cancel();
            } catch (e) {}
            this.currentRenderTask = null;
        }

        const page = await this.pdfDoc.getPage(pageNum);

        // Container-aware scale fitting
        const containerWidth = this.container.clientWidth || 800;
        const unscaledViewport = page.getViewport({ scale: 1.0 });
        const padding = (this.options && this.options.fitContainer) ? 24 : 40;
        const availableWidth = Math.max(260, containerWidth - padding);
        const fitScale = availableWidth / unscaledViewport.width;
        const computedScale = fitScale * this.scale;

        const viewport = page.getViewport({ scale: computedScale });
        const pixelRatio = (typeof window !== 'undefined' && window.devicePixelRatio) ? window.devicePixelRatio : 1;

        this.canvas.width = Math.floor(viewport.width * pixelRatio);
        this.canvas.height = Math.floor(viewport.height * pixelRatio);
        this.canvas.style.width = Math.floor(viewport.width) + 'px';
        this.canvas.style.height = Math.floor(viewport.height) + 'px';

        const canvasContext = this.canvas.getContext('2d');
        canvasContext.save();
        canvasContext.scale(pixelRatio, pixelRatio);

        const renderContext = {
            canvasContext: canvasContext,
            viewport: viewport
        };

        try {
            this.currentRenderTask = page.render(renderContext);
            await this.currentRenderTask.promise;
        } catch (err) {
            if (err && err.name === 'RenderingCancelledException') {
                return; // Expected upon rapid navigation or resize
            }
            throw err;
        } finally {
            canvasContext.restore();
            this.currentRenderTask = null;
        }
    }

    async goToPage(pageNum) {
        this.currentPage = pageNum;
        await this.renderPage(pageNum);
    }

    getTotalPages() {
        return this.pdfDoc ? this.pdfDoc.numPages : null;
    }

    needsToolbar() { return true; }
    needsPageNavigation() { return true; }
    needsZoom() { return true; }
    needsScroll() { return false; }
    scrollUp() {}
    scrollDown() {}

    async zoomIn() {
        this.scale = Math.min(this.scale + 0.25, 3.0);
        await this.renderPage(this.currentPage);
        this.updateZoomIndicator();
    }

    async zoomOut() {
        this.scale = Math.max(this.scale - 0.25, 0.5);
        await this.renderPage(this.currentPage);
        this.updateZoomIndicator();
    }

    updateZoomIndicator() {
        const indicator = (this.container && this.container.querySelector('.viewer-zoom-indicator')) ||
                          (this.container && this.container.parentNode && this.container.parentNode.querySelector('.viewer-zoom-indicator')) ||
                          document.querySelector('.viewer-zoom-indicator');
        if (indicator) {
            const percentage = Math.round(this.scale * 100);
            indicator.textContent = `${percentage}%`;
        }
    }

    handleResize() {
        if (!this.pdfDoc || !this.canvas || !this.currentPage) return;
        if (this._resizeTimeout) clearTimeout(this._resizeTimeout);
        this._resizeTimeout = setTimeout(() => {
            if (this.pdfDoc && this.canvas) {
                this.renderPage(this.currentPage).catch(() => {});
            }
        }, 150);
    }

    destroy() {
        if (this._resizeTimeout) clearTimeout(this._resizeTimeout);
        if (this.currentRenderTask) {
            try { this.currentRenderTask.cancel(); } catch (e) {}
            this.currentRenderTask = null;
        }
        if (this.pdfDoc) {
            try { this.pdfDoc.destroy(); } catch (e) {}
            this.pdfDoc = null;
        }
    }
}

/**
 * DOCX Viewer using Mammoth.js
 */
class DOCXViewer {
    constructor(container, fileUrl, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.options = options || {};
    }

    async load() {
        if (typeof mammoth === 'undefined') {
            await this.loadMammoth();
        }

        const response = await fetch(this.fileUrl);
        if (!response.ok) {
            throw new Error(`Failed to download DOCX document (HTTP ${response.status})`);
        }
        const arrayBuffer = await response.arrayBuffer();
        const result = await mammoth.convertToHtml({ arrayBuffer });
        this.createContent(result.value);
    }

    async loadMammoth() {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/static/vendor/mammoth/mammoth.browser.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    createContent(html) {
        const viewerContainer = document.createElement('div');
        viewerContainer.className = 'docx-viewer-container';

        if (this.options && this.options.fitContainer) {
            viewerContainer.style.cssText = `
                height: 100%;
                min-height: 0;
                flex: 1 1 auto;
                overflow: auto;
                background: white;
                padding: 20px;
                border-radius: 0;
            `;
        } else {
            viewerContainer.style.cssText = `
                height: 70vh;
                min-height: 280px;
                overflow: auto;
                background: white;
                padding: 32px;
                border-radius: 0 0 12px 12px;
            `;
        }

        viewerContainer.innerHTML = `
            <div class="docx-content-body" style="max-width: 860px; margin: 0 auto; font-family: 'Times New Roman', serif; font-size: 1.05rem; line-height: 1.65; color: #212529;">
                ${html}
            </div>
        `;

        this.container.appendChild(viewerContainer);
    }

    goToPage() {}
    getTotalPages() { return null; }
    needsToolbar() { return true; }
    needsPageNavigation() { return false; }
    needsZoom() { return true; }
    needsScroll() { return true; }

    scrollUp() {
        const container = this.container.querySelector('.docx-viewer-container');
        if (container) container.scrollBy({ top: -200, behavior: 'smooth' });
    }

    scrollDown() {
        const container = this.container.querySelector('.docx-viewer-container');
        if (container) container.scrollBy({ top: 200, behavior: 'smooth' });
    }

    async zoomIn() {
        const body = this.container.querySelector('.docx-content-body');
        if (body) {
            const currentSize = parseFloat(window.getComputedStyle(body).fontSize) || 16;
            body.style.fontSize = `${Math.min(currentSize + 2, 28)}px`;
        }
    }

    async zoomOut() {
        const body = this.container.querySelector('.docx-content-body');
        if (body) {
            const currentSize = parseFloat(window.getComputedStyle(body).fontSize) || 16;
            body.style.fontSize = `${Math.max(currentSize - 2, 11)}px`;
        }
    }

    destroy() {}
}

/**
 * PPTX Viewer - Presentation placeholder with metadata and download CTA
 */
class PPTXViewer {
    constructor(container, fileUrl, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.options = options || {};
    }

    async load() {
        this.createContent();
    }

    createContent() {
        const heightStyle = (this.options && this.options.fitContainer) ? 'height: 100%; min-height: 200px; flex: 1 1 auto;' : 'height: 600px;';
        this.container.innerHTML = `
            <div class="pptx-viewer-container" style="${heightStyle} display: flex; flex-direction: column; align-items: center; justify-content: center; background: linear-gradient(135deg, #DC2626 0%, #B91C1C 100%); border-radius: 0 0 12px 12px; text-align: center; padding: 32px;">
                <i class="bi bi-file-earmark-play" style="font-size: 4.5rem; color: white; margin-bottom: 20px;"></i>
                <h3 style="font-size: 1.35rem; font-weight: 600; color: white; margin: 0 0 10px 0;">Presentation Preview</h3>
                <p style="font-size: 0.95rem; color: rgba(255,255,255,0.9); margin: 0 0 20px 0; max-width: 440px;">PowerPoint slides cannot be rendered interactively in browser canvas. Download the presentation file to view slide decks.</p>
                <a href="${this.fileUrl}" download class="btn btn-light rounded-pill px-4 py-2 font-weight-bold" style="color: #B91C1C; text-decoration: none; font-weight: 600; display: inline-flex; align-items: center; gap: 8px;">
                    <i class="bi bi-download"></i> Download Presentation
                </a>
            </div>
        `;
    }

    goToPage() {}
    getTotalPages() { return null; }
    needsToolbar() { return true; }
    needsPageNavigation() { return false; }
    needsZoom() { return false; }
    needsScroll() { return false; }
    scrollUp() {}
    scrollDown() {}
    zoomIn() {}
    zoomOut() {}
    destroy() {}
}

/**
 * Text / Markdown Viewer
 */
class TextViewer {
    constructor(container, fileUrl, isMarkdown, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.isMarkdown = isMarkdown;
        this.options = options || {};
    }

    async load() {
        const response = await fetch(this.fileUrl);
        if (!response.ok) {
            throw new Error(`Failed to load text file (HTTP ${response.status})`);
        }
        const text = await response.text();

        let content = text;
        if (this.isMarkdown) {
            if (typeof marked === 'undefined') {
                await this.loadMarked();
            }
            if (typeof marked !== 'undefined') {
                content = marked.parse(text);
            }
        }

        this.createContent(content);
    }

    async loadMarked() {
        return new Promise((resolve, reject) => {
            const script = document.createElement('script');
            script.src = '/static/vendor/marked/marked.min.js';
            script.onload = resolve;
            script.onerror = reject;
            document.head.appendChild(script);
        });
    }

    createContent(content) {
        const viewerContainer = document.createElement('div');
        viewerContainer.className = 'text-viewer-container';

        if (this.options && this.options.fitContainer) {
            viewerContainer.style.cssText = `
                height: 100%;
                min-height: 0;
                flex: 1 1 auto;
                overflow: auto;
                background: white;
                padding: 20px;
                border-radius: 0;
            `;
        } else {
            viewerContainer.style.cssText = `
                height: 70vh;
                min-height: 280px;
                overflow: auto;
                background: white;
                padding: 32px;
                border-radius: 0 0 12px 12px;
            `;
        }

        viewerContainer.innerHTML = `
            <div class="text-content-body" style="max-width: 820px; margin: 0 auto; font-family: ${this.isMarkdown ? 'inherit' : 'Consolas, monospace'}; line-height: 1.6; white-space: ${this.isMarkdown ? 'normal' : 'pre-wrap'}; word-wrap: break-word;">
                ${this.isMarkdown ? content : this.escapeHtml(content)}
            </div>
        `;

        this.container.appendChild(viewerContainer);
    }

    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }

    goToPage() {}
    getTotalPages() { return null; }
    needsToolbar() { return true; }
    needsPageNavigation() { return false; }
    needsZoom() { return true; }
    needsScroll() { return true; }

    scrollUp() {
        const container = this.container.querySelector('.text-viewer-container');
        if (container) container.scrollBy({ top: -200, behavior: 'smooth' });
    }

    scrollDown() {
        const container = this.container.querySelector('.text-viewer-container');
        if (container) container.scrollBy({ top: 200, behavior: 'smooth' });
    }

    async zoomIn() {
        const body = this.container.querySelector('.text-content-body');
        if (body) {
            const current = parseFloat(window.getComputedStyle(body).fontSize) || 15;
            body.style.fontSize = `${Math.min(current + 2, 26)}px`;
        }
    }

    async zoomOut() {
        const body = this.container.querySelector('.text-content-body');
        if (body) {
            const current = parseFloat(window.getComputedStyle(body).fontSize) || 15;
            body.style.fontSize = `${Math.max(current - 2, 10)}px`;
        }
    }

    destroy() {}
}

/**
 * Image Viewer
 */
class ImageViewer {
    constructor(container, fileUrl, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.options = options || {};
        this.scale = 1.0;
    }

    async load() {
        this.createContent();
    }

    createContent() {
        const viewerContainer = document.createElement('div');
        viewerContainer.className = 'image-viewer-container';

        if (this.options && this.options.fitContainer) {
            viewerContainer.style.cssText = `
                height: 100%;
                min-height: 0;
                flex: 1 1 auto;
                overflow: auto;
                background: #f8f9fa;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 16px;
                border-radius: 0;
                position: relative;
            `;
        } else {
            viewerContainer.style.cssText = `
                height: 70vh;
                min-height: 280px;
                overflow: auto;
                background: #f8f9fa;
                display: flex;
                align-items: center;
                justify-content: center;
                padding: 20px;
                border-radius: 0 0 12px 12px;
                position: relative;
            `;
        }

        const img = document.createElement('img');
        img.src = this.fileUrl;
        img.style.cssText = 'max-width: 100%; max-height: 100%; object-fit: contain; display: block; box-shadow: 0 4px 12px rgba(0,0,0,0.1); border-radius: 4px;';
        img.alt = 'Document image preview';

        viewerContainer.appendChild(img);
        this.container.appendChild(viewerContainer);
    }

    goToPage() {}
    getTotalPages() { return null; }
    needsToolbar() { return true; }
    needsPageNavigation() { return false; }
    needsZoom() { return true; }
    needsScroll() { return false; }
    scrollUp() {}
    scrollDown() {}

    async zoomIn() {
        const img = this.container.querySelector('.image-viewer-container img');
        if (img) {
            this.scale = Math.min(this.scale + 0.25, 3.0);
            img.style.transform = `scale(${this.scale})`;
            img.style.transformOrigin = 'center center';
        }
    }

    async zoomOut() {
        const img = this.container.querySelector('.image-viewer-container img');
        if (img) {
            this.scale = Math.max(this.scale - 0.25, 0.5);
            img.style.transform = `scale(${this.scale})`;
            img.style.transformOrigin = 'center center';
        }
    }

    destroy() {}
}

/**
 * Unsupported Viewer - Graceful fallback card
 */
class UnsupportedViewer {
    constructor(container, fileUrl, fileType, options = {}) {
        this.container = container;
        this.fileUrl = fileUrl;
        this.fileType = (fileType || 'file').replace(/^\./, '');
        this.options = options || {};
    }

    async load() {
        this.createContent();
    }

    createContent() {
        const heightStyle = (this.options && this.options.fitContainer) ? 'height: 100%; min-height: 200px; flex: 1 1 auto;' : 'height: 600px;';
        const displayType = (this.fileType || 'Document').toUpperCase();
        this.container.innerHTML = `
            <div class="unsupported-viewer-container" style="${heightStyle} display: flex; flex-direction: column; align-items: center; justify-content: center; background: linear-gradient(135deg, var(--card-bg, #ffffff) 0%, var(--bg-surface, #f8f9fa) 100%); border: 1px solid var(--border, #dee2e6); border-radius: 0 0 12px 12px; text-align: center; padding: 32px; gap: 12px;">
                <div style="width: 60px; height: 60px; border-radius: 50%; background: rgba(13, 110, 253, 0.08); display: flex; align-items: center; justify-content: center;">
                    <i class="bi bi-file-earmark-text" style="font-size: 2rem; color: var(--primary, #0d6efd);"></i>
                </div>
                <h3 style="font-size: 1.25rem; font-weight: 600; color: var(--text-dark, #212529); margin: 0;">${displayType} Document</h3>
                <p style="font-size: 0.9rem; color: var(--text-secondary, #6c757d); max-width: 440px; margin: 0; line-height: 1.5;">
                    If the document preview did not load automatically, click Reload Document to reinitialize the viewer, or download the original file.
                </p>
                <div style="display: flex; gap: 10px; align-items: center; justify-content: center; margin-top: 6px; flex-wrap: wrap;">
                    <button type="button" class="btn btn-primary rounded-pill px-4 py-2 d-inline-flex align-items-center gap-2 viewer-retry-btn" id="viewer-retry-btn" style="cursor: pointer; font-weight: 600;">
                        <i class="bi bi-arrow-clockwise"></i> Reload Document
                    </button>
                    <a href="${this.fileUrl}" download class="btn btn-outline-secondary rounded-pill px-4 py-2 font-weight-bold viewer-download-btn" style="text-decoration: none; font-weight: 600; display: inline-flex; align-items: center; gap: 8px;">
                        <i class="bi bi-download"></i> Download File
                    </a>
                </div>
            </div>
        `;
        const retryBtn = this.container.querySelector('#viewer-retry-btn');
        if (retryBtn) {
            retryBtn.onclick = () => {
                if (typeof window !== 'undefined' && typeof window.reloadDocumentDetailViewer === 'function') {
                    window.reloadDocumentDetailViewer();
                } else if (this.options && typeof this.options.onRetry === 'function') {
                    this.options.onRetry();
                }
            };
        }
    }

    goToPage() {}
    getTotalPages() { return null; }
    needsToolbar() { return true; }
    needsPageNavigation() { return false; }
    needsZoom() { return false; }
    needsScroll() { return false; }
    scrollUp() {}
    scrollDown() {}
    zoomIn() {}
    zoomOut() {}
    destroy() {}
}

    // Global Browser Export
    root.DocumentViewer = DocumentViewer;
    root.ViewerFactory = ViewerFactory;
    root.PDFViewer = PDFViewer;
    root.DOCXViewer = DOCXViewer;
    root.PPTXViewer = PPTXViewer;
    root.TextViewer = TextViewer;
    root.ImageViewer = ImageViewer;
    root.UnsupportedViewer = UnsupportedViewer;

    // Node.js CommonJS Export for Testing
    if (typeof module !== 'undefined' && module.exports) {
        module.exports = {
            DocumentViewer,
            ViewerFactory,
            PDFViewer,
            DOCXViewer,
            PPTXViewer,
            TextViewer,
            ImageViewer,
            UnsupportedViewer
        };
    }
})(typeof window !== 'undefined' ? window : globalThis);
