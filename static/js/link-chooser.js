/**
 * vasto Smart Outside-Link Chooser
 * Activates when an external link (e.g. from WhatsApp) is opened in an Android mobile browser.
 * Presents options:
 *   1. Native App (com.vasto.app)
 *   2. PWA (Progressive Web App)
 *   3. Chrome (Browser - continue reading)
 */

(function () {
    'use strict';

    function isAndroid() {
        return /Android/i.test(navigator.userAgent || navigator.vendor || window.opera || '');
    }

    function isNativeApp() {
        return (
            document.documentElement.classList.contains('is-native-app') ||
            document.documentElement.classList.contains('is-capacitor') ||
            typeof window.Capacitor !== 'undefined' ||
            Boolean(window.AndroidBridge || window.vastoBridge) ||
            navigator.userAgent.includes('vastoApp')
        );
    }

    function isStandalone() {
        return (
            window.matchMedia('(display-mode: standalone)').matches ||
            window.matchMedia('(display-mode: fullscreen)').matches ||
            window.matchMedia('(display-mode: minimal-ui)').matches ||
            window.navigator.standalone === true
        );
    }

    function isDirectPostEntry() {
        const path = window.location.pathname;
        return path.includes('/posts/') || path.includes('/documents/') || path.includes('/groups/');
    }

    function createChooserModal() {
        if (document.getElementById('vasto-link-chooser-sheet')) return;

        const currentPath = window.location.pathname + window.location.search;
        const nativeIntentUrl = `intent://pwaninet.app${currentPath}#Intent;scheme=https;package=com.vasto.app;end`;

        const sheet = document.createElement('div');
        sheet.id = 'vasto-link-chooser-sheet';
        sheet.innerHTML = `
            <style>
                #vasto-link-chooser-backdrop {
                    position: fixed;
                    inset: 0;
                    background: rgba(0, 0, 0, 0.55);
                    backdrop-filter: blur(4px);
                    -webkit-backdrop-filter: blur(4px);
                    z-index: 10050;
                    opacity: 0;
                    transition: opacity 0.3s ease;
                }
                #vasto-link-chooser-card {
                    position: fixed;
                    bottom: 0;
                    left: 0;
                    right: 0;
                    max-width: 520px;
                    margin: 0 auto;
                    background: var(--card-bg, #ffffff);
                    color: var(--text-primary, #0f172a);
                    border-top-left-radius: 24px;
                    border-top-right-radius: 24px;
                    padding: 16px 20px calc(24px + env(safe-area-inset-bottom, 12px));
                    box-shadow: 0 -8px 32px rgba(0, 0, 0, 0.2);
                    z-index: 10051;
                    transform: translateY(100%);
                    transition: transform 0.35s cubic-bezier(0.16, 1, 0.3, 1);
                }
                [data-theme="dark"] #vasto-link-chooser-card {
                    background: var(--card-bg, #1e293b);
                    color: var(--text-primary, #f8fafc);
                    border-top: 1px solid rgba(255, 255, 255, 0.1);
                }
                .chooser-drag-handle {
                    width: 38px;
                    height: 4px;
                    background: rgba(148, 163, 184, 0.5);
                    border-radius: 4px;
                    margin: 0 auto 14px;
                }
                .chooser-header {
                    display: flex;
                    align-items: center;
                    gap: 12px;
                    margin-bottom: 16px;
                }
                .chooser-header img {
                    width: 44px;
                    height: 44px;
                    border-radius: 12px;
                    box-shadow: 0 2px 8px rgba(0,0,0,0.12);
                }
                .chooser-title {
                    font-size: 1.05rem;
                    font-weight: 700;
                    margin: 0;
                    line-height: 1.3;
                }
                .chooser-subtitle {
                    font-size: 0.82rem;
                    color: var(--text-secondary, #64748b);
                    margin: 0;
                }
                .chooser-options-grid {
                    display: flex;
                    flex-direction: column;
                    gap: 10px;
                    margin-bottom: 16px;
                }
                .chooser-option-btn {
                    display: flex;
                    align-items: center;
                    justify-content: space-between;
                    padding: 12px 14px;
                    border-radius: 14px;
                    border: 1px solid var(--border, rgba(0,0,0,0.08));
                    background: var(--brand-light, #f8fafc);
                    color: inherit;
                    text-decoration: none;
                    cursor: pointer;
                    transition: all 0.2s ease;
                }
                [data-theme="dark"] .chooser-option-btn {
                    background: rgba(255, 255, 255, 0.04);
                    border-color: rgba(255, 255, 255, 0.1);
                }
                .chooser-option-btn:hover, .chooser-option-btn:active {
                    background: rgba(37, 99, 235, 0.08);
                    border-color: #2563eb;
                    transform: translateY(-1px);
                }
                .chooser-option-left {
                    display: flex;
                    align-items: center;
                    gap: 12px;
                }
                .chooser-option-icon {
                    width: 38px;
                    height: 38px;
                    border-radius: 10px;
                    display: flex;
                    align-items: center;
                    justify-content: center;
                    font-size: 1.25rem;
                }
                .chooser-icon-native { background: #dcfce7; color: #16a34a; }
                .chooser-icon-pwa { background: #dbeafe; color: #2563eb; }
                .chooser-icon-chrome { background: #fef3c7; color: #d97706; }
                [data-theme="dark"] .chooser-icon-native { background: rgba(22, 163, 74, 0.2); color: #4ade80; }
                [data-theme="dark"] .chooser-icon-pwa { background: rgba(37, 99, 235, 0.2); color: #60a5fa; }
                [data-theme="dark"] .chooser-icon-chrome { background: rgba(217, 119, 6, 0.2); color: #fbbf24; }
                .chooser-opt-name {
                    font-weight: 600;
                    font-size: 0.92rem;
                    margin: 0 0 2px 0;
                }
                .chooser-opt-desc {
                    font-size: 0.76rem;
                    color: var(--text-secondary, #64748b);
                    margin: 0;
                }
                .chooser-badge {
                    font-size: 0.68rem;
                    font-weight: 700;
                    padding: 3px 8px;
                    border-radius: 50px;
                    text-transform: uppercase;
                    letter-spacing: 0.4px;
                }
                .chooser-badge-rec { background: #2563eb; color: #fff; }
                .chooser-badge-pwa { background: #059669; color: #fff; }
                .chooser-badge-web { background: rgba(148, 163, 184, 0.2); color: inherit; }
                .chooser-chrome-action {
                    width: 100%;
                    padding: 10px;
                    border: none;
                    background: transparent;
                    color: var(--text-secondary, #64748b);
                    font-size: 0.88rem;
                    font-weight: 600;
                    text-align: center;
                    cursor: pointer;
                    border-radius: 12px;
                    transition: background 0.2s ease;
                }
                .chooser-chrome-action:hover {
                    background: rgba(148, 163, 184, 0.1);
                    color: var(--text-primary, #0f172a);
                }
            </style>
            <div id="vasto-link-chooser-backdrop"></div>
            <div id="vasto-link-chooser-card">
                <div class="chooser-drag-handle"></div>
                <div class="chooser-header">
                    <img src="/static/images/web-app-manifest-192x192-rounded.png" alt="vasto">
                    <div>
                        <h5 class="chooser-title">Open with vasto</h5>
                        <p class="chooser-subtitle">Choose where you want to view this post</p>
                    </div>
                </div>
                <div class="chooser-options-grid">
                    <!-- Option 1: Native App -->
                    <button type="button" class="chooser-option-btn text-start" id="chooser-opt-native">
                        <div class="chooser-option-left">
                            <div class="chooser-option-icon chooser-icon-native">
                                <i aria-hidden="true" data-lucide="smartphone" class="vasto-icon"></i>
                            </div>
                            <div>
                                <div class="chooser-opt-name">vasto App</div>
                                <div class="chooser-opt-desc">Fastest experience & offline caching</div>
                            </div>
                        </div>
                        <span class="chooser-badge chooser-badge-rec">App</span>
                    </button>

                    <!-- Option 2: Installed PWA -->
                    <button type="button" class="chooser-option-btn text-start" id="chooser-opt-pwa">
                        <div class="chooser-option-left">
                            <div class="chooser-option-icon chooser-icon-pwa">
                                <i aria-hidden="true" data-lucide="monitor" class="vasto-icon"></i>
                            </div>
                            <div>
                                <div class="chooser-opt-name">vasto Web App (PWA)</div>
                                <div class="chooser-opt-desc">Open in standalone WebAPK or install</div>
                            </div>
                        </div>
                        <span class="chooser-badge chooser-badge-pwa">PWA</span>
                    </button>

                    <!-- Option 3: Continue in Chrome -->
                    <button type="button" class="chooser-option-btn text-start" id="chooser-opt-chrome">
                        <div class="chooser-option-left">
                            <div class="chooser-option-icon chooser-icon-chrome">
                                <i aria-hidden="true" data-lucide="compass" class="vasto-icon"></i>
                            </div>
                            <div>
                                <div class="chooser-opt-name">Continue in Chrome</div>
                                <div class="chooser-opt-desc">Stay in current browser tab</div>
                            </div>
                        </div>
                        <span class="chooser-badge chooser-badge-web">Browser</span>
                    </button>
                </div>
                <button type="button" class="chooser-chrome-action" id="chooser-dismiss-btn">
                    Dismiss
                </button>
            </div>
        `;

        document.body.appendChild(sheet);

        const backdrop = document.getElementById('vasto-link-chooser-backdrop');
        const card = document.getElementById('vasto-link-chooser-card');

        requestAnimationFrame(() => {
            backdrop.style.opacity = '1';
            card.style.transform = 'translateY(0)';
        });

        function closeChooser() {
            backdrop.style.opacity = '0';
            card.style.transform = 'translateY(100%)';
            setTimeout(() => {
                sheet.remove();
                if (window.dismissvastoLinkChooser === closeChooser) {
                    delete window.dismissvastoLinkChooser;
                }
            }, 350);
            sessionStorage.setItem('vasto_link_chooser_dismissed', 'true');
        }

        window.dismissvastoLinkChooser = closeChooser;

        backdrop.addEventListener('click', closeChooser);
        document.getElementById('chooser-dismiss-btn').addEventListener('click', closeChooser);

        document.getElementById('chooser-opt-chrome').addEventListener('click', function () {
            closeChooser();
        });

        document.getElementById('chooser-opt-native').addEventListener('click', function () {
            closeChooser();
            const start = Date.now();
            window.location.href = nativeIntentUrl;

            setTimeout(() => {
                if (Date.now() - start < 2500) {
                    if (confirm("vasto Native App is not installed. Would you like to download the APK?")) {
                        window.location.href = '/download/app/latest/';
                    }
                }
            }, 1800);
        });

        document.getElementById('chooser-opt-pwa').addEventListener('click', function () {
            closeChooser();
            if (window.PWAInstallManager && typeof window.PWAInstallManager.promptInstall === 'function') {
                window.PWAInstallManager.promptInstall();
            } else if (window.deferredPrompt) {
                window.deferredPrompt.prompt();
            } else {
                const pwaIntent = `intent://pwaninet.app${currentPath}#Intent;scheme=https;action=android.intent.action.VIEW;category=android.intent.category.BROWSABLE;end`;
                window.location.href = pwaIntent;
            }
        });
    }

    function initLinkChooser() {
        if (!isAndroid()) return;
        if (isNativeApp() || isStandalone()) return;

        if (sessionStorage.getItem('vasto_link_chooser_dismissed') === 'true') {
            return;
        }

        if (isDirectPostEntry()) {
            setTimeout(createChooserModal, 700);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initLinkChooser);
    } else {
        initLinkChooser();
    }

    window.showvastoLinkChooser = createChooserModal;

})();
