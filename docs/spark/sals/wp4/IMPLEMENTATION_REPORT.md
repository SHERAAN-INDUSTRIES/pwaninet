# WP4 — Unified Study Mode Implementation

Date: 2026-11-14. Target: Spark in PwaniNet/Vasto. **IMPLEMENTED — UNVERIFIED for PostgreSQL and real-browser acceptance.** This report supersedes the earlier audit-only checkpoint. The owner subsequently authorized coordinated, non-destructive integration. No R3, R4, R5, new agent engine, replacement session model or indexing pipeline was implemented.

## Canonical session and handoff

`ResolvedStudyContext` in `services/study_context.py` is the shared compatibility adapter. It validates owner/current conversation and resolves one canonical session or explicit ambiguity. It includes generation, lifecycle, academic scope/record, tracking posture, pending task/question/checkpoint, document diagnostics and retained progress. Read-only resolution never resumes or replaces a paused session.

The existing PwanimateStudySession remains authoritative. Additive migration **0038_canonical_study_binding** adds a nullable canonical-session FK and generation counter to PwanimateConversation, preserving its existing one-to-one home-session relationship. No existing row is selected or converted by migration.

`select` locks the conversation, compares the supplied generation, locks candidate sessions in stable order and rejects foreign/unbound selections. Departing active candidates are paused, not deleted or merged. Executing controllers and unscoped pending tasks prevent unsafe handoff. Owned waiting teaching questions remain attached to their original session/run. Selecting a paused target does not resume it; selecting a completed target does not reopen it.

The same adapter now supplies Study Mode readers, teaching selection, API serialization, sidebar template data and restored frontend session state. The new owner-only study-context endpoint returns canonical metadata or explicit handoff choices. The existing interaction surface renders choices and sends generation-aware selection; stale/network failures retain a refresh/retry path.

## Exact files changed

### Models and adapter

- `pwanimate/models.py`: conversation canonical pointer/generation.
- `pwanimate/migrations/0038_canonical_study_binding.py`: additive schema only.
- `pwanimate/services/study_context.py`: adapter, explicit selection, lifecycle/run fences and tracking-renewal request.
- `pwanimate/api/study_context.py`, `pwanimate/api/urls.py`: authenticated resolution, handoff and renewal endpoint.

### Lifecycle, authorization, runtime and capture

- `pwanimate/services/study_session.py`: canonical readers/start guards, explicit resume/end behavior, coherent serialization and template decoration.
- `pwanimate/services/unit_learning.py`: prospective attachment on explicit approval, paused-session reuse without activation, shared scoped/document/contextual attribution, atomic request/approval handling and correct bound-session history links.
- `pwanimate/services/teaching.py`: canonical selection, lifecycle fencing, document-enabled teaching, post-decision source receipts, bounded contextual continuation, completion/source gating and cached provenance preservation.
- `pwanimate/services/academic_progress.py`: selected/active-session fence and actual source/citation projection; no mastery-policy change.
- `pwanimate/services/agent_runs.py`, `pwanimate/services/agent_execution.py`: original-run consent pause/decision, one post-decision execution boundary, snapshot preservation, generation/lifecycle checks and public decision events.
- `pwanimate/services/actions.py`: conversation-first academic approval locking, transactional academic approval and durable run IDs on restored tracking cards.
- `pwanimate/services/followups.py`: narrow R1 integration for canonical selection and consistent conversation-first claim ordering; its existing offer lifecycle/result semantics remain.
- `pwanimate/services/conversation.py`: internal decision/execution receipts excluded from dialogue history; existing completion and R2 outbox transactions preserved.

### API, views and existing UI

- `pwanimate/api/views.py`: canonical chat validation, pending-task protection, unit/document clarification, consent-gated run creation and lifecycle errors.
- `pwanimate/api/serializers.py`: canonical study-session serialization and hidden internal receipts.
- `pwanimate/views.py`, `pwanimate/templates/pwanimate/partials/conversation_list.html`: resolved sidebar/session compatibility data instead of treating the home relation as selection authority.
- `static/js/pwanimate/pwanimate-chat.js`: reused handoff/consent components, original-run decisions, canonical restoration, tracking status and retry behavior. Existing modal/mobile session API controls, composer, viewer and theme/safe-area CSS remain in use; no new navigation or CSS redesign.

### Verification and documentation

- `tests/sals_wp4/test_unified.py`: new canonical/lifecycle/consent/document/migration/race tests, including parked-question handoff without blocking the selected controller.
- `tests/sals_wp4/ui_settings.py`: isolated SQLite settings using real project URL namespaces, templates and release context for chat-view coverage.
- `tests/spark/study-context.cjs`: actual frontend helper with mocked DOM/fetch.
- `tests/sals_academic_continuity/test_continuity.py`: expectations updated for held first consent and explicitly authorized attachment to the current session.
- `tests/sals_02/test_foundation.py`: multi-unit isolation now uses separate canonical study conversations rather than approving competing controllers in one chat.
- `pwanimate/tests/test_document_context.py`: lifecycle diagnostics use an authorized owner; a nonowner fail-closed check remains explicit. PostgreSQL vector-search assertion split into a PostgreSQL-only test while exact-page text checks continue locally. Document access policy and retrieval implementation were not relaxed.
- `scripts/test-sals-wp4-postgres.sh`: owner-run disposable verification.
- `docs/spark/sals/wp4/UNIFIED_STUDY_MODE_ARCHITECTURE.md`: approved implementation recorded in section 10, retaining the original audit as history.

Pre-existing uncommitted SALS changes were retained. This list identifies WP4 integration edits, not the whole workspace diff.

## Tracked and untracked Study Mode

Starting @study still creates/uses an untracked StudySession; it does not grant academic consent. Ordinary saved dialogue, legacy session state and document context continue to exist without creating enrollment-linked evidence or verified mastery.

A request that needs tracking approval now pauses an **existing infrastructure approval run before lesson generation**. The persisted academic card offers Start Tracking / Not Now and carries its owning run ID, including after reload. Decision processing uses the existing approval/resume machinery. No additional LLM call is used to classify the unit or request consent.

On approval, the compatible current untracked session can acquire the exact owner/enrollment/unit/semester record prospectively. This happens only as part of the explicit decision. Earlier turns/history are not backfilled. If the current session belongs to a different unit/record, the user must choose the appropriate session or a separate study conversation; it is not silently reassigned.

A single internal `academic_execution` message is created after the decision, stored once as execution_boundary_id in the durable snapshot. The original requested run and question ownership are preserved. Turn receipt provenance uses the post-decision input while subsequent pauses/resumes retain the original run identity. Internal execution/authorization receipts are excluded from visible dialogue/history, avoiding duplicated user questions on retry.

Decline continues ordinary tutoring without enrollment-linked activity/evidence. Ask/automatic/disabled policies and revisions remain. An old retained session that needs renewed ask-scope/revision authorization can request a fresh PendingAction through the existing session surface without consuming its outstanding practice question. Explicit renewal preserves paused state and question identity.

## Lifecycle and recovery semantics

- A canonical paused session remains paused during reads, attribution and history lookup. The academic resolver returns it rather than creating another active row.
- Start against a paused canonical session requires explicit Resume. A completed session cannot silently reopen.
- Pause/resume/end and handoff use the same selected identity; a stale client cannot act on an unselected competing session as though it were canonical.
- Conversation-first coordination protects selection, academic approval, input claims and lifecycle writes. Nullable joined locking queries retain PostgreSQL self-row scopes.
- Pending answers cannot be claimed for paused/unselected sessions. Handoff does not copy interaction IDs or evidence to its target.
- Background completion rechecks selected identity and active status before academic finalization. A response already generating can persist as dialogue, but cannot finalize progress for the paused/departed controller.
- Existing R1 replay/claim results and R2 outbox delivery remain separate authorities. No outbox row is treated as academic evidence.

Known incomplete provider output is not finalized. An incomplete cached response cannot be upgraded into completion merely by retry. Completed document retries retain their recorded sources/citations; a document response lacking durable provenance is not blindly reused as sourced completion. This package does not implement broader R3 automatic stream recovery or change provider mission-completion semantics.

## Document-grounded teaching and evidence

DocumentTurnContext, DocumentAccessPolicy, actual document academic-unit associations, eligible enrollment/semester checks and the existing retrieval components are reused. A page-oriented request can be attributed through its authorized canonical unit/document association even when it does not repeat the course name. Shared/ambiguous unit associations require clarification unless the authorized selected scope disambiguates them. Explicit unit/document mismatch fails closed.

The existing controller receives the original document resources; retrieved source/page metadata is persisted with the assistant and projected in teaching progress. Explanatory document turns without reported sources are not finalized as sourced textbook learning. Ordinary open/retrieval events grant no learning evidence. Generated controller practice remains distinct from source-based explanation, and WP1 alone determines verified assessment/mastery.

Request observation, completed exposure, assigned exercise, attempted answer, assistance, feedback and formal evidence remain separate. A typed contextual follow-up must match a bounded tutoring command and a selected authorized session with existing finalized provenance. Arbitrary chatter and a cutoff complaint are not silently turned into an academic completion.

Read-only new-chat history returns authentic finalized progress, statuses, feedback and outstanding references. Explicit “continue here” establishes a new authorized execution boundary/controller where required; it does not transfer another conversation's run identity. Returning to an existing session uses its bound experience URL and exact selected state. Multiple unit/term/session candidates require clarification/handoff.

## Local verification

All database execution below used isolated local settings/test databases; no owner PostgreSQL or Docker suite was run.

- Broad existing SALS local script: **357 tests reported, OK with 22 skipped**, 221.020 seconds (335 non-skipped), plus existing teaching/lifecycle/math/question/composer/quiz Node contracts. This covers compatibility through the coordinated session changes; subsequent focused runs cover final grounding refinements.
- Final combined WP4 + Phase B + R1 followup + R2 dispatch + document-context run: **146 tests reported, OK with eight skipped**, 43.449 seconds (138 non-skipped). It includes actual additive 0038 migration operations on isolated SQLite, owner isolation, original-run consent/decline, assignment/answer preservation, pause/revocation fences, handoff generation and document source/page projection.
- The new PostgreSQL-only handoff race and existing PostgreSQL locking/revocation/vector tests were skipped locally. The local offline PostgreSQL SQL compiler regression remains included; compilation is not a server concurrency test.
- Frontend actual-helper tests: canonical choices, generation submission, stale/network retry, question restoration and authoritative paused UI passed. Existing academic-consent and R1 followup Node checks, JavaScript/Bash syntax and whitespace checks passed. These are DOM/fetch mocks, not browser rendering acceptance.
- Migration drift check using isolated settings and the real graph: **No changes detected**. No live migration was applied.

- Final handoff-specific run: **14 tests reported, OK with one skipped**, 18.336 seconds; the PostgreSQL race remains skipped. Selection detaches only the local old composer question, preserves it in the database and excludes that parked controller from the selected session's pending-task guard.
- Existing real-template chat-view suite: **seven tests, OK**, 5.826 seconds using `tests.sals_wp4.ui_settings`.
- Additional legacy Study Mode/export coverage is **PARTIAL**: an initial combined run using the narrow SALS harness had missing namespaces/templates. With real URLs/templates the combined 30-test run still had four missing release-context errors and one PDF/export failure due to absent `vendor/dompurify/purify.min.js`. The release-context harness was corrected and all seven chat-view tests passed on rerun; the export asset failure was not repaired in WP4. A separate old StudyAssistant expectation still requests raw transcript digest regeneration, deliberately disabled by the earlier memory-consent policy; no transcript-memory behavior was restored. These failed exploratory runs are not counted as passed coverage.

Counts overlap and must not be added as unique coverage. Initial development runs exposed expected old-contract tests (nonblocking consent, legacy separate controller, paused answer acceptance and multi-unit controller scope) plus stale document-test diagnostics/vector backend assumptions. The relevant expectations and fixtures were corrected explicitly; no application ACL or vector pipeline was weakened to make tests pass.

The local combined run measured one source/preflight sample at **105.909 ms** and bounded history retrieval at **88.901 ms**. These are local ORM samples under concurrent test load, not production latency distributions or provider timing. Consent/handoff adds no classifier LLM call. Real provider responsiveness and document-answer quality remain browser/provider acceptance items; R4 optimization was not attempted.

## Owner PostgreSQL verification

From the project root:

```bash
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-wp4-postgres.sh
```

The existing isolation runner provisions a new disposable PostgreSQL container/database with isolated credentials; it does not accept an owner database URL. The suite includes WP4, Phase B, academic foundation, mastery, teaching/R1, memory/R2 and document-context/vector coverage. The new threaded handoff test requires one generation winner and one stale rejection; existing completion/concurrency tests remain required.

After owner verification, apply additive migration 0038 through the owner's normal deployment process **before** starting code that queries its columns. Restart web and Celery workers together to load the same release. An autoreloading development server using new code against an unmigrated database will fail closed; no fallback to competing legacy writers is provided. No migration, restart or real student-data alteration was performed during this implementation.

## Real-browser acceptance sequence

1. Open an indexed enrolled-unit PDF and activate @study. Confirm the displayed session ID/status and canonical endpoint agree, and tracking is not implied by activation.
2. Request a relevant lesson. Before any lesson token, verify the persisted Start Tracking / Not Now choice and waiting public activity. Reload and verify the card still targets the same run.
3. Approve. Confirm one post-decision execution boundary, the original requested run identity, a genuinely completed sourced explanation and durable teaching progress. Repeat/reconnect without duplicated questions or evidence.
4. Assign practice, submit an answer and inspect the exact question/run ID, evaluation and assistance. Explanation/formative success alone must not create MASTERED state.
5. Pause from desktop and mobile controls with a pending exercise. Confirm session API, canonical context and controller are paused; the question and completed history remain.
6. In another chat request history/status. Verify no automatic reactivation. Return to the existing session and explicitly resume its pending question; or explicitly continue in a new scope with a new execution boundary. Renew ask-scope authorization if necessary without consuming the old question.
7. Exercise an existing legacy/SALS collision. Both candidates must be offered; selecting one pauses the departing controller, preserves its question and increments generation. A stale second-tab selection must fail and refresh.
8. Switch two enrolled-unit sessions, revoke consent, try a foreign/unrelated document and interrupt generation. Verify no unit contamination, new unauthorized evidence, source invention or false completion.
9. Check mobile safe areas, light/dark themes, viewer page restoration, unified composer and Spark Thinking. No screenshot-based/browser acceptance was performed locally.

## Remaining limits and checklist

- Canonical adapter, explicit selection, lifecycle fences, first tracked boundary and scoped completion: implemented; locally tested within the scopes above.
- PostgreSQL migration/locking/race behavior, live Celery reload and real-browser acceptance: **IMPLEMENTED — UNVERIFIED**.
- Actual live textbook retrieval/generation quality and page citation correctness: **IMPLEMENTED — UNVERIFIED**. Local indexed-document fixtures validate resolution/controller/provenance; generation is mocked in that integration test. PostgreSQL semantic retrieval remains owner-run.
- Arbitrary freeform contextual attribution, unrecognized complaint recovery, automatic R3 continuation and R4 speed improvements: not implemented by this package. Recognized contextual commands are bounded and provenance-checked.
- Unscoped or executing task conflicts require settling/cancelling the existing task before handoff. Waiting owned teaching questions are preserved rather than silently reassigned.
- Legacy percentages remain legacy UI data; they are not substituted for WP1 mastery. Historical rows are not merged, automatically selected or backfilled.
- Scope intentionally permits separate explicit study experiences across conversations/units; within a conversation only the canonical selected controller can execute. Ask scope can require explicit renewal on return.
- R1, R2, Phase B completion provenance, WP1 policy and embeddings are preserved with narrow, regression-tested integration changes.
- R3, R4, R5 and unrelated features: not started.

**Delivery boundary:** coordinated WP4 implementation and permitted local checks complete. Do not mark WP4 fully verified until the owner-run PostgreSQL suite and the real-browser sequence succeed.

## PostgreSQL deadlock hotfix — 2026-11-14

### Owner evidence and root cause

The owner reported **351 tests, one error** in `PostgreSQLTeachingConcurrencyTests.test_hint_and_external_grading_serialize_assistance_at_submission`. Migration conflicts were absent and isolation/locking probes passed. This is owner-provided evidence, not a PostgreSQL run performed during this hotfix.

Source inspection identifies an inverted parent/child order. WP4 `TeachingService.prepare` acquired the canonical conversation and session `FOR UPDATE`, then `_authorize` requested the academic tracking preference, enrollment and record. External `AssessmentService.submit_answer` acquired preference/enrollment/record, then assessment, and `ConceptMasteryService.capture_locked` inserted evidence referencing the session. PostgreSQL's FK key-share check (including deferred checks at commit) conflicts with an existing session `FOR UPDATE` lock:

```text
Hint preparation: holds session -> waits for preference held by grading
External grading: holds preference -> evidence FK waits for session held by hint
```

The preference named in the error is one edge of this cycle, not evidence that preference locking should be removed. The session-before-authorization sequence is present in WP4's added canonical lifecycle fence; WP1 external grading already follows parent-first authorization. This is a WP4-introduced/exposed inversion against that existing grading path. The current uncommitted history does not establish an independent pre-WP4 PostgreSQL failure; none is claimed. The full server deadlock graph was not supplied, so the cycle above is the source-supported explanation of the reported failure, pending owner rerun.

Completion had related child-before-parent sequences: assistant persistence locked the teaching turn before authorization; `academic_progress.finalize` locked turn/session before authorization; ordinary exposure created state/turn receipts before authorization. These were aligned as part of the same focused repair.

### Before/after locking contract

Before:

- Prepare: conversation → session → preference → enrollment → record → state → run/question/turn.
- External grading: preference → enrollment → record → assessment → evidence, with implicit source-session FK locks.
- Assistant completion: conversation → turn → session → preference → enrollment → record.

After:

- Shared partial order: **conversation, when required → preference → enrollment → academic record → session, when required → teaching state/turn or assessment → evidence**.
- Prepare resolves canonical identity under the conversation lock, obtains current authorization before the session lock, then reloads/locks the session and revalidates active status and stable record identity before touching state. A changed binding fails explicitly.
- External grading retains its existing preference/enrollment/record → assessment/evidence order. It never requests the conversation after taking these locks. Its actual grading remains deterministic, with no provider calls under locks.
- Assistant persistence authorizes before session/turn locks and retains the existing conversation, self-only turn lock, duplicate completed-message return and one-to-one binding. Revoked authorization still permits an untracked dialogue response; finalization independently rejects the academic write.
- Direct completion reads immutable identity without a child lock, authorizes, then locks session and turn and rechecks active state, binding and duplicate completion. Nullable related-message loading retains `of=('self',)`.
- Ordinary exposure obtains conversation and authorization locks before session/state/turn creation; a paused session produces no new exposure receipt.
- Configure already authorizes before state locks. Lifecycle-only pause/resume/handoff retain conversation → session (stable candidate ordering) and do not subsequently request authorization locks. Read-only canonical resolution adds no locks or lifecycle changes. Claim/decision transactions do not encompass provider generation.

No lock was removed, no validation bypassed, no retry loop added, and no schema/migration changes were needed. Provider execution remains outside these short database transactions. Consent revision, enrollment ownership, assistance timestamps and immutable WP1 first-attempt evidence semantics are preserved.

### Exact hotfix files

- `pwanimate/services/teaching.py`: authorization before session acquisition; locked binding/status revalidation.
- `pwanimate/services/concept_mastery.py`: shared lock-order contract documented at `lock_record`; evidence semantics unchanged.
- `pwanimate/services/conversation.py`: completion parent locks before session/turn locks; owner and completed-message replay protection retained.
- `pwanimate/services/academic_progress.py`: parent-first direct completion and ordinary exposure; locked lifecycle and duplicate checks retained.
- `tests/sals_03_wp2/test_concurrency.py`: event-coordinated hint-versus-grading and preparation-versus-answer-submission cases, independent PostgreSQL connections, bounded lock waits and immutable replay assertions.
- `tests/sals_03_wp2/test_teaching.py`: offline assertion that actual preparation requests authorization locks before session/state locks.
- `tests/sals_academic_continuity/test_postgres_locking.py`: actual completion lock-order assertions in addition to nullable-join PostgreSQL SQL compilation and original completion/revocation races.
- `scripts/test-sals-wp4-concurrency-postgres.sh`: focused owner-run disposable suite using the existing isolation runner.
- This report: root cause, verification and remaining limits.

### Regression and verification scope

The new PostgreSQL race deliberately lets grading acquire authorization first, then signals preparation to request that authorization. On the old order preparation already holds the session, exposing the FK cycle. Events and separate database connections coordinate the test; no scheduling sleep is used. Both hint delivery and an actual student-answer continuation compete with external grading. Each must retain one evidence row and the first independent attempt on replay, even when assistance arrives later.

The focused owner suite also includes existing duplicate answer claims, initial preparation retries, concurrent mode changes, completion idempotency, revocation-before-completion, canonical handoff races and pause/authorization fences. Pause and generation-completion fencing are locally exercised without a live provider; actual provider/worker interruption remains operational acceptance. R1/R2 regressions are included in the local compatibility run and complete owner suite.

Owner commands, from the project root:

```bash
# First: focused concurrency and completion verification (disposable PostgreSQL).
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-wp4-concurrency-postgres.sh

# Then: complete WP4 compatibility suite (disposable PostgreSQL).
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-wp4-postgres.sh
```

Both commands use the existing isolated runner; neither was executed here. No owner database, Docker suite, migration or live provider was used. The new deterministic PostgreSQL tests and actual server deadlock elimination remain **IMPLEMENTED — UNVERIFIED** until owner execution. Local row-lock order assertions are useful coverage but cannot establish PostgreSQL concurrency correctness. This focused partial-order audit does not certify every legacy lifecycle/report writer against all possible operations; new writers must follow the documented order, and any different owner deadlock graph should be retained for diagnosis. No broad automatic deadlock retries mask an unresolved inversion.

### Hotfix local results

- **VERIFIED (isolated local scope):** teaching + academic continuity + WP4 + R1 followup repair + R2 dispatch compatibility run: **155 tests reported, OK, 12 skipped**, 82.968 seconds (143 non-skipped). PostgreSQL connection/race tests were explicitly skipped, not counted as passed races.
- **VERIFIED (isolated local scope):** after adding locked-record identity guards, academic continuity + WP4 rerun: **39 tests reported, OK, four skipped**, 28.184 seconds.
- **VERIFIED (isolated local scope):** final teaching/concurrency collection after removing redundant authorization re-entry: **46 tests reported, OK, five skipped**, 34.605 seconds. Consent parent locks stay held while locked session status/identity are checked; no second authorization query is needed.
- **VERIFIED (offline/static scope):** Python compilation for modified services/tests, Bash syntax for both owner commands, whitespace checks, and Node canonical session, R1 followup and early academic-consent contracts passed. Frontend source was not changed by this hotfix.
- One initial 83-test development run failed the offline SQL-compilation expectation after the completion-lock query was changed to omit its nullable related-message join. The final query retains explicit `select_related('completed_message')` with self-only locking, and the existing PostgreSQL nullable-side regression now passes. That initial failed run is not passed evidence.
- **IMPLEMENTED — UNVERIFIED:** event-coordinated actual PostgreSQL races, server lock compatibility and production elimination of the reported deadlock. Both owner commands above remain required. No database schema or migration change was introduced.
