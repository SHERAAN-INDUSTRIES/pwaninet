/* Public task progress, scoped approval cards, and the planner's stated thinking.
 *
 * Activity lifecycle (deterministic, backend-event driven):
 *  - Live trails open while the agent works and collapse when the final
 *    answer starts streaming or the run finalizes.
 *  - Hydrated/history trails always start collapsed; a run that is still
 *    executing re-opens its trail while polling the journal.
 *  - A student's manual toggle is remembered per trail and never overridden
 *    by ordinary events; a pending APPROVAL forces the trail open because
 *    its controls live inside it. Clarification questions render as compact
 *    records here — the actionable form lives in the floating block above
 *    the composer (pwanimate-chat.js).
 */
(() => {
    if (window.SparkActivity) return;
    const make = (tag, cls, text) => {
        const el = document.createElement(tag);
        if (cls) el.className = cls;
        if (text !== undefined) el.textContent = text;
        return el;
    };
    const CHECKLIST_STATUS = {pending:'pending', doing:'doing', running:'doing', done:'done', completed:'done',
        blocked:'blocked', failed:'failed', skipped:'skipped', cancelled:'cancelled'};

    // Small markdown summaries under each step, styled subtle like the agent
    // traces students know. Falls back to plain text when marked/DOMPurify
    // are not loaded on the page.
    const md = (el, text) => {
        if (!text) return;
        if (window.marked && window.DOMPurify) {
            try {
                el.innerHTML = window.DOMPurify.sanitize(window.marked.parse(String(text)));
                return;
            } catch (_) {}
        }
        el.textContent = String(text);
    };

    function conversationKey() {
        const ws = document.getElementById('pwanimate-workspace');
        const id = ws && ws.dataset ? (ws.dataset.conversationId || 'new') : 'new';
        return 'spark-activity-expanded:' + id;
    }
    // Per-trail manual open/close choices, keyed by run id (or first event id
    // for legacy trails). Survives refresh without forcing every panel open.
    function manualKey() { return 'spark-activity-manual:' + conversationKey(); }
    function readManual() {
        try { return JSON.parse(localStorage.getItem(manualKey()) || '{}'); } catch (_) { return {}; }
    }
    function writeManual(map) {
        try { localStorage.setItem(manualKey(), JSON.stringify(map)); } catch (_) {}
    }

    // Canonical semantic activity types emitted by the backend as
    // `activity_type`. The icon describes WHAT Spark is doing; the outcome
    // is carried separately by data-status styling — a failed web search
    // stays a red globe, never a generic file icon.
    const ACTIVITY_ICONS = {
        thinking: 'brain',
        checklist: 'list-checks',
        web_search: 'globe',
        document_read: 'book-open',
        document_search: 'book-search',
        content_search: 'search',
        tool_call: 'wrench',
        plan_revision: 'clipboard-check',
        verification: 'shield-check',
        clarification: 'message-circle-question',
        response_draft: 'file-pen-line',
        retry: 'refresh-cw',
    };
    // Deterministic fallback for events persisted before `activity_type`
    // existed: derive the same semantic type from kind/tool/stage fields.
    const LEGACY_KIND_TYPES = {
        thinking: 'thinking', plan: 'thinking', say: 'thinking',
        checklist: 'checklist', clarification: 'clarification',
        checklist_review: 'plan_revision', verify: 'verification', approval: 'verification',
    };
    const LEGACY_TOOL_TYPES = {
        web_search: 'web_search', retrieve_documents: 'document_read',
        academic_lookup: 'document_read', document_detail: 'document_read',
        student_timetable: 'document_read', content_search: 'content_search',
    };
    const LEGACY_STAGE_TYPES = {
        thinking: 'thinking', searching_web: 'web_search',
        reading_sources: 'document_read', devouring_context: 'document_read',
        searching_documents: 'document_read', searching_posts: 'content_search',
        writing_answer: 'response_draft',
    };
    const STATUS_ICONS = {
        completed: 'check-circle-2', blocked: 'circle-alert', failed: 'circle-x',
        cancelled: 'ban', waiting: 'message-circle-question', retrying: 'refresh-cw',
    };
    function legacyActivityType(event) {
        const kind = event.kind || '';
        if (kind === 'status') return null; // outcome markers handled separately
        if (kind === 'tool') return LEGACY_TOOL_TYPES[event.tool] || 'tool_call';
        if (kind === 'stage') {
            const id = String(event.id || '');
            const stage = id.startsWith('stage-') ? id.slice(6) : '';
            return LEGACY_STAGE_TYPES[stage] || null;
        }
        return LEGACY_KIND_TYPES[kind] || null;
    }
    function iconFor(event) {
        const kind = event.kind || '';
        // Dedicated lifecycle/status markers describe outcomes, not work.
        if (kind === 'status') return STATUS_ICONS[event.status] || 'refresh-cw';
        const activityType = event.activity_type || legacyActivityType(event);
        return ACTIVITY_ICONS[activityType] || 'circle-dot';
    }

    class Trail {
        constructor(live = true) {
            this.element = make('details', 'spark-activity');
            // Live trails open while work happens; hydrated trails start
            // collapsed unless the caller re-opens them (active run watch).
            this.element.open = Boolean(live);
            this.mode = live ? 'live' : 'history';
            this.userOpen = null;      // null = no explicit choice
            this.persistKey = null;    // resolved lazily from run/event ids
            this.summary = make('summary', 'spark-activity-summary');
            this.title = make('span', 'spark-activity-title', 'Working on your request');
            this.checkpoint = make('span', 'spark-activity-checkpoint');
            this.count = make('span', 'spark-activity-count');
            this.summary.append(this.title, this.checkpoint, this.count);
            this.checklistBox = make('div', 'spark-checklist');
            this.checklistBox.hidden = true;
            this.list = make('ol', 'spark-activity-list');
            this.live = make('span', 'visually-hidden');
            this.live.setAttribute('role', 'status');
            this.live.setAttribute('aria-live', 'polite');
            this.element.append(this.summary, this.checklistBox, this.list, this.live);
            this.entries = new Map();
            this.thinkingIds = new Set();
            this.lastThinking = '';
            this.thinkingBox = null;
            this.lastCheckpoint = '';
            this.createdAt = Date.now();
            this.startedAt = null;
            this.lastAt = null;
            this.waitingApproval = false;
            this.element.addEventListener('toggle', () => {
                // 'toggle' fires after the browser flips open; record the
                // student's explicit choice so events never fight it.
                this.userOpen = this.element.open;
                this._persistManual();
            });
        }
        _resolvePersistKey() {
            if (this.persistKey) return this.persistKey;
            for (const row of this.entries.values()) {
                if (row.dataset.runId) { this.persistKey = 'run:' + row.dataset.runId; return this.persistKey; }
            }
            const first = this.entries.keys().next();
            if (!first.done) { this.persistKey = 'evt:' + first.value; return this.persistKey; }
            return null;
        }
        _persistManual() {
            const key = this._resolvePersistKey();
            if (!key || this.userOpen === null) return;
            const map = readManual();
            map[key] = this.userOpen;
            writeManual(map);
        }
        restoreManual() {
            const key = this._resolvePersistKey();
            if (!key || this.waitingApproval) return; // approvals must stay visible
            const saved = readManual()[key];
            if (saved === true || saved === false) {
                this.userOpen = saved;
                this.element.open = saved;
            }
        }
        // A run observed live again after hydration (reload while running):
        // the trail becomes the current activity section and re-opens.
        resumeLive() {
            this.mode = 'live';
            if (this.userOpen === null) this.element.open = true;
        }
        add(event) {
            if (!event || typeof event.text !== 'string' || !event.id) return;
            const stamp = event.at ? Date.parse(event.at) : NaN;
            if (!isNaN(stamp)) {
                if (this.startedAt === null || stamp < this.startedAt) this.startedAt = stamp;
                if (this.lastAt === null || stamp > this.lastAt) this.lastAt = stamp;
            }
            if (event.run_id) {
                // Keep the newest run id as the persistence key source.
                for (const row of this.entries.values()) row.dataset.runId = event.run_id;
            }
            if (event.kind === 'checklist') { this.renderChecklist(event); return; }
            if (event.kind === 'say') return; // spoken updates render as chat bubbles, not trail steps
            if (event.kind === 'thinking') { this.addThinking(event); return; }
            if (event.kind === 'plan' && event.text.trim()) this.lastCheckpoint = event.text.trim();
            this.checkpoint.textContent = this.lastCheckpoint;
            this.checkpoint.title = this.lastCheckpoint;
            let row = this.entries.get(event.id);
            if (!row) {
                row = make('li', 'spark-activity-step');
                if (event.kind === 'plan') row.classList.add('spark-activity-note');
                if (event.run_id) row.dataset.runId = event.run_id;
                const marker = make('span', 'spark-activity-marker');
                marker.appendChild(make('i', null));
                marker.firstChild.setAttribute('data-lucide', iconFor(event));
                row.append(marker, make('div', 'spark-activity-body'));
                row.dataset.eid = event.id;
                row.dataset.activityType = event.activity_type || '';
                const body = row.querySelector('.spark-activity-body');
                body.append(make('p', 'spark-activity-text'));
                this.entries.set(event.id, row);
                this.list.appendChild(row);
                if (window.sparkRenderIcons) window.sparkRenderIcons(row);
            }
            row.dataset.status = event.status || 'running';
            row.dataset.kind = event.kind || '';
            row.dataset.tool = event.tool || '';
            if (event.activity_type) row.dataset.activityType = event.activity_type;
            // Refresh the icon when status changes the category (e.g. a tool
            // finishing flips from wrench to check-circle-2).
            const iconEl = row.querySelector('.spark-activity-marker [data-lucide], .spark-activity-marker svg');
            if (iconEl) {
                const name = iconFor(event);
                if (iconEl.getAttribute('data-lucide') !== name) {
                    if (window.sparkSetIcon) window.sparkSetIcon(iconEl, name);
                    else iconEl.setAttribute('data-lucide', name);
                }
            }
            const textEl = row.querySelector('.spark-activity-text');
            if (event.kind === 'plan' || event.kind === 'verify') md(textEl, event.text); else textEl.textContent = event.text;
            let detailEl = row.querySelector('.spark-activity-detail');
            const detail = typeof event.detail === 'string' ? event.detail.trim() : '';
            const summary = typeof event.summary === 'string' ? event.summary.trim() : '';
            if (detail || summary) {
                if (!detailEl) {
                    detailEl = make('p', 'spark-activity-detail spark-activity-md');
                    textEl.after(detailEl);
                }
                md(detailEl, summary || detail);
            } else if (detailEl) {
                detailEl.remove();
            }
            if (event.kind === 'approval' && Array.isArray(event.actions)) this.approvals(row, event);
            if (event.kind === 'clarification' && event.run_id) this.clarification(row, event);
            this.count.textContent = String(this.entries.size);
            this.live.textContent = event.text;
            this.title.textContent = event.status === 'waiting' ? 'Waiting for you' : 'Working on your request';
            // Approvals keep their controls inside the trail, so a pending
            // approval must stay visible. Clarifications now live in the
            // floating block above the composer and may collapse freely.
            if (event.status === 'waiting' && event.kind === 'approval') {
                this.waitingApproval = true;
                this.element.open = true;
            }
        }
        addThinking(event) {
            if (this.thinkingIds.has(event.id)) return;
            this.thinkingIds.add(event.id);
            const text = String(event.text || '').trim();
            // Skip near-identical consecutive updates; the planner can repeat
            // itself across rounds and the section should stay readable.
            if (text && text === this.lastThinking) return;
            if (text) this.lastThinking = text;
            if (!this.thinkingBox) {
                const box = make('details', 'spark-thinking');
                const summary = make('summary', 'spark-thinking-summary');
                const brain = make('i', 'spark-thinking-icon');
                brain.setAttribute('data-lucide', 'brain');
                summary.append(brain, document.createTextNode('Spark’s thinking'));
                box.appendChild(summary);
                const body = make('div', 'spark-thinking-body');
                box.appendChild(body);
                this.element.insertBefore(box, this.list);
                this.thinkingBox = box;
                if (window.sparkRenderIcons) window.sparkRenderIcons(box);
                if (this.mode === 'live' && this.userOpen === null) this.thinkingBox.open = true;
            }
            const line = make('p', 'spark-thinking-line');
            md(line, event.text);
            this.thinkingBox.querySelector('.spark-thinking-body').appendChild(line);
        }
        renderChecklist(event) {
            const items = Array.isArray(event.items) ? event.items : [];
            if (!items.length) { this.checklistBox.hidden = true; return; }
            this.checklistBox.hidden = false;
            this.checklistBox.dataset.state = event.status || 'running';
            this.checklistBox.replaceChildren();
            const title = make('p', 'spark-checklist-title');
            const listIcon = make('i', 'spark-checklist-icon');
            listIcon.setAttribute('data-lucide', 'list-checks');
            title.append(listIcon, document.createTextNode('Checklist'));
            this.checklistBox.appendChild(title);
            const list = make('ul', 'spark-checklist-list');
            for (const item of items) {
                const status = CHECKLIST_STATUS[item && item.status] || 'pending';
                const li = make('li', 'spark-checklist-item spark-checklist-' + status);
                li.appendChild(make('span', 'spark-checklist-marker'));
                const body = make('div', 'spark-checklist-body');
                body.appendChild(make('span', 'spark-checklist-label', String(item.label || '')));
                const finding = typeof item.finding === 'string' ? item.finding.trim() : '';
                if (finding) body.appendChild(make('span', 'spark-checklist-finding', finding));
                li.appendChild(body);
                list.appendChild(li);
            }
            this.checklistBox.appendChild(list);
            if (window.sparkRenderIcons) window.sparkRenderIcons(this.checklistBox);
            if (event.status && event.status !== 'running') {
                const done = items.filter(i => i.status === 'done').length;
                this.live.textContent = `Checklist: ${done} of ${items.length} complete`;
            }
        }
        // Compact, non-interactive record. The actionable question lives in
        // the floating block above the composer — the trail only narrates.
        clarification(row, event) {
            const previous = row.querySelector('.spark-clarification');
            if (previous) previous.remove();
            const chat = window._activeSparkChat;
            const resolved = Boolean(chat && chat.resolvedActivityEvents && chat.resolvedActivityEvents.has(event.id))
                || event.status === 'answered';
            const waiting = event.status === 'waiting' && !resolved;
            const box = make('div', 'spark-clarification' + (resolved || !waiting ? ' spark-clarification-resolved' : ''));
            box.dataset.runId = event.run_id;
            box.appendChild(make('p', 'spark-clarification-text', event.text || 'Spark has a question.'));
            const note = make('p', 'spark-clarification-note',
                resolved ? 'Answered.'
                    : waiting ? 'Waiting for your answer — reply in the box above the composer.'
                    : '');
            if (note.textContent) box.appendChild(note);
            row.appendChild(box);
        }
        approvals(row, event) {
            const previous = row.querySelector('.spark-approval');
            if (previous) previous.remove();
            const chat = window._activeSparkChat;
            const resolved = Boolean(chat && chat.resolvedActivityEvents && chat.resolvedActivityEvents.has(event.id));
            const box = make('div', 'spark-approval');
            box.dataset.runId = event.run_id;
            const pending = [];
            for (const action of event.actions) {
                const card = make('div', 'spark-approval-card');
                card.appendChild(make('p', '', action.summary || 'Review the proposed action.'));
                if (action.status === 'pending' && resolved) {
                    card.appendChild(make('small', 'text-muted', 'Decision recorded.'));
                } else if (action.status === 'pending') {
                    pending.push(String(action.id));
                    const controls = make('div', 'spark-approval-controls');
                    controls.append(this.button('Approve', 'confirm', [String(action.id)], event.run_id, event.id),
                        this.button('Decline', 'cancel', [String(action.id)], event.run_id, event.id, true));
                    card.appendChild(controls);
                } else {
                    card.appendChild(make('small', 'text-muted', action.status === 'confirmed' ? 'Approved and completed' : action.status));
                }
                box.appendChild(card);
            }
            if (pending.length > 1) {
                const controls = make('div', 'spark-approval-controls');
                controls.append(this.button('Approve all shown', 'confirm_all', pending, event.run_id, event.id),
                    this.button('Decline all', 'cancel_all', pending, event.run_id, event.id, true));
                box.appendChild(controls);
            }
            if (pending.length) box.appendChild(make('p', 'spark-approval-scope', 'Approval applies only to the actions shown in this task.'));
            row.appendChild(box);
        }
        button(label, decision, ids, runId, eventId = null, secondary = false) {
            const button = make('button', 'btn btn-sm ' + (secondary ? 'btn-outline-secondary' : 'btn-primary'), label);
            button.type = 'button';
            button.addEventListener('click', () => {
                const chat = window._activeSparkChat;
                if (!chat || chat.isGenerating || typeof chat.resumeAgent !== 'function') return;
                const approval = button.closest('.spark-approval');
                approval.querySelectorAll('button').forEach(b => { b.disabled = true; });
                // resumeAgent re-arms the widget if the request fails; on success
                // the event id lands in resolvedActivityEvents so replays render
                // the recorded decision instead of live buttons.
                chat.resumeAgent(runId, decision, ids, this, eventId).catch(() => {
                    approval.querySelectorAll('button').forEach(b => { b.disabled = false; });
                });
            });
            return button;
        }
        finish(waiting = false, failed = false) {
            this.mode = 'done';
            this.element.dataset.state = waiting ? 'waiting' : failed ? 'failed' : 'completed';
            const start = this.startedAt ?? this.createdAt;
            const end = this.lastAt ?? Date.now();
            const seconds = Math.max(1, Math.round((end - start) / 1000));
            this.title.textContent = waiting ? 'Waiting for your decision'
                : failed ? 'Work interrupted'
                : `Devoured for ${seconds}s`;
            for (const row of this.entries.values()) {
                if (row.dataset.status === 'running') row.dataset.status = failed ? 'failed' : 'completed';
                // Refresh icons now that rows are terminal.
                const iconEl = row.querySelector('.spark-activity-marker [data-lucide], .spark-activity-marker svg');
                if (iconEl && window.sparkSetIcon) {
                    window.sparkSetIcon(iconEl, iconFor({
                        kind: row.dataset.kind, tool: row.dataset.tool,
                        activity_type: row.dataset.activityType || undefined,
                        status: row.dataset.status, id: row.dataset.eid || ''}));
                }
            }
            if (waiting && this.waitingApproval) {
                // Pending approvals keep their in-trail controls, so the trail
                // must stay open. Clarification waits collapse freely — the
                // actionable form lives in the block above the composer.
                this.element.open = true;
            } else if (this.userOpen === true) {
                // The student explicitly opened this trail; respect that.
            } else {
                // Terminal completion collapses the preparation phase; the
                // collapsed summary keeps the last checkpoint visible.
                this.element.open = false;
            }
        }
    }

    function hydrate(scope = document) {
        scope.querySelectorAll('.spark-activity-history').forEach(holder => {
            let events;
            try { events = JSON.parse(holder.querySelector('script').textContent); } catch (_) { return; }
            const trail = new Trail(false);
            events.forEach(e => trail.add(e));
            const approvals = events.filter(e => e.kind === 'approval' && e.run_id);
            trail.finish(approvals.length > 0);
            trail.restoreManual();
            holder.replaceWith(trail.element);
            const latest = approvals.at(-1);
            if (latest) fetch(`/api/pwanimate/agent-runs/${encodeURIComponent(latest.run_id)}/`, {credentials:'same-origin',cache:'no-store'})
                .then(r => r.ok ? r.json() : null).then(run => {
                    if (!run) return;
                    if (run.status === 'running') { watchAgentRun(run.id, trail); return; }
                    const expired = new Date(run.expires_at) <= new Date();
                    if (run.status === 'waiting' && !expired && window._activeSparkChat && typeof window._activeSparkChat.setWaitingForInput === 'function') {
                        window._activeSparkChat.setWaitingForInput(true, run.interaction || null, run.id);
                    }
                    const actions = run.actions.map(a => expired && a.status === 'pending' ? {...a,status:'expired'} : a);
                    trail.add({...latest, actions, text:expired ? 'This approval expired.' : run.status === 'waiting' ? latest.text : 'Your decisions are recorded.',status:run.status==='waiting' && !expired?'waiting':'completed'});
                    trail.finish(run.status==='waiting' && !expired);
                    trail.restoreManual();
                }).catch(() => { /* Owner-scoped approval endpoint still validates every decision. */ });
            const clar = events.filter(e => e.kind === 'clarification' && e.run_id).at(-1);
            if (clar) fetch(`/api/pwanimate/agent-runs/${encodeURIComponent(clar.run_id)}/`, {credentials:'same-origin',cache:'no-store'})
                .then(r => r.ok ? r.json() : null).then(run => {
                    if (!run) return;
                    const chat = window._activeSparkChat;
                    if (run.status === 'waiting' && chat && typeof chat.setWaitingForInput === 'function') {
                        chat.setWaitingForInput(true, run.interaction || null, run.id);
                        return;
                    }
                    if (run.status === 'running') { watchAgentRun(run.id, trail); return; }
                    if (run.status === 'waiting') return;
                    const box = trail.element.querySelector('.spark-clarification');
                    if (box) box.classList.add('spark-clarification-resolved');
                    if (chat && typeof chat._clearRunState === 'function') chat._clearRunState();
                }).catch(() => {});
        });
    }

    // A run that was still executing when the page reloaded keeps going in the
    // worker; replay the journal until it pauses or finishes, then reconcile
    // the UI with the authoritative run state.
    function watchAgentRun(runId, trail) {
        let after = 0;
        let stopped = false;
        trail.resumeLive();
        const finish = (status, run) => {
            stopped = true;
            clearInterval(timer);
            const chat = window._activeSparkChat;
            if (status === 'waiting') {
                if (chat && typeof chat.setWaitingForInput === 'function') {
                    chat.setWaitingForInput(true, (run && run.interaction) || null, runId);
                }
                // A waiting run may pause again on a NEW question; keep the
                // trail open only while approval controls are pending.
                trail.finish(true);
            } else if (status === 'completed') {
                trail.finish(false);
                // The answer persisted server-side; reload shows it in place.
                setTimeout(() => window.location.reload(), 800);
            } else {
                trail.finish(false, true);
            }
        };
        const tick = async () => {
            if (stopped) return;
            try {
                const r = await fetch(`/api/pwanimate/agent-runs/${encodeURIComponent(runId)}/events/?after=${after}&limit=200`,
                    {credentials:'same-origin',cache:'no-store'});
                if (!r.ok) { finish('failed'); return; }
                const data = await r.json();
                after = data.next_seq || after;
                (data.events || []).forEach(e => {
                    if (e && e.type === 'activity' && e.payload && e.payload.type === 'activity') {
                        trail.add(e.payload);
                    }
                });
                if (['completed','failed','cancelled','expired','interrupted','waiting'].includes(data.status)) {
                    let run = null;
                    try {
                        const rr = await fetch(`/api/pwanimate/agent-runs/${encodeURIComponent(runId)}/`,
                            {credentials:'same-origin',cache:'no-store'});
                        if (rr.ok) run = await rr.json();
                    } catch (_) {}
                    finish(data.status, run);
                }
            } catch (_) { /* transient network error — keep polling */ }
        };
        const timer = setInterval(tick, 2500);
        tick();
    }
    window.SparkActivity = {create:(live = true) => new Trail(live), hydrate};
    document.addEventListener('htmx:afterSwap', () => hydrate());
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => hydrate());
    else hydrate();
})();
