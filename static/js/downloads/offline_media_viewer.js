/**
 * vasto - Offline Media Library & Fullscreen Reels Engine
 * Handles offline video streaming, document viewing, photos, and audio playback.
 */

(function(window) {
    'use strict';

    let allItems = [];
    let currentCategory = 'all';
    let searchQuery = '';
    let currentSort = 'newest';

    // Reels state
    let reelsObserver = null;
    let isReelsMuted = false;
    let activeReelVideo = null;
    let reelSources = new Map(); // id -> blob/stream url

    async function getServices() {
        let dm = window.downloadManager;
        let ds = window.downloadStorage;

        if (!dm || !ds) {
            try {
                const modManager = await import('/static/js/downloads/download_manager.js?v=20261106-group1');
                dm = modManager.downloadManager;
                window.downloadManager = dm;
            } catch (e) {
                console.warn('[OfflineMedia] Dynamic import of download_manager failed:', e);
            }
            try {
                const modStorage = await import('/static/js/downloads/download_storage.js?v=20261106-group1');
                ds = modStorage.downloadStorage;
                window.downloadStorage = ds;
            } catch (e) {
                console.warn('[OfflineMedia] Dynamic import of download_storage failed:', e);
            }
        }
        return { downloadManager: dm, downloadStorage: ds };
    }

    async function initOfflineMediaHub() {
        const hubElement = document.querySelector('.offline-media-container');
        if (!hubElement) {
            return;
        }

        const { downloadManager } = await getServices();
        if (downloadManager && typeof downloadManager.initialize === 'function') {
            await downloadManager.initialize();
        }

        await loadItems();
        setupEvents();
    }

    function cleanupOfflineMediaHub() {
        // Disconnect reels observer
        if (reelsObserver) {
            try { reelsObserver.disconnect(); } catch (_) {}
            reelsObserver = null;
        }

        // Close and clean reels player if active
        closeReelsPlayer();

        // Pause and release any media playing in modals
        const video = document.getElementById('offlinePlayerVideo');
        if (video) {
            try { video.pause(); video.src = ''; } catch (_) {}
        }
        const audio = document.getElementById('offlinePlayerAudio');
        if (audio) {
            try { audio.pause(); audio.src = ''; } catch (_) {}
        }
        const frame = document.getElementById('offlineDocFrame');
        if (frame) {
            try { frame.src = ''; } catch (_) {}
        }

        // Dispose any active Bootstrap modals
        ['offlineDocModal', 'offlineVideoModal', 'offlineImageModal', 'offlineAudioModal'].forEach(id => {
            const el = document.getElementById(id);
            if (el && typeof bootstrap !== 'undefined' && bootstrap.Modal) {
                const inst = bootstrap.Modal.getInstance(el);
                if (inst) {
                    try { inst.hide(); inst.dispose(); } catch (_) {}
                }
            }
        });

        // Clean up DOM overlays and body lock
        document.querySelectorAll('.modal-backdrop').forEach(el => el.remove());
        document.body.classList.remove('in-reels-mode', 'modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');

        // Remove keydown listener
        window.removeEventListener('keydown', handleReelsKeyDown);
        window.removeEventListener('vasto:download-completed', handleDownloadUpdate);
        window.removeEventListener('vasto:download-deleted', handleDownloadUpdate);
    }

    async function loadItems() {
        try {
            const { downloadManager, downloadStorage } = await getServices();
            if (!downloadManager) return;

            allItems = await downloadManager.getAllDownloads();
            renderStats();
            renderItems();

            // Self-heal: Cache remote thumbnails to Base64 if online
            if (navigator.onLine && Array.isArray(allItems) && downloadStorage) {
                allItems.forEach(async (it) => {
                    if (it.thumbnail && typeof it.thumbnail === 'string' && !it.thumbnail.startsWith('data:')) {
                        try {
                            const resp = await fetch(it.thumbnail);
                            if (resp.ok) {
                                const blob = await resp.blob();
                                const reader = new FileReader();
                                reader.onloadend = async () => {
                                    if (reader.result) {
                                        it.thumbnail = reader.result;
                                        await downloadStorage.saveMetadata(it);
                                    }
                                };
                                reader.readAsDataURL(blob);
                            }
                        } catch (err) { /* silent */ }
                    }
                });
            }
        } catch (e) {
            console.error('[OfflineMedia] Error loading items:', e);
        }
    }

    function handleDownloadUpdate() {
        if (document.querySelector('.offline-media-container')) {
            loadItems();
        }
    }

    function handleReelsKeyDown(e) {
        const reelsContainer = document.getElementById('offlineReelsContainer');
        if (!reelsContainer || reelsContainer.style.display === 'none') return;

        const slider = document.getElementById('offlineReelsSlider');
        if (!slider) return;

        if (e.key === 'Escape') {
            closeReelsPlayer();
        } else if (e.key === 'ArrowDown') {
            e.preventDefault();
            slider.scrollBy({ top: window.innerHeight, behavior: 'smooth' });
        } else if (e.key === 'ArrowUp') {
            e.preventDefault();
            slider.scrollBy({ top: -window.innerHeight, behavior: 'smooth' });
        } else if (e.key === ' ' || e.code === 'Space') {
            e.preventDefault();
            if (activeReelVideo) {
                if (activeReelVideo.paused) activeReelVideo.play();
                else activeReelVideo.pause();
            }
        } else if (e.key === 'm' || e.key === 'M') {
            toggleReelsMute();
        }
    }

    function setupEvents() {
        // Tab buttons
        document.querySelectorAll('#offlineCategoryTabs button').forEach(btn => {
            btn.onclick = () => {
                document.querySelectorAll('#offlineCategoryTabs button').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                currentCategory = btn.dataset.category;
                renderItems();
            };
        });

        // Search
        const searchInput = document.getElementById('offline-search-input');
        if (searchInput) {
            searchInput.oninput = (e) => {
                searchQuery = e.target.value;
                renderItems();
            };
        }

        // Sort
        const sortSelect = document.getElementById('offline-sort-select');
        if (sortSelect) {
            sortSelect.onchange = (e) => {
                currentSort = e.target.value;
                renderItems();
            };
        }

        // Clear All
        const clearBtn = document.getElementById('btn-clear-all-downloads');
        if (clearBtn) {
            clearBtn.onclick = async () => {
                if (confirm('Delete all downloaded offline media? This cannot be undone.')) {
                    const { downloadManager } = await getServices();
                    if (!downloadManager) return;
                    for (const item of allItems) {
                        await downloadManager.delete(item.id);
                    }
                    await loadItems();
                }
            };
        }

        // Header Watch Reels button
        const watchReelsHeader = document.getElementById('btn-watch-reels-header');
        if (watchReelsHeader) {
            watchReelsHeader.onclick = () => openReelsPlayer(0);
        }

        // Close Reels button
        const closeReelsBtn = document.getElementById('btn-close-reels');
        if (closeReelsBtn) {
            closeReelsBtn.onclick = closeReelsPlayer;
        }

        // Keyboard navigation for Reels
        window.removeEventListener('keydown', handleReelsKeyDown);
        window.addEventListener('keydown', handleReelsKeyDown);

        // Reactive updates
        window.removeEventListener('vasto:download-completed', handleDownloadUpdate);
        window.removeEventListener('vasto:download-deleted', handleDownloadUpdate);
        window.addEventListener('vasto:download-completed', handleDownloadUpdate);
        window.addEventListener('vasto:download-deleted', handleDownloadUpdate);

        // Stop fallback video playback on modal close
        const videoModal = document.getElementById('offlineVideoModal');
        if (videoModal) {
            videoModal.addEventListener('hidden.bs.modal', () => {
                const video = document.getElementById('offlinePlayerVideo');
                if (video) {
                    video.pause();
                    video.src = '';
                }
            });
        }

        // Stop audio playback on modal close
        const audioModal = document.getElementById('offlineAudioModal');
        if (audioModal) {
            audioModal.addEventListener('hidden.bs.modal', () => {
                const audio = document.getElementById('offlinePlayerAudio');
                if (audio) {
                    audio.pause();
                    audio.src = '';
                }
            });
        }

        // Clean document modal on close
        const docModal = document.getElementById('offlineDocModal');
        if (docModal) {
            docModal.addEventListener('hidden.bs.modal', () => {
                const frame = document.getElementById('offlineDocFrame');
                if (frame) frame.src = '';
            });
        }
    }

    async function renderStats() {
        const { downloadStorage } = await getServices();
        if (!downloadStorage) return;

        const stats = await downloadStorage.getStorageStats();
        const format = downloadStorage.formatBytes;

        const totalText = document.getElementById('stat-total-storage-text');
        if (totalText) totalText.textContent = format(stats.totalBytes);

        const countVideo = document.getElementById('stat-count-video');
        const countImage = document.getElementById('stat-count-image');
        const countAudio = document.getElementById('stat-count-audio');
        const countDoc = document.getElementById('stat-count-document');

        if (countVideo) countVideo.textContent = stats.counts.video;
        if (countImage) countImage.textContent = stats.counts.image;
        if (countAudio) countAudio.textContent = stats.counts.audio;
        if (countDoc) countDoc.textContent = stats.counts.document;

        const clearBtn = document.getElementById('btn-clear-all-downloads');
        if (clearBtn) {
            clearBtn.classList.toggle('d-none', allItems.length === 0);
        }

        // Show Watch Reels button if videos exist
        const watchReelsHeader = document.getElementById('btn-watch-reels-header');
        if (watchReelsHeader) {
            watchReelsHeader.classList.toggle('d-none', stats.counts.video === 0);
            watchReelsHeader.classList.toggle('d-flex', stats.counts.video > 0);
        }

        // Progress bar segments
        const barVideos = document.getElementById('bar-videos');
        const barPhotos = document.getElementById('bar-photos');
        const barAudios = document.getElementById('bar-audios');
        const barDocs = document.getElementById('bar-documents');

        if (stats.totalBytes > 0) {
            if (barVideos) barVideos.style.width = `${(stats.bytes.video / stats.totalBytes) * 100}%`;
            if (barPhotos) barPhotos.style.width = `${(stats.bytes.image / stats.totalBytes) * 100}%`;
            if (barAudios) barAudios.style.width = `${(stats.bytes.audio / stats.totalBytes) * 100}%`;
            if (barDocs) barDocs.style.width = `${(stats.bytes.document / stats.totalBytes) * 100}%`;
        } else {
            if (barVideos) barVideos.style.width = '0%';
            if (barPhotos) barPhotos.style.width = '0%';
            if (barAudios) barAudios.style.width = '0%';
            if (barDocs) barDocs.style.width = '0%';
        }
    }

    async function renderItems() {
        const { downloadManager } = await getServices();
        if (!downloadManager) return;

        let filtered = [...allItems];

        if (currentCategory !== 'all') {
            filtered = filtered.filter(item => item.category === currentCategory);
        }

        if (searchQuery) {
            filtered = downloadManager.searchDownloads(filtered, searchQuery);
        }

        filtered = downloadManager.sortDownloads(filtered, currentSort);

        const grid = document.getElementById('offline-media-grid');
        const empty = document.getElementById('offline-empty-state');

        if (!grid || !empty) return;

        if (filtered.length === 0) {
            grid.innerHTML = '';
            empty.classList.remove('d-none');
            updateEmptyState();
            return;
        }

        empty.classList.add('d-none');
        grid.innerHTML = filtered.map(item => renderMediaCard(item)).join('');
        attachCardActions();
        resolveCardThumbnails();
    }

    async function resolveCardThumbnails() {
        const { downloadManager } = await getServices();
        if (!downloadManager) return;

        const lazyThumbs = document.querySelectorAll('[data-thumb-lazy]');
        for (const el of lazyThumbs) {
            const id = el.dataset.thumbLazy;
            const item = allItems.find(it => it.id === id);
            if (!item) continue;

            try {
                const src = await downloadManager.getMediaSrc(item);
                if (src && el.tagName === 'IMG' && !el.src) {
                    el.src = src;
                }
            } catch (err) {
                console.warn('[Thumbnail] Failed to resolve:', id, err);
            }
        }
    }

    function renderMediaCard(item) {
        const format = window.downloadStorage ? window.downloadStorage.formatBytes : (b => `${b} B`);
        const dateStr = item.downloadedAt ? new Date(item.downloadedAt).toLocaleDateString() : '';
        const sizeStr = format(item.size || 0);

        let thumbMarkup = '';
        let playButton = '';

        if (item.category === 'video') {
            if (item.thumbnail) {
                thumbMarkup = `
                    <img src="${item.thumbnail}" class="offline-thumb-img" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                    <div class="offline-video-fallback d-none flex-column align-items-center justify-content-center w-100 h-100 text-primary" style="background: radial-gradient(circle, rgba(37,99,235,0.2) 0%, rgba(0,0,0,0.8) 100%);">
                        <i class="bi bi-camera-video fs-1"></i>
                    </div>
                `;
            } else {
                thumbMarkup = `
                    <div class="offline-video-fallback d-flex flex-column align-items-center justify-content-center w-100 h-100 text-primary" style="background: radial-gradient(circle, rgba(37,99,235,0.2) 0%, rgba(0,0,0,0.8) 100%);">
                        <i class="bi bi-camera-video fs-1"></i>
                    </div>
                `;
            }
            playButton = `<div class="offline-play-overlay"><i class="bi bi-play-fill"></i></div>`;
        } else if (item.category === 'image') {
            thumbMarkup = `
                <img data-thumb-lazy="${item.id}" src="${item.thumbnail || ''}" class="offline-thumb-img" alt="" onerror="this.style.display='none'; this.nextElementSibling.style.display='flex';">
                <div class="d-none w-100 h-100 align-items-center justify-content-center text-info fs-1 bg-dark">
                    <i class="bi bi-image"></i>
                </div>
            `;
        } else if (item.category === 'audio') {
            thumbMarkup = `
                <div class="w-100 h-100 d-flex flex-column align-items-center justify-content-center text-center p-2" style="background: linear-gradient(135deg, #1e1b4b 0%, #312e81 60%, #4338ca 100%);">
                    <div class="rounded-circle bg-warning bg-opacity-25 p-3 mb-1">
                        <i class="bi bi-music-note-beamed fs-2 text-warning"></i>
                    </div>
                    <span class="text-white-50 fw-semibold text-uppercase tracking-wider" style="font-size: 10px;">Offline Audio</span>
                </div>
            `;
            playButton = `<div class="offline-play-overlay bg-warning"><i class="bi bi-play-fill"></i></div>`;
        } else {
            // Document
            const ext = (item.filename?.split('.').pop() || 'doc').toLowerCase();
            let docColor = '#dc2626';
            let docBg = 'linear-gradient(135deg, #450a0a 0%, #7f1d1d 60%, #991b1b 100%)';
            let docIcon = 'bi-file-earmark-pdf-fill';

            if (ext === 'doc' || ext === 'docx') {
                docColor = '#2563eb';
                docBg = 'linear-gradient(135deg, #0f172a 0%, #1e3a8a 60%, #1d4ed8 100%)';
                docIcon = 'bi-file-earmark-word-fill';
            } else if (ext === 'xls' || ext === 'xlsx' || ext === 'csv') {
                docColor = '#16a34a';
                docBg = 'linear-gradient(135deg, #022c22 0%, #064e3b 60%, #047857 100%)';
                docIcon = 'bi-file-earmark-excel-fill';
            } else if (ext === 'ppt' || ext === 'pptx') {
                docColor = '#ea580c';
                docBg = 'linear-gradient(135deg, #431407 0%, #7c2d12 60%, #c2410c 100%)';
                docIcon = 'bi-file-earmark-ppt-fill';
            }

            if (item.thumbnail) {
                thumbMarkup = `
                    <img src="${item.thumbnail}" class="offline-thumb-img" style="object-position: top;" alt="" onerror="this.style.display='none'; if (this.nextElementSibling) this.nextElementSibling.style.display='none'; this.parentElement.querySelector('.doc-fallback-cover').style.display='flex';">
                    <span class="badge position-absolute top-0 end-0 m-2 bg-dark bg-opacity-75 text-white" style="font-size: 10px; font-weight: 600; z-index: 2;">${ext.toUpperCase()}</span>
                    <div class="doc-fallback-cover w-100 h-100 d-flex flex-column align-items-center justify-content-center text-center p-2" style="background: ${docBg}; display: none;">
                        <i class="bi ${docIcon} fs-1" style="color: ${docColor};"></i>
                        <span class="badge text-white mt-1" style="background: ${docColor}; font-size: 10px;">${ext.toUpperCase()}</span>
                    </div>
                `;
            } else {
                thumbMarkup = `
                    <div class="w-100 h-100 d-flex flex-column align-items-center justify-content-center text-center p-2" style="background: ${docBg};">
                        <i class="bi ${docIcon} fs-1" style="color: ${docColor};"></i>
                        <span class="badge text-white mt-1" style="background: ${docColor}; font-size: 10px;">${ext.toUpperCase()}</span>
                    </div>
                `;
            }
        }

        return `
            <div class="col-6 col-md-4 col-lg-3" id="card-${item.id}">
                <div class="card h-100 border-0 shadow-sm offline-media-card bg-body-tertiary">
                    <div class="offline-thumb-container cursor-pointer" data-action="open-media" data-id="${item.id}">
                        <span class="badge badge-category">${item.category}</span>
                        ${thumbMarkup}
                        ${playButton}
                    </div>
                    <div class="card-body p-2 d-flex flex-column justify-content-between">
                        <div>
                            <div class="fw-semibold small text-truncate mb-1" title="${item.filename}">
                                ${item.filename}
                            </div>
                            <div class="d-flex justify-content-between align-items-center text-muted" style="font-size: 11px;">
                                <span>${sizeStr}</span>
                                <span>${dateStr}</span>
                            </div>
                        </div>
                        <div class="d-flex justify-content-between align-items-center mt-2 pt-1 border-top border-secondary-subtle">
                            ${item.category === 'document' ? `
                                <button class="btn btn-xs btn-outline-primary py-0 px-2 rounded-pill d-flex align-items-center gap-1" data-action="export-doc" data-id="${item.id}" title="Save to Phone">
                                    <i class="bi bi-phone-arrow-down" style="font-size: 11px;"></i>
                                    <span style="font-size: 11px;">Phone</span>
                                </button>
                            ` : `<span></span>`}
                            <div class="d-flex gap-1">
                                <button class="btn btn-sm btn-ghost p-1 text-muted" data-action="share-media" data-id="${item.id}" title="Share">
                                    <i class="bi bi-share"></i>
                                </button>
                                <button class="btn btn-sm btn-ghost p-1 text-danger" data-action="delete-media" data-id="${item.id}" title="Delete">
                                    <i class="bi bi-trash"></i>
                                </button>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        `;
    }

    function attachCardActions() {
        // Open/Play Media
        document.querySelectorAll('[data-action="open-media"]').forEach(el => {
            el.onclick = () => handleOpenMedia(el.dataset.id);
        });

        // Save Document to Phone
        document.querySelectorAll('[data-action="export-doc"]').forEach(btn => {
            btn.onclick = async (e) => {
                e.stopPropagation();
                const orig = btn.innerHTML;
                btn.innerHTML = '<i class="bi bi-arrow-repeat spin-icon"></i>';
                try {
                    const { downloadManager } = await getServices();
                    if (!downloadManager) throw new Error('Download manager unavailable');
                    await downloadManager.exportToDevice(btn.dataset.id);
                    btn.innerHTML = '<i class="bi bi-check2 text-success"></i> Saved';
                    setTimeout(() => { btn.innerHTML = orig; }, 2000);
                } catch (err) {
                    console.error('[Export Doc] Failed:', err);
                    btn.innerHTML = orig;
                    alert('Could not export to phone: ' + (err.message || err));
                }
            };
        });

        // Share Media
        document.querySelectorAll('[data-action="share-media"]').forEach(el => {
            el.onclick = async (e) => {
                e.stopPropagation();
                const { downloadManager } = await getServices();
                if (downloadManager) downloadManager.share(el.dataset.id);
            };
        });

        // Delete Media
        document.querySelectorAll('[data-action="delete-media"]').forEach(el => {
            el.onclick = async (e) => {
                e.stopPropagation();
                if (confirm('Delete this download from your device?')) {
                    const { downloadManager } = await getServices();
                    if (downloadManager) {
                        await downloadManager.delete(el.dataset.id);
                        await loadItems();
                    }
                }
            };
        });
    }

    async function handleOpenMedia(id) {
        const item = allItems.find(it => it.id === id);
        if (!item) return;

        // For Videos -> Open Fullscreen TikTok/Reels Player
        if (item.category === 'video') {
            const videoItems = allItems.filter(it => it.category === 'video');
            const index = videoItems.findIndex(it => it.id === id);
            await openReelsPlayer(index >= 0 ? index : 0);
            return;
        }

        const { downloadManager } = await getServices();
        if (!downloadManager) return;

        const src = await downloadManager.getMediaSrc(item);
        if (!src) {
            alert('Media file could not be loaded offline.');
            return;
        }

        if (item.category === 'image') {
            const img = document.getElementById('offlinePlayerImage');
            const title = document.getElementById('offlineImageTitle');
            if (img && title) {
                title.textContent = item.filename;
                img.src = src;
                const modal = new bootstrap.Modal(document.getElementById('offlineImageModal'));
                modal.show();
            }
        } else if (item.category === 'audio') {
            const audio = document.getElementById('offlinePlayerAudio');
            const title = document.getElementById('offlineAudioTitle');
            if (audio && title) {
                title.textContent = item.filename;
                audio.src = src;
                const modal = new bootstrap.Modal(document.getElementById('offlineAudioModal'));
                modal.show();
                audio.play().catch(() => {});
            }
        } else {
            // Document Viewer Modal with Save to Phone action
            const docFrame = document.getElementById('offlineDocFrame');
            const docTitle = document.getElementById('offlineDocTitle');
            const exportBtn = document.getElementById('offlineDocExportBtn');

            if (docFrame && docTitle) {
                docTitle.textContent = item.filename;
                docFrame.src = src;

                if (exportBtn) {
                    exportBtn.onclick = async () => {
                        const orig = exportBtn.innerHTML;
                        exportBtn.innerHTML = '<i class="bi bi-arrow-repeat spin-icon"></i> Saving…';
                        try {
                            await downloadManager.exportToDevice(item.id);
                            exportBtn.innerHTML = '<i class="bi bi-check2"></i> Saved to Phone!';
                            setTimeout(() => { exportBtn.innerHTML = orig; }, 2000);
                        } catch (err) {
                            console.error('Export failed:', err);
                            exportBtn.innerHTML = orig;
                            alert('Failed to save to phone: ' + (err.message || err));
                        }
                    };
                }

                const modal = new bootstrap.Modal(document.getElementById('offlineDocModal'));
                modal.show();
            }
        }
    }

    /* ========================================================================= */
    /* Offline Reels Player Engine (TikTok / Instagram / Facebook style) */
    /* ========================================================================= */
    async function openReelsPlayer(startIndex = 0) {
        const videoItems = allItems.filter(item => item.category === 'video');
        if (videoItems.length === 0) {
            alert('No downloaded videos found.');
            return;
        }

        const container = document.getElementById('offlineReelsContainer');
        const slider = document.getElementById('offlineReelsSlider');
        if (!container || !slider) return;

        slider.innerHTML = '';
        container.style.display = 'flex';
        document.body.style.overflow = 'hidden';
        document.body.classList.add('in-reels-mode');

        // Hide native system navigation / status bars if on Capacitor/Android
        if (window.Capacitor && window.Capacitor.Plugins) {
            if (window.Capacitor.Plugins.StatusBar && typeof window.Capacitor.Plugins.StatusBar.hide === 'function') {
                window.Capacitor.Plugins.StatusBar.hide().catch(() => {});
            }
            if (window.Capacitor.Plugins.NavigationBar && typeof window.Capacitor.Plugins.NavigationBar.hide === 'function') {
                window.Capacitor.Plugins.NavigationBar.hide().catch(() => {});
            }
        }
        if (window.AndroidBridge && typeof window.AndroidBridge.setImmersiveMode === 'function') {
            try { window.AndroidBridge.setImmersiveMode(true); } catch (_) {}
        }

        const { downloadManager } = await getServices();

        // Preload sources and render slides
        for (let i = 0; i < videoItems.length; i++) {
            const item = videoItems[i];
            let src = reelSources.get(item.id);
            if (!src && downloadManager) {
                src = await downloadManager.getMediaSrc(item);
                reelSources.set(item.id, src);
            }

            const slide = createReelSlide(item, src, i, videoItems.length);
            slider.appendChild(slide);
        }

        // Setup scroll observer
        setupReelsObserver(videoItems);

        // Scroll to startIndex
        const targetSlide = slider.children[startIndex];
        if (targetSlide) {
            targetSlide.scrollIntoView({ behavior: 'auto' });
        }
    }

    function createReelSlide(item, src, index, total) {
        const format = window.downloadStorage ? window.downloadStorage.formatBytes : (b => `${b} B`);
        const sizeStr = format(item.size || 0);
        const dateStr = item.downloadedAt ? new Date(item.downloadedAt).toLocaleDateString() : '';

        const slide = document.createElement('div');
        slide.className = 'offline-reel-slide';
        slide.dataset.id = item.id;
        slide.dataset.index = index;

        slide.innerHTML = `
            ${item.thumbnail ? `<img class="offline-reel-poster-overlay" src="${item.thumbnail}" alt="" />` : ''}
            <video class="offline-reel-video" src="${src}" poster="${item.thumbnail || ''}" loop playsinline webkit-playsinline disablePictureInPicture controlsList="nodownload nofullscreen noremoteplayback" preload="auto" ${isReelsMuted ? 'muted' : ''}></video>
            
            <div class="offline-reel-tap-indicator">
                <i class="bi bi-play-fill"></i>
            </div>

            <!-- Continuous borderless action panel -->
            <div class="offline-reels-right-rail">
                <button class="reels-rail-btn reel-sound-btn" title="Toggle Sound">
                    <i class="bi ${isReelsMuted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}"></i>
                    <span class="rail-label">${isReelsMuted ? 'Muted' : 'Sound'}</span>
                </button>

                <button class="reels-rail-btn reel-export-btn" title="Save to Phone">
                    <i class="bi bi-phone-arrow-down"></i>
                    <span class="rail-label">Save</span>
                </button>

                <button class="reels-rail-btn reel-share-btn" title="Share">
                    <i class="bi bi-share-fill"></i>
                    <span class="rail-label">Share</span>
                </button>

                <button class="reels-rail-btn reel-delete-btn" title="Delete">
                    <i class="bi bi-trash3"></i>
                    <span class="rail-label">Delete</span>
                </button>
            </div>

            <!-- Bottom Caption & Progress -->
            <div class="offline-reels-bottom-info">
                <div class="fw-bold text-white text-truncate mb-1" style="font-size: 15px;">${item.filename}</div>
                <div class="text-white-50 small d-flex align-items-center gap-2">
                    <span><i class="bi bi-hdd me-1"></i>${sizeStr}</span>
                    <span>•</span>
                    <span><i class="bi bi-calendar-check me-1"></i>${dateStr}</span>
                </div>
                <div class="reels-progress-bar-container">
                    <div class="reels-progress-bar-track">
                        <div class="reels-progress-bar-fill"></div>
                    </div>
                </div>
            </div>
        `;

        const video = slide.querySelector('.offline-reel-video');
        const posterOverlay = slide.querySelector('.offline-reel-poster-overlay');
        const tapIndicator = slide.querySelector('.offline-reel-tap-indicator');
        const progressFill = slide.querySelector('.reels-progress-bar-fill');
        const progressContainer = slide.querySelector('.reels-progress-bar-container');

        const hidePoster = () => {
            if (posterOverlay) {
                posterOverlay.classList.add('hidden');
            }
        };
        video.addEventListener('playing', hidePoster);
        video.addEventListener('timeupdate', () => {
            if (video.currentTime > 0) hidePoster();
        });

        // Tap to play/pause
        video.onclick = () => {
            if (video.paused) {
                video.play();
                tapIndicator.querySelector('i').className = 'bi bi-play-fill';
            } else {
                video.pause();
                tapIndicator.querySelector('i').className = 'bi bi-pause-fill';
            }
            tapIndicator.classList.add('active');
            setTimeout(() => tapIndicator.classList.remove('active'), 400);
        };

        // Progress bar updates
        let isScrubbing = false;
        let wasPausedBeforeScrub = false;

        video.ontimeupdate = () => {
            if (video.duration && !isScrubbing) {
                const pct = (video.currentTime / video.duration) * 100;
                progressFill.style.width = `${pct}%`;
            }
        };

        // Interactive scrubbing & dragging support
        const updateScrubPosition = (clientX) => {
            const rect = progressContainer.getBoundingClientRect();
            if (rect.width <= 0) return;
            let pos = (clientX - rect.left) / rect.width;
            pos = Math.max(0, Math.min(1, pos));
            progressFill.style.width = `${pos * 100}%`;
            if (video.duration) {
                video.currentTime = pos * video.duration;
            }
        };

        progressContainer.addEventListener('pointerdown', (e) => {
            e.stopPropagation();
            e.preventDefault();
            isScrubbing = true;
            wasPausedBeforeScrub = video.paused;
            video.pause();
            progressContainer.classList.add('is-scrubbing');
            try {
                progressContainer.setPointerCapture(e.pointerId);
            } catch (_) {}
            updateScrubPosition(e.clientX);
        });

        progressContainer.addEventListener('pointermove', (e) => {
            if (!isScrubbing) return;
            e.stopPropagation();
            e.preventDefault();
            updateScrubPosition(e.clientX);
        });

        const stopScrubbing = (e) => {
            if (!isScrubbing) return;
            e.stopPropagation();
            isScrubbing = false;
            progressContainer.classList.remove('is-scrubbing');
            try {
                progressContainer.releasePointerCapture(e.pointerId);
            } catch (_) {}
            if (!wasPausedBeforeScrub) {
                video.play().catch(() => {});
            }
        };

        progressContainer.addEventListener('pointerup', stopScrubbing);
        progressContainer.addEventListener('pointercancel', stopScrubbing);

        // Right Rail Actions
        video.muted = isReelsMuted;
        video.addEventListener('volumechange', () => {
            isReelsMuted = video.muted;
            document.querySelectorAll('.reel-sound-btn').forEach(btn => {
                const icon = btn.querySelector('i');
                if (icon) icon.className = `bi ${isReelsMuted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}`;
                const lbl = btn.querySelector('.rail-label');
                if (lbl) lbl.textContent = isReelsMuted ? 'Muted' : 'Sound';
            });
        });

        const soundBtn = slide.querySelector('.reel-sound-btn');
        soundBtn.onclick = (e) => {
            e.stopPropagation();
            toggleReelsMute();
        };

        const exportBtn = slide.querySelector('.reel-export-btn');
        exportBtn.onclick = async (e) => {
            e.stopPropagation();
            const orig = exportBtn.innerHTML;
            exportBtn.innerHTML = '<i class="bi bi-arrow-repeat spin-icon"></i>';
            try {
                const { downloadManager } = await getServices();
                if (!downloadManager) throw new Error('Download manager unavailable');
                await downloadManager.exportToDevice(item.id);
                exportBtn.innerHTML = '<i class="bi bi-check2 text-success"></i><span class="rail-label">Saved</span>';
                setTimeout(() => { exportBtn.innerHTML = orig; }, 2000);
            } catch (err) {
                console.error('[Reel Export] Failed:', err);
                exportBtn.innerHTML = orig;
                alert('Could not save to phone: ' + (err.message || err));
            }
        };

        const shareBtn = slide.querySelector('.reel-share-btn');
        shareBtn.onclick = async (e) => {
            e.stopPropagation();
            const { downloadManager } = await getServices();
            if (downloadManager) downloadManager.share(item.id);
        };

        const deleteBtn = slide.querySelector('.reel-delete-btn');
        deleteBtn.onclick = async (e) => {
            e.stopPropagation();
            if (confirm(`Delete "${item.filename}"?`)) {
                const { downloadManager } = await getServices();
                if (downloadManager) {
                    await downloadManager.delete(item.id);
                    slide.remove();
                    await loadItems();
                    const remaining = document.querySelectorAll('.offline-reel-slide');
                    if (remaining.length === 0) {
                        closeReelsPlayer();
                    } else {
                        const counter = document.getElementById('reels-counter');
                        if (counter) counter.textContent = `1 / ${remaining.length}`;
                    }
                }
            }
        };

        return slide;
    }

    function setupReelsObserver(videoItems) {
        if (reelsObserver) reelsObserver.disconnect();

        const slider = document.getElementById('offlineReelsSlider');
        const counter = document.getElementById('reels-counter');

        reelsObserver = new IntersectionObserver((entries) => {
            entries.forEach(entry => {
                const video = entry.target.querySelector('video');
                const index = parseInt(entry.target.dataset.index, 10);
                if (!video) return;

                if (entry.isIntersecting && entry.intersectionRatio >= 0.6) {
                    activeReelVideo = video;
                    video.muted = isReelsMuted;
                    video.play().catch(() => {});
                    if (counter) {
                        counter.textContent = `${index + 1} / ${videoItems.length}`;
                    }
                } else {
                    video.pause();
                    video.currentTime = 0;
                    const posterOverlay = entry.target.querySelector('.offline-reel-poster-overlay');
                    if (posterOverlay) {
                        posterOverlay.classList.remove('hidden');
                    }
                }
            });
        }, {
            root: slider,
            threshold: 0.6
        });

        document.querySelectorAll('.offline-reel-slide').forEach(slide => {
            reelsObserver.observe(slide);
        });
    }

    function toggleReelsMute() {
        isReelsMuted = !isReelsMuted;
        document.querySelectorAll('.offline-reel-video').forEach(vid => {
            vid.muted = isReelsMuted;
        });
        document.querySelectorAll('.reel-sound-btn').forEach(btn => {
            btn.querySelector('i').className = `bi ${isReelsMuted ? 'bi-volume-mute-fill' : 'bi-volume-up-fill'}`;
            btn.querySelector('.rail-label').textContent = isReelsMuted ? 'Muted' : 'Sound';
        });
    }

    function closeReelsPlayer() {
        const container = document.getElementById('offlineReelsContainer');
        if (container) container.style.display = 'none';
        document.body.style.removeProperty('overflow');
        document.body.classList.remove('in-reels-mode');

        // Restore native system navigation / status bars
        if (window.Capacitor && window.Capacitor.Plugins) {
            if (window.Capacitor.Plugins.StatusBar && typeof window.Capacitor.Plugins.StatusBar.show === 'function') {
                window.Capacitor.Plugins.StatusBar.show().catch(() => {});
            }
            if (window.Capacitor.Plugins.NavigationBar && typeof window.Capacitor.Plugins.NavigationBar.show === 'function') {
                window.Capacitor.Plugins.NavigationBar.show().catch(() => {});
            }
        }
        if (window.AndroidBridge && typeof window.AndroidBridge.setImmersiveMode === 'function') {
            try { window.AndroidBridge.setImmersiveMode(false); } catch (_) {}
        }

        if (reelsObserver) {
            try { reelsObserver.disconnect(); } catch (_) {}
            reelsObserver = null;
        }

        document.querySelectorAll('.offline-reel-video').forEach(video => {
            try {
                video.pause();
                video.src = '';
            } catch (_) {}
        });

        activeReelVideo = null;
    }

    function updateEmptyState() {
        const title = document.getElementById('empty-title');
        const desc = document.getElementById('empty-desc');
        const icon = document.getElementById('empty-icon');
        if (!title || !desc || !icon) return;

        if (currentCategory === 'video') {
            icon.className = 'bi bi-camera-video';
            title.textContent = 'No downloaded videos';
            desc.textContent = 'Videos you download from posts and reels will appear here for offline streaming.';
        } else if (currentCategory === 'image') {
            icon.className = 'bi bi-image';
            title.textContent = 'No downloaded photos';
            desc.textContent = 'Images you save will be available here without an internet connection.';
        } else if (currentCategory === 'audio') {
            icon.className = 'bi bi-music-note-beamed';
            title.textContent = 'No downloaded audio';
            desc.textContent = 'Voice messages, songs, and audio tracks will be ready here offline.';
        } else if (currentCategory === 'document') {
            icon.className = 'bi bi-file-earmark-text';
            title.textContent = 'No downloaded documents';
            desc.textContent = 'PDFs, docs, and notes you download can be read offline anytime.';
        } else {
            icon.className = 'bi bi-cloud-arrow-down';
            title.textContent = 'No downloaded media yet';
            desc.textContent = 'Files you download while browsing vasto will automatically be saved here for full offline streaming and viewing.';
        }
    }

    // Expose to window for HTMX lifecycle hooks and manual calls
    window.initOfflineMediaHub = initOfflineMediaHub;
    window.cleanupOfflineMediaHub = cleanupOfflineMediaHub;
    window.openOfflineReelsPlayer = openReelsPlayer;
    window.closeOfflineReelsPlayer = closeReelsPlayer;

    // Auto-init if DOM is already loaded with the offline container
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => {
            if (document.querySelector('.offline-media-container')) {
                initOfflineMediaHub();
            }
        }, { once: true });
    } else {
        if (document.querySelector('.offline-media-container')) {
            initOfflineMediaHub();
        }
    }
})(window);
