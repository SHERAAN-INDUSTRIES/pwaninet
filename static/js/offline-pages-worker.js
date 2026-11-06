/* Same-origin, account-scoped rendered copies. No raw authenticated HTML/API cache. */
const OfflinePages = (() => {
    const META = 'vasto-private-state-v1';
    const PREFIX = 'vasto-private-pages-v1-';
    const STATE_URL = new URL('/__offline_state__', self.location.origin).href;
    const MAX_PAGES = 120;
    const MAX_AGE = 30 * 86400000;
    // Mirrors OFFLINE_DENIED_PREFIX / OFFLINE_DENIED_ACTION in pwaninet/offline.py.
    const DENIED_PREFIX = /^\/(?:admin|api|accounts|o|\.well-known|static|media|download|apk|clear-cache|test|system|dashboard|login|logout|register|offline(?:\.html)?|i\/[^/]+\/?$|app\/[^/]+\/?$|invite\/[^/]+\/?$|share\/app-to-users)(?:\/|$)/;
    const DENIED_ACTION = /\/(?:read|delete|edit|settings|new|create|write|send|mark|archive|block|revoke|leave|join|privacy|download|comment|like|reply|repost|hide|unhide|search|follow|unfollow|report|pin|mute|invite|remove|switch|verify|reset|password|2fa|device|approve|reject|kick|ban|assign|role|auth|token|upload|media|logout|login|register)(?:\/|$)/;
    const eligible = path => !DENIED_PREFIX.test(path) && !DENIED_ACTION.test(path);
    const key = value => {
        const url = new URL(value, self.location.origin);
        url.hash = '';
        ['view_cache', 'cached', 'offline_saved'].forEach(p => url.searchParams.delete(p));
        url.searchParams.sort();
        return url.href;
    };
    // Serialize identity changes, exports, and clears; late exports cannot cross accounts.
    let queue = Promise.resolve();
    const locked = work => {
        const result = queue.then(work);
        queue = result.catch(() => {});
        return result;
    };
    async function state() {
        const response = await (await caches.open(META)).match(STATE_URL);
        return response ? response.json() : { owner: '', routes: {}, generation: 0 };
    }
    async function setState(value) {
        await (await caches.open(META)).put(STATE_URL, new Response(JSON.stringify(value)));
    }
    async function invalidateClients(owner) {
        const clients = await self.clients.matchAll({ type: 'window' });
        clients.forEach(client => client.postMessage({ type: 'PRIVATE_OFFLINE_INVALIDATED', owner }));
    }
    async function deleteMedia(owner) {
        if (!owner) return;
        for (const name of [`vasto_offline_media_${owner}`, `vasto_chat_v2_${owner}_direct`, `vasto_chat_v2_${owner}_group`]) {
            await new Promise(resolve => {
                const request = indexedDB.deleteDatabase(name);
                request.onsuccess = request.onerror = request.onblocked = resolve;
            });
        }
    }
    async function purge() {
        for (const name of await caches.keys()) {
            if (name.startsWith(PREFIX)) await caches.delete(name);
        }
    }
    async function observe(request, response, generation) {
        if (!response.headers.has('X-Vasto-Account')) return;
        return locked(async () => {
            const owner = response.headers.get('X-Vasto-Account') || '';
            let current = await state();
            // Responses started before an identity/access change cannot restore old state.
            if (generation !== current.generation) return owner === current.owner;
            let invalidated = false;
            if (owner && owner !== current.owner) {
                invalidated = Boolean(current.owner);
                await deleteMedia(current.owner);
                await purge();
                current = { owner, routes: {}, generation: current.generation + 1 };
            }
            const route = key(request.url);
            if (response.status === 401 ||
                ([403, 404].includes(response.status) && eligible(new URL(route).pathname) && current.routes[route]) ||
                (response.ok && (request.method === 'DELETE' ||
                    (request.method === 'POST' && /(?:block|revoke|leave|assign-role|delete|privacy)/.test(new URL(route).pathname))))) {
                await deleteMedia(current.owner);
                await purge();
                current.routes = {};
                current.groupChats = {};
                current.generation++;
                invalidated = true;
            }
            if (owner && eligible(new URL(route).pathname) && response.ok && !response.redirected &&
                response.headers.get('X-Vasto-Offline') === 'rendered-v1' &&
                !/no-store/i.test(response.headers.get('Cache-Control') || '')) {
                current.routes[route] = Date.now();
                const groupId = response.headers.get('X-Vasto-Group');
                if (/^\d+$/.test(groupId || '') && /^\/messaging\/conversation\/\d+\/$/.test(new URL(route).pathname)) {
                    current.groupChats = { ...current.groupChats, [groupId]: route };
                }
            }
            const approvals = Object.entries(current.routes).sort((a, b) => b[1] - a[1]).slice(0, 160);
            current.routes = Object.fromEntries(approvals);
            current.groupChats = Object.fromEntries(Object.entries(current.groupChats || {}).filter(([, target]) => current.routes[target]));
            if (eligible(new URL(route).pathname) && response.ok &&
                /text\/html/.test(response.headers.get('Content-Type') || '') &&
                (response.redirected || response.headers.get('X-Vasto-Offline') !== 'rendered-v1')) {
                delete current.routes[route];
                if (current.owner) await (await caches.open(PREFIX + current.owner)).delete(route);
            }
            await setState(current);
            if (invalidated) await invalidateClients(owner);
            return true;
        });
    }
    async function clear() {
        return locked(async () => {
            const old = await state();
            await deleteMedia(old.owner);
            await purge();
            await setState({ owner: '', routes: {}, generation: old.generation + 1 });
            await invalidateClients('');
        });
    }
    async function saved(request) {
        const current = await state();
        if (!current.owner) return null;
        const cache = await caches.open(PREFIX + current.owner);
        // A saved group profile may still have the gateway link from before
        // the first chat open. Resolve only server-confirmed account-scoped links.
        const groupMatch = new URL(request.url).pathname.match(/^\/groups\/(\d+)\/chat\/$/);
        const route = groupMatch ? current.groupChats?.[groupMatch[1]] : key(request.url);
        if (!route) return null;
        const response = await cache.match(route);
        const latest = await state();
        if (latest.owner !== current.owner || latest.generation !== current.generation) return null;
        if (!response) return null;
        if (Date.now() - Number(response.headers.get('X-Vasto-Saved-At')) > MAX_AGE) {
            await cache.delete(route);
            return null;
        }
        return response;
    }
    // Self-contained last resort mirroring templates/offline.html. Only used
    // when even the precached /offline.html is unavailable.
    const OFFLINE_HTML = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>vasto - Offline</title><style>' +
        ':root,[data-theme="light"]{--primary:#2563eb;--primary-light:#dbeafe;--text-dark:#0f172a;--text-secondary:#64748b;--background:#f8fafc;--border:#e2e8f0}' +
        '[data-theme="dark"]{--primary:#6366f1;--primary-light:#312e81;--text-dark:#e4e6eb;--text-secondary:#e4e6eb;--background:#18191f;--border:#3a3b3c}' +
        '*{margin:0;padding:0;box-sizing:border-box}body{font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;background:var(--background);color:var(--text-dark);min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;text-align:center;padding:20px}' +
        '.offline-container{max-width:400px;width:100%}.offline-icon{width:120px;height:120px;margin:0 auto 32px}.offline-icon svg{width:120px;height:120px;fill:var(--primary)}' +
        '.offline-title{font-size:28px;font-weight:700;margin-bottom:16px;letter-spacing:-.5px}.offline-message{font-size:16px;color:var(--text-secondary);margin-bottom:32px;line-height:1.6}' +
        '.action-buttons{display:flex;flex-direction:column;gap:16px;align-items:center}.action-button{background:transparent;color:var(--text-secondary);padding:12px 24px;border-radius:50px;font-size:14px;font-weight:600;cursor:pointer;border:2px solid var(--border);text-decoration:none;display:inline-flex;align-items:center;gap:8px;width:100%;max-width:280px;justify-content:center}' +
        '.action-button:hover{background:var(--primary-light);border-color:var(--primary);color:var(--primary)}' +
        '@media(max-width:480px){.offline-title{font-size:20px}.offline-icon,.offline-icon svg{width:80px;height:80px}}' +
        '</style></head><body><div class="offline-container"><div class="offline-icon"><svg viewBox="0 0 24 24" xmlns="http://www.w3.org/2000/svg"><path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm-1 17.93c-3.95-.49-7-3.85-7-7.93 0-.62.08-1.21.21-1.79L9 15v1c0 1.1.9 2 2 2v1.93zm6.9-2.54c-.26-.81-1-1.39-1.9-1.39h-1v-3c0-.55-.45-1-1-1H8v-2h2c.55 0 1-.45 1-1V7h2c1.1 0 2-.9 2-2v-.41c2.93 1.19 5 4.06 5 7.41 0 2.08-.8 3.97-2.1 5.39z"/></svg></div>' +
        '<h1 class="offline-title">YOU ARE OFFLINE</h1><p class="offline-message">WE WILL RECONNECT WHEN YOU HAVE INTERNET ACCESS</p>' +
        '<div class="action-buttons">' +
        '<a class="action-button" href="/?view_cache=1"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 19v2a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-2"/><polyline points="7 9 12 4 17 9"/><line x1="12" y1="4" x2="12" y2="17"/></svg><span>VIEW CACHED PAGES</span></a>' +
        '<a class="action-button" href="/offline-media/"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"/><polyline points="7 10 12 15 17 10"/><line x1="12" y1="15" x2="12" y2="3"/></svg><span>VIEW DOWNLOADED MEDIA</span></a>' +
        '<button class="action-button" id="offline-retry"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M23 4v6h-6"/><path d="M1 20v-6h6"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg><span>RETRY CONNECTION</span></button>' +
        '</div></div><script>(function(){var t=localStorage.getItem("theme")||"system";var d=document.documentElement;if(t==="dark"||t==="system"&&matchMedia("(prefers-color-scheme: dark)").matches)d.setAttribute("data-theme","dark");document.getElementById("offline-retry").onclick=function(){location.href="/"};var busy=false;function probe(){if(busy)return;busy=true;fetch("/api/health/",{mode:"no-cors",cache:"no-store"}).then(function(){location.href="/"}).catch(function(){busy=false})}addEventListener("online",probe);setInterval(function(){if(navigator.onLine)probe()},5000)})();</script></body></html>';
    async function offlinePage() {
        const cached = await caches.match('/offline.html');
        if (cached) return cached;
        try {
            const response = await fetch('/offline.html', { cache: 'no-store' });
            if (response.ok && /text\/html/.test(response.headers.get('Content-Type') || '')) return response;
        } catch (_) { /* Fall through to the self-contained page. */ }
        return new Response(OFFLINE_HTML, { headers: { 'Content-Type': 'text/html; charset=utf-8', 'Cache-Control': 'no-store' } });
    }
    async function navigate(request) {
        const initial = await state();
        const copy = await saved(request);
        const fallback = async () => {
            const latest = await state();
            return copy && latest.owner === initial.owner && latest.generation === initial.generation ? copy : offlinePage();
        };
        if (copy && new URL(request.url).searchParams.has('offline_saved')) return copy;
        const network = offlineFetch(request).then(async response => {
            if (response.status >= 500 && copy) return fallback();
            return response;
        }).catch(fallback);
        // A failed or stalled refresh must not blank an available view.
        if (copy) return Promise.race([network, new Promise(resolve => setTimeout(() => resolve(fallback()), 1500))]);
        return network;
    }
    async function exportPage(event) {
        const payload = event.data.payload || {};
        const client = event.source && await self.clients.get(event.source.id);
        if (!client || new URL(client.url).origin !== self.location.origin) return;
        if (!payload.url || new URL(payload.url, self.location.origin).origin !== self.location.origin) return;
        const route = key(payload.url);
        if (key(client.url) !== route || !eligible(new URL(route).pathname)) return;
        if (typeof payload.html !== 'string' || payload.html.length > 3000000) return;
        await locked(async () => {
            const current = await state();
            if (!current.owner || String(payload.owner) !== current.owner ||
                !current.routes[route] || Date.now() - current.routes[route] > 3600000) return;
            const cache = await caches.open(PREFIX + current.owner);
            await cache.put(route, new Response(payload.html, { headers: {
                'Content-Type': 'text/html; charset=utf-8',
                'X-Vasto-Saved-At': String(Date.now()),
                'X-Vasto-Snapshot-Bytes': String(payload.html.length * 2),
                'X-Vasto-Post-IDs': JSON.stringify(Array.isArray(payload.postIds) ? payload.postIds.slice(0, 200) : []),
                // Only our small offline runtime executes. Captured event handlers,
                // forms, scripts, frames and CSRF values have also been removed.
                'Content-Security-Policy': "default-src 'none'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; media-src 'self' blob:; script-src " + self.location.origin + "/static/js/offline-view.js; connect-src 'self'; base-uri 'none'; form-action 'none'; frame-src 'none'"
            } }));
            const entries = await Promise.all((await cache.keys()).map(async request => {
                const response = await cache.match(request);
                return { request, savedAt: Number(response.headers.get('X-Vasto-Saved-At')), bytes: Number(response.headers.get('X-Vasto-Snapshot-Bytes')) || 0 };
            }));
            entries.sort((a, b) => b.savedAt - a.savedAt);
            let bytes = 0;
            for (const [index, entry] of entries.entries()) {
                bytes += entry.bytes;
                if (index >= MAX_PAGES || bytes > 60 * 1024 * 1024) await cache.delete(entry.request);
            }
            client.postMessage({ type: 'PRIVATE_PAGE_SAVED', url: route, savedAt: Date.now() });
        });
    }
    async function reconcile(payload) {
        const before = await state();
        if (!before.owner || String(payload.owner) !== before.owner || Date.now() - (before.reconciledAt || 0) < 60000) return;
        await locked(async () => { const current = await state(); current.reconciledAt = Date.now(); await setState(current); });
        const cache = await caches.open(PREFIX + before.owner);
        const entries = await cache.keys();
        for (const request of entries) {
            if ((await state()).owner !== before.owner) return;
            try {
                const response = await offlineFetch(new Request(request.url, { credentials: 'include', cache: 'no-store', headers: { 'X-Vasto-Offline-Revalidate': '1' } }));
                if ((response.status < 500 && !response.ok) || response.redirected ||
                    (response.ok && response.headers.get('X-Vasto-Offline') !== 'rendered-v1')) {
                    await cache.delete(request);
                }
            } catch (_) { return; } // Outages never erase the last good copies.
        }
        // Reconcile nested posts and downloaded media as well as top-level routes.
        const snapshots = await Promise.all(entries.map(async request => {
            const copy = await cache.match(request);
            return { request, ids: copy ? JSON.parse(copy.headers.get('X-Vasto-Post-IDs') || '[]') : [] };
        }));
        const db = await new Promise(resolve => {
            const req = indexedDB.open(`vasto_offline_media_${before.owner}`, 1);
            req.onupgradeneeded = () => req.result.createObjectStore('files', { keyPath: 'id' });
            req.onsuccess = () => resolve(req.result); req.onerror = () => resolve(null);
        });
        let files = [];
        if (db?.objectStoreNames.contains('files')) files = await new Promise(resolve => {
            const req = db.transaction('files').objectStore('files').getAll();
            req.onsuccess = () => resolve(req.result); req.onerror = () => resolve([]);
        });
        const ids = [...new Set([...snapshots.flatMap(item => item.ids), ...files.map(item => item.id)])];
        try {
            if (!ids.length) return;
            const response = await offlineFetch(new Request(new URL('/offline/post-access/', self.location.origin), {
                method: 'POST', credentials: 'include', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': payload.csrf || '' },
                body: JSON.stringify({ ids: ids.slice(0, 500) })
            }));
            if (!response.ok || (await state()).owner !== before.owner) return;
            const posts = (await response.json()).posts;
            const visible = new Set(posts.map(post => post.id));
            const downloadable = new Set(posts.filter(post => post.downloadable).map(post => post.id));
            // Items beyond the validation batch are left untouched, never guessed revoked.
            const checked = new Set(ids.slice(0, 500));
            for (const item of snapshots) {
                if (item.ids.some(id => checked.has(id) && !visible.has(id))) await cache.delete(item.request);
            }
            if (db?.objectStoreNames.contains('files')) await new Promise(resolve => {
                const tx = db.transaction('files', 'readwrite');
                files.filter(file => (checked.has(file.id) && !downloadable.has(file.id)))
                    .forEach(file => tx.objectStore('files').delete(file.id));
                tx.oncomplete = tx.onerror = resolve;
            });
        } catch (_) { /* Reconciliation can be retried without discarding available content. */ }
        finally { db?.close(); }
    }
    self.addEventListener('message', event => {
        if (event.data?.type === 'RECONCILE_PRIVATE_OFFLINE') event.waitUntil(reconcile(event.data.payload || {}));
        if (event.data?.type === 'SAVE_RENDERED_PAGE') event.waitUntil(exportPage(event));
        if (event.data?.type === 'CLEAR_PRIVATE_OFFLINE') event.waitUntil(clear().then(() => event.ports[0]?.postMessage({ cleared: true })));
    });
    return { eligible, key, observe, clear, saved, navigate, state, offlinePage };
})();
async function offlineFetch(request) {
    const generation = (await OfflinePages.state()).generation;
    const response = await fetch(request);
    const accepted = await OfflinePages.observe(request, response, generation);
    if (accepted === false) return new Response('Account changed. Reopen this page.', { status: 409, headers: { 'Cache-Control': 'no-store' } });
    return response;
}
