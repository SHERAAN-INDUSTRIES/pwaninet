/**
 * Spark Chat Controller
 * Vanilla JS controller for Spark assistant interface
 * Supports Web, PWA, and Capacitor Android WebView
 */

(function () {
    'use strict';

    function getCsrfToken() {
        const meta = document.querySelector('meta[name="csrf-token"]');
        if (meta && meta.content) return meta.content;
        const cookieMatch = document.cookie.match(/csrftoken=([^;]+)/);
        return cookieMatch ? cookieMatch[1] : '';
    }

    function escapeHtml(str) {
        if (!str) return '';
        const div = document.createElement('div');
        div.textContent = str;
        return div.innerHTML;
    }

    function isFinePointerDevice() {
        return typeof window !== 'undefined' &&
               window.matchMedia &&
               window.matchMedia('(hover: hover) and (pointer: fine)').matches;
    }

    // Icon helpers (sparkIconSpec / sparkRenderIcons / sparkSetIcon) live in
    // static/js/pwanimate/spark-icons.js and are exposed on window.

    function isSparkCitationLink(element) {
        if (!element) return false;
        const citationControl = (element.matches && element.matches('a, button'))
            ? element
            : (typeof element.closest === 'function' ? element.closest('a, button') : null);
        if (!citationControl) return false;
        if (citationControl.classList && citationControl.classList.contains('pwanimate-citation-badge')) return true;
        if (typeof citationControl.closest === 'function') {
            if (citationControl.closest('.pwanimate-citations') || citationControl.closest('.pwanimate-citation-list')) return true;
        }
        if (typeof citationControl.hasAttribute === 'function') {
            if (citationControl.hasAttribute('data-pwanimate-resource') || citationControl.hasAttribute('data-citation')) return true;
        }
        const ds = citationControl.dataset || {};
        if (ds.resourceType || ds.sourceType || ds.documentShareId || ds.documentId || ds.postId) return true;
        return false;
    }

    /**
     * Returns a human-readable relative time string for a given ISO 8601 timestamp.
     * e.g. "just now", "2 minutes ago", "3 hours ago", "yesterday", "5 days ago"
     */
    function formatRelativeTime(isoString) {
        if (!isoString) return '';
        let date;
        try { date = new Date(isoString); } catch (e) { return ''; }
        if (isNaN(date.getTime())) return '';
        const diffMs = Date.now() - date.getTime();
        const diffSec = Math.floor(diffMs / 1000);
        if (diffSec < 60) return 'just now';
        const diffMin = Math.floor(diffSec / 60);
        if (diffMin < 60) return diffMin === 1 ? '1 minute ago' : diffMin + ' minutes ago';
        const diffHr = Math.floor(diffMin / 60);
        if (diffHr < 24) return diffHr === 1 ? '1 hour ago' : diffHr + ' hours ago';
        const diffDay = Math.floor(diffHr / 24);
        if (diffDay === 1) return 'yesterday';
        if (diffDay < 7) return diffDay + ' days ago';
        const diffWk = Math.floor(diffDay / 7);
        if (diffWk < 5) return diffWk === 1 ? '1 week ago' : diffWk + ' weeks ago';
        const diffMo = Math.floor(diffDay / 30);
        if (diffMo < 12) return diffMo === 1 ? '1 month ago' : diffMo + ' months ago';
        const diffYr = Math.floor(diffDay / 365);
        return diffYr === 1 ? '1 year ago' : diffYr + ' years ago';
    }

    class SparkChat {
        constructor(workspace) {
            this.workspace = workspace;
            this.conversationId = workspace.dataset.conversationId || null;
            this.pendingConversationId = this.conversationId;
            this.transcript = workspace.querySelector('#pwanimate-transcript');
            this.composerForm = workspace.querySelector('#pwanimate-composer-form');
            this.input = workspace.querySelector('#pwanimate-input');
            this.sendBtn = workspace.querySelector('#pwanimate-send-btn');
            this.voiceBtn = workspace.querySelector('#pwanimateVoiceBtn');
            this.voiceCancelBtn = workspace.querySelector('#pwanimateVoiceCancelBtn');
            this.voiceStopBtn = workspace.querySelector('#pwanimateVoiceStopBtn');
            this.voiceSendBtn = workspace.querySelector('#pwanimateVoiceSendBtn');
            this.voiceStatus = workspace.querySelector('#pwanimate-voice-status');
            this.voiceFeedback = workspace.querySelector('#pwanimate-voice-feedback');
            this.voiceCapture = workspace.querySelector('#pwanimate-voice-capture');
            this.voiceWaveform = workspace.querySelector('#pwanimate-voice-waveform');
            this.voiceWaveformBars = this.voiceWaveform ? Array.from(this.voiceWaveform.querySelectorAll('span')) : [];
            this.voiceWaveformHistory = [];
            this.voiceWaveformLastSampleAt = 0;
            this.voiceRecorder = null;
            this.voiceChunks = [];
            this.voiceStarting = false;
            this.voiceTranscriptionAbortController = null;
            this.voiceRecorderSupported = Boolean(
                navigator.mediaDevices && navigator.mediaDevices.getUserMedia && window.MediaRecorder
            );
            this.voiceListening = false;
            this.voiceStopping = false;
            this.voiceSendAfterTranscription = false;
            this.voiceFinalTranscript = '';
            this.voiceSelection = null;
            this.voiceErrorMessage = '';
            this.voiceCancelled = false;
            this.voiceMediaStream = null;
            this.voiceAudioContext = null;
            this.voiceAnalyser = null;
            this.voiceAnimationFrame = null;
            // Spark voice output (reads a reply aloud via the Web Speech API)
            this.speaking = false;
            this.speakingBtn = null;
            this.speakUtterance = null;
            this.speechAudio = null;
            this.speechRequestId = 0;
            this.voiceStyle = workspace.dataset.sparkVoiceStyle || 'warm';
            this.voiceGender = workspace.dataset.sparkVoiceGender || 'female';
            this.autoTtsEnabled = workspace.dataset.sparkAutoTts === 'true';
            this.sidebarList = document.getElementById('pwanimate-sidebar-list');
            this.mobileList = document.getElementById('pwanimate-mobile-list');
            this.quotaStatusEl = document.getElementById('pwanimate-quota-status');
            this.isGenerating = false;
            this.waitingForInput = false;
            // Activity event ids whose clarification/approval widget was already
            // answered — replayed events render resolved instead of re-armed.
            this.resolvedActivityEvents = new Set();
            // Durable-run coordination: the pending structured interaction the
            // composer may answer, and the run currently executing server-side.
            this.pendingInteraction = null;
            this.activeRunId = null;
            this._interactionBlockEl = null;
            this._interactionBlockSig = '';
            this._interactionSubmitting = false;
            this.abortController = null;
            this.resizeFrame = null;
            this.lastPreviewTriggerEl = null;
            this._onDocumentClick = null;
            this._onKeyDown = null;
            this._mobileMenuSwipeStart = null;
            this._onMobileMenuTouchStart = null;
            this._onMobileMenuTouchMove = null;
            this._onMobileMenuTouchEnd = null;
            this._onMobileMenuTouchCancel = null;

            // Phase 1 Workspace Geometry & State Contract
            this.leftRailCollapsed = localStorage.getItem('pwanimate_left_rail_collapsed') === 'true';
            const parsedPreferred = parseInt(localStorage.getItem('pwanimate_context_rail_width'), 10);
            // null means "no deliberate user preference" — the rail then
            // defaults to the equal split computed by calculateEffectiveContextLimits().
            this.savedPreferredWidth = (!isNaN(parsedPreferred) && parsedPreferred >= 200 && parsedPreferred <= 2400) ? parsedPreferred : null;
            this.currentRenderedWidth = 0;
            // Start closed. Initial context resources are loaded immediately
            // after workspace geometry initializes and will open the tray.
            // Do not restore an old open preference into an empty workspace.
            this.contextRailOpen = false;
            this.activeResourceDetails = null;
            this.resizerEl = null;
            this.isResizing = false;
            this._onWindowResize = null;
            this._resizerCleanup = null;

            // Phase 2A Context Workspace & Multi-Resource State
            this.previewResourceState = null;
            this.contextResources = [];
            this.activeWorkspaceTab = 'preview'; // 'preview' | 'context'
            this.activeContextIndex = -1;
            this.previewDocViewer = null;
            this.contextDocViewer = null;
            // Canonical live page state keyed by document identity. Viewer
            // page-change callbacks write here; getContextResourcesForRequest()
            // snapshots it at send time so remounts/re-previews never send stale pages.
            this.viewerPageState = {};

            // Persistent Study Mode state
            this.activeStudySession = null;
            this.resumableStudySession = null;
            this.unavailableResourceNotices = [];
            this.studyModeActive = workspace.dataset.studyMode === 'true' || workspace.dataset.initialStudyMode === 'true';
            this.studySessionId = workspace.dataset.studySessionId || null;
            this.historyFilter = 'all';
            this._studyContextSyncTimer = null;

            // Model selection & Quota modal state
            // Engines-only picker: raw provider/model selections are retired —
            // migrate any stored legacy selection back to Auto.
            ['pwanimate_selected_provider', 'pwanimate_selected_model', 'pwanimate_selected_display', 'pwanimate_selected_provider_label']
                .forEach((key) => localStorage.removeItem(key));
            this.selectedProvider = '';
            this.selectedModel = '';
            this.selectedDisplay = 'Auto';
            this.selectedProviderLabel = '';
            this.selectedEngine = localStorage.getItem('pwanimate_selected_engine') || '';
            this.selectedEngineLabel = localStorage.getItem('pwanimate_selected_engine_label') || '';
            this.selectedModelLabel = workspace.querySelector('#pwanimate-selected-model-label');
            this.dropdownStatusDot = workspace.querySelector('#pwanimate-dropdown-status-dot');
            this.dropdownModelsList = workspace.querySelector('#pwanimate-dropdown-models-list');
            this.modalModelsContainer = document.getElementById('pwanimate-modal-models-container');
            this.refreshQuotaBtn = document.getElementById('pwanimateRefreshQuotaBtn');
            this.quotaModal = document.getElementById('pwanimateQuotaModal');
            this.cachedQuotaStatus = null;

            this.init();
        }

        init() {
            this.initWorkspaceGeometry();
            this.initInitialContextResources();
            this.initStudyMode();
            this.initMarkdown();
            this.renderExistingMarkdown();
            this.initEmptyStateWelcome();
            this.checkCollapsibleUserBubbles();
            requestAnimationFrame(() => this.checkCollapsibleUserBubbles());
            this.bindEvents();
            this.initVoiceInput();
            this.initSpeakReplies();
            this.initMobileMenuSwipeNavigation();
            this.initContextDocumentPicker();
            this.initAttachmentHandlers();
            this.updateQuotaStatus({ engines: this.getDefaultEngines() });
            this.updateSelectedModelUI();
            this.updateSendButtonState();
            this.initQuotaChip();
            this.initSubscriptionOverlay();
            this.initVisionChoice();
            if (this.input && this.input.value) {
                this.resizeTextarea();
            }
            this.scrollToBottom(false);
            this.fetchQuotaStatus();
            this.updateTimestamps(); // apply relative time to any server-rendered timestamps
            this.startTimestampTicker();
        }

        // ------------------------------------------------------------ quota chip
        // The composer's daily-usage indicator. It reads the same counters the
        // quota check enforces, so the number shown is the number acted on.
        initQuotaChip() {
            this.quotaWrap = document.getElementById('pwanimateQuotaWrap');
            this.quotaChip = document.getElementById('pwanimateQuotaChip');
            this.quotaArc = document.getElementById('pwanimateQuotaArc');
            this.quotaLabel = document.getElementById('pwanimateQuotaLabel');
            this.quotaPopover = document.getElementById('pwanimateQuotaPopover');
            if (!this.quotaWrap || !this.quotaChip) return;

            this.quotaState = null;

            // Hover/focus opens the detail; click toggles it (touch friendly).
            this.quotaChip.addEventListener('mouseenter', () => this.openQuotaPopover());
            this.quotaChip.addEventListener('mouseleave', () => this.scheduleQuotaClose());
            this.quotaPopover.addEventListener('mouseenter', () => this.quotaCloseTimer && clearTimeout(this.quotaCloseTimer));
            this.quotaPopover.addEventListener('mouseleave', () => this.scheduleQuotaClose());
            this.quotaChip.addEventListener('focus', () => this.openQuotaPopover());
            this.quotaChip.addEventListener('blur', () => this.scheduleQuotaClose());
            this.quotaChip.addEventListener('click', (event) => {
                event.preventDefault();
                event.stopPropagation();
                if (this.quotaPopover.hidden) this.openQuotaPopover();
                else this.closeQuotaPopover();
            });
            this.quotaPopover.addEventListener('click', (event) => event.stopPropagation());
            document.addEventListener('click', () => this.closeQuotaPopover());
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape') this.closeQuotaPopover();
            });

            this.loadQuota();
            this.quotaTimer = setInterval(() => this.loadQuota(), 60000);
            document.addEventListener('visibilitychange', () => {
                if (!document.hidden) this.loadQuota();
            });
        }

        async loadQuota() {
            try {
                const response = await fetch('/api/pwanimate/billing/quota/', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin',
                });
                if (!response.ok) return;
                const data = await response.json();
                this.quotaState = data.quota || null;
                this.renderQuotaChip();
            } catch (error) {
                // The chip is an aid, never a dependency: a failure here must
                // not disturb the chat.
            }
        }

        renderQuotaChip() {
            const state = this.quotaState;
            if (!state || !this.quotaChip) return;
            const chats = state.chats || {};
            const limit = Number(chats.limit);
            const used = Number(chats.used || 0);
            if (!limit || limit <= 0) {
                this.quotaWrap.hidden = true;
                return;
            }
            this.quotaWrap.hidden = false;

            const remaining = Math.max(0, limit - used);
            const ratio = Math.min(1, Math.max(0, remaining / limit));
            const circumference = 2 * Math.PI * 15;
            if (this.quotaArc) {
                this.quotaArc.setAttribute('stroke-dasharray', `${circumference}`);
                this.quotaArc.setAttribute('stroke-dashoffset', `${circumference * (1 - ratio)}`);
            }
            // The ring is the chat allowance (the thing that can run out);
            // the number is the tokens Spark has read for you today.
            const tokens = state.tokens || {};
            if (this.quotaLabel) {
                this.quotaLabel.textContent = this.formatQuotaTokens(tokens.total || 0);
            }
            this.quotaChip.classList.toggle('is-warning', ratio > 0 && ratio <= 0.2);
            this.quotaChip.classList.toggle('is-critical', remaining <= 0);

            this.quotaChip.setAttribute(
                'aria-label',
                `${this.formatQuotaTokens(tokens.total || 0)} tokens today. ` +
                `${remaining} of ${limit} chats left today.`
            );
            if (this.quotaPopover && !this.quotaPopover.hidden) this.renderQuotaPopover();
        }

        formatQuotaTokens(value) {
            const tokens = Number(value || 0);
            if (tokens >= 1000000) return `${(tokens / 1000000).toFixed(1)}M`;
            if (tokens >= 1000) return `${(tokens / 1000).toFixed(1)}k`;
            return String(tokens);
        }

        scheduleQuotaClose() {
            if (this.quotaCloseTimer) clearTimeout(this.quotaCloseTimer);
            this.quotaCloseTimer = setTimeout(() => {
                if (this.quotaPopover && this.quotaPopover.matches(':hover')) return;
                this.closeQuotaPopover();
            }, 140);
        }

        openQuotaPopover() {
            if (!this.quotaPopover || !this.quotaState) return;
            this.renderQuotaPopover();
            this.quotaPopover.hidden = false;
            this.quotaChip.setAttribute('aria-expanded', 'true');
            // Flip below when there is no room above.
            this.quotaPopover.classList.remove('is-below');
            if (this.quotaPopover.getBoundingClientRect().top < 8) {
                this.quotaPopover.classList.add('is-below');
            }
        }

        closeQuotaPopover() {
            if (!this.quotaPopover) return;
            this.quotaPopover.hidden = true;
            this.quotaChip.setAttribute('aria-expanded', 'false');
        }

        renderQuotaPopover() {
            const state = this.quotaState;
            if (!state || !this.quotaPopover) return;
            const chats = state.chats || {};
            const images = state.images || {};
            const tokens = state.tokens || {};
            const tier = state.tier || {};
            const resetText = state.resets_at ? this.quotaResetText(state.resets_at) : '';
            const rows = [
                ['Chats left today', `${chats.remaining ?? '—'} of ${chats.limit ?? '—'}`],
                ['Images left today', `${images.remaining ?? '—'} of ${images.limit ?? '—'}`],
                ['Tokens today', this.formatQuotaTokens(tokens.total || 0)],
            ];
            this.quotaPopover.innerHTML = `
                <div class="pwanimate-quota-popover-title" id="pwanimateQuotaPopoverTitle">
                    <span>${escapeHtml(tier.name || 'Free')}</span>
                    <span class="pwanimate-quota-popover-tier">${tier.is_free ? 'Free tier' : 'Paid tier'}</span>
                </div>
                <dl class="pwanimate-quota-popover-rows">
                    ${rows.map(([label, value]) => `<dt>${escapeHtml(label)}</dt><dd>${escapeHtml(value)}</dd>`).join('')}
                </dl>
                <p class="pwanimate-quota-popover-note">${resetText ? `Daily allowance resets ${escapeHtml(resetText)}. ` : ''}Free routes are used whenever they are healthy — your allowance is never a hard block.</p>
                <button type="button" class="pwanimate-quota-popover-cta" data-quota-usage-link>View full usage</button>
            `;
            const link = this.quotaPopover.querySelector('[data-quota-usage-link]');
            if (link) {
                link.addEventListener('click', () => {
                    this.closeQuotaPopover();
                    this.openUsageDashboard();
                });
            }
        }

        /** Open the Spark settings on the Usage tab — the right surface for the device. */
        openUsageDashboard() {
            // The mobile settings are a PAGE, not a modal: the modal is the
            // desktop surface. Same breakpoint the app uses for the mobile
            // settings layout (max-width: 767.98px).
            const isMobile = !!(window.matchMedia
                && window.matchMedia('(max-width: 767.98px)').matches);
            if (isMobile) {
                window.location.href = '/pwanimate/settings/usage/';
                return;
            }
            const modal = document.getElementById('pwanimateSettingsModal');
            if (modal && window.bootstrap && window.bootstrap.Modal) {
                window.bootstrap.Modal.getOrCreateInstance(modal).show();
            }
            if (typeof window.switchSparkSettingsTab === 'function') {
                window.switchSparkSettingsTab('modal', 'usage');
            }
        }

        // ------------------------------------------------- vision image choice
        /**
         * One-time choice after a vision failure: keep the image in the
         * conversation (so it can be reused) or drop it for good. The answer
         * is recorded server-side, so the question is never asked twice.
         */
        initVisionChoice() {
            document.addEventListener('click', async (event) => {
                const button = event.target.closest('[data-vision-policy]');
                if (!button) return;
                const holder = button.closest('[data-vision-choice]');
                const attachmentId = holder ? holder.dataset.visionChoice : null;
                if (!attachmentId) return;
                const policy = button.dataset.visionPolicy;
                button.disabled = true;
                try {
                    const response = await fetch(
                        `/api/pwanimate/attachments/${attachmentId}/vision-policy/`,
                        {
                            method: 'POST',
                            headers: {
                                'Content-Type': 'application/json',
                                'Accept': 'application/json',
                                'X-CSRFToken': getCsrfToken(),
                            },
                            credentials: 'same-origin',
                            body: JSON.stringify({ policy }),
                        }
                    );
                    const data = await response.json().catch(() => ({}));
                    if (!response.ok) {
                        button.disabled = false;
                        return;
                    }
                    if (holder) {
                        holder.innerHTML = `<i data-lucide="check"></i><span>${escapeHtml(data.message || 'Saved.')}</span>`;
                    }
                } catch (error) {
                    button.disabled = false;
                }
            });
        }

        // ------------------------------------------------- subscription overlay
        initSubscriptionOverlay() {
            this.subscriptionOverlay = document.getElementById('pwanimateSubscriptionOverlay');
            if (!this.subscriptionOverlay) return;
            this.paymentTier = null;
            this.paymentReference = null;

            // Lift it to the body so it always paints above the settings
            // modal (and its backdrop) whatever the chat layout does.
            if (this.subscriptionOverlay.parentElement !== document.body) {
                document.body.appendChild(this.subscriptionOverlay);
            }

            document.addEventListener('click', (event) => {
                const opener = event.target.closest('[data-open-subscription]');
                if (opener) {
                    event.preventDefault();
                    this.openSubscriptionOverlay();
                    return;
                }
                const closer = event.target.closest('[data-close-subscription]');
                if (closer) {
                    event.preventDefault();
                    this.closeSubscriptionOverlay();
                    return;
                }
                const subscribe = event.target.closest('[data-subscribe-tier]');
                if (subscribe) {
                    event.preventDefault();
                    this.startSubscriptionPayment(
                        subscribe.dataset.subscribeTier,
                        subscribe.dataset.tierPrice
                    );
                    return;
                }
                if (event.target.closest('[data-cancel-payment]')) {
                    event.preventDefault();
                    this.closeSubscriptionOverlay();
                    return;
                }
                if (event.target.closest('[data-submit-payment]')) {
                    event.preventDefault();
                    this.submitSubscriptionPayment();
                    return;
                }
                if (event.target.closest('[data-demo-complete]')) {
                    event.preventDefault();
                    this.completeDemoPayment();
                }
            });

            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && !this.subscriptionOverlay.hidden) {
                    this.closeSubscriptionOverlay();
                }
            });
        }

        openSubscriptionOverlay() {
            if (!this.subscriptionOverlay) return;
            this.subscriptionOverlay.hidden = false;
            this.loadTiers();
        }

        closeSubscriptionOverlay() {
            if (!this.subscriptionOverlay) return;
            this.subscriptionOverlay.hidden = true;
            const payment = document.getElementById('pwanimateSubscriptionPayment');
            if (payment) {
                payment.hidden = true;
                payment.classList.remove('is-active');
            }
        }

        async loadTiers() {
            const container = document.getElementById('pwanimateSubscriptionCards');
            if (!container) return;
            try {
                const response = await fetch('/api/pwanimate/billing/tiers/', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin',
                });
                if (!response.ok) return;
                const data = await response.json();
                const current = data.current_tier;
                container.querySelectorAll('[data-tier-code]').forEach((card) => {
                    const isCurrent = card.dataset.tierCode === current;
                    card.classList.toggle('is-current', isCurrent);
                    // The "Your plan" badge moves with the current tier.
                    const badge = card.querySelector('.pwanimate-subscription-current');
                    if (badge) badge.classList.toggle('d-none', !isCurrent);
                    // ...and so does the button's label.
                    const button = card.querySelector('[data-subscribe-tier]');
                    if (!button) return;
                    const price = Number(button.dataset.tierPrice || 0);
                    button.disabled = isCurrent;
                    button.classList.toggle('btn-outline-secondary', isCurrent);
                    button.classList.toggle('btn-primary', !isCurrent);
                    button.textContent = isCurrent
                        ? 'Current plan'
                        : (price > 0 ? 'Upgrade' : 'Included');
                });
            } catch (error) {
                // The overlay stays usable with its server-rendered cards.
            }
        }

        startSubscriptionPayment(tierCode, price) {
            this.paymentTier = tierCode;
            this.paymentReference = null;
            const payment = document.getElementById('pwanimateSubscriptionPayment');
            const summary = document.getElementById('pwanimatePaymentSummary');
            const label = document.getElementById('pwanimatePaymentButtonLabel');
            const status = document.getElementById('pwanimatePaymentStatus');
            const demoButton = this.subscriptionOverlay.querySelector('[data-demo-complete]');
            // Use the tier's display name ("Pro Monthly"), not its code.
            const card = this.subscriptionOverlay.querySelector(`[data-tier-code="${tierCode}"]`);
            const nameEl = card ? card.querySelector('.pwanimate-subscription-name') : null;
            const tierName = nameEl ? nameEl.textContent.trim() : tierCode.replace(/_/g, ' ');
            if (summary) {
                summary.textContent = `You are upgrading to ${tierName} — KSh ${price}.`;
            }
            if (label) label.textContent = `Pay KSh ${price}`;
            if (status) {
                status.textContent = '';
                status.classList.remove('is-error', 'is-success');
            }
            if (demoButton) demoButton.classList.add('d-none');
            if (payment) {
                payment.hidden = false;
                payment.classList.add('is-active');
                // The payment panel sits below the tier cards; bring it into
                // view so it never looks like it failed to open.
                requestAnimationFrame(() => {
                    payment.scrollIntoView({ behavior: 'smooth', block: 'center' });
                });
            }
            const phone = document.getElementById('pwanimatePaymentPhone');
            if (phone) phone.focus();
        }

        async submitSubscriptionPayment() {
            const phone = document.getElementById('pwanimatePaymentPhone');
            const status = document.getElementById('pwanimatePaymentStatus');
            const demoButton = this.subscriptionOverlay.querySelector('[data-demo-complete]');
            if (!this.paymentTier) return;
            try {
                const response = await fetch('/api/pwanimate/billing/mpesa/stk/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        tier_code: this.paymentTier,
                        phone: phone ? phone.value : '',
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    if (status) {
                        status.textContent = data.error || 'Could not start the payment.';
                        status.classList.add('is-error');
                    }
                    return;
                }
                this.paymentReference = data.payment ? data.payment.reference : null;
                if (status) {
                    status.textContent = data.message || 'Check your phone for the M-Pesa prompt.';
                    status.classList.remove('is-error');
                }
                if (demoButton) demoButton.classList.toggle('d-none', !data.demo);
            } catch (error) {
                if (status) {
                    status.textContent = 'Could not reach the payment service. Try again.';
                    status.classList.add('is-error');
                }
            }
        }

        async completeDemoPayment() {
            const status = document.getElementById('pwanimatePaymentStatus');
            if (!this.paymentReference) return;
            try {
                const response = await fetch('/api/pwanimate/billing/mpesa/demo-complete/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ reference: this.paymentReference }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok || !data.activated) {
                    if (status) {
                        status.textContent = data.error || 'The payment did not complete.';
                        status.classList.add('is-error');
                    }
                    return;
                }
                if (status) {
                    status.textContent = 'Payment complete — your plan is active.';
                    status.classList.remove('is-error');
                    status.classList.add('is-success');
                }
                // Refresh everything that depends on the tier.
                if (this.loadQuota) this.loadQuota();
                this.loadTiers();
                this.refreshUsageTab();
            } catch (error) {
                if (status) {
                    status.textContent = 'Could not complete the payment. Try again.';
                    status.classList.add('is-error');
                }
            }
        }

        /** Re-render the Usage tab from the server so the numbers are fresh. */
        async refreshUsageTab() {
            const tab = document.getElementById('pwanimateUsageTab');
            if (!tab) return;
            try {
                const response = await fetch('/pwanimate/settings/usage/', {
                    headers: {
                        'Accept': 'text/html',
                        'HX-Request': 'true',
                        'HX-Target': 'pwanimate-settings-modal-body',
                    },
                    credentials: 'same-origin',
                });
                if (!response.ok) return;
                const html = await response.text();
                tab.outerHTML = html;
            } catch (error) {
                // Keep the stale (but true) numbers rather than breaking the tab.
            }
        }

        quotaResetText(iso) {
            try {
                const diff = new Date(iso).getTime() - Date.now();
                if (diff <= 0) return 'shortly';
                const hours = Math.floor(diff / 3600000);
                const minutes = Math.floor((diff % 3600000) / 60000);
                if (hours >= 1) return `in ${hours}h ${minutes}m`;
                return `in ${minutes}m`;
            } catch (error) {
                return '';
            }
        }

        initInitialContextResources() {
            const jsonEl = document.getElementById('pwanimate-initial-context-resources');
            let initialResources = [];
            if (jsonEl) {
                try {
                    initialResources = JSON.parse(jsonEl.textContent || '[]');
                } catch (error) {
                    console.warn('[Spark] Could not read initial context resources.', error);
                }
            }

            this.contextResources = (Array.isArray(initialResources) ? initialResources : []).map(resource =>
                this.normalizeResource(resource.mediaUrl || resource.url, resource.title, resource)
            );
            this.addImageAttachmentsToContext(this.transcript, false);
            if (!this.contextResources.length) {
                this.syncDocumentStudyUI();
                return;
            }

            this.activeContextIndex = 0;
            this.updateContextCountBadges();
            this.syncDocumentStudyUI();

            // Reveal the desktop rail before creating its viewer so the viewer
            // measures its final width on the first render. switchWorkspaceTab()
            // calls syncContextView(), so avoid an eager sync here: rendering it
            // twice immediately destroys the first viewer while it is loading.
            if (window.innerWidth >= 1200) {
                this.openContextRail();
            }
            this.switchWorkspaceTab('context');

            if (window.innerWidth < 1200) {
                const sheetEl = document.getElementById('pwanimateResourcePreviewSheet');
                if (sheetEl && window.bootstrap) {
                    let offcanvas = bootstrap.Offcanvas.getInstance(sheetEl);
                    if (!offcanvas) offcanvas = new bootstrap.Offcanvas(sheetEl);
                    offcanvas.show();
                }
            }
        }

        initStudyMode() {
            const activeJsonEl = document.getElementById('pwanimate-active-study-session');
            if (activeJsonEl && activeJsonEl.textContent) {
                try {
                    const parsed = JSON.parse(activeJsonEl.textContent);
                    if (parsed && parsed.id) {
                        this.activeStudySession = parsed;
                        this.studySessionId = parsed.id;
                        this.studyModeActive = parsed.status === 'active';
                    }
                } catch (err) {
                    console.warn('[Spark] Could not parse active study session:', err);
                }
            }

            const resumableJsonEl = document.getElementById('pwanimate-resumable-study-session');
            if (resumableJsonEl && resumableJsonEl.textContent) {
                try {
                    const parsed = JSON.parse(resumableJsonEl.textContent);
                    if (parsed && parsed.id) {
                        this.resumableStudySession = parsed;
                    }
                } catch (err) {
                    console.warn('[Spark] Could not parse resumable study session:', err);
                }
            }

            const noticesJsonEl = document.getElementById('pwanimate-unavailable-resource-notices');
            if (noticesJsonEl && noticesJsonEl.textContent) {
                try {
                    const parsed = JSON.parse(noticesJsonEl.textContent);
                    if (Array.isArray(parsed) && parsed.length > 0) {
                        this.unavailableResourceNotices = parsed;
                        this.showUnavailableSourceWarnings(parsed);
                    }
                } catch (err) {
                    console.warn('[Spark] Could not parse unavailable resource notices:', err);
                }
            }

            // Restore active document page if specified in activeStudySession.context_state
            if (this.activeStudySession && this.activeStudySession.context_state) {
                const activeDocState = this.activeStudySession.context_state.active_document;
                if (activeDocState && activeDocState.pageNumber && this.contextResources.length > 0) {
                    const matchIdx = this.contextResources.findIndex(item =>
                        (activeDocState.documentId && item.documentId === activeDocState.documentId) ||
                        (activeDocState.documentShareId && item.documentShareId === activeDocState.documentShareId) ||
                        (activeDocState.attachmentId && item.attachmentId === activeDocState.attachmentId)
                    );
                    if (matchIdx >= 0) {
                        this.contextResources[matchIdx].pageNumber = activeDocState.pageNumber;
                        this.activeContextIndex = matchIdx;
                        this.syncContextView();
                    }
                }
            }

            this.syncStudyModeUI();
            this.initStudyModal();
        }

        syncStudyModeUI() {
            const toggleBtn = document.getElementById('pwanimateStudyModeToggleBtn');
            if (toggleBtn) {
                const active = Boolean(this.studyModeActive);
                toggleBtn.classList.toggle('active', active);
                toggleBtn.classList.toggle('is-active', active);
                toggleBtn.setAttribute('aria-pressed', active ? 'true' : 'false');
            }
            if (this.workspace) {
                this.workspace.dataset.studyMode = this.studyModeActive ? 'true' : 'false';
                if (this.studySessionId) {
                    this.workspace.dataset.studySessionId = this.studySessionId;
                }
            }
            if (this.input) {
                this.input.placeholder = this.studyModeActive
                    ? 'Ask a study question...'
                    : 'Ask Spark anything...';
            }

            const banner = document.getElementById('pwanimate-study-session-banner');
            if (!banner) return;

            const hasSession = Boolean(this.activeStudySession && this.activeStudySession.id);
            if (!hasSession && !this.studyModeActive) {
                banner.classList.add('d-none');
                return;
            }

            banner.classList.remove('d-none');
            if (hasSession) {
                banner.dataset.studySessionId = this.activeStudySession.id;
                banner.dataset.sessionId = this.activeStudySession.id;
            }

            const session = this.activeStudySession || {};
            const status = (session.status || (this.studyModeActive ? 'active' : 'paused')).toLowerCase();

            const statusBadge = document.getElementById('pwanimateStudyBannerStatusBadge') ||
                document.getElementById('pwanimate-study-status-badge');
            const statusTextEl = document.getElementById('pwanimateStudyBannerStatusText');
            if (statusBadge) {
                statusBadge.classList.remove('status-active', 'status-paused', 'status-completed');
                statusBadge.classList.add(`status-${status}`);
                const shortLabel = status === 'paused' ? 'Paused' : (status === 'completed' ? 'Completed' : 'Study');
                if (statusTextEl) {
                    statusTextEl.textContent = shortLabel;
                } else {
                    statusBadge.innerHTML = `<i data-lucide="graduation-cap"></i><span>${escapeHtml(shortLabel)}</span>`;
                }
            }

            const titleEl = document.getElementById('pwanimateStudyBannerTitle') ||
                document.getElementById('pwanimate-study-topic-label');
            if (titleEl) {
                titleEl.textContent = session.learning_objective ||
                    session.title ||
                    session.current_topic ||
                    'Guided Study Session';
            }

            const topicEl = document.getElementById('pwanimateStudyBannerTopic') ||
                document.getElementById('pwanimate-study-objective-sub');
            if (topicEl) {
                const topText = session.current_topic || '';
                const objText = session.learning_objective || session.title || '';
                if (topText && topText !== objText) {
                    topicEl.textContent = `· ${topText}`;
                    topicEl.classList.remove('d-none');
                } else {
                    topicEl.textContent = '';
                    topicEl.classList.add('d-none');
                }
            }

            const nextStepWrap = document.getElementById('pwanimateStudyBannerNextStep');
            const nextStepText = document.getElementById('pwanimateStudyBannerNextStepText');
            const nextStepVal = session.latest_checkpoint && session.latest_checkpoint.next_step
                ? session.latest_checkpoint.next_step
                : '';
            if (nextStepWrap && nextStepText) {
                if (nextStepVal) {
                    nextStepText.textContent = nextStepVal;
                    nextStepWrap.classList.remove('d-none');
                } else {
                    nextStepText.textContent = '';
                    nextStepWrap.classList.add('d-none');
                }
            }

            const pauseResumeBtn = document.getElementById('pwanimateStudyPauseResumeBtn') ||
                document.getElementById('pwanimate-study-PauseResumeBtn');
            const pauseResumeIcon = document.getElementById('pwanimateStudyPauseResumeIcon');
            const pauseResumeText = document.getElementById('pwanimateStudyPauseResumeText');
            if (pauseResumeBtn) {
                const nextAction = status === 'active' ? 'pause' : 'resume';
                pauseResumeBtn.dataset.action = nextAction;
                pauseResumeBtn.dataset.studyStatusAction = nextAction;
                pauseResumeBtn.title = nextAction === 'pause' ? 'Pause Study Session' : 'Resume Study Session';
                if (pauseResumeIcon && pauseResumeText) {
                    sparkSetIcon(pauseResumeIcon, nextAction === 'pause' ? 'pause' : 'play');
                    pauseResumeText.textContent = nextAction === 'pause' ? 'Pause' : 'Resume';
                } else {
                    pauseResumeBtn.innerHTML = nextAction === 'pause'
                        ? '<i data-lucide="pause"></i><span class="pwanimate-study-btn-label">Pause</span>'
                        : '<i data-lucide="play"></i><span class="pwanimate-study-btn-label">Resume</span>';
                }
                pauseResumeBtn.classList.remove('d-none');
            }

            const endBtn = document.getElementById('pwanimateStudyEndBtn') ||
                document.getElementById('pwanimate-study-end-btn');
            if (endBtn) {
                endBtn.classList.toggle('d-none', status === 'completed');
            }
        }

        showUnavailableSourceWarnings(warnings) {
            if (!Array.isArray(warnings) || warnings.length === 0) return;
            const banner = document.getElementById('pwanimate-unavailable-source-banner');
            const listEl = document.getElementById('pwanimate-unavailable-source-list');
            const textEl = document.getElementById('pwanimate-unavailable-source-text');
            if (!banner) return;
            const formatted = warnings.map(w => {
                if (typeof w === 'object' && w !== null) {
                    return String(w.message || w.title || '').trim();
                }
                return String(w || '').trim();
            }).filter(Boolean);
            const uniqueWarnings = Array.from(new Set(formatted));
            if (!uniqueWarnings.length) return;
            if (listEl) {
                listEl.innerHTML = uniqueWarnings.map(msg => `<li>${escapeHtml(msg)}</li>`).join('');
            } else if (textEl) {
                textEl.textContent = uniqueWarnings.join(' · ');
            }
            banner.classList.remove('d-none');
        }

        updateStudyCommandHint() {
            const hintEl = document.getElementById('pwanimate-study-cmd-hint');
            if (!hintEl || !this.input) return;
            const raw = this.input.value || '';
            const trimmedStart = raw.replace(/^\s+/, '');
            // Show hint only when user is typing a leading @st / @study token (not @studybuddy or mid-sentence)
            const isLeadingStudyPrefix = /^@(?:s|st|stu|stud|study)(?:\s|$)/i.test(trimmedStart);
            hintEl.classList.toggle('d-none', !isLeadingStudyPrefix);
        }

        applyStudyCommandHint() {
            if (!this.input) return;
            const raw = this.input.value || '';
            const match = raw.match(/^\s*@(?:s|st|stu|stud|study)\b\s*(.*)$/i);
            this.studyModeActive = true;
            if (match) {
                const remainder = (match[1] || '').trim();
                this.input.value = remainder ? `@study ${remainder}` : '@study ';
            } else if (!/^\s*@study\b/i.test(raw)) {
                this.input.value = `@study ${raw.trim()}`.trim() + ' ';
            }
            this.syncStudyModeUI();
            this.updateStudyCommandHint();
            this.updateSendButtonState();
            this.input.focus();
        }

        scheduleStudyContextSync() {
            if (!this.studySessionId) return;
            if (this._studyContextSyncTimer) {
                clearTimeout(this._studyContextSyncTimer);
            }
            this._studyContextSyncTimer = setTimeout(() => {
                this._studyContextSyncTimer = null;
                this.syncStudyContextToServer();
            }, 600);
        }

        async syncStudyContextToServer() {
            if (!this.studySessionId) return;
            const activeDoc = this.getActiveStudyDocument();
            const contextResources = this.getContextResourcesForRequest();
            try {
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/`, {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({
                        context_resources: contextResources,
                        active_document: activeDoc || null,
                    }),
                });
                if (!response.ok) return;
                const data = await response.json();
                if (data.study_session) {
                    this.activeStudySession = data.study_session;
                }
                if (Array.isArray(data.warnings) && data.warnings.length > 0) {
                    this.showUnavailableSourceWarnings(data.warnings);
                }
            } catch (err) {
                console.warn('[Spark] Failed to sync study context state:', err);
            }
        }

        async ensureStudySession(extraFields = {}) {
            if (this.studySessionId && this.activeStudySession) {
                return this.activeStudySession;
            }
            try {
                const response = await fetch('/api/pwanimate/study-sessions/', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({
                        conversation_id: this.conversationId || undefined,
                        create_conversation: Boolean(this.conversationId),
                        context_resources: this.getContextResourcesForRequest(),
                        ...extraFields,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok && data.study_session) {
                    this.activeStudySession = data.study_session;
                    this.studySessionId = data.study_session.id;
                    this.studyModeActive = data.study_session.status === 'active';
                    if (data.conversation_id && !this.conversationId) {
                        this.conversationId = data.conversation_id;
                        if (this.workspace) {
                            this.workspace.dataset.conversationId = data.conversation_id;
                        }
                        if (window.history && window.history.replaceState) {
                            window.history.replaceState({}, '', `/pwanimate/${encodeURIComponent(data.conversation_id)}/`);
                        }
                    }
                    this.syncStudyModeUI();
                    this.refreshConversationsList();
                    return data.study_session;
                }
            } catch (err) {
                console.warn('[Spark] Could not initialize study session:', err);
            }
            return null;
        }

        async handleStudySessionStatusAction(action, targetSessionId = null) {
            if (!action) return null;
            let sessionId = targetSessionId || this.studySessionId || (this.activeStudySession && this.activeStudySession.id);
            if (!sessionId && !targetSessionId) {
                if (action === 'end' || action === 'complete') {
                    this.studyModeActive = false;
                    this.syncStudyModeUI();
                    return null;
                }
                const created = await this.ensureStudySession();
                sessionId = created && created.id;
            }
            if (!sessionId) return null;
            try {
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(sessionId)}/status/`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({ action }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || `Could not perform ${action} on study session.`);
                }
                if (data.study_session) {
                    if (!targetSessionId || targetSessionId === this.studySessionId) {
                        this.activeStudySession = data.study_session;
                        this.studySessionId = data.study_session.id;
                        this.studyModeActive = data.study_session.status === 'active';
                        this.syncStudyModeUI();
                    }
                }
                this.refreshConversationsList();
                return data.study_session || null;
            } catch (err) {
                console.error('[Spark] Study session status update error:', err);
                return null;
            }
        }

        initStudyModal() {
            const modalEl = document.getElementById('pwanimateStudyModal');
            if (!modalEl) return;

            modalEl.addEventListener('show.bs.modal', () => {
                this.populateStudyModal();
                this.loadUserCollectionsForModal();
            });
        }

        openStudyModal(tab = 'checkpoint') {
            const modalEl = document.getElementById('pwanimateStudyModal');
            if (!modalEl || !window.bootstrap) return;
            this.populateStudyModal();
            this.loadUserCollectionsForModal();
            const modal = window.bootstrap.Modal.getOrCreateInstance(modalEl);
            modal.show();

            const tabBtn = tab === 'summary'
                ? (document.getElementById('pwanimateStudyTabSummaryBtn') || document.getElementById('pwanimate-study-tab-summary'))
                : (document.getElementById('pwanimateStudyTabCheckpointBtn') || document.getElementById('pwanimate-study-tab-checkpoint'));
            if (tabBtn && window.bootstrap.Tab) {
                const tabInstance = window.bootstrap.Tab.getOrCreateInstance(tabBtn);
                tabInstance.show();
            } else if (tabBtn) {
                tabBtn.click();
            }
        }

        populateStudyModal() {
            const session = this.activeStudySession;
            const topicInput = document.getElementById('pwanimateStudyTopicInput') ||
                document.getElementById('pwanimate-study-modal-topic');
            const objInput = document.getElementById('pwanimateStudyObjectiveInput') ||
                document.getElementById('pwanimate-study-modal-objective');
            if (topicInput) topicInput.value = session ? (session.current_topic || '') : '';
            if (objInput) objInput.value = session ? (session.learning_objective || '') : '';

            this.renderCheckpointInModal(session ? (session.latest_checkpoint || null) : null);

            const lastSummary = session && session.context_state && session.context_state.last_summary
                ? session.context_state.last_summary
                : (session && session.latest_summary ? session.latest_summary : null);
            if (lastSummary) {
                this.populateSummaryInModal(lastSummary);
            }
        }

        renderCheckpointInModal(ckpt) {
            const emptyEl = document.getElementById('pwanimateStudyCheckpointEmpty');
            const detailsEl = document.getElementById('pwanimateStudyCheckpointDetails');
            if (emptyEl && detailsEl) {
                if (!ckpt) {
                    emptyEl.classList.remove('d-none');
                    detailsEl.classList.add('d-none');
                    return;
                }
                emptyEl.classList.add('d-none');
                detailsEl.classList.remove('d-none');

                const renderItems = (items, emptyText) => {
                    if (!Array.isArray(items) || items.length === 0) {
                        return `<li class="text-muted">${escapeHtml(emptyText)}</li>`;
                    }
                    return items.map(item => {
                        if (typeof item === 'object' && item !== null) {
                            const concept = item.concept || item.title || '';
                            const evidence = item.evidence_excerpt || item.evidence || '';
                            return `<li>${escapeHtml(concept)}${evidence ? ` — <em class="text-muted">"${escapeHtml(evidence)}"</em>` : ''}</li>`;
                        }
                        return `<li>${escapeHtml(String(item))}</li>`;
                    }).join('');
                };

                const demEl = document.getElementById('pwanimateCkptDemonstratedList');
                const expEl = document.getElementById('pwanimateCkptExplainedList');
                const misEl = document.getElementById('pwanimateCkptMisconceptionsList');
                const infEl = document.getElementById('pwanimateCkptInferredText');
                const nextEl = document.getElementById('pwanimateCkptNextStepText');

                if (demEl) {
                    demEl.innerHTML = renderItems(
                        ckpt.concepts_demonstrated,
                        'None verified yet — explain a concept or solve a practice question in your own words!'
                    );
                }
                if (expEl) {
                    expEl.innerHTML = renderItems(ckpt.concepts_explained, 'None recorded yet.');
                }
                if (misEl) {
                    misEl.innerHTML = renderItems(ckpt.misconceptions, 'None identified.');
                }
                if (infEl) {
                    const inferred = ckpt.inferred_understanding;
                    if (Array.isArray(inferred)) {
                        infEl.textContent = inferred.length ? inferred.join('; ') : 'No unverified inferences.';
                    } else {
                        infEl.textContent = inferred || ckpt.summary || 'No unverified inferences.';
                    }
                }
                if (nextEl) {
                    nextEl.textContent = ckpt.next_step || 'Continue practicing the current topic.';
                }
                this.syncStudyModeUI();
                return;
            }

            const container = document.getElementById('pwanimate-checkpoint-body');
            if (!container) return;

            if (!ckpt) {
                container.innerHTML = '<div class="text-muted small py-2">No checkpoint generated yet. Click <strong>Update Checkpoint</strong> to capture your current progress, demonstrated concepts, and next steps.</div>';
                return;
            }

            const renderList = (items, emptyText) => {
                if (!Array.isArray(items) || items.length === 0) {
                    return `<li class="text-muted">${escapeHtml(emptyText)}</li>`;
                }
                return items.map(i => `<li>${escapeHtml(String(i))}</li>`).join('');
            };

            const demonstratedItems = Array.isArray(ckpt.concepts_demonstrated) && ckpt.concepts_demonstrated.length > 0
                ? ckpt.concepts_demonstrated.map(item => {
                    const concept = typeof item === 'object' ? (item.concept || '') : String(item);
                    const evidence = typeof item === 'object' && item.evidence_excerpt ? ` — <em class="text-muted">"${escapeHtml(item.evidence_excerpt)}"</em>` : '';
                    return `<li>${escapeHtml(concept)}${evidence}</li>`;
                }).join('')
                : '<li class="text-muted">None verified yet — explain a concept or solve a practice question in your own words!</li>';

            container.innerHTML = `
                <div class="row g-2">
                    <div class="col-12 col-md-6">
                        <div class="p-2 rounded-3 border bg-body-tertiary h-100">
                            <div class="fw-semibold x-small text-uppercase text-primary mb-1"><i data-lucide="book-open" class="me-1"></i>Concepts Explained</div>
                            <ul class="small mb-0 ps-3">${renderList(ckpt.concepts_explained, 'None recorded yet.')}</ul>
                        </div>
                    </div>
                    <div class="col-12 col-md-6">
                        <div class="p-2 rounded-3 border bg-body-tertiary h-100">
                            <div class="fw-semibold x-small text-uppercase text-success mb-1"><i data-lucide="badge-check" class="me-1"></i>Verified Demonstrated Mastery</div>
                            <ul class="small mb-0 ps-3">${demonstratedItems}</ul>
                        </div>
                    </div>
                    <div class="col-12 col-md-6">
                        <div class="p-2 rounded-3 border bg-body-tertiary h-100">
                            <div class="fw-semibold x-small text-uppercase text-warning-emphasis mb-1"><i data-lucide="lightbulb" class="me-1"></i>Inferred Understanding (Unverified)</div>
                            <ul class="small mb-0 ps-3">${renderList(ckpt.inferred_understanding, 'No unverified inferences.')}</ul>
                        </div>
                    </div>
                    <div class="col-12 col-md-6">
                        <div class="p-2 rounded-3 border bg-body-tertiary h-100">
                            <div class="fw-semibold x-small text-uppercase text-danger mb-1"><i data-lucide="circle-help" class="me-1"></i>Open Questions & Misconceptions</div>
                            <ul class="small mb-0 ps-3">${renderList(ckpt.misconceptions, 'None identified.')}</ul>
                        </div>
                    </div>
                    ${ckpt.next_step ? `
                    <div class="col-12">
                        <div class="p-2 rounded-3 border border-primary-subtle bg-primary-subtle text-primary-emphasis small">
                            <strong><i data-lucide="signpost" class="me-1"></i>Recommended Next Step:</strong> ${escapeHtml(ckpt.next_step)}
                        </div>
                    </div>` : ''}
                </div>
            `;
        }

        populateSummaryInModal(summary) {
            if (!summary) return;
            const markdown = typeof summary === 'string'
                ? summary
                : (summary.content || summary.markdown || summary.summary_markdown || '');
            const title = typeof summary === 'object' ? (summary.title || '') : '';
            const titleInput = document.getElementById('pwanimate-summary-title-input');
            const mdInput = document.getElementById('pwanimateStudySummaryTextarea') ||
                document.getElementById('pwanimate-summary-markdown-input');
            const previewEl = document.getElementById('pwanimate-summary-preview');
            if (titleInput && title) titleInput.value = title;
            if (mdInput && markdown) mdInput.value = markdown;
            if (previewEl && markdown) {
                previewEl.innerHTML = this.renderMarkdownWithMathAndCode(markdown);
                previewEl.classList.remove('d-none');
            }
            document.querySelectorAll('.pwanimate-export-summary-btn, #pwanimateExportSummaryPdfBtn, #pwanimateExportSummaryDocxBtn').forEach(btn => {
                btn.disabled = false;
            });
            const saveColBtn = document.getElementById('pwanimateSaveToCollectionBtn') ||
                document.getElementById('pwanimate-save-to-collection-btn');
            if (saveColBtn) saveColBtn.disabled = false;
        }

        async handleSaveStudyMetadata(button) {
            const topicInput = document.getElementById('pwanimateStudyTopicInput') ||
                document.getElementById('pwanimate-study-modal-topic');
            const objInput = document.getElementById('pwanimateStudyObjectiveInput') ||
                document.getElementById('pwanimate-study-modal-objective');
            const currentTopic = topicInput ? topicInput.value.trim() : '';
            const learningObjective = objInput ? objInput.value.trim() : '';

            const originalHtml = button ? button.innerHTML : '';
            if (button) {
                button.disabled = true;
                button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
            }
            try {
                if (!this.studySessionId) {
                    const created = await this.ensureStudySession({
                        current_topic: currentTopic,
                        learning_objective: learningObjective,
                    });
                    if (created && button) {
                        button.innerHTML = '<i data-lucide="check" class="me-1"></i>Saved';
                    }
                    return;
                }
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/`, {
                    method: 'PATCH',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({
                        current_topic: currentTopic,
                        learning_objective: learningObjective,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok && data.study_session) {
                    this.activeStudySession = data.study_session;
                    this.syncStudyModeUI();
                    this.refreshConversationsList();
                    if (button) button.innerHTML = '<i data-lucide="check" class="me-1"></i>Saved';
                }
            } catch (err) {
                console.error('[Spark] Error saving study metadata:', err);
            } finally {
                setTimeout(() => {
                    if (button) {
                        button.disabled = false;
                        button.innerHTML = originalHtml || '<i data-lucide="check" class="me-1"></i>Save Topic & Objective';
                    }
                }, 1400);
            }
        }

        async handleGenerateCheckpoint(button) {
            const originalHtml = button ? button.innerHTML : '';
            if (button) {
                button.disabled = true;
                button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Updating…';
            }
            try {
                if (!this.studySessionId) {
                    await this.ensureStudySession();
                }
                if (!this.studySessionId) return;
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/checkpoint/`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({}),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok) {
                    if (data.study_session) {
                        this.activeStudySession = data.study_session;
                    }
                    if (data.checkpoint) {
                        if (this.activeStudySession) {
                            this.activeStudySession.latest_checkpoint = data.checkpoint;
                        }
                        this.renderCheckpointInModal(data.checkpoint);
                    }
                } else {
                    const emptyEl = document.getElementById('pwanimateStudyCheckpointEmpty');
                    if (emptyEl && data.error) {
                        emptyEl.textContent = data.error;
                        emptyEl.classList.remove('d-none');
                    }
                }
            } catch (err) {
                console.error('[Spark] Checkpoint generation failed:', err);
            } finally {
                if (button) {
                    button.disabled = false;
                    button.innerHTML = originalHtml || '<i data-lucide="refresh-cw" class="me-1"></i>Refresh Checkpoint Now';
                }
            }
        }

        async handleGenerateStudySummary(button) {
            const originalHtml = button ? button.innerHTML : '';
            const statusEl = document.getElementById('pwanimateCollectionSaveFeedback') ||
                document.getElementById('pwanimate-collection-save-status');
            if (button) {
                button.disabled = true;
                button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Generating…';
            }
            try {
                if (!this.studySessionId) {
                    await this.ensureStudySession();
                }
                if (!this.studySessionId) return;
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/summary/`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({ action: 'generate' }),
                });
                const data = await response.json().catch(() => ({}));
                if (response.ok) {
                    if (data.study_session) {
                        this.activeStudySession = data.study_session;
                    }
                    const summaryPayload = data.summary || (data.summary_markdown ? { markdown: data.summary_markdown } : null);
                    if (summaryPayload) {
                        this.populateSummaryInModal(summaryPayload);
                    }
                    if (statusEl) {
                        statusEl.classList.add('d-none');
                    }
                } else if (statusEl && data.error) {
                    statusEl.className = 'small mt-2 text-danger';
                    statusEl.textContent = data.error;
                    statusEl.classList.remove('d-none');
                }
            } catch (err) {
                console.error('[Spark] Summary generation failed:', err);
            } finally {
                if (button) {
                    button.disabled = false;
                    button.innerHTML = originalHtml || '<i data-lucide="sparkles" class="me-1"></i>Generate / Refresh Summary';
                }
            }
        }

        async handleExportStudySummary(button) {
            if (!button) return;
            const fmt = button.dataset.format || (button.id === 'pwanimateExportSummaryDocxBtn' ? 'docx' : 'pdf');
            const titleInput = document.getElementById('pwanimate-summary-title-input');
            const mdInput = document.getElementById('pwanimateStudySummaryTextarea') ||
                document.getElementById('pwanimate-summary-markdown-input');
            const statusEl = document.getElementById('pwanimateCollectionSaveFeedback') ||
                document.getElementById('pwanimate-collection-save-status');
            const originalHtml = button.innerHTML;
            button.disabled = true;
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Exporting…';
            try {
                if (!this.studySessionId) {
                    await this.ensureStudySession();
                }
                if (!this.studySessionId) {
                    throw new Error('Could not initialize study session for export.');
                }
                const markdownText = mdInput ? mdInput.value : '';
                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/summary/`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify({
                        action: 'export',
                        export_format: fmt,
                        format: fmt,
                        title: titleInput ? titleInput.value.trim() : '',
                        summary_markdown: markdownText,
                        markdown: markdownText,
                    }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || 'Could not export study summary.');
                }
                if (data.summary_markdown && mdInput && !mdInput.value.trim()) {
                    mdInput.value = data.summary_markdown;
                }
                const downloadUrl = data.download_url || (data.exported_document && data.exported_document.download_url) || '';
                if (statusEl) {
                    statusEl.className = 'small mt-2 text-success';
                    statusEl.innerHTML = `<i data-lucide="circle-check-big" class="me-1"></i>Exported ${escapeHtml(fmt.toUpperCase())} to My Resources.${downloadUrl ? ` <a href="${escapeHtml(downloadUrl)}" class="fw-semibold" target="_blank" rel="noopener">Download file</a>` : ''}`;
                    statusEl.classList.remove('d-none');
                }
                if (downloadUrl) {
                    window.location.assign(downloadUrl);
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.className = 'small mt-2 text-danger';
                    statusEl.textContent = err.message || 'Failed to export summary.';
                    statusEl.classList.remove('d-none');
                }
            } finally {
                button.disabled = false;
                button.innerHTML = originalHtml;
            }
        }

        async loadUserCollectionsForModal() {
            const selectEl = document.getElementById('pwanimateSummaryCollectionSelect') ||
                document.getElementById('pwanimate-collection-select');
            if (!selectEl) return;
            try {
                const response = await fetch('/api/pwanimate/collections/', {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' },
                });
                if (!response.ok) return;
                const data = await response.json();
                const collections = Array.isArray(data.collections) ? data.collections : [];
                const currentVal = selectEl.value;
                selectEl.innerHTML = '<option value="">— Select existing collection —</option>' + collections.map(col => (
                    `<option value="${escapeHtml(col.id)}">${escapeHtml(col.name)} (${col.item_count || 0} items)</option>`
                )).join('');
                if (currentVal && collections.some(c => String(c.id) === String(currentVal))) {
                    selectEl.value = currentVal;
                } else if (collections.length > 0) {
                    selectEl.value = collections[0].id;
                }
            } catch (err) {
                console.warn('[Spark] Could not load user collections:', err);
            }
        }

        async handleSaveSummaryToCollection(button) {
            const selectEl = document.getElementById('pwanimateSummaryCollectionSelect') ||
                document.getElementById('pwanimate-collection-select');
            const newFormEl = document.getElementById('pwanimate-new-collection-form');
            const newNameInput = document.getElementById('pwanimateNewCollectionNameInput') ||
                document.getElementById('pwanimate-new-collection-name');
            const newDescInput = document.getElementById('pwanimate-new-collection-desc');
            const formatSelect = document.getElementById('pwanimateSummaryCollectionFormat') ||
                document.getElementById('pwanimate-collection-format-select');
            const notesInput = document.getElementById('pwanimateSummaryCollectionNotes') ||
                document.getElementById('pwanimate-collection-item-notes');
            const titleInput = document.getElementById('pwanimate-summary-title-input');
            const mdInput = document.getElementById('pwanimateStudySummaryTextarea') ||
                document.getElementById('pwanimate-summary-markdown-input');
            const statusEl = document.getElementById('pwanimateCollectionSaveFeedback') ||
                document.getElementById('pwanimate-collection-save-status');

            const typedNewName = newNameInput ? newNameInput.value.trim() : '';
            const collectionId = (!typedNewName && selectEl) ? selectEl.value : '';
            const collectionName = typedNewName;

            if (!collectionId && !collectionName) {
                if (statusEl) {
                    statusEl.className = 'small mt-2 text-danger';
                    statusEl.textContent = 'Please select an existing collection or enter a new collection name.';
                    statusEl.classList.remove('d-none');
                }
                return;
            }

            const originalHtml = button ? button.innerHTML : '';
            if (button) {
                button.disabled = true;
                button.innerHTML = '<span class="spinner-border spinner-border-sm me-1"></span>Saving…';
            }

            try {
                if (!this.studySessionId) {
                    await this.ensureStudySession();
                }
                if (!this.studySessionId) {
                    throw new Error('Could not initialize study session.');
                }
                const fmt = formatSelect ? formatSelect.value : 'pdf';
                const markdownText = mdInput ? mdInput.value : '';
                const payload = {
                    file_format: fmt,
                    export_format: fmt,
                    title: titleInput ? titleInput.value.trim() : '',
                    summary_markdown: markdownText,
                    markdown: markdownText,
                    notes: notesInput ? notesInput.value.trim() : '',
                };
                if (collectionId) {
                    payload.collection_id = collectionId;
                } else {
                    payload.new_collection_name = collectionName;
                    payload.collection_name = collectionName;
                    payload.collection_description = newDescInput ? newDescInput.value.trim() : '';
                }

                const response = await fetch(`/api/pwanimate/study-sessions/${encodeURIComponent(this.studySessionId)}/summary/save-to-collection/`, {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    body: JSON.stringify(payload),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    throw new Error(data.error || 'Could not save summary to collection.');
                }
                if (statusEl) {
                    const colName = data.collection ? data.collection.name : (collectionName || 'collection');
                    statusEl.className = 'small mt-2 text-success';
                    statusEl.innerHTML = `<i data-lucide="bookmark-check" class="me-1"></i>Saved to collection <strong>${escapeHtml(colName)}</strong>!`;
                    statusEl.classList.remove('d-none');
                }
                if (newFormEl) newFormEl.classList.add('d-none');
                if (newNameInput) newNameInput.value = '';
                if (newDescInput) newDescInput.value = '';
                await this.loadUserCollectionsForModal();
                if (data.collection && selectEl) {
                    selectEl.value = data.collection.id;
                }
            } catch (err) {
                if (statusEl) {
                    statusEl.className = 'small mt-2 text-danger';
                    statusEl.textContent = err.message || 'Failed to save to collection.';
                    statusEl.classList.remove('d-none');
                }
            } finally {
                if (button) {
                    button.disabled = false;
                    button.innerHTML = originalHtml || '<i data-lucide="bookmark-plus" class="me-1"></i>Save to Collection';
                }
            }
        }

        addImageAttachmentsToContext(root, openRail = true) {
            if (!root || !root.querySelectorAll) return false;
            let added = false;
            root.querySelectorAll('.pwanimate-message-attachment[data-preview-type="image"], .pwanimate-message-attachment[data-preview-type="document"], .pwanimate-message-attachment[data-preview-type="pdf"]').forEach((link) => {
                const url = link.dataset.mediaUrl || link.getAttribute('href') || '';
                if (!url) return;
                const attachmentId = link.dataset.attachmentId || '';
                const exists = this.contextResources.some(item =>
                    (attachmentId && item.attachmentId === attachmentId) ||
                    item.mediaUrl === url || item.url === url
                );
                if (exists) return;
                const previewType = (link.dataset.previewType || 'document').toLowerCase();
                const isImage = previewType === 'image';
                const name = link.dataset.fileName || (isImage ? 'Image attachment' : 'Document attachment');
                const fileType = (link.dataset.fileType || (name.includes('.') ? name.split('.').pop() : (isImage ? 'jpg' : 'pdf'))).toLowerCase();
                const resource = this.normalizeResource(url, name, {
                    sourceType: 'attachment',
                    resourceType: isImage ? 'image' : 'document',
                    fileType: fileType,
                    mediaUrl: url,
                    attachmentId: attachmentId,
                    pageNumber: isImage ? null : 1,
                });
                resource.icon = isImage ? 'bi-image-fill' : 'bi-file-earmark-pdf-fill';
                this.contextResources.push(resource);
                added = true;
            });

            if (added) {
                this.activeContextIndex = this.contextResources.length - 1;
                this.updateContextCountBadges();
                this.updateAddToContextButtons();
                this.syncContextView();
                this.syncDocumentStudyUI();
                this.switchWorkspaceTab('context');
                if (openRail) {
                    if (window.innerWidth >= 1200) this.openContextRail();
                    else this.openMobileWorkspaceSheet('context');
                }
            }
            return added;
        }

        initContextDocumentPicker() {
            this.contextDocumentModalEl = document.getElementById('pwanimateContextDocumentModal');
            this.contextDocumentSearchInput = document.getElementById('pwanimateContextDocumentSearch');
            this.contextDocumentResultsEl = document.getElementById('pwanimateContextDocumentResults');
            this.contextDocumentSearchResults = [];
            this.contextDocumentSearchTimer = null;
            this.contextDocumentSearchController = null;

            if (!this.contextDocumentSearchInput || !this.contextDocumentResultsEl) return;
            this.contextDocumentSearchInput.addEventListener('input', () => {
                clearTimeout(this.contextDocumentSearchTimer);
                const query = this.contextDocumentSearchInput.value.trim();
                if (this.contextDocumentSearchController) this.contextDocumentSearchController.abort();
                if (query.length === 0) {
                    this.contextDocumentSearchTimer = setTimeout(() => this.searchContextDocuments('', true), 120);
                    return;
                }
                if (query.length < 2) {
                    this.contextDocumentSearchResults = [];
                    this.contextDocumentResultsEl.innerHTML = '<div class="text-center text-muted small py-4">Type at least two characters to search the repository.</div>';
                    return;
                }
                this.contextDocumentResultsEl.innerHTML = '<div class="text-center text-muted small py-4"><span class="spinner-border spinner-border-sm me-2" role="status"></span>Searching books…</div>';
                this.contextDocumentSearchTimer = setTimeout(() => this.searchContextDocuments(query, false), 250);
            });

            this.contextDocumentResultsEl.addEventListener('click', (event) => {
                const card = event.target.closest('[data-context-document-index]');
                if (!card) return;
                const index = Number.parseInt(card.dataset.contextDocumentIndex, 10);
                const result = this.contextDocumentSearchResults[index];
                if (!result) return;

                const unitCitation = result.unit_code
                    ? (result.unit_name ? `${result.unit_code} — ${result.unit_name}` : result.unit_code)
                    : result.category;
                const resource = this.normalizeResource(result.media_url, result.title, {
                    sourceType: 'document',
                    resourceType: 'document',
                    documentId: result.document_id,
                    documentShareId: result.document_share_id,
                    documentVersionId: result.document_version_id,
                    fileId: result.file_id,
                    fileType: result.file_type,
                    mediaUrl: result.media_url,
                    thumbnailUrl: result.thumbnail_url,
                    citation: unitCitation,
                    unitCode: result.unit_code || '',
                    unitName: result.unit_name || '',
                    pageNumber: 1,
                });
                const modal = window.bootstrap && this.contextDocumentModalEl
                    ? window.bootstrap.Modal.getInstance(this.contextDocumentModalEl)
                    : null;
                if (modal) modal.hide();
                this.addResourceToContext(resource);
                if (window.innerWidth >= 1200) {
                    this.openContextRail();
                }
            });
        }

        async searchContextDocuments(query, recommended = false) {
            if (!this.contextDocumentResultsEl) return;
            const controller = new AbortController();
            this.contextDocumentSearchController = controller;
            try {
                const url = recommended && !query
                    ? '/api/pwanimate/context/documents/?recommended=1'
                    : `/api/pwanimate/context/documents/?q=${encodeURIComponent(query)}`;
                const response = await fetch(url, {
                    credentials: 'same-origin',
                    headers: { 'Accept': 'application/json' },
                    signal: controller.signal,
                });
                const data = await response.json();
                if (!response.ok) throw new Error(data.error || 'Could not search the document repository.');
                if (!recommended && this.contextDocumentSearchInput.value.trim() !== query) return;
                this.contextDocumentSearchResults = Array.isArray(data.results) ? data.results : [];
                if (!this.contextDocumentSearchResults.length) {
                    this.contextDocumentResultsEl.innerHTML = recommended
                        ? '<div class="text-center text-muted small py-4">Type at least two characters to search books by title, topic, or unit code.</div>'
                        : '<div class="text-center text-muted small py-4">No books found. Try another title, unit code, or topic.</div>';
                    return;
                }
                this.contextDocumentResultsEl.innerHTML = this.contextDocumentSearchResults.map((book, index) => {
                    const title = escapeHtml(book.title || 'Untitled book');
                    const category = escapeHtml(book.category || 'Document');
                    const unitBadge = book.unit_code ? `${escapeHtml(book.unit_code)} · ` : '';
                    const fileType = escapeHtml((book.file_type || 'file').toUpperCase());
                    const thumbnail = book.thumbnail_url
                        ? `<img src="${escapeHtml(book.thumbnail_url)}" alt="" class="pwanimate-context-book-thumbnail">`
                        : '<span class="pwanimate-context-book-icon"><i data-lucide="book-marked"></i></span>';
                    return `<button type="button" class="pwanimate-context-book-result" data-context-document-index="${index}">
                        ${thumbnail}
                        <span class="pwanimate-context-book-copy"><strong>${title}</strong><small>${unitBadge}${category} · ${fileType}</small></span>
                        <i data-lucide="download" class="pwanimate-context-book-offline" data-offline-save-doc="${escapeHtml(book.document_share_id || book.document_id || '')}" title="Save for offline" role="button" aria-label="Save ${title} for offline"></i>
                        <i data-lucide="arrow-up-right" class="pwanimate-context-book-open"></i>
                    </button>`;
                }).join('');
            } catch (error) {
                if (error.name === 'AbortError') return;
                this.contextDocumentResultsEl.innerHTML = '<div class="text-center text-danger small py-4">Could not search the repository. Try again in a moment.</div>';
            }
        }

        openContextDocumentPicker() {
            if (!this.contextDocumentModalEl || !window.bootstrap) return;
            const modal = window.bootstrap.Modal.getOrCreateInstance(this.contextDocumentModalEl);
            modal.show();
            if (this.contextDocumentSearchInput) {
                this.contextDocumentSearchInput.value = '';
                this.contextDocumentSearchInput.dispatchEvent(new Event('input'));
                window.setTimeout(() => this.contextDocumentSearchInput.focus(), 150);
            }
        }

        async initEmptyStateWelcome() {
            const state = this.transcript && this.transcript.querySelector('#pwanimate-empty-state');
            if (!state) return;
            const greeting = state.querySelector('#pwanimate-empty-greeting');
            const question = state.querySelector('#pwanimate-empty-question');
            const suggestions = state.querySelector('#pwanimate-empty-suggestions');
            if (!greeting || !question || !suggestions) return;
            greeting.textContent = 'Hello' + (state.dataset.nickname || state.dataset.username ? ', ' + (state.dataset.nickname || state.dataset.username) : '') + '.';
            question.textContent = 'What would you like to work on today?';
            suggestions.replaceChildren();
            try {
                const response = await fetch('/api/pwanimate/memory/welcome/', {credentials: 'same-origin', cache: 'no-store'});
                if (!response.ok) return;
                const data = await response.json();
                if (!state.isConnected) return;
                greeting.textContent = data.greeting;
                question.textContent = data.question;
                (data.suggestions || []).forEach(item => {
                    const button = document.createElement('button');
                    button.type = 'button';
                    button.className = 'pwanimate-suggestion-chip';
                    button.dataset.prompt = item.prompt;
                    button.textContent = item.label;
                    suggestions.appendChild(button);
                });
            } catch (_) { /* Neutral welcome stays usable when offline. */ }
        }

        initMarkdown() {
            if (window.marked && typeof window.marked.use === 'function') {
                window.marked.use({
                    breaks: true,
                    gfm: true
                });
            }
        }

        renderExistingMarkdown() {
            if (!this.transcript) return;
            const parseElements = () => {
                const mdElements = this.transcript.querySelectorAll('.pwanimate-markdown-body[data-raw]');
                mdElements.forEach((el) => {
                    const raw = el.getAttribute('data-raw');
                    if (raw) {
                        try {
                            el.innerHTML = this.renderMarkdownWithMathAndCode(raw);
                            this.postProcessMarkdownDOM(el);
                            this.enhanceWebSources(el.closest('.pwanimate-message-row'));
                        } catch (e) {
                            console.warn('[Spark] Markdown parse error:', e);
                        }
                    }
                });
            };

            if (window.marked) {
                parseElements();
            } else {
                let attempts = 0;
                const checkMarked = setInterval(() => {
                    attempts++;
                    if (window.marked) {
                        clearInterval(checkMarked);
                        this.initMarkdown();
                        parseElements();
                    } else if (attempts > 30) {
                        clearInterval(checkMarked);
                    }
                }, 50);
            }
        }

        sanitizeToolCallTokens(text) {
            if (!text || typeof text !== 'string') return '';

            const parseToolBlock = (block) => {
                let cleaned = block.trim();
                if (cleaned.startsWith('[') && cleaned.endsWith(']')) {
                    cleaned = cleaned.slice(1, -1).trim();
                }
                const funcMatch = cleaned.match(/^([a-zA-Z0-9_]+)\s*\(([\s\S]*)\)\s*$/);
                if (!funcMatch) return block;

                const funcName = funcMatch[1];
                const argsStr = funcMatch[2].trim();

                if (funcName === 'write') {
                    const pathMatch = argsStr.match(/path=(['"])(.*?)\1/);
                    const path = pathMatch ? pathMatch[2] : '';
                    const ext = path.includes('.') ? path.split('.').pop().toLowerCase() : 'python';
                    const langMap = {
                        py: 'python',
                        js: 'javascript',
                        ts: 'typescript',
                        sh: 'bash',
                        sql: 'sql',
                        json: 'json',
                        html: 'html',
                        css: 'css'
                    };
                    const lang = langMap[ext] || ext || 'python';

                    let content = '';
                    const contentMatch = argsStr.match(/content=(['"])([\s\S]*)/);
                    if (contentMatch) {
                        const quote = contentMatch[1];
                        content = contentMatch[2];
                        content = content.replace(new RegExp(`${quote}\\s*\\)?\\s*$`), '');
                    } else {
                        content = argsStr;
                    }

                    try {
                        content = content
                            .replace(/\\n/g, '\n')
                            .replace(/\\t/g, '\t')
                            .replace(/\\r/g, '\r')
                            .replace(/\\"/g, '"')
                            .replace(/\\'/g, "'")
                            .replace(/\\\\/g, '\\');
                    } catch (e) {}

                    const fileHeader = path ? `### \`${path}\`\n\n` : '';
                    return `${fileHeader}\`\`\`${lang}\n${content.trim()}\n\`\`\``;
                }
                return block;
            };

            let res = text.replace(/<\|tool_call_start\|>([\s\S]*?)<\|tool_call_end\|>/g, (match, inner) => {
                return '\n\n' + parseToolBlock(inner) + '\n\n';
            });

            res = res.replace(/\[write\(\s*(?:path=['"][^'"]+['"]\s*,\s*)?content=['"][\s\S]*?['"]\s*\)\]/g, (match) => {
                return '\n\n' + parseToolBlock(match) + '\n\n';
            });

            res = res.replace(/<\/?(?:\|tool_call_start\||\|tool_call_end\||tool_call)>/g, '');
            return res.trim();
        }

        renderMarkdownWithMathAndCode(content) {
            if (!content) return '';

            // 0. Transform accidental synthetic tool calls into Markdown
            let processed = content;
            if (processed.includes('tool_call') || processed.includes('[write(') || processed.includes('write(path=')) {
                processed = this.sanitizeToolCallTokens(processed);
            }

            const mathPlaceholders = [];
            const storeMath = (mathCode, displayMode) => {
                const id = `PWANIMATE_MATH_PH_${mathPlaceholders.length}_XYZ`;
                mathPlaceholders.push({ id, code: mathCode, displayMode });
                return id;
            };

            // 1. Block math: $$ ... $$ and \[ ... \]
            processed = processed.replace(/\$\$([\s\S]*?)\$\$/g, (match, code) => {
                return '\n\n' + storeMath(code, true) + '\n\n';
            });
            processed = processed.replace(/\\\[([\s\S]*?)\\\]/g, (match, code) => {
                return '\n\n' + storeMath(code, true) + '\n\n';
            });

            // 2. Inline math: \( ... \) and $ ... $ (avoiding escaped \$ and empty/spaced dollars)
            processed = processed.replace(/\\\(([\s\S]*?)\\\)/g, (match, code) => {
                return storeMath(code, false);
            });
            processed = processed.replace(/(?<!\\)\$([^\$\n]+?)(?<!\\)\$/g, (match, code) => {
                if (!code.trim()) return match;
                return storeMath(code, false);
            });

            // 3. Marked parse
            let html = '';
            if (window.marked && typeof window.marked.parse === 'function') {
                try {
                    html = window.marked.parse(processed);
                } catch (e) {
                    console.warn('[Spark] Marked parse error:', e);
                    html = escapeHtml(content);
                    return html;
                }
            } else {
                html = escapeHtml(content);
            }

            // 4. Sanitize with DOMPurify
            if (window.DOMPurify && typeof window.DOMPurify.sanitize === 'function') {
                html = window.DOMPurify.sanitize(html, {
                    ADD_TAGS: ['annotation', 'math', 'semantics', 'mrow', 'mi', 'mn', 'mo', 'msup', 'msub', 'mfrac', 'mover', 'munder', 'msqrt', 'mroot', 'mtd', 'mtr', 'mtable', 'mtext', 'mspace'],
                    ADD_ATTR: ['xmlns', 'display', 'displaystyle', 'mathvariant', 'columnalign', 'rowalign', 'linethickness']
                });
            }

            // 5. Replace math placeholders with KaTeX rendered HTML
            if (window.katex && typeof window.katex.renderToString === 'function') {
                mathPlaceholders.forEach(({ id, code, displayMode }) => {
                    try {
                        const decodedCode = code.replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>').replace(/&quot;/g, '"').replace(/&#39;/g, "'");
                        const renderedMath = window.katex.renderToString(decodedCode, {
                            displayMode: displayMode,
                            throwOnError: false,
                            output: 'htmlAndMathml'
                        });
                        html = html.split(id).join(renderedMath);
                    } catch (err) {
                        console.warn('[Spark] KaTeX render error:', err);
                        const fallback = displayMode ? `<pre class="katex-fallback">${escapeHtml(code)}</pre>` : `<code>${escapeHtml(code)}</code>`;
                        html = html.split(id).join(fallback);
                    }
                });
            } else {
                mathPlaceholders.forEach(({ id, code, displayMode }) => {
                    const fallback = displayMode ? `<pre class="katex-fallback">${escapeHtml(code)}</pre>` : `<code>${escapeHtml(code)}</code>`;
                    html = html.split(id).join(fallback);
                });
            }

            return html;
        }

        getSafeExternalUrl(value) {
            if (typeof value !== 'string' || !value.trim()) return '';
            try {
                const url = new URL(value.trim());
                if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password) {
                    return '';
                }
                return url.href;
            } catch (error) {
                return '';
            }
        }

        getWebSourceIndex(source) {
            const direct = Number(source.webCitationIndex || source.web_citation_index || '');
            if (Number.isInteger(direct) && direct > 0) return direct;
            const citation = String(source.citation || '');
            const match = citation.match(/\[Web\s+(\d+)/i);
            return match ? Number(match[1]) : null;
        }

        createWebFavicon(domain, sourceUrl) {
            const frame = document.createElement('span');
            frame.className = 'pwanimate-web-favicon';
            frame.setAttribute('aria-hidden', 'true');

            const image = document.createElement('img');
            image.alt = '';
            image.loading = 'lazy';
            image.decoding = 'async';
            image.referrerPolicy = 'no-referrer';
            try {
                const parsedUrl = new URL(sourceUrl);
                image.src = `${parsedUrl.origin}/favicon.ico`;
            } catch (error) {
                image.hidden = true;
            }

            const fallback = document.createElement('span');
            fallback.className = 'pwanimate-web-favicon-fallback';
            fallback.textContent = (domain || 'W').charAt(0).toUpperCase();
            fallback.hidden = !image.hidden;
            image.addEventListener('error', () => {
                image.hidden = true;
                fallback.hidden = false;
            }, { once: true });

            frame.append(image, fallback);
            return frame;
        }

        createWebSourceLink(source, compact = false) {
            const url = this.getSafeExternalUrl(source.url || source.dataset?.url || '');
            if (!url) return null;

            let domain = '';
            try {
                domain = new URL(url).hostname.replace(/^www\./i, '');
            } catch (error) {}
            const title = String(source.title || source.dataset?.title || domain || 'Web source');
            const index = this.getWebSourceIndex(source);
            const link = document.createElement('a');
            link.className = compact ? 'pwanimate-inline-web-citation' : 'pwanimate-web-source-badge';
            link.href = url;
            link.target = '_blank';
            link.rel = 'noopener noreferrer';
            link.dataset.url = url;
            link.dataset.sourceType = 'web';
            link.dataset.resourceType = 'web_search';
            link.dataset.domain = domain;
            if (index) link.dataset.webCitationIndex = String(index);
            link.setAttribute('aria-label', `Open ${title} on ${domain}`);
            link.title = `${title} · ${domain}`;
            link.appendChild(this.createWebFavicon(domain, url));

            if (compact) {
                const label = document.createElement('span');
                label.className = 'pwanimate-inline-web-domain';
                label.textContent = domain;
                link.appendChild(label);
                return link;
            }

            const copy = document.createElement('span');
            copy.className = 'pwanimate-web-source-copy';
            const titleEl = document.createElement('span');
            titleEl.className = 'pwanimate-web-source-title';
            titleEl.textContent = title;
            const domainEl = document.createElement('span');
            domainEl.className = 'pwanimate-web-source-domain';
            domainEl.textContent = domain;
            copy.append(titleEl, domainEl);
            link.append(copy);

            const externalIcon = document.createElement('i');
            externalIcon.setAttribute('data-lucide', 'external-link');
            externalIcon.className = 'pwanimate-web-source-external';
            externalIcon.setAttribute('aria-hidden', 'true');
            link.appendChild(externalIcon);
            return link;
        }

        enhanceWebSources(row) {
            if (!row) return;

            row.querySelectorAll('.pwanimate-citation-badge[data-source-type="web"]').forEach((badge) => {
                const source = {
                    url: badge.dataset.url || badge.getAttribute('href') || '',
                    title: badge.dataset.title || badge.textContent.trim(),
                    citation: badge.dataset.citation || '',
                    domain: badge.dataset.domain || '',
                    web_citation_index: badge.dataset.webCitationIndex || '',
                };
                const link = this.createWebSourceLink(source, false);
                if (link) badge.replaceWith(link);
            });

            const sourcesByIndex = new Map();
            row.querySelectorAll('.pwanimate-web-source-badge[data-web-citation-index]').forEach((link) => {
                const index = Number(link.dataset.webCitationIndex);
                if (Number.isInteger(index) && index > 0 && link.dataset.url) {
                    sourcesByIndex.set(index, {
                        url: link.dataset.url,
                        title: link.querySelector('.pwanimate-web-source-title')?.textContent || '',
                        citation: link.dataset.citation || '',
                        web_citation_index: index,
                    });
                }
            });

            const markdown = row.querySelector('.pwanimate-markdown-body');
            if (!markdown || !sourcesByIndex.size) return;
            const walker = document.createTreeWalker(markdown, NodeFilter.SHOW_TEXT);
            const textNodes = [];
            while (walker.nextNode()) textNodes.push(walker.currentNode);

            const citationPattern = /\[Web\s+(\d+)(?:\s*:[^\]]*)?\]/gi;
            textNodes.forEach((textNode) => {
                const parent = textNode.parentElement;
                if (!parent || parent.closest('a, button, code, pre, script, style, .katex')) return;

                const content = textNode.nodeValue || '';
                citationPattern.lastIndex = 0;
                let match;
                let lastIndex = 0;
                let replaced = false;
                const fragment = document.createDocumentFragment();
                while ((match = citationPattern.exec(content))) {
                    const source = sourcesByIndex.get(Number(match[1]));
                    if (!source) continue;
                    if (match.index > lastIndex) {
                        fragment.appendChild(document.createTextNode(content.slice(lastIndex, match.index)));
                    }
                    const link = this.createWebSourceLink(source, true);
                    if (!link) continue;
                    fragment.appendChild(link);
                    lastIndex = match.index + match[0].length;
                    replaced = true;
                }
                if (replaced) {
                    if (lastIndex < content.length) {
                        fragment.appendChild(document.createTextNode(content.slice(lastIndex)));
                    }
                    textNode.parentNode.replaceChild(fragment, textNode);
                }
            });
        }

        postProcessMarkdownDOM(container) {
            if (!container) return;

            // 1. Wrap tables in responsive scroll wrapper
            const tables = container.querySelectorAll('table');
            tables.forEach(table => {
                if (!table.parentElement.classList.contains('pwanimate-table-scroll') &&
                    !table.parentElement.classList.contains('pwanimate-table-wrapper')) {
                    const wrapper = document.createElement('div');
                    wrapper.className = 'pwanimate-table-scroll pwanimate-table-wrapper';
                    table.parentNode.insertBefore(wrapper, table);
                    wrapper.appendChild(table);
                }
            });

            // 2. Enhance code blocks: Prism highlighting + Header with Language badge & Copy button
            const preBlocks = container.querySelectorAll('pre');
            preBlocks.forEach(pre => {
                if (pre.closest('.pwanimate-code-block') || pre.classList.contains('katex-fallback')) return;

                const code = pre.querySelector('code');
                let lang = 'code';
                if (code) {
                    const classMatch = (code.className || '').match(/language-([a-zA-Z0-9_\-#+]+)/);
                    if (classMatch) {
                        lang = classMatch[1];
                    }
                }

                const wrapper = document.createElement('div');
                wrapper.className = 'pwanimate-code-block';

                const header = document.createElement('div');
                header.className = 'pwanimate-code-header';

                const langSpan = document.createElement('span');
                langSpan.className = 'pwanimate-code-lang';
                langSpan.textContent = lang;

                const copyBtn = document.createElement('button');
                copyBtn.type = 'button';
                copyBtn.className = 'pwanimate-code-copy-btn';
                copyBtn.setAttribute('aria-label', 'Copy code');
                copyBtn.innerHTML = '<i data-lucide="copy"></i> <span>Copy</span>';

                header.appendChild(langSpan);
                header.appendChild(copyBtn);

                pre.parentNode.insertBefore(wrapper, pre);
                wrapper.appendChild(header);
                wrapper.appendChild(pre);

                if (code && window.Prism && typeof window.Prism.highlightElement === 'function') {
                    try {
                        window.Prism.highlightElement(code);
                    } catch (e) {
                        console.warn('[Spark] Prism highlight error:', e);
                    }
                }
            });
        }

        checkCollapsibleUserBubbles(container = this.transcript) {
            if (!container) return;
            const bubbles = container.querySelectorAll('.pwanimate-user-bubble');
            bubbles.forEach(bubble => {
                if (bubble.dataset.collapsibleChecked === 'true') return;

                if (bubble.scrollHeight > 220) {
                    bubble.classList.add('is-collapsible');
                    bubble.dataset.collapsibleChecked = 'true';

                    const wrapper = bubble.closest('.pwanimate-user-bubble-wrapper') || bubble.parentElement;
                    if (wrapper && !wrapper.querySelector('.pwanimate-show-more-btn')) {
                        const btn = document.createElement('button');
                        btn.type = 'button';
                        btn.className = 'pwanimate-show-more-btn';
                        btn.setAttribute('aria-expanded', 'false');
                        btn.innerHTML = '<i data-lucide="chevron-down"></i> <span>Show more</span>';
                        wrapper.appendChild(btn);
                    }
                }
            });
        }

        copyToClipboard(text, btnElement = null) {
            if (!text) return Promise.resolve(false);

            const showSuccess = () => {
                if (!btnElement) return;
                const originalHtml = btnElement.innerHTML;
                btnElement.classList.add('copied');
                btnElement.innerHTML = btnElement.classList.contains('pwanimate-code-copy-btn')
                    ? '<i data-lucide="check" class="text-success"></i> <span>Copied!</span>'
                    : '<i data-lucide="check" class="text-success"></i>';
                setTimeout(() => {
                    btnElement.classList.remove('copied');
                    btnElement.innerHTML = originalHtml;
                }, 2000);
            };

            if (navigator.clipboard && window.isSecureContext) {
                return navigator.clipboard.writeText(text).then(() => {
                    showSuccess();
                    return true;
                }).catch(err => {
                    console.warn('[Spark] Clipboard API error, falling back:', err);
                    return this.fallbackCopyText(text, showSuccess);
                });
            } else {
                return Promise.resolve(this.fallbackCopyText(text, showSuccess));
            }
        }

        fallbackCopyText(text, onSuccess) {
            try {
                const textarea = document.createElement('textarea');
                textarea.value = text;
                textarea.style.position = 'fixed';
                textarea.style.left = '-9999px';
                textarea.style.top = '0';
                textarea.style.opacity = '0';
                document.body.appendChild(textarea);
                textarea.focus();
                textarea.select();
                const successful = document.execCommand('copy');
                document.body.removeChild(textarea);
                if (successful && onSuccess) onSuccess();
                return successful;
            } catch (e) {
                console.warn('[Spark] Fallback copy failed:', e);
                return false;
            }
        }

        bindEvents() {
            // Form submission
            if (this.composerForm) {
                this.composerForm.addEventListener('submit', (e) => {
                    e.preventDefault();
                    if (this.isGenerating) {
                        this.handleAbort();
                    } else {
                        this.handleSend();
                    }
                });
            }

            // Direct Send / Stop button click handling
            if (this.sendBtn) {
                this.sendBtn.addEventListener('click', (e) => {
                    e.preventDefault();
                    if (this.isGenerating) {
                        this.handleAbort();
                    } else {
                        this.handleSend();
                    }
                });
            }

            // Global Escape shortcut to abort generation
            this._onKeyDown = (e) => {
                if (e.key === 'Escape' && this.isGenerating) {
                    e.preventDefault();
                    this.handleAbort();
                }
            };
            document.addEventListener('keydown', this._onKeyDown);

            // Keyboard navigation in textarea
            if (this.input) {
                this.input.addEventListener('keydown', (e) => {
                    if (e.isComposing || e.keyCode === 229) {
                        return;
                    }
                    if (e.key === 'Tab') {
                        const hintEl = document.getElementById('pwanimate-study-cmd-hint');
                        if (hintEl && !hintEl.classList.contains('d-none')) {
                            e.preventDefault();
                            this.applyStudyCommandHint();
                            return;
                        }
                    }
                    if (e.key === 'Enter' && !e.shiftKey) {
                        if (isFinePointerDevice()) {
                            e.preventDefault();
                            if (this.isGenerating) {
                                this.handleAbort();
                            } else {
                                this.handleSend();
                            }
                        }
                        // On touch/coarse devices, do not preventDefault: native newline is inserted
                    }
                });

                // Auto-resize textarea with rAF batching and synchronize send button state
                this.input.addEventListener('input', () => {
                    this.scheduleTextareaResize();
                    this.updateSendButtonState();
                    this.updateStudyCommandHint();
                });
            }

            // Transcript delegated actions (Suggestion chips, Citations, Copy, Edit, Show more)
            if (this.transcript) {
                this.transcript.addEventListener('click', (e) => {
                    const resourceDownload = e.target.closest('.pwanimate-resource-download');
                    if (resourceDownload) {
                        e.preventDefault();
                        this.downloadGeneratedResource(resourceDownload);
                        return;
                    }

                    const chip = e.target.closest('.pwanimate-suggestion-chip');
                    if (chip && chip.dataset.prompt) {
                        e.preventDefault();
                        if (this.input) {
                            this.input.value = chip.dataset.prompt;
                            this.updateSendButtonState();
                            this.handleSend();
                        }
                        return;
                    }

                    const personPreviewBtn = e.target.closest('.pwanimate-person-preview-btn');
                    if (personPreviewBtn) {
                        e.preventDefault();
                        let person = {};
                        try { person = JSON.parse(personPreviewBtn.dataset.person || '{}'); } catch (error) {}
                        if (!person.username) {
                            person = {
                                username: personPreviewBtn.dataset.username || '',
                                display_name: personPreviewBtn.dataset.displayName || '',
                                profile_url: personPreviewBtn.dataset.profileUrl || '',
                                profile_card_url: personPreviewBtn.dataset.profileCardUrl || '',
                                avatar_url: personPreviewBtn.dataset.avatarUrl || '',
                                headline: personPreviewBtn.dataset.headline || '',
                                academic_level: personPreviewBtn.dataset.academicLevel || '',
                                programme_name: personPreviewBtn.dataset.programmeName || '',
                                bio: personPreviewBtn.dataset.bio || '',
                            };
                        }
                        const profileUrl = personPreviewBtn.dataset.profileUrl || person.profile_url || '';
                        this.previewResource(profileUrl, person.display_name || person.username || 'Student profile', {
                            sourceType: 'user',
                            resourceType: 'profile',
                            username: person.username || '',
                            person: person,
                            profileUrl: profileUrl,
                            profileCardUrl: person.profile_card_url || person.profileCardUrl || '',
                            description: person.headline || person.bio || '',
                        });
                        return;
                    }

                    const messageAttachment = e.target.closest('.pwanimate-message-attachment');
                    if (messageAttachment) {
                        e.preventDefault();
                        const name = messageAttachment.dataset.fileName || 'Attachment';
                        const attachmentUrl = messageAttachment.dataset.mediaUrl || messageAttachment.getAttribute('href') || '';
                        this.previewResource(attachmentUrl, name, {
                            sourceType: 'attachment',
                            resourceType: messageAttachment.dataset.previewType || 'document',
                            fileType: messageAttachment.dataset.fileType || '',
                            mediaUrl: attachmentUrl,
                            attachmentId: messageAttachment.dataset.attachmentId || '',
                        });
                        return;
                    }

                    const badge = e.target.closest('a, button');
                    if (badge && isSparkCitationLink(badge)) {
                        if ((badge.dataset.sourceType || '').toLowerCase() === 'web') {
                            const webUrl = this.getSafeExternalUrl(badge.dataset.url || badge.getAttribute('href'));
                            if (!webUrl) {
                                e.preventDefault();
                                return;
                            }
                            const bridge = window.AndroidBridge || window.pwaninetBridge;
                            if (bridge && typeof bridge.openExternalUrl === 'function') {
                                e.preventDefault();
                                bridge.openExternalUrl(webUrl);
                            }
                            return;
                        }

                        e.preventDefault();
                        const url = badge.getAttribute('href') || badge.dataset.url || '';
                        let title = badge.dataset.title || 'Resource Preview';
                        if (title === 'Resource Preview') {
                            const span = badge.querySelector('span');
                            if (span && span.textContent.trim()) {
                                title = span.textContent.trim();
                            } else if (badge.textContent && badge.textContent.trim()) {
                                title = badge.textContent.trim();
                            }
                        }
                        const meta = {
                            sourceType: badge.dataset.sourceType || '',
                            resourceType: badge.dataset.resourceType || '',
                            mediaUrl: badge.dataset.mediaUrl || '',
                            thumbnailUrl: badge.dataset.thumbnailUrl || '',
                            hlsUrl: badge.dataset.hlsUrl || '',
                            author: badge.dataset.author || '',
                            snippet: badge.dataset.snippet || '',
                            citation: badge.dataset.citation || '',
                            pageNumber: badge.dataset.pageNumber || null,
                            documentId: badge.dataset.documentId || '',
                            documentShareId: badge.dataset.documentShareId || '',
                            fileType: badge.dataset.fileType || '',
                            postId: badge.dataset.postId || '',
                            username: badge.dataset.username || '',
                            profileUrl: badge.dataset.profileUrl || '',
                            profileCardUrl: badge.dataset.profileCardUrl || '',
                            person: {
                                username: badge.dataset.username || '',
                                profile_url: badge.dataset.profileUrl || '',
                                profile_card_url: badge.dataset.profileCardUrl || '',
                                avatar_url: badge.dataset.avatarUrl || '',
                                headline: badge.dataset.headline || '',
                                academic_level: badge.dataset.academicLevel || '',
                                programme_name: badge.dataset.programmeName || '',
                                bio: badge.dataset.bio || '',
                            },
                            triggerEl: badge
                        };
                        this.previewResource(url, title, meta);
                        return;
                    }

                    // Show more / Show less toggle on collapsible user bubble
                    const showMoreBtn = e.target.closest('.pwanimate-show-more-btn');
                    if (showMoreBtn) {
                        e.preventDefault();
                        const wrapper = showMoreBtn.closest('.pwanimate-user-bubble-wrapper') || showMoreBtn.parentElement;
                        const bubble = wrapper ? wrapper.querySelector('.pwanimate-user-bubble') : showMoreBtn.previousElementSibling;
                        if (bubble) {
                            const isExpanded = bubble.classList.toggle('is-expanded');
                            showMoreBtn.setAttribute('aria-expanded', isExpanded ? 'true' : 'false');
                            showMoreBtn.innerHTML = isExpanded
                                ? '<i data-lucide="chevron-up"></i> <span>Show less</span>'
                                : '<i data-lucide="chevron-down"></i> <span>Show more</span>';
                        }
                        return;
                    }

                    // Copy action on code block
                    const codeCopyBtn = e.target.closest('.pwanimate-code-copy-btn');
                    if (codeCopyBtn) {
                        e.preventDefault();
                        const codeBlock = codeCopyBtn.closest('.pwanimate-code-block');
                        const code = codeBlock ? codeBlock.querySelector('code') : null;
                        if (code) {
                            this.copyToClipboard(code.textContent, codeCopyBtn);
                        }
                        return;
                    }

                    // A proposed action: the student approves or declines it.
                    const actionConfirm = e.target.closest('.pwanimate-action-confirm');
                    const actionCancel = e.target.closest('.pwanimate-action-cancel');
                    if (actionConfirm || actionCancel) {
                        e.preventDefault();
                        const actionId = (actionConfirm || actionCancel).dataset.actionId;
                        const decision = actionConfirm ? 'confirm' : 'cancel';
                        this.decideAction(actionId, decision, actionConfirm || actionCancel);
                        return;
                    }

                    // Feedback on a Spark answer.
                    //
                    // This is the platform's only outcome signal: what was
                    // served and what it cost is already recorded, but
                    // nothing recorded whether the answer was right.
                    const feedbackBtn = e.target.closest('.pwanimate-feedback-btn[data-feedback]');
                    if (feedbackBtn) {
                        e.preventDefault();
                        const row = feedbackBtn.closest('.pwanimate-message-row');
                        this.sendFeedback(row, true, '');
                        return;
                    }

                    const feedbackReason = e.target.closest('.pwanimate-feedback-reason-item');
                    if (feedbackReason) {
                        e.preventDefault();
                        const row = feedbackReason.closest('.pwanimate-message-row');
                        const reason = feedbackReason.dataset.reason || '';
                        this.sendFeedback(row, false, reason);
                        return;
                    }

                    // Copy action on user or assistant message
                    const copyBtn = e.target.closest('.pwanimate-action-btn.copy-btn');
                    if (copyBtn) {
                        e.preventDefault();
                        const row = copyBtn.closest('.pwanimate-message-row');
                        if (row) {
                            if (row.classList.contains('user')) {
                                const bubble = row.querySelector('.pwanimate-user-bubble');
                                const text = bubble ? (bubble.getAttribute('data-raw') || bubble.textContent) : '';
                                this.copyToClipboard(text, copyBtn);
                            } else if (row.classList.contains('assistant')) {
                                const mdBody = row.querySelector('.pwanimate-markdown-body');
                                const text = mdBody ? (mdBody.getAttribute('data-raw') || mdBody.innerText) : '';
                                this.copyToClipboard(text, copyBtn);
                            }
                        }
                        return;
                    }

                    // Read an assistant reply aloud (per-message speaker)
                    const speakBtn = e.target.closest('.pwanimate-action-btn.pwanimate-speak-btn');
                    if (speakBtn) {
                        e.preventDefault();
                        const row = speakBtn.closest('.pwanimate-message-row.assistant');
                        if (row) {
                            const mdBody = row.querySelector('.pwanimate-markdown-body');
                            const raw = mdBody ? (mdBody.getAttribute('data-raw') || mdBody.innerText) : '';
                            this.speakResponse(raw, speakBtn);
                        }
                        return;
                    }

                    // Edit action on user message
                    const editBtn = e.target.closest('.pwanimate-action-btn.edit-btn');
                    if (editBtn) {
                        e.preventDefault();
                        const row = editBtn.closest('.pwanimate-message-row.user');
                        if (row) {
                            const bubble = row.querySelector('.pwanimate-user-bubble');
                            const text = bubble ? (bubble.getAttribute('data-raw') || bubble.textContent) : '';
                            if (this.input) {
                                this.input.value = text;
                                this.resizeTextarea();
                                this.input.focus();
                                this.input.scrollIntoView({ behavior: 'smooth', block: 'end' });
                            }
                        }
                        return;
                    }
                });
            }

            // Consolidated delegated click listener on document for delete, preview close, Study Mode, and model dropdowns
            this._onDocumentClick = (e) => {
                // Delete conversation buttons (delegated on document so desktop sidebar buttons work)
                const deleteBtn = e.target.closest('.pwanimate-conversation-delete');
                if (deleteBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const convId = deleteBtn.dataset.conversationId;
                    if (convId) {
                        this.handleDeleteConversation(convId);
                    }
                    return;
                }

                // History filter tabs (All / Study)
                const historyFilterBtn = e.target.closest('.pwanimate-history-filter-btn');
                if (historyFilterBtn) {
                    this.historyFilter = historyFilterBtn.dataset.historyFilter || 'all';
                    document.querySelectorAll('.pwanimate-history-filter-btn').forEach(btn => {
                        btn.classList.toggle('active', btn.dataset.historyFilter === this.historyFilter);
                    });
                    return;
                }

                // Composer Study Mode toggle button
                const studyToggleBtn = e.target.closest('#pwanimateStudyModeToggleBtn');
                if (studyToggleBtn) {
                    e.preventDefault();
                    this.studyModeActive = !this.studyModeActive;
                    this.syncStudyModeUI();
                    if (this.input) this.input.focus();
                    return;
                }

                // Composer @study command autocomplete hint button
                const studyCmdApplyBtn = e.target.closest('#pwanimateStudyCmdApplyBtn, #pwanimate-study-cmd-apply');
                if (studyCmdApplyBtn) {
                    e.preventDefault();
                    this.applyStudyCommandHint();
                    return;
                }

                // Study Session Header Bar: Checkpoint & Goal button
                const studyCheckpointBtn = e.target.closest('#pwanimateStudyCheckpointBtn, #pwanimate-study-checkpoint-btn');
                if (studyCheckpointBtn) {
                    e.preventDefault();
                    this.openStudyModal('checkpoint');
                    return;
                }

                // Study Session Header Bar: Summary & Save button
                const studySummaryBtn = e.target.closest('#pwanimateStudySummaryBtn, #pwanimate-study-summary-btn');
                if (studySummaryBtn) {
                    e.preventDefault();
                    this.openStudyModal('summary');
                    return;
                }

                // Study Session Header Bar: Pause / Resume button
                const studyPauseResumeBtn = e.target.closest('#pwanimateStudyPauseResumeBtn, #pwanimate-study-PauseResumeBtn');
                if (studyPauseResumeBtn) {
                    e.preventDefault();
                    const action = studyPauseResumeBtn.dataset.action ||
                        studyPauseResumeBtn.dataset.studyStatusAction ||
                        'pause';
                    this.handleStudySessionStatusAction(action);
                    return;
                }

                // Study Session Header Bar: End Session button
                const studyEndBtn = e.target.closest('#pwanimateStudyEndBtn, #pwanimate-study-end-btn');
                if (studyEndBtn) {
                    e.preventDefault();
                    this.handleStudySessionStatusAction('end').then((session) => {
                        if (session) {
                            this.openStudyModal('summary');
                        }
                    });
                    return;
                }

                // Home Resume Study Card: Resume Session button
                const resumeStudyBtn = e.target.closest('#pwanimateResumeStudySessionBtn, #pwanimate-resume-study-btn');
                if (resumeStudyBtn) {
                    e.preventDefault();
                    const resumeCard = document.getElementById('pwanimate-resume-study-card');
                    const sessionId = resumeStudyBtn.dataset.sessionId ||
                        resumeStudyBtn.dataset.studySessionId ||
                        (resumeCard && (resumeCard.dataset.sessionId || resumeCard.dataset.studySessionId));
                    const convId = resumeStudyBtn.dataset.conversationId ||
                        (resumeCard && resumeCard.dataset.conversationId);
                    this.handleStudySessionStatusAction('resume', sessionId).then(() => {
                        if (convId) {
                            const url = `/pwanimate/${encodeURIComponent(convId)}/`;
                            if (window.htmx) {
                                window.htmx.ajax('GET', url, {
                                    target: '#page-content-target',
                                    pushUrl: true,
                                    swap: 'innerHTML'
                                });
                            } else {
                                window.location.href = url;
                            }
                        }
                    });
                    return;
                }

                // Home Resume Study Card: Start New Study Session button
                const startNewStudyBtn = e.target.closest('#pwanimateStartNewStudySessionBtn, #pwanimate-start-new-study-btn');
                if (startNewStudyBtn) {
                    e.preventDefault();
                    const resumeCard = document.getElementById('pwanimate-resume-study-card');
                    const prevSessionId = resumeCard && (resumeCard.dataset.sessionId || resumeCard.dataset.studySessionId);
                    if (prevSessionId) {
                        this.handleStudySessionStatusAction('pause', prevSessionId);
                    }
                    if (resumeCard) resumeCard.classList.add('d-none');
                    this.studyModeActive = true;
                    this.syncStudyModeUI();
                    if (this.input) {
                        this.input.focus();
                    }
                    return;
                }

                // Home Resume Study Card: Dismiss Banner button
                const dismissResumeBtn = e.target.closest('#pwanimateDismissResumeBannerBtn, #pwanimate-dismiss-resume-btn');
                if (dismissResumeBtn) {
                    e.preventDefault();
                    const resumeCard = document.getElementById('pwanimate-resume-study-card');
                    const sessionId = dismissResumeBtn.dataset.sessionId ||
                        dismissResumeBtn.dataset.studySessionId ||
                        (resumeCard && (resumeCard.dataset.sessionId || resumeCard.dataset.studySessionId));
                    if (resumeCard) resumeCard.classList.add('d-none');
                    if (sessionId) {
                        this.handleStudySessionStatusAction('dismiss_banner', sessionId);
                    }
                    return;
                }

                // Study Modal actions: Save Goal, Update Checkpoint, Generate Summary, Export, Save to Collection
                const saveStudyMetaBtn = e.target.closest('#pwanimateSaveStudyMetaBtn, #pwanimate-save-study-meta-btn');
                if (saveStudyMetaBtn) {
                    e.preventDefault();
                    this.handleSaveStudyMetadata(saveStudyMetaBtn);
                    return;
                }

                const genCheckpointBtn = e.target.closest('#pwanimateGenerateCheckpointNowBtn, #pwanimate-generate-checkpoint-btn');
                if (genCheckpointBtn) {
                    e.preventDefault();
                    this.handleGenerateCheckpoint(genCheckpointBtn);
                    return;
                }

                const genSummaryBtn = e.target.closest('#pwanimateGenerateSummaryNowBtn, #pwanimate-generate-summary-btn');
                if (genSummaryBtn) {
                    e.preventDefault();
                    this.handleGenerateStudySummary(genSummaryBtn);
                    return;
                }

                const exportSummaryBtn = e.target.closest('#pwanimateExportSummaryPdfBtn, #pwanimateExportSummaryDocxBtn, .pwanimate-export-summary-btn');
                if (exportSummaryBtn) {
                    e.preventDefault();
                    this.handleExportStudySummary(exportSummaryBtn);
                    return;
                }

                const newCollectionToggleBtn = e.target.closest('#pwanimate-new-collection-toggle-btn');
                if (newCollectionToggleBtn) {
                    e.preventDefault();
                    const formEl = document.getElementById('pwanimate-new-collection-form');
                    if (formEl) {
                        formEl.classList.toggle('d-none');
                        const nameInput = document.getElementById('pwanimateNewCollectionNameInput') ||
                            document.getElementById('pwanimate-new-collection-name');
                        if (!formEl.classList.contains('d-none') && nameInput) {
                            nameInput.focus();
                        }
                    }
                    return;
                }

                const saveToCollectionBtn = e.target.closest('#pwanimateSaveToCollectionBtn, #pwanimate-save-to-collection-btn');
                if (saveToCollectionBtn) {
                    e.preventDefault();
                    this.handleSaveSummaryToCollection(saveToCollectionBtn);
                    return;
                }

                const openContextPicker = e.target.closest('[data-pwanimate-open-context-picker]');
                if (openContextPicker) {
                    e.preventDefault();
                    this.openContextDocumentPicker();
                    return;
                }

                const studyActionBtn = e.target.closest('[data-study-action]');
                if (studyActionBtn) {
                    e.preventDefault();
                    this.triggerDocumentStudyAction(studyActionBtn.dataset.studyAction);
                    return;
                }

                const mobileContextSheetBtn = e.target.closest('#pwanimateMobileContextSheetBtn');
                if (mobileContextSheetBtn) {
                    e.preventDefault();
                    this.openMobileWorkspaceSheet('context');
                    return;
                }

                // Close desktop preview button (closes active card, leaves rail open in State B)
                const closeDesktopPreview = e.target.closest('#desktopPreviewCloseBtn');
                if (closeDesktopPreview) {
                    e.preventDefault();
                    this.closePreview();
                    return;
                }

                // Workspace Tab Switching: Preview vs Context (Desktop & Mobile)
                const previewTabBtn = e.target.closest('#desktopTabPreviewBtn, #mobileTabPreviewBtn');
                if (previewTabBtn) {
                    e.preventDefault();
                    this.switchWorkspaceTab('preview');
                    return;
                }

                const contextTabBtn = e.target.closest('#desktopTabContextBtn, #mobileTabContextBtn');
                if (contextTabBtn) {
                    e.preventDefault();
                    this.switchWorkspaceTab('context');
                    return;
                }

                const offlineTabBtn = e.target.closest('#desktopTabOfflineBtn, #mobileTabOfflineBtn');
                if (offlineTabBtn) {
                    e.preventDefault();
                    this.switchWorkspaceTab('offline');
                    return;
                }

                // Consolidated rail overflow menu
                const railMenuAction = e.target.closest('#desktopRailMenu [data-rail-action]');
                if (railMenuAction) {
                    e.preventDefault();
                    this.handleRailMenuAction(railMenuAction.dataset.railAction);
                    return;
                }

                // Offline tab list actions (Read / Add to context / Save / Update / Remove)
                const offlineTabAction = e.target.closest('[data-offline-tab-action]');
                if (offlineTabAction) {
                    e.preventDefault();
                    this.handleOfflineTabAction(offlineTabAction.dataset.offlineTabAction, offlineTabAction.dataset.shareId);
                    return;
                }

                // Add to Context buttons (Desktop & Mobile)
                const addContextBtn = e.target.closest('#desktopPreviewAddToContextBtn, #previewSheetAddToContextBtn');
                if (addContextBtn) {
                    e.preventDefault();
                    this.addResourceToContext(this.previewResourceState);
                    return;
                }

                // Remove from Context buttons (Surface header)
                const removeContextBtn = e.target.closest('#desktopContextRemoveBtn, #mobileContextRemoveBtn');
                if (removeContextBtn) {
                    e.preventDefault();
                    this.removeResourceFromContext(this.activeContextIndex);
                    return;
                }

                // Context chip remove button (X icon on chip)
                const chipRemoveBtn = e.target.closest('.pwanimate-chip-remove');
                if (chipRemoveBtn) {
                    e.preventDefault();
                    e.stopPropagation();
                    const idx = parseInt(chipRemoveBtn.dataset.index, 10);
                    if (!isNaN(idx)) {
                        this.removeResourceFromContext(idx);
                    }
                    return;
                }

                // Context chip selection
                const chipItem = e.target.closest('.pwanimate-context-chip');
                if (chipItem) {
                    e.preventDefault();
                    const idx = parseInt(chipItem.dataset.index, 10);
                    if (!isNaN(idx)) {
                        this.selectContextResource(idx);
                    }
                    return;
                }

                // Close context rail button (closes entire rail)
                const closeContextRail = e.target.closest('#pwanimateContextRailCloseBtn');
                if (closeContextRail) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.closeContextRail();
                    return;
                }

                // Open context rail button (workspace top-right toggle)
                const toggleContextRail = e.target.closest('#pwanimateContextRailToggleBtn');
                if (toggleContextRail) {
                    e.preventDefault();
                    e.stopPropagation();
                    this.openContextRail();
                    return;
                }

                // Left rail toggle buttons (delegated on document so they work across OOB rail swaps)
                const collapseBtn = e.target.closest('#pwanimateLeftRailCollapseBtn');
                if (collapseBtn) {
                    e.preventDefault();
                    this.toggleLeftRail(true);
                    return;
                }

                const expandBtn = e.target.closest('#pwanimateLeftRailExpandBtn');
                if (expandBtn) {
                    e.preventDefault();
                    this.toggleLeftRail(false);
                    return;
                }

                // Spark engine dropdown selection (Auto item has no data-engine)
                const opt = e.target.closest('.pwanimate-model-option');
                if (opt) {
                    e.preventDefault();
                    const engine = opt.dataset.engine || '';
                    const display = opt.dataset.display || 'Auto';
                    this.selectEngine(engine, display);
                    return;
                }
            };
            document.addEventListener('click', this._onDocumentClick);

            // Escape key listener to close preview
            this._onKeyDown = (e) => {
                if (e.key === 'Escape' || e.key === 'Esc') {
                    if (this.activeWorkspaceTab === 'preview' && (this.previewResourceState || this.activeResourceDetails)) {
                        this.closePreview();
                    }
                }
            };
            document.addEventListener('keydown', this._onKeyDown);

            // Close mobile preview sheet handler
            const previewSheet = document.getElementById('pwanimateResourcePreviewSheet');
            if (previewSheet) {
                previewSheet.addEventListener('hidden.bs.offcanvas', () => {
                    this.resetMediaElements(previewSheet);
                    if (this.previewDocViewer) {
                        try { this.previewDocViewer.destroy(); } catch (err) {}
                        this.previewDocViewer = null;
                    }
                    if (this.contextDocViewer) {
                        try { this.contextDocViewer.destroy(); } catch (err) {}
                        this.contextDocViewer = null;
                    }
                    if (this.lastPreviewTriggerEl && typeof this.lastPreviewTriggerEl.focus === 'function') {
                        this.lastPreviewTriggerEl.focus();
                        this.lastPreviewTriggerEl = null;
                    }
                });
            }

            // Modal model selection
            if (this.modalModelsContainer) {
                this.modalModelsContainer.addEventListener('click', (e) => {
                    const btn = e.target.closest('.pwanimate-modal-select-btn');
                    if (btn && !btn.classList.contains('disabled')) {
                        e.preventDefault();
                        const engine = btn.dataset.engine || '';
                        const display = btn.dataset.display || 'Auto';
                        this.selectEngine(engine, display);

                        // Dismiss modal
                        if (this.quotaModal) {
                            if (window.bootstrap && typeof window.bootstrap.Modal?.getInstance === 'function') {
                                const instance = window.bootstrap.Modal.getInstance(this.quotaModal);
                                if (instance) instance.hide();
                            } else {
                                const closeBtn = this.quotaModal.querySelector('[data-bs-dismiss="modal"]');
                                if (closeBtn) closeBtn.click();
                            }
                        }
                    }
                });
            }

            // Refresh quota button
            if (this.refreshQuotaBtn) {
                this.refreshQuotaBtn.addEventListener('click', () => {
                    const icon = this.refreshQuotaBtn.querySelector('svg, i');
                    if (icon) icon.classList.add('pwanimate-spin');
                    this.fetchQuotaStatus().finally(() => {
                        setTimeout(() => {
                            if (icon) icon.classList.remove('pwanimate-spin');
                        }, 450);
                    });
                });
            }

            // Quota modal on show
            if (this.quotaModal) {
                this.quotaModal.addEventListener('show.bs.modal', () => {
                    this.fetchQuotaStatus();
                });
            }
        }

        initVoiceInput() {
            if (!this.voiceBtn || !this.input) return;

            if (this.voiceStopBtn) this.voiceStopBtn.addEventListener('click', () => this.stopVoiceCapture());
            if (this.voiceSendBtn) this.voiceSendBtn.addEventListener('click', () => this.stopVoiceCapture(true));
            if (this.voiceCancelBtn) this.voiceCancelBtn.addEventListener('click', () => this.cancelVoiceCapture());
            if (!this.voiceRecorderSupported) {
                this.voiceBtn.disabled = true;
                this.voiceBtn.title = 'Audio recording is not supported in this browser.';
                this.voiceBtn.setAttribute('aria-label', this.voiceBtn.title);
                this.setVoiceStatus('Voice recording is not available in this browser. You can still type.', true);
                return;
            }

            this.voiceBtn.addEventListener('click', () => {
                this.startVoiceCapture();
            });
        }

        initSpeakReplies() {
            // Kick off async system-voice loading so the first read finds a
            // matching voice right away. Playback itself is per-message and
            // starts from a click (a user gesture, which iOS/Safari require).
            if (typeof window.speechSynthesis !== 'undefined') {
                try { window.speechSynthesis.getVoices(); } catch (error) { /* no-op */ }
            }
            window.addEventListener('beforeunload', () => this.stopSpeaking());
        }

        // Reflect playback state on a single per-message speaker button.
        setSpeakBtnState(btn, active) {
            if (!btn) return;
            const icon = btn.querySelector('svg, i');
            btn.classList.toggle('is-speaking', active);
            btn.setAttribute('aria-pressed', String(active));
            const label = active ? 'Stop reading aloud' : 'Read this reply aloud';
            btn.title = label;
            btn.setAttribute('aria-label', label);
            if (icon) {
                sparkSetIcon(icon, active ? 'circle-stop' : 'volume-2');
            }
        }

        stopSpeaking() {
            const btn = this.speakingBtn;
            this.speechRequestId += 1;
            this.speaking = false;
            this.speakingBtn = null;
            this.speakUtterance = null;
            if (this.speechAudio) {
                this.speechAudio.pause();
                this.speechAudio.removeAttribute('src');
                this.speechAudio = null;
            }
            if (typeof window.speechSynthesis !== 'undefined') {
                try { window.speechSynthesis.cancel(); } catch (error) { /* no-op */ }
            }
            if (btn) this.setSpeakBtnState(btn, false);
        }

        // Flatten markdown to something natural to read aloud.
        textForSpeech(markdown) {
            if (!markdown) return '';
            let text = String(markdown);
            // Drop fenced code blocks and math; keep the surrounding prose.
            text = text.replace(/```[\s\S]*?```/g, ' ');
            text = text.replace(/`([^`]+)`/g, '$1');
            text = text.replace(/\$\$[\s\S]*?\$\$/g, ' ');
            text = text.replace(/\$[^$\n]+\$/g, ' ');
            // Images -> alt text; links -> their visible label.
            text = text.replace(/!\[([^\]]*)\]\([^)]*\)/g, '$1');
            text = text.replace(/\[([^\]]+)\]\([^)]*\)/g, '$1');
            // Headings, quotes, list bullets, numbered lists, table pipes.
            text = text.replace(/^\s{0,3}#{1,6}\s+/gm, '');
            text = text.replace(/^\s{0,3}>\s?/gm, '');
            text = text.replace(/^\s*[-*+]\s+/gm, '');
            text = text.replace(/^\s*\d+\.\s+/gm, '');
            text = text.replace(/\|/g, ' ');
            // Emphasis markers.
            text = text.replace(/(\*\*|__|~~|\*|_)/g, '');
            // Bare URLs -> read the hostname only.
            text = text.replace(/https?:\/\/\S+/g, (url) => {
                try { return new URL(url).hostname.replace(/^www\./, ''); } catch (e) { return 'link'; }
            });
            return text.replace(/\s+/g, ' ').trim();
        }

        speakResponse(markdown, btn) {
            // Tapping the active button again stops playback.
            if (this.speaking && this.speakingBtn === btn) {
                this.stopSpeaking();
                return;
            }

            const text = this.textForSpeech(markdown);
            if (!text) return;

            // Clear any other reply still marked active, then preempt it:
            // a new read cancels whatever is currently playing.
            if (this.speakingBtn && this.speakingBtn !== btn) this.setSpeakBtnState(this.speakingBtn, false);
            this.stopSpeaking();

            const requestId = ++this.speechRequestId;
            this.speaking = true;
            this.speakingBtn = btn;
            this.setSpeakBtnState(btn, true);
            this.requestServerSpeech(text, btn, requestId);
        }

        async requestServerSpeech(text, btn, requestId) {
            try {
                const response = await fetch('/api/pwanimate/voice/synthesize/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'audio/mpeg'
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({
                        text,
                        voice_id: `${this.voiceStyle}_${this.voiceGender}`
                    })
                });
                if (response.ok && requestId === this.speechRequestId) {
                    const audio = new Audio(URL.createObjectURL(await response.blob()));
                    this.speechAudio = audio;
                    audio.onended = () => {
                        if (requestId === this.speechRequestId) this.stopSpeaking();
                    };
                    audio.onerror = () => {
                        if (requestId === this.speechRequestId) this.stopSpeaking();
                    };
                    await audio.play();
                    return;
                }
            } catch (error) {
                // Fall back to the browser voice when Azure is unavailable.
            }
            if (requestId === this.speechRequestId) this.speakWithBrowserVoice(text, btn, requestId);
        }

        speakWithBrowserVoice(text, btn, requestId) {
            if (typeof window.speechSynthesis === 'undefined'
                || typeof window.SpeechSynthesisUtterance === 'undefined') {
                this.stopSpeaking();
                return;
            }
            const pageLang = document.documentElement.lang === 'sw' ? 'sw' : 'en';
            const utterance = new SpeechSynthesisUtterance(text);
            utterance.lang = pageLang;
            utterance.rate = 1.0;
            try {
                const voices = window.speechSynthesis.getVoices() || [];
                const match = voices.find(v => (v.lang || '').toLowerCase().replace('_', '-') === pageLang)
                    || voices.find(v => (v.lang || '').toLowerCase().startsWith(pageLang));
                if (match) utterance.voice = match;
            } catch (error) { /* keep default voice */ }
            const finish = () => {
                if (this.speakUtterance !== utterance || requestId !== this.speechRequestId) return;
                this.stopSpeaking();
            };
            utterance.onend = finish;
            utterance.onerror = finish;
            this.speakUtterance = utterance;
            window.speechSynthesis.speak(utterance);
        }

        async startVoiceCapture() {
            if (this.voiceStarting || this.voiceListening || this.voiceStopping || this.isGenerating) return;
            this.voiceStarting = true;
            this.voiceCancelled = false;
            this.voiceFinalTranscript = '';
            this.voiceErrorMessage = '';
            this.voiceSelection = {
                start: this.input.selectionStart ?? this.input.value.length,
                end: this.input.selectionEnd ?? this.input.value.length,
            };
            this.voiceBtn.disabled = true;
            this.input.disabled = true;
            if (this.voiceCapture) this.voiceCapture.classList.remove('d-none');
            if (this.voiceStopBtn) this.voiceStopBtn.disabled = true;
            if (this.voiceSendBtn) this.voiceSendBtn.disabled = true;
            this.setVoiceStatus('Starting microphone');

            let stream = null;
            try {
                stream = await navigator.mediaDevices.getUserMedia({
                    audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true },
                });
                if (this.voiceCancelled || this.isGenerating) {
                    stream.getTracks().forEach(track => track.stop());
                    return;
                }

                this.voiceMediaStream = stream;
                const mimeType = this.getVoiceMimeType();
                try {
                    this.voiceRecorder = new MediaRecorder(stream, mimeType ? { mimeType } : undefined);
                } catch (error) {
                    // Some Android WebView builds report a MIME type as supported but
                    // still reject it when constructing MediaRecorder. Let WebView choose.
                    if (!mimeType || error.name !== 'NotSupportedError') throw error;
                    console.warn('[Spark voice] Requested recording format was rejected; retrying with the WebView default.', error);
                    this.voiceRecorder = new MediaRecorder(stream);
                }
                this.voiceChunks = [];
                const recorder = this.voiceRecorder;
                recorder.addEventListener('dataavailable', event => {
                    if (event.data && event.data.size) this.voiceChunks.push(event.data);
                });
                recorder.addEventListener('start', () => {
                    this.voiceStarting = false;
                    this.setVoiceListening(true);
                    this.setVoiceStatus('Recording voice message');
                    this.startVoiceLevelMeter(stream);
                }, { once: true });
                recorder.addEventListener('stop', () => this.handleVoiceRecordingStopped(recorder), { once: true });
                recorder.addEventListener('error', event => {
                    const error = event.error || event;
                    console.error('[Spark voice] MediaRecorder failed:', error);
                    this.voiceErrorMessage = this.getVoiceStartupErrorMessage(error);
                    stream.getTracks().forEach(track => track.stop());
                    this.voiceMediaStream = null;
                    this.voiceRecorder = null;
                    this.voiceChunks = [];
                    this.voiceStarting = false;
                    this.voiceStopping = false;
                    this.setVoiceListening(false);
                    if (this.input) this.input.disabled = this.isGenerating;
                    this.stopVoiceLevelMeter();
                    this.setVoiceStatus(this.voiceErrorMessage, true);
                }, { once: true });
                recorder.start(1000);
            } catch (error) {
                console.error('[Spark voice] Could not start recording:', error);
                if (stream) stream.getTracks().forEach(track => track.stop());
                this.voiceMediaStream = null;
                if (this.voiceCancelled) return;
                this.voiceStarting = false;
                this.voiceRecorder = null;
                this.voiceChunks = [];
                this.setVoiceListening(false);
                if (this.input) this.input.disabled = this.isGenerating;
                this.stopVoiceLevelMeter();
                this.setVoiceStatus(this.getVoiceStartupErrorMessage(error), true);
            } finally {
                if (!this.voiceRecorder || this.voiceRecorder.state !== 'recording') this.voiceStarting = false;
                if (this.voiceBtn && !this.voiceListening && !this.voiceStopping && !this.voiceStarting) {
                    this.voiceBtn.disabled = this.isGenerating || !this.voiceRecorderSupported;
                }
            }
        }

        getVoiceMimeType() {
            const supportedTypes = [
                'audio/webm;codecs=opus',
                'audio/mp4',
                'audio/ogg;codecs=opus',
                'audio/webm',
                'audio/ogg',
            ];
            return supportedTypes.find(type => MediaRecorder.isTypeSupported(type)) || '';
        }

        getVoiceStartupErrorMessage(error) {
            switch (error && error.name) {
                case 'NotAllowedError':
                case 'SecurityError':
                    return 'Microphone access is blocked. Allow microphone access for pwaninet in Android settings, then try again.';
                case 'NotFoundError':
                case 'DevicesNotFoundError':
                    return 'No microphone is available on this device.';
                case 'NotReadableError':
                case 'TrackStartError':
                    return 'Android could not open the microphone. Close other apps using it, then try again.';
                case 'OverconstrainedError':
                    return 'The microphone could not meet the requested audio settings. Please try again.';
                case 'NotSupportedError':
                    return 'This Android WebView could not start a supported audio recording format. Update Android System WebView and try again.';
                default:
                    return 'Microphone could not be started. Check microphone permission and try again.';
            }
        }

        async handleVoiceRecordingStopped(recorder) {
            const blob = new Blob(this.voiceChunks, { type: recorder.mimeType || 'audio/webm' });
            this.voiceChunks = [];
            this.voiceRecorder = null;
            this.stopVoiceLevelMeter();
            if (this.voiceCancelled) return;
            if (!this.voiceStopping) {
                this.voiceStopping = true;
                this.voiceSendAfterTranscription = false;
                this.setVoiceListening(false);
                this.setVoiceTranscribing(true);
            }
            if (!blob.size) {
                this.voiceErrorMessage = 'No audio was captured. Please try again.';
                this.setVoiceStatus(this.voiceErrorMessage, true);
                this.finishVoiceCapture();
                return;
            }
            await this.transcribeVoiceRecording(blob);
        }

        async transcribeVoiceRecording(blob) {
            this.setVoiceStatus('Transcribing voice message');
            const formData = new FormData();
            const mimeType = (blob.type || 'audio/webm').split(';', 1)[0];
            const extension = mimeType === 'audio/mp4' ? 'm4a'
                : mimeType === 'audio/ogg' ? 'ogg'
                    : mimeType === 'audio/wav' || mimeType === 'audio/x-wav' ? 'wav'
                        : mimeType === 'audio/mpeg' ? 'mp3' : 'webm';
            formData.append('audio', blob, `voice.${extension}`);
            formData.append('language', document.documentElement.lang === 'sw' ? 'sw' : 'en');

            const controller = new AbortController();
            this.voiceTranscriptionAbortController = controller;
            try {
                const response = await fetch('/api/pwanimate/voice/transcribe/', {
                    method: 'POST',
                    credentials: 'same-origin',
                    headers: { 'X-CSRFToken': getCsrfToken() },
                    body: formData,
                    signal: controller.signal,
                });

                // Check content type before parsing JSON
                const contentType = response.headers.get('content-type') || '';
                if (!contentType.includes('application/json')) {
                    // Server returned HTML or non-JSON response (likely an error page)
                    const text = await response.text();
                    console.error('[Voice Transcription] Non-JSON response:', text.substring(0, 200));
                    throw new Error('Server returned an invalid response. Please try again.');
                }

                const payload = await response.json();
                if (!response.ok) throw new Error(payload.error || 'Transcription failed. Please try again.');
                this.voiceFinalTranscript = (payload.text || '').trim();
                if (!this.voiceFinalTranscript) this.voiceErrorMessage = 'No speech was recognized. Please try again.';
            } catch (error) {
                if (this.voiceCancelled) return;
                console.error('[Voice Transcription] Error:', error);
                this.voiceErrorMessage = error.message || 'Transcription failed. Please try again.';
            } finally {
                if (this.voiceTranscriptionAbortController === controller) this.voiceTranscriptionAbortController = null;
            }
            if (this.voiceCancelled) return;
            if (this.voiceErrorMessage) this.setVoiceStatus(this.voiceErrorMessage, true);
            this.finishVoiceCapture();
        }

        setVoiceListening(isListening) {
            const wasListening = this.voiceListening;
            this.voiceListening = isListening;
            if (isListening) this.stopSpeaking();
            if (isListening && !wasListening) {
                this.voiceWaveformHistory = this.voiceWaveformBars.map(() => 0);
                this.voiceWaveformLastSampleAt = 0;
                this.voiceWaveformBars.forEach(bar => { bar.style.height = '3px'; });
                if (this.voiceCapture) this.voiceCapture.classList.remove('is-speaking');
            }
            if (this.input) this.input.disabled = isListening || this.voiceStopping || this.isGenerating;
            if (this.composerForm) this.composerForm.classList.toggle('is-voice-recording', isListening || this.voiceStopping);
            if (this.voiceCapture) {
                this.voiceCapture.classList.toggle('is-listening', isListening);
                this.voiceCapture.classList.toggle('is-stopping', this.voiceStopping);
                this.voiceCapture.classList.toggle('d-none', !isListening && !this.voiceStopping && !this.voiceStarting);
            }
            if (this.voiceStopBtn) this.voiceStopBtn.disabled = this.voiceStarting || this.voiceStopping || this.isGenerating;
            if (this.voiceSendBtn) this.voiceSendBtn.disabled = this.voiceStarting || this.voiceStopping || this.isGenerating;
            if (!this.voiceBtn) return;
            this.voiceBtn.disabled = this.isGenerating || this.voiceStarting || this.voiceStopping || !this.voiceRecorderSupported;
            this.voiceBtn.classList.toggle('is-listening', isListening);
            this.voiceBtn.setAttribute('aria-pressed', String(isListening));
            this.voiceBtn.setAttribute('aria-label', isListening ? 'Stop voice input' : 'Dictate a message');
            this.voiceBtn.title = isListening ? 'Stop voice input' : 'Dictate a message';
            this.updateSendButtonState();
        }

        setVoiceStatus(message, isError = false) {
            if (!this.voiceStatus) return;
            this.voiceStatus.textContent = message;
            this.voiceStatus.classList.toggle('d-none', !message);
            this.voiceStatus.classList.toggle('is-error', Boolean(isError));
            if (this.voiceFeedback) {
                const showFeedback = Boolean(message) && (!this.voiceListening || this.voiceStopping);
                this.voiceFeedback.textContent = message;
                this.voiceFeedback.classList.toggle('d-none', !showFeedback);
                this.voiceFeedback.classList.toggle('is-error', Boolean(isError));
            }
        }

        finishVoiceCapture() {
            if (!this.voiceStopping) return;
            const transcript = (this.voiceFinalTranscript || '').trim();
            const sendAfterTranscription = this.voiceSendAfterTranscription && Boolean(transcript);
            if (transcript) {
                this.insertVoiceTranscript(transcript.replace(/\s+([,.;?!])/g, '$1'));
                this.setVoiceStatus(sendAfterTranscription ? 'Sending transcribed message' : 'Speech added to your draft. Review or edit it before sending.');
            } else if (!this.voiceErrorMessage) {
                this.setVoiceStatus('No speech was captured. Tap the microphone and try again.', true);
            }
            this.voiceFinalTranscript = '';
            this.voiceStopping = false;
            this.voiceSendAfterTranscription = false;
            this.stopVoiceLevelMeter();
            this.setVoiceListening(false);
            this.setVoiceTranscribing(false);
            if (this.voiceStatus) {
                this.setVoiceStatus(this.voiceStatus.textContent, this.voiceStatus.classList.contains('is-error'));
            }
            if (this.input) this.input.disabled = this.isGenerating;
            if (this.voiceBtn) this.voiceBtn.disabled = this.isGenerating || !this.voiceRecorderSupported;
            this.updateSendButtonState();
            if (sendAfterTranscription) this.handleSend();
        }

        stopVoiceCapture(sendAfterTranscription = false) {
            if ((!this.voiceListening && !this.voiceStarting) || this.voiceStopping) return;
            this.voiceErrorMessage = '';
            this.voiceStopping = true;
            this.voiceSendAfterTranscription = sendAfterTranscription;
            this.setVoiceListening(false);
            this.setVoiceTranscribing(true, sendAfterTranscription);
            this.setVoiceStatus('Finishing recording');
            if (this.voiceRecorder && this.voiceRecorder.state === 'recording') {
                try {
                    this.voiceRecorder.stop();
                } catch (error) {
                    this.voiceErrorMessage = 'Recording could not be stopped cleanly. Please try again.';
                    this.setVoiceStatus(this.voiceErrorMessage, true);
                    this.finishVoiceCapture();
                }
            } else if (!this.voiceRecorder) {
                this.voiceErrorMessage = 'The microphone did not finish starting. Please try again.';
                this.setVoiceStatus(this.voiceErrorMessage, true);
                this.finishVoiceCapture();
            }
        }

        setVoiceTranscribing(isLoading, sending = false) {
            [this.voiceStopBtn, this.voiceSendBtn].forEach((button) => {
                if (!button) return;
                const isActive = isLoading && ((sending && button === this.voiceSendBtn) || (!sending && button === this.voiceStopBtn));
                button.classList.toggle('is-loading', isActive);
                button.disabled = isLoading || this.isGenerating;
            });
        }

        cancelVoiceCapture() {
            if (!this.voiceListening && !this.voiceStopping && !this.voiceStarting) return;
            this.voiceCancelled = true;
            if (this.voiceTranscriptionAbortController) this.voiceTranscriptionAbortController.abort();
            this.voiceTranscriptionAbortController = null;
            this.voiceListening = false;
            this.voiceStarting = false;
            this.voiceStopping = false;
            this.voiceSendAfterTranscription = false;
            this.voiceFinalTranscript = '';
            this.voiceErrorMessage = '';
            if (this.voiceRecorder && this.voiceRecorder.state === 'recording') {
                const recorder = this.voiceRecorder;
                recorder.addEventListener('stop', () => {
                    this.stopVoiceLevelMeter();
                    this.voiceRecorder = null;
                    this.voiceChunks = [];
                }, { once: true });
                try { recorder.stop(); } catch (error) { this.stopVoiceLevelMeter(); }
            } else {
                this.voiceRecorder = null;
                this.voiceChunks = [];
                this.stopVoiceLevelMeter();
            }
            this.setVoiceListening(false);
            if (this.input) this.input.disabled = this.isGenerating;
            this.setVoiceTranscribing(false);
            this.setVoiceStatus('Voice recording cancelled');
            if (this.voiceCapture) this.voiceCapture.classList.add('d-none');
        }

        async startVoiceLevelMeter(stream = this.voiceMediaStream) {
            if (!stream || this.voiceAudioContext) return;
            try {
                const AudioContextClass = window.AudioContext || window.webkitAudioContext;
                if (!AudioContextClass) return;
                this.voiceAudioContext = new AudioContextClass();
                if (this.voiceAudioContext.state === 'suspended') this.voiceAudioContext.resume();
                this.voiceAnalyser = this.voiceAudioContext.createAnalyser();
                this.voiceAnalyser.fftSize = 256;
                this.voiceAudioContext.createMediaStreamSource(stream).connect(this.voiceAnalyser);
                const samples = new Uint8Array(this.voiceAnalyser.fftSize);
                const draw = () => {
                    if (!this.voiceAnalyser) return;
                    this.voiceAnalyser.getByteTimeDomainData(samples);
                    let sum = 0;
                    for (let i = 0; i < samples.length; i += 1) {
                        const amplitude = (samples[i] - 128) / 128;
                        sum += amplitude * amplitude;
                    }
                    this.setVoiceWaveformLevel(Math.min(1, Math.sqrt(sum / samples.length) * 4));
                    this.voiceAnimationFrame = window.requestAnimationFrame(draw);
                };
                draw();
            } catch (error) {
                // Recording remains available if the browser cannot create a visualizer.
            }
        }

        setVoiceWaveformLevel(level) {
            if (!this.voiceCapture || !this.voiceWaveformBars.length) return;
            const normalized = Math.max(0, Math.min(1, Number(level) || 0));
            const isSpeaking = normalized > 0.065;
            this.voiceCapture.classList.toggle('is-speaking', isSpeaking);

            // Add one audio sample at the right edge at a steady cadence; the existing samples
            // shift left, creating a scrolling trace that begins as quiet baseline dots.
            const now = performance.now();
            if (!this.voiceWaveformLastSampleAt || now - this.voiceWaveformLastSampleAt >= 65) {
                if (this.voiceWaveformHistory.length !== this.voiceWaveformBars.length) {
                    this.voiceWaveformHistory = this.voiceWaveformBars.map(() => 0);
                }
                this.voiceWaveformHistory.shift();
                this.voiceWaveformHistory.push(normalized);
                this.voiceWaveformLastSampleAt = now;
            }

            this.voiceWaveformBars.forEach((bar, index) => {
                const sample = this.voiceWaveformHistory[index] || 0;
                const variation = 0.72 + 0.28 * (0.5 + 0.5 * Math.sin((index + 1) * 2.37));
                const height = sample > 0.065 ? 3 + Math.pow(sample, 0.78) * 48 * variation : 3;
                bar.style.height = `${height}px`;
            });
        }

        stopVoiceLevelMeter() {
            if (this.voiceAnimationFrame) window.cancelAnimationFrame(this.voiceAnimationFrame);
            this.voiceAnimationFrame = null;
            this.voiceAnalyser = null;
            if (this.voiceMediaStream) this.voiceMediaStream.getTracks().forEach(track => track.stop());
            this.voiceMediaStream = null;
            if (this.voiceAudioContext) {
                try { this.voiceAudioContext.close(); } catch (error) {}
            }
            this.voiceAudioContext = null;
            this.voiceWaveformHistory = this.voiceWaveformBars.map(() => 0);
            this.voiceWaveformLastSampleAt = 0;
            this.setVoiceWaveformLevel(0);
        }

        insertVoiceTranscript(transcript) {
            const value = this.input.value;
            const start = this.voiceSelection ? this.voiceSelection.start : value.length;
            const end = this.voiceSelection ? this.voiceSelection.end : value.length;
            const before = value.slice(0, start);
            const after = value.slice(end);
            const needsSpaceBefore = Boolean(before && !/\s$/.test(before));
            const needsSpaceAfter = Boolean(after && !/^\s/.test(after));
            const spokenText = transcript.trim();
            const insertion = `${needsSpaceBefore ? ' ' : ''}${spokenText}${needsSpaceAfter ? ' ' : ''}`;
            const cursor = before.length + insertion.length;

            this.input.value = `${before}${insertion}${after}`;
            this.input.setSelectionRange(cursor, cursor);
            this.input.dispatchEvent(new Event('input', { bubbles: true }));
            this.input.focus({ preventScroll: true });
        }

        scheduleTextareaResize() {
            if (this.resizeFrame) {
                cancelAnimationFrame(this.resizeFrame);
            }
            this.resizeFrame = requestAnimationFrame(() => {
                this.resizeTextarea();
                this.resizeFrame = null;
            });
        }

        resizeTextarea() {
            if (!this.input) return;
            this.input.style.height = 'auto';
            const newHeight = Math.min(this.input.scrollHeight, 160);
            this.input.style.height = `${newHeight}px`;
        }

        resetTextareaHeight() {
            if (this.resizeFrame) {
                cancelAnimationFrame(this.resizeFrame);
                this.resizeFrame = null;
            }
            if (this.input) {
                this.input.style.height = 'auto';
            }
        }

        updateLocationBadge(prefix, page, totalPages = null) {
            let container = null;
            if (prefix === 'desktopPreview') {
                container = document.getElementById('desktopPreviewActive');
            } else if (prefix === 'previewSheet') {
                container = document.getElementById('pwanimateResourcePreviewSheet');
            } else if (prefix === 'desktopContext') {
                container = document.getElementById('desktopContextSurfaceCard');
            } else if (prefix === 'mobileContext') {
                container = document.getElementById('mobileContextActive');
            }
            if (!container) container = document;

            const locBadge = container.querySelector(`#${prefix}LocationBadge`);
            const locText = container.querySelector(`#${prefix}LocationText`);
            if (locBadge && locText) {
                if (page) {
                    locBadge.classList.remove('d-none');
                    locText.textContent = totalPages ? `p. ${page} / ${totalPages}` : `p. ${page}`;
                } else {
                    locBadge.classList.add('d-none');
                }
            }
        }

        static normalizeResource(url, title, meta = {}) {
            meta = meta || {};
            const rawUrl = url || meta.mediaUrl || meta.media_url || meta.url || '#';
            const cleanUrl = typeof rawUrl === 'string' ? rawUrl : '#';

            // Extract authoritative IDs
            let documentShareId = meta.documentShareId || meta.document_share_id || '';
            if (!documentShareId) {
                const docMatch = cleanUrl.match(/\/documents\/document\/([0-9a-fA-F-]+)/i);
                if (docMatch) documentShareId = docMatch[1];
            }

            const documentId = meta.documentId || meta.document_id || documentShareId || '';
            const documentVersionId = meta.documentVersionId || meta.document_version_id || meta.versionId || '';
            const fileId = meta.fileId || meta.file_id || '';

            let postId = meta.postId || meta.post_id || '';
            if (!postId) {
                const postMatch = cleanUrl.match(/\/(?:posts\/)?post\/([0-9a-fA-F-]+)/i);
                if (postMatch) postId = postMatch[1];
            }

            // Media URLs
            const mediaUrl = meta.mediaUrl || meta.media_url || '';
            const hlsUrl = meta.hlsUrl || meta.hls_url || '';
            const thumbnailUrl = meta.thumbnailUrl || meta.thumbnail_url || meta.avatar_url || meta.profile_photo_url || '';

            // File type derivation & normalization
            let fileType = (meta.fileType || meta.file_type || meta.file_extension || meta.extension || '').toLowerCase().trim();
            if (fileType.startsWith('.')) fileType = fileType.substring(1);
            if (!fileType) {
                const candidate = (mediaUrl || cleanUrl || '').split('?')[0].split('#')[0];
                const extMatch = candidate.match(/\.([a-zA-Z0-9]+)$/);
                if (extMatch && extMatch[1].length <= 5) fileType = extMatch[1].toLowerCase();
            }

            // Classification
            const resourceType = String(meta.resourceType || meta.resource_type || meta.type || meta.previewType || meta.preview_type || '').toLowerCase();
            const sourceType = String(meta.sourceType || meta.source_type || meta.source || '').toLowerCase();
            const isProfile = resourceType === 'profile' || resourceType === 'user' ||
                sourceType === 'user' || sourceType === 'profile';
            const isPost = meta.sourceType === 'post' || meta.source_type === 'post' ||
                meta.resourceType === 'post' || meta.resource_type === 'post' ||
                cleanUrl.includes('/post/') || cleanUrl.includes('/posts/');
            const isVideo = meta.resourceType === 'video' || meta.resource_type === 'video' || Boolean(hlsUrl) ||
                /\.(mp4|webm|ogg|m3u8)(\?|#|$)/i.test(mediaUrl || cleanUrl);

            const isImage = !isPost && !isProfile && !isVideo && (meta.resourceType === 'image' || meta.resource_type === 'image' ||
                /\.(jpg|jpeg|png|gif|webp|svg)(\?|#|$)/i.test(mediaUrl || cleanUrl) ||
                ['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg'].includes(fileType));

            const isDoc = !isVideo && !isImage && !isPost && !isProfile && (meta.resourceType === 'document' || meta.resource_type === 'document' ||
                meta.sourceType === 'document' || meta.source_type === 'document' ||
                Boolean(documentShareId) || cleanUrl.includes('/documents/') ||
                ['pdf', 'docx', 'doc', 'pptx', 'ppt', 'txt', 'md', 'csv', 'log'].includes(fileType));

            let previewType = 'document';
            if (isProfile) previewType = 'profile';
            else if (isVideo) previewType = 'video';
            else if (isPost) previewType = 'post';
            else if (isImage) previewType = 'image';
            else if (isDoc) previewType = 'document';

            // Deterministic Page Number Resolution Order:
            // 1. Explicit structured metadata (highest priority)
            // 2. URL query parameter or hash (?page=12, #page=12)
            // 3. Fallback: parse citation text ("Page 12", "p. 12")
            let pageNumber = null;
            const rawPage = (meta.pageNumber !== undefined && meta.pageNumber !== null && meta.pageNumber !== '') ? meta.pageNumber :
                           ((meta.page_number !== undefined && meta.page_number !== null && meta.page_number !== '') ? meta.page_number : meta.page);
            if (rawPage !== undefined && rawPage !== null && String(rawPage).trim() !== '' && !isNaN(rawPage)) {
                pageNumber = Math.max(1, parseInt(rawPage, 10));
            } else {
                const queryMatch = cleanUrl.match(/[?&](?:page|p)=(\d+)/i);
                const hashMatch = cleanUrl.match(/#(?:page=?|p=?)(\d+)/i);
                if (queryMatch) {
                    pageNumber = Math.max(1, parseInt(queryMatch[1], 10));
                } else if (hashMatch) {
                    pageNumber = Math.max(1, parseInt(hashMatch[1], 10));
                } else {
                    const citText = meta.citation || meta.description || title || '';
                    const textMatch = citText.match(/\b(?:page|p\.|pp\.)\s*(\d+)\b/i);
                    if (textMatch) {
                        pageNumber = Math.max(1, parseInt(textMatch[1], 10));
                    }
                }
            }

            let category = 'Resource';
            let icon = 'bi-link-45deg';
            let badgeClass = 'bg-secondary-subtle text-secondary-emphasis';
            let primaryText = 'Open Resource';
            let primaryIcon = 'bi-box-arrow-up-right';
            let docIcon = 'bi-file-earmark-text';

            if (previewType === 'video') {
                category = 'Video';
                icon = 'bi-play-circle-fill';
                badgeClass = 'bg-danger-subtle text-danger';
                primaryText = 'Watch Video';
                primaryIcon = 'bi-play-btn-fill';
            } else if (previewType === 'image') {
                category = 'Image';
                icon = 'bi-image-fill';
                badgeClass = 'bg-info-subtle text-info';
                primaryText = 'View Full Image';
                primaryIcon = 'bi-arrows-fullscreen';
            } else if (previewType === 'document') {
                category = 'Document';
                icon = 'bi-file-earmark-pdf-fill';
                badgeClass = 'bg-primary-subtle text-primary';
                primaryText = 'Open Document';
                primaryIcon = 'bi-file-earmark-arrow-up';
                docIcon = 'bi-file-earmark-pdf-fill text-danger';
            } else if (previewType === 'post') {
                category = 'Post';
                icon = 'bi-chat-square-text-fill';
                badgeClass = 'bg-success-subtle text-success';
                primaryText = 'View Post';
                primaryIcon = 'bi-chat-square-text';
                docIcon = 'bi-chat-quote-fill text-success';
            } else if (previewType === 'profile') {
                category = 'Student Profile';
                icon = 'bi-person-vcard-fill';
                badgeClass = 'bg-primary-subtle text-primary';
                primaryText = 'Open Profile';
                primaryIcon = 'bi-person-lines-fill';
                docIcon = 'bi-person-circle text-primary';
            }

            let subtitle = category;
            if (meta.author) {
                subtitle = `${category} · By ${meta.author}`;
            } else if (meta.citation) {
                subtitle = `${category} · ${meta.citation}`;
            } else {
                subtitle = `${category} · pwaninet Repository`;
            }

            const targetSrc = previewType === 'video'
                ? (hlsUrl || mediaUrl || cleanUrl)
                : (mediaUrl || cleanUrl);

            const cleanTitle = title || meta.title || (previewType === 'document' ? 'Document' : (previewType === 'post' ? 'Post' : 'Resource'));
            const person = meta.person || meta.profile || (isProfile ? meta : {});
            const profileUsername = meta.username || person.username ||
                ((cleanUrl.match(/\/users\/user\/([^/?#]+)/i) || [])[1] || '');
            const profileUrl = meta.profileUrl || meta.profile_url || person.profile_url ||
                (profileUsername ? `/users/user/${encodeURIComponent(profileUsername)}/` : cleanUrl);
            const profileCardUrl = meta.profileCardUrl || meta.profile_card_url || person.profile_card_url ||
                `${profileUrl.replace(/\/$/, '')}/pwanimate-card/`;
            const description = meta.description || meta.content || meta.snippet || meta.citation ||
                (previewType === 'document' ? 'Official course or academic document from pwaninet repository.' :
                (previewType === 'post' ? 'Discussion post on pwaninet.' :
                (previewType === 'profile' ? [person.headline, person.academic_level, person.programme_name, person.bio].filter(Boolean).join('\n') : 'Resource referenced in this conversation.')));

            const attachmentId = meta.attachmentId || meta.attachment_id || '';
            const unitCode = meta.unitCode || meta.unit_code || '';
            const unitName = meta.unitName || meta.unit_name || '';
            const totalPages = meta.totalPages || meta.total_pages || meta.pageCount || meta.page_count || null;

            return {
                id: documentShareId || attachmentId || postId || person.id || person.username || cleanUrl,
                type: previewType,
                sourceType: sourceType,
                resourceType: resourceType,
                category: category,
                title: cleanTitle,
                subtitle: subtitle,
                url: cleanUrl,
                targetSrc: targetSrc,
                mediaUrl: mediaUrl,
                hlsUrl: hlsUrl,
                thumbnailUrl: thumbnailUrl,
                previewType: previewType,
                documentId: documentId,
                documentShareId: documentShareId,
                documentVersionId: documentVersionId,
                fileId: fileId,
                attachmentId: attachmentId,
                fileType: fileType,
                pageNumber: pageNumber,
                totalPages: totalPages,
                unitCode: unitCode,
                unitName: unitName,
                description: description,
                author: meta.author || '',
                citation: meta.citation || '',
                postId: postId,
                username: profileUsername,
                person: person,
                profileUrl: profileUrl,
                profileCardUrl: profileCardUrl,
                icon: icon,
                badgeClass: badgeClass,
                primaryText: primaryText,
                primaryIcon: primaryIcon,
                docIcon: docIcon,
                // Local offline bytes (Blob) — consumed by the viewer only;
                // stripped before context payloads are serialized.
                fileBlob: meta.fileBlob || null,
                rawMeta: meta
            };
        }

        normalizeResource(url, title, meta = {}) {
            return SparkChat.normalizeResource(url, title, meta);
        }

        previewResource(url, title, meta = {}) {
            try { performance.mark('pw:preview-click'); } catch (_) {}
            this.lastPreviewTriggerEl = meta.triggerEl || null;
            const resource = this.normalizeResource(url, title, meta);

            const isDesktop = (window.innerWidth >= 1200);
            const prefix = isDesktop ? 'desktopPreview' : 'previewSheet';

            // Check if same document is already mounted in Preview
            const isSameDoc = Boolean(
                this.previewDocViewer && this.previewResourceState &&
                this.previewResourceState.previewType === 'document' && resource.previewType === 'document' &&
                ((resource.documentShareId && this.previewResourceState.documentShareId === resource.documentShareId) ||
                 (resource.documentId && this.previewResourceState.documentId === resource.documentId) ||
                 (resource.attachmentId && this.previewResourceState.attachmentId === resource.attachmentId) ||
                 (resource.mediaUrl && this.previewResourceState.mediaUrl === resource.mediaUrl && resource.mediaUrl !== ''))
            );

            this.activeResourceDetails = resource;
            this.previewResourceState = resource;

            // Switch workspace tab to Preview (non-destructive)
            this.switchWorkspaceTab('preview');

            if (isDesktop) {
                this.openContextRail();
                const emptyState = document.getElementById('desktopPreviewEmpty');
                const activeState = document.getElementById('desktopPreviewActive');
                if (emptyState && activeState) {
                    emptyState.classList.add('d-none');
                    activeState.classList.remove('d-none');
                    activeState.classList.add('d-flex');
                }
            } else {
                const sheetEl = document.getElementById('pwanimateResourcePreviewSheet');
                if (sheetEl && window.bootstrap) {
                    let offcanvas = bootstrap.Offcanvas.getInstance(sheetEl);
                    if (!offcanvas) offcanvas = new bootstrap.Offcanvas(sheetEl);
                    offcanvas.show();
                    this._expandMobileSheetForReading(sheetEl);
                }
            }

            if (isSameDoc) {
                // Same document already mounted: navigate to page without rebuilding viewer
                if (resource.pageNumber && typeof this.previewDocViewer.goToPage === 'function') {
                    this.previewDocViewer.goToPage(resource.pageNumber);
                }
                this.updateLocationBadge(prefix, resource.pageNumber, this.previewDocViewer.getTotalPages());
            } else {
                // Render or update surface for resource
                this.renderSurface(prefix, resource, false);
            }

            this.updateAddToContextButtons();
            this.syncDocumentStudyUI();
        }

        switchWorkspaceTab(tabName) {
            const validTabs = ['preview', 'context', 'offline'];
            const targetTab = validTabs.includes(tabName) ? tabName : 'preview';
            this.activeWorkspaceTab = targetTab;

            const tabIds = {
                preview: { btn: 'desktopTabPreviewBtn', pane: 'desktopTabPreviewPane', mobBtn: 'mobileTabPreviewBtn', mobPane: 'mobileTabPreviewPane' },
                context: { btn: 'desktopTabContextBtn', pane: 'desktopTabContextPane', mobBtn: 'mobileTabContextBtn', mobPane: 'mobileTabContextPane' },
                offline: { btn: 'desktopTabOfflineBtn', pane: 'desktopTabOfflinePane', mobBtn: 'mobileTabOfflineBtn', mobPane: 'mobileTabOfflinePane' },
            };

            for (const name of validTabs) {
                const ids = tabIds[name];
                const isActive = name === targetTab;
                const deskBtn = document.getElementById(ids.btn);
                const deskPane = document.getElementById(ids.pane);
                const mobBtn = document.getElementById(ids.mobBtn);
                const mobPane = document.getElementById(ids.mobPane);
                if (deskBtn) { deskBtn.classList.toggle('active', isActive); deskBtn.setAttribute('aria-selected', isActive ? 'true' : 'false'); }
                if (deskPane) { deskPane.classList.toggle('show', isActive); deskPane.classList.toggle('active', isActive); }
                if (mobBtn) { mobBtn.classList.toggle('active', isActive); mobBtn.setAttribute('aria-selected', isActive ? 'true' : 'false'); }
                if (mobPane) { mobPane.classList.toggle('show', isActive); mobPane.classList.toggle('active', isActive); }
            }

            if (targetTab === 'preview') {
                if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                    requestAnimationFrame(() => {
                        if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                            this.previewDocViewer.handleResize();
                        }
                    });
                }
            } else if (targetTab === 'context') {
                if (this.contextResources.length > 0 && this.activeContextIndex < 0) {
                    this.activeContextIndex = 0;
                }
                this.syncContextView();

                if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                    requestAnimationFrame(() => {
                        if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                            this.contextDocViewer.handleResize();
                        }
                    });
                }
            } else if (targetTab === 'offline') {
                this.renderOfflineTab();
            }
            this.syncRailDocTitle();
            this.syncDocumentStudyUI();
        }

        /* ==========================================================================
           Document-first workspace: rail title, overflow menu, offline tab
           ========================================================================== */

        _activeRailResource() {
            if (this.activeWorkspaceTab === 'context') {
                const idx = (this.activeContextIndex >= 0) ? this.activeContextIndex : 0;
                return this.contextResources[idx] || null;
            }
            return this.previewResourceState || null;
        }

        syncRailDocTitle() {
            const el = document.getElementById('desktopRailDocTitle');
            if (!el) return;
            const resource = this._activeRailResource();
            const title = resource && resource.title ? resource.title : '';
            if (title) {
                el.textContent = title;
                el.title = title;
                el.classList.remove('d-none');
            } else {
                el.textContent = '';
                el.classList.add('d-none');
            }
            this.syncRailMenuState();
        }

        syncRailMenuState() {
            const menu = document.getElementById('desktopRailMenu');
            if (!menu) return;
            const resource = this._activeRailResource();
            const hasResource = Boolean(resource);
            const isDoc = hasResource && (resource.previewType === 'document' || resource.documentShareId || resource.fileType === 'pdf');
            const viewer = this.activeWorkspaceTab === 'context' ? this.contextDocViewer : this.previewDocViewer;
            const hasViewer = Boolean(viewer);
            const online = typeof navigator === 'undefined' || navigator.onLine !== false;
            const map = {
                'open-original': hasResource && Boolean(resource.url || resource.mediaUrl),
                'download': hasViewer || (hasResource && Boolean(resource.url)),
                'save-offline': online && isDoc && Boolean(resource.documentShareId),
                'refresh': hasViewer,
                'fullscreen': hasViewer,
                'add-context': Boolean(this.previewResourceState) && online,
                'remove-context': this.activeWorkspaceTab === 'context' && this.contextResources.length > 0,
                'close-preview': hasResource || this.contextResources.length > 0,
            };
            menu.querySelectorAll('[data-rail-action]').forEach(item => {
                const enabled = map[item.dataset.railAction] !== false;
                item.classList.toggle('disabled', !enabled);
                item.style.opacity = enabled ? '' : '0.45';
                item.style.pointerEvents = enabled ? '' : 'none';
            });
        }

        handleRailMenuAction(action) {
            const resource = this._activeRailResource();
            const isContextSurface = this.activeWorkspaceTab === 'context';
            const viewer = isContextSurface ? this.contextDocViewer : this.previewDocViewer;
            switch (action) {
                case 'open-original': {
                    const url = resource && (resource.url || resource.mediaUrl);
                    if (url) window.open(url, '_blank', 'noopener');
                    break;
                }
                case 'download': {
                    if (viewer && typeof viewer.trackAndDownload === 'function') {
                        viewer.trackAndDownload();
                    } else if (resource && resource.url) {
                        window.open(resource.url, '_blank', 'noopener');
                    }
                    break;
                }
                case 'save-offline': {
                    const shareId = resource && resource.documentShareId;
                    if (!shareId || !window.PwaniOfflineLibrary) break;
                    const lib = window.PwaniOfflineLibrary;
                    lib.save(shareId).then(() => {
                        this.renderOfflineTab();
                    }).catch(err => {
                        alert((err && err.message) || 'Could not save this document offline.');
                    });
                    break;
                }
                case 'refresh': {
                    if (viewer && typeof viewer.initialize === 'function') {
                        viewer.initialize();
                    }
                    break;
                }
                case 'fullscreen': {
                    if (viewer && typeof viewer.toggleFullscreen === 'function') {
                        viewer.toggleFullscreen();
                    }
                    break;
                }
                case 'add-context': {
                    if (this.previewResourceState) this.addResourceToContext(this.previewResourceState);
                    break;
                }
                case 'remove-context': {
                    this.removeResourceFromContext(this.activeContextIndex >= 0 ? this.activeContextIndex : 0);
                    break;
                }
                case 'close-preview': {
                    this.closePreview();
                    break;
                }
                default:
                    break;
            }
        }

        /* ---------------- Offline tab ---------------- */

        _offlineSearchBound = false;

        _bindOfflineSearchInputs() {
            if (this._offlineSearchBound) return;
            this._offlineSearchBound = true;
            ['desktopOfflineSearch', 'mobileOfflineSearch'].forEach(id => {
                const input = document.getElementById(id);
                if (!input) return;
                input.addEventListener('input', () => {
                    this._offlineQuery = input.value || '';
                    this.renderOfflineTab();
                });
            });
        }

        async renderOfflineTab() {
            this._bindOfflineSearchInputs();
            const containers = [
                document.getElementById('desktopOfflineList'),
                document.getElementById('mobileOfflineList'),
            ].filter(Boolean);
            if (!containers.length) return;
            const lib = window.PwaniOfflineLibrary;
            containers.forEach(el => {
                el.innerHTML = '<div class="text-center text-muted small py-4"><span class="spinner-border spinner-border-sm me-2"></span>Loading…</div>';
            });
            if (!lib || typeof lib.list !== 'function') {
                containers.forEach(el => { el.innerHTML = '<div class="text-muted small py-4 text-center">Offline library is unavailable.</div>'; });
                return;
            }

            let records = [];
            try { records = await lib.list(); } catch (e) { records = []; }

            // Remote catalog powers "available to save" and update detection.
            let remoteDocs = [];
            const online = typeof navigator === 'undefined' || navigator.onLine !== false;
            if (online) {
                try {
                    const resp = await fetch('/api/pwanimate/offline/library/', {
                        credentials: 'same-origin',
                        headers: { Accept: 'application/json' },
                    });
                    if (resp.ok) {
                        const data = await resp.json().catch(() => ({}));
                        remoteDocs = Array.isArray(data.documents) ? data.documents : [];
                    }
                } catch (_) { /* offline or unauthorized — saved list still renders */ }
            }

            const query = (this._offlineQuery || '').trim().toLowerCase();
            const savedIds = new Set(records.map(r => r.share_id));
            const remoteById = new Map(remoteDocs.map(d => [d.document_id, d]));

            const matches = (record) => {
                if (!query) return true;
                const hay = [record.title, record.unit_code, record.unit_name, record.category,
                    record.file && record.file.filename, record.description]
                    .filter(Boolean).join(' ').toLowerCase();
                return hay.includes(query);
            };

            const esc = (v) => String(v == null ? '' : v)
                .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
                .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
            const fmtBytes = (n) => {
                n = Number(n) || 0;
                if (n >= 1048576) return (n / 1048576).toFixed(1) + ' MB';
                if (n >= 1024) return Math.round(n / 1024) + ' KB';
                return n + ' B';
            };

            const savedHtml = records.filter(matches).map(record => {
                const shareId = esc(record.share_id);
                const title = esc(record.title || 'Untitled document');
                const unit = record.unit_code
                    ? `${esc(record.unit_code)}${record.unit_name ? ' — ' + esc(record.unit_name) : ''}`
                    : esc(record.category || 'Document');
                const size = record.file && record.file.size_bytes ? fmtBytes(record.file.size_bytes) : '';
                const pages = record.file && record.file.page_count ? `${record.file.page_count} pages` : '';
                const remote = remoteById.get(record.share_id);
                const updateAvailable = Boolean(remote && record.doc_updated_at && remote.updated_at && remote.updated_at > record.doc_updated_at);
                const hasBlob = Boolean(record.file_blob);
                const meta = [unit, size, pages, record.chunks ? `${record.chunks.length} sections` : ''].filter(Boolean).join(' · ');
                return `<div class="pwanimate-offline-item d-flex align-items-center gap-2 p-2 mb-2" role="listitem" data-share-id="${shareId}">
                    <div class="flex-grow-1 min-width-0">
                        <div class="fw-semibold small text-truncate">${title}</div>
                        <div class="text-muted" style="font-size:0.72rem;">${meta}</div>
                        <div class="d-flex align-items-center gap-1 mt-1">
                            <span class="badge rounded-pill bg-success-subtle text-success-emphasis pwanimate-offline-badge">${hasBlob ? 'On device' : 'Metadata only'}</span>
                            ${updateAvailable ? '<span class="badge rounded-pill bg-warning-subtle text-warning-emphasis pwanimate-offline-badge">Update available</span>' : ''}
                        </div>
                    </div>
                    <div class="pwanimate-offline-actions d-flex flex-column gap-1 flex-shrink-0">
                        <button type="button" class="btn btn-sm btn-primary rounded-pill px-2" data-offline-tab-action="read" data-share-id="${shareId}" ${hasBlob ? '' : 'disabled'}><i data-lucide="book-open"></i> Read</button>
                        <div class="d-flex gap-1">
                            <button type="button" class="btn btn-sm btn-outline-secondary rounded-pill px-2" data-offline-tab-action="context" data-share-id="${shareId}" title="Add to Spark context" ${online ? '' : 'disabled'}><i data-lucide="plus"></i></button>
                            ${updateAvailable ? `<button type="button" class="btn btn-sm btn-outline-warning rounded-pill px-2" data-offline-tab-action="update" data-share-id="${shareId}" title="Download latest version"><i data-lucide="refresh-cw"></i></button>` : ''}
                            <button type="button" class="btn btn-sm btn-outline-danger rounded-pill px-2" data-offline-tab-action="remove" data-share-id="${shareId}" title="Remove offline copy"><i data-lucide="trash-2"></i></button>
                        </div>
                    </div>
                </div>`;
            }).join('');

            const available = online ? remoteDocs.filter(d => !savedIds.has(d.document_id) && matches({
                title: d.title, unit_code: d.unit_code, unit_name: d.unit_name, category: d.category,
                description: d.description,
            })).slice(0, 50).map(doc => {
                const shareId = esc(doc.document_id);
                const unit = doc.unit_code ? `${esc(doc.unit_code)}${doc.unit_name ? ' — ' + esc(doc.unit_name) : ''}` : esc(doc.category || 'Document');
                const size = doc.file && doc.file.size_bytes ? fmtBytes(doc.file.size_bytes) : '';
                return `<div class="pwanimate-offline-item d-flex align-items-center gap-2 p-2 mb-2" role="listitem">
                    <div class="flex-grow-1 min-width-0">
                        <div class="fw-semibold small text-truncate">${esc(doc.title)}</div>
                        <div class="text-muted" style="font-size:0.72rem;">${unit}${size ? ' · ' + size : ''} · ${doc.chunk_count || 0} sections</div>
                    </div>
                    <button type="button" class="btn btn-sm btn-outline-primary rounded-pill px-2 flex-shrink-0" data-offline-tab-action="save" data-share-id="${shareId}"><i data-lucide="download"></i> Save</button>
                </div>`;
            }).join('') : '';

            const html = `
                ${savedHtml ? `<div class="text-muted text-uppercase fw-semibold mb-2" style="font-size:0.68rem;letter-spacing:0.04em;">Saved on this device</div>${savedHtml}` : '<div class="text-muted small py-3">No documents saved on this device yet.</div>'}
                ${available ? `<div class="text-muted text-uppercase fw-semibold mb-2 mt-3" style="font-size:0.68rem;letter-spacing:0.04em;">Available to save</div>${available}` : ''}
            `;
            containers.forEach(el => { el.innerHTML = html; });
            if (window.lucide && typeof window.lucide.createIcons === 'function') {
                try { window.lucide.createIcons(); } catch (_) {}
            }
        }

        async handleOfflineTabAction(action, shareId) {
            const lib = window.PwaniOfflineLibrary;
            if (!lib || !shareId) return;
            try {
                if (action === 'read') {
                    await this.openOfflineDocument(shareId);
                } else if (action === 'context') {
                    const resource = await this._offlineContextResource(shareId);
                    if (resource) this.addResourceToContext(resource);
                } else if (action === 'save' || action === 'update') {
                    await lib.save(shareId);
                    this.renderOfflineTab();
                } else if (action === 'remove') {
                    await lib.remove(shareId);
                    this.renderOfflineTab();
                }
            } catch (err) {
                alert((err && err.message) || 'Offline library action failed.');
            }
        }

        async _offlineContextResource(shareId) {
            const lib = window.PwaniOfflineLibrary;
            const record = await lib.get(shareId);
            if (!record) return null;
            // Prefer the local blob so the Context tab can render the same PDF
            // without a network fetch; fall back to no media when bytes are gone.
            let blobUrl = '';
            let fileBlob = null;
            if (record.file_blob) {
                if (typeof lib.getBlobUrl === 'function') {
                    try { blobUrl = await lib.getBlobUrl(shareId); } catch (_) { blobUrl = ''; }
                }
                if (typeof lib.getBlob === 'function') {
                    try { fileBlob = await lib.getBlob(shareId); } catch (_) { fileBlob = null; }
                }
            }
            return this.normalizeResource(`/documents/document/${shareId}/`, record.title || 'Document', {
                mediaUrl: blobUrl,
                fileBlob: fileBlob,
                fileType: (record.file && record.file.extension) || 'pdf',
                resourceType: 'document',
                documentShareId: shareId,
                documentId: record.document_pk || shareId,
                documentVersionId: record.document_version_id || '',
                fileId: (record.file && record.file.file_id) || '',
                pageNumber: 1,
                url: `/documents/document/${shareId}/`,
                thumbnailUrl: (record.file && record.file.preview_url) || '',
            });
        }

        async openOfflineDocument(shareId) {
            const lib = window.PwaniOfflineLibrary;
            if (!lib) return;
            const record = await lib.get(shareId);
            if (!record) {
                alert('This document is no longer saved on this device.');
                this.renderOfflineTab();
                return;
            }
            if (!record.file_blob) {
                alert('The local file for this document is missing. Re-download it while online.');
                return;
            }
            let blobUrl;
            let fileBlob = null;
            try {
                blobUrl = await lib.getBlobUrl(shareId);
                if (typeof lib.getBlob === 'function') {
                    fileBlob = await lib.getBlob(shareId);
                }
            } catch (err) {
                alert('The saved file appears to be corrupted. Remove it and save it again while online.');
                return;
            }
            const file = record.file || {};
            const ext = (file.extension || 'pdf').toLowerCase();
            const meta = {
                mediaUrl: blobUrl,
                fileBlob: fileBlob,
                fileType: ext,
                resourceType: 'document',
                documentShareId: shareId,
                documentId: record.document_pk || shareId,
                documentVersionId: record.document_version_id || '',
                fileId: file.file_id || '',
                pageNumber: 1,
                thumbnailUrl: file.preview_url || '',
            };
            this.previewResource(`/documents/document/${shareId}/`, record.title || 'Document', meta);
        }

        addResourceToContext(resource) {
            if (!resource) return;

            // Deduplicate
            const existingIndex = this.contextResources.findIndex(item =>
                (resource.documentShareId && item.documentShareId === resource.documentShareId) ||
                (resource.documentId && item.documentId === resource.documentId) ||
                (resource.attachmentId && item.attachmentId === resource.attachmentId) ||
                (resource.mediaUrl && item.mediaUrl === resource.mediaUrl) ||
                (resource.url && item.url === resource.url && item.title === resource.title)
            );

            if (existingIndex >= 0) {
                this.activeContextIndex = existingIndex;
                if (resource.pageNumber) {
                    this.contextResources[existingIndex].pageNumber = resource.pageNumber;
                }
            } else {
                const contextItem = Object.assign({}, resource, {
                    pageNumber: resource.pageNumber || 1
                });
                this.contextResources.push(contextItem);
                this.activeContextIndex = this.contextResources.length - 1;
            }

            this.updateContextCountBadges();
            this.updateAddToContextButtons();
            this.syncContextView();
            this.syncDocumentStudyUI();
            this.scheduleStudyContextSync();
            this.switchWorkspaceTab('context');
        }

        _resourceStateKey(details) {
            // Stable identity for page-state tracking: prefer the public share
            // UUID, then internal id, attachment id, media URL, or page URL.
            if (!details) return '';
            return String(
                details.documentShareId || details.documentId ||
                (details.attachmentId ? 'att:' + details.attachmentId : '') ||
                details.mediaUrl || details.url || ''
            );
        }

        getContextResourcesForRequest() {
            const resources = [...this.contextResources];
            const preview = this.previewResourceState;
            if (!preview) return this._snapshotResourcePages(resources);

            const alreadyIncluded = resources.some(item =>
                (preview.documentId && item.documentId === preview.documentId) ||
                (preview.documentShareId && item.documentShareId === preview.documentShareId) ||
                (preview.attachmentId && item.attachmentId === preview.attachmentId) ||
                (preview.mediaUrl && item.mediaUrl === preview.mediaUrl) ||
                (preview.url && item.url === preview.url)
            );
            if (!alreadyIncluded) resources.push(preview);
            return this._snapshotResourcePages(resources);
        }

        _snapshotResourcePages(resources) {
            // Immutable request-time snapshot: overlay the live viewer page
            // state onto a shallow copy so in-flight requests keep the page
            // that was visible when the message was sent.
            return (resources || []).map(item => {
                const { fileBlob, ...safeItem } = item || {};
                const key = this._resourceStateKey(safeItem);
                const state = key ? this.viewerPageState[key] : null;
                if (!state || !state.page) return safeItem;
                return {
                    ...safeItem,
                    pageNumber: state.page,
                    totalPages: state.totalPages || safeItem.totalPages || null,
                };
            });
        }

        removeResourceFromContext(index) {
            if (index < 0 || index >= this.contextResources.length) return;

            if (this.contextDocViewer && index === this.activeContextIndex) {
                try { this.contextDocViewer.destroy(); } catch (e) {}
                this.contextDocViewer = null;
            }

            this.contextResources.splice(index, 1);

            if (this.contextResources.length === 0) {
                this.activeContextIndex = -1;
            } else if (index < this.activeContextIndex) {
                this.activeContextIndex -= 1;
            } else if (this.activeContextIndex >= this.contextResources.length) {
                this.activeContextIndex = this.contextResources.length - 1;
            }

            this.updateContextCountBadges();
            this.updateAddToContextButtons();
            this.syncContextView();
            this.syncDocumentStudyUI();
            this.scheduleStudyContextSync();
            if (this.contextResources.length === 0 && !this.previewResourceState) {
                this.closeContextRail();
            }
        }

        selectContextResource(index) {
            if (index < 0 || index >= this.contextResources.length) return;
            if (this.activeContextIndex === index) return;

            this.activeContextIndex = index;
            this.syncContextView();
            this.syncDocumentStudyUI();
            this.scheduleStudyContextSync();
        }

        getActiveStudyDocument() {
            if (this.activeContextIndex >= 0 && this.activeContextIndex < this.contextResources.length) {
                const candidate = this.contextResources[this.activeContextIndex];
                if (candidate && candidate.previewType === 'document') {
                    return candidate;
                }
            }
            const firstContextDoc = (this.contextResources || []).find(item => item && item.previewType === 'document');
            if (firstContextDoc) return firstContextDoc;
            if (this.previewResourceState && this.previewResourceState.previewType === 'document') {
                return this.previewResourceState;
            }
            return null;
        }

        syncDocumentStudyUI() {
            const docResource = this.getActiveStudyDocument();
            const studyCard = document.getElementById('pwanimate-document-study-card');
            const liveBar = document.getElementById('pwanimate-live-page-bar');

            if (!docResource) {
                if (studyCard) studyCard.classList.add('d-none');
                if (liveBar) liveBar.classList.add('d-none');
                return;
            }

            const page = Math.max(1, parseInt(docResource.pageNumber || 1, 10));
            const totalPages = docResource.totalPages ? parseInt(docResource.totalPages, 10) : null;
            const title = docResource.title || 'Document';
            const unitCode = (docResource.unitCode || '').trim();
            const unitName = (docResource.unitName || '').trim();
            const unitBadgeText = unitCode
                ? (unitName ? `${unitCode} · ${unitName}` : unitCode)
                : ((docResource.citation && docResource.citation !== 'Document') ? docResource.citation : '');

            if (studyCard) {
                studyCard.classList.remove('d-none');
                const cardTitle = document.getElementById('pwanimate-study-card-title');
                const cardUnit = document.getElementById('pwanimate-study-card-unit');
                const cardPage = document.getElementById('pwanimate-study-card-page');
                const btnExplain = document.getElementById('pwanimate-study-btn-explain');
                const btnTakeaways = document.getElementById('pwanimate-study-btn-takeaways');
                const btnQuiz = document.getElementById('pwanimate-study-btn-quiz');

                if (cardTitle) cardTitle.textContent = title;
                if (cardUnit) {
                    if (unitBadgeText) {
                        cardUnit.textContent = unitBadgeText;
                        cardUnit.classList.remove('d-none');
                    } else {
                        cardUnit.classList.add('d-none');
                    }
                }
                if (cardPage) {
                    const pageLabel = totalPages ? `Viewing Page ${page} of ${totalPages}` : `Viewing Page ${page}`;
                    cardPage.innerHTML = `<i data-lucide="file-text" class="me-1"></i><span>${escapeHtml(pageLabel)}</span>`;
                }
                if (btnExplain) btnExplain.textContent = `Explain Page ${page}`;
                if (btnTakeaways) btnTakeaways.textContent = `Key takeaways from Page ${page}`;
                if (btnQuiz) btnQuiz.textContent = `Quiz me on Page ${page}`;
            }

            if (liveBar) {
                liveBar.classList.remove('d-none');
                const barTitle = document.getElementById('pwanimate-live-page-title');
                const barUnit = document.getElementById('pwanimate-live-page-unit');
                const barBadge = document.getElementById('pwanimate-live-page-badge');
                const pillExplain = document.getElementById('pwanimate-live-pill-explain');
                const pillTakeaways = document.getElementById('pwanimate-live-pill-takeaways');
                const pillQuiz = document.getElementById('pwanimate-live-pill-quiz');

                if (barTitle) {
                    barTitle.textContent = title;
                    barTitle.title = title;
                }
                if (barUnit) {
                    if (unitCode) {
                        barUnit.textContent = unitCode;
                        barUnit.classList.remove('d-none');
                    } else {
                        barUnit.classList.add('d-none');
                    }
                }
                if (barBadge) {
                    barBadge.textContent = totalPages ? `p. ${page} / ${totalPages}` : `p. ${page}`;
                }
                if (pillExplain) pillExplain.textContent = `Explain Page ${page}`;
                if (pillTakeaways) pillTakeaways.textContent = `Key takeaways (p. ${page})`;
                if (pillQuiz) pillQuiz.textContent = `Quiz Page ${page}`;
            }
        }

        triggerDocumentStudyAction(actionType) {
            if (this.isGenerating || !this.input) return;
            const docResource = this.getActiveStudyDocument();
            const page = Math.max(1, parseInt((docResource && docResource.pageNumber) || 1, 10));
            const title = (docResource && docResource.title) ? docResource.title : 'this document';

            let prompt = '';
            if (actionType === 'summarize_book') {
                prompt = `Give me a comprehensive summary of the entire book "${title}", including its overall structure, main sections, and core takeaways.`;
            } else if (actionType === 'explain_page') {
                prompt = `Explain the concepts and content on Page ${page} of "${title}" clearly step by step.`;
            } else if (actionType === 'page_takeaways') {
                prompt = `What are the key takeaways, definitions, and important points on Page ${page} of "${title}"?`;
            } else if (actionType === 'quiz_page') {
                prompt = `Quiz me on the material from Page ${page} of "${title}" with 3 focused practice questions.`;
            }
            if (!prompt) return;

            this.input.value = prompt;
            this.updateSendButtonState();
            this.handleSend();
        }

        updateContextCountBadges() {
            const countStr = this.contextResources.length.toString();
            const deskBadge = document.getElementById('desktopContextCountBadge');
            const mobBadge = document.getElementById('mobileContextCountBadge');
            const mobileComposerBadge = document.getElementById('pwanimateMobileContextSheetCount');
            if (deskBadge) deskBadge.textContent = countStr;
            if (mobBadge) mobBadge.textContent = countStr;
            if (mobileComposerBadge) {
                mobileComposerBadge.textContent = countStr;
                mobileComposerBadge.classList.toggle('d-none', this.contextResources.length === 0);
            }
        }

        updateAddToContextButtons() {
            const isAdded = Boolean(this.previewResourceState && this.contextResources.some(item =>
                (this.previewResourceState.documentId && item.documentId === this.previewResourceState.documentId) ||
                (this.previewResourceState.attachmentId && item.attachmentId === this.previewResourceState.attachmentId) ||
                (this.previewResourceState.mediaUrl && item.mediaUrl === this.previewResourceState.mediaUrl) ||
                (this.previewResourceState.url && item.url === this.previewResourceState.url && item.title === this.previewResourceState.title)
            ));

            const deskBtn = document.getElementById('desktopPreviewAddToContextBtn');
            const mobBtn = document.getElementById('previewSheetAddToContextBtn');

            if (deskBtn) {
                if (isAdded) {
                    deskBtn.className = 'btn btn-sm btn-outline-success rounded-pill px-2 py-0 d-inline-flex align-items-center gap-1 disabled';
                    deskBtn.innerHTML = '<i data-lucide="check"></i> <span>In Context</span>';
                } else {
                    deskBtn.className = 'btn btn-sm btn-primary rounded-pill px-2 py-0 d-inline-flex align-items-center gap-1';
                    deskBtn.innerHTML = '<i data-lucide="plus"></i> <span id="desktopPreviewAddToContextText">+ Add to Context</span>';
                }
            }

            if (mobBtn) {
                if (isAdded) {
                    mobBtn.className = 'btn btn-sm btn-outline-success rounded-pill px-2 py-0 d-inline-flex align-items-center gap-1 disabled';
                    mobBtn.innerHTML = '<i data-lucide="check"></i> <span>In Context</span>';
                } else {
                    mobBtn.className = 'btn btn-sm btn-primary rounded-pill px-2 py-0 d-inline-flex align-items-center gap-1';
                    mobBtn.innerHTML = '<i data-lucide="plus"></i> <span id="previewSheetAddToContextText">+ Add to Context</span>';
                }
            }
        }

        syncContextView() {
            const deskEmpty = document.getElementById('desktopContextEmpty');
            const deskActive = document.getElementById('desktopContextActive');
            const mobEmpty = document.getElementById('mobileContextEmpty');
            const mobActive = document.getElementById('mobileContextActive');

            if (this.contextResources.length === 0) {
                if (deskEmpty) deskEmpty.classList.remove('d-none');
                if (deskActive) { deskActive.classList.remove('d-flex'); deskActive.classList.add('d-none'); }
                if (mobEmpty) mobEmpty.classList.remove('d-none');
                if (mobActive) { mobActive.classList.remove('d-flex'); mobActive.classList.add('d-none'); }

                if (this.contextDocViewer) {
                    try { this.contextDocViewer.destroy(); } catch (e) {}
                    this.contextDocViewer = null;
                }
                this.syncDocumentStudyUI();
                return;
            }

            if (deskEmpty) deskEmpty.classList.add('d-none');
            if (deskActive) { deskActive.classList.remove('d-none'); deskActive.classList.add('d-flex'); }
            if (mobEmpty) mobEmpty.classList.add('d-none');
            if (mobActive) { mobActive.classList.remove('d-none'); mobActive.classList.add('d-flex'); }

            const deskChips = document.getElementById('desktopContextChipsList');
            const mobChips = document.getElementById('mobileContextChipsList');

            const chipsHtml = this.contextResources.map((res, idx) => {
                const isActive = idx === this.activeContextIndex;
                const safeTitle = escapeHtml(res.title || 'Resource');
                const chipIcon = sparkIconSpec(res.icon || 'bi-file-earmark');
                const safeIcon = escapeHtml(chipIcon.name);
                const chipIconExtra = escapeHtml(chipIcon.extra);
                return `
                    <div class="pwanimate-context-chip ${isActive ? 'active' : ''}" data-index="${idx}" role="button" tabindex="0">
                        <i data-lucide="${safeIcon}" class="me-1${chipIconExtra ? ' ' + chipIconExtra : ''}"></i>
                        <span class="pwanimate-chip-title" title="${safeTitle}">${safeTitle}</span>
                        <button type="button" class="pwanimate-chip-remove" data-index="${idx}" aria-label="Remove ${safeTitle}">
                            <i data-lucide="x"></i>
                        </button>
                    </div>
                `;
            }).join('');

            if (deskChips) deskChips.innerHTML = chipsHtml;
            if (mobChips) mobChips.innerHTML = chipsHtml;

            const activeResource = this.contextResources[this.activeContextIndex];
            if (activeResource) {
                if (window.innerWidth >= 1200) {
                    this.renderSurface('desktopContext', activeResource, true);
                } else {
                    this.renderSurface('mobileContext', activeResource, true);
                }
            }
            this.syncRailDocTitle();
            this.syncDocumentStudyUI();
        }

        renderSurface(prefix, details, isContext = false) {
            let container = null;
            if (prefix === 'desktopPreview') {
                container = document.getElementById('desktopPreviewActive');
            } else if (prefix === 'previewSheet') {
                container = document.getElementById('pwanimateResourcePreviewSheet');
            } else if (prefix === 'desktopContext') {
                container = document.getElementById('desktopContextSurfaceCard');
            } else if (prefix === 'mobileContext') {
                container = document.getElementById('mobileContextActive');
            }
            if (!container || !details) return;

            container.classList.toggle('pwanimate-profile-mode', details.previewType === 'profile');

            // 1. Header category badge
            const categoryBadge = container.querySelector(`#${prefix}CategoryBadge`);
            const categoryIcon = container.querySelector(`#${prefix}CategoryIcon`);
            const categoryText = container.querySelector(`#${prefix}CategoryText`);
            if (categoryBadge) {
                categoryBadge.className = `pwanimate-preview-badge badge rounded-pill px-2 py-1 ${details.badgeClass}`;
            }
            if (categoryIcon) {
                sparkSetIcon(categoryIcon, details.icon);
            }
            if (categoryText) {
                categoryText.textContent = details.category;
            }

            // Location badge (e.g. p. 1)
            const locBadge = container.querySelector(`#${prefix}LocationBadge`);
            const locText = container.querySelector(`#${prefix}LocationText`);
            if (locBadge && locText) {
                if (details.pageNumber) {
                    locBadge.classList.remove('d-none');
                    locText.textContent = details.totalPages ? `p. ${details.pageNumber} / ${details.totalPages}` : `p. ${details.pageNumber}`;
                } else {
                    locBadge.classList.add('d-none');
                }
            }

            // 2. Body hosts
            const video = container.querySelector(`#${prefix}Video`);
            const imgContainer = container.querySelector(`#${prefix}ImageContainer`);
            const img = container.querySelector(`#${prefix}Image`);
            const docViewer = container.querySelector(`#${prefix}DocViewer`);
            const docContainer = container.querySelector(`#${prefix}DocContainer`);
            const spinner = container.querySelector(`#${prefix}Spinner`);
            const iframe = container.querySelector(`#${prefix}Iframe`);

            // Reset hosts
            if (video) {
                if (video._hlsInstance) {
                    try { video._hlsInstance.destroy(); } catch (e) {}
                    delete video._hlsInstance;
                }
                delete video.dataset.hlsReady;
                video.removeAttribute('data-hls-url');
                video.removeAttribute('data-video-url');
                video.poster = '';
                video.pause();
                video.src = '';
                video.classList.add('d-none');
            }
            if (imgContainer) { imgContainer.classList.add('d-none'); if (img) img.src = ''; }
            if (iframe) { iframe.classList.add('d-none'); iframe.src = ''; }
            if (docContainer) { docContainer.classList.add('d-none'); }
            if (spinner) spinner.classList.add('d-none');

            // Clean up previous doc viewer if present for this pane
            if (isContext && this.contextDocViewer) {
                try { this.contextDocViewer.destroy(); } catch (e) {}
                this.contextDocViewer = null;
            } else if (!isContext && this.previewDocViewer) {
                try { this.previewDocViewer.destroy(); } catch (e) {}
                this.previewDocViewer = null;
            }
            if (docViewer) {
                docViewer.innerHTML = '';
                docViewer.classList.add('d-none');
                docViewer.classList.remove('d-flex');
            }

            const fileType = (details.fileType || this.inferFileType(details.mediaUrl || details.url || '')).toLowerCase();
            const canUseDocViewer = Boolean(
                window.DocumentViewer &&
                docViewer &&
                details.mediaUrl &&
                ['pdf', 'docx', 'doc', 'pptx', 'ppt', 'md', 'txt', 'text', 'csv', 'log'].includes(fileType)
            );

            if (details.previewType === 'video' && video) {
                video.autoplay = true;
                video.playsInline = true;
                video.muted = false;
                if (details.thumbnailUrl) {
                    video.poster = details.thumbnailUrl;
                }
                const hlsStreamUrl = details.hlsUrl || (/\.m3u8/i.test(details.targetSrc) ? details.targetSrc : '');
                const fallbackMp4Url = (!/\.m3u8/i.test(details.targetSrc)) ? details.targetSrc : (details.mediaUrl || '');

                if (hlsStreamUrl) {
                    video.dataset.hlsUrl = hlsStreamUrl;
                    if (fallbackMp4Url && fallbackMp4Url !== hlsStreamUrl) {
                        video.dataset.videoUrl = fallbackMp4Url;
                    }
                    if (typeof window.initHLSForElement === 'function') {
                        window.initHLSForElement(video);
                    } else if (typeof window.Hls !== 'undefined' && window.Hls.isSupported()) {
                        const hls = new window.Hls({
                            startLevel: 0,
                            abrEwmaDefaultEstimate: 400000,
                            capLevelToPlayerSize: true,
                        });
                        hls.loadSource(hlsStreamUrl);
                        hls.attachMedia(video);
                        video._hlsInstance = hls;
                    } else if (video.canPlayType('application/vnd.apple.mpegurl')) {
                        video.src = hlsStreamUrl;
                    } else if (fallbackMp4Url) {
                        video.src = fallbackMp4Url;
                    }
                } else if (details.targetSrc) {
                    video.src = details.targetSrc;
                }
                video.classList.remove('d-none');
                const tryPlayback = () => {
                    const playback = video.play();
                    if (playback && typeof playback.catch === 'function') {
                        playback.catch((error) => {
                            if (error && error.name === 'NotAllowedError') {
                                video.muted = true;
                                video.play().catch(() => {});
                            }
                        });
                    }
                };
                video.addEventListener('loadeddata', tryPlayback, { once: true });
                tryPlayback();
            } else if (details.previewType === 'image' && imgContainer && img) {
                if (spinner) spinner.classList.remove('d-none');
                img.onload = () => { if (spinner) spinner.classList.add('d-none'); };
                img.onerror = () => { if (spinner) spinner.classList.add('d-none'); };
                img.src = details.targetSrc;
                imgContainer.classList.remove('d-none');
            } else if (canUseDocViewer) {
                docViewer.classList.remove('d-none');
                docViewer.classList.add('d-flex');

                const initialPage = (details.pageNumber && !isNaN(details.pageNumber)) ? parseInt(details.pageNumber, 10) : 1;
                const viewerStateKey = this._resourceStateKey(details);
                // Seed canonical page state so a document that is never
                // navigated still reports its mounted page at send time.
                if (viewerStateKey) {
                    this.viewerPageState[viewerStateKey] = {
                        page: initialPage,
                        totalPages: details.totalPages || null,
                    };
                }
                const options = {
                    compact: true,
                    fitContainer: true,
                    minimalChrome: true,
                    initialPage: initialPage,
                    showDownload: true,
                    showFullscreen: true,
                    fileBlob: details.fileBlob || null,
                    onPageChange: (page, totalPages) => {
                        details.pageNumber = page;
                        if (totalPages) details.totalPages = totalPages;
                        // Write canonical state by document identity — the
                        // `details` object may be replaced by re-preview/remount,
                        // but this key always resolves to the live page.
                        if (viewerStateKey) {
                            this.viewerPageState[viewerStateKey] = {
                                page: page,
                                totalPages: totalPages || details.totalPages || null,
                            };
                        }
                        if (locBadge && locText) {
                            locBadge.classList.remove('d-none');
                            locText.textContent = totalPages ? `p. ${page} / ${totalPages}` : `p. ${page}`;
                        }
                        this.syncDocumentStudyUI();
                        this.scheduleStudyContextSync();
                    }
                };
                const viewerInstance = new window.DocumentViewer(
                    docViewer,
                    details.mediaUrl,
                    fileType,
                    details.title || 'Document',
                    details.documentId || null,
                    details.documentShareId || null,
                    options
                );
                if (isContext) {
                    this.contextDocViewer = viewerInstance;
                } else {
                    this.previewDocViewer = viewerInstance;
                }
                viewerInstance.initialize().then(() => {
                    try {
                        performance.mark('pw:preview-viewer-ready');
                        performance.measure('pw:preview-to-viewer', 'pw:preview-click', 'pw:preview-viewer-ready');
                    } catch (_) {}
                    if (typeof viewerInstance.getTotalPages === 'function') {
                        const tp = viewerInstance.getTotalPages();
                        if (tp) {
                            details.totalPages = tp;
                            if (viewerStateKey) {
                                const st = this.viewerPageState[viewerStateKey] || {};
                                this.viewerPageState[viewerStateKey] = { page: st.page || initialPage, totalPages: tp };
                            }
                            this.syncDocumentStudyUI();
                        }
                    }
                    if (window.ResizeObserver && !docViewer._resizeObs) {
                        docViewer._resizeObs = new ResizeObserver(() => {
                            if (typeof viewerInstance.handleResize === 'function') {
                                viewerInstance.handleResize();
                            }
                        });
                        docViewer._resizeObs.observe(docViewer);
                    }
                }).catch((err) => {
                    console.warn('[Spark] DocumentViewer initialization failed, falling back to doc card:', err);
                    docViewer.classList.remove('d-flex');
                    docViewer.classList.add('d-none');
                    this.renderDocCard(container, prefix, details);
                });
            } else if (docContainer) {
                this.renderDocCard(container, prefix, details);
            }

            // 3. Footer info
            const titleEl = container.querySelector(`#${prefix}Title`);
            const subtitleEl = container.querySelector(`#${prefix}Subtitle`);
            if (titleEl) titleEl.textContent = details.title;
            if (subtitleEl) subtitleEl.textContent = details.subtitle;

            // Primary action button
            const primaryAction = container.querySelector(`#${prefix}PrimaryAction`);
            const primaryTextEl = container.querySelector(`#${prefix}PrimaryText`);
            const primaryIconEl = container.querySelector(`#${prefix}PrimaryIcon`);
            if (primaryTextEl) primaryTextEl.textContent = details.primaryText;
            if (primaryIconEl) sparkSetIcon(primaryIconEl, details.primaryIcon);

            if (primaryAction) {
                primaryAction.href = details.url;
                const isInternal = details.url.startsWith('/') || (details.url.startsWith('http') && details.url.startsWith(window.location.origin));
                if (isInternal) {
                    primaryAction.removeAttribute('hx-get');
                    primaryAction.removeAttribute('hx-target');
                    primaryAction.removeAttribute('hx-swap');
                    primaryAction.removeAttribute('hx-push-url');
                    primaryAction.setAttribute('data-bypass-htmx', 'true');
                    primaryAction.setAttribute('hx-boost', 'false');
                    primaryAction.removeAttribute('target');
                    primaryAction.onclick = (e) => {
                        if (prefix.startsWith('previewSheet') || prefix.startsWith('mobileContext')) {
                            const sheet = document.getElementById('pwanimateResourcePreviewSheet');
                            if (sheet && window.bootstrap) {
                                const inst = bootstrap.Offcanvas.getInstance(sheet);
                                if (inst) inst.hide();
                            }
                        }
                        window.location.href = details.url;
                    };
                } else {
                    primaryAction.removeAttribute('hx-get');
                    primaryAction.setAttribute('target', '_blank');
                    primaryAction.setAttribute('rel', 'noopener');
                    primaryAction.onclick = null;
                }
            }

            // Secondary action button (original link)
            const origLink = container.querySelector(`#${prefix}OriginalLink`);
            if (origLink) {
                origLink.href = details.url;
                origLink.setAttribute('target', '_blank');
                origLink.setAttribute('rel', 'noopener');
            }

            if (details.previewType === 'profile') {
                this.loadProfilePreviewCard(container, prefix, details);
            }
            this.syncRailDocTitle();
        }

        async loadProfilePreviewCard(container, prefix, details) {
            const docContainer = container.querySelector(`#${prefix}DocContainer`);
            if (!docContainer) return;
            const profileUrl = details.profileUrl || details.url;
            if (!profileUrl) return;
            const cardUrl = details.profileCardUrl || `${profileUrl.replace(/\/$/, '')}/pwanimate-card/`;
            docContainer.dataset.profileCardUrl = cardUrl;
            docContainer.classList.remove('text-center', 'align-items-center', 'justify-content-center');
            docContainer.classList.add('align-items-stretch', 'justify-content-start', 'p-2');
            docContainer.innerHTML = '<div class="spinner-border spinner-border-sm text-primary m-auto" role="status" aria-label="Loading profile"></div>';
            try {
                const response = await fetch(cardUrl, { credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
                if (!response.ok) throw new Error(`Profile card request failed (${response.status})`);
                const html = await response.text();
                if (!html.includes('pwanimate-mini-profile')) throw new Error('The profile endpoint did not return the profile card fragment.');
                if (docContainer.dataset.profileCardUrl !== cardUrl) return;
                docContainer.innerHTML = html;
            } catch (error) {
                if (docContainer.dataset.profileCardUrl !== cardUrl) return;
                docContainer.innerHTML = '<div class="small text-muted text-center m-auto">This profile preview is unavailable.</div>';
                console.warn('[Spark] Profile card preview could not be loaded:', error);
            }
        }

        renderDocCard(container, prefix, details) {
            const docContainer = container.querySelector(`#${prefix}DocContainer`);
            if (!docContainer) return;
            const docThumbWrapper = container.querySelector(`#${prefix}DocThumbnailWrapper`);
            const docThumb = container.querySelector(`#${prefix}DocThumbnail`);
            const docIconWrapper = container.querySelector(`#${prefix}DocIconWrapper`);
            const docIcon = container.querySelector(`#${prefix}DocIcon`);
            const docTitle = container.querySelector(`#${prefix}DocTitle`);
            const docMeta = container.querySelector(`#${prefix}DocMeta`);
            const docDesc = container.querySelector(`#${prefix}DocDescription`);

            if (details.thumbnailUrl && docThumb && docThumbWrapper) {
                docThumb.src = details.thumbnailUrl;
                docThumbWrapper.classList.remove('d-none');
                if (docIconWrapper) docIconWrapper.classList.add('d-none');
            } else {
                if (docThumbWrapper) docThumbWrapper.classList.add('d-none');
                if (docThumb) docThumb.src = '';
                if (docIconWrapper) docIconWrapper.classList.remove('d-none');
            }
            if (docIcon) sparkSetIcon(docIcon, details.docIcon);
            if (docTitle) docTitle.textContent = details.title;
            if (docMeta) docMeta.textContent = details.subtitle;
            if (docDesc) docDesc.textContent = details.description;
            docContainer.classList.remove('d-none');
        }

        inferFileType(url) {
            if (!url || typeof url !== 'string') return '';
            const cleanUrl = url.split('?')[0].split('#')[0];
            const ext = cleanUrl.split('.').pop().toLowerCase();
            return ext && ext.length <= 5 ? ext : '';
        }

        populatePreviewCard(container, details, mode) {
            const prefix = mode === 'desktop' ? 'desktopPreview' : 'previewSheet';
            this.renderSurface(prefix, details, false);
        }

        closePreview() {
            // Dismiss active preview card without closing the context rail (transitions State C -> State B)
            this.activeResourceDetails = null;
            this.previewResourceState = null;

            if (this.previewDocViewer) {
                try { this.previewDocViewer.destroy(); } catch (e) {}
                this.previewDocViewer = null;
            }
            this.syncDocumentStudyUI();

            // Desktop
            const emptyState = document.getElementById('desktopPreviewEmpty');
            const activeState = document.getElementById('desktopPreviewActive');
            if (emptyState && activeState) {
                activeState.classList.remove('d-flex');
                activeState.classList.add('d-none');
                emptyState.classList.remove('d-none');
                this.resetMediaElements(activeState);
            }

            // Mobile
            const sheet = document.getElementById('pwanimateResourcePreviewSheet');
            if (sheet && window.bootstrap) {
                const inst = bootstrap.Offcanvas.getInstance(sheet);
                if (inst) inst.hide();
                this.resetMediaElements(sheet);
            }

            this.updateAddToContextButtons();
            this.syncRailDocTitle();

            if (this.contextResources.length === 0) {
                this.closeContextRail();
            }

            // Restore focus
            if (this.lastPreviewTriggerEl && typeof this.lastPreviewTriggerEl.focus === 'function') {
                this.lastPreviewTriggerEl.focus();
                this.lastPreviewTriggerEl = null;
            }
        }

        /* ==========================================================================
           Phase 1 Workspace Geometry, State & Drag Resizer Implementation
           ========================================================================== */

        initWorkspaceGeometry() {
            // 1. Synchronize Left Rail Collapsed State
            this.syncLeftRailState(this.leftRailCollapsed);

            // 2. Bind Left Rail Toggle Buttons
            const collapseBtn = document.getElementById('pwanimateLeftRailCollapseBtn');
            const expandBtn = document.getElementById('pwanimateLeftRailExpandBtn');
            if (collapseBtn) {
                collapseBtn.onclick = (e) => {
                    e.preventDefault();
                    this.toggleLeftRail(true);
                };
            }
            if (expandBtn) {
                expandBtn.onclick = (e) => {
                    e.preventDefault();
                    this.toggleLeftRail(false);
                };
            }

            // 3. Initialize Context Rail Drag Resizer
            this.initRailResizer();

            // 4. Apply Initial Context Rail State on Desktop
            if (this.contextRailOpen && window.innerWidth >= 1200) {
                this.openContextRail();
            } else {
                this.closeContextRail();
            }

            // 6. Bind Responsive Window Resize (dynamic viewport constraint handling)
            if (this._onWindowResize) {
                window.removeEventListener('resize', this._onWindowResize);
            }
            this._onWindowResize = () => {
                if (this.contextRailOpen && window.innerWidth >= 1200) {
                    this.updateContextRailWidth(false); // does NOT overwrite savedPreferredWidth
                }
                this.syncContextRailUI();
                if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                    this.previewDocViewer.handleResize();
                }
                if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                    this.contextDocViewer.handleResize();
                }
            };
            window.addEventListener('resize', this._onWindowResize);

            // 7. Keep transcript anchored when virtual keyboard opens or composer expands
            if (this._transcriptResizeObserver) {
                try { this._transcriptResizeObserver.disconnect(); } catch (_) {}
                this._transcriptResizeObserver = null;
            }
            if (this.transcript && typeof ResizeObserver !== 'undefined') {
                let lastTranscriptHeight = this.transcript.clientHeight;
                this._transcriptResizeObserver = new ResizeObserver(() => {
                    if (!this.transcript) return;
                    const newHeight = this.transcript.clientHeight;
                    const deltaH = lastTranscriptHeight - newHeight;
                    lastTranscriptHeight = newHeight;

                    if (deltaH > 0) {
                        const distFromBottomBefore = this.transcript.scrollHeight - this.transcript.scrollTop - newHeight - deltaH;
                        if (distFromBottomBefore < 140) {
                            this.transcript.scrollTop = this.transcript.scrollHeight;
                        } else {
                            this.transcript.scrollTop += deltaH;
                        }
                    }
                });
                this._transcriptResizeObserver.observe(this.transcript);
            }
        }

        toggleLeftRail(collapsed) {
            this.leftRailCollapsed = (collapsed !== undefined) ? collapsed : !this.leftRailCollapsed;
            try {
                localStorage.setItem('pwanimate_left_rail_collapsed', this.leftRailCollapsed ? 'true' : 'false');
            } catch (e) {}
            this.syncLeftRailState(this.leftRailCollapsed);
            if (this.contextRailOpen && window.innerWidth >= 1200) {
                this.updateContextRailWidth(false);
            }
        }

        syncLeftRailState(collapsed) {
            if (collapsed) {
                document.documentElement.classList.add('pwanimate-left-collapsed');
                document.body.classList.add('pwanimate-left-collapsed');
            } else {
                document.documentElement.classList.remove('pwanimate-left-collapsed');
                document.body.classList.remove('pwanimate-left-collapsed');
            }
        }

        calculateEffectiveContextLimits() {
            const availableWidth = window.innerWidth;
            const leftWidth = this.leftRailCollapsed ? 56 : 240;
            const minCenterWidth = 480;
            const configuredMin = 280;

            // Default target: an equal split of the workspace remaining after
            // the global navigation rail. The chat pane keeps a usable minimum;
            // on narrow desktops the rail yields first.
            const splitWidth = Math.floor((availableWidth - leftWidth) / 2);
            const effectiveMax = Math.max(
                configuredMin,
                Math.min(splitWidth, availableWidth - leftWidth - minCenterWidth)
            );
            return { min: configuredMin, max: effectiveMax, split: splitWidth };
        }

        updateContextRailWidth(saveToStorage = false) {
            const limits = this.calculateEffectiveContextLimits();
            // Equal split is the default; a saved preference only applies when
            // the user deliberately resized the rail.
            const preferred = (this.savedPreferredWidth != null) ? this.savedPreferredWidth : limits.split;
            this.currentRenderedWidth = Math.max(limits.min, Math.min(preferred, limits.max));
            document.documentElement.style.setProperty('--pwanimate-context-rail-width', `${this.currentRenderedWidth}px`);
            document.body.style.setProperty('--pwanimate-context-rail-width', `${this.currentRenderedWidth}px`);
            if (this.resizerEl) {
                this.resizerEl.setAttribute('aria-valuenow', this.currentRenderedWidth.toString());
                this.resizerEl.setAttribute('aria-valuemin', limits.min.toString());
                this.resizerEl.setAttribute('aria-valuemax', limits.max.toString());
            }
            if (saveToStorage) {
                this.savedPreferredWidth = this.currentRenderedWidth;
                try {
                    localStorage.setItem('pwanimate_context_rail_width', this.savedPreferredWidth.toString());
                } catch (e) {}
            }
        }

        toggleContextRail(open) {
            const shouldOpen = (open !== undefined) ? open : !this.contextRailOpen;
            if (shouldOpen) {
                this.openContextRail();
            } else {
                this.closeContextRail();
            }
        }

        openMobileWorkspaceSheet(tabName = 'context') {
            this.switchWorkspaceTab(tabName);
            const sheetEl = document.getElementById('pwanimateResourcePreviewSheet');
            if (!sheetEl || !window.bootstrap) return;
            let offcanvas = window.bootstrap.Offcanvas.getInstance(sheetEl);
            if (!offcanvas) offcanvas = new window.bootstrap.Offcanvas(sheetEl);
            offcanvas.show();
            if (tabName === 'preview' || tabName === 'context') {
                this._expandMobileSheetForReading(sheetEl);
            }
        }

        /**
         * Expanded document-reading state for the mobile bottom sheet:
         * grow to nearly the full viewport (minus top safe area) while keeping
         * the drag handle and sheet chrome. Matches bottom-sheet-handles.js math.
         */
        _expandMobileSheetForReading(sheetEl) {
            const apply = () => {
                const viewportHeight = (window.visualViewport && window.visualViewport.height) || window.innerHeight;
                const safeTop = parseFloat(getComputedStyle(document.documentElement).getPropertyValue('--vasto-safe-area-top')) || 0;
                const maxHeight = Math.max(280, viewportHeight - Math.max(16, safeTop + 8));
                sheetEl.style.setProperty('height', `${maxHeight}px`, 'important');
                sheetEl.style.setProperty('max-height', `${maxHeight}px`, 'important');
                sheetEl.dataset.bottomSheetSize = 'expanded';
            };
            if (sheetEl.classList.contains('show')) {
                apply();
            } else {
                sheetEl.addEventListener('shown.bs.offcanvas', apply, { once: true });
            }
        }

        openContextRail() {
            if (window.innerWidth < 1200) {
                this.openMobileWorkspaceSheet('context');
                return;
            }
            this.contextRailOpen = true;
            try {
                localStorage.setItem('pwanimateContextRailOpen', 'true');
            } catch (e) {}
            document.documentElement.classList.add('pwanimate-context-rail-open');
            document.body.classList.add('pwanimate-context-rail-open');
            this.updateContextRailWidth(false);
            this.syncContextRailUI();

            if (this.activeWorkspaceTab === 'context') {
                this.syncContextView();
            } else if (this.activeWorkspaceTab === 'offline') {
                this.renderOfflineTab();
            } else {
                // Restore preview if active details exist (State D -> State C), otherwise show empty state (State B)
                const emptyState = document.getElementById('desktopPreviewEmpty');
                const activeState = document.getElementById('desktopPreviewActive');
                if (emptyState && activeState) {
                    if (this.previewResourceState || this.activeResourceDetails) {
                        emptyState.classList.add('d-none');
                        activeState.classList.remove('d-none');
                        activeState.classList.add('d-flex');
                    } else {
                        activeState.classList.remove('d-flex');
                        activeState.classList.add('d-none');
                        emptyState.classList.remove('d-none');
                    }
                }
            }
        }

        closeContextRail() {
            this.contextRailOpen = false;
            try {
                localStorage.setItem('pwanimateContextRailOpen', 'false');
            } catch (e) {}
            document.documentElement.classList.remove('pwanimate-context-rail-open');
            document.body.classList.remove('pwanimate-context-rail-open');
            document.documentElement.style.setProperty('--pwanimate-context-rail-width', '0px');
            document.body.style.setProperty('--pwanimate-context-rail-width', '0px');
            this.syncContextRailUI();

            // Pause any playing media without clearing active preview or context state (preserves State D)
            const activeState = document.getElementById('desktopPreviewActive');
            if (activeState) {
                const video = activeState.querySelector('video');
                if (video && !video.paused) {
                    try { video.pause(); } catch (err) {}
                }
            }
            const contextCard = document.getElementById('desktopContextSurfaceCard');
            if (contextCard) {
                const video = contextCard.querySelector('video');
                if (video && !video.paused) {
                    try { video.pause(); } catch (err) {}
                }
            }
        }

        syncContextRailUI() {
            const toggleBtn = document.getElementById('pwanimateContextRailToggleBtn');
            if (toggleBtn) {
                toggleBtn.setAttribute('aria-expanded', this.contextRailOpen ? 'true' : 'false');
                toggleBtn.setAttribute('title', this.contextRailOpen ? 'Close Context Rail' : 'Open Context Rail');
                const icon = toggleBtn.querySelector('i, svg');
                if (icon) {
                    sparkSetIcon(icon, this.contextRailOpen ? 'bi-layout-sidebar-inset-reverse' : 'bi-layout-sidebar-reverse');
                }
            }
        }

        initRailResizer() {
            if (typeof this._resizerCleanup === 'function') {
                try { this._resizerCleanup(); } catch (_) {}
                this._resizerCleanup = null;
            }

            this.resizerEl = document.getElementById('pwanimateRailResizer');
            if (!this.resizerEl) return;

            let isDragging = false;
            let startX = 0;
            let startWidth = 0;

            const onPointerDown = (e) => {
                if (e.button !== 0 && e.pointerType === 'mouse') return;
                isDragging = true;
                this.isResizing = true;
                document.body.classList.add('pwanimate-resizing');
                startX = e.clientX;
                startWidth = this.currentRenderedWidth || this.savedPreferredWidth || this.calculateEffectiveContextLimits().split;

                const resizerNode = document.getElementById('pwanimateRailResizer') || this.resizerEl;
                if (resizerNode && typeof resizerNode.setPointerCapture === 'function') {
                    try {
                        resizerNode.setPointerCapture(e.pointerId);
                    } catch (err) {}
                }

                e.preventDefault();
            };

            const onPointerMove = (e) => {
                if (!isDragging) return;
                const deltaX = startX - e.clientX; // dragging left widens the right rail
                const targetWidth = startWidth + deltaX;
                const limits = this.calculateEffectiveContextLimits();
                const clampedWidth = Math.max(limits.min, Math.min(targetWidth, limits.max));

                if (this.resizeFrame) cancelAnimationFrame(this.resizeFrame);
                this.resizeFrame = requestAnimationFrame(() => {
                    this.currentRenderedWidth = clampedWidth;
                    document.documentElement.style.setProperty('--pwanimate-context-rail-width', `${clampedWidth}px`);
                    document.body.style.setProperty('--pwanimate-context-rail-width', `${clampedWidth}px`);
                    const resizerNode = document.getElementById('pwanimateRailResizer');
                    if (resizerNode) {
                        resizerNode.setAttribute('aria-valuenow', clampedWidth.toString());
                    }
                });
            };

            const onPointerUp = (e) => {
                if (!isDragging) return;
                isDragging = false;
                this.isResizing = false;
                document.body.classList.remove('pwanimate-resizing');

                const resizerNode = document.getElementById('pwanimateRailResizer') || this.resizerEl;
                if (resizerNode && typeof resizerNode.releasePointerCapture === 'function') {
                    try {
                        resizerNode.releasePointerCapture(e.pointerId);
                    } catch (err) {}
                }

                // Commit user-preferred width to savedPreferredWidth and localStorage
                this.savedPreferredWidth = this.currentRenderedWidth;
                try {
                    localStorage.setItem('pwanimate_context_rail_width', this.savedPreferredWidth.toString());
                } catch (err) {}

                if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                    this.previewDocViewer.handleResize();
                }
                if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                    this.contextDocViewer.handleResize();
                }
            };

            const delegatedPointerDown = (e) => {
                const targetResizer = e.target.closest('#pwanimateRailResizer');
                if (targetResizer) {
                    onPointerDown(e);
                }
            };

            document.addEventListener('pointerdown', delegatedPointerDown);
            window.addEventListener('pointermove', onPointerMove);
            window.addEventListener('pointerup', onPointerUp);
            window.addEventListener('pointercancel', onPointerUp);

            // Keyboard accessibility for resizer (ArrowLeft, ArrowRight, Home, End)
            const onKeyDown = (e) => {
                const targetResizer = e.target.closest('#pwanimateRailResizer');
                if (!targetResizer || !this.contextRailOpen) return;
                const limits = this.calculateEffectiveContextLimits();
                let step = 16;
                let updated = false;

                if (e.key === 'ArrowLeft') {
                    this.currentRenderedWidth = Math.min(limits.max, this.currentRenderedWidth + step);
                    updated = true;
                } else if (e.key === 'ArrowRight') {
                    this.currentRenderedWidth = Math.max(limits.min, this.currentRenderedWidth - step);
                    updated = true;
                } else if (e.key === 'Home') {
                    this.currentRenderedWidth = limits.min;
                    updated = true;
                } else if (e.key === 'End') {
                    this.currentRenderedWidth = limits.max;
                    updated = true;
                }

                if (updated) {
                    e.preventDefault();
                    this.savedPreferredWidth = this.currentRenderedWidth;
                    document.documentElement.style.setProperty('--pwanimate-context-rail-width', `${this.currentRenderedWidth}px`);
                    document.body.style.setProperty('--pwanimate-context-rail-width', `${this.currentRenderedWidth}px`);
                    targetResizer.setAttribute('aria-valuenow', this.currentRenderedWidth.toString());
                    try {
                        localStorage.setItem('pwanimate_context_rail_width', this.savedPreferredWidth.toString());
                    } catch (err) {}

                    if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                        this.previewDocViewer.handleResize();
                    }
                    if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                        this.contextDocViewer.handleResize();
                    }
                }
            };
            document.addEventListener('keydown', onKeyDown);

            // Double-click resets to the equal split and clears any saved preference.
            const onDoubleClick = (e) => {
                const targetResizer = e.target.closest('#pwanimateRailResizer');
                if (!targetResizer) return;
                this.savedPreferredWidth = null;
                try {
                    localStorage.removeItem('pwanimate_context_rail_width');
                } catch (err) {}
                this.updateContextRailWidth(false);
                if (this.previewDocViewer && typeof this.previewDocViewer.handleResize === 'function') {
                    this.previewDocViewer.handleResize();
                }
                if (this.contextDocViewer && typeof this.contextDocViewer.handleResize === 'function') {
                    this.contextDocViewer.handleResize();
                }
            };
            document.addEventListener('dblclick', onDoubleClick);

            this._resizerCleanup = () => {
                document.removeEventListener('pointerdown', delegatedPointerDown);
                document.removeEventListener('dblclick', onDoubleClick);
                window.removeEventListener('pointermove', onPointerMove);
                window.removeEventListener('pointerup', onPointerUp);
                window.removeEventListener('pointercancel', onPointerUp);
                document.removeEventListener('keydown', onKeyDown);
                document.body.classList.remove('pwanimate-resizing');
            };
        }

        resetMediaElements(container) {
            if (!container) return;
            const video = container.querySelector('video');
            if (video) {
                if (video._hlsInstance) {
                    try { video._hlsInstance.destroy(); } catch (e) {}
                    delete video._hlsInstance;
                }
                delete video.dataset.hlsReady;
                video.removeAttribute('data-hls-url');
                video.removeAttribute('data-video-url');
                video.poster = '';
                video.pause();
                video.src = '';
            }
            const img = container.querySelector('.pwanimate-preview-img');
            if (img) { img.src = ''; }
            const docThumb = container.querySelector('.pwanimate-preview-doc-thumbnail-wrapper img');
            if (docThumb) { docThumb.src = ''; }
            const docViewer = container.querySelector('.pwanimate-surface-viewer-host');
            if (docViewer && docViewer._resizeObs) {
                try { docViewer._resizeObs.disconnect(); } catch (e) {}
                delete docViewer._resizeObs;
            }
        }

        scrollToBottom(smooth = true) {
            if (!this.transcript) return;
            this.transcript.scrollTo({
                top: this.transcript.scrollHeight,
                behavior: smooth ? 'smooth' : 'auto'
            });
        }

        scrollToMessageStart(message, smooth = true) {
            if (!this.transcript || !message) return;
            const transcriptTop = this.transcript.getBoundingClientRect().top;
            const messageTop = message.getBoundingClientRect().top;
            const targetTop = this.transcript.scrollTop + messageTop - transcriptTop - 12;
            this.transcript.scrollTo({
                top: Math.max(0, targetTop),
                behavior: smooth ? 'smooth' : 'auto'
            });
        }

        appendTranscriptRow(row) {
            if (!this.transcript || !row) return;
            const disclaimer = this.transcript.querySelector('.pwanimate-chat-disclaimer');
            if (disclaimer) this.transcript.insertBefore(row, disclaimer);
            else this.transcript.appendChild(row);
            sparkRenderIcons(row);
        }

        setGenerating(loading) {
            this.isGenerating = loading;
            if (loading) {
                this.stopSpeaking();
                this.waitingForInput = false;
            }
            if (this.input) this.input.disabled = loading;
            if (this.voiceBtn) this.voiceBtn.disabled = loading || this.voiceStarting || !this.voiceRecorderSupported;
            if (loading && (this.voiceListening || this.voiceStarting || this.voiceStopping)) this.cancelVoiceCapture();
            this.updateSendButtonState();
        }

        // The agent run stays alive while it waits on the student; keep the
        // send button pulsing so the engine visibly remains on standby.
        // `interaction` is the serialized pending interaction (prompt,
        // options, input_type, allow_custom_answer) when one exists.
        setWaitingForInput(waiting, interaction = null, runId = null) {
            this.waitingForInput = Boolean(waiting);
            if (interaction && interaction.interaction_id) {
                this.pendingInteraction = {
                    run_id: interaction.run_id || runId || (this.pendingInteraction && this.pendingInteraction.run_id),
                    interaction_id: interaction.interaction_id,
                    input_type: interaction.input_type || 'text',
                    kind: interaction.kind || 'clarification',
                    prompt: interaction.prompt || '',
                    options: Array.isArray(interaction.options) ? interaction.options : [],
                    questions: Array.isArray(interaction.questions) ? interaction.questions : [],
                    allow_custom_answer: interaction.allow_custom_answer !== false,
                };
            }
            this._syncInteractionBlock();
            this.updateSendButtonState();
        }

        // While a structured interaction is pending, the floating block above
        // the composer IS the answer path (same authoritative backend
        // resolution as before). A "new task" escape keeps unrelated requests
        // possible; the run stays paused server-side either way.
        _interactionSignature() {
            const pi = this.pendingInteraction;
            return (this.waitingForInput && pi) ? `${pi.run_id}:${pi.interaction_id}` : '';
        }

        _syncInteractionBlock() {
            if (!this.composerForm) return;
            const sig = this._interactionSignature();
            if (this._interactionBlockEl && this._interactionBlockSig === sig) return;
            if (this._interactionBlockEl) {
                this._interactionBlockEl.remove();
                this._interactionBlockEl = null;
                this._interactionBlockSig = '';
            }
            if (!sig) return;
            const el = this._buildInteractionBlock(this.pendingInteraction);
            this.composerForm.parentNode.insertBefore(el, this.composerForm);
            this._interactionBlockEl = el;
            this._interactionBlockSig = sig;
            if (window.sparkRenderIcons) window.sparkRenderIcons(el);
        }

        // Renders the answer controls for ONE question spec and returns
        // {read, controls}: read() yields the normalized answer payload
        // ({text}|{selected}|{confirmed}) or undefined when unanswered.
        _questionControls(q, initial, onChange) {
            const frag = document.createDocumentFragment();
            const controls = [];
            let current = initial || null;
            const itype = q.input_type || 'text';
            const options = Array.isArray(q.options) ? q.options.filter(o => o && (o.id || o.label)) : [];
            const emit = (value) => { current = value; if (onChange) onChange(value); };

            if (itype === 'confirm') {
                const yes = document.createElement('button');
                yes.type = 'button'; yes.className = 'btn btn-sm btn-primary spark-interaction-submit';
                yes.textContent = 'Confirm';
                const no = document.createElement('button');
                no.type = 'button'; no.className = 'btn btn-sm btn-outline-secondary spark-interaction-submit';
                no.textContent = 'Decline';
                if (current && current.confirmed === true) yes.classList.add('is-selected');
                if (current && current.confirmed === false) no.classList.add('is-selected');
                yes.addEventListener('click', () => emit({confirmed: true}));
                no.addEventListener('click', () => emit({confirmed: false}));
                frag.append(yes, no);
                controls.push(yes, no);
                return {read: () => current, controls, el: frag};
            }

            if ((itype === 'single_choice' || itype === 'multi_choice') && options.length) {
                const wrap = document.createElement('div');
                wrap.className = 'spark-interaction-choices';
                const selected = new Set((current && current.selected) || []);
                let customInput = null;
                const syncCustom = () => {
                    if (!customInput) return;
                    const text = (customInput.value || '').trim();
                    if (text) {
                        emit(itype === 'single_choice' ? {text} : {selected: [...selected], text});
                    } else if (itype === 'single_choice' && !selected.size) {
                        emit(null);
                    } else if (itype === 'multi_choice') {
                        emit(selected.size ? {selected: [...selected]} : null);
                    }
                };
                for (const opt of options) {
                    const b = document.createElement('button');
                    b.type = 'button';
                    b.className = 'spark-interaction-option' + (selected.has(opt.id) ? ' is-selected' : '');
                    b.textContent = opt.label || opt.id;
                    b.addEventListener('click', () => {
                        if (itype === 'single_choice') {
                            wrap.querySelectorAll('.spark-interaction-option').forEach(x => x.classList.remove('is-selected'));
                            selected.clear();
                            if (selected.has(opt.id)) { selected.delete(opt.id); b.classList.remove('is-selected'); }
                            else { selected.add(opt.id); b.classList.add('is-selected'); }
                            if (customInput) { customInput.value = ''; customInput.hidden = true; }
                            emit(selected.size ? {selected: [...selected]} : null);
                        } else {
                            if (selected.has(opt.id)) { selected.delete(opt.id); b.classList.remove('is-selected'); }
                            else { selected.add(opt.id); b.classList.add('is-selected'); }
                            syncCustom();
                        }
                    });
                    controls.push(b);
                    wrap.appendChild(b);
                }
                frag.appendChild(wrap);
                if (q.allow_custom_answer !== false && itype === 'multi_choice') {
                    // Multi-select keeps a visible free-text field alongside
                    // the choices, matching the original single-question UI.
                    const holder = document.createElement('div');
                    holder.className = 'spark-interaction-custom';
                    customInput = document.createElement('input');
                    customInput.className = 'spark-interaction-input';
                    customInput.type = 'text';
                    customInput.placeholder = 'Your answer';
                    customInput.maxLength = 1000;
                    if (current && current.text) customInput.value = current.text;
                    customInput.addEventListener('input', syncCustom);
                    holder.appendChild(customInput);
                    frag.appendChild(holder);
                } else if (q.allow_custom_answer !== false) {
                    const other = document.createElement('button');
                    other.type = 'button';
                    other.className = 'spark-interaction-option spark-interaction-other';
                    other.textContent = 'Other — enter my own answer';
                    other.addEventListener('click', () => {
                        other.hidden = true;
                        const holder = document.createElement('div');
                        holder.className = 'spark-interaction-custom';
                        const input = document.createElement('input');
                        input.className = 'spark-interaction-input';
                        input.type = 'text';
                        input.placeholder = 'Your answer';
                        input.maxLength = 1000;
                        if (current && current.text && !current.selected) input.value = current.text;
                        input.addEventListener('input', syncCustom);
                        holder.appendChild(input);
                        frag.appendChild(holder);
                        customInput = input;
                        requestAnimationFrame(() => input.focus());
                    });
                    controls.push(other);
                    frag.appendChild(other);
                }
                return {read: () => current, controls, el: frag};
            }

            // Free text (and choice questions that lost their options).
            const input = document.createElement(itype === 'text' ? 'textarea' : 'input');
            input.className = 'spark-interaction-input';
            input.placeholder = 'Your answer';
            if (input.tagName === 'TEXTAREA') { input.rows = 2; input.maxLength = 2000; }
            else { input.type = 'text'; input.maxLength = 1000; }
            if (current && current.text) input.value = current.text;
            input.addEventListener('input', () => {
                const text = (input.value || '').trim();
                emit(text ? {text} : null);
            });
            frag.appendChild(input);
            controls.push(input);
            return {read: () => current, controls, el: frag};
        }

        _buildInteractionBlock(pi) {
            const el = document.createElement('div');
            el.className = 'spark-interaction-block';
            el.dataset.runId = pi.run_id;
            el.dataset.interactionId = pi.interaction_id;

            const head = document.createElement('div');
            head.className = 'spark-interaction-head';
            const icon = document.createElement('i');
            icon.setAttribute('data-lucide', 'message-circle-question');
            const kind = document.createElement('span');
            kind.className = 'spark-interaction-kind';
            kind.textContent = pi.kind === 'approval' ? 'Approval needed' : 'Spark asks';
            head.append(icon, kind);
            el.appendChild(head);

            const progress = document.createElement('p');
            progress.className = 'spark-interaction-progress';
            el.appendChild(progress);

            const prompt = document.createElement('p');
            prompt.className = 'spark-interaction-prompt';
            el.appendChild(prompt);

            const status = document.createElement('p');
            status.className = 'spark-interaction-status';
            status.setAttribute('role', 'status');
            el.appendChild(status);

            const body = document.createElement('div');
            body.className = 'spark-interaction-body';
            el.appendChild(body);

            const isGroup = pi.input_type === 'group';
            const questions = isGroup
                ? (Array.isArray(pi.questions) && pi.questions.length ? pi.questions
                    : (Array.isArray(pi.options) ? pi.options : []))
                : [{id: 'q1', prompt: pi.prompt || 'Spark has a question.',
                    input_type: pi.input_type || 'text', options: pi.options || [],
                    required: pi.required !== false,
                    allow_custom_answer: pi.allow_custom_answer !== false}];

            // Editing state: independent answers per stable question id,
            // current index, and submission lifecycle. Drafts persist in
            // localStorage so a refresh restores progress and answers.
            const state = {index: 0, answers: {}, submitting: false};
            const draftKey = 'spark-interaction-draft:' + pi.interaction_id;
            try {
                const draft = JSON.parse(localStorage.getItem(draftKey) || 'null');
                if (draft && typeof draft === 'object') {
                    if (Number.isInteger(draft.index)) state.index = Math.min(Math.max(draft.index, 0), questions.length - 1);
                    if (draft.answers && typeof draft.answers === 'object') state.answers = draft.answers;
                }
            } catch (_) {}
            const saveDraft = () => {
                try { localStorage.setItem(draftKey, JSON.stringify({index: state.index, answers: state.answers})); } catch (_) {}
            };
            const clearDraft = () => { try { localStorage.removeItem(draftKey); } catch (_) {} };

            let view = null;      // {read, controls} for the visible question
            const allControls = [];

            const setBusy = (busy) => {
                state.submitting = busy;
                document.querySelectorAll('.spark-interaction-block button, .spark-interaction-block input, .spark-interaction-block textarea')
                    .forEach(c => { c.disabled = busy; });
                status.textContent = busy ? 'Sending…' : '';
                status.classList.remove('is-error');
            };
            const fail = (message) => {
                status.dataset.error = '1';
                status.textContent = message || 'Could not send your answer. Try again.';
                status.classList.add('is-error');
                setBusy(false);
            };
            const submit = (payload) => {
                if (this.isGenerating || state.submitting) return;
                setBusy(true);
                this.resumeInteraction(pi.run_id, pi.interaction_id, payload, null)
                    .then(() => { state.submitting = false; clearDraft(); })
                    .catch(err => {
                        state.submitting = false;
                        fail(err && err.message ? err.message : 'Could not send your answer. Try again.');
                    });
            };

            const nav = document.createElement('div');
            nav.className = 'spark-interaction-nav';
            const backBtn = document.createElement('button');
            backBtn.type = 'button'; backBtn.className = 'spark-interaction-nav-btn';
            backBtn.textContent = 'Back';
            const nextBtn = document.createElement('button');
            nextBtn.type = 'button'; nextBtn.className = 'btn btn-sm btn-primary spark-interaction-submit';
            const sendBtn = document.createElement('button');
            sendBtn.type = 'button'; sendBtn.className = 'btn btn-sm btn-primary spark-interaction-submit';
            sendBtn.textContent = 'Send';

            const collectCurrent = () => {
                if (view) {
                    const ans = view.read();
                    const qid = questions[state.index] && questions[state.index].id;
                    if (qid) {
                        if (ans) state.answers[qid] = ans; else delete state.answers[qid];
                    }
                }
            };

            const render = () => {
                collectCurrent();
                saveDraft();
                const q = questions[state.index];
                body.replaceChildren();
                allControls.length = 0;
                progress.textContent = isGroup ? `Question ${state.index + 1} of ${questions.length}` : '';
                progress.hidden = !isGroup;
                prompt.textContent = q.prompt || 'Spark has a question.';
                view = this._questionControls(q, state.answers[q.id], (value) => {
                    if (value) state.answers[q.id] = value; else delete state.answers[q.id];
                    saveDraft();
                });
                body.appendChild(view.el);
                allControls.push(...view.controls);
                nav.replaceChildren();
                if (isGroup) {
                    backBtn.disabled = state.index === 0 || state.submitting;
                    backBtn.onclick = () => { if (state.index > 0) { state.index -= 1; render(); } };
                    if (state.index < questions.length - 1) {
                        nextBtn.textContent = 'Next';
                        nextBtn.disabled = state.submitting;
                        nextBtn.onclick = () => { state.index += 1; render(); };
                        nav.append(backBtn, nextBtn);
                    } else {
                        sendBtn.disabled = state.submitting;
                        sendBtn.onclick = () => {
                            collectCurrent();
                            const missing = questions.findIndex(qq =>
                                qq.required !== false && !state.answers[qq.id]);
                            if (missing >= 0) {
                                state.index = missing;
                                render();
                                status.dataset.error = '1';
                                status.textContent = 'Please answer every required question.';
                                status.classList.add('is-error');
                                return;
                            }
                            const answers = {};
                            for (const qq of questions) {
                                if (state.answers[qq.id]) answers[qq.id] = state.answers[qq.id];
                            }
                            submit({answers});
                        };
                        nav.append(backBtn, sendBtn);
                    }
                } else {
                    // Mode A: explicit Send — selecting an option never
                    // resolves the interaction by itself.
                    sendBtn.textContent = 'Send';
                    sendBtn.disabled = state.submitting;
                    sendBtn.onclick = () => {
                        collectCurrent();
                        const qid = questions[0].id;
                        const ans = state.answers[qid];
                        if (!ans) { status.textContent = 'Please answer the question.'; status.classList.add('is-error'); return; }
                        if (ans.confirmed !== undefined) submit({confirmed: ans.confirmed});
                        else if (ans.selected) submit({selected: ans.selected, ...(ans.text ? {text: ans.text} : {})});
                        else submit({text: ans.text});
                    };
                    nav.append(sendBtn);
                }
                el.querySelector('.spark-interaction-nav-slot')?.remove();
                const slot = document.createElement('div');
                slot.className = 'spark-interaction-nav-slot';
                slot.appendChild(nav);
                body.after(slot);
                if (window.sparkRenderIcons) window.sparkRenderIcons(body);
            };

            const foot = document.createElement('div');
            foot.className = 'spark-interaction-foot';
            const fresh = document.createElement('button');
            fresh.type = 'button';
            fresh.className = 'spark-interaction-link';
            fresh.textContent = 'New task';
            fresh.addEventListener('click', () => {
                // The run stays paused server-side; its trail still offers the
                // answer path if the student returns to it.
                this.pendingInteraction = null;
                this.setWaitingForInput(false);
                if (this.input) this.input.focus();
            });
            const cancel = document.createElement('button');
            cancel.type = 'button';
            cancel.className = 'spark-interaction-link';
            cancel.textContent = 'Cancel task';
            cancel.addEventListener('click', () => {
                const runId = pi.run_id;
                this.pendingInteraction = null;
                this.setWaitingForInput(false);
                if (runId) this.cancelRun(runId);
            });
            foot.append(fresh, cancel);
            el.appendChild(foot);
            render();
            return el;
        }

        // Mark the compact trail record for this run as answered once the
        // backend accepts the interaction.
        _markClarificationResolved(runId) {
            if (!runId || !this.transcript) return;
            this.transcript.querySelectorAll(`.spark-clarification[data-run-id="${CSS.escape(String(runId))}"]`).forEach(box => {
                box.classList.add('spark-clarification-resolved');
                const note = box.querySelector('.spark-clarification-note');
                if (note) note.textContent = 'Answered.';
            });
        }

        // Record which run/interaction the latest activity implies so the
        // composer, the cancel control, and reload-recovery all agree.
        _trackRunState(data, events) {
            const list = Array.isArray(events) ? events : (data && data.activity) || [];
            let latestWait = null;
            for (const e of list) {
                if (e && e.run_id && (e.kind === 'clarification' || e.kind === 'approval')
                        && e.status === 'waiting') {
                    latestWait = e;
                }
            }
            const metaRun = data && data.metadata && data.metadata.run;
            const interaction = (data && data.interaction)
                || (metaRun && metaRun.interaction) || null;
            if (interaction && interaction.interaction_id) {
                this.pendingInteraction = {
                    run_id: interaction.run_id || (metaRun && metaRun.id) || data.run_id,
                    interaction_id: interaction.interaction_id,
                    input_type: interaction.input_type || 'text',
                    kind: interaction.kind || 'clarification',
                    prompt: interaction.prompt || '',
                    options: Array.isArray(interaction.options) ? interaction.options : [],
                    questions: Array.isArray(interaction.questions) ? interaction.questions : [],
                    allow_custom_answer: interaction.allow_custom_answer !== false,
                };
            } else if (latestWait && latestWait.kind === 'clarification') {
                this.pendingInteraction = {
                    run_id: latestWait.run_id,
                    interaction_id: latestWait.interaction_id || null,
                    input_type: latestWait.input_type || 'text',
                    kind: 'clarification',
                    prompt: latestWait.text || '',
                    options: Array.isArray(latestWait.options) ? latestWait.options : [],
                    questions: Array.isArray(latestWait.questions) ? latestWait.questions : [],
                    allow_custom_answer: latestWait.allow_custom_answer !== false,
                };
            } else {
                this.pendingInteraction = null;
            }
            this.activeRunId = (latestWait && latestWait.run_id)
                || (interaction && interaction.run_id)
                || (data && data.run_id) || null;
            this._syncInteractionBlock();
        }

        _clearRunState() {
            const resolvedRunId = this.pendingInteraction && this.pendingInteraction.run_id;
            if (resolvedRunId) this._markClarificationResolved(resolvedRunId);
            this.pendingInteraction = null;
            this.activeRunId = null;
            this._syncInteractionBlock();
        }

        async cancelRun(runId) {
            if (!runId) return;
            try {
                await fetch(`/api/pwanimate/agent-runs/${encodeURIComponent(runId)}/cancel/`, {
                    method: 'POST', credentials: 'same-origin',
                    headers: {'Content-Type': 'application/json',
                              'Accept': 'application/json',
                              'X-CSRFToken': getCsrfToken()},
                    body: '{}',
                });
            } catch (error) {
                console.warn('[Spark] Could not cancel run', runId, error);
            }
        }

        updateSendButtonState() {
            if (!this.sendBtn) return;
            if (this.voiceListening || this.voiceStarting || this.voiceStopping) {
                this.sendBtn.disabled = true;
                this.sendBtn.classList.remove('is-active');
                this.sendBtn.setAttribute('aria-label', 'Stop voice input before sending');
                this.sendBtn.setAttribute('title', 'Stop voice input before sending');
                return;
            }
            if (this.isGenerating || this.waitingForInput) {
                this.sendBtn.disabled = false;
                this.sendBtn.classList.add('is-generating');
                this.sendBtn.classList.remove('is-active');
                if (this.isGenerating) {
                    this.sendBtn.setAttribute('aria-label', 'Stop generating');
                    this.sendBtn.setAttribute('title', 'Stop generating (Esc)');
                } else {
                    this.sendBtn.setAttribute('aria-label', 'Spark is waiting for your input');
                    this.sendBtn.setAttribute('title', 'Spark is paused until you respond');
                }
                return;
            }

            this.sendBtn.classList.remove('is-generating');
            const hasText = Boolean(this.input && this.input.value.trim().length > 0);
            const hasAttachments = this.stagedAttachments && this.stagedAttachments.length > 0;
            
            // Check if any attachment is still uploading
            const hasUploadingAttachments = this._composerAttachmentsEl && 
                this._composerAttachmentsEl.querySelector('.pwanimate-staged-chip.is-uploading');
            const pendingDocument = (this.stagedAttachments || []).some(attachment =>
                attachment.attachment_type === 'document' &&
                ['pending', 'processing'].includes(attachment.processing_status)
            );
            const failedDocument = (this.stagedAttachments || []).some(attachment =>
                attachment.attachment_type === 'document' && attachment.processing_status === 'failed'
            );

            if (hasText || hasAttachments) {
                // Disable send button if any attachment is still uploading
                if (hasUploadingAttachments) {
                    this.sendBtn.disabled = true;
                    this.sendBtn.classList.remove('is-active');
                    this.sendBtn.setAttribute('aria-label', 'Wait for attachments to finish uploading');
                    this.sendBtn.setAttribute('title', 'Wait for attachments to finish uploading');
                } else if (pendingDocument) {
                    this.sendBtn.disabled = true;
                    this.sendBtn.classList.remove('is-active');
                    this.sendBtn.setAttribute('aria-label', 'Wait for document processing to finish');
                    this.sendBtn.setAttribute('title', 'Spark is reading the attached document');
                } else if (failedDocument) {
                    this.sendBtn.disabled = true;
                    this.sendBtn.classList.remove('is-active');
                    this.sendBtn.setAttribute('aria-label', 'Remove the document that could not be processed');
                    this.sendBtn.setAttribute('title', 'This document could not be processed. Remove it to continue.');
                } else {
                    this.sendBtn.disabled = false;
                    this.sendBtn.classList.add('is-active');
                    this.sendBtn.setAttribute('aria-label', 'Send message');
                    this.sendBtn.setAttribute('title', 'Send message (Enter)');
                }
            } else {
                this.sendBtn.disabled = true;
                this.sendBtn.classList.remove('is-active');
                this.sendBtn.setAttribute('aria-label', 'Send message');
                this.sendBtn.setAttribute('title', 'Type a message to send');
            }
        }

        // -----------------------------------------------------------------------
        // Attachment Staging & Upload
        // -----------------------------------------------------------------------

        initAttachmentHandlers() {
            this.stagedAttachments = [];
            this._composerAttachmentsEl = this.composerForm && this.composerForm.querySelector('#pwanimate-composer-attachments');

            const photoInput = this.composerForm && this.composerForm.querySelector('#pwanimatePhotoInput');
            const docInput   = this.composerForm && this.composerForm.querySelector('#pwanimateDocInput');
            const fileInput  = this.composerForm && this.composerForm.querySelector('#pwanimateFileInput');
            const cameraInput = this.composerForm && this.composerForm.querySelector('#pwanimateCameraInput');

            const modal = document.getElementById('pwanimateAttachmentModal');

            const wireOption = (optionId, input, useCapacitorCamera) => {
                const opt = document.getElementById(optionId);
                if (!opt) return;
                opt.addEventListener('click', async () => {
                    if (modal) modal.classList.remove('show');

                    // Capacitor camera path (native mobile)
                    if (useCapacitorCamera &&
                        typeof window.Capacitor !== 'undefined' &&
                        window.Capacitor.Plugins &&
                        window.Capacitor.Plugins.Camera) {
                        try {
                            const { Camera } = window.Capacitor.Plugins;
                            const photo = await Camera.getPhoto({
                                quality: 85,
                                resultType: 'base64',
                                source: 'CAMERA',
                                saveToGallery: false,
                            });
                            if (photo && photo.base64String) {
                                const mime = `image/${photo.format || 'jpeg'}`;
                                const byteStr = atob(photo.base64String);
                                const ab = new ArrayBuffer(byteStr.length);
                                const ia = new Uint8Array(ab);
                                for (let i = 0; i < byteStr.length; i++) ia[i] = byteStr.charCodeAt(i);
                                const blob = new Blob([ab], { type: mime });
                                const fileName = `camera_${Date.now()}.${photo.format || 'jpg'}`;
                                const file = new File([blob], fileName, { type: mime });
                                this._stageFiles([file]);
                            }
                        } catch (err) {
                            if (err && err.message && err.message.toLowerCase().includes('cancel')) return;
                            console.warn('[Spark] Capacitor camera error, falling back to file input:', err);
                            if (cameraInput) cameraInput.click();
                        }
                        return;
                    }

                    if (input) input.click();
                });
            };

            wireOption('pwanimateAttachCamera', cameraInput, true);
            wireOption('pwanimateAttachPhotos', photoInput, false);
            wireOption('pwanimateAttachDocs', docInput, false);
            wireOption('pwanimateAttachFiles', fileInput, false);

            const bindInput = (input) => {
                if (!input) return;
                input.addEventListener('change', (e) => {
                    const files = Array.from(e.target.files || []);
                    if (files.length > 0) this._stageFiles(files);
                    input.value = '';
                });
            };
            bindInput(photoInput);
            bindInput(docInput);
            bindInput(fileInput);
            bindInput(cameraInput);
        }

        _stageFiles(files) {
            files.forEach(file => {
                const chipId = `staged-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
                const chip = this._buildChip(chipId, file.name, file.type);
                if (this._composerAttachmentsEl) {
                    this._composerAttachmentsEl.classList.remove('d-none');
                    this._composerAttachmentsEl.appendChild(chip);
                }
                this._uploadFile(file, chipId);
            });
            this.updateSendButtonState();
        }

        _buildChip(chipId, fileName, mimeType) {
            const isImage = mimeType && mimeType.startsWith('image/');
            const chip = document.createElement('div');
            chip.className = 'pwanimate-staged-chip is-uploading';
            chip.id = chipId;
            chip.dataset.attachmentId = '';

            // Create inner media container
            const mediaContainer = document.createElement('div');
            mediaContainer.className = 'pwanimate-staged-media';

            if (isImage) {
                const iconEl = document.createElement('i');
                iconEl.setAttribute('data-lucide', 'image');
                iconEl.className = 'pwanimate-staged-icon';
                mediaContainer.appendChild(iconEl);
            } else {
                const iconEl = document.createElement('i');
                iconEl.setAttribute('data-lucide', 'file-text');
                iconEl.className = 'pwanimate-staged-icon';
                mediaContainer.appendChild(iconEl);
            }
            sparkRenderIcons(mediaContainer);

            // Add spinner inside the media container
            const spinner = document.createElement('span');
            spinner.className = 'pwanimate-staged-spinner spinner-border spinner-border-sm text-primary';
            spinner.style.width = '16px';
            spinner.style.height = '16px';
            spinner.setAttribute('role', 'status');
            mediaContainer.appendChild(spinner);

            chip.appendChild(mediaContainer);

            return chip;
        }

        async _uploadFile(file, chipId) {
            const csrfToken = getCsrfToken();
            const formData = new FormData();
            formData.append('file', file);
            if (this.conversationId) {
                formData.append('conversation_id', this.conversationId);
            }

            let chip = document.getElementById(chipId);

            try {
                const response = await fetch('/api/pwanimate/attachments/upload/', {
                    method: 'POST',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'Accept': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: formData,
                });

                const data = await response.json();
                chip = document.getElementById(chipId);

                if (!response.ok || !data.id) {
                    const errMsg = data.error || 'Upload failed.';
                    console.warn('[Spark] Attachment upload failed:', errMsg);
                    if (chip) {
                        chip.classList.remove('is-uploading');
                        chip.classList.add('is-error');
                        const mediaContainer = chip.querySelector('.pwanimate-staged-media');
                        const spinner = chip.querySelector('.pwanimate-staged-spinner');
                        if (spinner && mediaContainer) spinner.remove();
                        // Auto-remove error chips after 3s
                        setTimeout(() => { if (chip && chip.parentNode) chip.parentNode.removeChild(chip); }, 3000);
                    }
                    return;
                }
                // Success
                const stagedAttachment = {
                    id: data.id,
                    file_name: data.file_name,
                    attachment_type: data.attachment_type,
                    processing_status: data.processing_status || 'not_required',
                    url: data.url
                };
                this.stagedAttachments.push(stagedAttachment);
                if (chip) {
                    chip.classList.remove('is-uploading');
                    chip.dataset.attachmentId = data.id;
                    if (data.attachment_type === 'document' && stagedAttachment.processing_status !== 'ready') {
                        chip.classList.add('is-processing');
                        this._setAttachmentStatusLabel(chip, 'Reading pages…');
                    }
                    
                    const mediaContainer = chip.querySelector('.pwanimate-staged-media');
                    const spinner = chip.querySelector('.pwanimate-staged-spinner');
                    if (spinner && mediaContainer) spinner.remove();

                    // If image, show a tiny thumbnail inside the media container
                    if (data.attachment_type === 'image' && data.url && mediaContainer) {
                        const thumb = document.createElement('img');
                        thumb.src = data.url;
                        thumb.className = 'pwanimate-staged-thumb';
                        thumb.alt = data.file_name;
                        const existingIcon = chip.querySelector('.pwanimate-staged-icon');
                        if (existingIcon) {
                            mediaContainer.removeChild(existingIcon);
                        }
                        mediaContainer.appendChild(thumb);
                    }

                    // Remove button
                    const removeBtn = document.createElement('button');
                    removeBtn.type = 'button';
                    removeBtn.className = 'pwanimate-staged-remove';
                    removeBtn.setAttribute('aria-label', `Remove ${data.file_name}`);
                    removeBtn.innerHTML = '<i data-lucide="x"></i>';
                    removeBtn.addEventListener('click', (e) => {
                        e.stopPropagation();
                        this.stagedAttachments = this.stagedAttachments.filter(a => a.id !== data.id);
                        if (data.attachment_type === 'document' && data.id) {
                            const matchRes = this.contextResources.find(r => r.attachmentId === data.id);
                            if (matchRes) {
                                this.removeContextResource(matchRes.key);
                            }
                        }
                        if (chip && chip.parentNode) chip.parentNode.removeChild(chip);
                        if (this._composerAttachmentsEl && !this._composerAttachmentsEl.querySelector('.pwanimate-staged-chip')) {
                            this._composerAttachmentsEl.classList.add('d-none');
                        }
                        this.updateSendButtonState();
                    });
                    chip.appendChild(removeBtn);
                }

                if (data.attachment_type === 'document' && data.url) {
                    const ext = ((data.file_name || '').split('.').pop() || 'pdf').toLowerCase();
                    const docRes = this.normalizeResource(data.url, data.file_name, {
                        sourceType: 'attachment',
                        resourceType: 'document',
                        attachmentId: data.id,
                        fileType: ext,
                        mediaUrl: data.url,
                        pageNumber: 1,
                    });
                    this.addResourceToContext(docRes);
                    if (window.innerWidth >= 1200) {
                        this.openContextRail();
                    }
                    if (chip) {
                        chip.style.cursor = 'pointer';
                        chip.title = 'Click to view in Context Rail';
                        chip.addEventListener('click', () => {
                            this.setActiveContextResource(docRes.key);
                            this.openContextRail();
                        });
                    }
                }

                if (data.attachment_type === 'document' && stagedAttachment.processing_status !== 'ready') {
                    this._watchAttachmentProcessing(stagedAttachment, chipId);
                }

                this.updateSendButtonState();

            } catch (err) {
                console.error('[Spark] Attachment upload network error:', err);
                chip = document.getElementById(chipId);
                if (chip) {
                    chip.classList.remove('is-uploading');
                    chip.classList.add('is-error');
                    const mediaContainer = chip.querySelector('.pwanimate-staged-media');
                    const spinner = chip.querySelector('.pwanimate-staged-spinner');
                    if (spinner && mediaContainer) spinner.remove();
                    setTimeout(() => { if (chip && chip.parentNode) chip.parentNode.removeChild(chip); }, 3000);
                }
            }
        }

        _setAttachmentStatusLabel(chip, label, isError = false) {
            if (!chip) return;
            let statusEl = chip.querySelector('.pwanimate-staged-status');
            if (!statusEl) {
                statusEl = document.createElement('span');
                statusEl.className = 'pwanimate-staged-status';
                chip.appendChild(statusEl);
            }
            statusEl.textContent = label;
            statusEl.classList.toggle('is-error', isError);
        }

        async _watchAttachmentProcessing(attachment, chipId) {
            for (let attempt = 0; attempt < 180; attempt += 1) {
                if (!this.stagedAttachments.some(item => item.id === attachment.id)) return;
                await new Promise(resolve => setTimeout(resolve, 1000));
                try {
                    const response = await fetch(`/api/pwanimate/attachments/${encodeURIComponent(attachment.id)}/status/`, {
                        credentials: 'same-origin',
                        headers: { 'Accept': 'application/json' }
                    });
                    if (!response.ok) continue;
                    const data = await response.json();
                    attachment.processing_status = data.processing_status;
                    const chip = document.getElementById(chipId);
                    if (data.processing_status === 'ready') {
                        if (chip) {
                            chip.classList.remove('is-processing');
                            chip.title = data.processing_error || '';
                            this._setAttachmentStatusLabel(chip, data.processing_error ? 'Ready · partial' : 'Ready');
                        }
                        this.updateSendButtonState();
                        return;
                    }
                    if (data.processing_status === 'failed') {
                        if (chip) {
                            chip.classList.remove('is-processing');
                            chip.title = data.processing_error || 'Spark could not read this document.';
                            this._setAttachmentStatusLabel(chip, 'Could not read', true);
                        }
                        attachment.processing_error = data.processing_error || '';
                        this.updateSendButtonState();
                        return;
                    }
                } catch (error) {
                    // Keep polling through brief network interruptions.
                }
            }
        }

        _clearStagedAttachments() {
            this.stagedAttachments = [];
            if (this._composerAttachmentsEl) {
                this._composerAttachmentsEl.innerHTML = '';
                this._composerAttachmentsEl.classList.add('d-none');
            }
        }

        handleAbort() {
            if (!this.isGenerating || !this.abortController) return;
            try {
                this.abortController.abort();
            } catch (e) {
                console.error('[Spark] Abort error:', e);
            }
            // Aborting the fetch only drops the stream — the durable run keeps
            // executing server-side unless we explicitly cancel it.
            if (this.activeRunId) this.cancelRun(this.activeRunId);
        }

        showSystemNotice(text) {
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant d-flex justify-content-center';
            row.innerHTML = `
                <div class="pwanimate-system-notice">
                    <i data-lucide="circle-stop"></i>
                    <span>${escapeHtml(text)}</span>
                </div>
            `;
            this.appendTranscriptRow(row);
            this.scrollToBottom();
        }

        async downloadGeneratedResource(button) {
            const row = button.closest('.pwanimate-message-row');
            const messageId = row && row.dataset.messageId;
            const fileFormat = button.dataset.format;
            if (!messageId || !['pdf', 'docx'].includes(fileFormat)) return;

            const originalMarkup = button.innerHTML;
            const menu = button.closest('.pwanimate-resource-export-menu');
            const menuButtons = menu ? Array.from(menu.querySelectorAll('.pwanimate-resource-download')) : [button];
            menuButtons.forEach((item) => { item.disabled = true; });
            button.innerHTML = '<span class="spinner-border spinner-border-sm me-2" aria-hidden="true"></span>Preparing…';

            try {
                const response = await fetch(`/api/pwanimate/messages/${encodeURIComponent(messageId)}/resources/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                        'Accept': 'application/json',
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ format: fileFormat }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) throw new Error(data.error || 'Could not create the document. Please try again.');
                button.innerHTML = '<i data-lucide="check" class="me-2"></i>Saved in My Resources';
                window.location.assign(data.download_url);
            } catch (error) {
                console.error('[Spark] Resource export failed:', error);
                button.innerHTML = '<i data-lucide="circle-alert" class="me-2"></i>Could not prepare file';
                window.setTimeout(() => { button.innerHTML = originalMarkup; }, 2600);
                return;
            } finally {
                menuButtons.forEach((item) => { item.disabled = false; });
            }
            window.setTimeout(() => { button.innerHTML = originalMarkup; }, 2200);
        }

        async handleSend(retryOptions = null) {
            if (this.voiceListening || this.voiceStopping) {
                this.setVoiceStatus('Stop voice input before sending or editing your draft.');
                return;
            }
            if (this.isGenerating) return;
            const isRetry = Boolean(retryOptions);
            const text = isRetry ? retryOptions.text : (this.input ? this.input.value.trim() : '');
            const hasStagedAttachments = isRetry
                ? (retryOptions.attachments || []).length > 0
                : (this.stagedAttachments && this.stagedAttachments.length > 0);
            if (!text && !hasStagedAttachments) {
                if (this.waitingForInput) this.showSystemNotice('Spark is waiting for your input above — answer the question or review the actions to continue.');
                return;
            }

            const isBareStudyCommand = !isRetry && /^\s*@study\s*$/i.test(text) && !hasStagedAttachments;

            // Setup AbortController for modern cancellation support
            this.abortController = new AbortController();
            let awaitingInput = false;

            // Clear input & reset height
            if (!isRetry) {
                this.input.value = '';
                this.resetTextareaHeight();
                this.updateSendButtonState();
                this.updateStudyCommandHint();
                this.setVoiceStatus('');
            }

            // Snapshot staged attachments and clear the strip before sending
            const snapshotAttachments = isRetry
                ? (retryOptions.attachments || []).slice()
                : (this.stagedAttachments || []).slice();
            if (!isRetry) this._clearStagedAttachments();

            let userRow = null;
            if (!isBareStudyCommand) {
                // Remove empty state if present
                const emptyState = this.transcript.querySelector('#pwanimate-empty-state');
                if (emptyState) {
                    emptyState.remove();
                }

                // Render optimistic user message (with attachment chips)
                userRow = isRetry
                    ? retryOptions.userRow
                    : this.appendUserMessage(text, null, snapshotAttachments);
                this.addImageAttachmentsToContext(userRow);
                this.showTypingIndicator();
                this.scrollToBottom();
            }
            this.setGenerating(true);

            const csrfToken = getCsrfToken();
            if (!this.conversationId && !this.pendingConversationId) {
                this.pendingConversationId = window.crypto && window.crypto.randomUUID
                    ? window.crypto.randomUUID()
                    : 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, c => {
                        const r = Math.random() * 16 | 0;
                        return (c === 'x' ? r : (r & 0x3 | 0x8)).toString(16);
                    });
            }

            try {
                const browserTimezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
                const payload = {
                    message: text || ' ',
                    local_time: new Date().toISOString(),
                    timezone: (!browserTimezone || browserTimezone === 'UTC' || browserTimezone === 'Etc/UTC')
                        ? 'Africa/Nairobi'
                        : browserTimezone
                };
                if (this.studyModeActive) {
                    payload.study_mode = true;
                }
                if (this.studySessionId) {
                    payload.study_session_id = this.studySessionId;
                }
                if (isRetry) {
                    payload.retry = true;
                    if (retryOptions.userMessageId) {
                        payload.retry_user_message_id = retryOptions.userMessageId;
                    }
                }
                const targetConversationId = this.conversationId || this.pendingConversationId;
                if (targetConversationId) {
                    payload.conversation_id = targetConversationId;
                }
                if (!this.conversationId) {
                    payload.create_conversation = true;
                }
                if (this.selectedEngine) {
                    payload.engine = this.selectedEngine;
                }
                if (snapshotAttachments.length > 0) {
                    payload.attachments = snapshotAttachments.map(a => a.id);
                }
                const contextResources = this.getContextResourcesForRequest();
                if (contextResources.length > 0) {
                    payload.context_resources = contextResources;
                }
                // Composer-as-answer: while a structured interaction is
                // pending, a typed reply resolves THAT interaction through the
                // same authoritative path as the embedded widget — it does not
                // start an unrelated turn.
                if (this.waitingForInput && this.pendingInteraction && text
                        && this.pendingInteraction.input_type !== 'group') {
                    if (this.pendingInteraction.interaction_id) {
                        payload.interaction_id = this.pendingInteraction.interaction_id;
                    } else {
                        payload.resume_run_id = this.pendingInteraction.run_id;
                    }
                }

                const response = await fetch('/api/pwanimate/chat/', {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'X-CSRFToken': csrfToken,
                        'Accept': 'text/event-stream, application/json'
                    },
                    credentials: 'same-origin',
                    signal: this.abortController.signal,
                    body: JSON.stringify(payload)
                });

                let data;
                let responseOk = response.ok;
                let responseStatus = response.status;
                // Progressive token rendering: a live bubble fills as `chunk`
                // events arrive; once the final `response` event lands it is
                // replaced by the fully-rendered message row (identical to the
                // non-streaming path).
                let streamRow = null;
                let streamRaw = '';
                let streamRenderTimer = null;
                const finalizeStreamBubble = () => {
                    if (streamRenderTimer) {
                        clearTimeout(streamRenderTimer);
                        streamRenderTimer = null;
                    }
                    if (streamRow && streamRow.parentNode) {
                        streamRow.remove();
                    }
                    streamRow = null;
                };
                const onChunk = (chunkText) => {
                    if (!chunkText) return;
                    if (!streamRow) {
                        this.removeTypingIndicator();
                        streamRow = this.createStreamingRow();
                        this.adoptActivity(streamRow, [], false, false);
                        // The final answer has started streaming: collapse the
                        // completed preparation trail unless the student
                        // explicitly opened it.
                        if (this.currentActivityTrail) this.currentActivityTrail.finish();
                    }
                    streamRaw += chunkText;
                    if (!streamRenderTimer) {
                        streamRenderTimer = setTimeout(() => {
                            streamRenderTimer = null;
                            const body = streamRow ? streamRow.querySelector('.pwanimate-markdown-body') : null;
                            if (body) {
                                body.innerHTML = this.renderMarkdownWithMathAndCode(streamRaw);
                                body.dataset.raw = streamRaw;
                                this.scrollToBottom();
                            }
                        }, 80);
                    }
                };
                if ((response.headers.get('content-type') || '').includes('text/event-stream')) {
                    const streamed = await this.readChatEventStream(response, onChunk);
                    data = streamed.data || {};
                    responseStatus = Number(streamed.status_code) || response.status;
                    responseOk = responseStatus >= 200 && responseStatus < 300;
                } else {
                    data = await response.json();
                }
                finalizeStreamBubble();

                // Handle conversation ID update even if there's an error, to prevent spawning new conversations
                if (data.conversation_id && !this.conversationId) {
                    this.conversationId = data.conversation_id;
                    this.pendingConversationId = data.conversation_id;
                    this.workspace.dataset.conversationId = data.conversation_id;

                    // Update URL without full page reload
                    const targetUrl = `/pwanimate/${data.conversation_id}/`;
                    window.history.replaceState({ htmx: true }, '', targetUrl);
                }

                if (data.study_session) {
                    this.activeStudySession = data.study_session;
                    this.studySessionId = data.study_session.id;
                    this.studyModeActive = data.study_session.status === 'active';
                    this.syncStudyModeUI();
                }

                if (!responseOk) {
                    let errMsg = data.error || 'Failed to get answer from Spark.';
                    if (responseStatus === 429) {
                        errMsg = data.error || 'All free-tier model quotas are temporarily depleted. Please wait a moment for limits to reset.';
                    } else if (responseStatus === 504) {
                        errMsg = 'Request timed out. Please try asking again.';
                    } else if (responseStatus === 401 || responseStatus === 403) {
                        errMsg = 'Your session has expired. Please refresh the page to sign in again.';
                    }
                    this.removeTypingIndicator();
                    if (userRow && data.user_message_id) {
                        userRow.dataset.messageId = data.user_message_id;
                    }
                    if (userRow) {
                        this.showErrorBubble(errMsg, text, userRow, snapshotAttachments);
                    } else {
                        this.showSystemNotice(errMsg);
                    }
                    this.refreshConversationsList();
                    if (data.quota_status) {
                        this.updateQuotaStatus(data.quota_status);
                    }
                    return;
                }

                this.removeTypingIndicator();
                if (data.study_mode_activated) {
                    this.showSystemNotice(data.notice || 'Study Mode activated. Enter a learning objective or ask a question to begin.');
                    this.refreshConversationsList();
                    return;
                }

                if (userRow && data.user_message_id) {
                    userRow.dataset.messageId = data.user_message_id;
                }

                if (Array.isArray(data.warnings) && data.warnings.length > 0) {
                    this.showUnavailableSourceWarnings(data.warnings);
                }

                const meta = data.metadata || {};
                awaitingInput = responseOk && Boolean(meta.awaiting_approval || meta.awaiting_clarification);
                if (awaitingInput) {
                    this._trackRunState(data);
                } else if (responseOk) {
                    this._clearRunState();
                }
                console.log('[Spark Telemetry]', {
                    conversation_id: data.conversation_id,
                    provider: data.provider,
                    model: data.model,
                    finish_reason: data.finish_reason,
                    prompt_tokens: data.prompt_tokens,
                    completion_tokens: data.completion_tokens,
                    thoughts_tokens: meta.thoughts_tokens,
                    total_output_tokens: meta.total_output_tokens,
                    total_tokens: data.total_tokens,
                    max_output_tokens: meta.max_output_tokens,
                    answer_length: data.answer ? data.answer.length : 0,
                    fallback_used: data.fallback_info ? data.fallback_info.fallback_used : false
                });

                // Interim Spark updates persisted server-side render here when
                // they were not already shown live during streaming.
                (data.activity || []).forEach(e => {
                    if (e && e.kind === 'say') this.appendInterimMessage(e.text, e.id);
                });
                // A paused run shows only the trail's input block — the question
                // or approval prompt is not a normal answer bubble.
                const assistantRow = this.appendAssistantMessage(awaitingInput ? '' : data.answer, data.sources, data.citations, data.fallback_info, data.quota_info, data.people, data.message_id, data.people_html, data.pending_actions || []);
                this.adoptActivity(assistantRow, data.activity || [], Boolean(data.metadata?.awaiting_approval || data.metadata?.awaiting_clarification));
                const disclaimer = this.transcript.querySelector('#pwanimate-chat-disclaimer');
                if (disclaimer) disclaimer.classList.remove('d-none');
                this.scrollToMessageStart(assistantRow);
                this.refreshConversationsList();
                this.updateQuotaStatus(data.quota_status);
                this.updateActiveModelChip(data.quota_info, data.serving_engine);
                // The usage counters moved — refresh the composer's quota chip.
                if (this.loadQuota) this.loadQuota();

            } catch (err) {
                if (err.name === 'AbortError') {
                    console.log('[Spark] Chat request aborted by user.');
                    this.removeTypingIndicator();
                    this.preserveInterruptedActivity();
                    this.showSystemNotice('Generation stopped');
                } else {
                    console.error('[Spark] Chat network error:', err);
                    this.removeTypingIndicator();
                    this.preserveInterruptedActivity();
                    this.showErrorBubble(
                        'Network connection error. Please check your internet connection.',
                        text,
                        userRow,
                        snapshotAttachments
                    );
                }
            } finally {
                this.abortController = null;
                this.setGenerating(false);
                this.setWaitingForInput(awaitingInput);
                if (this.input && isFinePointerDevice()) {
                    this.input.focus();
                }
            }
        }

        appendUserMessage(text, messageId = null, attachments = []) {
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row user';
            if (messageId) {
                row.dataset.messageId = messageId;
            }
            const escaped = escapeHtml(text || '');

            // Build attachment HTML for optimistic rendering
            let attachmentHtml = '';
            if (attachments && attachments.length > 0) {
                const chips = attachments.map(att => {
                    const attachmentUrl = att.url || `/api/pwanimate/attachments/${encodeURIComponent(att.id)}/view/`;
                    const fileName = att.file_name || 'File';
                    const fileType = (fileName.split('.').pop() || '').toLowerCase();
                    const commonAttrs = `href="${escapeHtml(attachmentUrl)}" data-attachment-id="${escapeHtml(att.id || '')}" data-file-name="${escapeHtml(fileName)}" data-file-type="${escapeHtml(fileType)}" data-media-url="${escapeHtml(attachmentUrl)}"`;
                    if (att.attachment_type === 'image' && attachmentUrl) {
                        return `<a ${commonAttrs} class="pwanimate-message-attachment pwanimate-attachment-thumb-link" data-preview-type="image" title="Preview ${escapeHtml(fileName)}">
                            <img src="${escapeHtml(attachmentUrl)}" alt="${escapeHtml(fileName)}" class="pwanimate-attachment-thumb rounded" loading="lazy">
                        </a>`;
                    }
                    return `<a ${commonAttrs} data-preview-type="document" class="pwanimate-message-attachment pwanimate-attachment-doc-chip badge text-decoration-none d-inline-flex align-items-center gap-1 p-2" title="Preview ${escapeHtml(fileName)}">
                        <i data-lucide="file-text" class="fs-6"></i>
                        <span class="text-truncate" style="max-width: 140px;">${escapeHtml(fileName)}</span>
                    </a>`;
                }).join('');
                attachmentHtml = `<div class="pwanimate-user-attachments mb-2 d-flex flex-wrap gap-2">${chips}</div>`;
            }

            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-user-bubble-wrapper">
                        ${attachmentHtml}
                        ${escaped ? `<div class="pwanimate-user-bubble" data-raw="${escaped}">${escaped}</div>` : ''}
                    </div>
                    <div class="pwanimate-message-actions user-actions">
                        <button type="button" class="btn pwanimate-action-btn copy-btn" title="Copy message" aria-label="Copy message">
                            <i data-lucide="copy"></i>
                        </button>
                        <button type="button" class="btn pwanimate-action-btn edit-btn" title="Edit message" aria-label="Edit message">
                            <i data-lucide="pencil"></i>
                        </button>
                    </div>
                </div>
            `;
            this.appendTranscriptRow(row);
            if (escaped) {
                this.checkCollapsibleUserBubbles(row);
                requestAnimationFrame(() => this.checkCollapsibleUserBubbles(row));
            }
            return row;
        }

        showTypingIndicator() {
            this.removeTypingIndicator();

            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant pwanimate-thinking-loader-row';
            row.id = 'pwanimate-typing-row';
            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-assistant-bubble">
                        <div class="pwanimate-assistant-header">
                            <img src="/static/images/pwanimate/pwanimate-transparent.png" class="pwanimate-assistant-avatar" alt="Spark">
                            <span>Spark</span>
                        </div>
                        <div class="pwanimate-thinking-loader pwanimate-typing-indicator" role="status" aria-live="polite" aria-atomic="true">
                            <div class="pwanimate-thinking-animation" aria-hidden="true">
                                <span class="pwanimate-thinking-dot dot-1"></span>
                                <span class="pwanimate-thinking-dot dot-2"></span>
                                <span class="pwanimate-thinking-dot dot-3"></span>
                            </div>
                            <span class="pwanimate-thinking-text">Thinking…</span>
                        </div>
                    </div>
                </div>
            `;
            this.appendTranscriptRow(row);
            // The trail appears lazily on the first real activity event, so
            // lightweight questions stay silent and keep only the dots.
            this.currentActivityTrail = null;
            this.renderedSayIds = new Set();

        }

        appendInterimMessage(text, eventId) {
            if (!text || !String(text).trim()) return null;
            if (!this.renderedSayIds) this.renderedSayIds = new Set();
            if (eventId && this.renderedSayIds.has(eventId)) return null;
            if (eventId) this.renderedSayIds.add(eventId);
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant pwanimate-interim-row';
            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-assistant-bubble pwanimate-interim-bubble">
                        <div class="pwanimate-markdown-body">${this.renderMarkdownWithMathAndCode(String(text))}</div>
                    </div>
                </div>
            `;
            const typing = this.transcript ? this.transcript.querySelector('#pwanimate-typing-row') : null;
            if (typing && typing.parentNode === this.transcript) {
                this.transcript.insertBefore(row, typing);
            } else {
                this.appendTranscriptRow(row);
            }
            this.scrollToBottom();
            return row;
        }

        ensureActivityTrail() {
            if (this.currentActivityTrail) return this.currentActivityTrail;
            if (!window.SparkActivity) return null;
            const trail = window.SparkActivity.create();
            trail.add({id:'request',kind:'stage',text:'Looking at your request',status:'running'});
            const row = this.transcript ? this.transcript.querySelector('#pwanimate-typing-row') : null;
            const bubble = row ? row.querySelector('.pwanimate-assistant-bubble') : null;
            const loader = row ? row.querySelector('.pwanimate-thinking-loader') : null;
            if (bubble && loader) {
                // Anchored directly above the thinking dots: the chain grows
                // upward while the loader stays put as the live indicator.
                bubble.insertBefore(trail.element, loader);
            } else if (bubble) {
                bubble.appendChild(trail.element);
            }
            this.currentActivityTrail = trail;
            return trail;
        }

        adoptActivity(row, events = [], waiting = false, finish = true) {
            if (!row || !window.SparkActivity) return;
            // Silent turns carry only the synthetic 'request' marker — do not
            // attach an empty trail to the answer.
            const hasWork = Boolean(this.currentActivityTrail)
                || events.some(e => e && e.id !== 'request');
            if (!hasWork) return;
            const trail = this.currentActivityTrail || this.ensureActivityTrail() || window.SparkActivity.create();
            events.forEach(event => trail.add(event));
            const body = row.querySelector('.pwanimate-markdown-body');
            if (body) body.before(trail.element);
            else {
                const content = row.querySelector('.pwanimate-message-content');
                if (content) content.appendChild(trail.element);
            }
            if (finish) trail.finish(waiting);
            this.currentActivityTrail = trail;
        }

        updateThinkingStatus(stage) {
            const labels = {
                thinking: 'Thinking…',
                searching_web: 'Searching the web…',
                reading_sources: 'Reading sources…',
                devouring_context: 'Devouring the context…',
                searching_posts: 'Searching pwaninet…',
                searching_documents: 'Searching the repository…'
            };
            const label = labels[stage] || (stage === 'writing_answer' ? 'Putting the answer together…' : null);
            if (label && this.currentActivityTrail) {
                this.currentActivityTrail.add({id:'stage-'+stage,kind:'stage',text:label,status:'running'});
            }
            const textEl = this.transcript
                ? this.transcript.querySelector('#pwanimate-typing-row .pwanimate-thinking-text')
                : null;
            if (!textEl || !label || textEl.textContent === label) return;

            textEl.textContent = label;
            textEl.classList.remove('status-changing');
            requestAnimationFrame(() => textEl.classList.add('status-changing'));
        }

        async readChatEventStream(response, onChunk = null) {
            if (!response.body || typeof response.body.getReader !== 'function') {
                throw new Error('Live progress is not supported by this browser.');
            }

            const reader = response.body.getReader();
            const decoder = new TextDecoder();
            let buffer = '';
            let finalPayload = null;

            const dispatchBlock = (block) => {
                const lines = block.split(/\r?\n/);
                const eventLine = lines.find(line => line.startsWith('event:'));
                const eventName = eventLine ? eventLine.slice(6).trim() : 'message';
                const data = lines
                    .filter(line => line.startsWith('data:'))
                    .map(line => line.slice(5).replace(/^ /, ''))
                    .join('\n');
                if (!data) return;

                let payload;
                try {
                    payload = JSON.parse(data);
                } catch (error) {
                    return;
                }

                if (eventName === 'activity') {
                    if (payload && payload.kind === 'say') {
                        // Spark speaks mid-task: a real bubble, not a trail step.
                        this.appendInterimMessage(payload.text, payload.id);
                    } else if (payload && payload.id !== 'request') {
                        // The synthetic 'request' marker is seeded when the trail
                        // first appears; it must not summon the trail by itself.
                        this.ensureActivityTrail()?.add(payload);
                    }
                    if (payload && payload.run_id && payload.status === 'waiting'
                            && (payload.kind === 'clarification' || payload.kind === 'approval')) {
                        // A durable run just paused: the composer can now answer
                        // the pending interaction through the same resolution path.
                        this.activeRunId = payload.run_id;
                        if (payload.kind === 'clarification') {
                            this.pendingInteraction = {
                                run_id: payload.run_id,
                                interaction_id: payload.interaction_id || null,
                                input_type: payload.input_type || 'text',
                                kind: 'clarification',
                                prompt: payload.text || '',
                                options: Array.isArray(payload.options) ? payload.options : [],
                                questions: Array.isArray(payload.questions) ? payload.questions : [],
                                allow_custom_answer: payload.allow_custom_answer !== false,
                            };
                        } else {
                            this.pendingInteraction = null;
                        }
                        this._syncInteractionBlock();
                    }
                } else if (eventName === 'status') {
                    this.updateThinkingStatus(payload.stage);
                } else if (eventName === 'chunk') {
                    if (onChunk && typeof payload.text === 'string') {
                        onChunk(payload.text);
                    }
                } else if (eventName === 'response') {
                    finalPayload = payload;
                }
            };

            while (!finalPayload) {
                const { value, done } = await reader.read();
                buffer += decoder.decode(value || new Uint8Array(), { stream: !done });

                let separator;
                while ((separator = buffer.match(/\r?\n\r?\n/))) {
                    const boundary = separator.index;
                    dispatchBlock(buffer.slice(0, boundary));
                    buffer = buffer.slice(boundary + separator[0].length);
                    if (finalPayload) break;
                }

                if (done) {
                    if (buffer.trim()) dispatchBlock(buffer);
                    break;
                }
            }

            if (finalPayload) {
                try { await reader.cancel(); } catch (error) {}
                return finalPayload;
            }
            throw new Error('The chat response ended before the answer was received.');
        }

        removeTypingIndicator() {
            const typingRows = this.transcript ? this.transcript.querySelectorAll('#pwanimate-typing-row, .pwanimate-thinking-loader-row') : [];
            typingRows.forEach(r => r.remove());
        }

        /**
         * Updates all conversation timestamp elements ([data-ts]) in both
         * desktop and mobile sidebars with live-calculated relative time strings.
         * Safe to call at any time; skips elements with no/invalid timestamp.
         */
        updateTimestamps() {
            const containers = [
                this.sidebarList || document.getElementById('pwanimate-sidebar-list'),
                this.mobileList  || document.getElementById('pwanimate-mobile-list'),
            ];
            containers.forEach((container) => {
                if (!container) return;
                container.querySelectorAll('time[data-ts]').forEach((el) => {
                    const rel = formatRelativeTime(el.getAttribute('data-ts'));
                    if (rel) el.textContent = rel;
                });
            });
        }

        /**
         * Starts a 60-second interval that keeps all sidebar timestamps current.
         * Clears any previously running ticker first to avoid duplicates.
         */
        startTimestampTicker() {
            if (this._timestampTicker) {
                clearInterval(this._timestampTicker);
            }
            this._timestampTicker = setInterval(() => {
                this.updateTimestamps();
            }, 60000);
        }

        initMobileMenuSwipeNavigation() {
            const menu = document.getElementById('pwanimateMobileMenu');
            if (!menu) return;

            this._onMobileMenuTouchStart = (event) => {
                if (!event.touches || event.touches.length !== 1) {
                    this._mobileMenuSwipeStart = null;
                    return;
                }

                const touch = event.touches[0];
                const isOpen = menu.classList.contains('show');
                if (isOpen) {
                    if (!menu.contains(event.target)) {
                        this._mobileMenuSwipeStart = null;
                        return;
                    }
                    this._mobileMenuSwipeStart = { x: touch.clientX, y: touch.clientY, action: 'close' };
                } else if (touch.clientX >= window.innerWidth - 64) {
                    this._mobileMenuSwipeStart = { x: touch.clientX, y: touch.clientY, action: 'open' };
                } else {
                    this._mobileMenuSwipeStart = null;
                }
            };

            this._onMobileMenuTouchMove = (event) => {
                const start = this._mobileMenuSwipeStart;
                if (!start || start.action !== 'open' || !event.touches || event.touches.length !== 1) return;

                const touch = event.touches[0];
                const deltaX = touch.clientX - start.x;
                const deltaY = touch.clientY - start.y;
                // Open as soon as a deliberate left swipe is clear instead of waiting for release.
                if (deltaX > -44 || Math.abs(deltaX) < Math.abs(deltaY) * 1.15) return;

                this._mobileMenuSwipeStart = null;
                const offcanvas = window.bootstrap && window.bootstrap.Offcanvas
                    ? window.bootstrap.Offcanvas.getOrCreateInstance(menu)
                    : null;
                if (offcanvas) offcanvas.show();
            };

            this._onMobileMenuTouchEnd = (event) => {
                const start = this._mobileMenuSwipeStart;
                this._mobileMenuSwipeStart = null;
                if (!start || !event.changedTouches || event.changedTouches.length !== 1) return;

                const touch = event.changedTouches[0];
                const deltaX = touch.clientX - start.x;
                const deltaY = touch.clientY - start.y;
                if (Math.abs(deltaX) < 72 || Math.abs(deltaX) < Math.abs(deltaY) * 1.3) return;

                const shouldOpen = start.action === 'open' && deltaX < 0;
                const shouldClose = start.action === 'close' && deltaX > 0;
                if (!shouldOpen && !shouldClose) return;

                const offcanvas = window.bootstrap && window.bootstrap.Offcanvas
                    ? window.bootstrap.Offcanvas.getOrCreateInstance(menu)
                    : null;
                if (!offcanvas) return;
                if (shouldOpen) offcanvas.show();
                else offcanvas.hide();
            };

            this._onMobileMenuTouchCancel = () => {
                this._mobileMenuSwipeStart = null;
            };

            document.addEventListener('touchstart', this._onMobileMenuTouchStart, { passive: true });
            document.addEventListener('touchmove', this._onMobileMenuTouchMove, { passive: true });
            document.addEventListener('touchend', this._onMobileMenuTouchEnd, { passive: true });
            document.addEventListener('touchcancel', this._onMobileMenuTouchCancel, { passive: true });
        }

        destroy() {
            this.voiceCancelled = true;
            if (this.voiceTranscriptionAbortController) this.voiceTranscriptionAbortController.abort();
            this.voiceTranscriptionAbortController = null;
            if (this.voiceRecorder && this.voiceRecorder.state === 'recording') {
                this.voiceRecorder.onstop = null;
                try { this.voiceRecorder.stop(); } catch (e) {}
            }
            this.stopVoiceLevelMeter();
            this.voiceRecorder = null;
            this.voiceChunks = [];
            if (this.isGenerating && this.abortController) {
                try { this.abortController.abort(); } catch (e) {}
            }
            this.removeTypingIndicator();
            if (this._timestampTicker) {
                clearInterval(this._timestampTicker);
                this._timestampTicker = null;
            }
            if (this._studyContextSyncTimer) {
                clearTimeout(this._studyContextSyncTimer);
                this._studyContextSyncTimer = null;
            }
            if (this.resizeFrame) {
                cancelAnimationFrame(this.resizeFrame);
                this.resizeFrame = null;
            }
            if (this._onDocumentClick) {
                document.removeEventListener('click', this._onDocumentClick);
                this._onDocumentClick = null;
            }
            if (this._onKeyDown) {
                document.removeEventListener('keydown', this._onKeyDown);
                this._onKeyDown = null;
            }
            if (this._onMobileMenuTouchStart) {
                document.removeEventListener('touchstart', this._onMobileMenuTouchStart);
                this._onMobileMenuTouchStart = null;
            }
            if (this._onMobileMenuTouchEnd) {
                document.removeEventListener('touchend', this._onMobileMenuTouchEnd);
                this._onMobileMenuTouchEnd = null;
            }
            if (this._onMobileMenuTouchMove) {
                document.removeEventListener('touchmove', this._onMobileMenuTouchMove);
                this._onMobileMenuTouchMove = null;
            }
            if (this._onMobileMenuTouchCancel) {
                document.removeEventListener('touchcancel', this._onMobileMenuTouchCancel);
                this._onMobileMenuTouchCancel = null;
            }
            if (this._onWindowResize) {
                window.removeEventListener('resize', this._onWindowResize);
                this._onWindowResize = null;
            }
            if (this._transcriptResizeObserver) {
                try { this._transcriptResizeObserver.disconnect(); } catch (_) {}
                this._transcriptResizeObserver = null;
            }
            if (typeof this._resizerCleanup === 'function') {
                this._resizerCleanup();
                this._resizerCleanup = null;
            }
            if (this.previewDocViewer && typeof this.previewDocViewer.destroy === 'function') {
                try { this.previewDocViewer.destroy(); } catch (_) {}
                this.previewDocViewer = null;
            }
            if (this.contextDocViewer && typeof this.contextDocViewer.destroy === 'function') {
                try { this.contextDocViewer.destroy(); } catch (_) {}
                this.contextDocViewer = null;
            }
            document.body.classList.remove('pwanimate-resizing');
            if (window._activeSparkChat === this) {
                window._activeSparkChat = null;
            }
        }


        createStreamingRow() {
            // Lightweight assistant bubble used ONLY while chunks stream in;
            // the final response event swaps it for the full message row.
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant pwanimate-streaming-row';
            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-assistant-bubble">
                        <div class="pwanimate-assistant-header">
                            <img src="/static/images/pwanimate/pwanimate-transparent.png" class="pwanimate-assistant-avatar" alt="Spark">
                            <span>Spark</span>
                        </div>
                        <div class="pwanimate-markdown-body" data-raw=""></div>
                    </div>
                </div>
            `;
            this.transcript.appendChild(row);
            return row;
        }

        preserveInterruptedActivity() {
            if (!this.currentActivityTrail) return;
            this.currentActivityTrail.finish(false, true);
            if (!this.currentActivityTrail.element.isConnected) {
                const row = document.createElement('div');
                row.className = 'pwanimate-message-row assistant';
                row.appendChild(this.currentActivityTrail.element);
                this.appendTranscriptRow(row);
            }
        }

        async resumeAgent(runId, decision, actionIds, priorTrail, eventId = null) {
            if (this.isGenerating) return;
            this.setGenerating(true);
            this.abortController = new AbortController();
            this.showTypingIndicator();
            this.currentActivityTrail = priorTrail;
            priorTrail.element.open = true;
            priorTrail.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            let stillWaiting = false;
            try {
                const response = await fetch('/api/pwanimate/chat/', {
                    method:'POST', credentials:'same-origin',signal:this.abortController.signal,
                    headers:{'Content-Type':'application/json','Accept':'text/event-stream, application/json','X-CSRFToken':getCsrfToken()},
                    body:JSON.stringify({resume_run_id:runId,decision,action_ids:actionIds})
                });
                const result = (response.headers.get('content-type') || '').includes('text/event-stream')
                    ? await this.readChatEventStream(response) : {status_code:response.status,data:await response.json()};
                const data = result.data || {};
                if (result.status_code >= 400) throw new Error(data.error || 'Could not continue this task.');
                if (data.approval_pending) {
                    stillWaiting = true;
                    this._trackRunState(data);
                    if (eventId) this.resolvedActivityEvents.add(eventId);
                    priorTrail.add({id:'approval-'+runId,kind:'approval',text:'Please review the remaining actions.',status:'waiting',run_id:runId,actions:data.run.actions});
                    priorTrail.finish(true);
                    // Replace the original approval controls, which may contain stale decisions.
                    priorTrail.element.querySelectorAll('.spark-approval').forEach(box => {
                        if (box !== priorTrail.element.querySelector('.spark-activity-step:last-child .spark-approval')) box.remove();
                    });
                    return;
                }
                this.removeTypingIndicator();
                (data.activity || []).forEach(e => {
                    if (e && e.kind === 'say') this.appendInterimMessage(e.text, e.id);
                });
                const awaitingNow = Boolean(data.metadata?.awaiting_approval || data.metadata?.awaiting_clarification);
                if (eventId) this.resolvedActivityEvents.add(eventId);
                if (awaitingNow) {
                    // Still waiting: the paused trail already hosts the input
                    // block — no new bubble, generation visibly continues.
                    stillWaiting = true;
                    this._trackRunState(data);
                } else {
                    this._clearRunState();
                    const row = this.appendAssistantMessage(data.answer,data.sources,data.citations,null,data.quota_info,[],data.message_id);
                    this.adoptActivity(row,data.activity || [],false);
                    // adoptActivity moved the trail into the answer row; drop the
                    // emptied pause bubble so no blank Spark message remains.
                    const trailEl = this.currentActivityTrail && this.currentActivityTrail.element;
                    const pauseRow = priorTrail && priorTrail.element
                        ? priorTrail.element.closest('.pwanimate-message-row') : null;
                    if (trailEl && row.contains(trailEl) && pauseRow && pauseRow !== row) {
                        pauseRow.remove();
                    }
                    this.scrollToMessageStart(row);
                    this.refreshConversationsList();
                }
            } catch (error) {
                // The dots must never outlive the request: a failed resume is
                // over, so clear them before surfacing the notice.
                this.removeTypingIndicator();
                this.showSystemNotice(error.message || 'The task was interrupted. Refresh to see saved outcomes.');
                priorTrail.element.querySelectorAll('button').forEach(b => { b.disabled = false; });
                throw error;
            } finally {
                this.abortController = null;
                this.setGenerating(false);
                this.setWaitingForInput(stillWaiting);
            }
        }

        async resumeClarification(runId, answer, priorTrail, eventId = null, interactionId = null) {
            const payload = interactionId
                ? {interaction_id: interactionId, resume_run_id: runId, answer: {text: answer}}
                : {resume_run_id: runId, clarification_answer: answer};
            return this._resumeChatPayload(payload, priorTrail, eventId);
        }

        // Structured interaction answers (single/multi choice, confirm, custom
        // text) resolve through the same durable claim path as free text.
        // `answer` matches the server contract: {text} | {selected:[ids]} |
        // {confirmed:bool}.
        async resumeInteraction(runId, interactionId, answer, priorTrail, eventId = null) {
            return this._resumeChatPayload(
                {interaction_id: interactionId, resume_run_id: runId, answer},
                priorTrail, eventId);
        }

        async _resumeChatPayload(payload, priorTrail, eventId = null) {
            if (this.isGenerating) return;
            this.setGenerating(true);
            this.abortController = new AbortController();
            // Dots at the bottom signal live work; events still stream into
            // the paused trail, which we scroll back into view.
            this.showTypingIndicator();
            this.currentActivityTrail = priorTrail || this.currentActivityTrail;
            if (priorTrail) {
                priorTrail.element.open = true;
                priorTrail.element.scrollIntoView({ behavior: 'smooth', block: 'center' });
            }
            let stillWaiting = false;
            try {
                const response = await fetch('/api/pwanimate/chat/', {
                    method:'POST', credentials:'same-origin',signal:this.abortController.signal,
                    headers:{'Content-Type':'application/json','Accept':'text/event-stream, application/json','X-CSRFToken':getCsrfToken()},
                    body:JSON.stringify(payload)
                });
                const result = (response.headers.get('content-type') || '').includes('text/event-stream')
                    ? await this.readChatEventStream(response) : {status_code:response.status,data:await response.json()};
                const data = result.data || {};
                if (result.status_code >= 400) throw new Error(data.error || 'Could not continue this task.');
                if (data.approval_pending) {
                    stillWaiting = true;
                    this._trackRunState(data);
                    if (eventId) this.resolvedActivityEvents.add(eventId);
                    if (priorTrail) priorTrail.add({id:'approval-'+runId,kind:'approval',text:'Please review the remaining actions.',status:'waiting',run_id:runId,actions:data.run.actions});
                    if (priorTrail) priorTrail.finish(true);
                    return;
                }
                this.removeTypingIndicator();
                (data.activity || []).forEach(e => {
                    if (e && e.kind === 'say') this.appendInterimMessage(e.text, e.id);
                });
                const awaitingNow = Boolean(data.metadata?.awaiting_approval || data.metadata?.awaiting_clarification);
                if (eventId) this.resolvedActivityEvents.add(eventId);
                if (awaitingNow) {
                    stillWaiting = true;
                    this._trackRunState(data);
                } else {
                    this._clearRunState();
                    const row = this.appendAssistantMessage(data.answer,data.sources,data.citations,null,data.quota_info,[],data.message_id);
                    this.adoptActivity(row,data.activity || [],false);
                    const trailEl = this.currentActivityTrail && this.currentActivityTrail.element;
                    const pauseRow = priorTrail && priorTrail.element
                        ? priorTrail.element.closest('.pwanimate-message-row') : null;
                    if (trailEl && row.contains(trailEl) && pauseRow && pauseRow !== row) {
                        pauseRow.remove();
                    }
                    this.scrollToMessageStart(row);
                    this.refreshConversationsList();
                }
            } catch (error) {
                this.removeTypingIndicator();
                this.showSystemNotice(error.message || 'The task was interrupted. Refresh to see saved outcomes.');
                if (priorTrail) priorTrail.element.querySelectorAll('button, input').forEach(b => { b.disabled = false; });
                throw error;
            } finally {
                this.abortController = null;
                this.setGenerating(false);
                this.setWaitingForInput(stillWaiting);
            }
        }

        // ------------------------------------------------- proposed actions
        async decideAction(actionId, decision, button) {
            if (!actionId) return;
            if (button) button.disabled = true;

            try {
                const response = await fetch(`/api/pwanimate/actions/${actionId}/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ decision: decision }),
                });
                const data = await response.json().catch(() => ({}));
                if (!response.ok) {
                    this.showSystemNotice(data.error || 'That action could not be completed.');
                    if (button) button.disabled = false;
                    return;
                }

                const action = data.action || {};
                const card = button ? button.closest('.pwanimate-action-card') : null;
                if (card) {
                    card.dataset.actionStatus = action.status || '';
                    const title = card.querySelector('.pwanimate-action-card-title');
                    const buttons = card.querySelector('.pwanimate-action-card-buttons');
                    if (buttons) buttons.remove();
                    if (title) {
                        title.textContent = action.status === 'confirmed'
                            ? 'Done'
                            : (action.status === 'cancelled' ? 'Cancelled' : 'Done');
                    }
                }
            } catch (error) {
                console.warn('[Spark] Could not complete the action', error);
                if (button) button.disabled = false;
            }
        }

        // ------------------------------------------------- answer feedback
        async sendFeedback(row, helpful, reason) {
            if (!row) return;
            const messageId = row.dataset.messageId;
            if (!messageId) return;

            const buttons = row.querySelectorAll('.pwanimate-feedback-btn');
            buttons.forEach((btn) => btn.classList.add('disabled'));

            try {
                const response = await fetch(`/api/pwanimate/messages/${messageId}/feedback/`, {
                    method: 'POST',
                    headers: {
                        'Content-Type': 'application/json',
                        'Accept': 'application/json',
                        'X-CSRFToken': getCsrfToken(),
                    },
                    credentials: 'same-origin',
                    body: JSON.stringify({ helpful: helpful, reason: reason || '' }),
                });
                if (!response.ok) return;

                // Mark the chosen verdict so the student can see it landed.
                buttons.forEach((btn) => btn.classList.remove('feedback-active'));
                const wanted = helpful ? '[data-feedback="helpful"]' : '.pwanimate-feedback-reason > .pwanimate-feedback-btn';
                const chosen = row.querySelector(wanted);
                if (chosen) chosen.classList.add('feedback-active');
            } catch (error) {
                // Feedback is a courtesy; never surface an error to a student.
                console.warn('[Spark] Could not record feedback', error);
            } finally {
                buttons.forEach((btn) => btn.classList.remove('disabled'));
            }
        }

        appendAssistantMessage(answer, sources, citations, fallbackInfo, quotaInfo, people = [], messageId = null, peopleHtmlFromServer = '', pendingActions = []) {
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant';
            if (messageId) {
                row.dataset.messageId = messageId;
            }

            const parsedHtml = this.renderMarkdownWithMathAndCode(answer);

            let peopleHtml = '';
            if (Array.isArray(people) && people.length > 0) {
                peopleHtml = typeof peopleHtmlFromServer === 'string' && peopleHtmlFromServer
                    ? peopleHtmlFromServer
                    : this.renderPeopleCards(people);
            }

            let citationsHtml = '';
            // If people cards are rendered, omit redundant person items from general sources
            const filteredSources = (Array.isArray(people) && people.length > 0)
                ? (sources || []).filter(src => src.source !== 'user')
                : (sources || []);

            if (Array.isArray(filteredSources) && filteredSources.length > 0) {
                const badges = filteredSources.map((src) => {
                    const title = escapeHtml(src.title || src.citation || 'Document');
                    const person = src.person || {};
                    if (src.url) {
                        return `<button type="button" class="pwanimate-citation-badge"
                            data-url="${escapeHtml(src.url)}"
                            data-title="${title}"
                            data-source-type="${escapeHtml(src.source || '')}"
                            data-web-citation-index="${escapeHtml(src.web_citation_index || '')}"
                            data-domain="${escapeHtml(src.domain || '')}"
                            data-resource-type="${escapeHtml(src.resource_type || '')}"
                            data-media-url="${escapeHtml(src.media_url || '')}"
                            data-thumbnail-url="${escapeHtml(src.thumbnail_url || '')}"
                            data-hls-url="${escapeHtml(src.hls_url || '')}"
                            data-author="${escapeHtml(src.author || '')}"
                            data-snippet="${escapeHtml(src.snippet || '')}"
                            data-post-id="${escapeHtml(src.post_id || '')}"
                            data-citation="${escapeHtml(src.citation || '')}"
                            data-page-number="${escapeHtml(src.page_number || '')}"
                            data-document-id="${escapeHtml(src.document_id || '')}"
                            data-document-share-id="${escapeHtml(src.document_share_id || '')}"
                            data-file-type="${escapeHtml(src.file_type || src.file_extension || '')}"
                            data-username="${escapeHtml(person.username || '')}"
                            data-profile-url="${escapeHtml(person.profile_url || '')}"
                            data-profile-card-url="${escapeHtml(person.profile_card_url || '')}"
                            data-avatar-url="${escapeHtml(person.avatar_url || person.profile_photo_url || '')}"
                            data-headline="${escapeHtml(person.headline || '')}"
                            data-academic-level="${escapeHtml(person.academic_level || '')}"
                            data-programme-name="${escapeHtml(person.programme_name || person.programme || '')}"
                            data-bio="${escapeHtml(person.bio || '')}"
                            aria-label="View ${title}">
                            <i data-lucide="link"></i>
                            <span>${title}</span>
                        </button>`;
                    }
                    return `<span class="pwanimate-citation-badge">
                        <i data-lucide="file-text"></i>
                        <span>${title}</span>
                    </span>`;
                }).join('');

                citationsHtml = `
                    <div class="pwanimate-citations">
                        <div class="pwanimate-citations-header">
                            <i data-lucide="file-check"></i>
                            <span>Sources referenced</span>
                        </div>
                        <div class="pwanimate-citation-list">${badges}</div>
                    </div>
                `;
            } else if (Array.isArray(citations) && citations.length > 0 && (!Array.isArray(people) || people.length === 0)) {
                const badges = citations.map((cit) => `
                    <span class="pwanimate-citation-badge">
                        <i data-lucide="bookmark"></i>
                        <span>${escapeHtml(cit)}</span>
                    </span>
                `).join('');

                citationsHtml = `
                    <div class="pwanimate-citations">
                        <div class="pwanimate-citations-header">
                            <i data-lucide="quote"></i>
                            <span>Citations</span>
                        </div>
                        <div class="pwanimate-citation-list">${badges}</div>
                    </div>
                `;
            }

            // Actions Spark proposed. Nothing has happened yet: the student
            // approves here, and the card updates to say so.
            let actionsHtml = '';
            if (Array.isArray(pendingActions) && pendingActions.length > 0) {
                const cards = pendingActions.map((action) => {
                    const actionId = escapeHtml(action.id || '');
                    const summary = escapeHtml(action.summary || '');
                    const decided = action.status && action.status !== 'pending';
                    const statusText = decided
                        ? (action.status === 'confirmed' ? 'Done' : escapeHtml(action.status))
                        : 'Needs your approval';
                    return `
                        <div class="pwanimate-action-card" data-action-id="${actionId}" data-action-status="${escapeHtml(action.status || 'pending')}">
                            <div class="pwanimate-action-card-header">
                                <i data-lucide="send"></i>
                                <span class="pwanimate-action-card-title">${statusText}</span>
                            </div>
                            <div class="pwanimate-action-card-body">${summary}</div>
                            ${decided ? '' : `
                                <div class="pwanimate-action-card-buttons">
                                    <button type="button" class="btn btn-sm btn-primary pwanimate-action-confirm" data-action-id="${actionId}">Send</button>
                                    <button type="button" class="btn btn-sm btn-outline-secondary pwanimate-action-cancel" data-action-id="${actionId}">Cancel</button>
                                </div>`}
                        </div>`;
                }).join('');
                actionsHtml = `<div class="pwanimate-action-cards">${cards}</div>`;
            }

            let fallbackHtml = '';
            if (fallbackInfo && fallbackInfo.vision_text_fallback) {
                // No banner: the model reports the outage in its answer. The
                // only extra is a one-time CHOICE (the image may be one the
                // student wants to keep asking about) — shown while the
                // decision is still open, never repeated.
                const attachmentId = fallbackInfo.vision_attachment_id;
                const policy = fallbackInfo.vision_policy || 'ask';
                if (attachmentId && policy === 'ask') {
                    fallbackHtml = `
                        <div class="pwanimate-fallback-notice pwanimate-vision-choice" data-vision-choice="${escapeHtml(attachmentId)}">
                            <i data-lucide="eye-off"></i>
                            <span>Keep this image in the conversation?</span>
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-vision-policy="keep">Keep it</button>
                            <button type="button" class="btn btn-sm btn-outline-secondary" data-vision-policy="drop">Drop it</button>
                        </div>
                    `;
                }
            }

            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-assistant-bubble">
                        <div class="pwanimate-assistant-header">
                            <img src="/static/images/pwanimate/pwanimate-transparent.png" class="pwanimate-assistant-avatar" alt="Spark">
                            <span>Spark</span>
                        </div>
                        <div class="pwanimate-markdown-body" data-raw="${escapeHtml(answer)}">${parsedHtml}</div>
                        ${peopleHtml}
                        ${citationsHtml}
                        ${fallbackHtml}
                        ${actionsHtml}
                    </div>
                    <div class="pwanimate-message-actions assistant-actions">
                        <div class="dropdown pwanimate-resource-export">
                            <button type="button" class="btn pwanimate-action-btn" data-bs-toggle="dropdown" aria-expanded="false" title="Save response to My Resources" aria-label="Save response to My Resources">
                                <i data-lucide="file-down"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end pwanimate-resource-export-menu">
                                <li><button type="button" class="dropdown-item pwanimate-resource-download" data-format="pdf"><i data-lucide="file-text" class="me-2"></i>Download PDF</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-resource-download" data-format="docx"><i data-lucide="file-text" class="me-2"></i>Download Word document</button></li>
                            </ul>
                        </div>
                        <button type="button" class="btn pwanimate-action-btn pwanimate-feedback-btn" data-feedback="helpful" title="This helped" aria-label="This helped">
                            <i data-lucide="thumbs-up"></i>
                        </button>
                        <div class="dropdown pwanimate-feedback-reason">
                            <button type="button" class="btn pwanimate-action-btn pwanimate-feedback-btn" data-bs-toggle="dropdown" aria-expanded="false" title="This did not help" aria-label="This did not help">
                                <i data-lucide="thumbs-down"></i>
                            </button>
                            <ul class="dropdown-menu dropdown-menu-end pwanimate-feedback-reason-menu">
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="wrong_answer">The answer is wrong</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="not_my_document">It ignored my document</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="too_slow">It took too long</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="wrong_language">Wrong language or register</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="too_long">Too long or too short</button></li>
                                <li><button type="button" class="dropdown-item pwanimate-feedback-reason-item" data-reason="other">Something else</button></li>
                            </ul>
                        </div>
                        <button type="button" class="btn pwanimate-action-btn pwanimate-speak-btn" title="Read this reply aloud" aria-label="Read this reply aloud" aria-pressed="false">
                            <i data-lucide="volume-2" aria-hidden="true"></i>
                        </button>
                        <button type="button" class="btn pwanimate-action-btn copy-btn" title="Copy answer" aria-label="Copy answer">
                            <i data-lucide="copy"></i>
                        </button>
                    </div>
                </div>
            `;

            this.appendTranscriptRow(row);
            const mdBody = row.querySelector('.pwanimate-markdown-body');
            if (mdBody) {
                this.postProcessMarkdownDOM(mdBody);
            }
            this.enhanceWebSources(row);
            sparkRenderIcons(row);
            if (window.htmx && typeof window.htmx.process === 'function') {
                window.htmx.process(row);
            }
            if (this.autoTtsEnabled) {
                const speakBtn = row.querySelector('.pwanimate-speak-btn');
                if (speakBtn) this.speakResponse(answer, speakBtn);
            }
            return row;
        }

        renderPeopleCards(people) {
            if (!Array.isArray(people) || people.length === 0) {
                return '';
            }

            const cardsHtml = people.map(p => {
                const username = escapeHtml(p.username || 'user');
                const displayName = escapeHtml(p.display_name || p.username || 'User');
                const profileUrl = escapeHtml(p.profile_url || `/users/user/${username}/`);
                const personData = escapeHtml(JSON.stringify(p));
                const initial = escapeHtml((p.display_name || p.username || 'U').charAt(0).toUpperCase());

                let avatarHtml = '';
                if (p.avatar_url) {
                    avatarHtml = `<img src="${escapeHtml(p.avatar_url)}" alt="${displayName}" class="rounded-circle object-fit-cover shadow-xs pwanimate-person-avatar" style="width: 48px; height: 48px;" onerror="this.onerror=null; this.src='/static/images/default_pic1.jpg';">`;
                } else {
                    avatarHtml = `<div class="rounded-circle pwanimate-avatar-placeholder d-flex align-items-center justify-content-center fw-bold shadow-xs" style="width: 48px; height: 48px; font-size: 1.1rem;">${initial}</div>`;
                }

                let academicHtml = '';
                if (p.academic_level || p.programme_name) {
                    const level = p.academic_level ? escapeHtml(p.academic_level) : '';
                    const prog = p.programme_name ? escapeHtml(p.programme_name) : '';
                    const sep = (level && prog) ? ' • ' : '';
                    academicHtml = `<div class="pwanimate-person-academic small pwanimate-line-clamp-2 mb-2"><i data-lucide="graduation-cap" class="me-1"></i>${level}${sep}${prog}</div>`;
                }

                let headlineHtml = '';
                if (p.headline) {
                    headlineHtml = `<div class="pwanimate-person-headline small text-muted pwanimate-line-clamp-2 mb-2">${escapeHtml(p.headline)}</div>`;
                }

                let badgesHtml = '';
                if (p.collaboration_status === 'open_to_projects') {
                    badgesHtml += `<span class="badge pwanimate-badge-collab"><i data-lucide="code" class="me-1"></i>Open to Projects</span>`;
                } else if (p.collaboration_status === 'open_to_study_groups') {
                    badgesHtml += `<span class="badge pwanimate-badge-collab"><i data-lucide="book-open" class="me-1"></i>Open to Study Groups</span>`;
                } else if (p.collaboration_status === 'open_to_networking') {
                    badgesHtml += `<span class="badge pwanimate-badge-collab"><i data-lucide="users" class="me-1"></i>Open to Networking</span>`;
                } else if (p.collaboration_status === 'any_open') {
                    badgesHtml += `<span class="badge pwanimate-badge-collab"><i data-lucide="user-check" class="me-1"></i>Open to Collaborate</span>`;
                }

                if (Array.isArray(p.matched_skills)) {
                    p.matched_skills.forEach(skill => {
                        badgesHtml += `<span class="badge pwanimate-badge-skill"><i data-lucide="check" class="me-1"></i>${escapeHtml(skill)}</span>`;
                    });
                }

                if (Array.isArray(p.matched_interests)) {
                    p.matched_interests.forEach(interest => {
                        badgesHtml += `<span class="badge pwanimate-badge-interest"><i data-lucide="star" class="me-1"></i>${escapeHtml(interest)}</span>`;
                    });
                }

                if (p.evidence && typeof p.evidence === 'object') {
                    if (p.evidence.academic_alignment) {
                        badgesHtml += `<span class="badge pwanimate-badge-evidence"><i data-lucide="graduation-cap" class="me-1"></i>${escapeHtml(p.evidence.academic_alignment)}</span>`;
                    }
                    if (p.evidence.mutual_connections_count) {
                        badgesHtml += `<span class="badge pwanimate-badge-evidence"><i data-lucide="users" class="me-1"></i>${p.evidence.mutual_connections_count} mutual</span>`;
                    }
                    if (p.evidence.shared_groups_count) {
                        badgesHtml += `<span class="badge pwanimate-badge-evidence"><i data-lucide="library" class="me-1"></i>${p.evidence.shared_groups_count} groups</span>`;
                    }
                }

                return `
                    <div class="col-12 col-xl-6 pwanimate-person-col">
                        <div class="card h-100 border rounded-4 p-3 shadow-xs pwanimate-person-card">
                            <div class="d-flex align-items-start gap-3">
                                <a href="${profileUrl}" class="text-decoration-none flex-shrink-0" hx-get="${profileUrl}" hx-target="#page-content-target" hx-swap="innerHTML" hx-push-url="true" hx-history="true" title="${displayName}">
                                    <div class="position-relative">
                                        ${avatarHtml}
                                    </div>
                                </a>
                                <div class="flex-grow-1 min-w-0">
                                    <div class="d-flex align-items-center justify-content-between gap-1 mb-1">
                                        <div class="min-w-0 flex-grow-1 pe-1">
                                            <a href="${profileUrl}" class="text-decoration-none pwanimate-person-name fw-bold text-truncate d-block" hx-get="${profileUrl}" hx-target="#page-content-target" hx-swap="innerHTML" hx-push-url="true" hx-history="true" style="font-size: 0.95rem;">
                                                ${displayName}
                                            </a>
                                            <span class="pwanimate-person-handle text-muted small d-block text-truncate" style="font-size: 0.78rem;">
                                                @${username}
                                            </span>
                                        </div>
                                    </div>
                                    ${academicHtml}
                                    ${headlineHtml}
                                    <div class="d-flex flex-wrap gap-1 pwanimate-person-badges mb-2">
                                        ${badgesHtml}
                                    </div>
                                    <div class="d-flex align-items-center justify-content-end gap-2 mt-2 pt-1 border-top" style="border-color: var(--pwanimate-border) !important;">
                                        <button type="button" class="btn btn-sm btn-outline-primary rounded-pill px-3 py-1 pwanimate-person-preview-btn" data-profile-url="${profileUrl}" data-person="${personData}" style="font-size: 0.78rem;">
                                            <i data-lucide="panel-right-open" class="me-1"></i>Preview here
                                        </button>
                                        <a href="${profileUrl}" class="btn btn-sm btn-outline-primary rounded-pill px-3 py-1 pwanimate-view-profile-btn" hx-get="${profileUrl}" hx-target="#page-content-target" hx-swap="innerHTML" hx-push-url="true" hx-history="true" style="font-size: 0.78rem;">
                                            <i data-lucide="user" class="me-1"></i>View Profile
                                        </a>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                `;
            }).join('');

            return `
                <div class="pwanimate-people-section mt-3">
                    <div class="pwanimate-people-header d-flex align-items-center gap-2 mb-2 text-muted small fw-semibold">
                        <i data-lucide="users" class="text-primary"></i>
                        <span>Discovered Students & Peers</span>
                    </div>
                    <div class="pwanimate-people-grid row g-2">
                        ${cardsHtml}
                    </div>
                </div>
            `;
        }

        showErrorBubble(message, originalText, userRow = null, attachments = []) {
            const row = document.createElement('div');
            row.className = 'pwanimate-message-row assistant';
            row.innerHTML = `
                <div class="pwanimate-message-content">
                    <div class="pwanimate-error-bubble">
                        <div>
                            <i data-lucide="triangle-alert" class="me-2"></i>
                            <span>${escapeHtml(message)}</span>
                        </div>
                        <button type="button" class="btn btn-sm btn-outline-danger pwanimate-retry-btn">
                            <i data-lucide="rotate-cw"></i> Retry
                        </button>
                    </div>
                </div>
            `;

            const retryBtn = row.querySelector('.pwanimate-retry-btn');
            if (retryBtn) {
                retryBtn.addEventListener('click', () => {
                    row.remove();
                    this.handleSend({
                        text: originalText,
                        userRow,
                        userMessageId: userRow && userRow.dataset.messageId,
                        attachments
                    });
                });
            }

            this.appendTranscriptRow(row);
            this.scrollToBottom();
        }

        async handleDeleteConversation(convId) {
            if (!confirm('Are you sure you want to delete this conversation?')) return;

            const csrfToken = getCsrfToken();
            try {
                let res = await fetch(`/pwanimate/${convId}/delete/`, {
                    method: 'DELETE',
                    headers: {
                        'X-CSRFToken': csrfToken,
                        'Accept': 'application/json'
                    }
                });

                if (res.status === 404 || res.status === 405) {
                    res = await fetch(`/api/pwanimate/conversations/${convId}/`, {
                        method: 'DELETE',
                        headers: {
                            'X-CSRFToken': csrfToken
                        }
                    });
                }

                if (res.status === 204 || res.ok) {
                    // Remove from desktop and mobile lists across the whole document
                    const items = document.querySelectorAll(`.pwanimate-conversation-item[data-id="${convId}"]`);
                    items.forEach((it) => it.remove());

                    // Check if either list is now empty and insert placeholder
                    const desktopList = (this && this.sidebarList) || document.getElementById('pwanimate-sidebar-list');
                    if (desktopList && !desktopList.querySelector('.pwanimate-conversation-item')) {
                        desktopList.innerHTML = '<div class="text-center text-muted p-3 x-small">No conversations yet. Ask a question to start one!</div>';
                    }
                    const mobileList = (this && this.mobileList) || document.getElementById('pwanimate-mobile-list');
                    if (mobileList && !mobileList.querySelector('.pwanimate-conversation-item')) {
                        mobileList.innerHTML = '<div class="text-center text-muted p-3 x-small">No conversations yet. Ask a question to start one!</div>';
                    }

                    // If active conversation was deleted, navigate to index
                    const activeConvId = (this && this.conversationId) || document.getElementById('pwanimate-workspace')?.dataset?.conversationId;
                    if (activeConvId === convId) {
                        if (window.htmx) {
                            window.htmx.ajax('GET', '/pwanimate/', {
                                target: '#page-content-target',
                                pushUrl: true,
                                swap: 'innerHTML'
                            });
                        } else {
                            window.location.href = '/pwanimate/';
                        }
                    }
                } else {
                    alert('Failed to delete conversation.');
                }
            } catch (e) {
                console.error('[Spark] Delete error:', e);
                alert('Network error while deleting conversation.');
            }
        }

        async refreshConversationsList() {
            try {
                const url = this.historyFilter === 'study'
                    ? '/api/pwanimate/conversations/?filter=study'
                    : '/api/pwanimate/conversations/';
                const res = await fetch(url, {
                    headers: {
                        'Accept': 'application/json'
                    }
                });
                if (!res.ok) return;
                const conversations = await res.json();
                this.renderConversationsList(conversations);
            } catch (e) {
                console.warn('[Spark] Failed to refresh conversation list:', e);
            }
        }

        renderConversationsList(conversations) {
            if (!Array.isArray(conversations)) return;

            let html = '';
            if (conversations.length === 0) {
                html = `<div class="text-center text-muted p-3 x-small">No conversations yet. Ask a question to start one!</div>`;
            } else {
                html = conversations.map((c) => {
                    const isActive = this.conversationId === c.id ? 'active' : '';
                    const title = escapeHtml(c.title || 'New Conversation');
                    const ts = c.updated_at || c.created_at || '';
                    const relTime = ts ? formatRelativeTime(ts) : 'just now';
                    const tsAttr = ts ? ` data-ts="${escapeHtml(ts)}"` : '';
                    const ss = c.study_session || null;
                    let studyBadgeHtml = '';
                    let objectiveHtml = '';
                    if (ss) {
                        const statusClass = escapeHtml(ss.status || 'active');
                        const statusTitle = escapeHtml((ss.status || 'active').replace(/^\w/, ch => ch.toUpperCase()));
                        studyBadgeHtml = `<span class="badge rounded-pill pwanimate-study-conv-badge status-${statusClass}" title="Study Session (${statusTitle})"><i data-lucide="graduation-cap" class="me-1"></i>Study</span>`;
                        if (ss.learning_objective) {
                            const objEscaped = escapeHtml(ss.learning_objective);
                            objectiveHtml = `<div class="text-muted text-truncate x-small mt-1" title="${objEscaped}">${objEscaped}</div>`;
                        }
                    }
                    return `
                        <div class="pwanimate-conversation-item ${isActive}"
                             data-id="${c.id}"
                             data-is-study="${ss ? 'true' : 'false'}"
                             hx-get="/pwanimate/${c.id}/"
                             hx-target="#page-content-target"
                             hx-swap="innerHTML"
                             hx-push-url="true"
                             role="button"
                             tabindex="0">
                            <div class="pwanimate-conversation-info">
                                <div class="pwanimate-conversation-title d-flex align-items-center gap-1" title="${title}">
                                    ${studyBadgeHtml}
                                    <span class="text-truncate">${title}</span>
                                </div>
                                ${objectiveHtml}
                                <div class="pwanimate-conversation-time"><time${tsAttr}>${relTime}</time></div>
                            </div>
                            <button type="button"
                                    class="pwanimate-conversation-delete"
                                    data-conversation-id="${c.id}"
                                    aria-label="Delete conversation"
                                    title="Delete conversation"
                                    onclick="event.stopPropagation(); event.preventDefault(); window.handleDeleteSparkConversation('${c.id}');">
                                <i data-lucide="trash-2"></i>
                            </button>
                        </div>
                    `;
                }).join('');
            }

            const desktopList = this.sidebarList || document.getElementById('pwanimate-sidebar-list');
            if (desktopList) {
                desktopList.innerHTML = html;
                if (window.htmx) window.htmx.process(desktopList);
            }
            const mobileList = this.mobileList || document.getElementById('pwanimate-mobile-list');
            if (mobileList) {
                mobileList.innerHTML = html;
                if (window.htmx) window.htmx.process(mobileList);
            }
        }

        async fetchQuotaStatus() {
            try {
                const res = await fetch('/api/pwanimate/quota-status/', {
                    headers: { 'Accept': 'application/json' },
                    credentials: 'same-origin'
                });
                if (!res.ok) return;
                const data = await res.json();
                this.updateQuotaStatus(data.quota_status);

                // Initialize active model chip from status if available
                if (data.quota_status && Array.isArray(data.quota_status.models) && data.quota_status.models.length > 0) {
                    const primary = data.quota_status.models[0];
                    const active = data.quota_status.models.find((m) => !m.rate_limited) || primary;
                    // Brand-level attribution: credit the engine whose primary
                    // matches the active model — never show the model itself.
                    const enginesList = data.quota_status.engines || [];
                    const matched = enginesList.find((eng) =>
                        eng.primary_provider === active.provider && eng.primary_model === active.model
                    );
                    this.updateActiveModelChip({
                        active_model: active.model || active.provider,
                        primary_model: primary.model || primary.provider,
                        switched: (active.model !== primary.model || active.provider !== primary.provider),
                    }, matched ? matched.name : null);
                }
            } catch (e) {
                console.warn('[Spark] Failed to fetch quota status:', e);
            }
        }

        getDefaultEngines() {
            // Static mirror of the server engine registry — used only when
            // the quota-status API is unreachable.
            return [
                { slug: 'speed', name: 'Spark Speed', icon: 'bi-lightning-charge', tagline: 'Rapid everyday Q&A and chat', description: "Fast, balanced answers for quick questions — Spark's everyday workhorse with automatic multi-provider failover.", powered_by: ['Google', 'Groq', 'OpenRouter'], is_configured: true, rate_limited: false, cooldown_remaining: 0, fallbacks_ready: 2 },
                { slug: 'coda', name: 'Spark Coda', icon: 'bi-terminal', tagline: 'Code debugging and explanations', description: 'Reads, traces and fixes code — line-by-line debugging help for assignments and past papers.', powered_by: ['Groq', 'Google', 'OpenRouter'], is_configured: true, rate_limited: false, cooldown_remaining: 0, fallbacks_ready: 2 },
                { slug: 'scholar', name: 'Spark Scholar', icon: 'bi-journal-richtext', tagline: 'Lecture slides, notes & quiz synthesis', description: 'Long-context reading of course documents — summaries, revision guides and quiz generation.', powered_by: ['Google', 'OpenRouter', 'Groq'], is_configured: true, rate_limited: false, cooldown_remaining: 0, fallbacks_ready: 2 },
                { slug: 'vision', name: 'Spark Vision', icon: 'bi-eye', tagline: 'Images, OCR and whiteboard math', description: 'Reads uploaded images — timetables, circuit diagrams and handwritten working.', powered_by: ['Google', 'Groq'], is_configured: true, rate_limited: false, cooldown_remaining: 0, fallbacks_ready: 1 },
            ];
        }

        selectEngine(engine, display) {
            this.selectedEngine = engine || '';
            this.selectedEngineLabel = this.selectedEngine ? (display || 'Spark') : '';

            if (this.selectedEngine) {
                localStorage.setItem('pwanimate_selected_engine', this.selectedEngine);
                localStorage.setItem('pwanimate_selected_engine_label', this.selectedEngineLabel);
            } else {
                localStorage.removeItem('pwanimate_selected_engine');
                localStorage.removeItem('pwanimate_selected_engine_label');
            }

            // Dismiss dropdown menu if open
            const dropdownBtn = this.workspace ? this.workspace.querySelector('#pwanimateModelDropdownBtn') : null;
            if (dropdownBtn && window.bootstrap && typeof window.bootstrap.Dropdown?.getInstance === 'function') {
                const inst = window.bootstrap.Dropdown.getInstance(dropdownBtn);
                if (inst) inst.hide();
            }

            this.updateSelectedModelUI();
            if (this.cachedQuotaStatus) {
                this.updateQuotaStatus(this.cachedQuotaStatus);
            } else {
                this.updateQuotaStatus({ engines: this.getDefaultEngines() });
            }
        }

        updateSelectedModelUI() {
            if (this.selectedModelLabel) {
                if (this.selectedEngine) {
                    this.selectedModelLabel.textContent = this.selectedEngineLabel || 'Spark';
                    this.selectedModelLabel.title = `Engine: ${this.selectedEngineLabel || this.selectedEngine}`;
                } else {
                    this.selectedModelLabel.textContent = 'Auto';
                    this.selectedModelLabel.title = 'Auto routing: Failover across available models';
                }
            }
            this.updateDropdownStatusDot();
        }

        updateDropdownStatusDot() {
            if (!this.dropdownStatusDot) return;

            this.dropdownStatusDot.classList.remove('healthy', 'rate-limited', 'cooldown');

            const engines = (this.cachedQuotaStatus && Array.isArray(this.cachedQuotaStatus.engines))
                ? this.cachedQuotaStatus.engines : [];
            if (!engines.length) {
                this.dropdownStatusDot.classList.add('healthy');
                return;
            }

            if (this.selectedEngine) {
                const found = engines.find((e) => e.slug === this.selectedEngine);
                if (found && (!found.is_configured || found.rate_limited)) {
                    if (!found.is_configured || found.cooldown_remaining > 0) {
                        this.dropdownStatusDot.classList.add('rate-limited');
                    } else {
                        this.dropdownStatusDot.classList.add('cooldown');
                    }
                } else {
                    this.dropdownStatusDot.classList.add('healthy');
                }
                return;
            }

            const allRateLimited = engines.every((e) => e.rate_limited);
            const primary = engines[0];
            if (allRateLimited) {
                this.dropdownStatusDot.classList.add('rate-limited');
            } else if (primary && primary.rate_limited) {
                this.dropdownStatusDot.classList.add('cooldown');
            } else {
                this.dropdownStatusDot.classList.add('healthy');
            }
        }

        getEngineStatusPresentation(item) {
            if (!item.is_configured) {
                return {
                    dotClass: 'rate-limited',
                    statusText: 'Unavailable',
                    badge: '<span class="pwanimate-status-badge rate-limited"><i data-lucide="octagon-alert"></i> Unavailable</span>',
                };
            }
            // primary_status is server-computed: WHY the primary candidate is or
            // isn't usable — a missing API key is not the same as a rate limit.
            const primaryStatus = item.primary_status || (item.rate_limited ? 'rate_limited' : 'ready');
            if (primaryStatus === 'ready') {
                return {
                    dotClass: 'healthy',
                    statusText: 'Ready',
                    badge: '<span class="pwanimate-status-badge healthy"><i data-lucide="circle-check-big"></i> Operational</span>',
                };
            }
            if (item.cooldown_remaining > 0) {
                // Live quota cooldown tracked from real 429s in this process.
                return {
                    dotClass: 'rate-limited',
                    statusText: `Rate limited (${Math.ceil(item.cooldown_remaining)}s)`,
                    badge: `<span class="pwanimate-status-badge rate-limited"><i data-lucide="hourglass"></i> Rate Limited (${Math.ceil(item.cooldown_remaining)}s)</span>`,
                };
            }
            if (primaryStatus === 'unconfigured') {
                return {
                    dotClass: 'cooldown',
                    statusText: 'Backup active — primary provider not configured',
                    badge: '<span class="pwanimate-status-badge cooldown"><i data-lucide="plug"></i> Primary Not Configured</span>',
                };
            }
            if (item.fallbacks_ready > 0) {
                return {
                    dotClass: 'cooldown',
                    statusText: 'Busy — backup routing active',
                    badge: '<span class="pwanimate-status-badge cooldown"><i data-lucide="refresh-cw"></i> Backup Active</span>',
                };
            }
            return {
                dotClass: 'cooldown',
                statusText: 'Recovering',
                badge: '<span class="pwanimate-status-badge cooldown"><i data-lucide="refresh-cw"></i> Recovering</span>',
            };
        }

        updateQuotaStatus(quotaStatus) {
            if (!quotaStatus) return;
            this.cachedQuotaStatus = quotaStatus;

            if (this.quotaStatusEl) {
                this.quotaStatusEl.innerHTML = '';
            }

            const engines = Array.isArray(quotaStatus.engines)
                ? quotaStatus.engines
                : this.getDefaultEngines();
            const isAutoSelected = !this.selectedEngine;

            // 1. Update Auto item in dropdown menu
            const autoItem = this.workspace ? this.workspace.querySelector('.pwanimate-model-option[data-model=""]') : null;
            if (autoItem) {
                if (isAutoSelected) {
                    autoItem.classList.add('active');
                    if (!autoItem.querySelector('.pwanimate-check-icon')) {
                        const icon = document.createElement('i');
                        icon.setAttribute('data-lucide', 'check');
                        icon.className = 'text-primary pwanimate-check-icon';
                        autoItem.appendChild(icon);
                        sparkRenderIcons(autoItem);
                    }
                } else {
                    autoItem.classList.remove('active');
                    const icon = autoItem.querySelector('.pwanimate-check-icon');
                    if (icon) icon.remove();
                }
            }

            // 2. Populate dropdown with Spark engines
            if (this.dropdownModelsList && engines.length > 0) {
                let dropdownHtml = '';
                for (const item of engines) {
                    const isSelected = (this.selectedEngine === item.slug);
                    const st = this.getEngineStatusPresentation(item);

                    dropdownHtml += `
                        <li>
                            <button class="dropdown-item d-flex align-items-center justify-content-between pwanimate-model-option ${isSelected ? 'active' : ''}"
                                    data-engine="${escapeHtml(item.slug)}"
                                    data-display="${escapeHtml(item.name)}"
                                    type="button"
                                    ${!item.is_configured ? 'disabled' : ''}>
                                <div class="pe-2">
                                    <div class="d-flex align-items-center gap-1">
                                        <span class="pwanimate-model-status-dot ${st.dotClass}"></span>
                                        <span class="fw-semibold small">${escapeHtml(item.name)}</span>
                                    </div>
                                    <div class="text-muted" style="font-size: 0.72rem;">${escapeHtml(st.statusText)}</div>
                                </div>
                                ${isSelected ? '<i data-lucide="check" class="text-primary pwanimate-check-icon"></i>' : ''}
                            </button>
                        </li>
                    `;
                }
                this.dropdownModelsList.innerHTML = dropdownHtml;
            }

            // 3. Populate Modal container
            if (this.modalModelsContainer) {
                let modalHtml = '';

                // Auto routing card
                modalHtml += `
                    <div class="pwanimate-quota-card ${isAutoSelected ? 'selected' : ''}">
                        <div class="d-flex align-items-center justify-content-between mb-2">
                            <div class="d-flex align-items-center gap-2">
                                <span class="pwanimate-model-status-dot healthy"></span>
                                <span class="fw-bold fs-6">Auto (Recommended)</span>
                                <span class="badge bg-primary-subtle text-primary" style="font-size: 0.7rem;">Smart Failover</span>
                            </div>
                            <span class="pwanimate-status-badge healthy">
                                <i data-lucide="shield-check"></i> Multi-tier Active
                            </span>
                        </div>
                        <p class="text-muted small mb-3">
                            Automatically routes queries to the fastest available model and fails over without downtime if a provider is rate-limited.
                        </p>
                        <div class="d-flex align-items-center justify-content-between">
                            <span class="text-muted small" style="font-size: 0.75rem;">Default production failover</span>
                            ${isAutoSelected ? `
                                <button type="button" class="btn btn-sm btn-outline-primary active disabled" style="font-size: 0.8rem;">
                                    <i data-lucide="check" class="me-1"></i>Active
                                </button>
                            ` : `
                                <button type="button" class="btn btn-sm btn-outline-primary pwanimate-modal-select-btn"
                                        data-provider=""
                                        data-model=""
                                        data-display="Auto"
                                        data-provider-label=""
                                        style="font-size: 0.8rem;">
                                    Switch to Auto
                                </button>
                            `}
                        </div>
                    </div>
                `;

                // Spark engine cards
                for (const item of engines) {
                    const isSelected = (this.selectedEngine === item.slug);
                    const st = this.getEngineStatusPresentation(item);

                    modalHtml += `
                        <div class="pwanimate-quota-card ${isSelected ? 'selected' : ''}">
                            <div class="d-flex align-items-center justify-content-between mb-2">
                                <div class="d-flex align-items-center gap-2">
                                    <span class="pwanimate-model-status-dot ${st.dotClass}"></span>
                                    <span class="fw-bold fs-6">${escapeHtml(item.name)}</span>
                                    <span class="badge bg-secondary-subtle text-secondary" style="font-size: 0.7rem;">${escapeHtml((item.powered_by || []).join(' · '))}</span>
                                </div>
                                ${st.badge}
                            </div>
                            <p class="text-muted small mb-3">
                                ${escapeHtml(item.description || item.tagline || '')}
                            </p>
                            <div class="d-flex align-items-center justify-content-between">
                                <span class="text-muted" style="font-size: 0.75rem;">${escapeHtml(item.tagline || '')}</span>
                                ${isSelected ? `
                                    <button type="button" class="btn btn-sm btn-outline-primary active disabled" style="font-size: 0.8rem;">
                                        <i data-lucide="check" class="me-1"></i>Active
                                    </button>
                                ` : `
                                    <button type="button" class="btn btn-sm btn-primary pwanimate-modal-select-btn"
                                            data-engine="${escapeHtml(item.slug)}"
                                            data-display="${escapeHtml(item.name)}"
                                            ${!item.is_configured ? 'disabled' : ''}
                                            style="font-size: 0.8rem;">
                                        Select Engine
                                    </button>
                                `}
                            </div>
                        </div>
                    `;
                }
                this.modalModelsContainer.innerHTML = modalHtml;
            }

            this.updateDropdownStatusDot();
        }

        updateActiveModelChip(quotaInfo, servingEngine = null) {
            const chip = document.getElementById('pwanimate-active-model-chip');
            const nameEl = document.getElementById('pwanimate-active-model-name');
            // Brand-level attribution only: the chip and header credit an
            // engine (Speed/Coda/Scholar/Vision) — raw provider/model names
            // are never surfaced (Build ③ privacy rule).
            const engineShortName = servingEngine
                ? String(servingEngine).replace(/^Spark\s+/i, '')
                : null;
            if (chip && nameEl && quotaInfo) {
                if (this.selectedEngine) {
                    nameEl.textContent = this.selectedEngineLabel || 'Spark';
                    if (quotaInfo.switched) {
                        chip.classList.add('switched');
                        chip.title = `${this.selectedEngineLabel || 'Spark'} busy — backup routing active`;
                    } else {
                        chip.classList.remove('switched');
                        chip.title = `${this.selectedEngineLabel || 'Spark'} ready`;
                    }
                } else {
                    nameEl.textContent = engineShortName || 'Spark';
                    if (quotaInfo.switched) {
                        chip.classList.add('switched');
                        chip.title = 'Busy — backup routing active';
                    } else {
                        chip.classList.remove('switched');
                        chip.title = 'Smart routing active';
                    }
                }
            }

            if (!this.selectedEngine && quotaInfo && this.selectedModelLabel) {
                if (engineShortName) {
                    this.selectedModelLabel.textContent = `Auto (${engineShortName})`;
                    this.selectedModelLabel.title = quotaInfo.switched
                        ? 'Auto routing: backup engine active'
                        : 'Auto routing: engine selected automatically';
                } else {
                    this.selectedModelLabel.textContent = 'Auto';
                    this.selectedModelLabel.title = 'Auto routing: engine selected automatically';
                }
            }
        }
    }

    function initSpark() {
        const workspace = document.getElementById('pwanimate-workspace');
        if (!workspace) {
            if (window._activeSparkChat) {
                window._activeSparkChat.destroy();
                window._activeSparkChat = null;
            }
            document.documentElement.classList.remove('pwanimate-active');
            document.body.classList.remove('pwanimate-active');
            return;
        }
        document.documentElement.classList.add('pwanimate-active');
        document.body.classList.add('pwanimate-active');
        // Guard against double-init on the *same* workspace node (e.g. DOMContentLoaded
        // firing alongside an htmx:afterSwap). We intentionally do NOT block re-init
        // when the workspace element itself has changed (HTMX navigation between conversations),
        // which is why we compare by reference rather than a data-initialized attribute.
        if (window._activeSparkChat && window._activeSparkChat.workspace === workspace) {
            window._activeSparkChat.initWorkspaceGeometry();
            return;
        }
        if (window._activeSparkChat) {
            window._activeSparkChat.destroy();
            window._activeSparkChat = null;
        }
        window._activeSparkChat = new SparkChat(workspace);
    }

    window.initSpark = initSpark;

    window.sparkWorkspace = {
        toggleContextRail: () => {
            if (window._activeSparkChat) {
                window._activeSparkChat.toggleContextRail();
            } else {
                const workspace = document.getElementById('pwanimate-workspace');
                if (workspace) workspace.classList.toggle('pwanimate-context-open');
            }
        },
        openContextRail: () => {
            if (window._activeSparkChat) {
                window._activeSparkChat.openContextRail();
            } else {
                const workspace = document.getElementById('pwanimate-workspace');
                if (workspace) workspace.classList.add('pwanimate-context-open');
            }
        },
        closeContextRail: () => {
            if (window._activeSparkChat) {
                window._activeSparkChat.closeContextRail();
            } else {
                const workspace = document.getElementById('pwanimate-workspace');
                if (workspace) workspace.classList.remove('pwanimate-context-open');
            }
        },
        switchWorkspaceTab: (tab) => {
            if (window._activeSparkChat) {
                window._activeSparkChat.switchWorkspaceTab(tab);
            }
        },
        toggleLeftRail: (collapse) => {
            if (window._activeSparkChat) {
                window._activeSparkChat.toggleLeftRail(collapse);
            } else {
                const workspace = document.getElementById('pwanimate-workspace');
                if (workspace) {
                    if (collapse) workspace.classList.add('pwanimate-left-collapsed');
                    else workspace.classList.remove('pwanimate-left-collapsed');
                }
            }
        }
    };

    window.handleDeleteSparkConversation = function (convId) {
        if (!convId) return;
        if (window._activeSparkChat && typeof window._activeSparkChat.handleDeleteConversation === 'function') {
            window._activeSparkChat.handleDeleteConversation(convId);
        } else {
            const activeConvId = document.getElementById('pwanimate-workspace')?.dataset?.conversationId || null;
            SparkChat.prototype.handleDeleteConversation.call({ conversationId: activeConvId }, convId);
        }
    };

    window.switchSparkSettingsTab = function (scope, tabName) {
        if (!tabName) return;
        const isModal = scope === 'modal';
        const tabsContainerId = isModal ? 'pwanimate-modal-tabs' : 'pwanimate-mobile-tabs';
        const panePrefix = isModal ? 'pwanimate-modal-pane-' : 'pwanimate-mobile-pane-';
        const targetPaneId = panePrefix + tabName;

        // 1. Update tab button active states
        const tabsContainer = document.getElementById(tabsContainerId);
        if (tabsContainer) {
            tabsContainer.querySelectorAll('.nav-link').forEach(function (btn) {
                const btnTab = btn.getAttribute('data-tab') || (btn.id ? btn.id.replace(/^(modal|mobile)-tab-/, '') : '');
                if (btnTab === tabName) {
                    btn.classList.add('active');
                } else {
                    btn.classList.remove('active');
                }
            });
        }

        // 2. Switch content pane
        const targetPane = document.getElementById(targetPaneId);
        if (targetPane) {
            const parent = targetPane.parentElement;
            if (parent) {
                const panes = parent.querySelectorAll('.pwanimate-settings-pane');
                panes.forEach(function (pane) {
                    if (pane === targetPane) {
                        pane.classList.remove('d-none');
                        requestAnimationFrame(function () {
                            pane.classList.add('active');
                        });
                    } else {
                        pane.classList.remove('active');
                        pane.classList.add('d-none');
                    }
                });
            }
            requestAnimationFrame(() => window.initSparkStudySettings?.(targetPane));
            // Scroll to top of scroll container
            if (isModal) {
                const modalBody = document.getElementById('pwanimate-settings-modal-body');
                if (modalBody) modalBody.scrollTop = 0;
            } else {
                const scrollContainer = document.querySelector('.pwanimate-settings-mobile') || document.getElementById('pwanimate-workspace');
                if (scrollContainer) scrollContainer.scrollTop = 0;
            }
        } else {
            // Graceful fallback for HTMX dynamic swap if panes weren't preloaded
            const urlMap = {
                personalization: '/pwanimate/settings/personalization/',
                memory: '/pwanimate/settings/memory/',
                plans: '/pwanimate/settings/plans/',
                study: '/pwanimate/settings/study/',
                usage: '/pwanimate/settings/usage/',
                about: '/pwanimate/settings/about/'
            };
            const targetId = isModal ? '#pwanimate-settings-modal-body' : '#pwanimate-settings-mobile-content';
            if (window.htmx && urlMap[tabName]) {
                window.htmx.ajax('GET', urlMap[tabName], { target: targetId, swap: 'innerHTML' });
            }
        }
    };

    // Opens the existing offline document library. On desktop the settings
    // modal is dismissed first so the library modal does not stack backdrops.
    window.openSparkOfflineSettings = function (scope) {
        const isModal = scope === 'modal';
        if (isModal) {
            const modalEl = document.getElementById('pwanimateSettingsModal');
            const inst = modalEl && window.bootstrap && window.bootstrap.Modal.getInstance(modalEl);
            if (inst) inst.hide();
        }
        setTimeout(function () {
            if (window.PwaniOfflineLibrary && typeof window.PwaniOfflineLibrary.open === 'function') {
                window.PwaniOfflineLibrary.open();
            }
        }, isModal ? 300 : 0);
    };

    // Mobile offcanvas embedded panels (Settings / Study Sessions). Loading a
    // panel inside the sheet keeps the chat workspace mounted, so the active
    // conversation, scroll position and any in-flight generation survive.
    window.sparkOpenOffcanvasPanel = function (url) {
        var panel = document.getElementById('pwanimate-offcanvas-panel');
        var menu = document.getElementById('pwanimate-offcanvas-menu');
        if (!panel || !window.htmx) return;
        panel.classList.remove('d-none');
        panel.classList.add('d-flex');
        if (menu) menu.classList.add('d-none');
        window.htmx.ajax('GET', url, { target: '#pwanimate-offcanvas-panel', swap: 'innerHTML' });
    };

    window.sparkCloseOffcanvasPanel = function () {
        var panel = document.getElementById('pwanimate-offcanvas-panel');
        var menu = document.getElementById('pwanimate-offcanvas-menu');
        if (panel) {
            panel.classList.add('d-none');
            panel.classList.remove('d-flex');
            panel.innerHTML = '';
        }
        if (menu) menu.classList.remove('d-none');
    };

    // Reset to the menu pane whenever the sheet is dismissed. Delegated on
    // document because the offcanvas node is replaced on page-content swaps.
    document.addEventListener('hidden.bs.offcanvas', function (e) {
        if (e.target && e.target.id === 'pwanimateMobileMenu') window.sparkCloseOffcanvasPanel();
    });

    window.setSparkModalActiveTab = function (el) {
        if (!el) return;
        const tab = el.getAttribute('data-tab') || (el.id ? el.id.replace('modal-tab-', '') : 'personalization');
        window.switchSparkSettingsTab('modal', tab);
    };

    window.setSparkMobileActiveTab = function (el) {
        if (!el) return;
        const tab = el.getAttribute('data-tab') || (el.id ? el.id.replace('mobile-tab-', '') : 'personalization');
        window.switchSparkSettingsTab('mobile', tab);
    };

    // Global listener to sync choice card selections with radio button state
    document.addEventListener('change', function (e) {
        if (e.target && e.target.type === 'radio' && e.target.closest && e.target.closest('.pwanimate-choice-card')) {
            const form = e.target.closest('form');
            if (form) {
                form.querySelectorAll('.pwanimate-choice-card').forEach(function (card) {
                    const r = card.querySelector('input[type="radio"]');
                    if (r && r.checked) {
                        card.classList.add('selected');
                    } else {
                        card.classList.remove('selected');
                    }
                });
            }
        }
    });

    // Ensure modal shows active pane when opened
    document.addEventListener('show.bs.modal', function (e) {
        if (e.target && e.target.id === 'pwanimateSettingsModal') {
            const activeModalTab = e.target.querySelector('#pwanimate-modal-tabs .nav-link.active');
            const tabName = activeModalTab ? activeModalTab.getAttribute('data-tab') : 'personalization';
            window.switchSparkSettingsTab('modal', tabName || 'personalization');
        }
    });

    function cleanupSparkModals() {
        const modalEl = document.getElementById('pwanimateSettingsModal');
        if (modalEl && window.bootstrap && typeof window.bootstrap.Modal?.getInstance === 'function') {
            const inst = window.bootstrap.Modal.getInstance(modalEl);
            if (inst) {
                inst.hide();
            }
        }
        document.querySelectorAll('.modal-backdrop').forEach(bd => bd.remove());
        document.body.classList.remove('modal-open');
        document.body.style.removeProperty('overflow');
        document.body.style.removeProperty('padding-right');
    }

    // Attach to lifecycle
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initSpark);
    } else {
        initSpark();
    }

    if (!window._pwanimateSwapListenerAttached) {
        window._pwanimateSwapListenerAttached = true;
        document.body.addEventListener('htmx:beforeSwap', function (evt) {
            if (evt.detail && evt.detail.target && evt.detail.target.id === 'page-content-target') {
                if (window._activeSparkChat) {
                    window._activeSparkChat.destroy();
                    window._activeSparkChat = null;
                }
                cleanupSparkModals();
                if (!evt.detail.xhr || !evt.detail.xhr.responseText.includes('pwanimate-workspace')) {
                    document.documentElement.classList.remove('pwanimate-active');
                    document.body.classList.remove('pwanimate-active');
                }
            }
        });

        document.body.addEventListener('htmx:afterSwap', function (evt) {
            if (document.getElementById('pwanimate-workspace')) {
                initSpark();
            } else {
                document.documentElement.classList.remove('pwanimate-active');
                document.body.classList.remove('pwanimate-active');
            }
            if (evt.detail && evt.detail.target) {
                sparkRenderIcons(evt.detail.target);
            }
        });
    }
})();
