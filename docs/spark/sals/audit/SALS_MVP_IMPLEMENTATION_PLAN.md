# SALS MVP implementation plan

11 November 2026 · PwaniNet only · **Not authorization to implement.** This audit created documentation only. Existing evidence: [inventory and risks](SALS_EXISTING_SYSTEM_AUDIT.md), [capability matrix](SALS_CAPABILITY_MATRIX.md), [architecture](SALS_ARCHITECTURE_RECOMMENDATION.md), [ownership/consent](SALS_DATA_AND_MEMORY_AUDIT.md).

## First demo outcome

One student explicitly opens one unit and approves a small determinant/inverse roadmap. Spark teaches from selected authorized notes. The student completes a few independently answered, validated questions, gets explanatory feedback and sees honestly labeled evidence/progress. They leave, reopen the same session with its document page and evidence intact, and take a later revision assessment. Personal chat stays available and does not award academic credit. Memory controls explain transcript, cross-chat recall and learning-record retention separately.

This reuses existing unit enrollment, study session/conversation, viewer, RAG, assessment, report and settings infrastructure. It does not require all eleven modules, full assignment management, a prerequisite graph editor, arbitrary symbolic marking, handwriting recognition, or multi-paper mock examination generation.

## Gate 0 — Establish source/deployment baseline and product contracts

Dependencies: none. First review the substantial current modified/untracked implementation and identify the branch intended for delivery. Read actual deployment migration history only in an authorized environment; this audit did not query it. Existing migrations 0017–0026 are source inventory, not proof of applied schema. Reconcile documentation/test assumptions about one active session per user versus per unit.

Decide private-document elevated-role access, memory opt-in defaults, acceptable first-demo question types, accuracy versus mastery labels, minimum concept evidence, custom/unenrolled-unit access, and conversation deletion effects. Capture explicit approval before implementing the plan.

Acceptance: agreed meanings for a learning session, concept evidence, mastery/insufficient evidence, practice and revision; stable owner policy; deployable baseline and migration order identified. Blockers: unresolved private-data policy, unknown migration state or inability to isolate tests. No new feature should be shipped on assumed deployment parity.

## Milestone 1 — Trustworthy existing assessment and privacy boundaries

This is the smallest safe first development milestone. It proves that existing persisted outcomes can be trusted before a new dashboard displays them.

| Area | Expected future change | Reuse | Migration/API/UI implications | Required tests and acceptance |
|---|---|---|---|---|
| Grade validation | Strict generated-question and verdict schemas; boolean must be boolean; invalid MCQ answer/key cannot fall through to discretionary LLM credit | AssessmentService; public_question/public_assessment; AIGateway | Preserve existing endpoints; add explicit invalid/inconclusive response. Key validation may need metadata/version fields | Missing/string/null verdict, malformed JSON, missing/duplicate options, mismatched key, fewer returned items: reject or clearly mark incomplete; no score on failure |
| Atomic answers | Unique request identity and concurrency-safe finalization; model work outside DB locks | AssessmentView and existing run/idempotency patterns | Proposed submit request key/expected version; additive submission/evaluation storage if necessary | Two concurrent different-question answers both survive; duplicate same answer has one result; retry after timeout does not double credit |
| Honest scores | Expose accuracy distinctly; no 100% mastery from one item; scope evidence to approved objective/version | Session/assessment fields, report and frontend progress components | Versioned objective/evidence relationship; preserve legacy score display as legacy accuracy/unknown mastery | One correct/four unanswered cannot certify mastery; wrong and unanswered remain distinguishable; old goal results cannot populate new goal |
| Document isolation | One policy shared by vector/page/search/inventory; apply selected unit/category filters before limit | Existing authorized queryset/permission helpers | No domain redesign; policy decision may change allowed behavior, requiring explicit review | Synthetic role/user/visibility/enrollment and access-revocation matrix; no unit-filter fallback to unrelated docs |
| Memory policy | Explicit approved default, distinct meaning of off/forget; no unsolicited personal fact store | MemoryView, preference, reset cutoff, job rechecks | Consent scope/version fields only if needed; readable preferences UI | Disable/forget during success and failure jobs; transcript retained as promised; academic records handled according to separate choice |
| Cost attribution | Carry actor/task/budget metadata on quiz writing/grading | AIGateway, existing quota/billing services | No separate billing system | Quota refusal preserves state; no unexpected fallback model outside entitlement |

Risks addressed: R1–R8, R11 and assessment part of R13. Acceptance: deterministic reviewed MCQ fixture yields expected persisted result; malformed model verdict never grants credit; cross-user requests are denied; result survives reload; documentation/UI calls accuracy by its correct name. No autonomous tutoring progression yet.

## Milestone 2 — Unit entry and approved roadmap

Dependencies: baseline policy and Milestone 1 evidence scope. Extend existing sessions page and study endpoints; do not introduce another conversation model.

- Proposed `LearningSpace.open(actor, unit_ref, goal)` wraps existing owner-resolved `get_or_create_unit_session` and enrollment/canonical-unit lookup. Explicit code/name/ID handling avoids silently treating IDs as custom labels. Keep custom units only if approved.
- Proposed `Roadmap.propose/approve` exposes the currently unwired set_roadmap/update_roadmap_item methods behind version/consent validation. Stable concept IDs bind each section/objective to evidence; pending proposals do not change accepted state.
- Reuse existing session roadmap display, sidebar/rails, study context and document selection. Add unit entry and roadmap review controls within current Django/JS structure. Keep companion chat entry accessible.
- Possible migrations: objective/roadmap version and stable concept metadata; historical sessions remain valid with legacy version. No automatic mastery backfill.
- API requirements: owner-scoped unit-open adapter and roadmap read/propose/approve actions under existing Spark API namespace; typed tools can call the same services later. These are proposed additions, not existing endpoints.

Tests: ambiguous unit, inactive enrollment, custom-label policy, duplicate open, roadmap edits, stale proposal approval, disabled capability, owner mismatch and preserved old session history. Acceptance: opening a unit twice returns the intended existing learning context; student can approve/reject a versioned roadmap; no model text alone marks a concept mastered.

## Milestone 3 — One complete concept learning loop

Dependencies: approved concepts and trustworthy assessment outcomes.

Proposed `Tutor.context` adds accepted objective, concept, recent observations, eligible evidence and source references to existing `StudyOrchestrationContext`. Use existing chat/RAG and AIGateway. The initial planner excludes study/selected-resource turns; use explicit service/API actions initially, with later planner admission changes only if tested. Persist lesson observations as observations, never grade based on a confident summary or acknowledgement.

Extend `AssessmentService.generate` with source-bounded context and concept/version metadata. Current `_unit_context` supplies labels/goals/roadmap only and does not retrieve notes, so source grounding must be explicit. Narrow MVP to reviewed/deterministically validated MCQ and selected numeric formats. A wrong LLM reference answer cannot be made trustworthy simply by comparing text to it.

Frontend: reuse `spark-study-sessions.js` question rendering and clarification's sequential UX, but keep separate assessment contracts. Show one question at a time; choosing an option changes selection; explicit Submit commits; disable controls while pending; maintain drafts on errors; refresh retrieves authoritative questions/results. Do not expose answer keys. Explain whether feedback is immediate or deferred; current modes do not enforce fully deferred marking/feedback.

Proposed Mastery.project computes a modest concept state (insufficient evidence, practising, demonstrated) with minimum evidence and policy version. Product approval defines thresholds; avoid unsupported numerical precision. Preserve practice exclusion, evidence count and assessment confidence. A source change invalidates grounding assumptions without erasing historical attempts.

Possible migrations: concept-linked submission/evaluation data and projection. Tests: reviewed determinant fixtures, incorrect reasoning, acknowledgement-only messages, hints versus independent answers, source revocation, duplicate/reordered submissions and model refusal. Acceptance: student learns, answers independently, receives correct feedback and sees persistent evidence-linked concept state; mastery cannot be changed by editing roadmap coverage or saying “I understand.”

## Milestone 4 — Return, revise and control privacy

Dependencies: reliable concept evidence and resume.

Reuse session detail/status/context restoration, checkpoints, digests, reports and practice generation. Add a simple due-review policy using eligible evidence and timestamp; keep manual revision available. Revision assessment must record retention evidence when designated as such; existing practice packs are feedback-only and do not count toward mastery. Do not silently reinterpret old practice packs as retention passes.

Possible migration: due/review status, policy version, optional separate continuity preferences. API: proposed concept progress/due-review read projection, revision generation via existing assessment service, explicit record-retention preferences. UI: existing session list/report plus small revision entry, privacy explanations and owner-readable history. Notification opt-in uses existing reminder delivery; automatic reminders are not necessary to prove the first complete journey.

Tests: return after simulated weeks, disabled cross-chat recall with same-session access, deleted/unavailable notes, source version changes, due calculations, opt-out, repeated review, source-conversation deletion policy, stale projections and feature disabled mid-session. Acceptance: a fresh authenticated browser visit reopens the correct unit session/reading location, distinguishes taught versus demonstrated concepts, runs revision, preserves outcome and respects privacy choices.

## Milestone 5 — Reliability gate before expanding the demo group

Dependencies: complete journey. Use an isolated staging/test environment with synthetic data and mocked providers by default; approved evaluation calls are separate from this audit.

Run meaningful existing tests after verifying settings/task/network isolation: study ownership/checkpoints, conversation memory, document context/retrieval, ingestion and durable clarification/approval. Add missing assessment contracts and concurrency tests; inspect older lifecycle assertions before treating them as current requirements. Migration checks must demonstrate additive upgrade and preserved transcripts/results, not just clean installation.

Test fresh SSE disconnect/reload, continuation replay, process/worker loss, duplicate delivery, cancel/lease expiry and same-conversation overlapping turns. Fresh SSE currently uses a process-local thread/queue; decide whether full initial-turn durable execution is necessary before promising recoverability. Do not claim existing continuation durability covers every initial request.

Measure retrieval latency and relevance, DB query counts/plans, time to first token and persisted result, token/cost per learning journey, fallback frequency, queue age and extraction memory/page coverage. Use 700–1,000-page text and scanned fixtures only when resources permit. Set budgets based on actual measurement; no timing or throughput targets were measured here.

Browser matrix: desktop/mobile, dark/light, collapsed/expanded left rail, document split, keyboard/safe area, explicit answer Send, failed submit retry and restored state. Acceptance: published invariants pass in isolation, no unauthorized keys/content in API/events/logs, reconnect behavior documented, known performance limits visible to demo participants.

## Deferred work

Assignment/submission workflow, official grades, essay rubrics/partial credit, arbitrary symbolic validation, multiple handwritten-image marking, full prerequisite graph, automatic course pacing and multi-paper mock-exam analysis are later product increments. Reuse document categories and existing generation/export only for clearly labeled drafts until a source-backed blueprint and exam attempts exist. No microservices, new vector store or frontend rewrite is justified.

## Owner decisions and stop condition

Approve data-access and memory policy, evidence/score semantics, first question formats, enrolled/custom-unit behavior and deletion/retention rules before implementation. Architecture recommendation: a small modular extension around existing services; acceptance of this recommendation is separate from permission to implement. Audit work stops with these documents.
