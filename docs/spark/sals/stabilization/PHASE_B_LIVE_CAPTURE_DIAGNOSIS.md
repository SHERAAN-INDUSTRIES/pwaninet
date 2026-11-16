# Phase B Live Academic Capture Diagnosis

Date: 2026-11-14. Scope: the owner's SMA B203 — Linear Algebra I record and the two specified conversations. **Read-only investigation; no patch implemented.** The owner reports 300 disposable PostgreSQL tests passed after the locking hotfix; that result does not constitute browser acceptance.

## Executive finding

The empty summary is accurate for the inspected database. There are **zero unit activities, zero teaching states, zero teaching turns and zero concept evidence** for this record; there are also no teaching states or turns elsewhere under this owner. Retrieval is not dropping completed teaching records.

The newer conversation contains two persisted request/response pairs:

1. The original academic request was submitted **before tracking approval**. Its generated answer was persisted after approval, but the existing contract authorizes future source messages rather than retroactively authorizing an already-started request. Its exclusion is intentional under Phase B's nonblocking-consent design.
2. The post-approval message reports that the response was cut off. It does not repeat a unit/topic and is not a fresh subject-specific lesson request. Current deterministic detection rejects it at the meaningful-request gate. TeachingService finds the correct active session but rejects attribution, and the trace confirms ordinary RAG handling. The subsequent mathematical answer is persisted, but there is no authorized request activity for ordinary-answer exposure finalization to use.

**Confirmed cause of this stored scenario:** pre-consent exclusion followed by a context-dependent interruption complaint that is not attributed or handled by the teaching controller. **Not established:** a fresh, explicitly attributable post-consent lesson being lost by finalization. No such request exists in the inspected newer conversation. Do not manufacture completion for either exchange.

## Read-only method and redaction

Host process metadata was inspected without signalling, restarting or attaching a debugger to any process. Database probes used the live web child's environment and local Django settings, including a probe under the web server's `venv` interpreter. PostgreSQL connections used `default_transaction_read_only=on`; probes explicitly entered repeatable-read/read-only transactions, verified read-only state and ended with rollback. No model/provider requests, application HTTP writes, migrations, Docker commands, consent mutations or data repairs were performed.

The following read-only helpers were evaluated: Units.detect, TeachingService.session_for/applies/attributed, UnitLearningService.summary/context_for_query/tracking_state, PwanimateChatView.get_academic_context, and ContextPackage.format_context_text. No process_message, prepare, finalize, approval or preference-creation helper was invoked.

Identifiers below use aliases: owner O, academic record R, enrollment E and sessions S-old/S-new. Conversation prefixes are the two already supplied by the owner; no full identifiers, account names, credentials or lesson bodies are included. All timestamps below are **UTC**; Nairobi is UTC+03:00.

## Deployment and migration verification

- The live listener is a uvicorn reload parent with a Python child on loopback port 8000, working from `/home/zayne/projects/pwaninet` and using `pwaninet.settings.local`. The web interpreter is the project's `venv`, not an assumed `.venv` invocation.
- Read-only ORM access resolved the two exact conversation prefixes to the same owner and found the reported SMA B203 enrollment/record. PostgreSQL was the active database engine. Connection identifiers were compared through redacted fingerprints; no evidence of inspecting an unrelated test database was found.
- `django_migrations` records **0037_academic_completion applied at 2026-11-13 23:36:38.762869 UTC**. Both new binding and completion fields were successfully queried. Migration absence is ruled out for this database.
- The current web child started at **2026-11-14 00:25:36 UTC**, after the two locking-hotfix service files were modified at **00:24:51 UTC**. Current disk source contains both `select_for_update(of=('self',))` fixes. Process timing and paths are consistent with the current hotfix being loaded; a fresh ORM probe does not inspect the live child's in-memory bytecode, so absolute callable-level attestation is not claimed.
- The persisted lesson exchange occurred at **00:12–00:13 UTC**, before that current web child and before the locking-hotfix file timestamps. Its trace contains Phase B preflight/history timing marks, proving Phase B handling was in use, but it is not evidence of a browser rerun after the current hotfix/restart.
- Celery workers exist in this checkout using the local settings and consuming default, media, docs and search queues. The worker parent started at **2026-11-13 23:47:49 UTC**, before the locking hotfix. Loaded worker module versions cannot be certified from process metadata; stale worker code is an operational risk until checked/restarted under owner authorization. No separate beat process was visible in the inspected process snapshot. Neither observation explains these two responses: the target conversation has **zero agent runs**, and its traces show synchronous ordinary answering rather than Celery continuation.
- Current server/worker stdout and stderr point to terminal devices. The on-disk Django log is dated October 3 and is not a current November request log. Terminal streams were not read or consumed. Durable request traces, rather than unavailable historical console output, supply routing evidence.

No processes were restarted and no migrations applied.

## Actual record and session relationships

**Owner O / record R:** one SMA B203 record was found for this owner. Enrollment E is active, unit/term eligibility succeeds, and its semester matches the record: Semester 1, 2026/2027. Academic preference is **ask**, revision **0**. Record revision is also **0**. Its current authorization timestamp is **2026-11-14 00:12:55.310751 UTC**, scoped to `1485a1a1`.

- **S-old:** active; home conversation `6a70cf9e`; academic_conversation binding NULL; learning_record R; started November 12 at 18:33:01 UTC. It has no teaching state, active teaching run or turns. It is a linked academic foundation session, not an unlinked legacy session. However, it is **not authorized in the current ask-mode conversation scope**: tracking_state for the old chat reports no active authorized unit tracking.
- **S-new:** active; home conversation and academic_conversation both `1485a1a1`; learning_record R; created **00:12:55.420819 UTC**, during approval. It has no teaching state, active teaching run or turns. tracking_state for this chat reports SMA B203 as active authorized tracking.

Thus “two active sessions” describes status rows, not two controllers that have recorded learning or two currently authorized ask scopes. There is no unlinked legacy session in either inspected conversation. Ordinary Study Mode/session timestamps can advance without unit activities; S-new's last-active update is not proof of capture.

The current authorization overwrites the record's prior grant timestamp/scope. This investigation does not infer the exact historical consent state of all November 12 messages from the current grant alone.

## Consent and lesson timeline

Aliases M1–M4 identify the chronological messages in the newer chat; all personal content is redacted.

- **M1 — 00:12:44.811394:** initial academic request. The current detector recognizes the enrolled SMA B203 subject. This is **10.499 seconds before** record authorization.
- **00:12:45.079266:** persisted tracking proposal created for M1, revision 0.
- **00:12:55.310751:** record authorization begins.
- **00:12:55.420819:** S-new created and bound to the current chat.
- **00:12:55.433052:** pending action confirmed; result records historical_content_imported false.
- **M2 — 00:12:56.374875:** assistant lesson persisted. Trace intent is routine_tutoring with fast_path_direct, not teaching_path. Source M1 remains pre-consent even though M2 is later than approval.
- **M3 — 00:13:34.908068:** short interruption complaint, **39.597 seconds after** authorization. It references the preceding answer without naming a subject. Current read-only detection returns no matches, and the meaningful-request gate is false.
- **M4 — 00:13:43.713288:** mathematical response to M3 persisted. Trace intent and task are rag, with rag_path and orchestrator_done present. No teaching_path mark exists.

The M1 trace records academic_consent_published at **1,399.70 ms** and first_token at **10,595.34 ms** relative to that request: the consent event preceded the first token by about **9.196 seconds**. This supports the owner's observation that early presentation worked; it does not make the already-submitted source message post-consent. Exact approval-versus-first-token wall-clock ordering is not inferred from mixed wall-clock and monotonic timestamps.

For M3, the trace has academic_preflight_done and academic_history_done but no new consent event. That is consistent with valid existing authorization and no repeat proposal.

Re-evaluating M1's applies predicate now returns true because S-new now exists. That is **not** its historical predicate at generation time. The recorded routine_tutoring route is the historical evidence.

## Exact persistence trace and first missing stage

### Initial lesson

`ConversationService.persist_user_message` → `UnitLearningService.process_message` → detection recognizes SMA B203 → no grant yet → PendingAction only → no authorized SparkUnitActivity.

At initial answer routing the trace shows the routine-tutoring fast path rather than TeachingService. Approval creates the foundation session during this request, but does not restart the request as a new post-consent teaching turn. Ordinary finalization has no authorized source activity. No historical import is performed. This is the current privacy boundary, not a demonstrated completion-write failure.

### Post-consent complaint and generated answer

Current read-only predicate results for M3:

- Owner, enrollment, semester, preference revision, authorized conversation and source timestamp: valid.
- Attachments: none, so attachment bypass is not the reason.
- TeachingService.session_for: selects S-new correctly.
- `UnitLearningService.detect`: no matches; meaningful-request predicate false.
- `TeachingService.attributed`: false.
- `TeachingService.applies`: false.

The rejection first occurs in `unit_learning.py::detect` at the meaningful/history/special-continuation gate. Its narrow exact continuation fallback does not recognize a complaint about truncated output. `process_message` returns before activity creation. TeachingService repeats that attribution requirement and ordinary orchestration handles the reply instead. The persisted trace independently confirms RAG routing.

`academic_progress.finalize_ordinary` requires exactly one authorized source activity with a session. There are none for this exchange, so it returns before creating a turn/state. The nullable-join finalization queries are therefore not needed for this observed path; absence of the locking hotfix at the historical test time does not explain these empty rows.

**Raw evidence:** R has zero activities, zero turns, zero finalized turns, zero assessments and zero concept evidence. Both sessions lack teaching states. Across this owner's sessions, teaching-state and teaching-turn counts are also zero. There is no teaching progress hidden under a different session or semester.

M2 and M4 are durable assistant messages and have durable successful-return request traces. This shows they were served/persisted. It does not prove complete provider output: the trace schema does not retain provider finish reason or an exact final model request. The owner's interruption complaint must not be converted into proof of a completed lesson. No failed or rolled-back teaching-finalization attempt is established; the evidence points to earlier routing/eligibility gates, not a missing completed_at update on an existing turn.

## Retrieval verification

The following existing functions were executed against the real owner record in enforced read-only transactions:

1. `UnitLearningService.summary(R, O)` — empty activities, covered_topics, teaching_progress, checkpoints, assessments and approved observations; last-covered absent; mastery_assessed false.
2. `context_for_query` for named SMA B203 history/stop prompts and generic previous-lesson/mistakes prompts — selects R and returns those same empty values.
3. `PwanimateChatView.get_academic_context` — equals the direct service output for each probe.
4. `ContextPackage.format_context_text` — includes the academic_unit_records block and teaching_progress key with the empty data; rendered probe block was 2,450 characters.

The first missing stage is **event eligibility/creation**, not summary selection or prompt formatting. Empty arrays survive each layer accurately. There is no verified mastery evidence to expose.

This verifies current model-facing context reconstruction, without calling a provider. The exact historical model-facing request was not saved in the inspected trace schema, so historical prompt contents cannot be independently replayed byte-for-byte from these rows.

## Hypotheses resolved

- **Pre-consent lesson intentionally excluded:** confirmed for M1/M2.
- **Valid fresh post-consent academic lesson lost:** not demonstrated; M3 is an interruption complaint, not a new subject-specific request.
- **Context-dependent post-consent response not recorded:** confirmed as an outcome of attribution gates; M4 contains mathematical tutoring but has no authorized input activity. Treating that answer as completed exposure requires an explicitly approved capture contract, not automatic inference.
- **Wrong session/current authorization:** ruled out for M3 by current bindings, timestamps and read-only predicates.
- **Untracked legacy session mistaken for SALS:** neither inspected row is unlinked legacy; the old row's active status is misleading about current ask authorization, but is not the cause of M3's route.
- **Ordinary answer already captured as exposure:** ruled out by zero turns and zero source activities.
- **Existing completed turn missing finalization:** ruled out as the observed storage pattern; no turn exists to finalize.
- **Stored progress dropped by retrieval:** ruled out by raw counts and equal service/API/context output.
- **Missing migration:** ruled out.
- **Current stale web deployment:** not supported by restart/source timing; historical browser exchange predates the current hotfix/restart. Potential stale Celery modules remain an independent operational uncertainty, not this exchange's routing cause.

## Smallest corrective proposal — not implemented

First establish the intended acceptance contract. Current nonblocking consent deliberately leaves the initial in-flight lesson untracked. If the first lesson must count, request approval **before beginning that lesson**, through the existing durable action/run interaction, or offer an explicit post-approval “Begin tracked lesson” action/new turn. Recording should use that post-approval decision/input provenance and genuinely later completed teaching, never rewrite M1's timestamp or import M2 retrospectively.

For continuity, introduce one shared, bounded contextual-attribution resolver for post-consent follow-ups. It must require the exact owned/current unit-term binding, current consent revision/scope, unambiguous recent local academic provenance and a recognized follow-up intent. Reuse it in request capture and TeachingService eligibility so they cannot disagree. Topic-free arbitrary chatter, thanks and unrelated requests must not become academic events merely because a session is active.

Handle interruption complaints explicitly as response-repair requests, not answers proving understanding. If an owned original logical run exists, preserve its identity through the established recovery interface. This observed exchange has no original agent run to resume: a genuine new operation must not pretend to resurrect one. Do not label the prior answer completed or create mastery evidence from the complaint. Broader automatic response-recovery behavior remains outside this diagnostic task.

No new schema is shown necessary by these rows; existing action, source, session binding and receipt provenance can support a minimal coordinated repair. Restarting/verifying workers after deployment is an operational prerequisite, not a substitute for fixing attribution or defining first-lesson consent semantics. No patch, restart or backfill was performed here.

## Targeted acceptance and regression plan

- Reproduce this exact structural sequence in PostgreSQL tests: initial detected request, early nonblocking consent, approval during generation, then a topic-free truncation complaint. Assert pre-consent exclusion and explicit handling of the later complaint; never silently grant completion.
- Add a control: after approval, submit a **fresh explicit SMA B203 topic request** and a completed answer. Require the exact session/record, one finalized turn, covered topic and visible retained history. This control has not been performed against live data because this investigation is read-only.
- Test an explicit post-approval begin/resume decision if that becomes the product contract; duplicate decisions and retries must not create duplicate user messages/events.
- Test bounded topic-free follow-ups against a unique current session versus multiple units/terms, stale revisions, revoked consent, another owner and unrelated chatter. Check request attribution and teaching eligibility agree.
- Keep ordinary exposure, incomplete response, formal assessment and independent mastery separate. A complaint about cutoff cannot create verified progress.
- Rerun the disposable PostgreSQL suite, including NULL completion locks, concurrent completion, revocation and original-run tests. The owner's prior 300 passing tests remain useful evidence, but did not cover this live interaction pattern.
- In the browser, record consent decision, new tracked request, selected session, route, durable assistant and finalized turn, then reload and query history from a new conversation. Verify the raw row exists before debugging retrieval.
- Verify the current web child and all worker processes loaded the intended release. Preserve logs with request correlation and safe metadata: selected academic record/session, attribution outcome/reason, teaching route, completion eligibility/result and provider finish reason. Do not log private chain-of-thought or full personal prompts.

## Evidence required to accept Phase B

Owner PostgreSQL success **and** a new browser trace demonstrating a valid post-consent academic request → correct authorized controller or explicitly classified ordinary exposure → successful durable outcome → finalized row → matching summary → matching new-conversation model context. Include a separate interrupted-output test that remains incomplete or is correctly recovered before completion is claimed. Resolve the initial-lesson consent contract visibly so an early optional card does not imply retrospective capture.

Until then, **Phase B live acceptance remains incomplete**. This report explains the inspected empty record and narrows the repair; it does not invent retrospective progress or claim that an untested fresh post-consent lesson failed. Only this diagnostic report was added to the application workspace; no source, migration, database row or consent state was modified.
