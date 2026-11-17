import re

path = 'templates/base.html'
with open(path, encoding='utf-8') as f:
    src = f.read()

# Locate the extra_js block: {% block extra_js %} ... {% endblock %}
start_marker = '{% block extra_js %}'
s = src.find(start_marker)
assert s != -1, 'extra_js block not found'

# Find the matching endblock: the first {% endblock %} after s
e = src.find('{% endblock %}', s)
assert e != -1, 'extra_js endblock not found'

# Cut old block (exclusive of {% endblock %}).
old_block = src[s:e + len('{% endblock %}')]

print('OLD block begins:', repr(old_block[:80]))
print('OLD block length:', len(old_block))

# New content for the extra_js block:
# 1. auto-refresh script
# 2. service worker script
# 3. PWA + online/offline script
# 4. shared Lucide loader (single, properly closed)
new_block = r'''{% block extra_js %}
<script>
// Auto-refresh functionality for user actions
function refreshPage() {
    window.location.reload();
}

// Listen for custom refresh trigger
document.addEventListener('htmx:afterSwap', function(evt) {
    if (evt.detail.trigger === 'refreshPage') {
        setTimeout(() => {
            refreshPage();
        }, 100);
    }
});

// Play like sound on successful HTMX like requests
document.addEventListener('htmx:afterRequest', async function(evt) {
    try {
        // Check if request was successful (HTTP 2xx status)
        if (!evt.detail.successful) {
            return;
        }

        // Check if this is a like toggle request
        const xhr = evt.detail.xhr;
        const requestUrl = xhr.responseURL || '';

        // Match like endpoint URLs (both post like and comment like)
        if (requestUrl.includes('/like/') || requestUrl.includes('/toggle_like')) {
            // Play like sound (async for mobile context resume)
            if (window.ReactionSound && window.ReactionSound.playLike) {
                await window.ReactionSound.playLike();
            }
        }
    } catch (e) {
        // Silently fail - never break HTMX functionality
        console.warn('[ReactionSound] Error in HTMX event handler:', e);
    }
});
</script>

<!-- Service Worker Registration -->
<script>
// Service Worker registration is now handled by update-manager.js
// The update-manager.js is the single source of truth for service worker lifecycle
// This script is kept for backward compatibility but does nothing
console.log('[ServiceWorker] Service worker registration delegated to update-manager.js');
</script>
<script>
// Listen for beforeinstallprompt event
window.addEventListener('beforeinstallprompt', (event) => {
    console.log('[PWA] beforeinstallprompt event fired - install prompt is available');
    // Prevent the default browser prompt
    event.preventDefault();
    // Store the event for later use
    window.deferredPrompt = event;
    // Show your custom install button here if needed
});

// Listen for appinstalled event
window.addEventListener('appinstalled', (event) => {
    console.log('[PWA] App was installed');
    window.deferredPrompt = null;
});

// Handle offline/online events
window.addEventListener('online', function() {
    console.log('App is now online');
    // Hide any offline indicators
    const offlineBanner = document.getElementById('offline-banner');
    if (offlineBanner) {
        offlineBanner.style.display = 'none';
    }
});

window.addEventListener('offline', function() {
    console.log('App is now offline');
    // Show offline indication if needed
    console.log('You are offline. Some features may be limited.');
});

// PWA Install Prompt
let deferredPrompt;
window.addEventListener('beforeinstallprompt', (e) => {
    e.preventDefault();
    deferredPrompt = e;

    // Show install button (optional)
    const installButton = document.getElementById('install-button');
    if (installButton) {
        installButton.style.display = 'block';
        installButton.addEventListener('click', () => {
            deferredPrompt.prompt();
            deferredPrompt.userChoice.then((choiceResult) => {
                if (choiceResult.outcome === 'accepted') {
                    console.log('User accepted the install prompt');
                } else {
                    console.log('User dismissed the install prompt');
                }
                deferredPrompt = null;
            });
        });
    }
});
</script>
<script>
(function () {
  'use strict';

  // Single-load guard: this block runs exactly once per page.
  if (window.__vastoIconsLoaderDone) { return; }
  window.__vastoIconsLoaderDone = true;

  var boot = function () {
    // 1) Ensure the local Lucide vendor loads at most once per page.
    if (!window.lucide || typeof window.lucide.createIcons !== 'function') {
      var v = document.createElement('script');
      v.id = 'vasto-lucide-vendor';
      v.src = '{% static "vendor/lucide/lucide.min.js" %}';
      v.onload = loadRuntime;
      v.onerror = loadRuntime; // best-effort; runtime degrades gracefully
      var head = document.head || document.getElementsByTagName('head')[0];
      head.appendChild(v);
      return;
    }
    loadRuntime();
  };

  function loadRuntime() {
    var t = document.createElement('script');
    t.src = '{% static "js/vasto-icons.js" %}';
    t.onload = function () {
      if (window.VastiIcons && typeof window.VastiIcons.init === 'function') {
        window.VastiIcons.init();
      }
    };
    var head = document.head || document.getElementsByTagName('head')[0];
    head.appendChild(t);
  }

  // Run when the page has already passed the loading state, otherwise wait.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', boot);
  } else {
    boot();
  }
})();
</script>
{% endblock %}'''

new_src = src[:s] + new_block + src[e + len('{% endblock %}'):]

with open(path, 'w', encoding='utf-8') as f:
    f.write(new_src)

print('Wrote new base.html, new length:', len(new_src))

# Verify
check = open(path, encoding='utf-8').read()
print('extra_js block count:', check.count('{% block extra_js %}'))
print('endblock count:', check.count('{% endblock %}'))
print('loader guards:', check.count('Single-load guard: this block runs exactly once per page'))
print('loader IIFE count:', check.count('(function () {'))
print('loader </script> pairs:', check.count('})();\n</script>'))
