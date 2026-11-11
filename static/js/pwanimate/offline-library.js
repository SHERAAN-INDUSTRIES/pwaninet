/* Spark Offline Library — saves authorized repository documents (file bytes +
 * extracted chunks) into IndexedDB so students can read them without network.
 * Spark itself stays online; this store powers the offline reader and feeds
 * the "saved for revision" context later.
 *
 * Exposes window.PwaniOfflineLibrary and auto-mounts a nav button + modal on
 * the Spark chat page when #navbar-top is present.
 */
(() => {
    'use strict';

    const DB_NAME = 'pwani-offline';
    const DB_VERSION = 2;
    const STORE = 'documents';
    const API = '/api/pwanimate/offline';
    const MAX_FILE_BYTES = 150 * 1024 * 1024;

    let dbPromise = null;
    const blobUrlCache = new Map(); // share_id -> object URL
    let persistRequested = false;

    function currentOwner() {
        return (document.body && document.body.dataset && document.body.dataset.userId) || '';
    }

    function openDb() {
        if (dbPromise) return dbPromise;
        dbPromise = new Promise((resolve, reject) => {
            if (!window.indexedDB) {
                reject(new Error('IndexedDB is not available in this browser.'));
                return;
            }
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains(STORE)) {
                    const store = db.createObjectStore(STORE, { keyPath: 'share_id' });
                    store.createIndex('saved_at', 'saved_at');
                    store.createIndex('owner_id', 'owner_id');
                } else {
                    const store = req.transaction.objectStore(STORE);
                    if (!store.indexNames.contains('owner_id')) {
                        store.createIndex('owner_id', 'owner_id');
                    }
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error || new Error('Could not open offline storage.'));
        });
        return dbPromise;
    }

    function tx(mode, fn) {
        return openDb().then(db => new Promise((resolve, reject) => {
            const t = db.transaction(STORE, mode);
            const store = t.objectStore(STORE);
            const out = fn(store);
            t.oncomplete = () => resolve(out && out.result !== undefined ? out.result : out);
            t.onerror = () => reject(t.error);
            t.onabort = () => reject(t.error || new Error('Offline storage transaction aborted.'));
        }));
    }

    function put(record) {
        return openDb().then(db => new Promise((resolve, reject) => {
            const t = db.transaction(STORE, 'readwrite');
            t.objectStore(STORE).put(record);
            t.oncomplete = () => resolve(record);
            t.onerror = () => reject(t.error);
        }));
    }

    function get(shareId) {
        return openDb().then(db => new Promise((resolve, reject) => {
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).get(shareId);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        }));
    }

    function del(shareId) {
        return openDb().then(db => new Promise((resolve, reject) => {
            const t = db.transaction(STORE, 'readwrite');
            t.objectStore(STORE).delete(shareId);
            t.oncomplete = () => resolve(true);
            t.onerror = () => reject(t.error);
        }));
    }

    function all() {
        const owner = currentOwner();
        return openDb().then(db => new Promise((resolve, reject) => {
            const req = db.transaction(STORE, 'readonly').objectStore(STORE).getAll();
            req.onsuccess = () => resolve((req.result || [])
                // Account isolation: records stamped with a different owner are
                // never visible to this session. Legacy unstamped records remain
                // visible (they predate owner scoping) until logout cleanup.
                .filter(r => !r.owner_id || !owner || r.owner_id === owner)
                .sort((a, b) => (b.saved_at || 0) - (a.saved_at || 0)));
            req.onerror = () => reject(req.error);
        }));
    }

    function requestPersistentStorage() {
        if (persistRequested || !navigator.storage || typeof navigator.storage.persist !== 'function') return;
        persistRequested = true;
        navigator.storage.persist().catch(() => {});
    }

    /** Revoke and drop every cached object URL (logout / account switch). */
    function revokeAllBlobUrls() {
        blobUrlCache.forEach(url => {
            try { URL.revokeObjectURL(url); } catch (_) {}
        });
        blobUrlCache.clear();
    }

    /**
     * Return a reusable blob: URL for a saved document's local file bytes.
     * Creates the URL once per record and caches it; callers must not revoke.
     */
    async function getBlobUrl(shareId) {
        const cached = blobUrlCache.get(shareId);
        if (cached) return cached;
        const record = await get(shareId);
        if (!record || !record.file_blob) {
            throw new Error('No local file is stored for this document.');
        }
        const owner = currentOwner();
        if (record.owner_id && owner && record.owner_id !== owner) {
            throw new Error('This offline copy belongs to a different account.');
        }
        const url = URL.createObjectURL(record.file_blob);
        blobUrlCache.set(shareId, url);
        return url;
    }

    /**
     * Return the stored Blob for a saved document (owner-checked).
     * Preferred over getBlobUrl for viewers: no object-URL lifetime to manage.
     */
    async function getBlob(shareId) {
        const record = await get(shareId);
        if (!record || !record.file_blob) {
            throw new Error('No local file is stored for this document.');
        }
        const owner = currentOwner();
        if (record.owner_id && owner && record.owner_id !== owner) {
            throw new Error('This offline copy belongs to a different account.');
        }
        return record.file_blob;
    }

    function releaseBlobUrl(shareId) {
        const url = blobUrlCache.get(shareId);
        if (url) {
            try { URL.revokeObjectURL(url); } catch (_) {}
            blobUrlCache.delete(shareId);
        }
    }

    /** Remove every stored document and revoke object URLs (logout/switch). */
    async function clearAll() {
        revokeAllBlobUrls();
        return openDb().then(db => new Promise((resolve, reject) => {
            const t = db.transaction(STORE, 'readwrite');
            t.objectStore(STORE).clear();
            t.oncomplete = () => resolve(true);
            t.onerror = () => reject(t.error);
        }));
    }

    async function fetchJson(url, errorMessage) {
        const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'application/json' } });
        const data = await response.json().catch(() => ({}));
        if (!response.ok) throw new Error(data.error || data.detail || errorMessage);
        return data;
    }

    /** Download bundle JSON + file bytes and persist locally. */
    async function saveDocument(shareId) {
        const bundle = await fetchJson(`${API}/documents/${shareId}/`, 'Could not load the document bundle.');
        let fileBlob = null;
        if (bundle.file && bundle.file.url) {
            const fileResponse = await fetch(bundle.file.url, { credentials: 'same-origin' });
            if (fileResponse.ok) {
                fileBlob = await fileResponse.blob();
                if (fileBlob.size > MAX_FILE_BYTES) {
                    throw new Error('This document is too large to save offline.');
                }
            }
        }
        const record = {
            share_id: shareId,
            owner_id: currentOwner(),
            title: bundle.title || 'Untitled document',
            description: bundle.description || '',
            unit_code: bundle.unit_code || '',
            unit_name: bundle.unit_name || '',
            units: bundle.units || [],
            category: bundle.category || 'Document',
            file: bundle.file || null,
            file_blob: fileBlob,
            chunks: Array.isArray(bundle.chunks) ? bundle.chunks : [],
            document_pk: bundle.document_pk || null,
            document_version_id: bundle.document_version_id || null,
            doc_updated_at: bundle.updated_at || null,
            saved_at: Date.now(),
        };
        try {
            await put(record);
        } catch (err) {
            if (err && (err.name === 'QuotaExceededError' || err.name === 'NS_ERROR_DOM_QUOTA_REACHED')) {
                throw new Error('Device storage is full. Free up space or remove older saved documents, then try again.');
            }
            throw err;
        }
        requestPersistentStorage();
        // The blob URL cache must reflect the new bytes.
        releaseBlobUrl(shareId);
        // Mirror the save server-side into the student's "Spark Offline"
        // collection so Spark's ranking/context knows it's their revision
        // shelf. Local copy stands even if this call fails (e.g. offline).
        try {
            await fetch(`${API}/documents/${shareId}/save/`, {
                method: 'POST', credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-CSRFToken': getCsrfToken() },
            });
        } catch (_) { /* server mirror is best-effort */ }
        return record;
    }

    function getCsrfToken() {
        const cookie = document.cookie.split('; ').find(row => row.startsWith('csrftoken='));
        return cookie ? decodeURIComponent(cookie.split('=')[1]) : '';
    }

    async function removeDocument(shareId) {
        releaseBlobUrl(shareId);
        try {
            await fetch(`${API}/documents/${shareId}/save/`, {
                method: 'DELETE', credentials: 'same-origin',
                headers: { 'Accept': 'application/json', 'X-CSRFToken': getCsrfToken() },
            });
        } catch (_) { /* best-effort */ }
        return del(shareId);
    }

    /** Case-insensitive substring search across a saved document's chunks. */
    async function searchChunks(shareId, query) {
        const record = await get(shareId);
        if (!record) return [];
        const q = (query || '').trim().toLowerCase();
        if (!q) return record.chunks.map((c, i) => ({ index: i, chunk: c }));
        return record.chunks
            .map((c, i) => ({ index: i, chunk: c }))
            .filter(({ chunk }) => (chunk.content || '').toLowerCase().includes(q)
                || (chunk.section_heading || '').toLowerCase().includes(q));
    }

    /* ---------------- UI ---------------- */

    const state = { modal: null, view: 'library', currentShareId: null, query: '' };

    function escapeHtml(value) {
        return String(value == null ? '' : value)
            .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }

    function formatBytes(bytes) {
        const n = Number(bytes) || 0;
        if (n >= 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
        if (n >= 1024) return Math.round(n / 1024) + ' KB';
        return n + ' B';
    }

    function ensureModal() {
        if (state.modal || !document.getElementById('pwani-offline-modal')) {
            const wrap = document.createElement('div');
            wrap.innerHTML = `
            <div class="modal fade" id="pwani-offline-modal" tabindex="-1" aria-hidden="true">
              <div class="modal-dialog modal-lg modal-dialog-scrollable">
                <div class="modal-content">
                  <div class="modal-header">
                    <h5 class="modal-title"><i data-lucide="book-down" class="me-2"></i>Offline library</h5>
                    <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                  </div>
                  <div class="modal-body" id="pwani-offline-body"></div>
                </div>
              </div>
            </div>`;
            document.body.appendChild(wrap.firstElementChild);
        }
        state.modal = window.bootstrap ? window.bootstrap.Modal.getOrCreateInstance(document.getElementById('pwani-offline-modal')) : null;
        return state.modal;
    }

    async function renderLibrary() {
        state.view = 'library';
        const body = document.getElementById('pwani-offline-body');
        body.innerHTML = '<div class="text-center text-muted py-4"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>';
        let remote = [];
        try {
            const data = await fetchJson(`${API}/library/`, 'Could not load the offline library.');
            remote = Array.isArray(data.documents) ? data.documents : [];
        } catch (err) {
            body.innerHTML = `<div class="text-danger small py-3">${escapeHtml(err.message)}</div>`;
            return;
        }
        const saved = await all();
        const savedIds = new Set(saved.map(r => r.share_id));
        if (!remote.length) {
            body.innerHTML = '<div class="text-muted small py-4 text-center">No study documents are available to save yet.</div>';
            return;
        }
        body.innerHTML = remote.map(doc => {
            const isSaved = savedIds.has(doc.document_id);
            const unit = doc.unit_code ? `${escapeHtml(doc.unit_code)}${doc.unit_name ? ' — ' + escapeHtml(doc.unit_name) : ''}` : escapeHtml(doc.category);
            const size = doc.file && doc.file.size_bytes ? formatBytes(doc.file.size_bytes) : '';
            return `<div class="d-flex align-items-center gap-2 py-2 border-bottom">
                <div class="flex-grow-1 min-width-0">
                    <div class="fw-semibold text-truncate">${escapeHtml(doc.title)}</div>
                    <div class="text-muted small">${unit}${size ? ' · ' + size : ''} · ${doc.chunk_count || 0} sections</div>
                </div>
                ${isSaved
                    ? `<button type="button" class="btn btn-sm btn-outline-success" data-offline-open="${escapeHtml(doc.document_id)}"><i data-lucide="book-open"></i> Read</button>
                       <button type="button" class="btn btn-sm btn-outline-danger" data-offline-remove="${escapeHtml(doc.document_id)}"><i data-lucide="trash-2"></i></button>`
                    : `<button type="button" class="btn btn-sm btn-outline-primary" data-offline-save="${escapeHtml(doc.document_id)}"><i data-lucide="download"></i> Save</button>`}
            </div>`;
        }).join('');
    }

    async function renderReader(shareId) {
        state.view = 'reader';
        state.currentShareId = shareId;
        const body = document.getElementById('pwani-offline-body');
        const record = await get(shareId);
        if (!record) { renderLibrary(); return; }
        const chunks = await searchChunks(shareId, state.query);
        body.innerHTML = `
            <div class="d-flex align-items-center gap-2 mb-2">
                <button type="button" class="btn btn-sm btn-outline-secondary" id="pwani-offline-back"><i data-lucide="arrow-left"></i> Library</button>
                <div class="flex-grow-1">
                    <div class="fw-semibold text-truncate">${escapeHtml(record.title)}</div>
                    <div class="text-muted small">${escapeHtml(record.unit_code || record.category)} · ${record.chunks.length} sections · saved ${new Date(record.saved_at).toLocaleDateString()}</div>
                </div>
                <button type="button" class="btn btn-sm btn-outline-danger" id="pwani-offline-delete"><i data-lucide="trash-2"></i></button>
            </div>
            <input type="search" id="pwani-offline-search" class="form-control form-control-sm mb-2" placeholder="Search inside this document…" value="${escapeHtml(state.query)}">
            <div id="pwani-offline-chunks">
                ${chunks.map(({ chunk }) => `
                    <div class="card mb-2">
                        <div class="card-body py-2 px-3">
                            ${chunk.section_heading ? `<div class="fw-semibold small mb-1">${escapeHtml(chunk.section_heading)}</div>` : ''}
                            <div class="small" style="white-space:pre-wrap;">${escapeHtml((chunk.content || '').slice(0, 4000))}</div>
                            ${chunk.page_number ? `<div class="text-muted" style="font-size:.7rem;">p. ${chunk.page_number}${chunk.page_end && chunk.page_end !== chunk.page_number ? '–' + chunk.page_end : ''}</div>` : ''}
                        </div>
                    </div>`).join('') || '<div class="text-muted small py-3">No sections match your search.</div>'}
            </div>`;
        document.getElementById('pwani-offline-back').onclick = () => { state.query = ''; renderLibrary(); };
        document.getElementById('pwani-offline-delete').onclick = async () => {
            await removeDocument(shareId);
            state.query = '';
            renderLibrary();
        };
        const searchInput = document.getElementById('pwani-offline-search');
        searchInput.addEventListener('input', () => {
            state.query = searchInput.value;
            renderReader(shareId);
            const el = document.getElementById('pwani-offline-search');
            if (el) { el.focus(); el.setSelectionRange(el.value.length, el.value.length); }
        });
    }

    function open() {
        const modal = ensureModal();
        if (modal) modal.show();
        renderLibrary();
    }

    function init() {
        // Capture phase: the picker card's own click handler lives on a
        // descendant container, so intercepting here prevents "add to context"
        // when the student only wants to save the document offline.
        document.addEventListener('click', async (event) => {
            let quickSave = event.target.closest('[data-offline-save-doc]');
            if (quickSave) {
                event.preventDefault();
                event.stopPropagation();
                const shareId = quickSave.dataset.offlineSaveDoc;
                if (!shareId) return;
                const swapIcon = (name) => {
                    const next = window.sparkSetIcon ? window.sparkSetIcon(quickSave, name) : null;
                    if (next) quickSave = next;
                };
                swapIcon('hourglass');
                try {
                    await saveDocument(shareId);
                    swapIcon('check');
                    setTimeout(() => swapIcon('download'), 1600);
                } catch (err) {
                    swapIcon('download');
                    alert(err.message || 'Could not save this document offline.');
                }
                return;
            }
            const saveBtn = event.target.closest('[data-offline-save]');
            const removeBtn = event.target.closest('[data-offline-remove]');
            const openBtn = event.target.closest('[data-offline-open]');
            if (saveBtn) {
                saveBtn.disabled = true;
                saveBtn.innerHTML = '<span class="spinner-border spinner-border-sm"></span>';
                try {
                    await saveDocument(saveBtn.dataset.offlineSave);
                } catch (err) {
                    saveBtn.disabled = false;
                    saveBtn.innerHTML = '<i data-lucide="download"></i> Save';
                    alert(err.message || 'Could not save this document offline.');
                    return;
                }
                renderLibrary();
            } else if (removeBtn) {
                await removeDocument(removeBtn.dataset.offlineRemove);
                renderLibrary();
            } else if (openBtn) {
                renderReader(openBtn.dataset.offlineOpen);
            }
        }, true);
    }

    window.PwaniOfflineLibrary = {
        save: saveDocument,
        list: all,
        get,
        remove: removeDocument,
        searchChunks,
        open,
        getBlobUrl,
        getBlob,
        releaseBlobUrl,
        clearAll,
    };

    /* Account lifecycle: cached documents must not survive logout or an
       account switch on a shared device. Mirrors the signals used by
       offline-capture.js / native-offline.js. */
    function bindLifecycleCleanup() {
        document.addEventListener('submit', (event) => {
            const action = event.target && event.target.action ? String(event.target.action) : '';
            if (/\/(?:logout|switch-account|accounts\/(?:switch|remove))\//.test(action)) {
                clearAll().catch(() => {});
            }
        }, true);
        if ('serviceWorker' in navigator) {
            navigator.serviceWorker.addEventListener('message', (event) => {
                if (event.data && event.data.type === 'PRIVATE_OFFLINE_INVALIDATED') {
                    clearAll().catch(() => {});
                }
            });
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => { init(); bindLifecycleCleanup(); });
    } else {
        init();
        bindLifecycleCleanup();
    }
})();
