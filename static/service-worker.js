/**
 * vasto Service Worker
 * Handles offline support, caching, and push notifications
 * Version: 1.2.0 - Updated offline page design and caching
 */

'use strict';
let CACHE_VERSION = '1.6.4';
let CACHE_BUILD = '117';
const SW_CACHE_REVISION = '20';
importScripts('/static/js/offline-pages-worker.js');
let CACHE_NAME = `vasto-v${CACHE_VERSION}-build${CACHE_BUILD}-sw${SW_CACHE_REVISION}`;
let OFFLINE_CACHE_NAME = `vasto-offline-v${CACHE_VERSION}-build${CACHE_BUILD}-sw${SW_CACHE_REVISION}`;

// Service worker version metadata
const SW_VERSION = {
    version: CACHE_VERSION,
    build: CACHE_BUILD,
    buildDate: new Date().toISOString(),
    cacheName: CACHE_NAME,
    environment: 'production',
    description: 'See more post content button enhancement and fix'
};

// Set version from message (called during registration)
self.addEventListener('message', (event) => {
    if (event.data && event.data.type === 'SET_VERSION') {
        CACHE_VERSION = event.data.version;
        CACHE_BUILD = event.data.build;
        CACHE_NAME = `vasto-v${CACHE_VERSION}-build${CACHE_BUILD}-sw${SW_CACHE_REVISION}`;
        OFFLINE_CACHE_NAME = `vasto-offline-v${CACHE_VERSION}-build${CACHE_BUILD}-sw${SW_CACHE_REVISION}`;
        SW_VERSION.version = CACHE_VERSION;
        SW_VERSION.build = CACHE_BUILD;
        SW_VERSION.cacheName = CACHE_NAME;
        SW_VERSION.environment = event.data.environment || 'development';
        SW_VERSION.buildDate = event.data.buildDate || new Date().toISOString();
        console.log('Service Worker: Version set to', CACHE_VERSION, 'build', CACHE_BUILD);
    }
});

// Core assets to cache for offline functionality
const CORE_ASSETS = [
    '/offline.html',
    '/static/js/offline-view.js',
    '/static/js/offline-capture.js',
    '/static/css/bootstrap.min.css',
    '/static/css/bootstrap-icons.css',
    '/static/css/custom.css',
    '/static/css/postcard.css',
    '/static/css/fonts.css',
    '/static/css/profile.css',
    '/static/css/comments.css',
    '/static/css/people-modal.css',
    '/static/notifications/css/notifications.css',
    '/static/css/downloads/offline_media_viewer.css',
    '/static/js/bootstrap.bundle.min.js',
    '/static/images/favicon.ico',
    '/static/images/favicon-96x96.png',
    '/static/images/favicon.svg',
    '/static/images/apple-touch-icon.png',
    '/static/images/web-app-manifest-192x192.png',
    '/static/images/web-app-manifest-192x192-rounded.png',
    '/static/images/web-app-manifest-512x512.png',
    '/static/images/vastomonochrome.png',
    '/static/images/browserconfig.xml',
    '/static/js/native-pwa-install.js',
    '/static/js/downloads/download_storage.js',
    '/static/js/downloads/download_queue.js',
    '/static/js/downloads/download_manager.js',
    '/static/js/downloads/download_ui.js',
    '/static/js/downloads/offline_media_viewer.js'
];

// Install event - cache core assets
self.addEventListener('install', (event) => {
    console.log('Service Worker: Installing...');
    
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log('Service Worker: Caching core assets');
                return Promise.all(CORE_ASSETS.map(async url => {
                    try {
                        const response = await fetch(url);
                        if (response.ok) await cache.put(url, response);
                    } catch (_) { /* A missing optional icon cannot abort installation. */ }
                }));
            })
            .then(() => {
                console.log('Service Worker: Core assets cached, activating immediately');
                return self.skipWaiting();
            })
            .catch((error) => {
                console.error('Service Worker: Failed to cache core assets:', error);
            })
    );
});

// Activate event - clean up old caches and take control
self.addEventListener('activate', (event) => {
    console.log('Service Worker: Activating...');
    
    event.waitUntil(
        caches.keys()
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames.map((cacheName) => {
                        const isvastoWorkerCache =
                            cacheName.startsWith('vasto-v') ||
                            cacheName.startsWith('vasto-offline-v');
                        if (isvastoWorkerCache && cacheName !== CACHE_NAME && cacheName !== OFFLINE_CACHE_NAME) {
                            console.log('Service Worker: Deleting old cache:', cacheName);
                            return caches.delete(cacheName);
                        }
                    })
                );
            })
            .then(() => {
                console.log('Service Worker: Activated and claiming clients');
                return self.clients.claim();
            })
            .then(() => {
                return self.clients.matchAll({ type: 'window' }).then((clients) => {
                    clients.forEach((client) => {
                        client.postMessage({
                            type: 'NEW_VERSION_ACTIVATED',
                            version: CACHE_VERSION,
                            build: CACHE_BUILD
                        });
                    });
                });
            })
    );
});

// Push event - handle incoming push notifications
self.addEventListener('push', (event) => {
    console.log('Service Worker: Push event received');

    let pushData = {
        title: 'vasto Notification',
        body: 'You have a new notification',
        icon: '/static/images/web-app-manifest-192x192-rounded.png',
        badge: '/static/images/vastomonochrome.png',
        vibrate: [200, 100, 200],
        requireInteraction: false,
        actions: [
            {
                action: 'view',
                title: 'View',
                icon: '/static/images/favicon-96x96.png'
            },
            {
                action: 'dismiss',
                title: 'Dismiss',
                icon: '/static/images/favicon-96x96.png'
            }
        ],
        data: {
            url: '/notifications/'
        }
    };

    if (event.data) {
        try {
            const data = event.data.json();
            console.log('Service Worker: Received push data:', JSON.stringify(data, null, 2));
            pushData = {
                title: data.title || pushData.title,
                body: data.body || pushData.body,
                icon: data.icon || pushData.icon,
                badge: data.badge || pushData.badge,
                image: data.image || undefined,
                tag: data.tag || undefined,
                renotify: data.renotify !== undefined ? data.renotify : undefined,
                vibrate: data.vibrate || pushData.vibrate,
                requireInteraction: data.requireInteraction || pushData.requireInteraction,
                actions: data.actions || pushData.actions,
                data: {
                    notification_id: data.data?.notification_id,
                    conversation_id: data.data?.conversation_id,
                    url: data.data?.url || pushData.data.url,
                    destination_url: data.data?.destination_url,
                    notification_type: data.data?.notification_type,
                    category: data.data?.category,
                    tag: data.data?.tag || data.tag
                }
            };
            console.log('Service Worker: Final push data to display:', JSON.stringify(pushData, null, 2));
        } catch (e) {
            console.error('Service Worker: Failed to parse push data:', e);
        }
    } else {
        console.log('Service Worker: No data in push event, using default');
    }

    event.waitUntil(
        self.clients.matchAll({ type: 'window', includeUncontrolled: true }).then((clientList) => {
            const convId = pushData.data?.conversation_id;
            if (convId) {
                const targetPath = `/messaging/conversation/${convId}`;
                const isActivelyViewingChat = clientList.some((client) => {
                    try {
                        const clientPath = new URL(client.url).pathname;
                        return (
                            (client.focused || client.visibilityState === 'visible') &&
                            clientPath.indexOf(targetPath) === 0
                        );
                    } catch (_) {
                        return false;
                    }
                });
                if (isActivelyViewingChat) {
                    console.log('Service Worker: Suppressing OS push because user is actively viewing conversation', convId);
                    return;
                }
            }
            return self.registration.showNotification(pushData.title, pushData);
        })
    );
});

// Notification click event - handle user interaction with notifications
self.addEventListener('notificationclick', (event) => {
    console.log('Service Worker: Notification clicked', event.action, event.notification);

    event.notification.close();

    // Handle action button clicks
    if (event.action === 'dismiss') {
        console.log('Service Worker: Dismiss action clicked');
        return;
    }

    const convId = event.notification.data?.conversation_id;
    if (event.action === 'mark_read' && convId) {
        event.waitUntil(
            fetch(`/messaging/v1/conversations/${convId}/mark_read/`, {
                method: 'POST',
                credentials: 'same-origin',
                headers: {
                    'Content-Type': 'application/json'
                }
            }).catch((err) => {
                console.warn('Service Worker: mark_read request failed:', err);
            })
        );
        return;
    }

    // Handle view action or default click
    const urlToOpen = event.notification.data?.url || event.notification.data?.destination_url || '/notifications/';

    event.waitUntil(
        clients.matchAll({
            type: 'window',
            includeUncontrolled: true
        }).then((clientList) => {
            // Check if there's already a window open with this exact url
            for (const client of clientList) {
                if (client.url === new URL(urlToOpen, self.location.origin).href && 'focus' in client) {
                    return client.focus();
                }
            }

            // If an app window is already open, focus it and navigate to the target
            for (const client of clientList) {
                if ('focus' in client) {
                    client.focus();
                    if ('navigate' in client) {
                        return client.navigate(urlToOpen);
                    }
                    return;
                }
            }

            // If no window is open, open a new one
            if (clients.openWindow) {
                return clients.openWindow(urlToOpen);
            }
        })
    );
});

// Fetch event - intercept requests and provide offline support
self.addEventListener('fetch', (event) => {
    const { request } = event;
    const url = new URL(request.url);
    
    if (url.origin !== self.location.origin) return;
    if (/(?:\/(?:logout|switch-account|remove-account)(?:\/|$)|\/users\/accounts\/(?:switch|remove)\/)/.test(url.pathname)) {
        event.respondWith(OfflinePages.clear().then(() => offlineFetch(request)));
        return;
    }
    if (request.method !== 'GET') {
        event.respondWith(offlineFetch(request));
        return;
    }
    if (request.mode === 'navigate') {
        event.respondWith(OfflinePages.navigate(request));
        return;
    }
    // Offline Media Hub keeps its own cache-first handler (it serves HTMX
    // partials while offline), so it is exempt from the HTMX bypass below.
    const isOfflineMedia = url.pathname === '/offline-media/' ||
                           url.pathname.startsWith('/offline-media-stream/');
    
    // HTMX partials are HTML fragments, not pages. They are never cached,
    // and turning a transient network failure into a 503 "Offline" body
    // makes htmx read a network blip as a server error (it then swaps
    // nothing and the user sees a dead page). Let them hit the network
    // directly so htmx sees the real failure and can fall back.
    if (!isOfflineMedia && isHtmxRequest(request)) {
        event.respondWith(offlineFetch(request));
        return;
    }
    
    event.respondWith(handleRequest(request));
});

function isHtmxRequest(request) {
    return request.headers.get('HX-Request') === 'true';
}

async function handleRequest(request) {
    const url = new URL(request.url);
    
    try {
        // Handle virtual offline media stream with range support (HTTP 206)
        if (url.pathname.startsWith('/offline-media-stream/')) {
            return await handleOfflineMediaStreamRequest(request);
        }

        // Dedicated handler for Offline Media Hub (/offline-media/) - supports HTMX partials & full page offline
        if (url.pathname === '/offline-media/') {
            return await handleOfflineMediaRequest(request);
        }

        // Try network first for navigation requests
        if (isNavigationRequest(request)) {
            return await handleNavigationRequest(request);
        }
        
        // Try cache first for static assets
        if (isStaticAsset(request)) {
            return await handleStaticAssetRequest(request);
        }
        
        // Try network with cache fallback for API requests
        if (isApiRequest(request)) {
            return await handleApiRequest(request);
        }
        
        // Default: network first with cache fallback
        return await networkFirst(request);
        
    } catch (error) {
        console.error('Service Worker: Request handling failed for', request.url, error);
        return await getOfflineResponse(request);
    }
}

async function handleOfflineMediaRequest(request) {
    return OfflinePages.navigate(request);
}

async function handleNavigationRequest(request) {
    return OfflinePages.navigate(request);
}

async function handleStaticAssetRequest(request) {
    const url = new URL(request.url);
    if (!url.pathname.startsWith('/static/')) return offlineFetch(request);
    const cached = await caches.match(request, { ignoreSearch: true });
    // Versioned scripts/styles revalidate; ordinary icons/fonts can reuse their copy.
    if (cached && !/\.(?:js|css)$/.test(url.pathname)) return cached;
    try {
        const response = await fetch(request);
        if (response.status >= 500 && cached) return cached;
        if (response.ok && !/no-store/i.test(response.headers.get('Cache-Control') || '') &&
            !/text\/html/i.test(response.headers.get('Content-Type') || '')) {
            const cache = await caches.open(CACHE_NAME);
            await cache.put(request, response.clone());
            const keys = await cache.keys();
            while (keys.length > 300) await cache.delete(keys.shift());
        }
        return response;
    } catch (_) {
        return cached || getOfflineAsset(request);
    }
}

async function handleOfflineMediaStreamRequest(request) {
    const current = await OfflinePages.state();
    if (!current.owner) return new Response('Sign in to access saved media', { status: 401 });
    const id = decodeURIComponent(new URL(request.url).pathname.split('/')[2] || '');
    const blob = await new Promise(resolve => {
        const req = indexedDB.open(`vasto_offline_media_${current.owner}`, 1);
        req.onupgradeneeded = () => req.result.createObjectStore('files', { keyPath: 'id' });
        req.onerror = () => resolve(null);
        req.onsuccess = () => {
            const db = req.result;
            if (!db.objectStoreNames.contains('files')) { db.close(); resolve(null); return; }
            const tx = db.transaction('files');
            const get = tx.objectStore('files').get(id);
            get.onsuccess = () => resolve(get.result ? get.result.blob : null);
            get.onerror = () => resolve(null);
            tx.oncomplete = () => db.close();
        };
    });
    if (!blob?.size) return new Response('Video was not saved or was removed', { status: 404 });
    // Recheck ownership after asynchronous storage reads.
    if ((await OfflinePages.state()).owner !== current.owner) return new Response('Account changed', { status: 403 });
    const headers = { 'Content-Type': blob.type || 'video/mp4', 'Accept-Ranges': 'bytes', 'Cache-Control': 'no-store' };
    const range = request.headers.get('Range');
    if (!range) return new Response(blob, { headers: { ...headers, 'Content-Length': String(blob.size) } });
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    let start, end;
    if (match && (match[1] || match[2])) {
        start = match[1] ? Number(match[1]) : Math.max(0, blob.size - Number(match[2]));
        end = match[1] && match[2] ? Math.min(Number(match[2]), blob.size - 1) : blob.size - 1;
    }
    if (!Number.isSafeInteger(start) || !Number.isSafeInteger(end) || start < 0 || start >= blob.size || end < start) {
        return new Response(null, { status: 416, headers: { ...headers, 'Content-Range': `bytes */${blob.size}` } });
    }
    const chunk = blob.slice(start, end + 1);
    return new Response(chunk, { status: 206, headers: { ...headers,
        'Content-Range': `bytes ${start}-${end}/${blob.size}`, 'Content-Length': String(chunk.size)
    } });
}

async function handleApiRequest(request) {
    try { return await offlineFetch(request); }
    catch (_) { return getOfflineApiResponse(request); }
}
async function networkFirst(request) {
    return offlineFetch(request);
}

async function getOfflineResponse(request) {
    const url = new URL(request.url);

    // Return cached offline media viewer response
    if (url.pathname === '/offline-media/') {
        const cached = (await caches.match(request)) || (await caches.match('/offline-media/'));
        if (cached) return cached;
    }
    
    // Return offline page for navigation requests
    if (isNavigationRequest(request)) {
        return await getOfflinePage();
    }
    
    // Return offline asset for static assets
    if (isStaticAsset(request)) {
        return await getOfflineAsset(request);
    }
    
    // Return offline API response
    if (isApiRequest(request)) {
        return await getOfflineApiResponse(request);
    }
    
    // Default offline response. XHR/fetch callers expect JSON - handing
    // them the text "Offline" makes their JSON.parse throw on top of the
    // real failure.
    const accept = request.headers.get('Accept') || '';
    if (accept.includes('application/json') || request.destination === '') {
        return await getOfflineApiResponse(request);
    }
    
    return new Response('Offline', {
        status: 503,
        statusText: 'Service Unavailable'
    });
}

async function getOfflinePage() {
    return OfflinePages.offlinePage();
}

async function getOfflineAsset(request) {
    // Return placeholder for missing assets
    const url = new URL(request.url);
    
    if (url.pathname.includes('.css')) {
        return new Response('/* CSS asset unavailable offline */', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: { 'Content-Type': 'text/plain' }
        });
    }
    
    if (url.pathname.includes('.js')) {
        return new Response('/* JavaScript asset unavailable offline */', {
            status: 503,
            statusText: 'Service Unavailable',
            headers: { 'Content-Type': 'text/plain' }
        });
    }
    
    if (url.pathname.includes('.png') || url.pathname.includes('.jpg') || url.pathname.includes('.ico')) {
        // Return 1x1 transparent pixel
        return new Response('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNkYPhfDwAChwGA60e6kgAAAABJRU5ErkJggg==', {
            status: 200,
            headers: { 'Content-Type': 'image/png' }
        });
    }
    
    return new Response('Offline', { status: 503 });
}

async function getOfflineApiResponse(request) {
    return new Response(JSON.stringify({
        error: 'offline',
        message: 'You are currently offline. Please check your internet connection.',
        timestamp: new Date().toISOString()
    }), {
        status: 503,
        statusText: 'Service Unavailable',
        headers: {
            'Content-Type': 'application/json'
        }
    });
}

// Helper functions
function isNavigationRequest(request) {
    return request.mode === 'navigate' || 
           (request.destination === 'document' && request.method === 'GET');
}

function isStaticAsset(request) {
    const url = new URL(request.url);
    return url.pathname.includes('/static/') || 
           url.pathname.includes('/media/') ||
           url.pathname.match(/\.(css|js|png|jpg|jpeg|gif|svg|ico|woff|woff2|ttf|eot)$/);
}

function isApiRequest(request) {
    const url = new URL(request.url);
    return url.pathname.includes('/api/') || 
           url.pathname.includes('/v1/') ||
           url.pathname.startsWith('/messaging/');
}

    // Message handling for cache management and version detection
self.addEventListener('message', (event) => {
    const { type, payload } = event.data;
    
    switch (type) {
        case 'SKIP_WAITING':
            // User has approved the update - activate immediately
            console.log('Service Worker: SKIP_WAITING received, activating...');
            self.skipWaiting();
            // After skipWaiting, the service worker will activate and we should claim clients
            self.clients.claim();
            break;

        case 'ACTIVATE_UPDATE':
            // Alternative message name for activation
            console.log('Service Worker: ACTIVATE_UPDATE received, activating...');
            self.skipWaiting();
            self.clients.claim();
            break;
            
        case 'GET_CACHE_INFO':
            getCacheInfo().then(info => {
                event.ports[0].postMessage({ type: 'CACHE_INFO', payload: info });
            });
            break;
            
        case 'CLEAR_CACHE':
            clearCache().then(() => {
                event.ports[0].postMessage({ type: 'CACHE_CLEARED' });
            });
            break;
            
        case 'CACHE_PAGE':
            cachePage(payload.url).then(success => {
                event.ports[0].postMessage({ type: 'PAGE_CACHED', payload: { success } });
            });
            break;
            
        case 'GET_VERSION':
            event.ports[0].postMessage({ type: 'VERSION_INFO', payload: SW_VERSION });
            break;
            
        case 'CHECK_UPDATE_STATUS':
            event.ports[0].postMessage({ 
                type: 'UPDATE_STATUS', 
                payload: { updateAvailable: false } 
            });
            break;
            
        case 'ACTIVATE_UPDATE':
            self.skipWaiting();
            break;

        case 'CLOSE_CHAT_NOTIFICATIONS':
            if (self.registration && typeof self.registration.getNotifications === 'function') {
                const targetTag = (payload && payload.tag) || (payload && payload.conversation_id ? `vasto-chat-${payload.conversation_id}` : null);
                const opts = targetTag ? { tag: targetTag } : {};
                self.registration.getNotifications(opts).then((notifications) => {
                    notifications.forEach((n) => {
                        if (!targetTag || n.tag === targetTag || (payload && payload.conversation_id && String(n.data?.conversation_id) === String(payload.conversation_id))) {
                            n.close();
                        }
                    });
                }).catch(() => {});
            }
            break;
    }
});

async function getCacheInfo() {
    const cache = await caches.open(CACHE_NAME);
    const keys = await cache.keys();
    
    return {
        cacheName: CACHE_NAME,
        itemCount: keys.length,
        urls: keys.map(request => request.url)
    };
}

async function clearCache() {
    await OfflinePages.clear();
    await caches.delete(CACHE_NAME);
    await caches.delete(OFFLINE_CACHE_NAME);
}

async function cachePage(url) {
    // Raw HTML may contain CSRF tokens; only SAVE_RENDERED_PAGE can persist it.
    return false;
}
