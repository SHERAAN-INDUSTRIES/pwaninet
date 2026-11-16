# SRI-01 repairs — implementation and verification

Date: 2026-11-14. These changes follow the diagnosis-only SRI-01 documents; those documents remain historical evidence. Repairs are implemented locally, with isolated verification described below. Production deployment, PostgreSQL concurrency and actual desktop/mobile browser acceptance remain UNVERIFIED.

No application models/migrations, live databases, configuration files or historical student records were changed. No services restarted, credentials printed or live LLM calls made. Tests created synthetic rows only in disposable SQLite using `tests.spark_memory.settings`; generation and broker work were mocked. The existing large dirty working tree was preserved. No roadmap milestone was marked complete.

## Fix 1 — effective execution source

Changed `pwanimate/services/agent_execution.py`; added `services/execution_source.py`.

Ordinary finalization now receives the owned effective source: the current accepted answer message, otherwise the post-decision academic execution receipt, otherwise the original input. Sources must belong to the same owner/conversation and have the correct role; prospective receipts must have `academic_execution` task identity. Original run identity is preserved. Teaching still uses its own interaction/boundary receipts; question-form academic responses are not forced into TeachingService.

Regressions in `tests/sals_wp4/test_sri_repairs.py::ExecutionSourceTests`: question-form held consent produces one ordinary exposure from the prospective source; original source remains unauthorized; same record/session/term; repeat finalization produces no duplicate turn; no concept evidence/mastery; document-mapped receipt; pause/revoke during generation; foreign boundary rejected. Initial combined check: 17 tests OK, one PostgreSQL skip; document variant separately passed after addition. Four specified Node scripts passed.

Assumption: an answered interaction's saved answer message supersedes an earlier lesson boundary for a later ordinary answer. This matches existing continuation provenance. Session creation and request activity remain distinct from completed exposure. Actual owner OOP cause remains unverified; this repairs the reproduced source defect, not a claimed live trace.

## Fix 3 — one completion interpretation

Added `pwanimate/ai/completion.py`. Changed gateway/types.py, orchestrator/types.py and service.py, providers/groq.py and openrouter.py, services/teaching.py and agent_execution.py, api/views.py, and chat JS incomplete-response notice.

Canonical token exhaustion is `length`; max_tokens/MAX_TOKENS normalize to it. Only explicit stop with nonempty content is completed. Missing/unknown/filter/error/interrupted/cancelled outcomes cannot earn completed learning. Authored deterministic teaching explicitly reports stop. Continuation remains bounded to one for substantive answers; pure greetings deliberately use no continuation to satisfy the one-answer-call ceiling. Durable nonwaiting partial runs become interrupted. Cancellation is rechecked after final generation. Public incomplete-output activity persists with the partial answer so reload is not silently presented as completed learning. Initial request traces now retain safe finish/completion metadata.

Regressions: canonical vocabulary and unknown status; stop versus repeated-limit bounded continuation; partial ordinary response remains unfinalized/interrupted; all native JSON/stream parser pairs; cancellation during generation. Two legacy tests assumed an unspecified finish reason was success: `tests/sals_wp4/test_unified.py::test_document_unit_page_and_completed_turn` and `tests/sals_03_wp2/test_teaching.py::test_model_fallback_uses_one_answer_path_without_planning`. Their successful-generation fixtures now explicitly report stop, rather than weakening unknown handling.

After the first fixture correction: 21 tests OK, one PostgreSQL skip; Node scripts passed. A later broad run found the second fixture (177 tests, one failure, ten skips); its corrected cache test and related suites passed in the 70-test focused rerun. Final combined result is recorded below.

Assumption: deterministic controller-authored text is an explicitly completed segment, even without a provider. Unknown provider completion is never inferred from fluent prose or a closed code fence. Live Java-cutoff attribution remains unverified.

## Fix 4 — terminal evidence and durable application outcome

Changed providers/groq.py, openrouter.py and gemini.py; tasks/agent_runs.py; services/agent_execution.py; and the affected existing services/followups.py consumer.

Adapters retain missing terminal finish information as unknown and expose `terminal_finish_received`; EOF no longer manufactures stop. The worker returns application_success only when the durable run is completed, its result pointer exists and the execution payload explicitly completed. Returned error payloads explicitly deny application success, including failure to mark the row failed. Duplicate delivery remains fenced; recovery never blindly replays a leased-out result.

Final assistant persistence/result publication runs in a short conversation/run-fenced transaction after generation, rejecting superseded attempts. An old failure cannot fail a newer attempt; failure publication also locks the run against explicit retry races. R1 follow-up execution/recovery now checks interrupted/cancelled durable state before consuming a result pointer; known failed offers retain their existing retry path. A partial result is not converted into a consumed successful offer merely because it has a message ID.

Regressions: clean EOF for all three adapters; existing gateway exception-after-chunks tests; worker error with failed run; failure while marking failed and duplicate delivery; lease interruption preserves pointer without dispatch; partial follow-up execution and replay remain interrupted. Intermediate combined check: 29 tests OK, one PostgreSQL skip; four Node scripts passed.

Assumption: Celery's transport SUCCESS is not student-task success. Application consumers must inspect the explicit payload and durable run state. Real worker termination, broker failures, concurrent finalization and process-version attestation remain unverified.

## Fix 2 — persisted teaching segment plus pending interaction

Changed api/agent_runs.py, orchestrator/types.py, services/agent_execution.py, api/serializers.py; added services/teaching_output.py.

A waiting durable run can now return an eligible persisted instructional segment together with its separately pending owned interaction. Eligibility is backed by an owned teaching receipt, matching persisted response and complete-answer metrics. Only a public field allowlist is projected. Result metadata is keyed to the exact result message and turn. Ordinary generic agent pauses still hide interim/planner text. JSON serialization no longer blanks a validated completed teaching segment solely because a next question is waiting. Restored message serialization includes eligible teaching metadata, and existing durable message-ID UI deduplication is retained.

`TeachingTransportTests` uses the actual authenticated HTTP SSE endpoint with real consent/teaching/persistence, mocked generation/dispatch, and verifies re-pause, reconnect, stable message/question IDs, sources/citations, in-memory JSON/SSE answer parity and serialized history. A separate test prevents exposure of generic paused output. Two transport tests passed.

Test limitation: this restricted Python 3.14 runner hangs on native asyncio default-executor shutdown, reproduced independently with a trivial to_thread call. The HTTP/SSE test schedules disposable ORM reads inline and mocks unrelated post-commit background callbacks. It validates actual response construction, not cross-process scheduling or browser rendering. Interrupted exploratory runs were terminated only in their disposable test processes; no application service was signalled.

Assumption: a completed explanatory segment may coexist with a waiting logical run. That does not certify a completed lesson sequence or mastery. Real reconnect and rendering acceptance remain unverified.

## Fix 5 — canonical header synchronization

Changed `static/js/pwanimate/pwanimate-chat.js`; extended `tests/spark/study-context.cjs` and lifecycle-resume.cjs.

All relevant resume terminal outcomes, including re-pauses and activity-trail decisions, refresh canonical context. Standalone academic decisions and generic action decisions refresh it too. Refresh is guarded after async decoding/detail fetch by conversation identity, request serial and nondecreasing binding generation. No-selection/ambiguity clears stale header state while preserving server-side parked work. Canonical pause/renewal semantics are unchanged; no lifecycle event, automatic resume or placement redesign was added.

Node checks cover existing approval/decline/retry/restoration behavior plus re-paused teaching refresh, conversation switch during JSON decode, stale generation rejection and clearing an absent selection. The required WP4 suite passed: 14 tests, one PostgreSQL skip. All four Node scripts passed repeatedly.

This is the requested consistency repair; the owner's exact missing-header cause remains unverified. Desktop/mobile share this helper but actual viewport/browser acceptance has not occurred. No claim is made that a missing header meant an absent backend session.

## Fix 6 — bounded greeting continuity

Added services/casual_context.py; changed orchestrator/service.py and api/views.py.

Pure compound/slang greetings reach a single bounded conversational answer before teaching, selected-document resolution, planner, tool selector or tools. The chat preflight does not start/update a study session or turn a greeting into a pending consent execution; document attribution and fact-vector recall are skipped for this route. Existing pending work remains owned and parked.

A read-only helper supplies at most one unambiguous recent session backed by finalized authorized academic activity, bounded to twenty turn candidates in the last thirty days. It honors academic opt-out/revision/eligibility and suppresses ambiguous sessions. It supplies approved response preferences independently of incidental greeting-word overlap and honors personal-memory opt-out separately. It does not inspect raw transcripts/digests, activate sessions or grant writes. Context explicitly labels exposure/explanation as distinct from mastery.

Regressions: Wagwan/compound/basic greetings across new-chat, active Study Mode and open-PDF orchestration; zero teaching/planner/document/router/selector calls; one answer; no history, real paused exposure, foreign owner, disabled tracking and multiple sessions; read-only query assertion; independent approved preferences and opt-out. Combined greeting/WP4 check: 17 tests OK, one PostgreSQL skip; additional projection/preference/greeting checks: eight tests OK. Four Node scripts passed.

Assumptions: thirty days is the bounded relevance window, and ambiguous sessions produce a generic greeting rather than an arbitrary continuation suggestion. Suggestions require finalized activity; a session row or request alone is insufficient. Actual conversational quality remains browser/provider acceptance, not certified by mocked answer tests.

## Fix 7 — historical read posture and bounded projection

Changed services/unit_learning.py and context/types.py; added context/academic_history.py.

Existing named historical retrieval remains separate from new-write consent. The context explicitly states that a historical read grants no writes. Oversized academic prompt context now uses complete bounded JSON with explicit truncation, preserving identity, scope and latest completed activity rather than cutting a JSON string mid-record at 18,000 characters. No missing historical events are manufactured.

Regressions compare finalized source/turn, summary and actual formatted ContextPackage; new-chat ask writes absent while named history survives; stale revision retained for named history; inactive enrollment read-only; disabled recall and foreign owner; multiple terms require clarification; oversized context stays valid JSON and retains latest activity/posture. Combined WP4/projection check: 17 tests OK, one PostgreSQL skip; final additional checks passed after adapting the multiple-term fixture to the repository's unique user/unit enrollment identity.

Assumption: inactive/old write scope does not delete authorized history. Live OOP absence versus filtering/model disregard remains unverified; no speculative projection rewrite or raw recall restoration was attempted.

## Fix 8 — versioned snapshots

Added services/run_snapshots.py; changed services/agent_runs.py, agent_execution.py and orchestrator/types.py.

New snapshots carry version 2. Missing version means legacy version 1. Version-aware readers reject unsupported/future versions, unexpected fields, invalid original request identity and invalid/missing required prospective boundaries before execution side effects. Explicit Study Mode intent is preserved and reconstructed from the current canonical active selection; paused selections are never resumed by reconstruction. Ordinary question-form requests retain their ordinary route.

Regressions: legacy/new/future payloads, version-aware older-reader rejection, unexpected fields, invalid/missing boundary, invalid source ownership and rejection before a prospective input is created. Intermediate combined check: 35 tests OK, one PostgreSQL skip. Four Node scripts passed.

Operational limitation: code cannot retrofit this reader into an already running pre-versioning worker. Such a worker can still reject a new payload at its old constructor. Web/worker release alignment and existing queue compatibility require owner deployment attestation; no restart or configuration change was performed or implied.

## Verification ledger

All counts overlap; do not add them as unique coverage. Required baseline (`tests.sals_wp4.test_unified`) and the four requested Node scripts were run through the staged changes; the expanded final suite includes baseline and all new regressions. Expected isolated Axes warnings and deliberately injected worker/provider errors appear in logs.

- Fix 1: 17 tests OK, one skip, then document fixture separately OK; Node four passed.
- Fix 3: 21 tests OK, one skip after explicit-success fixture correction; Node four passed.
- Fix 4: 29 tests OK, one skip, including existing gateway interruption tests; Node four passed.
- Fix 2: two HTTP/SSE/privacy tests OK with the scheduling limitation above; included again in combined suites; Node four passed.
- Fix 5: WP4 baseline 14 tests OK, one skip; extended Node contract groups passed.
- Fix 6: WP4/greeting 17 tests OK, one skip; Node four passed.
- Fix 7: WP4/projection 17 tests OK, one skip; eight additional projection/preference/greeting tests OK after fixture correction; Node four passed.
- Fix 8 and integrated repairs: 35 tests OK, one skip; later focused R1/cache/SRI integration: 70 tests OK, two skips; final WP4/SRI check after the serialized late-failure guard: **41 tests OK, one skip**, 26.246 seconds. The final four Node scripts all passed.
- Exploratory broad run: 177 tests, one fixture failure, ten skips. That failure was corrected explicitly and its test passed in the focused rerun. The final broad rerun passed: **180 tests, OK, ten PostgreSQL-dependent skips**, 67.531 seconds. This includes WP4, all SRI regressions then present, adaptive teaching/R1, academic continuity and the three native streaming adapters plus gateway streaming.

Logs are local `/tmp/sri-fix*.log`, `/tmp/sri-final-focused.log`, `/tmp/sri-final-verified.log`; they contain synthetic test identifiers only. No live PostgreSQL suite, Docker environment, provider generation, real browser session or deployment verification ran. Python parsing, JavaScript syntax and relevant tracked-diff whitespace checks passed. The existing owner PostgreSQL scripts discover the new tests through their tests.sals_wp4 package label; no script or deployment setting needed changing.

## Remaining acceptance and rollback

Run the existing isolated PostgreSQL WP4/concurrency suites in the owner's disposable test tooling, then the student browser checklist with matching web/worker/schema/assets. Key unverified gates are actual lock races, deployed queues/beat, stale workers, desktop/mobile consent/header/reconnect behavior, document grounding quality and natural greeting quality. The original owner observations require a fresh correlated acceptance journey; mocked tests do not establish their exact live cause.

Rollback producers/consumers together, retain durable action/boundary/result receipts and additive existing schema, and preserve partial/interrupted states. Never delete historical learning or restore broad raw transcript/digest recall. New version-2 queued snapshots require a compatible reader; a rollback must retain that compatibility or deliberately fence those jobs. There is no new migration to reverse.

Pending roadmap remains Phase B live continuity acceptance; WP4 real-browser acceptance and pending study-summary/revision-document follow-on; R3 Progressive Hint Restraint; R4 Response Quality, Orchestration and Runtime Optimization; R5 End-to-End Student Acceptance; SALS 0.4 Academic Content Intelligence; SALS 0.5 Quizzes, Assignments and Mock Exams; later student-progress/revision features; separate Vasto-wide Lucide icon migration.
