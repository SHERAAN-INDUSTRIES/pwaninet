# Spark activity privacy and missing memory approval fix

Date: 2026-11-11. Scope: existing PwaniNet Spark only. No WP4, assessment, model, migration, deployment, or production-data work.

## Confirmed causes

1. `pwanimate/orchestrator/agent.py:StudyAgent.run` requested the planner's inner reasoning and published `thinking`, `update`, `say`, and per-tool `reason` fields. Initial and resumed request handlers persisted `say` as ordinary `agent_interim` assistant messages. `static/js/pwanimate/activity-trail.js:Trail.addThinking` displayed this narration. These paths could expose internal reasoning and duplicate the final answer's content.
2. `pwanimate/ai/providers/groq.py` and `openrouter.py:_parse_response` substituted reasoning for empty visible content. Gemini joined thought-marked text into both streaming and nonstreaming answers. Provider-native reasoning must never become visible answer text.
3. `pwanimate/orchestrator/service.py:finish_agent` and `_run_tool` both announced final preparation. Journal deduplication included timestamps, so equivalent repeated events could receive different deduplication keys. Raw agent traces were attached to response metadata.
4. The original `pwanimate/tools/domain/memory.py:stated_preference` required the entire message to match a single canonical preference. **“Spark, I prefer short explanations and bullet points.” failed even with memory enabled.** It rejected the address, “short explanations,” and the conjunction. Narrating an intended tool call did not prove execution; proposal creation depended on optional model tool selection and could also silently fail eligibility/rate-limit checks.
5. The existing approval renderer already read WP3 records. Its GET fetched the latest 100 facts across conversations and then filtered client-side; it could miss older pending records. Refreshing also discarded an in-progress edit.

The user reports memory was enabled. This fix does not attribute their incident to disabled consent. No production/local user database was inspected, so the original run's actual tool invocation, effective deployed flag, stored proposals and rate-limit state cannot be independently confirmed here. Source confirms tool registration in `pwanimate/tools/registry.py:get_default_tool_registry` and trusted source injection in `StudyAgent.run`. No `SPARK_MEMORY_PROPOSALS_ENABLED` override was found in the inspected `pwaninet` Python settings; the tool's default remains true. Runtime overrides can differ.

## Execution and privacy changes

- `activity.py:public_event/public_events` project public progress onto fixed server labels, known statuses and bounded fields. Private narration kinds are suppressed; tool arguments/details/results and model-authored checklist findings are excluded. Checklist progress retains numbered steps and outcomes. Clarifications and intentional user approval summaries remain usable.
- Apply the projection to emission, initial/resumed capture, journal append, JSON/SSE replay, terminal activity, API conversation history and template history. Legacy interim messages are hidden from display and model history, without deleting records. Legacy activity is filtered on read.
- Fixed event identifiers and timestamp-independent journal deduplication retain running-to-terminal transitions. The duplicate `writing_answer` emission in `finish_agent` is removed. The frontend suppresses old interim narration and reuses an existing final row when its persisted message ID is replayed.
- Remove raw agent trace metadata; omit internal memory/retrieval traces and reasoning/prompt fields from response serialization. Public plan summaries expose numbered titles rather than model-authored findings.
- Gemini thought parts are excluded before token callbacks and parsing. Groq/OpenRouter reasoning-only output now fails closed through the existing provider error/fallback path. Raw reasoning metadata is not returned; token accounting remains available.
- Planner instructions no longer solicit hidden reasoning/interim chat. Answer instructions distinguish preference acknowledgement from authenticated approval. No heuristic can guarantee the content of arbitrary future model prose; the deterministic protections cover explicit reasoning channels, event fields, replay and metadata. Already persisted historical final-answer text is not bulk rewritten.

## Reliable proposal flow

`PwanimateChatView._post_chat` → authenticated, persisted user message → `propose_from_message` → existing `ProposePersonalMemoryTool` → `PersonalMemoryService.propose` → owner-scoped pending records → `GET /api/pwanimate/memory/?conversation_id=<uuid>&status=proposed` → `refreshMemoryProposals/_memoryApprovalBlock` → explicit authenticated PATCH approve/edit/reject.

- Server-controlled execution does not depend on the model selecting or describing the tool. It applies to the existing chat entry point, including requests that use conversational/study/document routing, without a second agent.
- Match only whole explicit allowlisted preference requests, including the reproduction. Embedded document text, sensitive unrelated facts, bare inferred preferences and conflicting choices are rejected. The optional model tool still rechecks the real owner-scoped user message using the server-injected ID.
- For the reproduction, create **two separate pending facts**: response length concise; response format bullet points. Each has its own Approve/Edit/Reject card and version. No bundled approval and no automatic approval.
- Frequency is bounded to **one explicit source request per 24 hours, at most two facts from that same request**. This deliberately adjusts the former one-fact-per-day rule so two related explicit preferences can each be reviewed. Other requests remain rate limited. Existing approved/pending duplicates are reused rather than recreated; a rejected source request cannot resurrect proposals on HTTP retry. Rejected records keep the existing WP3 content-clearing behavior.
- Return explicit outcomes for disabled feature, disabled consent, duplicate, rate limit and unavailable proposal service. Chat shows a Memory settings link when appropriate. Disabled consent is re-read from GET before showing controls; enabling it does not retroactively save the original preference.
- The optional GET filters apply owner scope, conversation and pending status **before** the existing limit; unfiltered settings GET retains compatibility. The renderer uses these records, never an LLM's prose. Initial completion, initialization/focus and resumed completion retain existing refresh hooks. Scope comparisons normalize IDs. Refresh preserves active edits/in-flight requests.
- Existing `PersonalMemoryService.change` continues to enforce authenticated ownership, fact version, current consent revision and settings. Editing returns the fact to proposed status. Only approved, owned facts enter recall; rejected/proposed facts do not. No change to the underlying consent service, assessment records or document policy.
- Existing opaque `--pwanimate-bg` memory surface, wrapped controls and mobile/theme layout are reused; no CSS redesign.

## Actual verification

| Check | Actual result |
| --- | --- |
| `.venv/bin/python -m tests.sals_wp3.run_offline` | **74 passed**, 0.282 seconds in the final recorded run. Uses WP0 standalone settings and network/provider/worker guards; no database connection or migrations. |
| Expanded offline run including the whole gateway module | **117 executed; 116 passed, 1 failed.** Existing `test_gemini_successful_generation` expects `gemini-2.5-flash`; configured resolution yields `gemini-3.8-flash`. Unrelated model mapping left unchanged. This broader run preceded subsequent additional regressions; it is not represented as a final full-suite pass. |
| `node tests/spark/activity-privacy.cjs` | PASS: legacy activity filtering, concise labels, checklist sanitization, final durable-message deduplication. |
| `node tests/spark/memory-approval.cjs` | PASS: approval/rejection/edit, rapid clicks, stale response, scoped persisted proposals, two separate facts, reload/resumption refresh, disabled-memory route, preserved edit and opaque theme token. |
| `node tests/spark/stream-collapse.cjs` | PASS: fresh/resumed streaming collapses activity on first answer chunk; later manual reopening is preserved. |
| `node tests/spark/interaction-flow.cjs` | PASS: single/group explicit Send, mixed input types, required validation, ordered IDs, failed retry and duplicate clicks. |
| `node tests/spark/quiz-flow.cjs` | PASS: existing quiz sequential Send, reload outcomes, immediate lock, error/retry and theme token regressions. Assessment logic unchanged. |
| `node --check` on both changed frontend files; `git diff --check` | PASS. |
| `.venv/bin/python -m tests.sals_wp0.run` | **BLOCKED:** Docker socket permission denied; runner exited 1 before database creation/tests. No database-backed pass claimed. |

Offline coverage includes the exact preference sentence, tool omission, disabled feature/consent, duplicates and rate limit, rejected-source retry, per-fact approval/edit/rejection/version/revision checks with mocked storage, owner-filtered memory API requests, activity/metadata/history/journal filtering and provider streaming reasoning channels. Provider responses are mocked, with real transports blocked.

`tests/sals_wp3/test_database.py` adds a real persisted combined-request/API approval-rejection/retry/rate-limit regression. It and existing PostgreSQL ownership/consent-race tests remain unexecuted here. The updated database-backed activity/reconciliation tests also require isolated PostgreSQL. The new offline runner is `tests/sals_wp3/run_offline.py`.

DOM checks use the actual frontend methods in Node with a fake DOM; they are not a live browser/phone visual or keyboard test. Live LLM behavior and actual deployed feature flags were not exercised. Existing WP3 migrations must already be applied for runtime memory persistence; no migration was created or applied for this task.

## Files changed in this task

Paths below describe this task's edits, not the entire pre-existing dirty working tree.

### Runtime/backend

- `pwanimate/ai/providers/gemini.py`
- `pwanimate/ai/providers/groq.py`
- `pwanimate/ai/providers/openrouter.py`
- `pwanimate/api/agent_runs.py`
- `pwanimate/api/serializers.py`
- `pwanimate/api/study_assistant.py`
- `pwanimate/api/views.py`
- `pwanimate/orchestrator/activity.py`
- `pwanimate/orchestrator/agent.py`
- `pwanimate/orchestrator/service.py`
- `pwanimate/orchestrator/types.py`
- `pwanimate/services/agent_execution.py`
- `pwanimate/services/agent_journal.py`
- `pwanimate/services/agent_plans.py`
- `pwanimate/services/conversation.py`
- `pwanimate/tools/domain/memory.py`
- `pwanimate/views.py`

### Frontend

- `static/js/pwanimate/activity-trail.js`
- `static/js/pwanimate/pwanimate-chat.js`

### Tests/documentation

- `pwanimate/tests/test_agent_activity.py`
- `pwanimate/tests/test_agent_reconciliation.py`
- `pwanimate/tests/test_gateway.py`
- `tests/sals_wp3/test_activity_security.py` (new)
- `tests/sals_wp3/test_chat_memory.py`
- `tests/sals_wp3/test_database.py`
- `tests/sals_wp3/run_offline.py` (new)
- `tests/spark/activity-privacy.cjs` (new)
- `tests/spark/memory-approval.cjs`
- `docs/spark/sals/wp3/ACTIVITY_AND_MEMORY_UX_FIX_REPORT.md` (new)

Pre-existing changes, including models/migrations and unrelated SALS work, were preserved. This task did not edit those models/migrations, access production data, call a live provider, push or deploy. Stop here; WP4 remains unauthorized.
