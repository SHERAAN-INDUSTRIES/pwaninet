/**
 * Push Notification Soft-Prompt Bottom Sheet Controller (Web / PWA)
 * Manages contextual bottom sheet prompt for push notifications with cooldown logic.
 */
(function() {
    'use strict';

    const STORAGE_KEY_DISMISSED = 'vasto_push_sheet_dismissed_at';
    const STORAGE_KEY_SUBSCRIBED = 'vasto_push_subscribed';
    const COOLDOWN_DAYS = 3;
    const COOLDOWN_MS = COOLDOWN_DAYS * 24 * 60 * 60 * 1000;
    const SHOW_DELAY_MS = 1000;

    let sheetRoot = null;
    let sheetBackdrop = null;
    let sheetContainer = null;
    let enableBtn = null;
    let dismissBtn = null;
    let closeBtn = null;
    let isScheduled = false;
    let isVisible = false;

    function isNativeApp() {
        return (
            (typeof window.Capacitor !== 'undefined' &&
             typeof window.Capacitor.isNativePlatform === 'function' &&
             window.Capacitor.isNativePlatform()) ||
            document.documentElement.classList.contains('is-capacitor') ||
            document.documentElement.classList.contains('is-native-app') ||
            typeof window.AndroidBridge !== 'undefined' ||
            typeof window.vastoBridge !== 'undefined'
        );
    }

    async function waitForBridge() {
        if (!isNativeApp()) return;
        if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications) {
            return;
        }
        for (let i = 0; i < 20; i++) {
            await new Promise(r => setTimeout(r, 25));
            if (window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications) {
                break;
            }
        }
    }

    function isSupported() {
        if (isNativeApp()) {
            const hasPlugin = typeof window.Capacitor !== 'undefined' &&
                window.Capacitor.Plugins &&
                !!window.Capacitor.Plugins.PushNotifications;
            const bridge = window.AndroidBridge || window.vastoBridge;
            const isPushReady = bridge && typeof bridge.isPushNotificationsAvailable === 'function'
                ? bridge.isPushNotificationsAvailable()
                : true;
            return hasPlugin && isPushReady;
        }
        return (
            'Notification' in window &&
            'serviceWorker' in navigator &&
            'PushManager' in window
        );
    }

    async function isEligible(bypassCooldown = false) {
        if (isNativeApp()) {
            await waitForBridge();
        }

        // 1. Feature support check
        if (!isSupported()) {
            return false;
        }

        // Check for debug/test query parameter to bypass cooldown
        try {
            const urlParams = new URLSearchParams(window.location.search);
            if (urlParams.has('push_prompt') || urlParams.has('test_push')) {
                bypassCooldown = true;
            }
        } catch (_) {}

        // 2. Cooldown check from previous dismissal
        if (!bypassCooldown) {
            const dismissedAt = localStorage.getItem(STORAGE_KEY_DISMISSED);
            if (dismissedAt) {
                const timeSinceDismiss = Date.now() - parseInt(dismissedAt, 10);
                if (timeSinceDismiss < COOLDOWN_MS) {
                    return false;
                }
            }
        }

        // 3. Platform-specific subscription & permission checks
        if (isNativeApp()) {
            const isSubscribed = localStorage.getItem(STORAGE_KEY_SUBSCRIBED) === 'true';

            try {
                const PushNotifications = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications;
                if (!PushNotifications) return false;
                const status = await PushNotifications.checkPermissions();

                // If already granted at OS level or marked subscribed, never prompt again
                if (status.receive === 'granted' || isSubscribed) {
                    localStorage.setItem(STORAGE_KEY_SUBSCRIBED, 'true');
                    return false;
                }
                if (status.receive === 'denied') {
                    return false;
                }
                // Eligible only if user has not yet decided (prompt or prompt-with-rationale)
                return status.receive === 'prompt' || status.receive === 'prompt-with-rationale';
            } catch (e) {
                return false;
            }
        } else {
            // Web / PWA browser environment
            if (!('Notification' in window)) {
                return false;
            }
            if (Notification.permission === 'denied') {
                return false;
            }
            if (Notification.permission === 'granted') {
                localStorage.setItem(STORAGE_KEY_SUBSCRIBED, 'true');
                return false;
            }

            // Notification.permission === 'default'
            // Permission has never been granted/denied in this browser session
            localStorage.removeItem(STORAGE_KEY_SUBSCRIBED);
            return true;
        }
    }

    function showSheet() {
        if (!sheetRoot || !sheetContainer || isVisible) return;

        sheetRoot.style.display = 'block';
        // Force reflow for animation
        sheetContainer.offsetHeight;

        sheetContainer.classList.add('visible');
        if (sheetBackdrop) {
            sheetBackdrop.classList.add('visible');
        }
        isVisible = true;

        console.log('[PUSH-SHEET] Push prompt bottom sheet presented');
    }

    function hideSheet(rememberDismissal) {
        if (rememberDismissal === undefined) rememberDismissal = true;
        if (!sheetContainer) return;

        sheetContainer.classList.remove('visible');
        if (sheetBackdrop) {
            sheetBackdrop.classList.remove('visible');
        }
        isVisible = false;

        setTimeout(function() {
            if (sheetRoot && !isVisible) {
                sheetRoot.style.display = 'none';
            }
        }, 380);

        if (rememberDismissal) {
            localStorage.setItem(STORAGE_KEY_DISMISSED, Date.now().toString());
            console.log('[PUSH-SHEET] Push prompt dismissed, cooldown saved for', COOLDOWN_DAYS, 'days');
        }
    }

    async function handleEnablePush() {
        if (!enableBtn) return;

        const btnText = enableBtn.querySelector('.btn-text');
        const btnSpinner = enableBtn.querySelector('.btn-spinner');

        try {
            if (btnText) btnText.classList.add('d-none');
            if (btnSpinner) btnSpinner.classList.remove('d-none');
            enableBtn.disabled = true;

            if (isNativeApp()) {
                const PushNotifications = window.Capacitor && window.Capacitor.Plugins && window.Capacitor.Plugins.PushNotifications;
                if (!PushNotifications) {
                    throw new Error('PushNotifications plugin not available');
                }

                let permStatus = await PushNotifications.checkPermissions();
                if (permStatus.receive === 'prompt' || permStatus.receive === 'prompt-with-rationale') {
                    permStatus = await PushNotifications.requestPermissions();
                }

                if (permStatus.receive === 'granted') {
                    localStorage.setItem(STORAGE_KEY_SUBSCRIBED, 'true');
                    if (typeof window.initNativePush === 'function') {
                        await window.initNativePush(true);
                    }
                    console.log('[PUSH-SHEET] Native push permission granted and subscribed');
                    showToast('Notifications enabled successfully!');
                    hideSheet(false);
                } else {
                    console.warn('[PUSH-SHEET] Native push permission not granted:', permStatus.receive);
                    hideSheet(true);
                }
            } else {
                if (typeof PushSubscriptionManager === 'undefined') {
                    throw new Error('PushSubscriptionManager not loaded');
                }

                const manager = new PushSubscriptionManager();
                const success = await manager.subscribe();

                if (success) {
                    localStorage.setItem(STORAGE_KEY_SUBSCRIBED, 'true');
                    console.log('[PUSH-SHEET] Push subscription successful');
                    showToast('Notifications enabled successfully!');
                    hideSheet(false);
                } else {
                    console.warn('[PUSH-SHEET] Push permission not granted or cancelled');
                    hideSheet(true);
                }
            }
        } catch (err) {
            console.error('[PUSH-SHEET] Error subscribing to push:', err);
            hideSheet(true);
        } finally {
            if (btnText) btnText.classList.remove('d-none');
            if (btnSpinner) btnSpinner.classList.add('d-none');
            if (enableBtn) enableBtn.disabled = false;
        }
    }

    function showToast(message) {
        const toast = document.createElement('div');
        toast.className = 'push-toast-alert';
        toast.innerHTML = '<i aria-hidden="true" data-lucide="circle-check-big" class="vasto-icon me-2"></i> ' + message;
        toast.style.cssText = [
            'position: fixed',
            'bottom: 30px',
            'left: 50%',
            'transform: translateX(-50%)',
            'background: #10b981',
            'color: #ffffff',
            'padding: 12px 24px',
            'border-radius: 9999px',
            'font-size: 0.95rem',
            'font-weight: 600',
            'box-shadow: 0 10px 25px rgba(0,0,0,0.2)',
            'z-index: 10060',
            'display: flex',
            'align-items: center'
        ].join(';');
        document.body.appendChild(toast);
        setTimeout(function() {
            toast.style.opacity = '0';
            toast.style.transition = 'opacity 0.3s ease';
            setTimeout(function() { toast.remove(); }, 300);
        }, 3000);
    }

    function initPushSheet(force = false) {
        sheetRoot = document.getElementById('push-notification-sheet-root');
        if (!sheetRoot) return;

        sheetBackdrop = document.getElementById('push-sheet-backdrop');
        sheetContainer = document.getElementById('push-sheet-container');
        enableBtn = document.getElementById('btn-push-sheet-enable');
        dismissBtn = document.getElementById('btn-push-sheet-dismiss');
        closeBtn = document.getElementById('push-sheet-close-btn');

        if (!sheetRoot._pushEventsBound) {
            if (enableBtn) {
                enableBtn.addEventListener('click', handleEnablePush);
            }
            if (dismissBtn) {
                dismissBtn.addEventListener('click', function() { hideSheet(true); });
            }
            if (closeBtn) {
                closeBtn.addEventListener('click', function() { hideSheet(true); });
            }
            if (sheetBackdrop) {
                sheetBackdrop.addEventListener('click', function() { hideSheet(true); });
            }
            sheetRoot._pushEventsBound = true;
        }

        if (isVisible || isScheduled) return;

        // Check eligibility and schedule presentation
        Promise.resolve(isEligible(force)).then(function(eligible) {
            if (eligible && !isVisible && !isScheduled) {
                isScheduled = true;
                setTimeout(function() {
                    isScheduled = false;
                    showSheet();
                }, SHOW_DELAY_MS);
            }
        });
    }

    // Expose programmatic trigger on window for testing or manual prompt
    window.showPushPromptSheet = async function(force = true) {
        if (isNativeApp()) {
            await waitForBridge();
        }
        initPushSheet(force);
    };

    window.dismissPushPromptSheet = function() {
        hideSheet(true);
    };

    // Initialize on DOM ready
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', function() { initPushSheet(false); });
    } else {
        initPushSheet(false);
    }

    // Re-check after HTMX navigation / page settling
    document.addEventListener('htmx:afterSettle', function() {
        initPushSheet(false);
    });
    document.addEventListener('htmx:historyRestore', function() {
        initPushSheet(false);
    });
})();
