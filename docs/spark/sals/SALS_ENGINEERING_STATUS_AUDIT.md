# SALS Engineering Status Audit

Audit date: 2026-11-13. Scope: current PwaniNet/Vasto Spark working tree, not Vasto Workspaces or Zane. Audit only; no application changes or database migrations authorized or performed.

## A. Executive summary

SALS foundations are substantial but are not ready to be described as fully verified releases. The repository contains extensive modified and untracked implementation files, including migrations 0027–0034; presence here does not establish deployment or committed release status. Persistent memory, unit tracking, mastery and teaching services exist. Follow-up execution has concrete defects. Runtime optimization has implementation evidence but no verified production improvement. Browser, PostgreSQL concurrency, broker failure and live-provider behavior remain release gates.

Status meanings: VERIFIED means specifically checked behavior within the stated local scope; IMPLEMENTED — UNVERIFIED means code exists without adequate execution evidence; PARTIAL means requirements are missing; BROKEN identifies a concrete incompatible path; NOT IMPLEMENTED means the inspected implementation lacks the deliverable; UNKNOWN means evidence is insufficient. No aggregate status implies production approval.

## B. Release status matrix

- SALS 0.1 Persistent Memory: PARTIAL. Consent-aware extraction/retrieval exists; durable dispatch recovery is missing.
- SALS 0.2 Academic Unit Foundation: IMPLEMENTED — UNVERIFIED end to end. Detection, pending approval, records and UI are connected in source; browser and PostgreSQL verification remain.
- SALS 0.3 WP1 Concept Mastery: IMPLEMENTED — UNVERIFIED at release level. Evidence provenance and independent retention policy exist; PostgreSQL locking requires owner verification.
- SALS 0.3 WP2 Adaptive Teaching: PARTIAL. Deterministic lesson/hint support and persisted teaching exist; general hints depend on model compliance and follow-up execution is defective.
- WP2 contextual follow-up continuity: BROKEN execution paths; PARTIAL overall.
- WP2 study consent/unified composer: IMPLEMENTED — UNVERIFIED in a real browser.
- SALS 0.3 WP3 Runtime Optimization and Reliability: PARTIAL, not fully verified.
- SALS 0.3 WP4 legacy study integration: NOT IMPLEMENTED as a new integration package; legacy services exist. No WP4 work begun.

## C. Completed and verified functionality

Source inspection verifies the existence and wiring below, not production behavior. Execution evidence is separately recorded in K.

Persistent memory: `pwanimate/models.py` defines SparkMemoryPreference, SparkPersonalMemory and SparkMemoryExtraction; migrations 0027, 0029 and 0030 add consent, persistence and extraction cutoffs. `services/persistent_memory.py::extract` locks preferences, checks consent revision and enabled timestamps, deduplicates source receipts, excludes pause/interim messages and prevents delayed work from reversing newer observations. Retrieval filters by owner and consent. `personal_memory.py` implements proposal/approval lifecycle. Academic-purpose facts and personal consent are distinct from semester-scoped unit learning.

Academic foundation: migration 0031, `services/unit_learning.py::detect/process_message/approve_action/get_record/context_for_query` connect eligible enrollments and semesters, unit records, activities, consent revisions and session attribution. Detection produces PendingAction.ACTION_TRACK_UNIT rather than treating model prose as authorization. Approval authorizes future activity, with ask-mode conversation/session restrictions. Historical unit activity is persisted across conversations; owner/enrollment/semester checks constrain access.

Mastery: migration 0032, `mastery_models.py`, `services/concept_mastery.py`, `mastery_policy.py` and assessment integration implement concept graphs, validated learning evidence, recalculation, prerequisite checks, regression and retention. The policy separates observations from verified decisive outcomes and independent answers. Self-reported understanding cannot satisfy verified independent mastery. Minimum question/context/difficulty/accuracy and delayed retention requirements are application rules. Locks and uniqueness/idempotency code exist; SQLite does not verify PostgreSQL races.

Teaching: migration 0033 and `services/teaching.py` persist mode, exercise, hint level, checkpoints and assessment progress. Commands and help classification route supported lessons to deterministic hint steps. `services/agent_execution.py` persists resumed responses and updates the same logical run rather than creating a replacement on each question.

## D. Partially implemented functionality

Memory extraction is retryable after delivery, but not durably recoverable before delivery: `queue_extraction` calls apply_async(retry=False), catches broker errors and only logs them. SparkMemoryExtraction is a completed-source receipt, not an unsent outbox. No equivalent recovery was established in the inspected path.

Follow-ups: migration 0034 and `followup_models.py` provide ownership, pending/consumed/expired/cancelled states and 24-hour expiry. Teaching generates suggestions automatically through `_generate_follow_up_suggestions`; API views persist them when not awaiting input. This establishes automatic generation for supported teaching contexts, not universal conversational relevance. Frontend accepts follow_ups in response rendering. Reliable action execution is incomplete.

General teaching content, prerequisite diagnosis and mathematical derivations are supported by mixed deterministic lessons and model prompts; correctness and hint restraint are not universally enforced. Cross-turn optimization is selective; a complete cache/reuse policy is not established by instrumentation alone.

## E. Known defects and regressions

1. High — unreliable follow-up execution. `services/followups.py::execute_follow_up_action` returns executed=True for teaching_continue without invoking continuation. clarification likewise returns a signal. `teaching_topic` calls nonexistent `UnitLearningService.get_owned_concept` (ownership accessor is on mastery service). answer_followup is a declared model action without an execution branch. Students can click an offer with no teaching result or receive a server error.
2. High — destructive resolution before successful execution. `resolve_follow_up` commits consumed state before execution; failures cannot retry via the pending-only resolver. Endpoint documentation promises replayed prior results, but repeat requests return not-found rather than a saved result. Execution is not transactionally coupled to consumption.
3. High — expired follow-up raises undefined ValidationError. The service does not import it; the API catches ValueError. The expiry update also occurs inside a transaction that rolls back on the exception. Negative short replies return cancelled objects and the chat view still calls execute_follow_up_action; cancellation therefore does not reliably prevent execution. Negative resolution also lacks a locked pending-state recheck.
4. High — extraction dispatch can be lost during broker outage, leaving cross-conversation memory incomplete.
5. High — small-hint protection is incomplete for general problems. `TeachingService._hint` constrains known exercises; lines around the general-lesson fallback clear the deterministic answer so the answer model handles hints. TEACHING instruction requests restraint, but there is no general answer-leak validator. Repeated hints also advance to level four (full explanation). The specific normal-vector incident was not reproduced against a live model; its unrestricted fallback risk remains.
6. Medium — incomplete generation can remain a successful answer path. Bounded continuation failure retains partial output; terminal run completion checks awaiting/running state, not proof that the provider finished completely. See G.

## F. Runtime performance findings

`orchestrator/timing.py::TimingRecorder` records monotonic marks, counts and durations. `orchestrator/service.py` adds teaching/direct/RAG fast paths, planner timing, first-token marks, model-call counts and LLM duration. `api/views.py` marks first chunk and final event publication. Provider response types carry finish reasons.

WP3 is PARTIAL. These are real instrumentation and routing changes, but their existence is not a measured speedup. `docs/spark/sals/wp3/RUNTIME_PIPELINE_AUDIT_AND_REUSE_ANALYSIS.md` explicitly labels its 15–35s versus 3–8s budget as estimates/hypotheses. No production request-correlated before/after distribution was established. Tool caching, safe prior-observation reuse and academic-context reuse must be evaluated separately; freshly validating consent is necessary and must not be bypassed by cached context. No live provider benchmark was run.

## G. Streaming and response recovery status

SSE emits thinking/status, public activity, chunk and final response events; first chunk is distinct from final response publication (`api/views.py::_stream_chat_response`). No evidence here that the SSE generator itself marks mission complete at first token. Keepalive and background processing exist; this does not establish durable byte-offset replay after disconnect.

`orchestrator/service.py::_needs_continuation` permits exactly one continuation for length/interrupted output with usable content. The continuation includes the partial assistant text. This can recover within a logical answer, but is not guaranteed complete: an empty interruption is ineligible; a second interruption or continuation failure can preserve incomplete output. Duplicate avoidance needs provider/browser testing, beyond asking the model to resume. `services/agent_execution.py` persists a response before atomically publishing completed/result pointer, an appropriate durability ordering; it does not gate completion on resolved truncation. `services/agent_runs.py`, `agent_recovery.py`, task leases and reconciliation provide worker recovery machinery, not proof of exactly-once generation across worker loss. Status: PARTIAL; live interruption, reconnect and worker-kill verification outstanding.

## H. Spark Thinking and activity dialogue status

`orchestrator/activity.py`, `services/agent_journal.py`, agent progress emission and SSE activity events supply public execution summaries. `static/js/pwanimate/activity-trail.js` renders progress, tools, checkpoints and history; chat JavaScript integrates answer streaming and activity restoration. Public summaries are distinct from private model reasoning; no private reasoning was sought.

Historical reports `ACTIVITY_EXPLANATIONS_FIX.md` and `sals/wp3/ACTIVITY_AND_MEMORY_UX_FIX_REPORT.md` describe restoration. Current source supports meaningful public events, but disappearance cannot be attributed to one proven historical cause without reproducing the earlier version. Fast paths bypassing planner events and answer-time collapse are relevant mechanisms. Automatic collapse, manual expansion, continued updates and reconnect restoration remain browser-unverified; do not interpret collapsed preparation as lost activity. Status: IMPLEMENTED — UNVERIFIED end to end.

## I. Academic consent and composer status

The SMA B203 fix is present in detection vocabulary and proposal lifecycle, not merely reported in prose: `unit_learning.py` creates persisted tracking actions; action approval, academic tracking state/API and `static/js/spark-unit-learning.js` expose them; chat renders academic actions. The consent/composer implementation report records a rolled-back real-account probe, but that is historical evidence, not this audit's execution.

Current chat code and `tests/spark/composer-interaction.cjs` implement a question section above the existing composer for typed/mixed input; choices-only retains its standalone block. Submissions use durable interaction/original-run continuation; frontend draft/failure restoration logic exists. Labels are Start Tracking / Not Now. Pending state is server-backed. Actual reload, reconnection, failed submission draft survival, mobile keyboard/safe areas and light/dark rendering were not browser-tested. DOM adapters and source contracts do not establish visual correctness. Status: IMPLEMENTED — UNVERIFIED for the complete user experience.

## J. Legacy Study Mode integration opportunities

`services/study_session.py` retains transactional start/continue/pause/resume/end controls, resume banners, summaries and document/collection reuse. `study_report.py`, `study_materials.py`, `api/study_assistant.py`, `static/js/spark-study-sessions.js` and `spark-study-desk.js` remain relevant legacy paths. Their existence does not certify current @study end-to-end operation or successful document export.

Potential WP4 seams: use the authorized enrollment/semester record as session scope; project tracking_state into a floating unit pill; reuse session status endpoints for controls; preserve teaching state/run IDs for continuity; summarize attributed evidence through existing summary service; reuse revision material/document generation with owner checks. These are integration opportunities only. Legacy functionality status: IMPLEMENTED — UNVERIFIED; new WP4 integration NOT IMPLEMENTED.

## K. Test coverage and outstanding PostgreSQL verification

Historical documentation reviewed includes SALS_0_1_VERIFICATION_REPORT, SALS_0_2_IMPLEMENTATION_REPORT, SALS_0_3_WP1_REPORT, SALS_0_3_WP2_REPORT, representative outputs, initial audit/plans, WP3 reuse analysis and consent-composer report. Their pass counts and DB probes are historical claims, not reruns. Consent report records 149 tests with 15 PostgreSQL skips and 18 unrelated orchestrator prompt failures; those figures must not be presented as current audit results.

Audit command: `bash scripts/test-sals03-wp2-local.sh`, using isolated `tests.spark_memory.settings` SQLite test database and SPARK_MEMORY_SYNC_SCHEMA. No Docker, PostgreSQL test suite or existing-database migrations used. Final result is recorded below after command completion. Suite covers memory, academic units, mastery, teaching, persistence, local lifecycle, provider streaming mocks and frontend contracts. `tests/sals_03_wp2/test_followups.py` exists: it is incorrect to claim no follow-up tests. Coverage includes short replies, ambiguity, persistence and expiry; dedicated regression coverage for the defective execution/retry/cancellation paths and real click-to-answer continuation still needs confirmation/addition.

Owner PostgreSQL gates: apply migration graph in an isolated DB; constraints and tenant isolation; simultaneous approval/revocation/extraction; duplicate evidence submissions/recalculations; enrollment/semester races; original-run continuation claims and leases; worker termination; transactional follow-up retries. Separate browser gates cover composer/theme/mobile/reconnect and public activity behavior. Provider fault injection and broker outage recovery are additional gates.

## L. Recommended next implementation task

Exactly one next package: **WP2 contextual follow-up execution and lifecycle repair**. It has directly demonstrable broken paths in a student-facing feature already offered by the application. Deliver real original-run execution, correct ownership accessor, supported-action validation, cancellation safety, defined expiry errors and retryable/idempotent durable outcomes, with targeted API/concurrency/browser regressions. This recommendation is not implementation authorization. Memory outbox and remaining WP3 reliability remain subsequent blockers.

## M. Revised SALS master checklist

- 0.1 consent/approval, owner filtering, retrieval and persistent facts: IMPLEMENTED — UNVERIFIED deployment; depends on migrations 0027/0029/0030 and owner verification.
- 0.1 extraction revision checks/deduplication: implemented; local evidence below; PostgreSQL race behavior unverified.
- 0.1 Celery retry/late acknowledgement: IMPLEMENTED — UNVERIFIED operationally; depends on broker/worker tests.
- 0.1 durable dispatch recovery: NOT IMPLEMENTED in inspected extraction path; required before reliable automatic memory claim.
- 0.2 enrollment/semester records and ownership: IMPLEMENTED — UNVERIFIED PostgreSQL; migration 0031.
- 0.2 detection/proposals/consent/frontend linkage: IMPLEMENTED — UNVERIFIED browser; depends on action lifecycle and vocabulary coverage.
- 0.2 attribution and cross-conversation history: IMPLEMENTED — UNVERIFIED deployment; depends on consented records.
- WP1 concepts, graph, evidence and assessment binding: IMPLEMENTED — UNVERIFIED deployment; migration 0032 and assessment services.
- WP1 independent mastery, regression/retention and recalculation: application-enforced policy present; PostgreSQL concurrency unverified.
- WP2 state, modes, assessments/checkpoints and original-run questions: IMPLEMENTED — UNVERIFIED browser/worker recovery; migration 0033.
- WP2 mathematical correctness and universal hint restraint: PARTIAL; depends on deterministic support and live-model regression evidence.
- WP2 follow-up persistence/generation/UI: PARTIAL; migration 0034; execution BROKEN; repair prerequisite to continuity claims.
- WP2 unified composer/academic consent: IMPLEMENTED — UNVERIFIED visual/reconnection behavior.
- WP3 instrumentation and fast paths: PARTIAL release; live latency validation absent.
- WP3 cross-turn/tool/academic reuse: UNKNOWN as complete deliverables; selective code must not be interpreted as blanket safe caching.
- WP3 interrupted answer recovery and terminal semantics: PARTIAL; depends on finish-reason success gating and fault tests.
- Spark Thinking public journal/SSE/rendering: IMPLEMENTED — UNVERIFIED browser restoration/collapse.
- Legacy @study controls/summary/revision documents: IMPLEMENTED — UNVERIFIED current end-to-end behavior.
- WP4 semester sessions/pill/controls/continuity/summary/revision integration: NOT IMPLEMENTED; depends on stable consent, teaching and legacy verification. Do not begin without owner approval.

### Audit-executed results

The local script completed with exit code 0: **287 tests run, OK, 19 skipped**, in 79.270 seconds. This is 268 non-skipped tests, not 287 executed successes. Its Node teaching-flow, lifecycle-resume, markdown/math, question-controls, composer-interaction and quiz-flow checks passed; chat JavaScript syntax and PostgreSQL script shell syntax checks also completed. These are isolated service/mock/DOM checks, not real-browser or PostgreSQL verification. Axes middleware/backend configuration warnings appeared in the isolated settings. Expected negative-path provider errors were logged without suite failure.

Within that scope, tested memory/academic/mastery/teaching and composer contracts are **VERIFIED locally**; release-level statuses above intentionally retain deployment/browser/concurrency limitations. Baseline outputs reported mathematics 65.859ms, programming 48.517ms and theory 60.223ms with zero model calls. They measure local deterministic test scenarios only and are not provider latency or production performance improvements. No historical test total was substituted for this result.
