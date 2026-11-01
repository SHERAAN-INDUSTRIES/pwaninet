/**
 * ResumableUpload — Phase 3 persistent client engine for direct-to-R2 uploads.
 *
 * One engine for the composer and the Upload Manager page. Sessions and file
 * blobs persist in IndexedDB so uploads survive refresh and resume from the
 * first unverified chunk. Django is authoritative: verified parts always come
 * from GET /api/uploads/{id}/, never from local state alone.
 */
(function () {
    'use strict';

    const DB_NAME = 'vasto_uploads';
    const DB_VERSION = 1;
    const API_BASE = '/api/uploads/';
    const BACKOFF_MS = [5000, 30000, 120000, 600000];
    const ACTIVE_STATES = ['queued', 'preparing', 'uploading', 'verifying', 'paused', 'failed'];

    function uuid() {
        if (typeof crypto !== 'undefined' && crypto.randomUUID) return crypto.randomUUID();
        return 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, (c) => {
            const r = (Math.random() * 16) | 0;
            return (c === 'x' ? r : (r & 0x3) | 0x8).toString(16);
        });
    }

    function getCSRFToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const match = document.cookie.match(/(?:^|;\s*)(csrftoken|vasto_local_csrftoken)=([^;]*)/);
        return match ? decodeURIComponent(match[2]) : '';
    }

    function openDB() {
        return new Promise((resolve, reject) => {
            const req = indexedDB.open(DB_NAME, DB_VERSION);
            req.onupgradeneeded = () => {
                const db = req.result;
                if (!db.objectStoreNames.contains('sessions')) {
                    db.createObjectStore('sessions', { keyPath: 'client_upload_id' });
                }
                if (!db.objectStoreNames.contains('blobs')) {
                    db.createObjectStore('blobs', { keyPath: 'client_upload_id' });
                }
            };
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        });
    }

    function storePut(store, value) {
        return openDB().then((db) => new Promise((resolve, reject) => {
            const tx = db.transaction(store, 'readwrite');
            tx.objectStore(store).put(value);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        }));
    }

    function storeGet(store, key) {
        return openDB().then((db) => new Promise((resolve, reject) => {
            const req = db.transaction(store, 'readonly').objectStore(store).get(key);
            req.onsuccess = () => resolve(req.result || null);
            req.onerror = () => reject(req.error);
        }));
    }

    function storeGetAll(store) {
        return openDB().then((db) => new Promise((resolve, reject) => {
            const req = db.transaction(store, 'readonly').objectStore(store).getAll();
            req.onsuccess = () => resolve(req.result || []);
            req.onerror = () => reject(req.error);
        }));
    }

    function storeDelete(store, key) {
        return openDB().then((db) => new Promise((resolve, reject) => {
            const tx = db.transaction(store, 'readwrite');
            tx.objectStore(store).delete(key);
            tx.oncomplete = () => resolve();
            tx.onerror = () => reject(tx.error);
        }));
    }

    // Exposed for upload_ui.js restoreBannerOnLoad cleanup
    window.storeDelete = storeDelete;

    async function api(path, method, body) {
        const headers = { 'X-CSRFToken': getCSRFToken(), 'X-Requested-With': 'XMLHttpRequest' };
        const opts = { method, headers, credentials: 'same-origin' };
        if (body !== undefined) {
            headers['Content-Type'] = 'application/json';
            opts.body = JSON.stringify(body);
        }
        const res = await fetch(API_BASE + path, opts);
        let data = null;
        try {
            data = await res.json();
        } catch (_) {
            data = null;
        }
        if (!res.ok) {
            const err = new Error((data && data.detail) || JSON.stringify(data) || `Upload API ${res.status}`);
            err.status = res.status;
            err.data = data;
            err.code = (data && data.error_code) || null;
            // 410 Gone = record soft-deleted on server; not a transient failure
            if (res.status === 410 || err.code === 'UPLOAD_DELETED') {
                err.deleted = true;
            }
            console.error('[ResumableUpload] API error:', { status: res.status, path, body, data });
            throw err;
        }
        return data;
    }

    function emitProgress(clientUploadId, loaded, total, serverId) {
        console.log('[ResumableUpload] emitProgress:', { clientUploadId, loaded, total, serverId });
        window.dispatchEvent(new CustomEvent('resumable-upload-progress', {
            detail: { clientUploadId, loaded, total, serverId },
        }));
    }

    function putPart(url, blob, mimeType, onProgress) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('PUT', url, true);
            if (mimeType) xhr.setRequestHeader('Content-Type', mimeType);
            if (onProgress) {
                xhr.upload.addEventListener('progress', (e) => {
                    if (e.lengthComputable) onProgress(e.loaded, e.total);
                });
            }
            xhr.onload = () => {
                if (xhr.status >= 200 && xhr.status < 300) {
                    resolve(xhr.getResponseHeader('ETag') || '');
                } else {
                    const err = new Error(`Part PUT failed: ${xhr.status}`);
                    err.status = xhr.status;
                    reject(err);
                }
            };
            xhr.onerror = () => {
                // Almost always the bucket's CORS allow-list missing this
                // origin (e.g. browsing via 0.0.0.0 instead of localhost).
                const origin = (typeof location !== 'undefined' && location.origin) || 'this origin';
                console.error(
                    `[ResumableUpload] storage PUT was blocked. If the browser console shows a ` +
                    `CORS error, add "${origin}" to the bucket's CORS allowed origins.`,
                    { origin, url }
                );
                const err = new Error('The upload to storage was blocked by the browser');
                err.corsSuspect = true;
                err.origin = origin;
                reject(err);
            };
            xhr.onabort = () => reject(new Error('Part upload cancelled'));
            xhr.send(blob);
            putPart._active = putPart._active || new Map();
        });
    }

    class ResumableUpload {
        constructor() {
            this.transfers = new Map();
        }

        async start(file, options) {
            const opts = options || {};
            const clientUploadId = uuid();
            await storePut('blobs', { client_upload_id: clientUploadId, blob: file });
            const kind = ['document', 'timetable'].indexOf(opts.kind) !== -1 ? opts.kind : 'post';
            const createBody = {
                client_upload_id: clientUploadId,
                filename: file.name || 'upload',
                mime_type: file.type || '',
                file_size: file.size,
                kind: kind,
            };
            if (opts.postGroup) createBody.post_group = opts.postGroup;
            if (kind === 'document') createBody.document = opts.document || {};
            if (kind === 'timetable') createBody.timetable = opts.timetable || {};
            const record = await api('', 'POST', createBody);
            let payload;
            if (kind === 'document') {
                payload = { document: opts.document || {} };
            } else if (kind === 'timetable') {
                payload = { timetable: opts.timetable || {} };
            } else {
                payload = {
                    post: opts.post || {
                        content: opts.content || '',
                        visibility: opts.visibility || 'AUTHENTICATED',
                    },
                };
            }
            await storePut('sessions', {
                client_upload_id: clientUploadId,
                server_id: record.id,
                filename: record.filename,
                kind: kind,
                payload: payload,
            });
            return this.resume(clientUploadId);
        }

        async resume(clientUploadId) {
            const saved = await storeGet('sessions', clientUploadId);
            const stored = await storeGet('blobs', clientUploadId);
            if (!saved) throw new Error('Unknown upload session');
            if (!stored || !stored.blob) {
                const err = new Error('Original file unavailable — please re-select it');
                err.code = 'FILE_MISSING';
                err.clientUploadId = clientUploadId;
                throw err;
            }
            // Sessions stored before the payload refactor kept `post`.
            const payload = saved.payload || (saved.post ? { post: saved.post } : {});
            return this._run(clientUploadId, saved.server_id, stored.blob, payload);
        }

        /** Re-attach a re-selected file to an existing session (no re-upload of verified bytes). */
        async reattach(clientUploadId, file) {
            await storePut('blobs', { client_upload_id: clientUploadId, blob: file });
            return this.resume(clientUploadId);
        }

        /** Get all active sessions for banner restoration */
        async getAllSessions() {
            const sessions = await storeGetAll('sessions');
            return sessions;
        }

        async _run(clientUploadId, serverId, blob, payload) {
            let status;
            try {
                status = await api(`${serverId}/`, 'GET');
            } catch (err) {
                if (err.deleted) {
                    // Server tombstoned this record: clear local state and stop.
                    await storeDelete('sessions', clientUploadId);
                    await storeDelete('blobs', clientUploadId);
                    const err2 = new Error('This upload was deleted. Start a new upload.');
                    err2.code = 'UPLOAD_DELETED';
                    err2.clientUploadId = clientUploadId;
                    throw err2;
                }
                throw err;
            }

            // Server is authoritative: if the record is already terminal on the
            // server, don't re-authorize or re-complete — just clear local
            // state and surface the outcome so the donut can dismiss.
            if (status.status === 'cancelled' || status.status === 'failed') {
                await storeDelete('sessions', clientUploadId);
                await storeDelete('blobs', clientUploadId);
                const err = new Error(`Upload ${status.status} on server`);
                err.code = status.status.toUpperCase();
                err.deleted = status.status === 'cancelled' ? 'UPLOAD_CANCELLED' : 'UPLOAD_FAILED';
                err.clientUploadId = clientUploadId;
                throw err;
            }
            if (status.status === 'completed') {
                await storeDelete('blobs', clientUploadId);
                await storePut('sessions', {
                    client_upload_id: clientUploadId,
                    server_id: serverId,
                    filename: status.filename,
                    kind: status.kind,
                    payload: payload,
                    done: true,
                });
                return status;
            }

            const partSize = status.session_part_size || 8388608;
            const auth = await api(`${serverId}/authorize/`, 'POST', {});
            const total = status.total_bytes || blob.size;
            const etags = {};
            let done = 0;

            if (auth.protocol === 'single-put') {
                const target = auth.parts[0];
                const etag = await this._putWithRetry(target.url, blob, blob.type, (l) => {
                    emitProgress(clientUploadId, l, total, serverId);
                });
                etags[1] = etag;
                done = total;
            } else {
                const verified = new Set(status.verified_parts || []);
                for (const target of auth.parts) {
                    if (verified.has(target.part_number)) {
                        done += Math.min(partSize, total - done);
                        emitProgress(clientUploadId, done, total, serverId);
                        continue;
                    }
                    const start = (target.part_number - 1) * partSize;
                    const chunk = blob.slice(start, Math.min(start + partSize, total));
                    const base = done;
                    const etag = await this._putWithRetry(target.url, chunk, blob.type, (l) => {
                        emitProgress(clientUploadId, base + l, total, serverId);
                    });
                    etags[target.part_number] = etag;
                    done = base + chunk.size;
                    emitProgress(clientUploadId, done, total, serverId);
                }
            }

            const parts = Object.keys(etags).map((n) => ({
                part_number: Number(n),
                etag: String(etags[n]).replace(/"/g, ''),
            }));
            const finalized = await api(`${serverId}/complete/`, 'POST', Object.assign({ parts }, payload || {}));
            await storeDelete('blobs', clientUploadId);
            await storePut('sessions', {
                client_upload_id: clientUploadId,
                server_id: serverId,
                filename: status.filename,
                kind: status.kind,
                payload: payload,
                done: true,
            });
            return finalized;
        }

        async _putWithRetry(url, blob, mimeType, onProgress) {
            let attempt = 0;
            for (;;) {
                // Offline: park until connectivity returns instead of burning retries.
                if (typeof navigator !== 'undefined' && navigator.onLine === false) {
                    await new Promise((r) => {
                        const onUp = () => {
                            window.removeEventListener('online', onUp);
                            r();
                        };
                        window.addEventListener('online', onUp);
                    });
                }
                try {
                    return await putPart(url, blob, mimeType, onProgress);
                } catch (err) {
                    if (err.status && err.status >= 400 && err.status < 500 && err.status !== 403) {
                        throw err;
                    }
                    if (attempt >= BACKOFF_MS.length) throw err;
                    await new Promise((r) => setTimeout(r, BACKOFF_MS[attempt]));
                    attempt += 1;
                }
            }
        }

        /** Resume every locally persisted active upload (call on page load). */
        async resumeAll() {
            const sessions = await storeGetAll('sessions');
            const results = [];
            for (const s of sessions) {
                if (s.done) continue;
                try {
                    const status = await api(`${s.server_id}/`, 'GET');
                    if (ACTIVE_STATES.indexOf(status.status) === -1) continue;
                    results.push(await this.resume(s.client_upload_id));
                } catch (err) {
                    if (err.deleted) {
                        // Server tombstoned this record: prune from local IndexedDB.
                        await storeDelete('sessions', s.client_upload_id);
                        await storeDelete('blobs', s.client_upload_id);
                        results.push({ deleted: true, session: s });
                        continue;
                    }
                    results.push({ error: String((err && err.message) || err), session: s });
                }
            }
            return results;
        }
    }

    window.ResumableUpload = ResumableUpload;
    window.resumableUpload = new ResumableUpload();
})();
