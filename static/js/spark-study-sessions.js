(() => {
  // Dedicated study-sessions surface: desktop modal + mobile settings-style page.
  // Renders unit-scoped sessions with roadmap coverage, tested mastery, quizzes,
  // and end-of-session packs. Answer keys never reach the client.
  const STATUS_META = {
    active:    { label: 'Active',    cls: 'status-active',    icon: 'play' },
    paused:    { label: 'Paused',    cls: 'status-paused',    icon: 'pause' },
    completed: { label: 'Completed', cls: 'status-completed', icon: 'circle-check' },
  };
  const MODE_LABEL = { immediate: 'Q&A', deferred: 'Questions first', mcq: 'Multiple choice' };

  const csrf = () => document.querySelector('meta[name="csrf-token"]')?.content
    || decodeURIComponent(document.cookie.match(/csrftoken=([^;]+)/)?.[1] || '');

  async function api(path, options = {}) {
    const response = await fetch('/api/pwanimate/' + path, {
      credentials: 'same-origin', cache: 'no-store', ...options,
      headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf(), ...(options.headers || {}) },
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(data.detail || data.error || 'Could not complete this change.');
    return data;
  }

  function el(tag, cls, text) {
    const node = document.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined && text !== null) node.textContent = text;
    return node;
  }

  // Lucide icon placeholder; rendered by spark-icons.js (observer/createIcons).
  function icon(value, cls) {
    const spec = window.sparkIconSpec ? window.sparkIconSpec(value) : { name: 'file', extra: '' };
    const node = document.createElement('i');
    node.setAttribute('data-lucide', spec.name);
    const all = [cls, spec.extra].filter(Boolean).join(' ');
    if (all) node.className = all;
    return node;
  }

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function timeAgo(iso) {
    if (!iso) return '';
    const then = new Date(iso).getTime();
    if (!then) return '';
    const mins = Math.max(0, Math.round((Date.now() - then) / 60000));
    if (mins < 1) return 'just now';
    if (mins < 60) return mins + 'm ago';
    const hours = Math.round(mins / 60);
    if (hours < 24) return hours + 'h ago';
    return Math.round(hours / 24) + 'd ago';
  }

  window.openSparkSessions = function () {
    const isMobile = !!(window.matchMedia && window.matchMedia('(max-width: 767.98px)').matches);
    if (isMobile) {
      window.location.href = '/pwanimate/sessions/';
      return;
    }
    const modal = document.getElementById('pwanimateSessionsModal');
    if (modal && window.bootstrap && window.bootstrap.Modal) {
      window.bootstrap.Modal.getOrCreateInstance(modal).show();
      return;
    }
    // Fallback (e.g. opened from a page without the chat modal): use the page.
    window.location.href = '/pwanimate/sessions/';
  };

  function progressRow(label, value, tone) {
    const wrap = el('div', 'spark-meter-row');
    wrap.appendChild(el('span', 'spark-meter-label', label));
    const track = el('div', 'spark-progress spark-progress-sm');
    const fill = el('div', `spark-progress-fill ${tone || ''}`);
    fill.style.width = `${Math.max(0, Math.min(100, value || 0))}%`;
    track.appendChild(fill);
    wrap.appendChild(track);
    wrap.appendChild(el('span', 'spark-meter-value', `${value || 0}%`));
    return wrap;
  }

  function roadmapList(roadmap) {
    const wrap = el('div', 'spark-roadmap');
    if (!Array.isArray(roadmap) || !roadmap.length) {
      wrap.appendChild(el('p', 'text-muted small mb-0', 'No roadmap sections yet — ask Spark to plan one.'));
      return wrap;
    }
    const icons = { done: 'circle-check-big text-success', doing: 'refresh-cw text-primary', pending: 'circle text-muted' };
    roadmap.forEach(item => {
      const status = item.status || 'pending';
      const row = el('div', `spark-roadmap-item ${status}`);
      row.appendChild(icon(icons[status] || icons.pending, 'spark-roadmap-icon'));
      row.appendChild(el('span', 'spark-roadmap-label', item.label || ''));
      wrap.appendChild(row);
    });
    return wrap;
  }

  function initialize(root) {
    if (!root || root.dataset.ready || !root.getClientRects().length) return;
    root.dataset.ready = 'true';

    const statusEl = root.querySelector('[data-sessions="status"]');
    const listEl = root.querySelector('[data-sessions="list"]');
    const filtersEl = root.querySelector('[data-sessions="filters"]');
    let sessions = [];
    let filter = 'progress';
    const quizState = {};   // sessionId -> { assessment, panel }
    const reportState = {}; // sessionId -> { loaded }

    function setStatus(text) { if (statusEl) statusEl.textContent = text || ''; }

    function visibleSessions() {
      if (filter === 'all') return sessions;
      if (filter === 'completed') return sessions.filter(s => s.status === 'completed');
      return sessions.filter(s => s.status === 'active' || s.status === 'paused');
    }

    async function load() {
      setStatus('Loading sessions…');
      try {
        const data = await api('study-sessions/');
        sessions = Array.isArray(data.sessions) ? data.sessions : [];
        render();
      } catch (err) {
        setStatus(err.message || 'Could not load sessions.');
      }
    }

    function render() {
      listEl.replaceChildren();
      const shown = visibleSessions();
      if (!shown.length) {
        const empty = el('div', 'pwanimate-choice-card p-4 rounded-3 border text-center');
        empty.appendChild(icon('book-x', 'display-6 text-muted d-block mb-2'));
        empty.appendChild(el('p', 'text-muted small mb-0',
          sessions.length
            ? 'No sessions in this view — switch filters or start a new unit session with Spark.'
            : 'No study sessions yet — ask Spark to start one for a unit you are taking.'));
        listEl.appendChild(empty);
        setStatus('');
        return;
      }
      setStatus(`${shown.length} session${shown.length === 1 ? '' : 's'} shown.`);
      shown.forEach(session => listEl.appendChild(renderCard(session)));
    }

    function renderCard(session) {
      const meta = STATUS_META[session.status] || STATUS_META.paused;
      const card = el('article', 'pwanimate-choice-card spark-session-card p-3 rounded-3 border');
      card.dataset.sessionId = session.id;

      // Header: unit badge + title + status
      const head = el('div', 'd-flex align-items-start justify-content-between gap-2 flex-wrap');
      const titleWrap = el('div', 'flex-grow-1 min-w-0');
      const unitCode = session.unit_code || session.unit_label || 'General';
      const badge = el('span', 'badge rounded-pill bg-primary-subtle text-primary-emphasis me-2', unitCode);
      titleWrap.appendChild(badge);
      titleWrap.appendChild(el('span', 'fw-semibold', session.title || session.unit_label || 'Study Session'));
      if (session.target_goal) {
        const goal = el('div', 'small text-muted mt-1', `Target: ${session.target_goal}`);
        titleWrap.appendChild(goal);
      }
      head.appendChild(titleWrap);

      const statusPill = el('span', `badge rounded-pill ${meta.cls} flex-shrink-0`);
      statusPill.appendChild(icon(meta.icon, 'me-1'));
      statusPill.appendChild(document.createTextNode(meta.label));
      head.appendChild(statusPill);
      card.appendChild(head);

      // Meta line
      const metaBits = [];
      if (session.assessment_mode) metaBits.push(`Mode: ${MODE_LABEL[session.assessment_mode] || session.assessment_mode}`);
      if (session.last_active_at) metaBits.push(`Active ${timeAgo(session.last_active_at)}`);
      if (metaBits.length) card.appendChild(el('div', 'small text-muted mt-1', metaBits.join(' · ')));

      // Progress
      const progress = el('div', 'spark-session-progress mt-3');
      const overall = el('div', 'd-flex align-items-center justify-content-between mb-1');
      overall.appendChild(el('span', 'small fw-semibold', 'Progress'));
      overall.appendChild(el('span', 'small fw-bold text-primary', `${session.progress_percent || 0}%`));
      progress.appendChild(overall);
      const bar = el('div', 'spark-progress');
      const fill = el('div', 'spark-progress-fill');
      fill.style.width = `${Math.max(0, Math.min(100, session.progress_percent || 0))}%`;
      bar.appendChild(fill);
      progress.appendChild(bar);
      progress.appendChild(progressRow('Roadmap coverage', session.roadmap_coverage_percent ?? 0, 'spark-progress-coverage'));
      progress.appendChild(progressRow('Tested mastery', session.mastery_percent || 0, 'spark-progress-mastery'));
      card.appendChild(progress);

      // Roadmap (collapsible)
      const roadmapDetails = el('details', 'spark-roadmap-details mt-2');
      roadmapDetails.appendChild(el('summary', 'small fw-semibold text-muted',
        `Roadmap — ${Array.isArray(session.roadmap) ? session.roadmap.filter(i => i.status === 'done').length : 0}/${Array.isArray(session.roadmap) ? session.roadmap.length : 0} sections`));
      roadmapDetails.appendChild(roadmapList(session.roadmap));
      card.appendChild(roadmapDetails);

      // Actions
      const actions = el('div', 'd-flex align-items-center gap-2 flex-wrap mt-3');
      if (session.url) {
        const open = el('a', 'btn btn-primary btn-sm d-inline-flex align-items-center gap-1', 'Open chat');
        open.href = session.url;
        open.prepend(icon('message-circle'));
        actions.appendChild(open);
      }
      const quizBtn = el('button', 'btn btn-outline-primary btn-sm d-inline-flex align-items-center gap-1', 'Quiz');
      quizBtn.type = 'button';
      quizBtn.appendChild(icon('circle-help'));
      quizBtn.addEventListener('click', () => toggleQuizPanel(card, session, quizBtn));
      actions.appendChild(quizBtn);

      const packBtn = el('button', 'btn btn-outline-secondary btn-sm d-inline-flex align-items-center gap-1', 'End-of-session pack');
      packBtn.type = 'button';
      packBtn.appendChild(icon('package'));
      packBtn.addEventListener('click', () => toggleReportPanel(card, session, packBtn));
      actions.appendChild(packBtn);

      if (session.status === 'active' || session.status === 'paused') {
        const pauseBtn = el('button', 'btn btn-outline-secondary btn-sm', session.status === 'paused' ? 'Resume' : 'Pause');
        pauseBtn.type = 'button';
        pauseBtn.addEventListener('click', async () => {
          pauseBtn.disabled = true;
          try {
            await api(`study-sessions/${session.id}/status/`, {
              method: 'POST',
              body: JSON.stringify({ action: session.status === 'paused' ? 'resume' : 'pause' }),
            });
            await load();
          } catch (err) { setStatus(err.message); pauseBtn.disabled = false; }
        });
        actions.appendChild(pauseBtn);

        const endBtn = el('button', 'btn btn-outline-danger btn-sm', 'End');
        endBtn.type = 'button';
        endBtn.addEventListener('click', async () => {
          if (!confirm('End this study session? Its history and progress are preserved.')) return;
          endBtn.disabled = true;
          try {
            await api(`study-sessions/${session.id}/status/`, {
              method: 'POST',
              body: JSON.stringify({ action: 'end' }),
            });
            await load();
          } catch (err) { setStatus(err.message); endBtn.disabled = false; }
        });
        actions.appendChild(endBtn);
      }
      card.appendChild(actions);

      // Panels mount point
      const panels = el('div', 'spark-session-panels mt-3');
      card.appendChild(panels);
      return card;
    }

    function panelFor(card, key) {
      const panels = card.querySelector('.spark-session-panels');
      let panel = panels.querySelector(`[data-panel="${key}"]`);
      if (!panel) {
        panel = el('div', 'spark-session-panel border rounded-3 p-3');
        panel.dataset.panel = key;
        panels.appendChild(panel);
      }
      return panel;
    }

    function togglePanel(card, key, button, loader) {
      const panels = card.querySelector('.spark-session-panels');
      const existing = panels.querySelector(`[data-panel="${key}"]`);
      if (existing) {
        const wasOpen = !existing.classList.contains('d-none');
        panels.querySelectorAll('.spark-session-panel').forEach(p => p.classList.add('d-none'));
        if (wasOpen) { button.classList.remove('active'); return; }
        existing.classList.remove('d-none');
      } else {
        panels.querySelectorAll('.spark-session-panel').forEach(p => p.classList.add('d-none'));
      }
      button.classList.add('active');
      loader(panelFor(card, key));
    }

    function toggleQuizPanel(card, session, button) {
      togglePanel(card, 'quiz', button, async (panel) => {
        panel.replaceChildren(el('p', 'small text-muted', 'Loading quizzes…'));
        try {
          await renderQuizPanel(panel, session);
        } catch (err) {
          panel.replaceChildren(el('p', 'small text-danger', err.message || 'Could not load quizzes.'));
        }
      });
    }

    function toggleReportPanel(card, session, button) {
      togglePanel(card, 'report', button, async (panel) => {
        panel.replaceChildren(el('p', 'small text-muted', 'Loading report…'));
        try {
          await renderReportPanel(panel, session);
        } catch (err) {
          panel.replaceChildren(el('p', 'small text-danger', err.message || 'Could not load report.'));
        }
      });
    }

    async function renderQuizPanel(panel, session) {
      const data = await api(`study-sessions/${session.id}/assessments/`);
      const assessments = Array.isArray(data.assessments) ? data.assessments : [];
      panel.replaceChildren();

      const head = el('div', 'd-flex align-items-center justify-content-between mb-2');
      head.appendChild(el('h6', 'fw-bold mb-0', 'Quiz & mastery'));
      const newBtn = el('button', 'btn btn-primary btn-sm', 'New quiz');
      newBtn.type = 'button';
      head.appendChild(newBtn);
      panel.appendChild(head);

      const history = el('div', 'spark-quiz-history mb-2');
      panel.appendChild(history);
      const renderHistory = () => {
        history.replaceChildren();
        if (!assessments.length) {
          history.appendChild(el('p', 'small text-muted mb-0', 'No quizzes yet. Only correctly graded answers raise mastery.'));
          return;
        }
        assessments.slice(0, 6).forEach(a => {
          const row = el('div', 'small d-flex align-items-center gap-2 py-1 border-bottom');
          row.appendChild(el('span', 'fw-semibold', a.title || a.mode));
          row.appendChild(el('span', 'badge rounded-pill bg-secondary-subtle text-secondary',
            `${a.correct}/${a.answered}/${a.total} · ${a.status}`));
          if (a.counts_toward_mastery === false) {
            row.appendChild(el('span', 'badge rounded-pill bg-warning-subtle text-warning-emphasis', 'practice'));
          }
          history.appendChild(row);
        });
      };
      renderHistory();

      const work = el('div', 'spark-quiz-work mt-2');
      panel.appendChild(work);

      const showAssessment = (assessment) => {
        work.replaceChildren();
        const unanswered = (assessment.questions || []).filter(q => !q.answered);
        if (!unanswered.length) {
          work.appendChild(el('p', 'small text-success mb-0',
            assessment.status === 'completed' ? 'This quiz is complete — every answer was graded.' : 'All questions answered.'));
          return;
        }
        const list = el('div', 'd-flex flex-column gap-3');
        unanswered.forEach(q => list.appendChild(renderQuestion(session, assessment, q)));
        work.appendChild(list);
      };

      const startNew = async () => {
        newBtn.disabled = true;
        newBtn.textContent = 'Generating…';
        try {
          const created = await api(`study-sessions/${session.id}/assessments/`, {
            method: 'POST',
            body: JSON.stringify({ action: 'generate', count: 5, mode: session.assessment_mode || undefined }),
          });
          assessments.unshift(created.assessment);
          renderHistory();
          showAssessment(created.assessment);
        } catch (err) {
          work.replaceChildren(el('p', 'small text-danger', err.message || 'Could not generate the quiz.'));
        } finally {
          newBtn.disabled = false;
          newBtn.textContent = 'New quiz';
        }
      };
      newBtn.addEventListener('click', startNew);

      const open = assessments.find(a => (a.questions || []).some(q => !q.answered));
      if (open) showAssessment(open);
    }

    function renderQuestion(session, assessment, question) {
      const box = el('div', 'spark-quiz-question border rounded-3 p-3');
      box.appendChild(el('p', 'small fw-semibold mb-2', question.prompt));
      const verdict = el('div', 'spark-quiz-verdict mt-2');
      const controls = el('div', 'spark-quiz-controls');

      const lock = (correct, feedback, explanation, mastery, progress) => {
        box.querySelectorAll('button, input').forEach(n => { n.disabled = true; });
        verdict.classList.remove('d-none');
        verdict.classList.add(correct ? 'text-success' : 'text-danger');
        verdict.innerHTML = '';
        verdict.appendChild(el('div', 'small fw-semibold', correct ? 'Correct' : 'Not quite'));
        if (feedback) verdict.appendChild(el('div', 'small text-muted', feedback));
        if (explanation) verdict.appendChild(el('div', 'small text-muted fst-italic', explanation));
        if (typeof mastery === 'number') {
          verdict.appendChild(el('div', 'small mt-1', `Mastery ${mastery}% · Progress ${progress ?? '—'}%`));
          updateSessionProgress(session.id, mastery, progress);
        }
      };

      const submit = async (answer) => {
        try {
          const res = await api(`study-sessions/${session.id}/assessments/`, {
            method: 'POST',
            body: JSON.stringify({ action: 'answer', assessment_id: assessment.id, qid: question.qid, answer }),
          });
          question.answered = true;
          question.correct = res.result.correct;
          question.feedback = res.result.feedback;
          question.explanation = res.result.explanation;
          lock(res.result.correct, res.result.feedback, res.result.explanation,
            res.result.mastery_percent, res.result.progress_percent);
        } catch (err) {
          verdict.classList.remove('d-none');
          verdict.classList.add('text-danger');
          verdict.textContent = err.message || 'Could not grade that answer.';
        }
      };

      if (Array.isArray(question.options) && question.options.length) {
        question.options.forEach((opt, idx) => {
          const btn = el('button', 'btn btn-outline-secondary btn-sm d-block w-100 text-start mb-1 spark-quiz-option');
          btn.type = 'button';
          btn.textContent = `${String.fromCharCode(65 + idx)}. ${opt}`;
          btn.addEventListener('click', () => submit(opt));
          controls.appendChild(btn);
        });
      } else {
        const input = el('input', 'form-control form-control-sm');
        input.type = 'text';
        input.placeholder = 'Type your answer…';
        const send = el('button', 'btn btn-primary btn-sm mt-2', 'Submit answer');
        send.type = 'button';
        send.addEventListener('click', () => {
          const value = input.value.trim();
          if (value) submit(value);
        });
        input.addEventListener('keydown', (e) => { if (e.key === 'Enter') send.click(); });
        controls.appendChild(input);
        controls.appendChild(send);
      }

      box.appendChild(controls);
      verdict.classList.add('d-none');
      box.appendChild(verdict);
      return box;
    }

    function updateSessionProgress(sessionId, mastery, progress) {
      // Patch the open card in place — a full render() would collapse the quiz panel.
      const card = listEl.querySelector(`[data-session-id="${sessionId}"]`);
      if (!card) return;
      const session = sessions.find(s => s.id === sessionId);
      if (session) {
        if (typeof mastery === 'number') session.mastery_percent = mastery;
        if (typeof progress === 'number') session.progress_percent = progress;
      }
      if (typeof progress === 'number') {
        const bar = card.querySelector('.spark-progress .spark-progress-fill');
        if (bar) bar.style.width = `${Math.max(0, Math.min(100, progress))}%`;
        const pct = card.querySelector('.spark-session-progress .fw-bold');
        if (pct) pct.textContent = `${progress}%`;
      }
      if (typeof mastery === 'number') {
        card.querySelectorAll('.spark-meter-row').forEach(row => {
          if (row.querySelector('.spark-meter-label')?.textContent === 'Tested mastery') {
            const fill = row.querySelector('.spark-progress-fill');
            const val = row.querySelector('.spark-meter-value');
            if (fill) fill.style.width = `${Math.max(0, Math.min(100, mastery))}%`;
            if (val) val.textContent = `${mastery}%`;
          }
        });
      }
    }

    async function renderReportPanel(panel, session) {
      const data = await api(`study-sessions/${session.id}/report/`);
      panel.replaceChildren();
      const stats = data.stats || {};
      const head = el('div', 'd-flex align-items-center justify-content-between mb-2');
      head.appendChild(el('h6', 'fw-bold mb-0', 'Performance report'));
      const genBtn = el('button', 'btn btn-primary btn-sm', 'Generate pack');
      genBtn.type = 'button';
      head.appendChild(genBtn);
      panel.appendChild(head);

      const summary = el('div', 'spark-report-stats small text-muted mb-2');
      const acc = stats.accuracy_percent;
      summary.textContent =
        `${stats.questions_answered || 0} answered · ${stats.questions_correct || 0} correct` +
        (acc === null || acc === undefined ? '' : ` (${acc}%)`) +
        ` · ${stats.duration_minutes || 0} min studied`;
      panel.appendChild(summary);

      const artifacts = el('div', 'spark-report-artifacts d-flex flex-column gap-2');
      panel.appendChild(artifacts);

      const renderArtifacts = (items) => {
        artifacts.replaceChildren();
        if (!items || !items.length) {
          artifacts.appendChild(el('p', 'small text-muted mb-0', 'No saved artifacts yet — generate a pack to create the report, practice quiz, and summary.'));
          return;
        }
        items.forEach(a => {
          const row = el('div', 'border rounded-3 p-2');
          const top = el('div', 'd-flex align-items-center justify-content-between gap-2');
          top.appendChild(el('span', 'small fw-semibold', a.title || a.kind));
          top.appendChild(el('span', 'badge rounded-pill bg-secondary-subtle text-secondary', a.kind));
          row.appendChild(top);
          if (a.markdown) {
            const det = el('details', 'mt-1');
            det.appendChild(el('summary', 'small text-muted', 'Preview'));
            const pre = el('pre', 'spark-artifact-md small mt-1 mb-0');
            pre.textContent = a.markdown;
            det.appendChild(pre);
            row.appendChild(det);
          }
          artifacts.appendChild(row);
        });
      };
      renderArtifacts(data.artifacts);

      genBtn.addEventListener('click', async () => {
        genBtn.disabled = true;
        genBtn.textContent = 'Generating…';
        try {
          const created = await api(`study-sessions/${session.id}/report/`, {
            method: 'POST',
            body: JSON.stringify({ include_practice: true, include_summary: true }),
          });
          const items = [created.report, created.practice, created.summary].filter(Boolean);
          const fresh = await api(`study-sessions/${session.id}/report/`);
          renderArtifacts(fresh.artifacts && fresh.artifacts.length ? fresh.artifacts : items);
          if (created.stats) {
            const acc2 = created.stats.accuracy_percent;
            summary.textContent =
              `${created.stats.questions_answered || 0} answered · ${created.stats.questions_correct || 0} correct` +
              (acc2 === null || acc2 === undefined ? '' : ` (${acc2}%)`) +
              ` · ${created.stats.duration_minutes || 0} min studied`;
          }
        } catch (err) {
          artifacts.appendChild(el('p', 'small text-danger', err.message || 'Could not generate the pack.'));
        } finally {
          genBtn.disabled = false;
          genBtn.textContent = 'Generate pack';
        }
      });
    }

    if (filtersEl) {
      filtersEl.addEventListener('click', (e) => {
        const btn = e.target.closest('[data-filter]');
        if (!btn) return;
        filter = btn.dataset.filter;
        filtersEl.querySelectorAll('[data-filter]').forEach(b => b.classList.toggle('active', b === btn));
        render();
      });
    }
    const refreshBtn = root.querySelector('[data-sessions="refresh"]');
    if (refreshBtn) refreshBtn.addEventListener('click', load);

    load();
  }

  window.initSparkStudySessions = function (scope = document) {
    if (scope.matches?.('[data-sessions-root]')) initialize(scope);
    scope.querySelectorAll('[data-sessions-root]').forEach(initialize);
  };
  document.addEventListener('htmx:afterSwap', () => window.initSparkStudySessions());
  document.addEventListener('shown.bs.modal', (e) => {
    if (e.target && e.target.id === 'pwanimateSessionsModal') window.initSparkStudySessions(e.target);
  });
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', () => window.initSparkStudySessions());
  else window.initSparkStudySessions();
})();
