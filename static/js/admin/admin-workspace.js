/*
  Admin command center — workspace interactions + hard workspace boundary.

  Loaded from <head> of base.html WITHOUT defer, on every page: its capture
  phase click listener has to register before base.html's inline boundary and
  progressive-enhancement handlers (both attach at parse time), so this file
  must execute during head parsing.

  Boundary rule (mirrors the vasto <-> Spark rule in base.html):
    entering or leaving /dashboard/ is always a native full-page navigation.
  That keeps the admin topbar/rails/stylesheet scoped to the workspace —
  they can never leak onto a normal page, and a normal chrome can never be
  swapped underneath the workspace.

  Everything else is Spark-style rail mechanics: persisted left-rail
  collapse, context rail closed by default, draggable up to an equal
  50/50 split with the centre panel (480px centre floor).
*/
(function () {
  'use strict';

  var LS_LEFT = 'adw_left_collapsed';
  var LS_CTX_OPEN = 'adw_ctx_open';
  var LS_CTX_WIDTH = 'adw_ctx_width';

  var MIN_CTX = 280;      /* px — narrowest useful ops rail */
  var MIN_CENTER = 480;   /* px — centre panel never shrinks below this */
  var STEP = 24;          /* px — keyboard resize increment */

  function isMobile() { return window.innerWidth < 992; }

  function store(key, value) {
    try { localStorage.setItem(key, value); } catch (e) { /* private mode */ }
  }

  function read(key) {
    try { return localStorage.getItem(key); } catch (e) { return null; }
  }

  /* ── Workspace detection ────────────────────────────────────────────── */

  // The two user-facing feedback routes render normal pages, not workspace
  // pages, even though they live under /dashboard/.
  function isAdminPath(pathname) {
    if (pathname !== '/dashboard' && pathname.indexOf('/dashboard/') !== 0) return false;
    if (pathname.indexOf('/dashboard/feedback/user/') === 0) return false;
    if (pathname.indexOf('/dashboard/feedback/submit/') === 0) return false;
    return true;
  }

  function bodyInWorkspace() {
    return document.body && document.body.classList.contains('adw-active');
  }

  function responseHasWorkspace(xhr) {
    var text = (xhr && xhr.responseText) || '';
    if (!text) return null; // no body to judge (history restore etc.)
    try {
      return !!new DOMParser().parseFromString(text, 'text/html')
        .querySelector('.admin-workspace');
    } catch (e) {
      return null;
    }
  }

  /* ── Boundary: clicks ───────────────────────────────────────────────── */
  document.addEventListener('click', function (evt) {
    if (evt.button !== 0 || evt.ctrlKey || evt.metaKey || evt.shiftKey || evt.altKey) return;
    if (evt.defaultPrevented) return;

    var target = evt.target;
    var link = target && target.closest ? target.closest('a') : null;
    if (!link) return;

    // Native by definition: new tab, downloads, modals, bypass marks.
    if (link.hasAttribute('download') ||
        link.getAttribute('target') === '_blank' ||
        link.hasAttribute('data-bs-toggle') ||
        link.getAttribute('hx-boost') === 'false' ||
        link.hasAttribute('data-bypass-htmx')) {
      return;
    }

    var rawHref = link.getAttribute('href');
    if (!rawHref || rawHref.charAt(0) === '#' ||
        rawHref.indexOf('javascript:') === 0 ||
        rawHref.indexOf('mailto:') === 0 ||
        rawHref.indexOf('tel:') === 0) {
      return;
    }

    var targetUrl;
    try { targetUrl = new URL(link.href, window.location.origin); } catch (e) { return; }
    if (targetUrl.origin !== window.location.origin) return;

    var crosses = isAdminPath(targetUrl.pathname) !== bodyInWorkspace();
    if (!crosses) return;

    evt.preventDefault();
    evt.stopPropagation();
    evt.stopImmediatePropagation();
    window.location.assign(targetUrl.href);
  }, true); // capture: beats HTMX and base.html's own handlers

  /* ── Boundary: programmatic swaps (htmx.ajax, forms, history) ───────── */
  document.addEventListener('htmx:beforeSwap', function (evt) {
    var detail = evt && evt.detail;
    if (!detail || !detail.target || detail.target.id !== 'page-content-target') return;

    var incoming = responseHasWorkspace(detail.xhr);
    if (incoming === null) return;
    var current = bodyInWorkspace();
    if (incoming === current) return;

    detail.shouldSwap = false;
    var destination = (detail.xhr && detail.xhr.responseURL) || window.location.href;
    window.location.assign(destination);
  });

  /* ── Rail state application ─────────────────────────────────────────── */
  function setCssWidth(px) {
    var value = Math.round(px) + 'px';
    document.documentElement.style.setProperty('--adw-ctx-width', value);
    document.body.style.setProperty('--adw-ctx-width', value);
  }

  function savedWidth() {
    var raw = parseInt(read(LS_CTX_WIDTH) || '', 10);
    return raw > 0 ? raw : 0;
  }

  function ctxLimits() {
    var width = document.documentElement.clientWidth;
    var leftW = document.body.classList.contains('adw-left-collapsed') ? 56 : 244;
    var max = Math.max(MIN_CTX, width - leftW - MIN_CENTER);
    var min = Math.min(MIN_CTX, max);
    var split = Math.floor((width - leftW) / 2); // equal split with the centre
    split = Math.max(min, Math.min(split, max));
    return { min: min, max: max, split: split };
  }

  function syncCtxUi(open) {
    var btn = document.getElementById('adwCtxToggleBtn');
    if (btn) btn.classList.toggle('is-on', open);
    if (btn) btn.setAttribute('aria-pressed', open ? 'true' : 'false');
    var resizer = document.getElementById('adwRailResizer');
    if (resizer) {
      var limits = ctxLimits();
      resizer.setAttribute('aria-valuemin', String(limits.min));
      resizer.setAttribute('aria-valuemax', String(limits.max));
      resizer.setAttribute('aria-valuenow', String(Math.round(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--adw-ctx-width')) || 0)));
    }
  }

  function applyPersistedState() {
    if (!document.body || !document.body.classList.contains('adw-active')) return;
    var collapsed = read(LS_LEFT) === '1';
    document.documentElement.classList.toggle('adw-left-collapsed', collapsed);
    document.body.classList.toggle('adw-left-collapsed', collapsed);

    var open = read(LS_CTX_OPEN) === '1';
    document.body.classList.toggle('adw-ctx-open', open);
    setCssWidth(open ? (savedWidth() || ctxLimits().split) : 0);
    syncCtxUi(open);
  }

  /* ── Public controls (used by inline onclick in the rail/topbar) ────── */
  window.adwToggleLeft = function () {
    var collapsed = !document.body.classList.contains('adw-left-collapsed');
    document.documentElement.classList.toggle('adw-left-collapsed', collapsed);
    document.body.classList.toggle('adw-left-collapsed', collapsed);
    store(LS_LEFT, collapsed ? '1' : '0');
    // The equal-split target moves with the left rail width.
    if (document.body.classList.contains('adw-ctx-open') && !savedWidth()) {
      setCssWidth(ctxLimits().split);
    }
    syncCtxUi(document.body.classList.contains('adw-ctx-open'));
  };

  window.adwToggleCtx = function (force) {
    var open = (typeof force === 'boolean')
      ? force
      : !document.body.classList.contains('adw-ctx-open');
    if (open && !isMobile()) {
      var width = savedWidth() || ctxLimits().split;
      setCssWidth(Math.max(ctxLimits().min, Math.min(width, ctxLimits().max)));
    } else if (!open) {
      setCssWidth(0);
    }
    document.body.classList.toggle('adw-ctx-open', open);
    store(LS_CTX_OPEN, open ? '1' : '0');
    syncCtxUi(open);
  };

  window.adwToggleNav = function (force) {
    var open = (typeof force === 'boolean')
      ? force
      : !document.body.classList.contains('adw-nav-open');
    document.body.classList.toggle('adw-nav-open', open);
  };

  window.adwThemeCycle = function () {
    var current = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    var next = current === 'dark' ? 'light' : 'dark';
    if (typeof window.setTheme === 'function') {
      window.setTheme(next);
    } else {
      document.documentElement.setAttribute('data-theme', next);
      try { localStorage.setItem('theme', next); } catch (e) {}
      document.dispatchEvent(new CustomEvent('themeChanged', { detail: { theme: next } }));
    }
    var icon = document.getElementById('adwThemeIcon');
    if (icon && window.VastoIcons) window.VastoIcons.setIcon(icon, next === 'dark' ? 'sun' : 'moon-star');
  };

  /* ── Context rail resizer ───────────────────────────────────────────── */
  var dragging = false;

  function widthFromClientX(clientX) {
    var width = document.documentElement.clientWidth;
    // The rail's right edge (the ctx column's left edge) is where the
    // resizer sits: ctx width = viewport width - that edge.
    var limits = ctxLimits();
    var value = width - clientX;
    return Math.max(limits.min, Math.min(value, limits.max)) || limits.split;
  }

  document.addEventListener('pointerdown', function (evt) {
    var handle = evt.target && evt.target.closest ? evt.target.closest('#adwRailResizer') : null;
    if (!handle || !document.body.classList.contains('adw-ctx-open')) return;
    evt.preventDefault();
    dragging = true;
    document.body.classList.add('adw-ctx-resizing');
    try { handle.setPointerCapture(evt.pointerId); } catch (e) {}
  });

  document.addEventListener('pointermove', function (evt) {
    if (!dragging) return;
    setCssWidth(widthFromClientX(evt.clientX));
  });

  function endDrag() {
    if (!dragging) return;
    dragging = false;
    document.body.classList.remove('adw-ctx-resizing');
    var value = Math.round(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--adw-ctx-width')) || 0);
    if (value > 0) store(LS_CTX_WIDTH, String(value));
  }

  document.addEventListener('pointerup', endDrag);
  document.addEventListener('pointercancel', endDrag);

  document.addEventListener('keydown', function (evt) {
    var handle = evt.target && evt.target.id === 'adwRailResizer' ? evt.target : null;
    if (!handle) return;
    var limits = ctxLimits();
    var current = Math.round(parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--adw-ctx-width')) || 0) || limits.split;
    var next = null;
    if (evt.key === 'ArrowLeft') next = current + STEP;      // rail grows leftwards
    else if (evt.key === 'ArrowRight') next = current - STEP;
    else if (evt.key === 'Home') next = limits.split;
    else if (evt.key === 'End') next = limits.max;
    else if (evt.key === 'Enter' || evt.key === ' ') { window.adwToggleCtx(); evt.preventDefault(); return; }
    if (next === null) return;
    evt.preventDefault();
    next = Math.max(limits.min, Math.min(next, limits.max));
    setCssWidth(next);
    store(LS_CTX_WIDTH, String(next));
    syncCtxUi(true);
  });

  document.addEventListener('dblclick', function (evt) {
    var handle = evt.target && evt.target.closest ? evt.target.closest('#adwRailResizer') : null;
    if (!handle) return;
    setCssWidth(ctxLimits().split);
    store(LS_CTX_WIDTH, String(ctxLimits().split));
  });

  /* ── HTMX lifecycle: state, title, drawer hygiene ───────────────────── */
  document.addEventListener('htmx:afterSwap', function (evt) {
    var detail = evt && evt.detail;
    if (!detail || !detail.target || detail.target.id !== 'page-content-target') return;

    var workspace = document.getElementById('admin-workspace');
    var inWorkspace = !!workspace;

    // Keep the shell class in sync with what actually got swapped in.
    document.documentElement.classList.toggle('adw-active', inWorkspace);
    document.body.classList.toggle('adw-active', inWorkspace);

    if (inWorkspace) {
      applyPersistedState();
      var title = workspace.getAttribute('data-admin-title');
      if (title) document.title = title + ' · Admin — vasto';
    }

    // Mobile drawers never stay open across a navigation.
    document.body.classList.remove('adw-nav-open');
  });

  /* ── Boot ───────────────────────────────────────────────────────────── */
  function boot() {
    if (document.body) applyPersistedState();
    var icon = document.getElementById('adwThemeIcon');
    if (icon && document.documentElement.getAttribute('data-theme') === 'dark') {
      if (window.VastoIcons) window.VastoIcons.setIcon(icon, 'sun');
    }
    if (window.matchMedia) {
      var mq = window.matchMedia('(max-width: 991.98px)');
      var onChange = function () { document.body.classList.remove('adw-nav-open'); };
      if (mq.addEventListener) mq.addEventListener('change', onChange);
      else if (mq.addListener) mq.addListener(onChange);
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
