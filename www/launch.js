/* No replacement app screens: resume the last original page saved on this phone. */
(() => {
    const params=new URLSearchParams(location.search);
    if(params.has('empty')) {
        const note=document.createElement('div');
        note.textContent='You are offline viewing cached pages';
        note.style.cssText='position:fixed;bottom:16px;left:50%;transform:translateX(-50%);font:12px system-ui;white-space:nowrap;color:#64748b';
        document.body.append(note);
        window.AndroidBridge?.notifyPageReady?.();
        const reopenLive = async()=>{
            let saved={};try{saved=await window.Capacitor?.Plugins?.OfflineStore?.status({});}catch(_){}
            location.replace((saved?.origin||'https://pwaninet.app')+(params.get('route')||'/'));
        };
        window.addEventListener('online',reopenLive);
        if(navigator.onLine !== false)reopenLive();
        return;
    }
    let launched = false;
    async function resume(legacy = {}) {
        if (launched) return;
        launched = true;
        let saved = {};
        try { saved = await window.Capacitor?.Plugins?.OfflineStore?.status({}); } catch (_) {}
        const origin = saved.origin || legacy.origin || 'https://pwaninet.app';
        const target = new URL(saved?.snapshotRoute || (!legacy.signedOut && legacy.route) || '/', origin);
        if (target.origin !== origin) target.href = origin + '/';
        ['offline_saved', 'view_cache', 'cached'].forEach(key => target.searchParams.delete(key));
        if (navigator.onLine === false && (saved?.snapshotRoute || legacy.returning)) target.searchParams.set('offline_saved', '1');
        location.replace(target.href);
    }
    window.showVastoLaunchState = resume;
    // Native onPageLoaded supplies the last route. The timeout covers older app builds.
    setTimeout(() => resume({}), 800);
})();
