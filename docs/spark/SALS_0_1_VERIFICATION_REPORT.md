# SALS 0.1 — implementation and verification report

Date: 2026-11-12. Scope: Spark in existing PwaniNet/Vasto only.

**Acceptance decision: NOT COMPLETE.** The verified defects were corrected, but PostgreSQL concurrency/full migration-chain verification and live broker/worker validation remain blocked or unexecuted. SALS 0.2 was not started. No development or production database was migrated.

## A. Existing functionality

The checkout already contained extensive modified and untracked work, including the Phase 1 memory implementation. Those changes were preserved; they are not all changes made during this verification pass.

| Area | Existing implementation and audit finding |
|---|---|
| Identity and transcripts | Authenticated user owns conversations and messages; facts have their own owner. Facts survive source-conversation deletion through nullable provenance links. |
| Durable execution | `SparkAgentRun`, persisted snapshots/checkpoints, interaction contracts, journal events and existing claim/resume paths. Resumed execution refreshes fact retrieval instead of trusting a stale memory snapshot. |
| Fact storage | Existing `SparkPersonalMemory`, `SparkMemoryPreference`, `SparkMemoryExtraction` and migrations 0027–0029. No second memory system was needed. |
| Consent | Memory defaults off. Consent version/revision gates recall and proposal review. Exact personal facts require explicit approval; correction returns a fact to proposed state. Separate default-off academic extraction consent already existed in the API. |
| Academic observations | Deterministic, conservative first-person extraction; semantic goals/study facts, learning self-reports and episodic completion observations. These are not assessed mastery or semester records. |
| Deduplication | Normalized fingerprints, active-fact constraint, source receipts, owner preference locking and academic topic supersession. Personal response-preference conflicts and delayed jobs after deletion/revocation had gaps. |
| Retrieval | Owner/approved-state filtering, bounded lexical relevance ranking, token budget, optional fact-vector path and failure fallback. Raw transcript/vector/digest recall stays quarantined. Empty compatibility queries could supply unrelated facts. |
| Background processing | Final assistant persistence schedules extraction after transaction commit; paused/interim turns are excluded. Celery extraction retries are receipt-protected. Publication has no durable outbox. |
| Management and UI | Owner-scoped list/detail/review/delete API and personal fact controls existed. Academic opt-in lacked a settings control, and all displayed facts were labelled personal. |
| Prompt boundaries | Memory text is escaped and identified as untrusted evidence. Existing activity serialization uses public execution summaries. This pass does not expose private model reasoning. |

## B. Changes made

| Files | Changes in this pass |
|---|---|
| `pwanimate/services/personal_memory.py` | Explicit scope metadata; supported response length/format/language keys; approval atomically supersedes conflicting values, including recognized legacy keyless preferences. Editing recalculates preference keys, preserves academic scope and still requires review. Duplicate approval also checks legacy text. Centralized owner-locked deletion. |
| `pwanimate/services/persistent_memory.py` | Academic scope/purpose in extraction and recall; revoked/rejected academic topics cannot be automatically reapproved. Deletion cutoffs exclude older unprocessed messages. |
| `pwanimate/models.py`, `pwanimate/migrations/0030_memory_extraction_cutoffs.py` | Add `SparkMemoryPreference.extraction_cutoffs`, a JSON mapping of existing academic topic keys to deletion timestamps. It stores no deleted fact text, creates no parallel fact store, and defaults to an empty mapping. |
| `pwanimate/api/study_assistant.py` | Detail deletion reuses the owner-locked service so both deletion routes apply cutoff protection. |
| `pwanimate/services/study_memory.py` | Clear-all removes per-topic cutoff metadata while its existing global reset cutoff/revision continues to block old extraction. |
| `pwanimate/services/conversation_memory.py` | Empty runtime queries return no facts; compatibility fallback uses the same gate. The explicit empty-query personal-service preview remains for existing callers/tests. |
| `pwanimate/context/types.py` | Escaped scope/category metadata accompanies memory text in answer context. Planner JSON already receives retrieved metadata. |
| `pwanimate/templates/pwanimate/settings/partials/study_content.html`, new `academic_memory_consent.html`, `static/js/spark-study-desk.js` | Separate academic opt-in within the existing settings layout, initially disabled until memory is enabled. Show academic/personal labels; preserve individual personal approvals; clarify forget behavior. No unrelated redesign. |
| `tests/spark_memory/test_pipeline.py` | Add preference conflict, legacy compatibility, corrected keys, revocation/rejection, scope, empty-query, irrelevant-chat, foreign-source, run-preservation, delayed-deletion, cutoff-clear and settings-render regressions; add PostgreSQL-only approval/revocation/deletion race cases. |
| `tests/spark_memory/test_migration.py` | Apply actual 0029 and 0030 operations to reconstructed historical memory tables and verify preserved facts/defaults. |
| `tests/sals_wp3/test_chat_memory.py` | Update the existing duplicate-query mock for the strengthened approval query. |
| `tests/sals_wp0/run.py` | Accept optional Django test labels through the existing disposable PostgreSQL/pgvector runner. Existing default suite remains available. |
| New `tests/spark/memory-settings.cjs` | Verify independent opt-ins, disable cascade, failed consent rollback, academic labelling and absence of chat/run submissions. |

Corrections were reproduced before fixes: six new cases initially produced five failures plus one missing-metadata error. A later delayed-job deletion regression also failed before cutoff protection was added. These are diagnostic results, not remaining final-suite failures.

## C. Verification

| Executed check | Result |
|---|---|
| Final combined Django suite below | **77 discovered: 73 passed, 4 skipped, 0 failed**, 16.935 seconds. |
| Independent interpreter restart test | Passed using a committed file-backed SQLite backup, with no shared Python service state. |
| Historical memory schema upgrade | Actual 0029 + 0030 migration operations passed on isolated SQLite memory tables. This is not a full PostgreSQL migration-chain test. |
| Memory review during waiting run | API approval and rejection left the existing run/interaction waiting and created no new run/message. Subsequent clarification resumed the same run ID; executor was mocked. |
| `node tests/spark/memory-settings.cjs` | Passed. |
| `node tests/spark/memory-approval.cjs` | Passed: review/edit/reject, repeat-click protection, stale consent and restored proposal UI. |
| `node tests/spark/lifecycle-resume.cjs` | Passed: original-run payload, accepted-block removal and interrupted-stream reconnection. |
| JavaScript syntax and Python compile checks | Passed for touched code. |
| `makemigrations --check --dry-run` | Passed: no changes detected after generating 0030. |
| `git diff --check` | Passed. |
| Disposable PostgreSQL runner | Attempted; exited 1 before database creation because Docker socket access was denied. Local PostgreSQL installations lack the pgvector server extension. |

The four skipped tests require real PostgreSQL row locks: duplicate extraction redelivery, competing preference approvals, revocation versus extraction, and deletion versus delayed extraction. SQLite success is not evidence that these races pass on PostgreSQL. The existing separate consent-disable concurrency test was included in the blocked PostgreSQL command and was not executed successfully.

The isolated settings intentionally do not import local settings or credentials. They synchronize ordinary schemas and exclude unrelated PostgreSQL-specific indexes. Existing django-axes test-settings warnings remained visible. No whole-project test-suite pass, live model-provider pass, real server restart, browser end-to-end pass or live Celery-worker pass is claimed.

### Reproduce the executed Django suite

From the `pwaninet` repository root:

```bash
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  tests.spark_memory \
  tests.sals_wp3.test_chat_memory tests.sals_wp3.test_offline \
  tests.sals_wp3.test_database.MemoryConsentTests \
  tests.sals_wp3.test_database.ChatProposalTests \
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests \
  pwanimate.tests.test_conversation_memory \
  pwanimate.tests.test_streaming.GatewayStreamingTests \
  pwanimate.tests.test_streaming.GroqStreamingTests \
  pwanimate.tests.test_streaming.OpenRouterStreamingTests \
  pwanimate.tests.test_streaming.GeminiStreamingTests \
  pwanimate.tests.test_streaming.MockProviderStreamingTests \
  pwanimate.tests.test_agent_reconciliation.ActivityTaxonomyTests \
  --noinput --verbosity 1
```

### Blocked PostgreSQL verification command

This uses the existing isolated runner, not a shared application database:

```bash
.venv/bin/python -m tests.sals_wp0.run \
  tests.spark_memory \
  tests.sals_wp3.test_database.MemoryConsentTests \
  tests.sals_wp3.test_database.ConcurrentConsentTests \
  tests.sals_wp3.test_database.ChatProposalTests \
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests
```

## D. Outstanding issues

1. **Acceptance blocker:** run the full migration chain and row-lock race tests on isolated PostgreSQL/pgvector. Migration 0030 has not been applied to the application's development/production database.
2. **Unverified integration:** real Celery publication, worker restart/redelivery, live model responses and complete browser settings/chat flows. Mocked delivery/failure contracts and JavaScript DOM tests passed.
3. **Known reliability limitation:** broker publication failures are logged and do not break chat, but there is no durable outbox/reconciliation recovery. Some eligible exchanges can remain unextracted.
4. **Extraction limits:** exact first-person grammar, finite academic vocabulary, bounded 20-message batches and no historical transcript import. Broad paraphrases, semantic duplicate detection and optional fact-vector indexing/retrieval quality were not certified. Arbitrary manually written facts require owner correction; automatic conflict inference is limited to recognized topic/preference keys.
5. **Existing data:** legacy approved conflicting preference rows are reconciled when a recognized replacement is explicitly approved; this pass does not bulk rewrite historical personal content.
6. **Retention boundary:** deleting a saved fact removes it from future recall. Existing transcripts, generated answers and historical run/turn evidence are not retroactively rewritten. Individual academic deletion retains only its topic/timestamp cutoff; clear-all removes those cutoffs and uses the global reset barrier instead. A fresh post-deletion academic statement may be saved under ongoing opt-in; revocation/rejection blocks automatic topic reuse pending owner review.

## E. SALS 0.1 acceptance checklist

PASS below means demonstrated within the stated isolated service/API test scope, not production certification.

| # | Mandatory requirement | Status | Evidence |
|---|---|---|---|
| 1 | Approved memory persists across conversations/restarts | PASS | Owner-scoped cross-chat recall, independent Python process, source deletion retention. Real deployed restart untested. |
| 2 | Personal, academic, episodic and working context distinguished | PASS | Scope/purpose plus semantic/learning/episodic category; settings/context metadata; runtime working state stays in existing runs rather than the fact store. |
| 3 | Relevant retrieval without unrelated injection | PASS | Unrelated-subject, bounded ranking/budget and empty runtime query tests. Broader retrieval-quality evaluation deferred. |
| 4 | Personal memory disabled by default | PASS | Default/legacy consent tests; settings default-off check. |
| 5 | Individual personal facts require explicit approval | PASS | Proposed facts excluded from recall; exact version/revision approval, rejection and edit reapproval tests. Academic extraction requires separate opt-in. |
| 6 | Duplicate prevention and updated/conflicting information | PARTIAL | Serial duplicate, retry, preference conflict, legacy and academic supersession tests pass; PostgreSQL concurrency is skipped. |
| 7 | View, correct, delete and revoke memories | PARTIAL | API/UI tests, delayed-job cutoff and revocation tests pass; PostgreSQL deletion/revocation races and live browser flow remain unverified. |
| 8 | Strict authenticated ownership/isolation | PASS | Anonymous denial; foreign-owner view/edit/delete denied; foreign-source proposals rejected; owner-filtered recall. |
| 9 | Academic records separate from ordinary chat | PASS | Provenance-linked facts are separate rows; unrelated chat creates no study sessions, assessments or artifacts; clear-all preserves transcripts/formal records. |
| 10 | Memory processing does not block generation | PARTIAL | After-commit enqueue, pause exclusion, storage/broker failure and retry contracts pass; actual broker/worker integration untested. |
| 11 | Consent does not cancel/duplicate runs; continuation uses original run | PASS | Nonblocking memory API leaves active/waiting run unchanged; same-ID continuation API test and existing JS resume tests pass. Consent itself does not pause the run. |

| Additional mandatory verification | Status |
|---|---|
| PostgreSQL concurrent memory operations | NOT TESTED — environment blocked |
| Full PostgreSQL migration chain | NOT TESTED — environment blocked |
| Actual Celery worker restart/redelivery | NOT TESTED |
| Existing Spark regression coverage | PARTIAL — selected gateway/streaming/activity/interaction suites passed; whole project not run |

## F. Next-phase integration

Existing fact IDs, authenticated ownership, topic keys, scope/category, confidence, timestamps, source message/conversation links and supersession provide provenance for future unit records. A future semester/enrollment record can associate approved academic observations and assessment evidence with the correct student and unit through an explicit mapping layer; personal response preferences remain outside that record.

Unit/session association must use the student's consent and enrollment context rather than assuming that an ordinary academic chat starts a semester record. Keep self-reported difficulty/understanding separate from demonstrated quiz/assignment evidence; present extraction confidence is not a mastery score. Existing durable interactions and resume paths are the integration mechanism, without a second orchestration runtime.

No semester tracker, mastery calculation, new assessment engine or SALS 0.2 feature was implemented in this pass. This report is the review boundary.

## Follow-up: running database missing migration 0030

The supplied server log showed `UndefinedColumn` for `extraction_cutoffs`, followed by an aborted-transaction error when retrieval tried to restore its PostgreSQL timeout. Database exceptions now propagate through the retrieval helpers to the enclosing atomic boundary, which rolls back before returning the optional-memory fallback. Provider failures still use the existing fallback. A regression checks that no timeout-restoration SQL runs after a database failure and that the error is logged once.

Follow-up verification: `tests.spark_memory` discovered **41 tests: 37 passed, 4 PostgreSQL-only skips, 0 failures**, 12.877 seconds; `git diff --check` passed. The previous combined-suite counts above belong to the earlier verification run.

An attempt to inspect the configured database using `venv/bin/python manage.py showmigrations pwanimate` failed with `OperationalError` in this restricted environment. No application migration was applied. Run from the repository root in the server environment:

```bash
venv/bin/python manage.py migrate pwanimate
venv/bin/python manage.py showmigrations pwanimate
```

Migration `0030_memory_extraction_cutoffs` must show `[X]`. Restart/reload the application and Celery workers after deploying the updated code; the transaction fix alone does not create the missing column.
