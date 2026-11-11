import { CallSession, duration, formatDuration } from './session.mjs';
import { VoiceMedia } from './media.mjs';
import { recoveryHint, validHint } from './recovery.mjs';
import { CallAlerts } from './alerts.mjs';
import { showNotification } from '../shared/utils/notification.js';

const root = document.getElementById('pwani-call-root');
if (root && !window.PwaniCalls) {
  const $ = id => root.querySelector(`#${id}`);
  const screen = $('call-screen'), banner = $('call-banner'), pill = $('call-pill');
  const csrf = () => document.cookie.split('; ').find(v => v.startsWith('csrftoken='))?.split('=').slice(1).join('=') || document.querySelector('meta[name="csrf-token"]')?.content || '';
  const request = async (path = '', data) => {
    const response = await fetch(root.dataset.api + path, {
      method: data === undefined ? 'GET' : 'POST', credentials: 'same-origin', cache: 'no-store', signal: AbortSignal.timeout(20000),
      headers: { Accept: 'application/json', ...(data === undefined ? {} : { 'Content-Type': 'application/json', 'X-CSRFToken': csrf() }) },
      ...(data === undefined ? {} : { body: JSON.stringify(data) }),
    });
    const payload = await response.json().catch(() => ({}));
    if (!response.ok) { const error = new Error(payload.detail || 'The call request could not be completed.'); error.status = response.status; error.code = payload.code; throw error; }
    return payload;
  };
  const api = {
    ensureAvailable: () => { if (root.dataset.available !== 'true') { const error = new Error('Voice calling is not configured.'); error.status = 503; throw error; } },
    start: conversation_id => request('', { conversation_id }),
    action: (id, action) => request(`${id}/`, { action }),
    get: id => request(`${id}/`),
    connection: id => request(`${id}/connection/`, {}),
  };
  const media = new VoiceMedia({ sdkUrl: root.dataset.sdk, audioHost: $('call-audio') });
  let focusBefore, lastAnnouncement = '', avatarKey = '', outputsKey = '', ownsTab = false, releaseTab;
  let refreshInFlight = false, outcomeToastEpoch = -1, panelCallId = null;
  const ownerId = crypto.randomUUID();
  const leaseKey = `pwani-call-owner:${root.dataset.userId}`;
  function releaseOwnership() {
    if (!ownsTab) return;
    ownsTab = false; releaseTab?.(); releaseTab = null;
    try { if (JSON.parse(localStorage.getItem(leaseKey) || 'null')?.id === ownerId) localStorage.removeItem(leaseKey); } catch (_) {}
  }
  async function ownership() {
    if (ownsTab) return true;
    if (navigator.locks) {
      return new Promise(resolve => {
        navigator.locks.request(leaseKey, { ifAvailable: true }, lock => {
          if (!lock) { resolve(false); return; }
          ownsTab = true; resolve(true);
          return new Promise(release => { releaseTab = release; });
        }).catch(() => resolve(false));
      });
    }
    try {
      const lease = JSON.parse(localStorage.getItem(leaseKey) || 'null');
      if (lease && lease.id !== ownerId && lease.expires > Date.now()) return false;
      localStorage.setItem(leaseKey, JSON.stringify({ id: ownerId, expires: Date.now() + 15000 }));
      ownsTab = JSON.parse(localStorage.getItem(leaseKey)).id === ownerId;
    } catch (_) { ownsTab = true; }
    return ownsTab;
  }
  const recoveryKey = `pwani-call-recovery:${root.dataset.userId}`;
  let savedHint = null, recovering = true;
  try { savedHint = validHint(JSON.parse(sessionStorage.getItem(recoveryKey) || 'null')); } catch (_) {}
  const alerts = new CallAlerts();
  document.addEventListener('pointerdown', () => alerts.unlock(), { passive: true });
  document.addEventListener('keydown', () => alerts.unlock(), { passive: true });
  const desktop = window.matchMedia('(min-width: 992px)');
  function saveRecovery() {
    if (recovering) return;
    try { const hint = recoveryHint(session.state); if (hint && desktop.matches && screen.open) { const rect = screen.getBoundingClientRect(); hint.panel = { x: rect.left, y: rect.top, width: rect.width, height: rect.height, expanded: screen.classList.contains('is-expanded') }; } if (hint) sessionStorage.setItem(recoveryKey, JSON.stringify(hint)); else sessionStorage.removeItem(recoveryKey); } catch (_) {}
  }
  const session = new CallSession({ api, media, userId: root.dataset.userId, changed: render });
  const outcomes = {
    ended: 'Call ended', cancelled: 'Call cancelled', declined: 'Call declined', no_answer: 'No answer',
    elsewhere: 'Answered on another device', other_session: 'Call active in another session',
    unconfigured: 'Calling is not available yet', busy: 'Call could not start', failed: 'Couldn’t connect',
  };
  const labels = { initiating: 'Calling…', ringing: 'Ringing…', incoming: 'Incoming voice call', connecting: 'Connecting…', connected: 'Connected', reconnecting: 'Reconnecting…', ending: 'Ending call…', end_failed: 'Finish ending the call', permission: 'Microphone access needed' };
  function text(id, value) { const el = $(id); if (el.textContent !== value) el.textContent = value; }
  function show(id, visible) { $(id).hidden = !visible; }
  let historySyncedCall = null;
  function render(state) {
    const idle = state.phase === 'idle', ended = state.phase === 'ended';
    if (ended && state.call?.id && historySyncedCall !== state.call.id) {
      historySyncedCall = state.call.id;
      window.dispatchEvent(new CustomEvent('pwani:call-history-ready', { detail: state.call }));
    }
    if (state.call?.id && state.call.id !== panelCallId) {
      panelCallId = state.call.id;
      for (const name of ['--call-panel-x', '--call-panel-y', '--call-panel-bottom']) root.style.removeProperty(name);
      screen.style.removeProperty('width'); screen.style.removeProperty('height'); screen.classList.remove('is-expanded');
      const expand = root.querySelector('[data-call-action="expand"]');
      expand.setAttribute('aria-pressed', 'false'); expand.setAttribute('aria-label', 'Expand call panel');
    }
    const incoming = state.phase === 'incoming';
    const full = !idle && (desktop.matches || state.presentation === 'full');
    if (full && screen.open && screen.matches(':modal') === desktop.matches) screen.close();
    if (full && !screen.open && desktop.matches) screen.show();
    if (full && !screen.open) { focusBefore = document.activeElement; screen.showModal(); requestAnimationFrame(() => screen.querySelector('[data-call-action="minimize"]')?.focus()); }
    if (!full && screen.open) { screen.close(); if (focusBefore?.isConnected) focusBefore.focus({ preventScroll: true }); }
    banner.hidden = idle || full || !incoming;
    pill.hidden = idle || full || ended || incoming;
    document.body.classList.toggle('call-pill-active', !pill.hidden);
    if (!session.live) releaseOwnership();
    saveRecovery(); alerts.update(state);
    const status = ended ? (state.outcome === 'no_answer' && state.direction === 'incoming' ? 'Missed call' : outcomes[state.outcome] || 'Call ended') : labels[state.phase] || '';
    text('call-person', state.peer?.name || 'PwaniNet member');
    root.querySelectorAll('[data-call-name]').forEach(el => { el.textContent = state.peer?.name || 'PwaniNet member'; });
    text('call-status', status);
    text('call-detail', state.phase === 'permission' ? state.error : state.phase === 'reconnecting' ? 'Audio is temporarily interrupted. Trying to reconnect.' :
      state.outcome === 'unconfigured' ? 'Voice calling needs to be configured before you can connect.' :
      state.outcome === 'elsewhere' ? 'This invitation is closed here. Your call continues on the other device.' :
      state.error || (ended && state.connectedAt != null ? 'Thanks for making time to connect.' : ''));
    const avatar = state.peer?.avatar || '';
    if (avatarKey !== avatar) {
      avatarKey = avatar;
      root.querySelectorAll('[data-call-avatar]').forEach(el => {
        el.replaceChildren();
        if (avatar && ['http:', 'https:'].includes(new URL(avatar, location.origin).protocol)) {
          const img = document.createElement('img'); img.src = avatar; img.alt = '';
          img.onerror = () => { img.remove(); el.innerHTML = '<i class="bi bi-person-fill" aria-hidden="true"></i>'; };
          el.append(img);
        } else el.innerHTML = '<i class="bi bi-person-fill" aria-hidden="true"></i>';
      });
    }
    show('call-incoming-actions', incoming);
    show('call-live-controls', ['connected', 'reconnecting'].includes(state.phase));
    show('call-control-pill', !idle && !ended && !incoming && state.phase !== 'permission');
    $('call-control-pill').classList.toggle('is-live', ['connected', 'reconnecting'].includes(state.phase));
    show('call-end', !idle && !ended && !incoming && state.phase !== 'permission');
    $('call-end').querySelector('span').textContent = ['initiating', 'ringing'].includes(state.phase) ? 'Cancel call' : 'End call';
    show('call-outcome-actions', ended);
    show('call-permission-actions', state.phase === 'permission');
    root.querySelector('[data-call-action="permission"]').textContent = state.permissionBlocked ? 'Try again' : 'Allow microphone';
    const retry = root.querySelector('[data-call-action="retry"]');
    retry.hidden = ['elsewhere', 'other_session', 'unconfigured'].includes(state.outcome);
    retry.textContent = 'Call again';
    root.querySelectorAll('[data-call-action="speaker"]').forEach(button => {
      button.setAttribute('aria-pressed', String(!!state.speaker));
      button.setAttribute('aria-label', state.speaker ? 'Turn off loudspeaker' : 'Turn on loudspeaker');
      button.disabled = !['connected', 'reconnecting'].includes(state.phase);
    });
    const mute = root.querySelector('[data-call-action="mute"]');
    mute.setAttribute('aria-pressed', String(!!state.muted));
    mute.querySelector('i').className = state.muted ? 'bi bi-mic-mute-fill' : 'bi bi-mic';
    mute.querySelector('[data-mute-label]').textContent = state.muted ? 'Unmute' : 'Mute';
    root.querySelectorAll('[data-call-action="answer"], [data-call-action="decline"]').forEach(el => { el.disabled = !!session.pending; });
    $('call-end').disabled = state.phase === 'ending';
    root.querySelector('[data-call-action="permission"]').disabled = !!session.pending;
    show('call-playback', !!state.playbackBlocked);
    if (!idle && status !== lastAnnouncement) { $('call-announcement').textContent = status; lastAnnouncement = status; }
    if (idle) { lastAnnouncement = ''; $('call-announcement').textContent = ''; }
    if (state.phase === 'connected' && outputsKey !== state.call?.id) {
      outputsKey = state.call?.id;
      media.outputs().then(devices => {
        if (session.state.phase !== 'connected') return;
        const select = $('call-output'); select.replaceChildren();
        devices.forEach((device, i) => { const option = document.createElement('option'); option.value = device.deviceId; option.textContent = device.label || `Audio output ${i + 1}`; select.append(option); });
        show('call-output-control', devices.length > 1);
        if (select.selectedOptions[0]) text('call-output-name', select.selectedOptions[0].textContent);
      }).catch(() => show('call-output-control', false));
    }
    text('call-footnote', ['connected', 'reconnecting'].includes(state.phase) && $('call-output-control').hidden ? 'Audio output is managed by your device' : 'One-to-one voice call');
    updateClock(); positionSurfaces();
    if (ended && !full && outcomeToastEpoch !== session.epoch) {
      const epoch = outcomeToastEpoch = session.epoch;
      notify(status + (state.connectedAt != null ? ` · ${formatDuration(duration(state))}` : ''));
      setTimeout(() => { if (session.epoch === epoch && session.state.phase === 'ended') session.dismiss(); }, 3000);
    }
    if (incoming && !document.hidden) session.acknowledge();
  }
  function updateClock() {
    const state = session.state, time = formatDuration(duration(state));
    show('call-timer', state.connectedAt != null);
    text('call-timer', time);
    text('call-pill-status', state.phase === 'connected' ? time : `${labels[state.phase] || ''}${state.connectedAt != null ? ` · ${time}` : ''}`);
  }
  function positionSurfaces() {
    const viewport = window.visualViewport;
    const header = document.querySelector('.chat-header') || document.querySelector('#navbar-top');
    const rect = header?.getBoundingClientRect();
    const top = Math.max(12 + (viewport?.offsetTop ?? 0), (rect?.height ? rect.bottom : 56) + 10);
    root.style.setProperty('--call-banner-top', `${top}px`);
    root.style.setProperty('--call-pill-top', `${top}px`);
  }
  function notify(message) { showNotification(message, 'info'); }
  async function answer() {
    if (await ownership()) session.answer();
    else notify('A call is already open in another tab. Continue there.');
  }
  function navigate(url) {
    const target = new URL(url, location.origin);
    const conversation = target.pathname.match(/^\/messaging\/conversation\/(\d+)\/$/);
    const sameRoute = target.pathname + target.search === location.pathname + location.search;
    // Closing a call already reveals the chat below it. Replacing that same
    // document reruns chat initialization and can disturb its layout/composer.
    if (sameRoute && (!conversation || document.getElementById('chatMainArea')?.dataset.conversationId === conversation[1])) return true;
    if (!window.htmx || !document.getElementById('page-content-target')) return false;
    window.htmx.ajax('GET', target.pathname + target.search, { target: '#page-content-target', swap: 'innerHTML', headers: { 'HX-Request': 'true', 'HX-Target': 'page-content-target' } });
    // The shell commits history after a successful swap, including redirects.
    return true;
  }
  root.addEventListener('click', async event => {
    const button = event.target.closest('[data-call-action]');
    if (!button || button.disabled) return;
    event.preventDefault(); event.stopPropagation();
    const action = button.dataset.callAction;
    if (action === 'minimize') { if (session.state.phase === 'ended') session.dismiss(); else session.minimize(); }
    if (action === 'restore') session.restore();
    if (action === 'answer') answer();
    if (action === 'decline') session.decline();
    if (action === 'end') session.end();
    if (action === 'mute') session.mute();
    if (action === 'speaker' && !button.disabled) {
      button.disabled = true;
      try { const result = await media.speaker(!session.state.speaker); session.update({ speaker: result.speaker, output: result.output || 'default' }); }
      catch (error) { notify(error.message || 'Use your device audio routing settings.'); }
      finally { render(session.state); }
    }
    if (action === 'expand') {
      const expanded = screen.classList.toggle('is-expanded');
      button.setAttribute('aria-pressed', String(expanded));
      button.setAttribute('aria-label', expanded ? 'Collapse call panel' : 'Expand call panel');
      screen.style.removeProperty('width'); screen.style.removeProperty('height');
      clampPanel();
    }
    if (action === 'permission') session.retryPermission();
    if (action === 'permission-back') {
      const id = session.state.conversationId;
      if (session.state.call?.status === 'active') await session.end();
      else if (session.state.call) await session.decline();
      else session.endLocal('cancelled');
      if (!session.live) { session.dismiss(); if (id) navigate(`/messaging/conversation/${id}/`); }
    }
    if (action === 'playback') { try { await media.playback(); session.update({ playbackBlocked: false }); } catch (_) { notify('Audio could not start. Check your device audio settings.'); } }
    if (action === 'retry') {
      const { conversationId, peer } = session.state;
      if (await ownership()) session.start(conversationId, peer);
      else notify('A call is already open in another tab.');
    }
    if (action === 'chat') { const id = session.state.conversationId; session.dismiss(); if (id) navigate(`/messaging/conversation/${id}/`); }
  });
  $('call-output').addEventListener('change', async event => {
    const previous = session.state.output;
    try { await media.output(event.target.value); session.update({ output: event.target.value }); text('call-output-name', event.target.selectedOptions[0].textContent); }
    catch (_) { event.target.value = previous; notify('This device could not switch audio output. Use its audio settings.'); }
  });
  screen.addEventListener('cancel', event => { event.preventDefault(); if (session.state.phase === 'ended') session.dismiss(); else session.minimize(); });
  document.addEventListener('click', event => {
    if (!session.live || event.defaultPrevented || event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
    const link = event.target.closest('a[href]');
    if (!link || link.target === '_blank' || link.hasAttribute('download')) return;
    const url = new URL(link.href, location.origin);
    if (url.origin !== location.origin || url.hash || !/^\/(?:messaging(?:\/|$)|groups(?:\/|$)|posts?(?:\/|$)|courses(?:\/|$)|documents(?:\/|$)|notifications(?:\/|$)|search(?:\/|$)|users\/profile(?:\/|$)|$)/.test(url.pathname)) return;
    if (link.hasAttribute('data-bs-toggle') || (link.hasAttribute('hx-target') && link.getAttribute('hx-target') !== '#page-content-target')) return;
    if (navigate(url.pathname + url.search)) { event.preventDefault(); event.stopImmediatePropagation(); }
  }, true);
  let starting = false;
  document.addEventListener('click', async event => {
    const button = event.target.closest('[data-start-voice-call]');
    if (!button) return;
    event.preventDefault(); event.stopImmediatePropagation();
    if (starting) return;
    if (session.live) { session.restore(); return; }
    starting = true; button.disabled = true;
    try {
      if (!(await ownership())) { notify('A call is already open in another tab. Continue there.'); return; }
      await session.start(Number(button.dataset.conversationId), { name: button.dataset.callName, avatar: button.dataset.callAvatar });
    } finally { starting = false; button.disabled = false; }
  }, true);
  async function refresh() {
    if (refreshInFlight || document.hidden || session.pending) return;
    refreshInFlight = true;
    try {
      if (session.live && session.state.call) { session.apply(await api.get(session.state.call.id)); if (session.state.phase === 'incoming') session.acknowledge(); }
      else if (session.state.phase === 'idle') {
        if (savedHint) {
          const hint = savedHint;
          const call = await api.get(hint.id);
          if (!['active', 'inviting'].includes(call.status)) { savedHint = null; recovering = false; saveRecovery(); releaseOwnership(); return; }
          if (await ownership()) {
            savedHint = null; session.restoreSaved(call, hint);
            if (hint.panel && desktop.matches) {
              screen.classList.toggle('is-expanded', !!hint.panel.expanded);
              const expand = root.querySelector('[data-call-action="expand"]');
              expand.setAttribute('aria-pressed', String(!!hint.panel.expanded));
              expand.setAttribute('aria-label', hint.panel.expanded ? 'Collapse call panel' : 'Expand call panel');
              screen.style.width = `${Math.max(340, Math.min(680, hint.panel.width))}px`;
              screen.style.height = `${Math.max(190, Math.min(520, hint.panel.height))}px`;
              clampPanel(hint.panel.x, hint.panel.y);
            }
            recovering = false; saveRecovery();
          }
          else savedHint = hint;
          return;
        }
        recovering = false;
        const { calls } = await request();
        const call = calls[0];
        if (!call) return;
        if (call.status === 'active' || String(call.caller_id) === root.dataset.userId) {
          if (await ownership()) session.apply(call, { recover: true });
        } else session.apply(call);
      }
    } catch (error) {
      if ([401, 403, 404].includes(error.status)) { savedHint = null; recovering = false; saveRecovery(); if (session.live) session.endLocal('ended'); }
    } finally { refreshInFlight = false; }
  }
  window.addEventListener('pwani:call-update', async event => {
    const call = event.detail;
    if (call?.status === 'active' && session.state.call?.id === call.id && !(await ownership())) { session.endLocal('other_session'); return; }
    session.apply(call);
  });
  window.addEventListener('pwani:socket-ready', refresh);
  window.addEventListener('online', refresh);
  navigator.mediaDevices?.addEventListener('devicechange', () => { outputsKey = ''; render(session.state); });
  document.addEventListener('visibilitychange', () => { if (!document.hidden) { refresh(); if (session.state.phase === 'incoming') session.acknowledge(); } });
  window.addEventListener('resize', positionSurfaces);
  window.visualViewport?.addEventListener('resize', positionSurfaces);
  window.visualViewport?.addEventListener('scroll', positionSurfaces);
  document.body.addEventListener('htmx:afterSwap', () => render(session.state));
  document.body.addEventListener('htmx:historyRestore', () => render(session.state));
  window.addEventListener('pagehide', () => { recovering = false; saveRecovery(); alerts.stop(); media.close(); releaseOwnership(); });
  window.addEventListener('pageshow', event => { if (event.persisted) location.reload(); });
  setInterval(() => { session.tick(); updateClock(); positionSurfaces(); }, 1000);
  setInterval(refresh, 5000); // Reconcile missed socket hints and backend expiry.
  setInterval(() => { if (ownsTab && !navigator.locks) { try { localStorage.setItem(leaseKey, JSON.stringify({ id: ownerId, expires: Date.now() + 15000 })); } catch (_) {} } }, 5000);
  window.PwaniCalls = Object.freeze({ hasSession: () => session.live, navigate, minimize: () => session.minimize() });
  let drag = null;
  const handle = root.querySelector('.call-drag-handle');
  function clampPanel(x, y) {
    if (!desktop.matches) return;
    const rect = screen.getBoundingClientRect();
    if (!rect.width) return;
    x = Math.max(8, Math.min(x ?? rect.left, innerWidth - rect.width - 8));
    y = Math.max(8, Math.min(y ?? rect.top, innerHeight - rect.height - 8));
    root.style.setProperty('--call-panel-x', `${x}px`); root.style.setProperty('--call-panel-y', `${y}px`); root.style.setProperty('--call-panel-bottom', 'auto');
  }
  handle.addEventListener('pointerdown', event => {
    if (!desktop.matches) return;
    const rect = screen.getBoundingClientRect(); drag = { x: event.clientX - rect.left, y: event.clientY - rect.top };
    handle.setPointerCapture(event.pointerId); event.preventDefault();
  });
  handle.addEventListener('pointermove', event => { if (drag) clampPanel(event.clientX - drag.x, event.clientY - drag.y); });
  handle.addEventListener('pointerup', () => { drag = null; saveRecovery(); });
  handle.addEventListener('pointercancel', () => { drag = null; saveRecovery(); });
  handle.addEventListener('keydown', event => {
    const delta = { ArrowLeft: [-20, 0], ArrowRight: [20, 0], ArrowUp: [0, -20], ArrowDown: [0, 20] }[event.key];
    if (delta) { event.preventDefault(); const rect = screen.getBoundingClientRect(); clampPanel(rect.left + delta[0], rect.top + delta[1]); saveRecovery(); }
  });
  new ResizeObserver(() => { if (desktop.matches && screen.open) { clampPanel(); saveRecovery(); } }).observe(screen);
  window.addEventListener('resize', () => clampPanel());
  desktop.addEventListener('change', () => { if (!desktop.matches) { screen.style.removeProperty('width'); screen.style.removeProperty('height'); } render(session.state); });
  render(session.state); refresh();
}
