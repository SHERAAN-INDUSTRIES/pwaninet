# SRI-01 — Proposed repair sequence

Date: 2026-11-14. Plan only. No fixes, migrations, deployment actions or student-data updates performed. Use existing session model, canonical resolver, orchestrator and durable run machinery.

## Gate 0 — Establish an attested failed journey

Before accepting a repair against the owner's OOP symptom, obtain the minimal read-only evidence listed in RELIABILITY_INVESTIGATION.md: current web/worker/schema/queue/asset identity and one aliased input → action → post-decision boundary → session/generation → run/attempt → turn/result chain. Inspect existing logs/rows with exact authenticated ownership, enforced read-only transactions and rollback; avoid write-on-read services. No provider call or consent replay is needed to inspect existing evidence.

Current access cannot attest the owner's processes/browser/database. Do not restart them as a diagnostic shortcut. Later operational release alignment needs its own authorized deployment procedure; keep pending runs and additive schema intact. A missing header alone is not a database diagnosis.

## Package 1 — Complete the consent-to-result contract

**Priority:** first implementation package. Coordinates RC1–RC4; smallest coherent change spans the existing boundary consumers, not a new orchestrator. Add completion-classifier coverage from Package 2 before claiming learning correctness; the packages can share release gates without becoming one redesign.

**Files/services likely affected:** `pwanimate/services/agent_execution.py`, `services/agent_runs.py`, `services/conversation.py`, `services/academic_progress.py`, `services/teaching.py` only where source selection/context is needed; `api/agent_runs.py`, `orchestrator/types.py`; `static/js/pwanimate/pwanimate-chat.js`, activity-trail approval callback if needed. Preserve `services/study_context.py` as authority; consume its payload rather than creating a competing selector. Review `api/views.py` initial-path parity and `services/actions.py` restoration. No schema change is shown necessary.

**Concrete changes to design/implement later:**

1. One validated effective source identity: interaction answer when appropriate, post-decision execution boundary for held academic work, original input otherwise. Keep original run/user-message identity for resume/idempotency. Use that provenance consistently in ordinary and teaching completion.
2. Serialize an eligible persisted teaching segment alongside its separately pending question on waiting runs. Make JSON, durable SSE, reconnect and restored history agree. Keep generic agent interim/planner text hidden. Preserve sources and citations; do not depend on in-memory metadata that durable reconstruction discards.
3. Synchronize header/session/tracking state after every consent decision route and re-pause, not only one card wrapper or nonwaiting result. Guard conversation and generation at application time. Show paused/ambiguous/no-session state explicitly; clear stale local session fields appropriately.
4. Verify the reported above-composer consent control's authority before changing placement. Unify successful removal/error recovery across actual action/run controls only if the trace shows divergence.

**Invariants:** consent prospective; no timestamp rewrites/backfill; session selection independent of grant; paused stays paused; ownership/enrollment/semester/revision checked; original run/question retained; repeated decision creates at most one execution receipt; no evidence from document open, request observation or consent alone; no transfer of another session's waiting interaction; R1 replay and R2 atomic outbox remain valid; existing PostgreSQL parent-first/self-row lock order preserved.

**Required disposable PostgreSQL regressions:** question-form and Explain-form first lesson; document-mapped lesson; decline and renewal while paused; legacy/canonical collision; concurrent approve/duplicate/handoff/revoke; ordinary exposure finalizes exactly once from prospective source; assistant/finalization rollback atomicity; waiting run response through actual HTTP SSE not just execute_claimed return; reconnect after result commit/before response delivery; parked question preserves identity; cross-owner and stale generation denied. Add fixtures for both schema/snapshot generations. Use existing isolated `scripts/test-sals-wp4-postgres.sh` and concurrency runner only after confirming their isolation guards; never aim Django tests at owner DB.

**Browser acceptance:** new OOP lesson with no prior session → approve → expected active canonical header, complete visible segment and one pending question → reload → same session/message/question; Not Now → ordinary untracked lesson; renewed grant on paused session → paused header until explicit Resume. Exercise dedicated card, activity-trail control and restored control on desktop/mobile, two tabs and conversation-switch races.

**Rollback:** revert coordinated consumers together, retain additive schema and existing action/boundary/result receipts. Never drop/rewrite completed learning or original pending interactions. A UI-only rollback must still understand the server response contract; assess cached assets and queued snapshots before deployment.

**Expected student behavior:** one decision starts the requested tracked lesson prospectively, header matches actual lifecycle, both explanation and next question remain visible, completed exposure survives reload without duplicating work.

## Package 2 — Make completion and recovery truthful

**Priority:** blocks acceptance of Package 1's “completed learning” and all later recall. RC5/RC6 are independent of header repair. Land normalization/negative tests early; do not postpone a false-completion fix behind cosmetic acceptance.

**Files/services likely affected:** `pwanimate/ai/gateway/types.py`, gateway/gateway.py, provider groq/openrouter/gemini adapters; `orchestrator/service.py::_generate/_needs_continuation`; `services/teaching.py::finish`; initial `api/views.py` and resumed `services/agent_execution.py`; `api/agent_runs.py` durable completion projection; message/trace serializers if metadata must be retained without a migration; tasks/agent_runs.py monitoring semantics; browser partial/recovery presentation.

**Proposal:** normalize raw and canonical finish reasons once; use a shared affirmative completion decision with explicit handling for authored deterministic segments. Keep raw provider reason and terminal-marker evidence in safe diagnostics. Unknown terminal state/known partial output must remain recoverable/incomplete, including after reconnect. Preserve one bounded continuation and idempotent attempts; avoid blind provider replay on uncertain outcomes. Define cancellation during final generation/result commit and expired-attempt fencing. Inspect current payload/metadata capacity before proposing any schema work.

**Invariants:** visible partial text is preserved honestly; no known partial output becomes completed explanation/exposure/mastery; provider errors do not masquerade as successful run; Celery SUCCESS plus error payload is an error; persisted message and terminal pointer agree; no duplicate completion/assessment; pause/revoke stops new evidence; token/cost accounting includes bounded continuation; no private prompts/reasoning in logs.

**Required PostgreSQL regressions:** parameterized stop/length/max_tokens/MAX_TOKENS/interrupted/content_filter/error/missing marker through actual initial/resume persistence; bounded continuation success/failure/repeated limit; kill/disconnect simulation at result commit boundary; stale attempt/lease versus current result; concurrent cancellation, pause, handoff and revoke during finalization. Assert dialogue may persist while completion/evidence remain absent. Queue publication accepted with no worker must remain pending/interrupted, not successful. Returned Celery error payload must fail application-success assertions.

**Browser acceptance:** interrupt a long explanation/code sample; show partial/incomplete state and a deliberate recovery option. Reconnect/reload compares persisted final content with display and never appends duplicates. Recover to actual completion before showing recorded completed activity. Do not assume visual code-fence closure proves provider completion.

**Rollback:** preserve raw completion diagnostics and partial/durable receipts; do not downgrade new partial states to completed on old clients/workers. Roll back producer/consumer semantics together; never automatically recertify historical partial records.

**Expected student behavior:** a cutoff is recognized as incomplete, recovery is explicit/bounded, a genuinely finished explanation is visible and recorded once.

## Package 3 — Restore precise cross-conversation history

**Dependency:** Packages 1/2 create trustworthy eligible events. Investigate raw rows before changing retrieval. RC7/RC8.

**Files/services likely affected:** `services/unit_learning.py::summary/context_for_query/tracking_state`, `academic_progress.py::progress_summary`, `study_context.py::ResolvedStudyContext.payload`, `api/unit_learning.py`, context/types.py and orchestrator context assembly; study desk/unit/session JS only for projection labels and history navigation. Personal facts stay in `personal_memory.py`/`persistent_memory.py`; R2 dispatch is delivery, not the history source.

**Proposal:** maintain explicit separate read posture and new-write authorization. Resolve named unit/semester history from retained eligible records, clarify ambiguous generic queries, and distinguish paused session/request activity/completed explanation/outstanding practice/verified mastery in model context and UI. Preserve bounded context and ensure truncation retains the most relevant continuity facts. Confirm a model ignored provided nonempty history before proposing prompt changes.

**Invariants:** no authorization by reading; disabled recall honored; owner isolation; term identity preserved; no raw-transcript recall restoration; no import of pre-consent content; no merging sessions/records; independent WP1 evidence unchanged.

**Required PostgreSQL regressions:** persisted completed teaching with sources appears in raw row, summary, API and formatted ContextPackage in another conversation; ask-mode new writes denied while allowed named historical read succeeds; old revision/current authorization, closed/inactive term, multiple semesters/units, disabled preference, deleted/revoked sources, foreign owner; bounded history truncation; R2 failed/retried dispatch cannot alter academic counts.

**Browser acceptance:** after actual completed OOP activity, pause, open another chat, ask where you stopped with unit/term; truthful prior topic/status plus retained practice reference. Return explicitly to original question or authorize a genuinely new “continue here” boundary. No active-tracking claim in an unauthorized new conversation.

**Rollback:** preserve stored learning and read/write separation; revert projection consumers together; never clear retained records to match an empty UI.

**Expected student behavior:** Spark can describe recorded learning across chats without pretending new-chat tracking is already active or that request/session existence proves mastery.

## Package 4 — Add lightweight grounded conversational continuity

**Dependency:** explicit historical read posture from Package 3; greeting matcher coverage can land earlier independently. RC9.

**Files/services likely affected:** `orchestrator/service.py` casual classification/routing/context assembly; `services/study_memory.py::welcome` only through a consent-compatible replacement; existing study session/academic read helpers; `conversation_memory.py`, `persistent_memory.py` for intentional preference relevance only; ContextPackage/prompts and user context. No new agent, transcript summarizer or document-retrieval subsystem.

**Proposal:** recognize brief compound/slang greetings before resource/study state forces expensive work; obtain at most a bounded authorized recent-session/progress summary without activation or remote retrieval. Include relevant approved response preferences independently of incidental greeting-word overlap. Offer continuation only for a real recent authorized activity; clarify or stay generic where ambiguous/absent. The old raw digest path is intentionally quarantined and must remain so.

**Invariants:** zero planner/tool-selector/document/tool calls for pure casual conversation; at most one bounded answer call; no fabricated history; no session mutation or new evidence; consent/opt-out/recency/ownership respected; genuine academic/resource questions still use grounded routes. A prior paused session is not a completed lesson.

**Required PostgreSQL regressions:** real authorized paused/completed-turn context versus only session/request/no record; multiple units/terms; disabled memory versus disabled academic recall independently; revoked consent/historical read posture; relevant approved preference; zero extra rows or last-access writes from the dedicated session read helper; exact greeting examples with no selected resources, open PDF and active Study Mode. Assert no embeddings or providers in deterministic context selection and no unnecessary tool/planner calls using mocks.

**Browser acceptance:** “Wagwan Spark, how are you?” yields a brief natural answer, optionally offering the real paused OOP session. It shows no document investigation/checklist. “Continue” then follows the actual owned session/run/authorization contract. Generic greeting remains generic without eligible history. Test mobile/desktop and restored state.

**Rollback:** retain existing consented facts; disable only new bounded continuity projection if needed; do not fall back to broad transcripts or expensive agents for every greeting.

**Expected student behavior:** natural brief conversation, grounded optional continuity, no unnecessary investigation and no invented prior progress.

## Release acceptance and roadmap

Test success is a prerequisite, not student acceptance. Run focused disposable PostgreSQL suites, then a deliberately authorized real-browser sequence from ACCEPTANCE_CHECKLIST.md with release/schema/worker/asset attestation. No SRI-01 action changes deployment. Any observation still lacking live evidence remains unverified after unit tests pass.

Preserve pending Phase B live continuity acceptance; WP4 real-browser acceptance and pending summaries/revision-document follow-on; R3 Progressive Hint Restraint; R4 Response Quality, Orchestration and Runtime Optimization; R5 End-to-End Student Acceptance; SALS 0.4 Academic Content Intelligence; SALS 0.5 Quizzes, Assignments and Mock Exams; later student-progress/revision features; separate Vasto-wide Lucide icon migration. These repair packages are reliability prerequisites, not completion or cancellation of that roadmap.
