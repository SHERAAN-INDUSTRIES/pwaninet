# Spark interaction lifecycle and Markdown/math fixes

Date: 2026-11-12 (Africa/Nairobi). Scope: existing PwaniNet Spark only. This report covers the interaction-lifecycle work and the subsequent interactive Markdown/LaTeX request together.

## Confirmed causes

- `pwanimate/services/interactions.py:normalize_group_questions` referenced undefined `INPUT_SINGLE`/`INPUT_MULTI` names. A one-question array also collapsed to an empty list. Pending interaction creation overwrote contract fields/version, rather than keeping the existing pending contract authoritative.
- `pwanimate/orchestrator/agent.py:StudyAgent.run` preferred the single `question` field even when the model also returned a complete `questions` group. It did not supply persisted answered-question identities to the planner or prevent a second generic question asking for memory consent.
- `pwanimate/api/agent_runs.py:_claim_and_tail` could re-arm a running attempt on a repeated SSE POST. `_terminal_payload` treated a re-paused run's `result_message` as a final ordinary answer with awaiting flags false. This explains how a question could appear as answer text as well as activity, as shown in the supplied screenshot.
- The frontend waited for full resumed generation to finish before closing the input block. Historical waiting events could restore stale interaction state. `showTypingIndicator` reset the original trail pointer; finding the old row after moving the trail prevented removal of the empty pause row. Form/Enter submission while generating could invoke Stop.
- `static/js/pwanimate/pwanimate-chat.js:_buildInteractionBlock` and `_questionControls` inserted question/option text with `textContent`, bypassing the existing Markdown/KaTeX renderer. Its shared renderer also processed math inside code spans/fences and had an unsafe raw-HTML path when Marked was present but DOMPurify was unavailable.
- Actual multi-choice controls did not emit selections when custom answers were disabled. Confirm/Decline selection did not visibly update. Both affected the required mixed-input flow.

## Implemented behavior

### One persisted interaction and original-run continuation

- Group normalization uses existing model constants, deduplicates prompts and preserves stable unique IDs. A one-question array becomes one typed question. A complete group takes precedence over a redundant single-question field.
- `create_interaction` locks the run, validates ownership/conversation/status/expiry, and returns an existing pending contract unchanged. Answered question identities prevent recreating the same question. Canonicalization preserves math operators, Unicode superscripts and case inside TeX/code delimiters.
- Planner context includes persisted answered questions. Known memory-consent question forms are excluded from generic clarification; WP3 memory approval stays with persisted fact cards and authenticated Approve/Edit/Reject. No clarification answer grants personal-memory approval.
- Re-pausing updates the original run and checkpoint under locks; terminal or unrelated runs cannot be reopened. Real action approval takes precedence over an otherwise unanswerable simultaneous clarification. The existing ActionService approval authority is retained.
- Submissions keep the original run ID, interaction ID and saved continuation. The server validates run/interaction pairing. Replayed identical SSE answers attach to existing execution without replacing the attempt, lease, continuation or dispatching again; conflicting answers are rejected.
- Additive owner-scoped `GET /api/pwanimate/agent-runs/<uuid>/stream/` reconnects without claiming or executing. SSE emits `interaction_accepted` after the persisted claim. The frontend removes the active block and its draft on that acknowledgement, before final generation finishes. Validation/transport failures before acceptance preserve answers for retry.
- Reconnection after transport failure checks authenticated run status and accepted interaction IDs, then uses GET streaming; it does not replay an independent chat POST. Enter/form events do not cancel an in-flight generation. The explicit Stop control remains.
- The original activity trail survives continuation; its empty pause row is removed after moving the trail to the final answer. Journal waiting events are progress only; forms come from authoritative terminal/status interaction data. There is one concise clarification/approval record per run in each trail.
- Paused API envelopes do not repeat their question as an ordinary answer. Waiting terminal envelopes return current interaction metadata rather than a stale result message. Initial pauses save the result pointer, claims/re-pauses clear stale pointers, and final status plus result pointer are published atomically.
- The inline worker fallback uses the same durable duplicate-delivery guard as Celery. Interrupted runs settle their stream instead of waiting indefinitely. Quiet streams can emit keep-alives.

### Existing Markdown, KaTeX and sanitization reused

- Active/restored question bodies and option labels call `renderMarkdownWithMathAndCode`. Activity Markdown delegates to that same method when the chat controller is available, falling back to safe plain text otherwise.
- Supports existing `$...$`, `$$...$$`, `\(...\)` and `\[...\]` delimiters. Math is protected before Marked processes it, so `\\` matrix row separators, fractions, subscripts and superscripts reach KaTeX unchanged.
- Fenced and inline code are protected from math extraction and restored before Marked parsing. No new Markdown/math engine or dependency was introduced.
- Missing/unsupported DOMPurify fails closed to escaped text. Rendered HTML is sanitized before math substitution and again after the complete result is assembled. KaTeX explicitly uses `trust: false`, bounded expansion and bounded sizing. Malformed TeX uses existing safe error/fallback behavior.
- The existing opaque `--pwanimate-bg` card surface is retained. Small scoped CSS rules let wide math/code scroll inside the card and avoid inheriting prose `pre-wrap` formatting. No theme or chat redesign.
- Updated existing asset cache tokens for chat JS, activity JS and CSS. New assets are picked up on a full page load; active old SPA controllers are not forcibly replaced.

## Actual verification

| Command/check | Result |
| --- | --- |
| `.venv/bin/python -m tests.sals_wp3.run_offline` | **90 passed**, 0.385 seconds in the final recorded Python run. WP0 settings plus provider/network/worker isolation; no database access. |
| `node tests/spark/markdown-math-unit.cjs` | PASS with the actual vendored Marked and KaTeX: matrix rows, all four delimiter forms, fractions/superscripts/subscripts, literal inline/fenced code, Markdown lists, malformed TeX, shared activity rendering, untrusted-math settings and required sanitizer/fail-closed boundaries. DOMPurify is mocked in this unit test. |
| `node tests/spark/lifecycle-resume.cjs` | PASS: acknowledgement closes block before generation ends, original run identity, rapid-click lock, single final response, retained trail/removed empty pause row, interrupted SSE GET reconnection and failed-validation answer preservation. |
| `node tests/spark/question-controls.cjs` | PASS using actual control code: restricted multiselect, restored IDs and explicit visible Decline selection. |
| `node tests/spark/interaction-flow.cjs` | PASS: one/group questions, mixed input payloads, explicit Send, all required answers, ordered IDs, Back, failed retry, rapid clicks, successful removal and restored mathematical prompt delegating to the shared renderer. |
| `node tests/spark/memory-approval.cjs` | PASS: persisted proposals, approve/edit/reject, repeated-click protection, stale response, disabled memory and reload/restore scope. |
| `node tests/spark/stream-collapse.cjs` | PASS: fresh/resumed streams collapse activity once at the first answer chunk; manual reopening remains available. |
| `node tests/spark/activity-privacy.cjs` | PASS: private narration/detail filtering, concise summaries and durable final-row deduplication. |
| `node tests/spark/quiz-flow.cjs` | PASS: existing quiz UI regressions. Assessment logic was not changed. |
| JS syntax checks on both production JS files, browser runner and fixture; `git diff --check` | PASS. |
| `.venv/bin/python -m tests.sals_wp0.run` | **BLOCKED** before database creation: permission denied accessing the Docker socket. |
| `node tests/spark/markdown-math.cjs` | **BLOCKED**, exit 2: Chrome process/socket permissions prevent launching the disposable browser. No real DOM/XSS/layout assertions executed. |

Offline lifecycle tests include grouped types/IDs, repeated/pending question reuse, mathematical question identity, memory-consent suppression, answered-question continuation with a mocked planner, group precedence, mismatched run IDs, accepted/conflicting replays, duplicate worker delivery, SSE acknowledgement order, paused-answer separation and re-paused terminal metadata.

A PostgreSQL integration regression in `tests/sals_wp3/test_database.py:InteractionLifecycleDatabaseTests` checks pending contract reuse, complete grouped validation, the original run's continuation/context, duplicate submission rejection and exactly one answer message. It remains unexecuted here, as do real lock/concurrency and migration-backed checks. No SQLite substitute was used.

## Remaining limits

- Real PostgreSQL locking, worker races and database/API integration need the existing isolated WP0 harness on a host with permitted Docker access. Mocked transaction tests do not prove PostgreSQL concurrency behavior.
- `tests/spark/markdown-math.cjs` and its browser fixture are ready to exercise the actual vendored DOMPurify, malicious HTML/URLs, malformed TeX, restored controls and light/dark 390px/1280px layouts. They must run on a host where Chrome can launch. Unit sanitizer-boundary assertions are not represented as a full browser XSS pass. Physical mobile/keyboard checks also remain outstanding.
- The question key handles canonical repeats, not arbitrary semantic equivalence in every language. The memory-consent guard covers the known consent wording, alongside planner/final-answer instructions; there were no live LLM calls or claims of exhaustive natural-language behavior verification.
- Existing persisted transcripts remain intact. No retroactive rewrite of historical ordinary answer text or private run state was performed. Existing compatible database tables/migrations must already be available at runtime.

## Files changed during this fix

Pre-existing dirty work was preserved. These are this fix's files, not a claim that every working-tree change originated here.

Backend/runtime:

- `pwanimate/services/interactions.py`
- `pwanimate/services/agent_runs.py`
- `pwanimate/services/agent_execution.py`
- `pwanimate/tasks/agent_runs.py`
- `pwanimate/api/agent_runs.py`
- `pwanimate/api/urls.py`
- `pwanimate/api/views.py`
- `pwanimate/orchestrator/agent.py`
- `pwanimate/orchestrator/service.py`
- `pwanimate/orchestrator/types.py`

Frontend and asset references:

- `static/js/pwanimate/pwanimate-chat.js`
- `static/js/pwanimate/activity-trail.js`
- `static/css/pwanimate/pwanimate.css`
- `pwanimate/templates/pwanimate/index.html`
- `pwanimate/templates/pwanimate/partials/chat_content.html`
- `templates/base.html` (activity asset cache token only)

Tests/documentation:

- `tests/sals_wp3/test_interaction_lifecycle.py` (new)
- `tests/sals_wp3/test_database.py`
- `tests/sals_wp3/run_offline.py`
- `tests/spark/interaction-flow.cjs`
- `tests/spark/lifecycle-resume.cjs` (new)
- `tests/spark/question-controls.cjs` (new)
- `tests/spark/markdown-math-unit.cjs` (new)
- `tests/spark/markdown-math.cjs` (new)
- `tests/spark/fixtures/markdown-math.browser.js` (new)
- `docs/spark/INTERACTION_LIFECYCLE_AND_MATH_FIX_REPORT.md` (new)

No model, migration, SALS grading, Workspaces, provider configuration or dependency changes were made in this fix. No production access, live provider calls, push or deployment. Stop here.
