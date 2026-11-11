/**
 * Spark Lucide icon helpers.
 * Self-hosted Lucide (static/vendor/lucide/lucide.min.js) is only loaded on
 * Spark surfaces; every helper no-ops when window.lucide is absent so this
 * file is safe to load site-wide.
 */
(function () {
    'use strict';

    // Bootstrap Icons -> Lucide name mapping for dynamically supplied icon
    // values (context resources, study-session metadata, engine fallbacks).
    const SPARK_BI_ICON_MAP = {
        'arrow-clockwise': 'rotate-cw', 'arrow-left': 'arrow-left', 'arrow-repeat': 'refresh-cw',
        'arrow-up': 'arrow-up', 'arrow-up-right': 'arrow-up-right', 'bell': 'bell',
        'book': 'book-open', 'book-half': 'book-open', 'bookmark': 'bookmark',
        'bookmark-check-fill': 'bookmark-check', 'bookmark-fill': 'bookmark',
        'bookmark-plus': 'bookmark-plus', 'box-arrow-left': 'arrow-left',
        'box-arrow-right': 'log-out', 'box-arrow-up-right': 'external-link', 'box-seam': 'package',
        'camera-fill': 'camera', 'chat-dots': 'message-circle', 'check-lg': 'check', 'check2': 'check',
        'check2-circle': 'circle-check', 'check-circle-fill': 'circle-check-big',
        'chevron-down': 'chevron-down', 'chevron-up': 'chevron-up', 'clipboard': 'copy',
        'code-slash': 'code', 'collection': 'library', 'collection-fill': 'library',
        'compass': 'compass', 'compass-fill': 'compass', 'cpu': 'cpu', 'download': 'download',
        'exclamation-circle': 'circle-alert', 'exclamation-octagon-fill': 'octagon-alert',
        'exclamation-triangle-fill': 'triangle-alert', 'eye': 'eye', 'eye-slash': 'eye-off',
        'file-earmark': 'file', 'file-earmark-arrow-down': 'file-down',
        'file-earmark-arrow-up': 'file-up', 'file-earmark-check': 'file-check',
        'file-earmark-pdf-fill': 'file-text', 'file-earmark-richtext': 'file-text',
        'file-earmark-text': 'file-text', 'file-earmark-text-fill': 'file-text',
        'filetype-docx': 'file-text', 'filetype-pdf': 'file-text', 'flag-fill': 'flag',
        'folder-fill': 'folder', 'folder2': 'folder', 'gear': 'settings', 'gear-fill': 'settings',
        'hand-thumbs-down': 'thumbs-down', 'hand-thumbs-up': 'thumbs-up',
        'hourglass-split': 'hourglass', 'image': 'image', 'image-fill': 'image',
        'info-circle': 'info', 'journal-arrow-down': 'book-down',
        'journal-bookmark-fill': 'book-marked', 'journal-check': 'book-open-check',
        'journal-richtext': 'notebook-text', 'journal-text': 'notebook-text', 'journal-x': 'book-x',
        'laptop': 'laptop', 'layout-sidebar': 'panel-left',
        'layout-sidebar-inset': 'panel-right-open',
        'layout-sidebar-inset-reverse': 'panel-right-close',
        'layout-sidebar-reverse': 'panel-right', 'lightbulb': 'lightbulb',
        'lightning-charge': 'zap', 'link-45deg': 'link', 'list': 'list',
        'list-check': 'list-checks', 'mic-fill': 'mic', 'moon-stars-fill': 'moon-star',
        'mortarboard': 'graduation-cap', 'mortarboard-fill': 'graduation-cap',
        'paperclip': 'paperclip', 'patch-check-fill': 'badge-check', 'patch-question': 'circle-help',
        'pause-fill': 'pause', 'people': 'users', 'people-fill': 'users', 'pencil': 'pencil',
        'person': 'user', 'person-check': 'user-check', 'person-circle': 'circle-user',
        'person-lines-fill': 'user', 'person-vcard-fill': 'contact', 'phone': 'phone',
        'play-btn-fill': 'circle-play', 'play-circle-fill': 'circle-play', 'play-fill': 'play',
        'plug-fill': 'plug', 'plus-circle': 'circle-plus', 'plus-lg': 'plus',
        'question-circle': 'circle-help', 'quote': 'quote', 'search': 'search', 'send': 'send',
        'shield-check': 'shield-check', 'signpost-split': 'signpost', 'signpost': 'signpost',
        'sliders': 'sliders-horizontal', 'speedometer2': 'gauge',
        'star': 'star', 'stars': 'sparkles', 'stop-circle': 'circle-stop',
        'stop-circle-fill': 'circle-stop', 'stop-fill': 'square', 'sun-fill': 'sun',
        'terminal': 'square-terminal', 'trash': 'trash-2', 'trash3': 'trash-2',
        'volume-up-fill': 'volume-2', 'x': 'x', 'x-lg': 'x',
        'arrows-fullscreen': 'maximize', 'chat-square-text': 'message-square-text',
        'chat-square-text-fill': 'message-square-text', 'chat-quote-fill': 'message-square-quote',
        'circle': 'circle', 'file-earmark-pdf': 'file-text',
    };
    const LUCIDE_NAMES = new Set(Object.values(SPARK_BI_ICON_MAP));

    // Full set of icon names the bundled Lucide build actually ships, derived
    // from window.lucide.icons (PascalCase keys -> kebab-case). sparkSetIcon
    // must accept every real Lucide name — semantic activity icons like
    // 'globe' or 'brain' are not in the Bootstrap map and previously fell
    // through to the 'file' default.
    let KNOWN_LUCIDE_NAMES = null;
    function knownLucideNames() {
        if (KNOWN_LUCIDE_NAMES) return KNOWN_LUCIDE_NAMES;
        KNOWN_LUCIDE_NAMES = new Set(LUCIDE_NAMES);
        // Semantic activity/status icons used by the Spark trail — seeded
        // explicitly so digit-bearing aliases like 'check-circle-2' always
        // resolve even if the bundle's PascalCase keys normalize differently.
        [
            'brain', 'list-checks', 'globe', 'book-open', 'book-search',
            'search', 'wrench', 'clipboard-check', 'shield-check',
            'message-circle-question', 'file-pen-line', 'refresh-cw',
            'circle-dot', 'check-circle-2', 'circle-alert', 'circle-x',
            'ban', 'sparkles', 'file',
        ].forEach(n => KNOWN_LUCIDE_NAMES.add(n));
        try {
            const icons = window.lucide && window.lucide.icons;
            if (icons && typeof icons === 'object') {
                for (const key of Object.keys(icons)) {
                    const kebab = String(key).replace(/([a-z0-9])([A-Z])/g, '$1-$2').toLowerCase();
                    KNOWN_LUCIDE_NAMES.add(kebab);
                    KNOWN_LUCIDE_NAMES.add(kebab.replace(/([a-z])(-?\d)/g, '$1-$2'));
                }
            }
        } catch (_) { /* fall back to the seeded set */ }
        return KNOWN_LUCIDE_NAMES;
    }

    // Accepts a legacy "bi-*" class string (optionally with extra utility
    // classes) or a bare Lucide name; returns { name, extra }.
    function sparkIconSpec(value) {
        const tokens = String(value || '').trim().split(/\s+/).filter(Boolean);
        let name = 'file';
        const extra = [];
        const known = knownLucideNames();
        for (const token of tokens) {
            if (token === 'bi' || token === 'bi-*') continue;
            if (token.startsWith('bi-')) {
                const key = token.slice(3);
                name = SPARK_BI_ICON_MAP[key] || key;
            } else if (known.has(token)) {
                name = token;
            } else {
                extra.push(token);
            }
        }
        return { name, extra: extra.join(' ') };
    }

    function sparkRenderIcons(root) {
        if (window.lucide && typeof window.lucide.createIcons === 'function') {
            try { window.lucide.createIcons({ root: root || document }); } catch (e) { /* noop */ }
        }
    }

    // Replace an icon element (<i data-lucide> placeholder or rendered <svg>)
    // with a fresh placeholder for `value` and render it immediately.
    function sparkSetIcon(el, value) {
        if (!el || !el.parentNode) return null;
        const spec = sparkIconSpec(value);
        const parent = el.parentNode;
        const i = document.createElement('i');
        i.setAttribute('data-spark-icon-swap', '');
        // Carry over every attribute (id, data-*, aria-*, title, style) so
        // delegated handlers and styling survive the swap.
        for (const attr of el.attributes) {
            if (attr.name === 'class' || attr.name === 'data-lucide' || attr.name === 'data-spark-icon-swap') continue;
            i.setAttribute(attr.name, attr.value);
        }
        i.setAttribute('data-lucide', spec.name);
        const prevClass = el.getAttribute('class') || '';
        const keep = prevClass.split(/\s+/).filter(c =>
            c && c !== 'bi' && !c.startsWith('bi-') && !c.startsWith('lucide'));
        const all = [...keep, ...spec.extra.split(/\s+/).filter(Boolean)];
        if (all.length) i.setAttribute('class', [...new Set(all)].join(' '));
        el.replaceWith(i);
        sparkRenderIcons(parent);
        const rendered = parent.querySelector('[data-spark-icon-swap]');
        if (rendered) rendered.removeAttribute('data-spark-icon-swap');
        return rendered || null;
    }

    window.sparkIconSpec = sparkIconSpec;
    window.sparkRenderIcons = sparkRenderIcons;
    window.sparkSetIcon = sparkSetIcon;

    // Lifecycle: initial render, HTMX swaps, and a MutationObserver safety net
    // for JS-injected markup (modals, dropdowns, staged media). Only active
    // when the Lucide bundle is present; createIcons is idempotent.
    function sparkIconsReady() {
        return Boolean(window.lucide && typeof window.lucide.createIcons === 'function');
    }

    function attachSparkIconLifecycle() {
        if (!sparkIconsReady() || window._pwanimateIconLifecycleAttached) return;
        window._pwanimateIconLifecycleAttached = true;

        sparkRenderIcons(document);

        if (document.body) {
            document.body.addEventListener('htmx:afterSwap', function (evt) {
                if (evt.detail && evt.detail.target) sparkRenderIcons(evt.detail.target);
            });
        }

        let iconScanQueued = false;
        const iconScanQueue = new Set();
        const flushIconScans = () => {
            iconScanQueued = false;
            iconScanQueue.forEach((node) => {
                if (!node.isConnected) return;
                if (node.matches && node.matches('[data-lucide]')) {
                    sparkRenderIcons(node.parentNode || document);
                } else if (node.querySelector && node.querySelector('[data-lucide]')) {
                    sparkRenderIcons(node);
                }
            });
            iconScanQueue.clear();
        };
        new MutationObserver((mutations) => {
            for (const mutation of mutations) {
                mutation.addedNodes.forEach((node) => {
                    if (node.nodeType === 1) iconScanQueue.add(node);
                });
            }
            if (iconScanQueue.size && !iconScanQueued) {
                iconScanQueued = true;
                requestAnimationFrame(flushIconScans);
            }
        }).observe(document.documentElement, { childList: true, subtree: true });
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', attachSparkIconLifecycle);
    } else {
        attachSparkIconLifecycle();
    }
})();
