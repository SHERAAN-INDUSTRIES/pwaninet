/* Only this script runs inside a saved document. Never replay writes or tokens. */
(() => {
    navigator.serviceWorker.addEventListener('message', event => {
        if (event.data?.type === 'PRIVATE_OFFLINE_INVALIDATED') {
            document.body.replaceChildren(document.createTextNode('Saved content was cleared or access changed. Connect to reopen this page.'));
        }
    });
    const note = document.createElement('div');
    note.dataset.offlineIndicator = 'true';
    note.style.cssText = 'position:fixed;bottom:16px;left:50%;transform:translateX(-50%);z-index:2147483647;padding:6px 14px;border-radius:999px;background:rgba(220,53,69,.92);color:#fff;font:12px system-ui;letter-spacing:.02em;pointer-events:none;white-space:nowrap;box-shadow:0 4px 12px rgba(0,0,0,.25)';
    const date = document.querySelector('meta[name="vasto-saved-at"]')?.content;
    const stamp = date ? new Date(Number(date)).toLocaleString() : '';
    note.textContent = 'Offline · viewing cached pages' + (stamp ? ' · saved ' + stamp : '');
    document.body.append(note);
    document.addEventListener('click', event => {
        if (event.target.closest('button:not(aside button),input,textarea,[role="button"]')) {
            event.preventDefault();
            alert('Connect to use this control. Your saved content is still available.');
        }
    }, true);
    document.querySelectorAll('.reel-tap-hitbox,.reel-stage-overlay-btn,.video-buffering-indicator').forEach(el => el.remove());
    document.querySelectorAll('video').forEach(video => {
        video.removeAttribute('autoplay');
        video.preload = 'none';
        const id = video.dataset.offlineDownloadId;
        video.querySelectorAll('source').forEach(source => source.remove());
        video.removeAttribute('src');
        if (id) {
            video.src = '/offline-media-stream/' + encodeURIComponent(id);
            video.controls = true;
            video.addEventListener('error', () => {
                video.after(document.createTextNode('Saved video is missing. Connect to save it again.'));
            }, { once: true });
        } else {
            video.after(document.createTextNode('Video needs internet. Save it for offline playback while online.'));
        }
    });
    let reconnecting = false;
    async function reconnect() {
        if (!navigator.onLine || reconnecting) return;
        reconnecting = true;
        try {
            const response = await fetch(url.href, { cache: 'no-store' });
            if (response.ok && response.headers.get('X-Vasto-Offline') === 'rendered-v1' && !response.headers.has('X-Vasto-Saved-At')) {
                location.replace(url.href); // Existing messaging/WebSocket sync resumes online.
            }
        } catch (_) { /* Keep the complete saved conversation visible during outages. */ }
        finally { reconnecting = false; }
    }
    window.addEventListener('online', reconnect);
    // A cold start may already be online, so it need not emit an online event.
    reconnect();
    if (/^\/(?:messaging|notifications)\/|^\/groups\/\d+\/chat\/$/.test(location.pathname)) {
        const retry = setInterval(reconnect, 10000);
        window.addEventListener('pagehide', () => clearInterval(retry), { once: true });
    }
    const end = document.createElement('p');
    end.textContent = 'End of saved content. Connect to load more.';
    document.body.append(end);
})();
