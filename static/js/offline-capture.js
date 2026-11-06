/* Save the rendered UI after HTMX pagination, never login HTML or CSRF material. */
(() => {
    if (!('serviceWorker' in navigator)) return;
    const owner = document.body.dataset.userId;
    if (!owner) return;
    navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'PRIVATE_OFFLINE_INVALIDATED' && event.data.owner !== owner) {
            document.body.replaceChildren(document.createTextNode('Account changed. Open Vasto again to continue.'));
        }
    });
    const mediaDB = `vasto_offline_media_${owner}`;
    let pending, capturing = false;
    const imageCopies = new Map();
    const openMedia = () => new Promise((resolve, reject) => {
        const req = indexedDB.open(mediaDB, 1);
        req.onupgradeneeded = () => req.result.createObjectStore('files', { keyPath: 'id' });
        req.onsuccess = () => resolve(req.result);
        req.onerror = () => reject(req.error);
    });
    async function records() {
        const db = await openMedia();
        try { return await new Promise((resolve, reject) => {
            const req = db.transaction('files').objectStore('files').getAll();
            req.onsuccess = () => resolve(req.result);
            req.onerror = () => reject(req.error);
        }); } finally { db.close(); }
    }
    async function dataImage(url) {
        if (!url) return '';
        const parsed = new URL(url, location.href);
        if (parsed.protocol === 'data:') return url;
        // No cross-origin credentials, no opaque or signed URL persistence.
        if (parsed.origin !== location.origin) return '';
        if (imageCopies.has(parsed.href)) return imageCopies.get(parsed.href);
        const response = await fetch(parsed.href);
        if (!response.ok || /no-store/i.test(response.headers.get('Cache-Control') || '')) return '';
        const blob = await response.blob();
        if (!blob.type.startsWith('image/') || blob.size > 750000) return '';
        return new Promise(resolve => {
            const reader = new FileReader();
            reader.onload = () => { if (imageCopies.size > 32) imageCopies.delete(imageCopies.keys().next().value); imageCopies.set(parsed.href, reader.result); resolve(reader.result); };
            reader.onerror = () => resolve('');
            reader.readAsDataURL(blob);
        });
    }
    function reconcile() {
        if (navigator.onLine) navigator.serviceWorker.controller?.postMessage({ type: 'RECONCILE_PRIVATE_OFFLINE', payload: {
            owner, csrf: document.querySelector('meta[name="csrf-token"]')?.content || ''
        } });
    }
    function schedule() {
        clearTimeout(pending);
        pending = setTimeout(capture, /^\/(?:messaging|notifications)\//.test(location.pathname) ? 200 : 900);
    }
    async function capture() {
        if (document.querySelector('.is-generating,.pwanimate-streaming-row') || capturing || !navigator.onLine || !navigator.serviceWorker.controller) return;
        capturing = true;
        const route = location.href;
        try {
            const clone = document.documentElement.cloneNode(true);
            const back = clone.querySelector('#chatBackBtn');
            if (back) { const link = document.createElement('a'); link.href = '/messaging/'; link.className = back.className; link.textContent = 'Back'; back.replaceWith(link); }
            clone.querySelectorAll('[data-offline-indicator]').forEach(el => el.remove());
            clone.querySelectorAll('script,form,input,textarea,select,[contenteditable],iframe,object,embed,base,meta[http-equiv],meta[name*="csrf"],link:not([rel="stylesheet"])').forEach(el => el.remove());
            // Merge previously loaded feed cards only after rechecking current access.
            // A successful first-page refresh must not discard pages loaded by scrolling.
            const routeKey = new URL(route);
            routeKey.hash = '';
            ['offline_saved', 'view_cache', 'cached'].forEach(key => routeKey.searchParams.delete(key));
            routeKey.searchParams.sort();
            const previous = await (await caches.open(`vasto-private-pages-v1-${owner}`)).match(routeKey.href);
            if (previous && clone.querySelector('.post-card[data-post-id]')) {
                const old = new DOMParser().parseFromString(await previous.clone().text(), 'text/html');
                const existing = new Set([...clone.querySelectorAll('.post-card[data-post-id]')].map(card => card.dataset.postId));
                const extras = [...old.querySelectorAll('.post-card[data-offline-post-id]')].filter(card => !existing.has(card.dataset.offlinePostId));
                if (extras.length) {
                    const response = await fetch('/offline/post-access/', { method: 'POST', headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': document.querySelector('meta[name="csrf-token"]')?.content || ''
                    }, body: JSON.stringify({ ids: extras.map(card => card.dataset.offlinePostId) }) });
                    if (!response.ok) throw new Error('Saved posts could not be revalidated');
                    const allowed = new Set((await response.json()).posts.map(post => post.id));
                    const container = clone.querySelector('.post-card[data-post-id]').parentElement;
                    extras.filter(card => allowed.has(card.dataset.offlinePostId)).forEach(card => container.append(card));
                }
            }
            const csrf = document.querySelector('meta[name="csrf-token"]')?.content || '';
            const chat = clone.querySelector('#messagesContainer');
            const conversation = /^\/messaging\/conversation\/(\d+)\//.exec(location.pathname)?.[1];
            if (previous && conversation && chat) {
                const old = new DOMParser().parseFromString(await previous.clone().text(), 'text/html');
                const currentIds = new Set([...chat.querySelectorAll('.message-wrapper[data-message-id]')].map(row => row.dataset.messageId));
                const extras = [...old.querySelectorAll('.message-wrapper[data-offline-message-id]')].filter(row => !currentIds.has(row.dataset.offlineMessageId));
                if (extras.length) {
                    const response = await fetch('/offline/message-access/', { method: 'POST', headers: {
                        'Content-Type': 'application/json', 'X-CSRFToken': csrf
                    }, body: JSON.stringify({ conversation, ids: extras.map(row => row.dataset.offlineMessageId) }) });
                    if (!response.ok) throw new Error('Saved messages could not be refreshed');
                    const messages = new Map((await response.json()).messages.map(message => [String(message.id), message]));
                    const fragment = document.createDocumentFragment();
                    extras.sort((a, b) => Number(a.dataset.offlineMessageId) - Number(b.dataset.offlineMessageId)).forEach(row => {
                        const message = messages.get(row.dataset.offlineMessageId);
                        if (!message) return;
                        if (message.is_deleted || (message.edited_at && message.edited_at !== row.dataset.offlineMessageVersion)) {
                            row.textContent = message.is_deleted ? 'This message was deleted' : message.is_encrypted ? 'Message updated. Open online to decrypt the updated version.' : message.content;
                        }
                        fragment.append(row);
                    });
                    chat.prepend(fragment);
                }
            }
            if (previous && location.pathname === '/notifications/') {
                const old = new DOMParser().parseFromString(await previous.clone().text(), 'text/html');
                const currentIds = new Set([...clone.querySelectorAll('.notif-item[data-notification-id]')].map(row => row.dataset.notificationId));
                const ids = [...old.querySelectorAll('.notif-item[data-offline-notification-id]')].map(row => row.dataset.offlineNotificationId).filter(id => !currentIds.has(id));
                if (ids.length) {
                    const response = await fetch('/offline/notification-access/', { method: 'POST', headers: {
                        'Content-Type': 'application/json', 'X-CSRFToken': csrf
                    }, body: JSON.stringify({ ids, type: routeKey.searchParams.get('type'), read: routeKey.searchParams.get('read') }) });
                    if (!response.ok) throw new Error('Saved notifications could not be refreshed');
                    const list = clone.querySelector('#notification-list .card-body') || clone.querySelector('#notification-list');
                    (await response.json()).notifications.forEach(item => list?.insertAdjacentHTML('beforeend', item.html));
                }
            }
            for (const [selector, attr] of [['.message-wrapper[data-message-id]', 'data-message-id'], ['.notif-item[data-notification-id]', 'data-notification-id']]) {
                const ids = new Set();
                clone.querySelectorAll(selector).forEach(row => { const id = row.getAttribute(attr); if (ids.has(id)) row.remove(); else ids.add(id); });
            }
            clone.querySelectorAll('.message-wrapper[data-message-id]').forEach(row => { if (!/^\d+$/.test(row.dataset.messageId)) row.remove(); else row.dataset.offlineMessageId = row.dataset.messageId; });
            clone.querySelectorAll('.notif-item[data-notification-id]').forEach(row => { row.dataset.offlineNotificationId = row.dataset.notificationId; });
            // Avoid duplicates introduced by reconnect/pagination races; keep first occurrence order.
            const seen = new Set();
            clone.querySelectorAll('.post-card[data-post-id]').forEach(card => {
                const id = card.dataset.postId;
                card.dataset.offlinePostId = id;
                if (seen.has(id)) card.remove(); else seen.add(id);
            });
            clone.querySelectorAll('#load-more-trigger,[hx-trigger*="revealed"],.offline-save-button').forEach(el => el.remove());
            const downloads = await records();
            clone.querySelectorAll('video').forEach(video => {
                const saved = downloads.find(item => item.id === (video.dataset.postId || video.dataset.offlineDownloadId));
                video.removeAttribute('src');
                video.querySelectorAll('source').forEach(el => el.remove());
                video.removeAttribute('autoplay');
                video.preload = 'none';
                if (saved?.blob?.size) video.dataset.offlineDownloadId = saved.id;
            });
            // Keep only successfully embedded image bytes. No missing image is advertised as saved.
            let imageBytes = 0;
            for (const image of clone.querySelectorAll('img,video[poster]')) {
                const attr = image.tagName === 'VIDEO' ? 'poster' : 'src';
                let data = '';
                if (imageBytes < 1200000) {
                    try { data = await dataImage(image.getAttribute(attr)); } catch (_) {}
                }
                imageBytes += data.length;
                image.removeAttribute('srcset');
                if (data) image.setAttribute(attr, data); else image.removeAttribute(attr);
            }
            clone.querySelectorAll('*').forEach(el => {
                for (const attr of [...el.attributes]) {
                    if (/^on|^hx-|^data-|csrf|token|nonce/i.test(attr.name) && !['data-offline-download-id', 'data-offline-post-id', 'data-offline-message-id', 'data-offline-message-version', 'data-offline-notification-id'].includes(attr.name)) el.removeAttribute(attr.name);
                    if (['href', 'src', 'poster'].includes(attr.name)) {
                        try {
                            const url = new URL(attr.value, location.href);
                            if (!['https:', 'data:', location.protocol].includes(url.protocol) ||
                                (url.protocol !== 'data:' && (url.origin !== location.origin || /(?:token|signature|expires|x-amz-)/i.test(url.search)))) el.removeAttribute(attr.name);
                        } catch (_) { el.removeAttribute(attr.name); }
                    }
                }
            });
            const timestamp = document.createElement('meta');
            timestamp.name = 'vasto-saved-at'; timestamp.content = String(Date.now());
            clone.querySelector('head').append(timestamp);
            const runtime = document.createElement('script');
            runtime.src = '/static/js/offline-view.js'; runtime.defer = true;
            clone.querySelector('head').append(runtime);
            if (location.href === route) navigator.serviceWorker.controller.postMessage({
                type: 'SAVE_RENDERED_PAGE', payload: { owner, url: route, postIds: [...clone.querySelectorAll('[data-offline-post-id]')].map(card => card.dataset.offlinePostId), html: '<!doctype html>' + clone.outerHTML }
            });
        } catch (error) { console.warn('[Offline] Could not save this view', error); }
        finally { capturing = false; }
    }
    const indicator = document.createElement('aside');
    indicator.style.cssText = 'position:fixed;bottom:80px;right:12px;z-index:10000;background:#17233b;color:white;border-radius:8px;padding:8px;font-size:12px';
    indicator.dataset.offlineIndicator = 'true';
    let lastSaved = 0;
    const updateIndicator = () => { indicator.textContent = 'Offline · available content stays visible' + (lastSaved ? ' · Saved ' + new Date(lastSaved).toLocaleTimeString() : ''); };
    navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'PRIVATE_PAGE_SAVED') { lastSaved = event.data.savedAt; updateIndicator(); }
    });
    updateIndicator();
    indicator.hidden = navigator.onLine;
    document.body.append(indicator);
    window.addEventListener('offline', () => { indicator.hidden = false; });
    window.addEventListener('online', () => { indicator.hidden = true; reconcile(); schedule(); });
    navigator.serviceWorker.addEventListener('controllerchange', schedule);
    document.addEventListener('htmx:afterSettle', schedule);
    window.addEventListener('vasto:download-deleted', schedule);
    window.addEventListener('pagehide', () => { clearTimeout(pending); });
    document.addEventListener('submit', event => {
        if (!navigator.onLine) {
            event.preventDefault(); event.stopImmediatePropagation();
            alert('Connect to send this request. Your draft stays on screen.');
        }
    }, true);
    document.addEventListener('click', event => {
        if (navigator.onLine) return;
        const link = event.target.closest('a[href]');
        if (link) {
            const url = new URL(link.href, location.href);
            if (url.origin === location.origin && url.hash === '' && !link.hasAttribute('download')) {
                event.preventDefault(); event.stopImmediatePropagation();
                location.assign(url.href);
            }
        } else if (event.target.closest('button,[hx-post],[hx-delete],[hx-put],[hx-patch]')) {
            event.preventDefault(); event.stopImmediatePropagation();
            alert('Connect to use this control. Your available content stays on screen.');
        }
    }, true);
    const observer = new MutationObserver(schedule);
    observer.observe(document.querySelector('main') || document.body, { childList: true, subtree: true, characterData: true });
    reconcile(); schedule();
})();
