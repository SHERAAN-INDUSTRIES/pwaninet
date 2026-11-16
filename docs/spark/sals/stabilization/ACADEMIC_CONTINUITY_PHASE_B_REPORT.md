# SALS Academic Continuity — Phase B

Date: 2026-11-14. Application: PwaniNet/Vasto Spark. Status: **IMPLEMENTED — UNVERIFIED** pending owner PostgreSQL and real-browser acceptance. The owner reports that R2 passed its disposable PostgreSQL suite (275 tests, zero failures); that is owner-supplied evidence, not a rerun here. R1/R2 delivery implementations are preserved except for the necessary integration points listed below. No R3–R5 or WP4 work begun.

## Root causes repaired

Phase A was rechecked against current source before edits. Its seven gaps were present: late card presentation, session/conversation mismatch, separate request/teaching projections, absent generic completion capture, short-reply attribution gaps, literal false mastery flag and optional on-commit academic write failures.

The repair adds deterministic early consent delivery, exact owned session binding, atomic completion receipts and a teaching-aware retained-history projection. It uses existing enrollment, PendingAction, teaching state/turn, assessment, agent run and SSE services. There is no new model orchestration engine and no automatic historical chat reconstruction.

## Files changed

- `pwanimate/services/conversation.py`: academic request attribution is part of the user-message transaction; assistant persistence atomically finalizes teaching outcomes; replay of an already-finalized teaching turn returns its original message.
- `pwanimate/services/unit_learning.py`: current-chat session binding, continuity matching and disambiguation, consent-aware model recall and completed-progress projection.
- `pwanimate/services/academic_progress.py`: finalization and bounded progress/history projection over existing turns/evidence, plus attributed ordinary-answer exposure.
- `pwanimate/services/teaching.py`: precise session/run ownership, retained-objective continuation, completion metadata and interruption-safe cached responses.
- `pwanimate/models.py`, `pwanimate/teaching_models.py`, `pwanimate/migrations/0037_academic_completion.py`: nullable binding and completion provenance fields.
- `pwanimate/api/views.py`: early academic-consent SSE event, current-revision proposal filtering, deterministic history clarification, timings and completion provenance.
- `pwanimate/services/agent_execution.py`: completion metadata forwarded into durable assistant persistence; original run lifecycle remains authoritative.
- `pwanimate/services/followups.py`: narrow R1 integration accepting an exact owned academic-conversation binding instead of assuming the session home conversation must be the chat. Other R1 lifecycle behavior is unchanged.
- `static/js/pwanimate/pwanimate-chat.js`: early consent event handler, deduplication and final-envelope fallback using existing card components.
- `tests/sals_academic_continuity/test_continuity.py`, `tests/spark/academic-preflight.cjs`: new local/integration and owner-run race/migration coverage.
- `tests/sals_02/test_foundation.py`: updated atomic-capture and current-conversation session expectations. Request-message deletion preserves observations; linked conversation deletion remains protected.
- Existing SALS 0.2/WP1/WP2 persistence harnesses: restore current completion columns after deliberately rebuilding historical teaching tables; this is test-fixture compatibility, not a historical data migration.
- `scripts/test-sals-academic-continuity-postgres.sh`: owner-only disposable verification entry point.

The working tree already contained extensive SALS work. This inventory identifies Phase B edits rather than attributing the full workspace diff to this package.

## Consent timeline: before and after

Before: student message commits → optional academic callback creates pending action → model streams lesson → final assistant envelope serializes action → card appears with the answer. Failure in the callback was logged without durable request capture.

After: student message and deterministic attribution/proposal commit together → current-revision pending actions are read → `academic_consent` SSE event is queued → lesson generation/streaming begins → assistant and eligible outcome commit together → final envelope also carries persisted actions for restoration/JSON fallback. Reload uses the existing pending-action endpoint. The frontend deduplicates by persisted action ID and rejects events belonging to another active conversation.

**Chosen interaction policy:** early optional consent, not a second paused run. The student may choose Start Tracking or Not Now while ordinary tutoring proceeds. This avoids stealing a pending teaching question and adds no consent-generation model call. The prompt's triggering message is always excluded if it predates approval, even if approval lands during its generation. Start Tracking authorizes future work; it does not retrofit the lesson already in flight. Decline persists through the existing action API and tutoring continues untracked. JSON clients receive the same persisted card in the response; the main frontend renders it before the answer bubble when there was no streamed event.

Ask mode remains conversation-scoped; automatic mode still requires initial unit authorization and a current revision. Valid authorization in its scope generates no new proposal. New scopes or consent revision changes legitimately require another approval. Model text never substitutes for a persisted card.

## Session binding architecture

A normal approval creates/uses a SALS session in the current conversation. It does not silently create an unrelated default conversation. Each SALS scope includes record/semester and current conversation, so a new chat can have an independent controller without moving an old waiting run.

The existing schema has a one-to-one legacy `conversation.study_session` relation. If that slot is occupied by legacy Study Mode or another unit, Phase B preserves it and creates an independent SALS controller with an explicit `academic_conversation` FK to the current chat. This narrow binding makes that controller usable by the chat while retaining the legacy relation and state. No existing legacy session is converted to authorized SALS tracking, no session is moved, and no existing waiting run is resumed as a new conversation's run. A collision may therefore have a separate controller home conversation, but runtime resolution, source attribution and completion use the explicit binding rather than guessing from that home ID.

Session selection matches owner and direct/bound conversation, prefers active linked candidates, and disambiguates enrolled units. Durable resumption resolves the exact server-persisted teaching_state_id on the owning run, verifies its conversation binding and current authorization, and retains the original interaction identity. Invalid/paused/expired contexts do not become writable merely because a session row exists.

## Learning-event and completion semantics

Existing teaching turns remain the event/receipt store. Added fields are `completed_message` (nullable one-to-one assistant message) and `completed_at`. The existing stable state/receipt key remains the idempotency boundary. Assistant persistence locks the turn, validates the owner/binding and current tracking authorization, and commits the message and completion pointer together. A repeat finalized turn returns the same durable assistant message.

`prepare` can persist state and questions before generation; that is not a completion. `finish` annotates whether a nonempty answer finished without known length/interrupted/error/cancelled termination. Incomplete cached model content cannot be promoted to a completed answer on retry: only a cached response explicitly marked complete is reusable as complete. A finalized explanatory segment can coexist with an outstanding question; the segment is completed while the logical run remains waiting.

The progress projection distinguishes:

- Student request: existing SparkUnitActivity, not demonstrated learning.
- Completed explanation/demonstration: finalized turn with response provenance.
- Assigned practice: outgoing question/exercise and outstanding interaction.
- Attempt/feedback: answered interaction, evaluation, correctness and misconception signals.
- Assistance: hint level and assistance classification, separate from independent answers.
- Formal assessment: existing assessment/evidence provenance validated by WP1.
- Mastery: existing WP1 policy state, never inferred from explanation, exposure or self-report.

For successful ordinary/RAG answers outside TeachingService, an already-authorized, unambiguous source request activity may produce a minimal turn classified only as `topic_exposure`. It does not parse correctness, fabricate completed teaching steps or add mastery evidence. Ambiguous multiple-unit spans do not produce a guessed ordinary completion. A pre-consent source has no authorized request activity and cannot use this path.

Completion is gated by durable response persistence, not first token or start of generation. If final persistence fails, both assistant and finalization roll back and the prepared turn remains discoverable for existing retry/recovery. Known partial output remains unfinalized. Original run claims, task leases and interaction receipts remain in charge of retries/resumption; this package does not change R3 response-recovery semantics.

## History projection and continuity

`UnitLearningService.summary` now includes bounded finalized `teaching_progress`, `covered_topics`, last-covered context, outstanding checkpoints, assistance/evaluation and bounded feedback excerpts. `requested_topics` and topic semantics identify legacy input observations separately. Existing personal approved observations and legacy roadmap are retained as separate data, not treated as a proxy for progress.

`mastery_assessed` is evidence-backed: at least one currently usable, currently verified WP1 evidence row. The existing mastery service supplies concept-state summary. One correct assessment can make assessed true while MASTERED remains false; formative independent attempts alone cannot certify mastery.

Named units/codes select retained owner history; explicit full semester descriptions can disambiguate matching terms. Generic previous-lesson/stop/mistakes prompts select only a unique eligible authorized unit; multiple units or terms return a structured clarification context and the main chat responds deterministically without an extra model call. History and request classification are bounded. Current projection reads at most 30 finalized turns and 100 recent evidence candidates; context selection inspects at most 100 records. Existing prompt assembly limits serialized history to 18,000 characters.

A fresh authorized conversation can initialize a new controller from a prior finalized objective/mode/phase. It advances a new teaching turn, allocating new questions/exercises where needed; it never copies or resumes old run/interaction/question IDs. Actual stored history reaches the existing ContextPackage's `format_context_text` academic-unit block and StudyAgent history context. Model recall is suppressed when tracking is disabled. Explicit owner record inspection remains available for retained history; reading retained rows is distinct from permission to write new activity. Historical named records may be read while their recording revision is obsolete, but cannot authorize new writes.

## Security, integrity and existing data

Enrollment/semester eligibility, ownership, preference revision, ask-scope and authorization timestamp are revalidated at capture. The final source message or answered interaction must postdate authorization. Binding cannot point at another student's conversation. Completed pointers cannot be reused across owners/conversations. No personal-memory outbox or extraction receipt becomes academic evidence.

Migration 0037 is additive and nullable: session academic_conversation plus turn completed_message/completed_at. Existing rows are not marked complete and no arbitrary historical conversations are imported. Old unfinalized teaching turns remain outside the new completed-progress view unless an explicit current runtime retry genuinely completes them with valid provenance. Existing request observations remain observations. The migration depends on completed R2 migration 0036 and does not rewrite it.

Deploy 0037 before the new code queries its fields. Keep the additive columns on code rollback; do not discard new completion provenance casually. No owner database migration was performed here. Retained-history deletion and existing account/session protections remain in force.

## Local verification and overhead

Permitted checks use isolated SQLite settings, local mocks and Node fixtures. Live providers, PostgreSQL and browser execution were not run. The new tests cover early consent before token callback, current authorization without duplicate consent, ask/automatic binding, legacy separation, completed versus partial outcomes, interruption retry, formative versus formal evidence, owner isolation, pre-consent exclusion, prompt injection, ambiguous continuity, new-run restoration, capture rollback and actual additive migration. A PostgreSQL duplicate-finalization test is supplied and skipped locally; existing owner-run suites cover approval/revocation and run races.

Timing marks include request_received, academic_preflight_start/done, academic_consent_published and academic_history_start/done. No extra LLM classification/consent call was introduced. Deterministic academic persistence is now synchronous DB work so that a message cannot commit while its eligible capture is silently lost. Local timings include ORM work and message/preflight persistence; they do not measure provider time or establish production speedup. Final executed counts and sample measurements are appended below.

## Owner PostgreSQL command

```bash
cd /home/zayne/projects/pwaninet
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-academic-continuity-postgres.sh
```

The existing runner creates a fresh disposable PostgreSQL container and isolated credentials and does not accept an owner database URL. The script includes the new continuity suite plus SALS 0.2, mastery, teaching/R1 and memory/R2 compatibility suites. No Docker or PostgreSQL test command was executed here.

## Real-browser acceptance

1. With no authorization, ask an enrolled-unit question. Confirm Start Tracking / Not Now appears before first visible lesson content; answer may continue untracked.
2. Approve; confirm the initial pre-approval question did not become recorded learning. Send a new academic turn; observe completed progress, pending practice, assistance and feedback.
3. Decline in a fresh scope; tutoring still works and no authorized progress is written. Reload: pending cards restore, declined cards do not execute or reappear in the same scope.
4. With valid authorization, ensure no repeated prompt. In ask mode test a new scope; in automatic mode test continued valid unit tracking. Revocation/revision/term changes must block new capture.
5. Test a chat containing legacy @study and a waiting question. Approval must not convert/steal that controller or interaction. Explicit SALS binding must resolve correctly on subsequent academic work.
6. Exercise typed/choice answers, hints, wrong answers, assisted and independent attempts. Explanation/formative success must not mark mastery; actual formal evidence changes assessed state accurately.
7. In another chat ask the four requested history/continuation prompts. Confirm correct unit/semester, real stored mistakes/progress, new run identity and no old-run resurrection. Ambiguous units/terms request clarification.
8. Interrupt generation, reload/reconnect and retry. Partial turns remain incomplete; finalized segments persist once; outstanding questions reflect current state.
9. Check existing light/dark themes and mobile safe areas. Existing consent components are reused; no redesign introduced.

## Remaining limits and updated SALS checklist

- Early consent, binding and completion projection: VERIFIED only within executed local tests; real-browser and PostgreSQL behavior IMPLEMENTED — UNVERIFIED.
- Existing legacy waiting runs remain their original untracked controllers; explicit SALS work uses its own bound controller. No retrospective conversion or arbitrary chat backfill.
- Optional nonblocking consent intentionally leaves the first pre-approval lesson untracked. This is a privacy boundary, not missing capture after authorization.
- Generic tutoring outside the controller is recorded only as attributable completed exposure; semantic grading/step discovery is NOT IMPLEMENTED by this repair.
- History is bounded and may omit older events; omitted entries must not be inferred. No provider benchmark or production latency result is claimed.
- Interrupted logical-run terminal semantics remain R3 territory; this package prevents known incomplete responses from being presented as completed academic progress.
- SALS 0.1/R2: owner-reported PostgreSQL pass retained; locally regression-checked integration. R1 owner browser gates remain separate.
- SALS 0.2 consent/session/capture/history and 0.3 WP1/WP2 projection integration: implemented, awaiting owner acceptance.
- R3, R4, R5 and broader WP4 redesign: NOT IMPLEMENTED by this package; no work begun.

Phase B must not be marked fully VERIFIED until owner PostgreSQL and real-browser acceptance complete.

## Final executed verification evidence

- Broad existing local script: **357 tests reported, OK with 22 skipped**, 145.036 seconds (335 non-skipped). Existing teaching, lifecycle/resume, Markdown/math, question controls, composer and quiz Node contracts also passed. Initial development runs exposed historical-schema fixture incompatibilities and session-selection errors; those were corrected before this passing run.
- New continuity suite: **21 tests reported, OK with one PostgreSQL skip**, 15.257 seconds (20 non-skipped), including real formative attempts, actual formal assessment evidence, prompt rendering, new-run continuation and actual migration operations.
- Final continuity plus R2 dispatch integration run after the conversation-lock ordering adjustment: **62 reported, OK with three PostgreSQL skips**, 25.896 seconds (59 non-skipped). Counts overlap the preceding suites and inherited cases; do not add them as unique coverage.
- Final early-consent, ambiguity and fresh-chat unknown-history routing checks: **three tests, OK**, 1.950 seconds. The unknown-history case confirms a deterministic unit/semester clarification with zero orchestration calls. Initial attempts used an incomplete test-module label and failed discovery; the corrected fully qualified labels executed successfully.
- New academic-preflight Node handler, existing R1 follow-up handler, JavaScript syntax, owner-script Bash syntax and whitespace checks: PASS. These are mocked DOM/handler checks, not real-browser acceptance.
- Real migration-graph drift check on isolated SQLite settings: **No changes detected**. Actual additive 0037 operations were applied only inside the disposable local test database. No owner migrations were run.
- Local samples varied with concurrent local test load. The 21-case continuity run measured source-message plus deterministic preflight at **58.042 ms** and bounded history retrieval at **56.823 ms**. The final 62-case integration run measured **100.324 ms** and **134.820 ms**, respectively. These are single local test samples, not distributions or before/after production measurements. No provider latency was included or measured; actual production responsiveness remains an owner acceptance item.
- Isolated settings emitted existing Axes configuration warnings; mock billing/provider and expected rejected API cases logged messages without failing the passing suites.

The owner-run PostgreSQL script and browser procedure above remain mandatory release gates. No PostgreSQL or Docker tests were executed and no later stabilization package was begun.

## PostgreSQL compatibility hotfix — nullable outer-join locks

Status: **IMPLEMENTED — UNVERIFIED on PostgreSQL**. The owner reported 297 tests executed with 12 errors and one failure, including `NotSupportedError: FOR UPDATE cannot be applied to the nullable side of an outer join`. Those owner-run failures supersede any interpretation of prior SQLite passes as PostgreSQL verification. No PostgreSQL server or Docker command was used for this hotfix.

### Root cause and queries repaired

Two Phase B queries used unrestricted `select_for_update()` alongside nullable joined relations:

1. `ConversationService.persist_assistant_message` in `pwanimate/services/conversation.py`: teaching-turn lock plus `select_related('completed_message')`. This nullable one-to-one FK generates a LEFT OUTER JOIN, including the normal initial state where completed_message is NULL.
2. `academic_progress.finalize` in `pwanimate/services/academic_progress.py`: teaching-turn lock plus `select_related('state__session__learning_record')`. The session's learning_record is nullable, so that path also generates an outer join.

PostgreSQL applies unrestricted FOR UPDATE to joined tables and rejects the nullable side. The failure is a query-shape restriction, not limited to rows whose current related value happens to be NULL. SQLite omits locking clauses and therefore could not expose it through ordinary execution tests.

Both queries now use `select_for_update(of=('self',))`, retaining related-object loading while explicitly locking only `SparkTeachingTurn`. The other reviewed Phase B locks were either single-table queries (conversation, preferences, runs and teaching-state configuration) or already self-scoped: teaching-state preparation and enrollment queries. The read-only summary/context joins have no FOR UPDATE. No additional affected unrestricted nullable-join lock was found in the inspected Phase B paths.

### Preserved semantics

Application changes are restricted to these two lock-target arguments. Transactions, conversation-first write ordering, teaching-turn serialization, duplicate-result replay, source/owner/conversation validation, consent revision/timestamp checks and one-to-one completed-message constraint are unchanged. The existing consent/enrollment/record locks in `ConceptMasteryService.lock_record` remain authoritative; joined descriptive rows are not implicitly locked by the turn query. No locking was removed from the authoritative teaching turn, and no provider work or run recovery behavior was moved into a new transaction. No migration is needed.

### Regression coverage and local execution

New file: `tests/sals_academic_continuity/test_postgres_locking.py`.

- An isolated local test captures the **actual querysets used by both services**, starting with completed_message NULL and repeating assistant persistence. It compiles them with Django's PostgreSQL backend and asserts `LEFT OUTER JOIN` remains present while the lock clause targets **only** `pwanimate_sparkteachingturn`. It rejects any PostgreSQL connection attempt. This is offline SQL-compilation coverage, not PostgreSQL execution.
- PostgreSQL-only threaded tests exercise two concurrent assistant persistence calls returning one message ID/one completed message, and revocation winning before finalization's consent-checked write. They are included automatically by the existing owner script and skipped locally.
- Existing continuity cases exercise ordinary-answer exposure, duplicate finalization, interrupted retry, owner isolation, formal/formative evidence and original-run continuation. Existing R1 and R2 suites provide compatibility coverage.

Executed locally:

```bash
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
  .venv/bin/python manage.py test tests.sals_academic_continuity \
  tests.sals_03_wp2.test_followup_repair tests.spark_memory.test_dispatch --noinput
```

Result: **95 tests reported, OK with six skipped**, 29.871 seconds (89 non-skipped). Skips include the new PostgreSQL races and existing PostgreSQL-specific tests. The initial continuity/offline-compilation run also passed (23 reported, one skipped); totals overlap and must not be added as unique cases. Academic-preflight and R1 follow-up Node handler checks, chat JavaScript syntax, owner-script Bash syntax and whitespace checks passed. Existing Axes settings warnings and mocked failure/retry logs remain; no production failure injection was performed.

### Required owner rerun and remaining risks

From the project root, run exactly:

```bash
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-academic-continuity-postgres.sh
```

PostgreSQL runtime lock behavior, concurrent finalization and revocation, and the full owner suite remain **IMPLEMENTED — UNVERIFIED** until this rerun succeeds. The separately reported single failure cannot be independently attributed from the summary alone; it may be secondary to the lock errors, but that is not assumed resolved without the actual owner result. Real-browser acceptance also remains outstanding. No new work package or unrelated repair was begun.
