# SALS 0.2 — Persistent Academic Unit Foundation

Date: 2026-11-12. Application: existing PwaniNet/Vasto Spark.

**Status: implementation/local checks finished; owner PostgreSQL schema and concurrency checks passed. Ownership/regressions and owner acceptance remain pending. SALS 0.2 is not marked complete.** No PostgreSQL or Docker command was executed by the agent. The owner reported applying application migration 0031 successfully. No SALS 0.3 work was started.

## A. Existing models reused

| Existing infrastructure | Reuse and audit finding |
|---|---|
| `documents.Institution`, `Programme`, `AcademicYear`, `Semester`, `AcademicUnit` | Preserve the existing academic domain and dated term identities. `courses` also has legacy curriculum models; those are not a second source of student enrollment for SALS. |
| `documents.StudentUnitEnrollment` | Canonical existing student-to-unit relationship, including owner, active state, source, semester and academic year. This differs from the shared unit definition and from `ProgrammeUnit`/`CourseAcademicUnit` curriculum offerings. |
| Enrollment services | `get_enrolled_units()` can auto-synchronize/create rows and enrollment rows can move terms. SALS intentionally reads existing enrollment rows directly and does not invoke synchronization, create enrollments or infer them from personal memory. |
| `PwanimateStudySession` | Reuse the active/paused/completed engine, timestamps, per-unit active constraint, existing conversations, roadmaps, checkpoints and assessment links. Existing standalone/custom study sessions remain standalone until authorized academic tracking; no historical backfill. |
| `PwanimateConversation`, `PwanimateMessage` | Durable sources for individually attributed activity spans. Ordinary chat remains independent of unit records. |
| `PendingAction`, `ActionService` | Reuse owner-checked explicit approval, expiration, cancellation, idempotent confirmation and existing action endpoints. No second approval engine. |
| `SparkAgentRun`, `SparkAgentInteraction` | Preserve checkpoints/continuations. Academic consent is nonblocking and does not claim, cancel, pause, resume or create an agent run. Existing waiting interactions resume the original run. |
| SALS 0.1 facts and vocabulary | Reuse its topic vocabulary and approved academic observations as explicitly labelled self-reports. Personal-memory consent and academic-record consent remain independent. |

Enrollment verification means verification against the application's enrollment source of truth, not an external registrar. Existing elective/retake enrollment rows may be student-selected. Missing semester, inactive enrollment/unit, inconsistent academic year, out-of-date term or a conflicting explicit profile semester makes enrollment ineligible for tracking. No missing academic data is invented.

## B. New or modified models and relationships

Migration: **`0031_unit_learning_foundation`**, dependent on existing pwanimate 0030 and documents 0021.

- `SparkAcademicTrackingPreference`: one per authenticated user; `ask` default, `automatic` or `disabled`; consent revision and change timestamp. Changing mode invalidates previous authorizations and pending proposals through revision matching.
- `SparkUnitLearningRecord`: immutable owner/enrollment/unit/semester identity plus authorization timestamp/revision and authorized source conversation. Database uniqueness on `(enrollment, semester)` and `(user, academic_unit, semester)` prevents duplicates, including duplicate inactive/history records. The unit and term are snapshots referencing existing domain rows; enrollment is not duplicated.
- `PwanimateStudySession.learning_record`: nullable FK for compatibility. Every session created for authorized unit tracking links to its record. A record may have many sessions. Linked sessions cannot switch records/owners/units and reject writes to expired or stale enrollment terms.
- `SparkUnitActivity`: attributed topic, bounded excerpt, evidence kind, session, record, source message/conversation, consent revision and occurrence time. Unique `(record, source_message, topic)` prevents retry duplicates. Study requests, self-reported difficulty and self-reported understanding are distinct; none is a mastery certification.
- `PendingAction.ACTION_TRACK_UNIT`: added action type; reuses the existing action model.

Creation re-reads/locks the real enrollment; consent and attribution serialize on the owner's preference, then enrollment/record. PostgreSQL locks target only the enrollment table (`of=('self',)`) to avoid nullable joined-table locking. Database uniqueness remains the final creation backstop.

Term snapshots survive enrollment rollover. A prior term is read-only through both the new endpoints and existing session/checkpoint/assessment write routes. No automated archive or academic calendar replacement was introduced. Catalog term/unit deletion is protected; linked relations use restricted deletion so account deletion can still cascade the student's records together. Deleting an ordinary source chat nulls provenance references and preserves academic excerpts/history. Deleting a study conversation that retains academic activity returns HTTP 409 instead of silently removing its sessions.

## C. Academic unit detection design

`UnitLearningService.detect` is deterministic, bounded and makes no model/provider call:

1. Read up to 100 existing student enrollment rows, including unit descriptions and terms; filter eligibility.
2. Require a direct study request, first-person learning statement or explicit academic-history question. Exclude casual mentions, third-party statements and recognized pasted/quoted material.
3. Split independently attributable clauses/topics. Require a supported enrolled-unit title/code, or corroborating enrolled subject title/topic vocabulary, or a meaningful multiword phrase from that enrolled unit's description.
4. Preserve multiple plausible candidates as ambiguity. An explicit enrolled unit name/code can disambiguate; no arbitrary tie-breaking.
5. Return separate unit/span matches where evidence is separable. For example, “Explain matrices and recursion” can produce separate matches if each topic has one enrolled candidate. Casual companion clauses do not become activity.

The requested symmetric-equations example resolves to Linear Algebra through its enrolled title/topic support or actual catalog description. Java without an eligible programming enrollment produces no unit proposal/record. Unsupported natural-language paraphrases abstain rather than manufacture confidence. The UI asks the student to name the unit when attribution is ambiguous.

## D. Study session and consent workflows

1. After a user message commits, the existing conversation service invokes optional academic processing. Failures are logged without failing chat generation.
2. A meaningful, unambiguous enrolled topic without valid consent creates an existing `PendingAction`. Retries reuse pending requests; rejection suppresses repeat requests for that unit/conversation/revision. No unit record, session or activity is created by a proposal.
3. The chat presents **Approve tracking / Not now**. The existing action endpoint requires an explicit confirm/cancel decision for academic proposals. Approval revalidates ownership, enrollment, term, source attribution, expiration and consent revision.
4. Approval creates/reuses the record and existing-engine unit session, and establishes a timestamp boundary. **The trigger and earlier chat are not imported.** The proposal explicitly describes future tracking.
5. Later eligible source spans can become activities. `ask` requires authorization for the current source conversation and an active session. `automatic` continues an already-authorized unit across conversations; selecting automatic mode alone does not authorize new units. `disabled` prevents new tracking. Changing preferences requires fresh unit authorization.
6. Pause, resume and end use the existing engine. Ending preserves history and timestamps; starting another session requires an explicit `tracking_consent: true` request. Existing resume now pauses peers of the same unit only. Legacy unit-session lookup skips read-only old-term sessions.

The sessions use dedicated existing-engine conversations so two units discussed inside one ordinary chat do not collide with the session engine's one-session-per-conversation relationship. Activities retain the original source conversation/message separately. A single unit session may collect correctly attributed spans from several chats under automatic authorization.

Approval/rejection does not submit a chat message or touch generation/cancellation controls. A local API test approves tracking while a run is waiting, then verifies that the existing clarification still resumes that exact run. Executor/model calls are mocked in that test.

## E. New APIs and UI changes

All routes authenticate the student. Detail/source/session lookups are owner-scoped. GET academic responses use private/no-store caching.

| Method and route | Contract |
|---|---|
| `GET /api/pwanimate/academic/` | Existing enrollments and eligibility, unit records, preference/revision and pending tracking actions. Optional `conversation_id` filters proposals and exposes current attribution/ambiguity. |
| `PATCH /api/pwanimate/academic/` | `{mode: "ask" | "automatic" | "disabled"}`; increments consent revision. |
| `POST /api/pwanimate/academic/detect/` | `{message_id}`; validates ownership, returns detected matches/ambiguity without creating enrollment or learning evidence. |
| `GET /api/pwanimate/academic/records/<uuid>/` | Term-specific chronological activities, sessions, topics, available safe assessment counts/status, academic self-reports, difficulties and supported outstanding roadmap items. |
| `POST /api/pwanimate/academic/records/<uuid>/` | `{action: "pause" | "resume" | "end", session_id}` or `{action: "start", tracking_consent: true}`. |
| Existing `POST /api/pwanimate/actions/<uuid>/` | Explicit `{decision: "confirm" | "cancel"}` for a pending unit-tracking action. |

The existing Memory settings layout includes tracking preferences, enrollments and a history viewer with lifecycle buttons. Read-only history hides write controls. Chat restores pending tracking controls after navigation/reload/focus and explains ambiguous attribution. Existing theme classes and responsive layout are reused; no unrelated redesign.

Relevant academic record summaries are supplied separately from personal memory to planner and answer context, and refreshed for durable continuation. Context explicitly labels data as untrusted, distinguishes requests/self-reports from assessed evidence, and forbids invented mastery/roadmaps. Assessment answer keys are not included. Approved memory observations are included only when owner, approved academic purpose, source message and attributed topic all agree; a personal interest never establishes enrollment.

## F. Files and migrations changed

The checkout contained substantial earlier uncommitted work. This pass preserved it and should not be represented as the author of the entire checkout diff.

| Area | Files changed in this pass |
|---|---|
| Models/schema | `pwanimate/models.py`; new `pwanimate/migrations/0031_unit_learning_foundation.py` |
| Academic service | New `pwanimate/services/unit_learning.py` |
| Existing service integration | `pwanimate/services/conversation.py`, `actions.py`, `study_session.py`, `agent_execution.py` |
| APIs | New `pwanimate/api/unit_learning.py`, `academic_guard.py`; `pwanimate/api/urls.py`, `views.py`, `study_assistant.py`; `pwanimate/views.py` for linked-history deletion responses |
| Context/orchestration | `pwanimate/context/types.py`, `pwanimate/orchestrator/types.py`, `service.py`, `agent.py` |
| UI | New `pwanimate/templates/pwanimate/settings/partials/unit_learning.html`; existing `study_content.html`; new `static/js/spark-unit-learning.js`; existing `static/js/pwanimate/pwanimate-chat.js`, `static/css/pwanimate/pwanimate.css` |
| Tests | New `tests/sals_02/__init__.py`, `test_foundation.py`, `test_persistence.py`; new `tests/spark/unit-learning.cjs`; `tests/spark_memory/urls.py` adds actual session page URL namespace for serializer tests |
| PostgreSQL handoff | New `scripts/test-sals02-postgres.sh`; `tests/sals_wp0/run.py` adds an explicit disposable-suite label while preserving its existing isolation guards/default suite |
| Report | This document |

## G. Local tests executed

**Final combined Django suite: 120 discovered, 111 passed, 9 skipped, 0 failed; 38.032 seconds.**

- SALS 0.2: 42 tests, including local service/API, actual migration-operation upgrade and fresh-process persistence checks; five PostgreSQL-only race cases skipped.
- Existing SALS 0.1 memory, consent, source ownership, waiting/resume, conversation recall, selected provider/gateway streaming and activity-taxonomy regressions included; four existing PostgreSQL memory race cases skipped.
- The SQLite upgrade test executes actual migration 0031 operations against the prior schema and confirms old sessions remain unlinked. It does not claim full PostgreSQL migration-chain compatibility.
- The restart test reads the same record, session and topic history from a new Python interpreter and a committed file database. It is not a deployed server restart test.
- `node tests/spark/unit-learning.cjs`: passed consent/rejection, repeated clicks, stale errors, restored requests, ambiguity, independent settings, read-only controls and no chat/run submission.
- Existing `memory-approval.cjs`, `memory-settings.cjs`, `lifecycle-resume.cjs`: passed.
- JavaScript syntax, Python compilation, `bash -n scripts/test-sals02-postgres.sh`, `makemigrations --check --dry-run` and `git diff --check`: passed.

The offline harness uses SQLite schema synchronization with ordinary constraints intact and excludes unrelated PostgreSQL-only indexes. It never imports application database credentials. Existing django-axes middleware/backend warnings remained visible. The PostgreSQL-only consent-disable test is part of the owner handoff, not a locally passed test. No PostgreSQL/Docker test, production migration, live model conversation, full browser end-to-end flow or complete project-wide test suite is claimed.

Reproduce the local combined suite from the repository root:

```bash
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  tests.sals_02 tests.spark_memory \
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

## H. PostgreSQL commands for the project owner

**These commands are supplied for the owner; the agent did not execute them.** They use temporary loopback-only PostgreSQL/pgvector containers and disposable databases through the project's existing isolated runner. They do not read production database settings or migrate the running application's database. The default interpreter is `venv/bin/python`; set `SALS_TEST_PYTHON=.venv/bin/python` if needed.

```bash
cd /home/zayne/projects/pwaninet

# 1. Python dependencies and Docker access
bash scripts/test-sals02-postgres.sh prerequisites

# 2. Prepare the isolated PostgreSQL/pgvector image
bash scripts/test-sals02-postgres.sh prepare

# 3. Full test-database migrations, migration reversal/upgrade and persistence
bash scripts/test-sals02-postgres.sh schema

# 4. Real row locks: record creation, approval/rejection, revocation and idempotency
bash scripts/test-sals02-postgres.sh concurrency

# 5. Ownership, uniqueness constraints, terms, attribution and session lifecycle
bash scripts/test-sals02-postgres.sh ownership

# 6. SALS 0.1 and targeted Spark regressions
bash scripts/test-sals02-postgres.sh regressions

# 7. Normal runners clean up automatically; remove stopped test leftovers
bash scripts/test-sals02-postgres.sh cleanup
```

After preparation, the equivalent combined run is:

```bash
bash scripts/test-sals02-postgres.sh all
```

Each schema/ownership/concurrency/regression invocation creates its own container, applies migrations to its own test database and removes its own container in `finally`, including normal test failure. No existing volume or database is reused. The cleanup phase removes only stopped containers labelled `sals.suite=sals02` and lists remaining labelled containers; an abruptly killed runner may leave a still-running test container requiring removal by its displayed exact name. Do not treat skipped PostgreSQL race tests or an interrupted command as passing. Share the full failing traceback plus final summary for review.

Migration 0031 has only been locally exercised in the isolated upgrade test. Applying it to the application's own database belongs to the normal deployment process after owner verification/acceptance; the commands above do not do that.

## I. Remaining limitations

1. **Acceptance blocker:** the owner's schema stage (8 tests) and concurrency stage (10 tests) passed. Ownership was interrupted during test-database migrations before its 35 tests ran; the PostgreSQL regression and cleanup stages have no completed output yet. These remaining stages and owner acceptance are pending.
2. **Enrollment provenance:** the existing enrollment table is the site's truth, including student-selected electives/retakes. There is no external registrar attestation, and old enrollment history cannot be reconstructed when no term-specific data was recorded. SALS does not invent it.
3. **Conservative detector:** English request grammar, a finite shared subject vocabulary and supplied catalog phrases. No syllabus extraction, multilingual/semantic model classifier or universal paraphrase coverage. Ambiguous/unsupported material remains ordinary chat.
4. **Consent policy:** automatic means continuation of an already-authorized unit, not blanket approval of every enrollment. Changing any tracking mode invalidates earlier grants. `ask` re-asks when source conversation/session changes. Trigger messages and older content remain unimported by design.
5. **History boundaries:** summaries return up to 100 recent activities, 50 sessions, 30 assessments and bounded related observations; activity truncation is flagged. Generic questions with no resolvable/named unit do not inject all academic history. Historical unit-linked sessions stay read-only; source excerpts remain when their ordinary source chat is deleted. Academic history deletion controls beyond account deletion are not added in this phase.
6. **Integration limits:** JavaScript DOM tests and rendered-template tests passed, but responsive visual/browser end-to-end and live model wording were not exercised. Model behavior must not be treated as proof of mastery. No background recovery sweep is provided if optional post-commit tracking processing fails.
7. **Deferred by scope:** no new mastery scoring, syllabus extraction, advanced roadmap generation, assignment grading, mock exams, spaced repetition or replacement orchestration engine. Existing assessment/roadmap models are read for available history only.

## J. SALS 0.2 acceptance checklist

PASS denotes demonstrated local service/API/DOM behavior unless otherwise stated; it is not PostgreSQL/deployment certification.

| Deliverable | Status | Supporting evidence |
|---|---|---|
| Audit/reuse existing academic domain and enrollment | PASS | Existing documents models/enrollment source reused; no alternate institution/enrollment domain or synchronization added. |
| Verified student enrollment required | PASS | Owner, active state, real term, date/year/profile consistency and non-enrolled-topic tests; missing information abstains. External registrar attestation remains unavailable. |
| One persistent record per student/enrollment/term | PASS | Local uniqueness tests and owner-executed PostgreSQL concurrent record creation passed. |
| Separate students and semesters; preserved history | PASS | Multiple-student/unit records, term rollover, immutable identity, read-only legacy APIs and account/source-deletion tests. |
| Reuse sessions; multiple sessions and lifecycle timestamps | PASS | Existing-engine pause/resume/end/start and historical-session skip tests; no second engine. |
| Cross-conversation and process-independent continuity | PASS | Authorized automatic tracking, chronological history, named-unit context and independent-interpreter record/session/topic tests. |
| Detection from enrolled/catalog context, including 3D lines | PASS | Title/description-backed detection, example topic, inactive/missing/foreign enrollment, casual/third-party exclusion and non-enrolled Java tests. Coverage is intentionally conservative. |
| Ambiguity and multiple correctly attributed units | PASS | Ambiguity abstention/explicit unit resolution, separate topic spans/sessions and cross-unit observation isolation tests; UI ambiguity notice. |
| Explicit scoped approval/rejection; no old-content import | PASS | Existing action expiry/revision/owner checks, mandatory API decision, pre-approval exclusion, rejection suppression and DOM tests. |
| Ask/automatic/disabled preferences and revocation | PASS | Local preference/stale-action tests and owner-executed PostgreSQL consent/attribution races passed. |
| Consent preserves original durable run/checkpoint | PASS | Approval while waiting leaves run untouched; existing interaction resumes same ID; original-run JS regressions pass. Executor mocked. |
| Personal memory and academic evidence remain separate | PASS | Independent consent, no interest-to-enrollment inference, source/topic-scoped academic self-reports, safe assessment summaries and no mastery claims. |
| Owner-scoped API access | PASS | Anonymous/foreign record, session and source denial; isolated unit summaries; no raw assessment keys. |
| Frontend settings, history and restored approvals | PARTIAL | DOM/template tests pass with existing theme/layout; real responsive/browser review pending. |
| Migration and targeted local regressions | PASS | Actual isolated 0031 upgrade; final 111 passing local tests, no migration drift; nine intentional PostgreSQL skips. |
| PostgreSQL migration-chain and five new concurrency tests | PASS | Owner output: schema 8/8 and concurrency 10/10, including five new unit races, four memory races and consent-disable race. |
| PostgreSQL ownership and targeted regressions | NOT TESTED | Ownership was interrupted during database setup; no completed regression-stage output supplied. |
| PostgreSQL handoff preparation/cleanup execution | PARTIAL | Owner prerequisites/image preparation passed; cleanup completion not shown. Agent did not invoke Docker. |
| Production/deployment verification and owner acceptance | PARTIAL | Owner reported application migration 0031 applied OK; UI/deployment behavior and final acceptance remain unverified. |

Stop point: implementation, local verification and PostgreSQL handoff. Await the owner's results and acceptance before considering a later phase.

### Owner verification update

Source: terminal output supplied by the project owner in this conversation.

- Application migration `0031_unit_learning_foundation`: reported `OK`.
- Prerequisites and pgvector image preparation: passed.
- PostgreSQL schema stage: **8 tests passed**, 13.928 seconds.
- PostgreSQL concurrency stage: **10 tests passed**, 10.097 seconds.
- Ownership stage: **interrupted**, with 35 tests discovered. The traceback ends inside Django's migration setup; no ownership assertion failure or pass was reported. Do not count it as a failing ownership test or as passing.
- PostgreSQL regressions and cleanup: no completed results supplied.
- Existing django-axes test-settings warnings and the legacy `user_following` migration notice did not prevent the completed stages from passing.

Resume with `ownership`, then `regressions`, then `cleanup`; prerequisites/image download and the passing schema/concurrency stages do not need to be repeated for this output.
