# SRI-01 — Root cause matrix

Date: 2026-11-14. Diagnosis only; proposed repairs have not been implemented. Evidence scope and fixture methods: [investigation](RELIABILITY_INVESTIGATION.md). Entries use a consistent field layout so unknown live transitions remain explicit.

## RC1 — Post-consent ordinary learning is missing

- **Symptom:** consent succeeds, a new authorized request activity exists, a response persists, but no completed learning appears.
- **First broken transition:** ordinary assistant finalization selects the original pre-consent message instead of the post-decision execution receipt.
- **Root cause:** `execution_boundary_id` is propagated into teaching receipt keys but omitted from ordinary `academic_source_id` selection. A question outside the teaching matcher takes this route.
- **Confidence:** CONFIRMED current source + disposable SRI-F2; UNVERIFIED as the owner's exact OOP cause.
- **Evidence:** `pwanimate/services/agent_execution.py::execute_claimed` (:146–166, :223); `teaching.py::applies` (:96), `prepare` (:479); `academic_progress.py::finalize_ordinary`; SRI-F2 produced activity counts `(0,1)`, zero turns, run completed. Existing WP4 consent test covers a recognized Explain request.
- **Minimal repair:** use a validated shared effective execution source for ordinary finalization, retaining original run identity. Reconstruct appropriate selected Study Mode context where needed; do not force every academic answer through teaching.
- **Regression test:** held consent → question-form academic input → post-decision receipt → completed ordinary exposure, once, same owner/session/term; no retroactive original-source authorization; no mastery. Include document-mapped and revoked/pause variants.
- **Shared cause:** SC1, incomplete execution-boundary propagation. Related to RC2/RC3, but a different consumer loses a different field.

## RC2 — A completed lesson segment disappears during resumed teaching

- **Symptom:** response can be fully persisted while browser shows only a next-step question/activity.
- **First broken transition:** result_message → durable waiting-run terminal response.
- **Root cause:** `_terminal_payload` excludes result content for waiting runs and omits teaching metadata; `OrchestrationResponse.to_dict` blanks all awaiting answers. Browser expects answer plus teaching metadata.
- **Confidence:** CONFIRMED source + SRI-F3 helper reproduction; actual OOP/browser occurrence UNVERIFIED.
- **Evidence:** `pwanimate/api/agent_runs.py::_terminal_payload`; `orchestrator/types.py::OrchestrationResponse.to_dict`; chat JS `_resumeChatPayload` awaiting branch (:7513). `execute_claimed` directly returns metadata in its in-memory response; HTTP durable reconstruction changes the contract. WP4 service-level consent test does not call that reconstruction.
- **Minimal repair:** preserve the eligible persisted instructional segment alongside a separately pending owned interaction in JSON, journal-tail, reconnect and restored history contracts. Preserve privacy filtering and do not expose generic interim/planner prose.
- **Regression test:** actual HTTP SSE consent resume → explanation + waiting question → reload/reconnect returns the same message/question once; no provider required in automated test. Check JSON/SSE parity and source/citation retention.
- **Shared cause:** SC1, service success is not faithfully projected across execution/transport/UI boundaries. Independent of a provider cutoff.

## RC3 — Missing Study Mode header after consent

- **Symptom:** A, owner-reported OOP consent approval without expected header.
- **First broken transition:** UNVERIFIED in live journey. Current source has inconsistent decision-route refresh behavior.
- **Root cause:** PROBABLE response/refresh asymmetry, not proven missing backend session. A run-bound dedicated card explicitly refreshes canonical state; activity-trail resume/re-paused output and standalone action success lack equivalent immediate refresh. No canonical-session payload or lifecycle event accompanies durable terminal output.
- **Confidence:** CONFIRMED asymmetry; PROBABLE contribution; exact A cause UNVERIFIED.
- **Evidence:** `unit_learning.py::approve_action/_session`; `study_context.py::resolve`; chat JS `_academicTrackingBlock` (:8379), `refreshStudyContext` (:8296), `_resumeChatPayload`, `syncStudyModeUI` (:824); `activity-trail.js::button` (:433); `api/agent_runs.py::_terminal_payload`. Canonical readers already replace the direct legacy selector on this path.
- **Minimal repair:** canonical UI synchronization after every relevant decision/terminal outcome, with current conversation and generation guards and visible paused/ambiguous states. No automatic resume on renewal; no new session system.
- **Regression test:** every displayed consent route, approval/decline/renewal, active/paused/collision states, stale two-tab handoff, conversation switch during fetch and reload; assert header ID/status equals canonical API. Include desktop/mobile.
- **Shared cause:** SC1 for result-state propagation; stale assets/workers remain a separate unverified operational possibility.

## RC4 — Consent control position or identity changed

- **Symptom:** B, connected block above composer replaces expected inline consent presentation.
- **First broken transition:** none established by placement. Wrong binding/submission is UNVERIFIED.
- **Root cause:** existing unified interaction UI intentionally moves typed/mixed SparkAgentInteractions above composer. Current academic consent remains PendingAction with dedicated transcript controls. The reported control cannot be identified from appearance alone.
- **Confidence:** CONFIRMED source distinction; live mismatch UNVERIFIED.
- **Evidence:** chat JS `_syncInteractionBlock/_buildInteractionBlock`, `_showAcademicPreflight/_academicTrackingBlock`, `services/actions.py::serialize`, `AgentRunService.decide`, `consent-composer/IMPLEMENTATION_REPORT.md`. Existing composer/lifecycle/consent Node contracts pass with mocks.
- **Minimal repair:** only if live trace proves a mismatch, align displayed control with exact action/run authority and retire successful control consistently. Do not redesign based on relocation alone.
- **Regression test:** restore consent and another parked question; submission may resolve only its own ID, cannot replace/answer the other; duplicate decision, expiry, failed validation, reconnect and drafts on desktop/mobile.
- **Shared cause:** potential SC1 consumer issue, not yet a diagnosed independent defect.

## RC5 — Token-limited output escapes recovery and may count as completed

- **Symptom:** C/D, partial explanation/code can appear successful and become completed learning.
- **First broken transition:** provider-normalized termination → orchestrator continuation/completion classification.
- **Root cause:** producer emits `max_tokens`, consumers test `length`; negative completion sets omit other non-success reasons as well. Run completion is not tied to an affirmative completion contract.
- **Confidence:** CONFIRMED current source and SRI-F1; Java-cutoff attribution UNVERIFIED.
- **Evidence:** provider Groq :382–391, OpenRouter :362–371, Gemini :358; orchestrator `_needs_continuation` :1407; `TeachingService.finish` :720; `api/views.py` ordinary completion; `agent_execution.py` :224 and terminal transition. Raw length fixture normalized to max_tokens, continuation false, completion predicate true.
- **Minimal repair:** one shared normalized completion interpretation across providers/gateway/continuation/persistence/capture/run/UI; known partial/unknown failure must not earn complete status. Preserve intentional bounded one-continuation policy.
- **Regression test:** stop, length, max_tokens, MAX_TOKENS, interrupted, content-filter, provider error, missing terminal marker; stream/JSON parity; continuation stop versus repeated limit; pause/revoke/cancel while generating; no completion evidence for known partial output.
- **Shared cause:** SC2, incompatible definitions of success. Separate from hidden full content in RC2.

## RC6 — Clean stream EOF or Celery result can imply false success

- **Symptom:** C/runtime, monitoring may call a partial or failed task successful.
- **First broken transition:** stream EOF without finish marker → fabricated stop; task return → external success interpretation.
- **Root cause:** adapters synthesize stop when terminal marker is absent; Celery task catches exceptions and returns `status: error`, which Celery can store as SUCCESS. Durable run is the application authority.
- **Confidence:** CONFIRMED source mechanisms; occurrence in owner traces UNVERIFIED.
- **Evidence:** provider streaming reassembly in Groq :267, OpenRouter :274, Gemini :235; `tasks/agent_runs.py::execute_agent_run`; `agent_execution.py::mark_failed`; `api/agent_runs.py::_terminal_payload` returns explicit error for failed/interrupted runs.
- **Minimal repair:** retain terminal-marker evidence/unknown outcome and evaluate task payload plus durable attempt/run state. Do not equate Celery transport success with student completion.
- **Regression test:** clean EOF without finish versus exception after chunks; worker returned error payload with failed run; failure while marking failed; reconcile lease interruption/result pointer without blind replay.
- **Shared cause:** SC2; operational monitoring remains distinct from frontend rendering.

## RC7 — Historical SMA B203 learning is empty

- **Symptom:** D/E, no completed teaching or learning history.
- **First broken transition:** M1 source predates approval; later complaint M3 fails academic attribution before activity creation.
- **Root cause:** earlier nonblocking first-consent contract plus topic-free interruption handling outside supported continuation attribution.
- **Confidence:** CONFIRMED in explicitly cited historical live investigation, not freshly re-queried here.
- **Evidence:** `stabilization/PHASE_B_LIVE_CAPTURE_DIAGNOSIS.md` timeline M1–M4 and zero raw counts; current `unit_learning.py::detect/contextual_match` and `TeachingService.applies` explain bounded continuation limits. No target run existed historically.
- **Minimal repair:** preserve prospective WP4 first-lesson boundary; separately define bounded interruption repair with exact owned source provenance. No unauthorized backfill, fabricated mastery or resurrection of nonexistent old run.
- **Regression test:** original nonblocking historical sequence remains unrecorded; new held-consent sequence records only later authorized execution; cutoff complaint remains incomplete until genuinely recovered.
- **Shared cause:** missing eligible events explains both capture and empty recall for that historical journey. Does not prove current OOP records are absent.

## RC8 — Cross-chat recall reports no history or no active tracking

- **Symptom:** E, unit/paused session can be found while learning history or active tracking is absent.
- **First broken transition:** historical SMA records stop at capture (RC7); OOP raw storage → projection → prompt → model transition unknown.
- **Root cause:** absent events and separate read/write permissions are confirmed distinctions. Generic continuity is restricted to unique eligible/revision-current candidates; named history can read older authorized records. Disabled preference suppresses model recall. Unfiltered session status is not active write authorization.
- **Confidence:** CONFIRMED service policy and historical absence; OOP filtering/misbinding/model disregard UNVERIFIED.
- **Evidence:** `unit_learning.py::context_for_query/summary/tracking_state`; `academic_progress.py::progress_summary`; ContextPackage `format_context_text` :527; actual historical projection was empty and accurate.
- **Minimal repair:** first repair eligible capture; then ensure explicit historical read posture and scope disambiguation survive projection/prompt formatting. No new write authorization from reading history.
- **Regression test:** real finalized record across conversations, ask-scope write denied but named read retained; stale revision, inactive/historical term, multiple terms, preference disabled, foreign owner; compare raw row → summary → formatted context and bounded truncation behavior.
- **Shared cause:** RC1/RC7 when records absent; selection/prompt interpretation needs separate evidence when records exist.

## RC9 — Proactive continuity diminished, greetings sometimes over-investigate

- **Symptom:** F, generic greeting lost prior study suggestion, some casual prompts invoke agentic work.
- **First broken transition:** old digest → welcome was removed; bare greeting → recent authorized session selection is absent; compound/slang greeting → lightweight classifier fails.
- **Root cause:** intentional approved-fact-only replacement lacks a bounded authorized recent-activity route; exact regex excludes common compound greetings. Study/resources gates also exclude lightweight conversation. Conversational generation itself accepts memory/context supplied to it.
- **Confidence:** CONFIRMED HEAD/current diff and offline regex controls; exact historical student exchange mechanism PROBABLE.
- **Evidence:** `git show HEAD:pwanimate/services/study_memory.py`, current refresh/welcome; `conversation_memory.py::search`; `persistent_memory.py::retrieve`; `unit_learning.py::context_for_query`; orchestrator patterns :87 and routing :1115–1230, `_run_conversational` :1504. No claim that all previous conversation memory remains available under current consent policy.
- **Minimal repair:** one inexpensive casual route with bounded real authorized recent-session context and relevant preferences, no document retrieval/planning; preserve opt-out and explicit activation rules.
- **Regression test:** supplied Wagwan example and common compound greetings, new chat/active Study Mode/open PDF, authorized paused activity/no history/multiple candidates/revoked consent; assert zero planner, selector, document and tool calls, one bounded answer at most.
- **Shared cause:** SC3, continuity replacement/policy coverage gap; independent of capture/transport, though absent captured progress also limits truthful suggestions.

## RC10 — Runtime source versions may differ

- **Symptom:** previously reported OrchestrationRequest/execution_boundary_id errors; code works in tests but not live.
- **First broken transition:** new serialized snapshot → an older worker's DTO constructor, if such a worker is loaded.
- **Root cause:** current field/snapshot agree, but no explicit snapshot schema-version compatibility boundary; accepted broker publication does not attest worker code or queue consumption.
- **Confidence:** CONFIRMED current source compatibility and rolling-version exposure; live stale worker/error UNVERIFIED.
- **Evidence:** `orchestrator/types.py:34`; `services/agent_runs.py::SNAPSHOT_FIELDS`; `agent_execution.py:166`; tasks dispatch fallback; historical Phase B worker timing. No host web/worker process or live browser is visible now.
- **Minimal repair:** attest release/schema/assets first; later coordinate deployment and use explicit durable snapshot compatibility handling if needed. SRI-01 authorizes no restart/configuration change.
- **Regression test:** old/new snapshot fixtures, unexpected fields handled deliberately, execution-boundary required for prospective receipt; worker/web contract tests and queued old-message compatibility. Browser asset fingerprint must match served release.
- **Shared cause:** independent operational hypothesis; may affect every resumed workflow, but must not replace confirmed code diagnoses.

## Roadmap

Still pending: Phase B live acceptance; WP4 real-browser acceptance and study summaries/revision documents where pending; R3 hint restraint; R4 quality/orchestration/runtime optimization; R5 end-to-end student acceptance; SALS 0.4 content intelligence; SALS 0.5 quizzes/assignments/mock exams; later progress/revision features; separate Vasto-wide Lucide migration. SRI-01 completes none of them.
