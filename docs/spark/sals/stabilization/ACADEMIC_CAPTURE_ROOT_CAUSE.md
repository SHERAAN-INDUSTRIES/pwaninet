# Academic Session Capture and Consent Timing — Phase A

Date: 2026-11-14. Scope: Spark inside PwaniNet/Vasto. **Read-only application investigation; no repair authorized.** Only this report was created. No application code, migrations or existing user data were modified. No databases, Docker, PostgreSQL suites, providers or browser sessions were invoked. R2 PostgreSQL completion was not verified here and remains an owner gate before Phase B.

## Findings and evidence limits

The owner's live observation is consistent with multiple concrete source-level disconnects: consent is presented after the answer, approval may establish a session in another conversation, and the unit-history summary does not project teaching-turn records. Session existence is not proof of authorization, capture or mastery.

**Observed live evidence supplied by owner:** SMA B203 — Linear Algebra I recognized; one active session; empty topics, activities, approved observations, difficulties, assessments and roadmap; mastery_assessed false; consent requested after the lesson.

**Confirmed by current source inspection:** the control-flow and data-projection defects below. These are deterministic branch traces, not a newly executed live reproduction. No claims are made about the owner's exact preference revision, session IDs, learning-record linkage, logs, turn rows or approval timestamp because those rows were not inspected. The observation alone does not distinguish a new, legitimately empty authorized record from a legacy session or a populated teaching state omitted by retrieval.

### Defects and limitations established from source

1. **Late consent presentation:** detection and PendingAction creation precede generation on the normal commit path, but pending academic actions are serialized only after orchestration and assistant persistence. They are rendered with/after the assistant bubble. No dedicated early academic-consent SSE event is emitted by this path.
2. **Conversation/session binding gap:** `approve_action` records the requesting conversation as authorized, then `_session` creates/reuses a unit session without passing that conversation. A newly created session therefore gets a separate conversation. `TeachingService.session_for` selects only sessions belonging to the current message's conversation. The original chat can be authorized for unit activity yet miss its SALS teaching session.
3. **Capture/projection split:** `SparkUnitActivity` records attributed student requests; `SparkTeachingTurn` records teaching transitions/attempts. `UnitLearningService.summary` reads the former, not the latter. Completed teaching cannot automatically appear as unit-history progress through this summary.
4. **No generic answer-completion capture:** normal conversational/RAG answers do not write semantic lesson-completion events. A request activity is recorded before the answer; it does not establish that a topic was actually taught successfully.
5. **Follow-up attribution gate:** short answers such as “continue”, “yes” or a numeric exercise answer generally do not identify an enrolled unit through `detect`. The teaching continuation path can handle them through an owned interaction/run, but independent ordinary chat messages do not gain equivalent attribution. Legitimate activity may therefore live only in teaching receipts, or never reach the teaching controller.
6. **Misleading summary field:** `mastery_assessed` is always False in the SALS 0.2 summary; it does not consult WP1 evidence. Empty difficulties/observations/roadmap are also not reliable proxies for lack of all learning activity.
7. **Silent capture failure window:** academic message processing is an on-commit callback guarded by `process_safely`, which logs and returns empty detection on errors. There is no durable academic-capture reconciliation in that callback. This is distinct from R2's personal-memory extraction outbox.

## Execution timeline

### Ordinary academic message

1. `PwanimateChatView._post_chat` resolves identity, conversation, engine and explicit Study Mode options. Resume/interaction submissions branch to the existing durable continuation API before normal message handling.
2. `ConversationService.persist_user_message` saves the student message in a short atomic transaction. It registers embedding and `UnitLearningService.process_safely(message_id)` on commit.
3. On a normal outermost commit, callbacks run before `persist_user_message` returns to the view. `process_message` calls `detect`, resolves current eligible enrollments/semester and checks academic tracking preference and record authorization. If another enclosing transaction defers commit, callback timing can differ; do not equate on-commit with an early browser event.
4. If unauthorized, `ActionService.propose(ACTION_TRACK_UNIT, ...)` persists a PendingAction with enrollment, semester, source-message and tracking-revision provenance. It does not record that source as learning activity. If authorized, `_session` obtains a unit-linked session and `SparkUnitActivity.get_or_create(record, source_message, topic)` records a request span before generation.
5. If explicit legacy Study Mode is enabled, the view separately calls `StudySessionService.start_or_continue_session(conversation=current_chat, ...)` and builds its checkpoint context. This path can produce a session without SALS academic authorization.
6. The view loads bounded conversation history, personal memory, `get_academic_context` and `tracking_state`, then constructs `OrchestrationRequest`.
7. `PwanimateOrchestrator.run` tests `TeachingService.applies`. That lookup is conversation-local; linked sessions additionally require consent revision and fresh eligible attribution. Attachments explicitly bypass this teaching route. Without an applicable session, other routing/planner/RAG paths can still teach in ordinary generated prose.
8. If routed to teaching, `prepare` locks/validates state, creates an idempotent turn and possibly a pending interaction/run. `finish` generates or retrieves an answer and saves its response text. Otherwise no equivalent teaching turn is guaranteed.
9. The view persists the assistant response, then reads pending academic actions into final response `academic_actions`. R2 separately schedules eligible personal-memory extraction; that does not create academic activities or teaching outcomes.
10. The browser's final-response handler renders the answer and `_academicTrackingBlock` with Start Tracking / Not Now. Poll/reload refresh can also restore pending cards, but the normal response does not wait for a tracking decision before streaming the lesson.

### Consent approval

`_academicTrackingBlock` posts to the existing action API. `ActionService.confirm` invokes `UnitLearningService.approve_action` under owner-checked transaction boundaries. Approval checks current preference revision, eligible enrollment/semester and source attribution, sets `authorized_at`, `consent_revision` and `authorized_conversation`, and obtains a linked session. It explicitly returns `historical_content_imported: false`. The frontend removes the card; it does not resume a paused academic preflight or replay/capture the already delivered lesson.

This means the triggering pre-approval question and lesson remain excluded by design. Consent arriving after delivery cannot retrospectively authorize that lesson. If there have been no qualifying post-approval messages, empty activity arrays are expected even though a linked session now exists.

## Consent timing and authorization root cause

Detection is not generally happening after the answer. **Persistence and presentation happen at different points.** The final `academic_actions` serialization and answer-attached DOM placement cause the visible timing. `ActionService.propose` stores an action; the inspected unit-tracking proposal path does not emit a dedicated progress/interaction event through the request's SSE callback. Teaching question events emitted from `prepare` are a separate mechanism and do not solve academic-consent timing.

The model receives `tracking_state`; `_apply_study_and_warning_context` instructs it to describe pending cards rendered “with this reply”. That instruction can encourage a consent mention at the end of a lesson; it cannot enforce UI ordering or decide consent. A card must never be inferred from generated prose.

An existing active session is authorized only after checking, together:

- Correct student, enrollment, academic unit and semester; current enrollment/unit/term eligibility.
- Non-null record authorization timestamp; matching academic preference revision; preference not disabled.
- In ask mode, the authorized conversation and an active linked session. `process_message` additionally requires source time strictly after authorization.
- Correct session `learning_record` linkage and originating conversation/runtime identity.

`process_message` and `tracking_state` use conversation-scoped ask-mode semantics. `tests/sals_02/test_foundation.py::test_ask_mode_needs_new_conversation_approval` explicitly expects new-conversation approval; `test_automatic_cross_conversation_continuity_and_history` covers automatic-mode future recording. Changing ask mode to never prompt across all conversations would change the existing consent contract and must be an explicit Phase B policy choice. Valid authorization in its existing scope should not prompt again; another scope, changed revision, ended session or invalid term is not equivalent authorization.

Legacy `@study` can set `status=active`, unit fields, current_topic and objective without setting `learning_record`, `authorized_at` or tracking revision. `TeachingService.applies` permits legacy unlinked tutoring; it must not be labelled authorized SALS recording. Even a summary's linked active session does not prove that the original conversation is using it. `session_for(...).first()` also does not explicitly select by unit/record and active status before returning a candidate; mixed/multiple sessions can select the wrong runtime context. Actual account classification requires owner inspection of the above IDs/timestamps; this report does not assume which row was observed.

## Learning-event persistence root cause

### What exists

- `SparkUnitActivity`: an authorized, detected span of a student message. Kinds are study, difficulty or self_report. It is not a record of successful answer generation. `process_message` is the producer in the inspected ordinary-message path.
- `SparkTeachingState`: current objective, concept, phase, mode, exercise, hint level, recent errors and active run.
- `SparkTeachingTurn`: receipt key, run, answer/question interactions, question, from/to phase, action, activity_kind, assistance, evaluation, response text, metrics and prepared payload.
- `TeachingService.prepare`: persists phase transitions and turns, assigned questions and durable interactions before answer generation. Exercise evaluation occurs on owned answer interactions; formal assessment questions delegate to `AssessmentService.submit_answer`. Formative results distinguish hinted/guided versus independent attempts; repeated errors can adjust state and feedback.
- `TeachingService.finish`: saves response_markdown and changes explanation/demonstration activity_kind to explanation_completed when finishing. This is not projected into unit activities or the summary. It also precedes final assistant-message persistence and does not by itself certify an uninterrupted, fully delivered lesson.
- `ConceptMasteryService.record_activity`: can turn an existing unit activity into observational evidence; it is not a universal producer of teaching-completion activity. Verified mastery uses WP1 assessment provenance, independence and retention rules.
- Legacy checkpoints and session roadmap use separate Study Mode services. An active SALS session does not populate them automatically.

### What the observed fields actually mean

`summary.topics` and `summary.activities` come only from record.activities. Empty arrays mean no such rows are projected, not necessarily no teaching turns. `difficulties` selects only activity kind difficulty, using narrow student-phrase matching; generated misconception feedback/recent_errors is not included. `approved_observations` joins approved personal academic-purpose facts through activity source IDs and topic matching; this independent memory-consent/quality pipeline may legitimately yield none. `assessments` includes actual SparkAssessment rows for listed sessions, not every formative exercise. `sessions[].roadmap` is the stored legacy roadmap, not a reconstruction from teaching turns. `mastery_assessed` is a literal false, not a WP1 query.

Two failures must therefore be diagnosed separately: **missing production/attribution** when TeachingService never runs, versus **missing projection** when its turns exist but the summary ignores them. Pre-approval exclusion is intentional safety behavior rather than a reason to synthesize old progress. Even successful student-request capture does not meet the desired contract of genuinely completed learning steps.

## Cross-conversation retrieval root cause

Retrieval and prompt injection do exist:

- `UnitLearningService.context_for_query` chooses owner records by detected unit/topic or explicit unit name/code, limited to 100 inspected records/three selected records. Each summary limits history to 100 activities and sends the latest ten activity items.
- `PwanimateChatView.get_academic_context` supplies those summaries to the request; durable `agent_execution.execute_claimed` refreshes academic context and tracking state on continuation.
- `ContextPackage.to_prompt_context` renders academic_unit_records with instructions distinguishing self-reports/requests from mastery. Conversational, tool and RAG paths copy the request academic context; StudyAgent also adds unit_learning_records/history context.

Thus missing prompt plumbing is not a blanket explanation. Empty stored activity or a summary that omits teaching turns is faithfully injected as empty history. A vague “continue” or “what did I study?” in a new chat may select no unit at all; there is no blanket automatic recall of all units. Detection also needs recognizable phrasing/topic evidence on ordinary messages, even when a session is active. Conversation-local session lookup further prevents authorized history alone from restoring a teaching controller in that chat.

`context_for_query` itself selects by owner/unit, without enforcing the recording authorization checks used by process_message; summary's read_only checks enrollment eligibility, not consent revision. Phase B must explicitly distinguish permission to read retained history from permission to record new work and specify revocation behavior. Do not solve continuity by trusting cached or stale consent. Multiple semester records for one named unit can be selected; the current selector is not an exact current-term teaching-session resolver.

## Proposed minimal Phase B repair — not implemented

1. Add one academic preflight using existing Units/action/runtime infrastructure. Resolve eligible unit and exact semester/session identity once; return an explicit posture: authorized, needs consent, declined/disabled, ambiguous or read-only. Reuse pending consent and valid existing scope; never use session existence as authority.
2. Publish an existing-style structured consent interaction before visible lesson content. Define the choice explicitly: pause the current durable run for a decision, with Not Now continuing untracked, or display an early optional card without blocking tutoring. Neither choice may authorize recording before approval. Reuse owned run/interaction claims and journal/SSE; no second orchestration engine.
3. Bind authorized learning context to the exact session and conversation the runtime will use. Avoid `_session` silently placing consent's teaching state in another chat. Preserve existing dedicated unit conversations through explicit routing/binding where appropriate; do not reassign legacy sessions or steal waiting runs. Clarify ask-mode scope before changing repeat-prompt behavior.
4. Keep input request observations separate from completed outcomes. On durable assistant persistence/success checkpoint, project actual completed teaching turns into a typed unit-progress view or an idempotent outcome record. Preserve assigned versus attempted versus feedback-completed states, topic/concept provenance, assistance and outcome. Do not count prepare-time transitions or streamed first tokens as completed teaching. Incrementally finalize successful steps, retaining interrupted/pending state when completion is uncertain.
5. Prefer joining existing teaching turn/state/assessment data into the unit-history projection where sufficient; do not duplicate facts into personal memory. For lessons outside TeachingService, record only outcomes with reliable owned runtime provenance and genuine completion. Never infer exercise success or misconceptions from arbitrary generated text.
6. Use a single fresh authorization guard before recording and again at finalization. Bind student/enrollment/semester/record/session/run/message and interaction. Handle short responses through the already-owned pending interaction or explicit current teaching context; do not loosen global topic detection to attribute arbitrary chatter.
7. Return/inject authorized unit history including typed recent completions, outstanding owned exercise/checkpoint and last-covered concept. Keep formative results, self-reports and verified mastery distinct. Replace the hard-coded mastery flag only with an actual evidence-backed result. Fresh chats should resolve the intended unit/term safely, asking clarification on ambiguity.
8. Make finalization recoverable and idempotent with stable turn/interaction/message keys and transactional persistence or reconciliation. R2's outbox is for personal-memory extraction; it must not be repurposed as academic evidence or silently expanded by Phase A.

## Migration requirements

No migration is needed for this investigation. A substantial part of Phase B can reuse existing action, session, teaching-turn and assessment models through binding and projection changes. If existing turn receipts and response linkage suffice, avoid new storage. If a durable per-outcome finalization receipt/status or explicit approved consent scope is necessary, use the smallest additive migration with uniqueness on owned event provenance. Do not overload SparkUnitActivity's request-only meaning, retrofit all past chats or add broad data migrations. Decide schema only after inspecting real affected-row provenance and approving the consent-scope contract.

## Regression-test plan for authorized Phase B

- Full request order: persist detection/proposal, emit consent before first visible lesson content, then approve/decline; verify no unauthorized events before approval.
- Existing valid authorization: no duplicate proposal; ask versus automatic mode; ended session, revision changes, revoked consent, term/enrollment changes, ambiguous units and wrong owner.
- Original-chat approval produces the exact session/context TeachingService uses; legacy @study stays distinguishable; multiple sessions cannot select a different unit/term.
- Post-approval completed explanation appears in history as exposure, never mastery. Assigned exercise, independent/assisted attempt, feedback and formal assessment remain distinct.
- Real unit summary projects actual teaching turns and last-covered concept; absence of optional personal memory/roadmap is not treated as failure.
- New chat named unit/recognized topic sees correct term history in the actual model request; vague continuation asks clarification or uses explicit safe binding. Test retained-history read policy separately from write consent.
- Failure before generation, interrupted provider output, persistence failure after generation, worker restart and run resume do not produce false completion. Successful finalized steps survive and duplicates do not create another checkpoint/evidence row.
- PostgreSQL simultaneous approvals, consent revocation/finalization and duplicate/resumed input races; browser SSE event order, reload/reconnect and early-card interaction.
- Keep existing SALS memory/R1/R2, academic, mastery and legacy Study Mode regression suites. Historical pass totals do not establish this new contract; Phase A ran no tests.

## Risks and existing-data handling

Do not backfill pre-consent source messages or ordinary chats. An approved active record with no events can be valid. Do not erase or relabel it merely because lists are empty. Existing unit request activities must remain request observations; never upgrade them to completed learning or mastery. Existing teaching receipts may contain useful provenance but historical projection/reconstruction requires explicit owner authorization, authorization-at-event checks and separate evidence classification. Preserve ownership/semester boundaries, user deletion and declined consent.

Owner evidence needed before choosing a repair: the observed record/session IDs and linkage, preference mode/revision, record authorization timestamp/conversation, current request conversation, pending-action source/revision/outcome, relevant teaching-turn/run/interaction IDs and final assistant message, and any process_safely error logs. Retrieve only the minimum necessary through an authorized read-only check; no such account-data check was performed in Phase A.

**Stop boundary:** investigation/report complete. Phase B is not started and awaits explicit owner authorization after the R2 PostgreSQL gate.
