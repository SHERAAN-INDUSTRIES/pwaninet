/* Native storage only. The live website and its saved DOM remain the interface. */
(() => {
    const native = window.Capacitor?.Plugins?.OfflineStore;
    const owner = document.body?.dataset.userId;
    if (!native || !owner) return;
    if (window.vastoNativeOffline?.owner === owner) return;
    let ready = native.refresh({ owner }); ready.catch(() => {});
    const run = async (method, payload) => { await ready; return native[method]({ ...payload, owner }); };
    window.vastoNativeOffline = {
        owner,
        resource: url => run('cacheResource', { url }),
        snapshot: payload => run('saveSnapshot', payload),
        remember: payload => native.rememberSnapshot({ ...payload, owner }),
        async retry() { ready = native.refresh({ owner }); return ready; }
    };
    navigator.serviceWorker?.addEventListener('message', event => {
        if (event.data?.type === 'PRIVATE_OFFLINE_INVALIDATED' && String(event.data.owner || '') !== String(owner)) native.clear().catch(() => {});
    });
    document.addEventListener('submit', event => {
        if (/\/(?:logout|switch-account|accounts\/(?:switch|remove))\//.test(event.target.action || '')) native.clear().catch(() => {});
    }, true);
    window.addEventListener('online', () => { ready = native.refresh({ owner }); ready.catch(() => {}); });
})();
