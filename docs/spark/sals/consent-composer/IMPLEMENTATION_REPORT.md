# Spark Study Consent Fix & Unified Composer Interactions — Implementation Report

Scope: PwaniNet/Vasto Spark. Two coupled fixes — (A) academic study-tracking
consent rendered as a persisted, explicit approval dialogue attached to the
turn that triggered it, and (B) typed/mixed agent interactions unified with
the main composer so there is exactly one answer field.

## A. Root cause

Unit detection (`UnitLearningService.process_safely`, dispatched `on_commit`
after the user message persists) already created a durable, idempotent
`PendingAction(ACTION_TRACK_UNIT)`. Approval/cancel already flowed through
`PwanimateActionView` → `ActionService.confirm/cancel` →
`UnitLearningService.approve_action` with full ownership, consent-revision,
enrollment, semester and source-message checks.

The missing piece was presentation: the chat response never carried the
proposal, so "May I track this study session for SMA B203?" existed only as
LLM prose, while the real control surfaced later — if at all — through the
`/api/pwanimate/academic/` poll appended at the transcript end, detached from
the message that asked.

## B. What changed

### Backend

- `pwanimate/api/views.py` (`_post_chat`): the response now includes
  `academic_actions` — pending, unexpired `ACTION_TRACK_UNIT` proposals for
  the conversation, serialized via `ActionService.serialize()` (same contract
  as the action endpoints). Failures degrade to no field, never a broken turn.
- `pwanimate/api/serializers.py`: `MessageSerializer` gained
  `academic_actions`; `ConversationDetailSerializer.get_messages` maps each
  pending proposal to the first assistant message after its
  `params.source_message_id` user turn — one bounded query per conversation,
  no N+1. Reload/API consumers get the card attached to the right reply.

### Frontend (`static/js/pwanimate/pwanimate-chat.js`)

- `appendAssistantMessage(..., academicActions)` renders
  `_academicTrackingBlock(action)` inside the assistant bubble, deduplicated
  by `data-action-id`.
- `_academicTrackingBlock` now sets `data-action-id` and uses explicit
  **Approve / Decline** labels (decision values `confirm`/`cancel` unchanged —
  same `/api/pwanimate/actions/<id>/` contract).
- `refreshAcademicTracking()` (the poll) now:
  - removes stale `.spark-academic-approval[data-action-id]` cards whose
    action is no longer pending (unless mid-decision),
  - only rebuilds detached `:scope >` transcript blocks,
  - places fallback blocks under the originating assistant message via
    `_academicActionHost()` (`params.source_message_id` → next non-interim
    assistant row), instead of always appending at the transcript end.

### Unified composer (`_buildInteractionBlock`, `_questionControls`,
`_syncInteractionBlock`, `handleSend`)

Classification (single-question, non-group interactions only):

| input_type | allow_custom_answer | Mode |
|---|---|---|
| `text` (or unknown) | — | merged: prompt panel above composer, composer is the field |
| `single_choice` / `multi_choice` | true (default) | merged: choices in panel + composer text, atomic submit |
| `single_choice` / `multi_choice` | false | standalone choices-only block (unchanged) |
| `confirm` | — | standalone Confirm/Decline block (unchanged) |
| `group` | — | standalone wizard (unchanged) |

Merged mode specifics:

- Panel gets `spark-interaction-block--merged`: composer background token,
  shared border, `26px 26px 0 0` radius, no bottom border; the composer form
  drops to `0 0 26px 26px` via the adjacent-sibling selector — one seamless
  surface. Safe-area padding, keyboard-open behavior and theme tokens are
  untouched (all live on `.pwanimate-composer-wrapper`).
- No second text input and no in-panel Send for merged interactions; a muted
  hint line explains the composer is the answer field.
- `el._composerAnswer(composerText)` assembles the atomic payload:
  `{text}` for typed, `{selected:[...]}` / `{selected:[...],text}` /
  `{text}` (custom path) for mixed — exactly what `_validate_question`
  accepts server-side.
- `handleSend` routes composer submission through the existing
  `resumeInteraction(run_id, interaction_id, answer)` path — same
  `interaction_id` + `resume_run_id`, same atomic claim, same journal tail.
  Standalone interactions still submit via their own block; group and
  attachment-carrying sends keep the existing guard notice.
- Placeholder swaps to "Type your answer" while merged and restores on
  clear (only if still ours).
- Drafts: the existing `spark-interaction-draft:<id>` record gains
  `composer_text`; keystrokes persist via a composer `input` listener that
  is removed with the block. Reload/reconnect restoration flows through the
  existing `activity-trail.js` → `setWaitingForInput` → draft restore.
- Failure path: `resumeInteraction` throws → composer text untouched, draft
  intact, block stays (same signature → no rebuild), system notice shown.
  `isGenerating`/`state.submitting` guards prevent double submission; the
  early return in `handleSend` prevents accidental new chat turns or
  duplicate runs.

### CSS (`static/css/pwanimate/pwanimate.css`)

- `.spark-interaction-block--merged`, `.spark-interaction-hint`,
  `.spark-interaction-block--merged + #pwanimate-composer-form` rules plus a
  mobile padding tweak. No new colors, gradients or decorative effects;
  light/dark via existing tokens.

## C. Files changed

- `pwanimate/api/views.py` — `academic_actions` in chat response.
- `pwanimate/api/serializers.py` — per-message `academic_actions`.
- `static/js/pwanimate/pwanimate-chat.js` — consent card rendering/dedupe/
  placement; merged interaction panel; composer answer routing; drafts.
- `static/css/pwanimate/pwanimate.css` — merged surface + hint styles.
- `tests/sals_03_wp2/test_consent.py` — new (7 tests).
- `tests/spark/composer-interaction.cjs` — new JS contract test.
- `tests/spark/interaction-flow.cjs` — updated single-question contract.
- `tests/spark/markdown-math.cjs`, `tests/spark/fixtures/markdown-math.browser.js`
  — slice marker + composer-bg expectation for merged panels.
- `scripts/test-sals03-wp2-local.sh` — runs the new cjs contract.

No model or migration changes. `collectstatic` run.

## D. Test results (local, no PostgreSQL/Docker)

- `manage.py test tests.sals_03_wp2 tests.sals_02.test_foundation`
  (SQLite sync schema): **104 tests, 0 failures** (9 skipped — PG-only).
  Includes new `test_consent.py`: proposal attaches to assistant reply,
  user messages carry none, duplicate detection never re-prompts, decline
  clears card + never tracks + tutoring continues, approve creates exactly
  one unit session + clears card + never imports the trigger + later
  activity flows, expired proposals hidden, cross-user isolation.
- `node --check static/js/pwanimate/pwanimate-chat.js` — OK.
- `node tests/spark/composer-interaction.cjs` — PASS (merged class,
  `_composerAnswer` payloads, no second input/Send, draft round-trip,
  placeholder swap/restore, listener cleanup, standalone choices-only and
  confirm, CSS contract).
- `node tests/spark/interaction-flow.cjs`, `question-controls.cjs`,
  `lifecycle-resume.cjs`, `teaching-flow.cjs`, `quiz-flow.cjs`,
  `stream-collapse.cjs`, `memory-approval.cjs`, `memory-settings.cjs`,
  `activity-*.cjs`, `markdown-math-unit.cjs`, `native-reminders.cjs` — PASS.
- `tests/spark/markdown-math.cjs` (real headless Chrome): 25/26 checks —
  the single failure `matrix double backslash preserved` is a KaTeX
  `<annotation>` assertion unrelated to this change (math rendering was not
  touched; predates this task).
- `tests/sals_wp3/run_offline.py`: 89/90 — one pre-existing failure in
  `test_complete_question_group_wins_over_duplicate_single_field`
  (`teaching.py session_for` hits the DB with a mock user); unrelated to
  consent/composer files.

## E. PostgreSQL verification (owner-run)

```bash
cd /home/zayne/projects/pwaninet

# Prerequisites + disposable pgvector container
bash scripts/test-sals03-wp2-postgres.sh prerequisites
bash scripts/test-sals03-wp2-postgres.sh prepare

# Consent/serialization + full WP2 regression suite on PostgreSQL
bash scripts/test-sals03-wp2-postgres.sh all

# Or targeted labels through the WP0 runner directly:
venv/bin/python -m tests.sals_wp0.run --suite sals03wp2 \
  tests.sals_03_wp2.test_consent \
  tests.sals_02.test_foundation.UnitFoundationTests \
  tests.sals_03_wp2.test_teaching \
  tests.sals_03_wp2.test_concurrency

# Interaction lifecycle + resume contracts (offline, no DB needed)
PYTHONPATH=. venv/bin/python tests/sals_wp3/run_offline.py

# JS contracts
node --check static/js/pwanimate/pwanimate-chat.js
for f in tests/spark/*.cjs; do node "$f"; done

# Cleanup disposable containers
bash scripts/test-sals03-wp2-postgres.sh cleanup
```

## F. Acceptance checklist

- [x] Enrolled-unit tracking proposal is a persisted `PendingAction`, surfaced
      in the chat response and conversation serialization — never prose-only.
- [x] Explicit Approve / Decline controls attached to the originating
      assistant message (live + reload via poll placement).
- [x] Existing `ActionService` confirm/cancel contract reused; ownership,
      consent revision, enrollment/semester and source-message checks intact.
- [x] No unit-linked activity without authorization; trigger message never
      imported; duplicate sessions/prompts prevented (existing idempotency).
- [x] Decline → tutoring continues, no re-prompt in same conversation.
- [x] Typed interactions: composer is the answer field; no second input;
      `interaction_id`/`resume_run_id` preserved; original-run continuation.
- [x] Mixed interactions: choices in panel + composer text submitted
      atomically; server validation unchanged.
- [x] Choices-only, confirm and group interactions unchanged.
- [x] Drafts persist across reconnects/reload; failures preserve typed text;
      double-submit and accidental new turns blocked.
- [x] Unified surface: composer bg/border/radii, single seam, theme tokens,
      safe areas and keyboard behavior preserved.
- [x] Consent state survives reloads, interrupted streams and reconnects.
- [ ] Live latency/UX verification on PostgreSQL deployment (owner).

## G. Follow-up polish (post-review)

- **Active state spans the merged surface:** composer `:focus-within` now
  also highlights the merged panel's border (`--primary`, light/dark), so the
  unified surface reads as one focused element instead of only the lower box.
- **Send button during pending input:** `is-generating` (stop glyph) now only
  applies while a response is actually generating. While Spark waits for
  input, the button shows a normal Send — highlighted "Send answer" when the
  composer/panel can produce an answer (typed text or selections), plain
  paused state otherwise. Panel selection changes refresh the state too.
- **Composer cleanup on submit:** a successful interaction answer now always
  clears the composer (previously only when the value still matched the sent
  text, which could leave text typed mid-flight behind) and refreshes the
  send-button state.

## H. Known limitations

- The consent card attaches to the *first* assistant message after the
  triggering user turn; interim `say` rows are skipped. If detection fires
  on a turn whose reply is still streaming when the poll runs, the card may
  briefly appear at the transcript end until the next poll re-places it.
- Group wizards intentionally keep their standalone block (multi-step
  Back/Next flow); only single-question typed/mixed interactions merge.
- `markdown-math.cjs` has one unrelated KaTeX annotation failure noted above.
- One pre-existing offline-suite failure in `test_interaction_lifecycle`
  (teaching service DB access with mock user) — not introduced here.
- Live end-to-end verification (real model stream + mobile keyboard) requires
  the owner's PostgreSQL environment.

## I. Multiple-choice question recovery (follow-up fix)

Reported symptom: Spark produced only free-text "explanation" questions —
no tappable multiple-choice prompts and no adaptive next question.

Root cause: choice interactions depend on the planner emitting structured
`question_type`/`question_options`. Weak planners (e.g. qwen3-8b) either omit
the fields, supply options without a type, or embed choices as prose
(`A) ... B) ...`) — all of which silently degraded to `text` interactions.

Fixes:
- `pwanimate/services/interactions.py`: new `extract_inline_choices()`
  recovers a trailing run of lettered/numbered marker lines into
  `[{id,label}]` options and strips them from the prompt. Mid-text numbered
  steps are ignored (only a trailing block counts). `normalize_group_questions`
  now upgrades text questions with recoverable choice blocks to
  `single_choice`.
- `pwanimate/orchestrator/agent.py`: after plan parsing, options supplied
  without a choice type default to `single_choice`; prose-embedded choices
  are extracted; planner prompt now explicitly prefers `single`/`multi`
  with 3–5 options for check/quiz/diagnostic questions.

Continuation was already correct: `claim_run` appends
`Student clarification: <answer>` to the resumed query, `answered_questions`
dedupes repeats, and a re-paused run emits the next adaptive interaction on
the same run row.

Tests: 6 new cases in `tests/sals_wp3/test_interaction_lifecycle.py`
(inline extraction, mid-text step safety, group upgrade, prose→single_choice,
options-without-type, resumed-run next question). 22 tests run; the single
remaining error is the pre-existing `test_complete_question_group` DB access
issue that also fails on clean HEAD.

## J. Study-tracking consent: detection recall fix (blocking acceptance)

Reported symptom: the Approve/Decline card had never appeared in real use;
Spark only asked in prose.

### Root cause

Verified against the live dev database (read-only + rolled-back probe): the
consent infrastructure was never the problem — `SparkAcademicTrackingPreference`
was `ask`/rev 0 and **zero** `PendingAction`s, `SparkUnitLearningRecord`s or
`SparkUnitActivity`s existed for the account. Detection never fired because:

1. **Vocabulary gap** — `SUBJECT_TOPICS['linear algebra']` had only 9 phrases
   (matrices, determinants, linear equations, ...). "equations of planes in
   3D", eigenvectors, row reduction, span/basis/rank, dot/cross product were
   all invisible. SMA B203's `academic_unit.description` is **empty**, so the
   catalog-phrase fallback contributed nothing.
2. **`MEANINGFUL` gate too narrow** — only imperative verbs (explain/teach/
   solve/...) qualified. Real student phrasing — "Quiz me on...", "Can we go
   through...", "It's linear algebra...", "I am preparing for my test" —
   exited detection before topic matching.
3. **Ambiguity silently abstains** (kept by design): shared terms like
   'recursion' claimed by both OOP and DSA enrollments produce `ambiguous`
   and no proposal.

### Fixes

- `pwanimate/services/unit_learning.py`
  - `MEANINGFUL` expanded: quiz/test me, practice, revision/review, past
    papers, homework, assignment, coursework, exam, problem sets, go through,
    can we, let's, it's/this is, i'm taking/doing/studying/learning/revising/
    preparing, we'are doing/studying/covering/learning, i am preparing/
    revising, i struggle with/have trouble/don't understand/don't get,
    questions on/about/from, show me, walk me, give me, help me, study,
    learn, tutorial.
  - `SUBJECT_TOPICS` expanded per enrolled unit: full Linear Algebra I
    coverage (planes/lines in 3D, eigenvectors, row reduction, span/basis/
    rank, dot/cross/inner product, symmetric/skew matrices, diagonalization,
    linear transformations, parametric/symmetric equations), Calculus II
    (series/sequences, mean value theorem, optimization, related rates,
    volumes, polar coordinates), Probability & Statistics, OOP/OOAD,
    Data Structures & Algorithms, PC Hardware. Multiword phrases preferred
    to limit false positives; bare 'graph' deliberately excluded.
- `pwanimate/orchestrator/service.py` — system instruction now states
  academic tracking consent is collected only by the persisted card; the
  model must not ask for tracking permission in prose.
- `static/js/pwanimate/pwanimate-chat.js` — consent card labels changed to
  the specified **Start Tracking / Not Now**; added `graduation-cap` Lucide
  icon + `spark-interaction-head`/`spark-interaction-kind` header matching
  sibling interaction blocks.

### Verification

- Live dev DB probe (rolled back): `process_message` on
  "Spark, teach me equations of planes in 3D." now produces
  `track_academic_unit` PendingAction for SMA B203 / Semester 1 2026/2027
  with correct enrollment_id, semester_id, source_message_id,
  tracking_revision. Residual rows: 0.
- `Units.detect` on the account's real message history: planes-in-3D,
  "Quiz me on ... Linear Algebra", "It's linear algebra...", "Can we go
  through the problem sets..." all resolve to Linear Algebra I; non-subject
  chatter still ignored; 'recursion' remains honestly ambiguous (OOP vs DSA).
- Tests: 3 new cases in `tests/sals_02/test_foundation.py` (empty-description
  vocabulary detection, conversational phrasing gate, non-subject negatives).
  sals_02 + sals_03_wp1 + sals_03_wp2: 149 tests OK (15 PG-skipped).
  `tests/spark/unit-learning.cjs` repaired (stale labels + mock gaps) and
  passing; all spark cjs contracts pass; `node --check` clean; collectstatic
  re-run.

### Remaining limitations

- Units with empty descriptions rely solely on subject vocabulary; highly
  idiosyncratic phrasing may still abstain (conservative by design).
- Cross-unit ambiguous topics (e.g. 'recursion' across OOP/DSA) still
  abstain — the academic poll surfaces an ambiguity note instead.
- `test_orchestrator` retains 18 pre-existing prompt-assertion failures
  unrelated to this change.
