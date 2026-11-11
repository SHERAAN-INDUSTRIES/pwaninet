/* Save the rendered UI after HTMX pagination, never login HTML or CSRF material. */
(() => {
    if (!('serviceWorker' in navigator)) return;
    const owner = document.body.dataset.userId;
    if (!owner) return;
    navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'PRIVATE_OFFLINE_INVALIDATED' && event.data.owner !== owner) {
            invalidated = true;
            document.body.replaceChildren(document.createTextNode('Account changed. Open Vasto again to continue.'));
        }
    });
    const mediaDB = `vasto_offline_media_${owner}`;
    let pending, capturing = false, invalidated = false, captureAgain = false;
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
        if(capturing){captureAgain=true;return;}
        clearTimeout(pending);
        pending = setTimeout(capture, /^\/(?:messaging|notifications)\//.test(location.pathname) ? 200 : 900);
    }
    async function capture() {
        if (document.querySelector('.is-generating,.pwanimate-streaming-row') || invalidated || capturing || !navigator.onLine || !navigator.serviceWorker.controller) return;
        capturing = true;
        const route = location.href;
        try {
            const clone = document.documentElement.cloneNode(true);
            clone.querySelectorAll('[data-offline-indicator]').forEach(el => el.remove());
            // Keep the actual layout: forms, inputs, buttons, attributes and wrappers stay put.
            // Only executable/server credentials are excluded from a saved document.
            clone.querySelectorAll('script,base,meta[http-equiv],meta[name*="csrf"]').forEach(el => el.remove());
            clone.querySelectorAll('input,textarea,[contenteditable]').forEach(field => {
                if(!field.matches('input[type="search"],input[name="q"],input[name="query"]')) {
                    field.removeAttribute('value'); if(field.tagName==='TEXTAREA'||field.hasAttribute('contenteditable'))field.textContent='';
                }
            });
            clone.querySelectorAll('form').forEach(form=>{form.removeAttribute('action');form.removeAttribute('method');});
            clone.querySelectorAll('iframe,object,embed').forEach(frame=>{frame.removeAttribute('src');frame.removeAttribute('data');});
            // Merge previously loaded feed cards only after rechecking current access.
            // A successful first-page refresh must not discard pages loaded by scrolling.
            const routeKey = new URL(route);
            routeKey.hash = '';
            ['offline_saved', 'view_cache', 'cached'].forEach(key => routeKey.searchParams.delete(key));
            routeKey.searchParams.sort();
            const previous = await caches.match(routeKey.href,{cacheName:`vasto-private-pages-v1-${owner}`});
            if(invalidated)return;
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
            clone.querySelectorAll('.offline-save-button').forEach(el => el.remove());
            if(invalidated)return;
            const downloads = await records();
            clone.querySelectorAll('video').forEach(video => {
                const saved = downloads.find(item => item.id === (video.dataset.postId || video.dataset.offlineDownloadId));
                video.removeAttribute('src');
                video.querySelectorAll('source').forEach(el => el.remove());
                video.removeAttribute('autoplay');
                video.preload = 'none';
                if (saved?.blob?.size) video.dataset.offlineDownloadId = saved.id;
            });
            if(window.vastoSnapshotResources) await window.vastoSnapshotResources.localize(clone);
            else {
                // Compatibility with older app/script versions: preserve unavailable sources.
                for(const image of clone.querySelectorAll('img'))try{
                    const data=await dataImage(image.getAttribute('src'));if(data)image.src=data;
                }catch(_){}
            }
            clone.querySelectorAll('*').forEach(el => {
                for(const attr of [...el.attributes]) {
                    if(/^on/i.test(attr.name)) {
                        const theme=/setTheme\(['"](light|dark|system)['"]/.exec(attr.value);
                        const navigation=/(?:location.href\s*=|location.assign\()\s*['"](\/[^'"]*)['"]/.exec(attr.value);
                        if(theme)el.dataset.offlineTheme=theme[1];
                        if(navigation)el.dataset.offlineHref=navigation[1];
                        el.removeAttribute(attr.name);
                    } else if(/csrf|token|nonce/i.test(attr.name) || (attr.name.startsWith('data-') && /[?&](?:token|signature|expires|x-amz-[^=]+)=/i.test(attr.value)))el.removeAttribute(attr.name);
                    // HTMX stays inert; Bootstrap/UI data attributes remain available offline.
                    if(['href','src','poster','data-src'].includes(attr.name) || /^https?:\/\//i.test(attr.value)) {
                        try {
                            const url=new URL(attr.value,location.href);
                            if(!['https:','http:','data:','blob:'].includes(url.protocol) || /(?:token|signature|expires|x-amz-)/i.test(url.search))el.removeAttribute(attr.name);
                        }catch(_){el.removeAttribute(attr.name);}
                    }
                }
            });
            const scroll={window:{x:window.scrollX,y:window.scrollY},elements:{}};
            document.querySelectorAll('[id]').forEach(el=>{if(el.scrollTop||el.scrollLeft)scroll.elements[el.id]={x:el.scrollLeft,y:el.scrollTop};});
            const position=document.createElement('meta');position.name='vasto-scroll';position.content=JSON.stringify(scroll);clone.querySelector('head').append(position);
            const timestamp = document.createElement('meta');
            timestamp.name = 'vasto-saved-at'; timestamp.content = String(Date.now());
            clone.querySelector('head').append(timestamp);
            const bootstrap=document.createElement('script');bootstrap.src='/static/js/bootstrap.bundle.min.js';bootstrap.defer=true;clone.querySelector('head').append(bootstrap);
            const runtime = document.createElement('script');
            runtime.src = '/static/js/offline-view.js'; runtime.defer = true;
            clone.querySelector('head').append(runtime);
            if(location.href===route && window.vastoNativeOffline)window.vastoNativeOffline.snapshot({url:route,groupProtected:Boolean(document.querySelector('#leaveGroupBtn,#leaveGroupBtnRail')),html:'<!doctype html>'+clone.outerHTML}).catch(error=>console.warn('[Offline] Native snapshot was not saved',error));
            if (location.href === route) navigator.serviceWorker.controller.postMessage({
                type: 'SAVE_RENDERED_PAGE', payload: { owner, url: route, postIds: [...clone.querySelectorAll('[data-offline-post-id]')].map(card => card.dataset.offlinePostId), html: '<!doctype html>' + clone.outerHTML }
            });
            window.vastoSnapshotResources?.startMedia();
        } catch (error) { console.warn('[Offline] Could not save this view', error); }
        finally { capturing = false; if(captureAgain){captureAgain=false;schedule();} }
    }
    const indicator = document.createElement('aside');
    indicator.style.cssText='position:fixed;bottom:calc(8px + env(safe-area-inset-bottom));left:50%;transform:translateX(-50%);z-index:10000;color:#64748b;font:11px system-ui;white-space:nowrap;pointer-events:none';
    indicator.dataset.offlineIndicator = 'true';
    const updateIndicator = () => { indicator.textContent = 'You are offline viewing cached pages'; };
    updateIndicator();
    indicator.hidden = navigator.onLine;
    document.body.append(indicator);
    window.addEventListener('offline', () => { indicator.hidden = false; });
    window.addEventListener('online', () => { indicator.hidden = true; reconcile(); schedule(); });
    navigator.serviceWorker.addEventListener('controllerchange', schedule);
    document.addEventListener('vasto:offline-resource-ready',schedule);
    document.addEventListener('htmx:afterSettle', schedule);
    window.addEventListener('vasto:download-deleted', schedule);
    window.addEventListener('pagehide', () => { clearTimeout(pending); });
    document.addEventListener('scroll',schedule,{capture:true,passive:true});
    document.addEventListener('submit', event => {
        if (!navigator.onLine) {
            event.preventDefault(); event.stopImmediatePropagation();

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
        } else if (event.target.closest('[hx-post],[hx-delete],[hx-put],[hx-patch],button[type=submit]')) {
            event.preventDefault(); event.stopImmediatePropagation();

        }
    }, true);
    const observer = new MutationObserver(schedule);
    observer.observe(document.querySelector('main') || document.body, { childList: true, subtree: true, characterData: true });
    reconcile(); schedule();
})();
