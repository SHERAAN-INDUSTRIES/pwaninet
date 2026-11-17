/**
 * Post Content Manager
 * Handles text truncation and see more/see less functionality
 * 
 * Architecture:
 * - Truncates long post content (> 200 characters)
 * - Shows "See more" button for truncated content
 * - Expands inline on click
 * - Shows "See less" button when expanded
 * - Uses delegated event handling to guarantee no duplicate listeners and full HTMX compatibility
 */

(function() {
    'use strict';

    // Single delegated click listener on document handles all current and future see-more-btn clicks
    document.addEventListener('click', function(e) {
        const seeMoreBtn = e.target.closest('.see-more-btn');
        if (!seeMoreBtn) return;
        
        e.preventDefault();
        e.stopPropagation();
        
        const targetId = seeMoreBtn.dataset.targetId;
        const wrapper = seeMoreBtn.closest('.post-content-wrapper');
        const target = (targetId && document.getElementById(targetId)) ||
                       (wrapper && wrapper.querySelector('.post-text-clamp')) ||
                       (wrapper && wrapper.querySelector('.post-text-body'));
        if (!target) return;
        
        const isExpanded = target.classList.toggle('is-expanded');
        seeMoreBtn.setAttribute('aria-expanded', String(isExpanded));
        
        const moreText = seeMoreBtn.dataset.moreText || 'See more';
        const lessText = seeMoreBtn.dataset.lessText || 'See less';
        seeMoreBtn.textContent = isExpanded ? lessText : moreText;
        
        if (!isExpanded) {
            target.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    });

    // Dynamic detection to ensure visually overflowing posts always have a See More button
    function checkPostClamping(root) {
        const textElements = (root || document).querySelectorAll('.post-text-clamp');
        textElements.forEach(function(el) {
            if (el.classList.contains('is-expanded')) return;
            const wrapper = el.closest('.post-content-wrapper');
            if (!wrapper) return;
            
            let btn = wrapper.querySelector('.see-more-btn');
            // Detect if element's content exceeds its clamped height
            const isOverflowing = el.scrollHeight > (el.clientHeight + 4);
            
            if (isOverflowing) {
                if (!btn) {
                    btn = document.createElement('button');
                    btn.type = 'button';
                    btn.className = 'see-more-btn';
                    btn.dataset.targetId = el.id;
                    btn.dataset.moreText = 'See more';
                    btn.dataset.lessText = 'See less';
                    btn.setAttribute('aria-expanded', 'false');
                    btn.setAttribute('aria-controls', el.id);
                    btn.textContent = 'See more';
                    el.insertAdjacentElement('afterend', btn);
                } else {
                    btn.style.display = 'inline-flex';
                }
            }
        });
    }

    // Run on DOM ready, HTMX swaps, history restoration, and window resize
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { checkPostClamping(); });
    } else {
        checkPostClamping();
    }

    document.addEventListener('htmx:afterSwap', function(evt) {
        checkPostClamping(evt.detail.target || document);
    });

    document.addEventListener('htmx:historyRestore', function() {
        checkPostClamping(document);
    });

    let resizeTimer = null;
    window.addEventListener('resize', function() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(function() { checkPostClamping(document); }, 200);
    });

    // Public API
    window.vastoPostContent = {
        init: function() { checkPostClamping(document); },
        checkClamping: checkPostClamping
    };

})();

// Ensure all auto-linked URLs in posts are handled correctly for PWA/Capacitor
document.addEventListener('DOMContentLoaded', function() {
    // 1. Inject the External Link Warning Modal if it doesn't exist
    if (!document.getElementById('externalLinkModal')) {
        const modalHtml = `
        <div class="modal fade" id="externalLinkModal" tabindex="-1" aria-labelledby="externalLinkModalLabel" aria-hidden="true" style="z-index: 1060;">
            <div class="modal-dialog modal-dialog-centered">
                <div class="modal-content">
                    <div class="modal-header border-0 pb-0">
                        <h5 class="modal-title fw-bold" id="externalLinkModalLabel">External Link</h5>
                        <button type="button" class="btn-close" data-bs-dismiss="modal" aria-label="Close"></button>
                    </div>
                    <div class="modal-body text-center py-4">
                        <div id="externalLinkLogo" class="d-flex align-items-center justify-content-center mx-auto mb-3 rounded-circle bg-body-secondary" style="width: 76px; height: 76px;" aria-hidden="true">
                            <i aria-hidden="true" data-lucide="globe" class="vasto-icon text-primary" style="--vasto-icon-size: 2.25rem;"></i>
                        </div>
                        <p class="mb-2">You are about to leave vasto and visit an external website:</p>
                        <p class="mb-4"><strong id="externalLinkUrl" style="word-break: break-all; color: var(--bs-primary);"></strong></p>
                        <p class="text-muted small mb-0">Are you sure you want to proceed?</p>
                    </div>
                    <div class="modal-footer border-0 pt-0 d-flex justify-content-between">
                        <button type="button" class="btn btn-outline-secondary" onclick="copyExternalLink()">Copy Link</button>
                        <div>
                            <button type="button" class="btn btn-light" data-bs-dismiss="modal">Cancel</button>
                            <button type="button" class="btn btn-primary px-4" onclick="proceedToExternalLink()">Open</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>
        `;
        document.body.insertAdjacentHTML('beforeend', modalHtml);
    }

    let pendingExternalUrl = '';

    window.copyExternalLink = function() {
        if (pendingExternalUrl) {
            if (window.copyLinkToClipboard) {
                window.copyLinkToClipboard(pendingExternalUrl);
            } else if (navigator.clipboard) {
                navigator.clipboard.writeText(pendingExternalUrl);
                alert("Link copied!");
            }
        }
    };

    function updateExternalLinkLogo(url, link) {
        const logo = document.getElementById('externalLinkLogo');
        if (!logo) return;

        let hostname = '';
        try {
            hostname = new URL(url).hostname.toLowerCase().replace(/^www\./, '');
        } catch (_) {
            hostname = '';
        }

        const brands = [
            { domains: ['youtube.com', 'youtu.be'], icon: 'bi-youtube', color: 'text-danger' },
            { domains: ['tiktok.com'], icon: 'bi-tiktok', color: 'text-dark' },
            { domains: ['facebook.com', 'fb.watch'], icon: 'bi-facebook', color: 'text-primary' },
            { domains: ['instagram.com'], icon: 'bi-instagram', color: 'text-danger' },
            { domains: ['telegram.org', 't.me'], icon: 'bi-telegram', color: 'text-info' },
            { domains: ['x.com', 'twitter.com'], icon: 'bi-twitter-x', color: 'text-dark' },
            { domains: ['spotify.com'], icon: 'bi-spotify', color: 'text-success' }
        ];
        const brand = brands.find(item => item.domains.some(domain => hostname === domain || hostname.endsWith(`.${domain}`)));
        logo.replaceChildren();

        if (brand) {
            const icon = document.createElement('i');
            icon.className = `bi ${brand.icon} ${brand.color}`;
            icon.style.fontSize = '2.25rem';
            logo.appendChild(icon);
            return;
        }

        // Reuse the cached favicon from this post's link preview when available.
        const post = link && link.closest('[id^="post-card-"]');
        const favicon = post && post.querySelector('.postcard-link-preview-container .link-preview-favicon');
        if (favicon && favicon.src) {
            const image = document.createElement('img');
            image.src = favicon.src;
            image.alt = '';
            image.style.width = '42px';
            image.style.height = '42px';
            image.style.objectFit = 'contain';
            image.onerror = () => {
                const fallback = document.createElement('i');
                fallback.setAttribute('data-lucide', 'globe');
                fallback.className = 'vasto-icon text-primary';
                fallback.style.setProperty('--vasto-icon-size', '2.25rem');
                logo.replaceChildren(fallback);
            };
            logo.appendChild(image);
            return;
        }

        const fallback = document.createElement('i');
        fallback.setAttribute('data-lucide', 'globe');
        fallback.className = 'vasto-icon text-primary';
        fallback.style.setProperty('--vasto-icon-size', '2.25rem');
        logo.appendChild(fallback);
    }

    window.proceedToExternalLink = function() {
        if (!pendingExternalUrl) return;
        const targetUrl = pendingExternalUrl;
        const modalEl = document.getElementById('externalLinkModal');
        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
            const modal = bootstrap.Modal.getInstance(modalEl);
            if (modal) modal.hide();
        }
        
        // 1. In Capacitor Android native wrapper, launch via AndroidBridge / vastoBridge
        const bridge = window.AndroidBridge || window.vastoBridge;
        if (bridge && typeof bridge.openExternalUrl === 'function') {
            bridge.openExternalUrl(targetUrl);
            return;
        }

        // 2. If Capacitor Browser plugin is installed
        if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.Browser) {
            window.Capacitor.Plugins.Browser.open({ url: targetUrl });
            return;
        }

        // 3. Web / PWA fallback: open in external browser tab
        window.open(targetUrl, '_blank', 'noopener,noreferrer');
    };

    function processLinks(root) {
        // Target all links inside posts, comments, and shared messages
        const links = root.querySelectorAll
            ? root.querySelectorAll('.post-content-wrapper a, .post-content-text a, .postcard-link-card, a:not([class])')
            : [];
        links.forEach(link => {
            if (!link.href || link.href.startsWith('javascript:')) return;

            // Check if internal link
            const isInternal = link.hostname === window.location.hostname || 
                               link.hostname.endsWith('pwaninet.app') ||
                               link.hostname === 'localhost' ||
                               link.hostname === '127.0.0.1';

            if (isInternal) {
                // Internal links should route normally within the app
                link.removeAttribute('target');
            } else {
                // External links - intercept click
                link.removeAttribute('target'); // Remove generic targets
                // Prevent duplicate listeners
                if (!link.hasAttribute('data-external-handled')) {
                    link.setAttribute('data-external-handled', 'true');
                    link.addEventListener('click', function(e) {
                        e.preventDefault();
                        e.stopPropagation();
                        pendingExternalUrl = link.href;
                        updateExternalLinkLogo(pendingExternalUrl, link);
                        const urlEl = document.getElementById('externalLinkUrl');
                        if (urlEl) urlEl.innerText = pendingExternalUrl;
                        const modalEl = document.getElementById('externalLinkModal');
                        if (modalEl && window.bootstrap && window.bootstrap.Modal) {
                            const modal = new bootstrap.Modal(modalEl);
                            modal.show();
                        } else {
                            window.proceedToExternalLink();
                        }
                    });
                }
            }
        });
    }
    
    processLinks(document);
    
    document.addEventListener('htmx:afterSwap', function(event) {
        if (event.detail.target) {
            processLinks(event.detail.target);
        }
    });
});
