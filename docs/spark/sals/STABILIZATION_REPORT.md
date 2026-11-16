# SALS 0.3 — Tutoring Behavior & Spark Thinking Restoration Report

Focused stabilization of three related defects in the existing PwaniNet/Vasto
Spark tutoring experience. Not a new work package; no WP4 work; no UI redesign.

## 1. Root causes

### 1.1 Spark Thinking / agentic activity appeared empty or absent

- The activity *framework* was intact end-to-end (backend `public_event`
  sanitization → `record_progress` dedup → SSE `activity` events →
  `activity-trail.js` rendering → collapse-on-first-chunk → journal replay).
  The regression was **event starvation**, not a rendering break:
  - `TeachingService.prepare()` emitted **zero** progress events during real
    preparation (session resolution, state lock, lesson/exercise selection).
    Its only emission was the `clarification` event when a question was
    pending — i.e., after all the work was already done.
  - The WP3 routine-tutoring fast path intentionally skips the planner, so it
    legitimately has no checklist/plan events — but combined with the
    teaching-path silence, most tutoring turns showed only the synthetic
    `request` marker plus a late `writing_answer` stage that collapsed almost
    immediately on the first answer chunk.
  - The earlier privacy cleanup had also removed the dedicated
    "Spark's thinking" subsection; public execution summaries remained, but
    nothing truthful was being emitted early enough to fill the trail.

### 1.2 Progressive hint restraint bypassed the hint state machine

- `TeachingService._hint()` already implements the required ladder
  (level 1 conceptual nudge → 2 strategy → 3 partial step → 4 full
  explanation) and `TEACHING_POLICY` already forbids revealing a full
  solution below level 4. The gap was **routing**:
  - Free-text limited-help requests such as *"Show me where I went wrong, but
    don't give me the complete solution"* matched neither the exact-command
    table in `_command()` nor the narrow solution regex in `prepare()`.
  - They fell through as `action='answer'`/`'start'` into the general
    answer-model path with **no `requested_action` constraint**, so the model
    computed and revealed the correct normal vector.
  - For non-`general` lesson keys a fresh hint request could even fall into
    the deterministic `EXPLAIN + DEMONSTRATE` dump — the opposite of
    restraint.

### 1.3 Academic tracking awareness was not represented to the model

- `academic_unit_context` carries record *summaries* only when a record
  exists and the query matches; it carries **no** authorization/pending/
  declined/disabled posture.
- `PendingAction` consent cards are serialized into the HTTP response
  *after* generation (`academic_actions`), so during generation the model had
  no server-verified way to know whether an approval card actually exists.
- With legacy Study Mode active (`study_mode=True`, `study_context` block) but
  no authorized unit-linked session, the model could invent consent language
  such as "give consent through the interactive approval prompt that will
  appear below" — a card that was never persisted and never rendered.

## 2. Changes

### Files modified

| File | Change |
|---|---|
| `pwanimate/orchestrator/activity.py` | New truthful stage `teaching_check` ("Preparing your lesson") in `STAGES`, `_STAGE_ACTIVITY_TYPES` (`thinking` icon) and `STAGE_SUMMARIES`. |
| `static/js/pwanimate/activity-trail.js` | Mirrored `teaching_check` label + deterministic summary (parity with backend). |
| `pwanimate/services/teaching.py` | Added `_assistance_request()` classifier (`_HINT_REQUEST_RE` / `_SOLUTION_REQUEST_RE`, restrained phrasing checked first). `_command()` now classifies free-text interaction answers; fresh queries classify instead of the old narrow solution regex. Hint-without-exercise requests are cleared for the constrained answer-model path (`requested_action='hint'`) instead of dumping explanation content. Emits one `teaching_check` stage event per real preparation (cached replays return earlier and never emit). |
| `pwanimate/services/unit_learning.py` | New `tracking_state(user, conversation)` — server-verified posture: `preference_mode`, `tracking_disabled`, `active_unit_tracking` (authorized + writable + live session, conversation-scoped for `ask` mode), `pending_consents` (persisted, unexpired, renderable in this conversation), `declined_units`. Read-only; no writes. |
| `pwanimate/orchestrator/types.py` | New `OrchestrationRequest.tracking_state` field. |
| `pwanimate/api/views.py` | `get_tracking_state()` populates the request field for every chat turn. |
| `pwanimate/services/agent_execution.py` | Resumed agent runs populate `tracking_state` from the run's conversation. |
| `pwanimate/orchestrator/service.py` | `_apply_study_and_warning_context()` injects the tracking-state JSON block plus an instruction restricting consent/tracking claims to the verified state. Applies to `_run_tool`, `_run_rag`, `_run_conversational` — i.e., every generation path including fast paths. |
| `tests/sals_03_wp2/test_teaching.py` | +5 regression tests (limited-help routing, escalation→solution, fresh-query routing, teaching_check emission/replay, classifier table). |
| `tests/sals_03_wp2/test_consent.py` | +7 `TrackingStateTests` (pending/authorized/declined/disabled/duplicates/cross-conversation/prompt block). |
| `tests/sals_wp3/test_runtime_pipeline.py` | +1 test: fast path emits only truthful execution stages — no fabricated checklist/plan events. |
| `tests/spark/activity-explanations.cjs` | Asserts `teaching_check` stage projects with correct label/summary and replay idempotence. |

No migrations. No new dependencies. No second grading/mastery system —
assistance recording reuses the existing `SparkTeachingTurn.assistance` →
`effective_assistance()` → evidence pipeline unchanged.

## 3. Behavior after the fix

### Activity / Spark Thinking

- Teaching turns now show **Preparing your lesson** (`teaching_check`,
  thinking icon) while session/state/lesson preparation really runs, then the
  existing `writing_answer` stage, then stream — collapsed automatically on
  the first answer chunk with completed entries preserved and manually
  re-expandable (`<details>`), hydrated identically on reload/reconnect via
  the durable activity journal.
- Cached teaching-turn replays emit nothing (they return before preparation).
- The WP3 fast path emits only stages for work that actually happens
  (`writing_answer` before generation; retrieval stages on the `rag` route).
  No planning/checklist events are fabricated for a planner that never ran.
- All events still pass through `public_event`/`public_events` on write and
  replay; raw tool arguments, prompts, reasoning and supplied summaries are
  still stripped. No artificial delays were added.

### Progressive hints

- "Show me where I went wrong, but don't give me the complete solution" now
  routes to `action='hint'` → existing `hint_level` machine:
  - With an exercise: deterministic level-1 conceptual nudge (no formula, no
    substitution, no expected value); repeated requests escalate 1→2→3.
  - Without an exercise or on `general` lessons: answer model receives
    `requested_action='hint'` + `TEACHING_POLICY` level constraints.
- "show me the full solution" / "just give me the answer" still reach level 4
  and record `assistance='worked_solution'` — assisted, never independent
  mastery evidence (existing `effective_assistance` logic, covered by
  `test_formal_hint_marks_assessment_assisted_and_never_mastered`).
- Ordinary attempts ("3, -1, 2", "is this wrong?") are **not** misclassified
  as help requests and still go through exercise evaluation.

### Tracking awareness

- The prompt now carries a server-verified `Academic tracking state` block:
  - `active_unit_tracking` non-empty → tracking is genuinely recording;
    consent is never re-requested (existing `process_message` dedup also
    holds).
  - `pending_consents` non-empty → a real persisted `PendingAction` exists and
    is serialized as `academic_actions` with this reply — the model may
    reference the card.
  - Otherwise the model is instructed to tutor normally and never promise a
    card — fixing the "approval prompt will appear below" fabrication.
- `declined_units`/`tracking_disabled` are explicit so Spark does not re-ask.
- No writes were added; unauthorized unit-linked persistence rules are
  unchanged (`require_writable`, consent-revision checks, no pre-approval
  import).

## 4. Verification (local only — no PostgreSQL/Docker run)

```
Ran 287 tests in 118.280s — OK (skipped=19)
```

Includes the 12 new regression tests. JS contracts all pass:

```
PASS backend/frontend explanation parity, replay idempotence, outcomes and injection filtering
PASS legacy activity narration/privacy, fixed summaries, checklist filtering, durable final-message deduplication
PASS first answer chunk collapses fresh/resumed trails once; user may reopen during remaining stream
PASS teaching explanation remains visible while original run waits on its next single question
PASS acceptance removes block before completion; original-run payload; rapid-click lock; one final response; interrupted SSE reconnects via GET; failed validation preserves answers
PASS real KaTeX/Marked ...
PASS authored WP2 mathematics, programming and theory examples ...
PASS actual question controls ...
PASS: merged composer panel ...
PASS quiz DOM ...
```

Script: `bash scripts/test-sals03-wp2-local.sh`

## 5. PostgreSQL verification commands (owner-run)

```bash
cd /home/zayne/projects/pwaninet
# Prerequisites (once): pulls pgvector image, checks deps
bash scripts/test-sals03-wp2-postgres.sh prerequisites
bash scripts/test-sals03-wp2-postgres.sh prepare

# Full WP2+WP3 PostgreSQL suite — includes the new teaching/consent tests
# (tests.sals_03_wp2 covers test_teaching.py + test_consent.py) and the
# concurrency/persistence phases:
bash scripts/test-sals03-wp2-postgres.sh all

# Targeted phases if preferred:
bash scripts/test-sals03-wp2-postgres.sh persistence   # teaching + persistence
bash scripts/test-sals03-wp2-postgres.sh integrity     # teaching + mastery
bash scripts/test-sals03-wp2-postgres.sh regressions   # broad regression set
bash scripts/test-sals03-wp2-postgres.sh concurrency   # run/interaction races

# Cleanup disposable containers when done:
bash scripts/test-sals03-wp2-postgres.sh cleanup
```

## 6. Browser acceptance procedure

1. Start a unit-linked study session (approve a tracking card once), ask a
   planes/lines question, reach a practice problem, submit a **wrong** answer.
2. When the question widget is pending, type:
   *"Show me where I went wrong, but don't give me the complete solution."*
   - Expect: activity trail shows **Preparing your lesson → Putting the
     answer together**, collapses on first streamed token; the reply is a
     level-1 nudge — **no correct normal vector, no final numeric answer**.
3. Ask *"another hint"* → strategy-level hint (still no final vector).
4. Ask *"show me the full solution"* → full worked explanation; the turn is
   recorded as assisted (verify via academic evidence API — `assistance`
   is `worked_solution`, never independent mastery).
5. In legacy Study Mode **without** unit tracking: ask an academic question.
   - Expect: normal tutoring; **no** text promising an approval card unless a
     card is actually rendered under the reply.
6. With tracking already authorized+active: confirm Spark never re-asks for
     consent and describes tracking as active.
7. Decline a consent card → tutoring continues normally; later academic
     messages do not resurrect the prompt for that enrollment/term.
8. Reload mid-stream and after completion: activity trail hydrates from the
   durable journal, collapsed with expandable completed steps.

## 7. Remaining limitations

- Resumed agent runs still do not carry the WP3 `TimingRecorder` beyond the
  resume boundary (pre-existing; perf marks stop at resume).
- `tracking_state` is computed per turn; a consent approved *during* a run
  appears on the next turn, not retroactively.
- Hint restraint for non-exercise lessons delegates phrasing restraint to the
  answer model under `requested_action='hint'` + policy — deterministic
  guarantees exist only where `state.exercise` is populated.
- `teaching_check` covers preparation only; generation itself is represented
  by the existing `writing_answer` stage.

## 8. Acceptance checklist

| Requirement | Status |
|---|---|
| Thinking/activity section during meaningful execution | PASS (`teaching_check` + existing stages) |
| Progress events stream during execution | PASS |
| Planning objectives/tool calls/retrieval/teaching prep shown where they occur | PASS (existing agent emissions + new teaching stage) |
| No raw CoT/credentials/prompts exposed | PASS (`public_event` on write+replay; privacy contract test) |
| No fabricated thoughts/progress | PASS (cached replays and fast path emit nothing extra; regression test) |
| Lucide icons preserved | PASS (stage→`thinking` activity type, existing icon map) |
| Subtle theme-aware design unchanged | PASS (no CSS/template changes) |
| Expanded during pre-answer work, collapse on first chunk, preserved entries, manual expand, reload/reconnect | PASS (existing trail behavior re-verified by stream-collapse/lifecycle JS contracts) |
| No artificial delays | PASS |
| Fast path shows only actual prep/execution activity | PASS (regression test) |
| Limited-assistance detection | PASS |
| Earliest meaningful error / smallest useful hint | PASS (deterministic level-1 nudge; model constrained otherwise) |
| Wait for another attempt; escalate on request | PASS (levels 1→4 tested) |
| No full solution during initial hint | PASS (test asserts expected/substitution absent) |
| Assistance recorded; no independent mastery credit | PASS (existing pipeline, tests) |
| Explicit full-solution requests honored | PASS (level 4, `worked_solution`) |
| Existing tutoring state machine + assessment infra reused | PASS (no second grading system) |
| Authorized+active tracking → no re-consent | PASS |
| Study Mode without authorization → structured consent flow only | PASS |
| No consent-card promise without persisted renderable interaction | PASS (`pending_consents` is conversation-scoped persisted state) |
| No tracking-active claims without verified session | PASS (`active_unit_tracking` requires authorized record + writable + live session) |
| No unauthorized unit-linked persistence | PASS (unchanged guards) |
| Normal tutoring after rejection | PASS (existing + tested) |
| No repeated prompts / duplicate sessions | PASS (existing dedup + tested via `tracking_state`) |
| WP4 not started; no UI redesign; prior consent/composer/follow-up/WP3 work preserved | PASS |
