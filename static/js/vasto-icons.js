/**
 * vasto-icons.js — shared, application-agnostic Lucide rendering for Vasto.
 *
 * Public API:
 *   window.VastoIcons = {
 *     ready(),         // true when the local Lucide vendor bundle is loaded
 *     render(root),    // render <i data-lucide> placeholders inside a container
 *     setIcon(el, name), // replace an <i data-lucide> (or rendered <svg>) with a named icon
 *     observe(root),   // start a scoped MutationObserver on a container
 *     disconnect(root),// stop the observer for a container
 *     init()           // initial document render + one native htmx event hook
 *   };
 *
 * Design notes:
 *  - Only the locally vendored asset static/vendor/lucide/lucide.min.js is used.
 *    No CDN, no external fetch, no network dependency for icon rendering.
 *  - Rendering is scoped to a supplied container. init() also installs ONE
 *    delegated document observer that renders [data-lucide] placeholders added
 *    later by non-htmx innerHTML injection; observe(root) remains available for
 *    containers that need a dedicated observer.
 *  - Rendering is idempotent at the container level: once a container is
 *    rendered and no new [data-lucide] nodes are added, repeated render() calls
 *    produce no new <svg class="lucide">.
 *  - Unknown or invalid icon names FAIL CLOSED: setIcon returns false and no
 *    partial/missing glyph is inserted. Invalid name is never injected as HTML.
 *  - No innerHTML / template injection of untrusted markup. Only <i data-lucide>
 *    placeholders + the library's createIcons() (library-managed SVG) are used.
 *  - Attributes (class, aria-*, data-*, title, style) are copied over when
 *    swapping an icon, so delegated handlers and styling survive.
 *  - Safe to load site-wide: every API is a no-op when window.lucide is absent.
 *  - Keeps Spark's existing lifecycle intact (sparkRenderIcons delegates here to avoid repeated SVG replacement); VastoIcons is generic infrastructure.
 */
(function (global) {
  'use strict';
  if (global.VastoIcons) return;

  var VastoIcons = {
    ready: isReady,
    render: render,
    setIcon: setIcon,
    observe: observe,
    disconnect: disconnect,
    init: init
  };

  function isReady() {
    return !!(global.lucide && typeof global.lucide.createIcons === 'function');
  }

  // Match this vendor's kebab/underscore/space -> PascalCase lookup exactly.
  function knownIcon(name) {
    if (typeof name !== 'string' || !global.lucide || !global.lucide.icons) return false;
    var key = name.replace(/^[\s_-]+|[\s_-]+$/g, '')
      .replace(/[-_\s]+(.)/g, function (_, c) { return c.toUpperCase(); });
    key = key.charAt(0).toUpperCase() + key.slice(1);
    return Object.prototype.hasOwnProperty.call(global.lucide.icons, key);
  }

  function toElement(value) {
    if (!value) return null;
    if (typeof value === 'string') {
      try { return global.document ? global.document.querySelector(value) : null; } catch (_) { return null; }
    }
    if (typeof value.nodeType === 'number') return value;
    return null;
  }

  function countSvgs(root) {
    var el = toElement(root);
    if (!el) return 0;
    try {
      return el.querySelectorAll('svg.lucide').length + (el.matches && el.matches('svg.lucide') ? 1 : 0);
    } catch (e) {
      return 0;
    }
  }

  // Render every <i data-lucide="…"> placeholder inside a container using the
  // local Lucide bundle. Idempotent per container: calling repeatedly never
  // produces duplicated <svg class="lucide">.
  function render(root) {
    if (!isReady()) return 0;
    var el = toElement(root);
    if (!el) return 0;
    var nodes = [];
    if (el.nodeType === 1 && el.matches('[data-lucide]')) nodes.push(el);
    if (el.querySelectorAll) nodes = nodes.concat(Array.from(el.querySelectorAll('[data-lucide]')));
    nodes.forEach(function (node) {
      // Lucide retains data-lucide on SVGs; never replace completed SVGs.
      if (node.namespaceURI === 'http://www.w3.org/2000/svg') return;
      var name = node.getAttribute('data-lucide');
      if (!knownIcon(name)) return;
      global.lucide.createIcons({ root: { querySelectorAll: function (selector) {
        return selector === '[data-lucide]' ? [node] : [];
      } } });
    });
    return countSvgs(el);
  }

  // Replace an icon element (<i data-lucide> placeholder or a rendered <svg>)
  // with a fresh <i data-lucide="name">, preserving all attributes, then render
  // that element. Fail closed on unknown names or missing elements.
  // Returns the rendered replacement. Direct listeners on the replaced element
  // cannot be preserved; attach listeners to a stable wrapper or delegate them.
  function setIcon(element, name) {
    if (typeof name !== 'string' || !name.trim()) return false;
    var el = toElement(element);
    if (!isReady() || !el || el.nodeType !== 1 || !el.parentNode) return false;
    if (!knownIcon(name)) return false; // unknown → fail closed

    var newNode = global.document.createElement('i');
    newNode.setAttribute('data-lucide', name.trim());

    // Preserve layout + accessibility + delegated-handler attributes.
    var keep = (el.getAttribute('class') || '').split(/\s+/).filter(function (c) {
      return c && c !== 'bi' && !c.startsWith('bi-') && !c.startsWith('lucide');
    });
    if (keep.length) newNode.setAttribute('class', keep.join(' '));

    var attrs = el.attributes;
    if (attrs) {
      for (var i = 0; i < attrs.length; i++) {
        var a = attrs[i];
        if (
          a.name === 'class' ||
          a.name === 'data-lucide' ||
          a.name === 'data-spark-icon-swap'
        ) {
          continue;
        }
        newNode.setAttribute(a.name, a.value);
      }
    }
    var token = String(++swapId);
    newNode.setAttribute('data-vasto-icon-swap', token);
    var parent = el.parentNode;
    parent.replaceChild(newNode, el);
    render(newNode);
    var rendered = parent.querySelector('[data-vasto-icon-swap="' + token + '"]');
    if (rendered) rendered.removeAttribute('data-vasto-icon-swap');
    return rendered;
  }

  // Start a single, scoped MutationObserver on a container. It only re-renders
  // when newly added nodes bring new [data-lucide] placeholders. Returns the
  // container reference (used as the token for disconnect). If an observer is
  // already running on this container, returns it unchanged (no duplicate).
  function observe(root) {
    if (!isReady() || !global.MutationObserver) return null;
    var el = toElement(root);
    if (!el) return null;

    // Idempotency: already observing this container?
    for (var i = 0; i < _observers.length; i++) {
      if (_observers[i].root === el) return el;
    }

    var observer = new global.MutationObserver(function (mutations) {
      var needsRender = false;
      for (var j = 0; j < mutations.length; j++) {
        var list = mutations[j].addedNodes;
        for (var k = 0; k < list.length; k++) {
          var n = list[k];
          if (n.nodeType !== 1) continue;
          if (n.matches && n.matches('[data-lucide]:not(svg)')) { needsRender = true; continue; }
          if (n.querySelector && n.querySelector('[data-lucide]:not(svg)')) { needsRender = true; }
        }
      }
      if (needsRender) render(el);
    });

    observer.observe(el, { childList: true, subtree: true });
    _observers.push({ root: el, observer: observer });
    return el;
  }

  // Stop and dispose the scoped observer for a container.
  function disconnect(root) {
    var el = toElement(root);
    if (!el) return false;
    for (var i = 0; i < _observers.length; i++) {
      if (_observers[i].root === el) {
        _observers[i].observer.disconnect();
        _observers.splice(i, 1);
        return true;
      }
    }
    return false;
  }

  // Bootstrap. Renders a root and installs a single scoped htmx:afterSwap hook
  // (re-renders only the swap target). Repeated calls render new placeholders
  // without registering additional listeners. No global observer is installed.
  function init(root) {
    if (!isReady()) return;
    var el = toElement(root) || global.document;
    if (!initialized) {
      var afterSwap = function (evt) {
        // OOB outerHTML swaps retain the removed node in detail.target.
        render(evt.type === 'htmx:oobAfterSwap' ? evt.target : (evt.detail && evt.detail.target || evt.target));
      };
      global.document.addEventListener('htmx:afterSwap', afterSwap);
      global.document.addEventListener('htmx:oobAfterSwap', afterSwap);
      initialized = true;
      installSweepObserver();
    }

    render(el);
    return el;
  }

  // One delegated observer for the whole document: any [data-lucide] node
  // injected later by plain innerHTML/insertAdjacentHTML is rendered without
  // each call site knowing about icons. SVG results are skipped by render(),
  // so the observer cannot retrigger itself.
  var sweepScheduled = false;
  function installSweepObserver() {
    if (!global.MutationObserver || global.document.__vastoIconSweep) return;
    global.document.__vastoIconSweep = true;
    var pending = [];
    var observer = new global.MutationObserver(function (mutations) {
      for (var i = 0; i < mutations.length; i++) {
        var added = mutations[i].addedNodes;
        for (var j = 0; j < added.length; j++) {
          var node = added[j];
          if (node.nodeType !== 1) continue;
          if (node.matches && node.matches('svg')) continue;
          if ((node.matches && node.matches('[data-lucide]')) ||
              (node.querySelector && node.querySelector('[data-lucide]'))) {
            pending.push(node);
          }
        }
      }
      if (!pending.length || sweepScheduled) return;
      sweepScheduled = true;
      global.setTimeout(function () {
        sweepScheduled = false;
        var batch = pending; pending = [];
        batch.forEach(function (node) { render(node); });
      }, 0);
    });
    observer.observe(global.document.documentElement, { childList: true, subtree: true });
  }

  // Container for all created observers, so they can be torn down cleanly.
  var _observers = [];
  var initialized = false;
  var swapId = 0;

  if (typeof module !== 'undefined' && module.exports) {
    // Node: expose for testability.
    module.exports = VastoIcons;
  }
  global.VastoIcons = VastoIcons;
  if (global.document) {
    if (global.document.readyState === 'loading') global.document.addEventListener('DOMContentLoaded', function () { init(); }, { once: true });
    else init();
  }
})(typeof window !== 'undefined' ? window : globalThis);
