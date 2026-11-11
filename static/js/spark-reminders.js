/* Native reminders use SQL as truth; local device state is only a scheduling receipt. */
(() => {
    if (window.sparkReminders) return;
    let busy = false;
    const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content || decodeURIComponent(document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '');
    const api = async (path, options = {}) => {
        const response = await fetch('/api/pwanimate/' + path, {credentials: 'same-origin', cache: 'no-store', ...options,
            headers: {'Content-Type': 'application/json', 'X-CSRFToken': csrf(), ...options.headers}});
        if (!response.ok) throw new Error('Spark could not save this change. Please try again.');
        return response.json();
    };
    const nativePlugin = () => {
        const cap = window.Capacitor;
        if (!cap?.isNativePlatform?.()) return null;
        return cap.Plugins?.LocalNotifications || cap.registerPlugin?.('LocalNotifications');
    };
    async function sync() {
        const plugin = nativePlugin();
        if (!plugin || busy || document.hidden) return;
        busy = true;
        try {
            const data = await api('reminders/');
            let installation = localStorage.getItem('spark-reminder-installation');
            if (!installation) { installation = crypto.randomUUID(); localStorage.setItem('spark-reminder-installation', installation); }
            const key = 'spark-device-reminders';
            const previous = JSON.parse(localStorage.getItem(key) || '[]');
            const active = data.reminders.filter(r => r.status === 'scheduled' && new Date(r.due_at) > new Date());
            const wanted = new Set(active.map(r => r.notification_id));
            const cancelled = previous.filter(id => !wanted.has(id));
            if (cancelled.length) await plugin.cancel({notifications: cancelled.map(id => ({id}))});
            if (!active.length) { localStorage.setItem(key, '[]'); return; }
            let permission = await plugin.checkPermissions();
            // Prompt only after a saved reminder exists, once per installation.
            if (permission.display === 'prompt' && !localStorage.getItem('spark-reminder-permission-asked')) {
                localStorage.setItem('spark-reminder-permission-asked', '1');
                permission = await plugin.requestPermissions();
            }
            if (permission.display !== 'granted') return;
            const pending = new Set((await plugin.getPending()).notifications.map(n => n.id));
            const saved = [];
            for (const item of active) {
                if (!pending.has(item.notification_id)) {
                    await plugin.schedule({notifications: [{id: item.notification_id, isExactNotification: false, title: 'Spark study reminder', body: item.title,
                        schedule: {at: new Date(item.due_at), allowWhileIdle: true}, extra: {sparkReminder: item.id}}]});
                }
                await api(`reminders/${item.id}/device/`, {method: 'POST', body: JSON.stringify({installation})});
                saved.push(item.notification_id);
            }
            localStorage.setItem(key, JSON.stringify(saved));
        } catch (error) { console.warn('[Spark reminders]', error.message); }
        finally { busy = false; }
    }
    window.sparkReminders = {sync, api};
    document.addEventListener('visibilitychange', () => { if (!document.hidden) sync(); });
    window.addEventListener('online', sync);
    setInterval(sync, 30000);
    sync();
})();
