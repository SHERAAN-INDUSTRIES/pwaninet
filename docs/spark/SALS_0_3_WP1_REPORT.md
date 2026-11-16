# SALS 0.3 / WP1 — Concept Mastery Foundation

Date: 2026-11-12. Scope: Spark in the existing PwaniNet/Vasto application.

**Status: implementation and local verification finished; owner PostgreSQL verification and acceptance pending. WP1 is not marked accepted/complete.** No PostgreSQL or Docker test command was executed by the agent. No application/production database was migrated. No adaptive teaching, WP2 or later phase was implemented.

## 1. Architecture summary

Before changes, the audit found a real student/enrollment/semester record (`SparkUnitLearningRecord`), its existing study-session engine, and `SparkAssessment` questions/responses/grades stored in JSON. The grader already withheld correctness when a generated answer key lacked independent verification. Session roadmap/percentage fields were legacy values, not sufficient concept identity or mastery certification. There was no versioned student-concept evidence policy.

The implementation extends that foundation:

`student → real enrollment + semester unit record → explicit student concept → frozen question binding → evidence → deterministic versioned decision`.

Concept parent/prerequisite relationships stay within the same student/unit/term and are checked for cycles under the record lock. Personal memory is not imported into formal assessment evidence. Existing unit activities may be explicitly associated as unverified observations. There is no second student/enrollment domain, session engine, assessment engine or agent runtime.

The pre-change architecture summary was provided in the conversation before modifying code. Substantial prior uncommitted changes in this checkout were preserved.

## 2. Existing components reused

| Component | Reuse |
|---|---|
| `StudentUnitEnrollment`, `Semester`, `SparkUnitLearningRecord` | Actual owner, unit and semester identity; current enrollment checks; historical isolation. |
| `SparkAcademicTrackingPreference` | Current tracking authorization/revision for new concepts, delivery, evidence and recalculation. Revocation blocks new positive evidence. |
| `PwanimateStudySession` | Existing sessions, active status, immutable record link, chronology and account-deletion behavior. |
| `SparkAssessment` | Existing question JSON, student submission, completion, existing endpoint and public serializer. No replacement assessment engine. |
| `assessment_validation` | Strict question validation, exact numerical/rational grading, reviewed-reference verification and unverified/inconclusive outcomes. |
| `SparkUnitActivity` | Already-consented owner-scoped activity provenance; self-reports remain unverified. |
| Existing durable runs/interactions | Unchanged. The new assessment path does not create or resume runs, and existing continuation regressions remain covered. |
| Existing isolated test runners | SQLite local harness; owner-only PostgreSQL/pgvector WP0 runner with a new `sals03wp1` resource label. |

## 3. Models, services and APIs added/modified

### Models

| Model | Purpose |
|---|---|
| `SparkUnitConcept` | Stable UUID and record-scoped immutable key; title/description; parent; current state/version; assessment/review/mastered/due timestamps; supported misconception codes. Unique `(record, key)` and a state constraint. |
| `SparkConceptPrerequisite` | Explicit directed links; same owner/unit/semester checks, unique edge, no self-edge, service-level cycle checks. |
| `SparkConceptQuestion` | One concept binding per `(assessment, qid)`; frozen source session, question snapshot, trusted reference digest, independence family, difficulty, assistance, purpose and delivery timestamp. The existing assessment remains the question/response engine. |
| `SparkConceptEvidence` | Immutable response/evaluation snapshot with concept, question or unit activity, source session, time, assistance, evaluation method and verification status. One evidence row per bound question; unique concept/activity observations; exactly one source category. Trusted invalidation changes status/reason without deleting the original response. |
| `SparkConceptTransition` | Append-only, unique `(concept, version)` history, including initial NOT_STARTED, prior/new state, policy version, policy snapshot, supporting IDs, metrics and explicit unmet requirements. |

Student, enrollment, semester and academic unit are reached through the concept's immutable unit record, rather than copied into another identity domain. One assessment question maps to one concept in WP1; future multi-concept scoring is not inferred.

### Services

- `ConceptMasteryService`: owner-scoped concept authoring, graph validation, reviewed question delivery, trusted question binding, evidence capture, observation association, trusted invalidation, recalculation and summaries.
- `mastery_policy`: database-free configurable deterministic evaluation. No model/provider call and no confidence-to-probability conversion.
- `AssessmentService.submit_answer`: now locks actual submissions and persists grades plus bound evidence in one transaction. The order is consent preference → enrollment → unit record → assessment. Concurrent submissions cannot overwrite each other's JSON question results.
- A bound question has **one accepted response**, including an inconclusive response. Replayed/different submissions cannot rewrite immutable evidence provenance; reassessment issues a new question. Invalid answer formats are not accepted as evidence. Unbound legacy questions retain their ordinary feedback behavior.
- Existing `mastery_percent`/`progress_percent` fields are not repurposed or promoted. Bound submissions add `concept_mastery` and `evidence_id` to the existing result; legacy metrics alone still do not certify mastery.

Trusted backend binding/invalidation methods are intentionally not exposed as student-supplied grading/assistance/verdict endpoints and are not registered as unrestricted model tools.

### Owner APIs

All routes authenticate; foreign records/concepts/sources are rejected. Responses are private/no-store. Client bodies use strict field allowlists.

| Method and route | Behavior |
|---|---|
| `GET/POST /api/pwanimate/academic/records/<record_id>/concepts/` | List or explicitly create concepts. POST requires `key`, `title`; optional `description`, `parent_id`, `prerequisites`. No automatic syllabus import. |
| `GET /api/pwanimate/academic/records/<record_id>/mastery/` | Counts of explicitly authored concepts by effective state; explicit incomplete-syllabus coverage label; no mastery probability. |
| `GET/PATCH /api/pwanimate/academic/concepts/<concept_id>/` | Metadata, current/effective state, policy/version and recent transition history. PATCH accepts metadata/relationships, never state, identity, grade or verification. |
| `GET/POST /api/pwanimate/academic/concepts/<concept_id>/evidence/` | Retrieve provenance/response/results; POST references an existing bound `assessment_id`/`qid`, or an owned same-record `activity_id`. It does not accept a verdict or arbitrary chat/memory source. |
| `POST /api/pwanimate/academic/concepts/<concept_id>/recalculate/` | Re-evaluate existing trusted evidence and prerequisites. No supplied score/state is accepted. |
| `POST /api/pwanimate/academic/concepts/<concept_id>/assessments/` | Explicit reviewed-bank delivery using `session_id`, optional `count` and `purpose` (`diagnostic`, `practice`, `retention`). Reuses the existing assessment model and answer endpoint. |
| Existing `POST /api/pwanimate/study-sessions/<session_id>/assessments/` | Existing `{action: "answer", assessment_id, qid, answer}` now captures eligible bound evidence atomically. |

Evidence views distinguish historical verification from current source usability/eligibility and expose purpose, difficulty and assistance without leaking answer keys. History responses are bounded (100 evidence rows / 100 recent decisions); stored history remains intact.

## 4. Mastery transition rules

`SALS_MASTERY_POLICY` overrides validated defaults; a hash of the effective policy is included in each versioned decision.

| State | Explicit rule |
|---|---|
| `NOT_STARTED` | No usable evidence. Revoking/invalidating all supporting evidence can return here. |
| `LEARNING` | Consented observations/self-reports or unverified attempts exist, without verified decisive assessment evidence. |
| `PRACTICING` | Verified decisive results exist but mastery requirements are incomplete, or at least two of the last three recent independent results show errors. Assisted/unknown-assistance results can show practice, but earn no independent mastery credit. |
| `MASTERED` | All configured independent quantity, context, difficulty, weighted accuracy, prerequisite, retention and later-gap requirements pass. |
| `REVIEW_DUE` | Previously qualified mastery reaches its scheduled review date, or a prerequisite/later verified gap requires review without meeting the stronger regression rule. |

Default mastery requirements:

- At least **five distinct reviewed question families**; repeated questions/parameter variants must share their independence identity.
- At least **two actual assessment contexts**.
- At least **two correct non-easy question families** (difficulty ≥ 2).
- Difficulty-weighted accuracy ≥ **0.80** across the latest independent result per question family. This is observed performance, not a validated mastery probability.
- Every explicit prerequisite is currently `MASTERED`, including its review deadline.
- At least **two distinct correct retention families**, delivered at least **seven days after** the first qualifying correct evidence. Delivering a question immediately and merely answering it later does not pass.
- No later incorrect independent result remains after the last successful retention check.
- Review becomes due **30 days after** the latest qualifying retention check; regression checks recent results within **90 days**.

Configuration validation preserves a minimum of three questions, two contexts and a positive retention delay, so configuration cannot make one correct answer sufficient. Policy changes produce a new policy/versioned decision without rewriting previous policy snapshots.

Reads expose a computed `effective_state` when a review date has elapsed; explicit recalculation persists the corresponding transition. No scheduler, review notification or adaptive teaching loop was added. Reassessment can regain mastery after actual evidence satisfies the rules again.

## 5. Evidence validation rules

1. New positive evidence requires an authenticated owner, the real matching enrollment/semester, a writable term and current academic tracking authorization.
2. Assessment owner, frozen delivery session, concept record and current assessment session must agree. Moving an assessment or changing its question/response invalidates its credit on recalculation rather than rewriting its original evidence.
3. Student APIs may reference only existing server-bound questions, never create verification flags, difficulty, assistance, timestamps, confidence values or mastery states.
4. Correctness is recomputed with the existing numerical grader against an exact reviewed prompt/key and matching registry digest. A model-authored `correct: true`, key or confidence cannot supply verification.
5. Independent credit additionally requires server delivery metadata `assistance=none`, the reviewed numeric evaluation method and the existing assessment eligibility flag. Unknown/helped/excluded assessments do not count toward independent mastery.
6. An accepted response has a persisted server grading time after delivery. Uncaptured responses from before current authorization are not silently imported.
7. Retry identity is the unique bound assessment/question; observation identity is concept/activity. Record-level serialization, unique constraints and decision fingerprints prevent duplicate evidence and replayed state versions.
8. Self-reports/activities are owner-scoped, same-unit/term/session observations. They are not assessed understanding. Personal memories and casual chat are not formal evidence sources.
9. Misconceptions are only explicit reviewed answer-pattern codes (for example, a known determinant sign error). Arbitrary incorrect answers do not cause invented diagnoses.
10. Trusted invalidation retains the original response/result and a reason/timestamp, removes credit and recalculates dependents. It remains possible after consent revocation/term closure because it corrects old credit rather than creates new proof. There is no student HTTP endpoint for rewriting or selectively deleting bad grades.

## 6. Database migration and files

**`pwanimate/migrations/0032_concept_mastery_foundation.py`** creates the five new models, FKs, indexes and constraints. No old questions, personal memories, roadmap percentages or unlinked sessions are automatically converted/certified. It was not applied to the application's database by the agent.

| Area | Files |
|---|---|
| Models | New `pwanimate/mastery_models.py`; re-exported from existing `pwanimate/models.py` |
| Policy/services | New `pwanimate/mastery_policy.py`, `pwanimate/services/concept_mastery.py`; existing `pwanimate/services/assessment.py`, `pwanimate/assessment_validation.py` |
| APIs | New `pwanimate/api/concept_mastery.py`; existing `pwanimate/api/urls.py` |
| New tests | `tests/sals_03_wp1/test_mastery.py`, `test_policy.py`, `test_persistence.py`, package initializer |
| Test compatibility | `tests/__init__.py` prevents cross-suite discovery collisions; database-free legacy grading/API tests now exercise the extracted pure grading helper; the 0031 schema harness rebuilds descendant mastery tables to preserve PostgreSQL foreign-key integrity |
| Existing tests adjusted | `tests/sals_wp1/test_service.py`, `tests/sals_wp1/test_api_checkpoint.py`, `tests/sals_02/test_persistence.py` |
| Handoff | New `scripts/test-sals03-wp1-postgres.sh`; existing `tests/sals_wp0/run.py` accepts the isolated `sals03wp1` label |

The pure legacy test adaptation does not replace real database/API coverage: the new tests exercise the actual submission endpoint, locks/capture contract, ownership, provenance and result serialization.

## 7. Executed local verification

| Executed check | Result |
|---|---|
| Combined mastery, legacy grading, SALS 0.1/0.2 and selected Spark regressions | **185 discovered: 170 passed, 15 skipped, 0 failed**, 68.628 seconds. |
| Final focused mastery/grading run after freezing inconclusive-response replays | **65 discovered: 59 passed, six skipped, 0 failed**, 32.150 seconds. Counts overlap the combined run; do not add them. |
| Actual isolated 0032 operations over the 0031 schema | Passed; existing unit/session/assessment data preserved, no automatic concepts/evidence certification. |
| New Python interpreter/database backup | Passed: same concept ID, state/version, evidence and transition counts; no shared process cache. PostgreSQL server restart was not simulated. |
| New policy tests | Passed: thresholds, family/context/difficulty distinctions, prerequisite gaps, future/invalid evidence, stable policy versioning and independently computed determinant reference values. |
| JavaScript unit tracking, memory approvals/settings, durable resume, quiz flow | All five selected scripts passed. No mastery-specific frontend redesign was introduced. |
| Python compilation, shell syntax, migration drift, whitespace | Passed. `makemigrations --check --dry-run` reported no changes. Only `bash -n` was used on the PostgreSQL script. |

The combined run's 15 skips comprise six new WP1 concurrency tests, five SALS 0.2 races and four SALS 0.1 memory races. They require real PostgreSQL and are not passing local evidence. The owner's isolated handoff also retains the older consent-disable concurrency test.

The final focused run covers the last submission-path correction: bound inconclusive responses now remain frozen under retries, preserving their original evidence source. The earlier combined regression result is reported separately and honestly.

Local settings use the existing isolated SQLite harness, not application database credentials. Existing django-axes test-settings warnings remain visible. The first test rounds found delayed-delivery clock behavior and test discovery/fake-persistence compatibility issues; these were corrected before the passing results above. No full project-wide suite, live browser/model rollout, PostgreSQL test or Docker test was executed by the agent.

Reproduce local combined verification:

```bash
cd /home/zayne/projects/pwaninet
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  tests.sals_03_wp1 tests.sals_wp1 tests.sals_02 tests.spark_memory \
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

## 8. Owner-run PostgreSQL handoff

**The following commands are supplied for the owner. The agent did not execute them.** Each test phase creates a new loopback-only PostgreSQL/pgvector container and disposable test database using the existing isolation guards; it runs that database's migrations and removes its exact container in `finally`. No production/application database or existing volume is reused.

```bash
cd /home/zayne/projects/pwaninet

# 1. Verify Python dependencies and Docker access
bash scripts/test-sals03-wp1-postgres.sh prerequisites

# 2. Prepare the isolated pgvector image
bash scripts/test-sals03-wp1-postgres.sh prepare

# 3. Full isolated migrations, reverse/forward checks and 0032 preservation
bash scripts/test-sals03-wp1-postgres.sh schema

# 4. Six new real PostgreSQL concurrency cases
bash scripts/test-sals03-wp1-postgres.sh concurrency

# 5. Ownership, terms, evidence validation, progression/regression and API tests
bash scripts/test-sals03-wp1-postgres.sh ownership

# 6. Existing grading, SALS 0.1/0.2 and selected Spark regressions
bash scripts/test-sals03-wp1-postgres.sh regressions

# 7. Normally automatic; remove stopped WP1-labelled leftovers and list any others
bash scripts/test-sals03-wp1-postgres.sh cleanup
```

After preparation, `bash scripts/test-sals03-wp1-postgres.sh all` runs the combined isolated suite. The default interpreter is `venv/bin/python`; override `SALS_TEST_PYTHON=.venv/bin/python` if needed.

The six WP1 concurrency cases are concept creation, same-question submission, different-question submissions without lost JSON updates, capture/recalculation replay, invalidation versus replay, and opposing prerequisite edits. Share the full traceback/final summary for any failure. Database setup applies the complete migration chain; allow it to finish before judging a stage as stalled. Interrupted/skipped tests are not passes.

Cleanup only removes stopped containers labelled `sals.suite=sals03wp1`. An abruptly killed runner may leave a running labelled test container; the cleanup listing identifies it for exact-name removal. The commands above migrate only disposable test databases. Application migration 0032 still belongs to the normal development/deployment migration process; no application migration command was executed here.

## 9. Known limitations and boundaries

- Thresholds and difficulty levels are **provisional engineering policy**, not empirically calibrated probabilities or external academic certification. MASTERED means the versioned policy passed for an explicitly authored concept.
- The starter trusted bank contains six determinant families only. Other concepts can be authored and observed, but unsupported/generated keys do not acquire verified correctness by assertion. General theory/symbolic grading and a wider vetted bank are not supplied in WP1.
- `assistance=none` means unassisted server delivery in this assessment flow. It cannot prove absence of outside help, prior exposure or help in another chat. Future in-product hint/solution workflows must mark assistance or invalidate/reissue bindings; this package does not implement that teaching flow.
- Explicit concepts/relationships are not a generated syllabus. Course completeness, broader curriculum mapping and cross-unit prerequisites remain outside this package. Only same-unit/semester prerequisites are supported.
- There is no automatic review scheduler or adaptive lesson generator. Effective review status is readable; recalculation records a new decision. Existing UI percentage fields remain legacy and a new concept-dashboard UI is not added.
- Trusted imports, reviewed bank maintenance and negative corrections remain backend integration responsibilities. Students cannot select a verification method/assistance level or directly revoke selected incorrect evidence through the public API.
- Provenance payloads are protected by normal model/service writes and database uniqueness. Privileged raw SQL can still modify stored data; source changes are checked again during recalculation. Persisted mastery is explicitly labelled a versioned snapshot and should be recalculated after trusted provenance corrections.
- Evidence/transition API histories are bounded; complete rows remain stored. The synchronous evaluator currently recalculates the unit's bounded concept graph; heavy-load performance has not been benchmarked.
- Owner PostgreSQL migration/concurrency results, live deployment behavior and owner acceptance remain outstanding for this WP1. Existing PostgreSQL results from SALS 0.2 do not certify these new changes.

## 10. WP1 acceptance checklist

PASS means demonstrated local behavior within the stated verification scope.

| Deliverable | Status | Evidence |
|---|---|---|
| Pre-change audit and architecture summary | PASS | Existing enrollment/unit/session/assessment architecture inspected; summary given before edits. |
| Stable student/unit/semester concept identity | PASS | Record-scoped unique keys, immutable identity and separate student/semester tests. |
| Titles/descriptions/parents/prerequisites | PASS | Owner APIs, same-scope relationships and cycle checks tested. No automatic syllabus invented. |
| Five explicit states and deterministic rules | PASS | Policy/service tests cover insufficient evidence, retention, mastery, review and regression. |
| Correct answer alone cannot establish mastery | PASS | Minimum independent/context/difficulty/retention gates and configuration guard tests. |
| Self-report, attempt, assistance, independence and retention distinctions | PASS | Separate provenance/kinds/assistance and policy exclusion tests; unverified observations never establish mastery. |
| Verified evidence and source provenance | PASS | Frozen question/session/reference binding, response regrading and tamper/moved-session tests; no client verdict acceptance. |
| Duplicate and replay prevention | PARTIAL | Local uniqueness, frozen accepted response and idempotent version tests passed; PostgreSQL races await owner. |
| Concurrent evidence and graph updates | NOT TESTED | Six PostgreSQL-only tests supplied and intentionally skipped locally. |
| Versioned history, misconceptions and timestamps | PASS | Contiguous decision versions, immutable policy snapshots, supported misconception patterns and review timestamp tests. |
| Reassessment/regression/invalidated evidence | PASS | Later-error regression, renewed retention, revoked/invalid evidence and trusted correction-after-revocation tests. |
| Prerequisite competence | PASS | Policy gating and scoped topological recalculation tested; concurrent opposing edits await owner. |
| Owner-scoped services/APIs and term isolation | PASS | Foreign/unauthenticated access, wrong session/unit/semester, forged metadata/verdict and revoked-consent tests. |
| Integration with existing assessments | PASS | Actual answer API persists grades/evidence atomically and adds concept results; legacy grading/quiz regressions pass. |
| Memory/ordinary chat kept out of formal mastery | PASS | Only explicit authorized activity associations; memory statements cannot create formal graded proof. |
| Migration 0032 | PARTIAL | Actual isolated SQLite upgrade preserves old records/assessments; full PostgreSQL migration handoff pending. |
| Local verification | PASS | Combined 170 passing / 15 skipped; final focused 59 passing / six skipped; selected JS/static checks pass. |
| PostgreSQL handoff | PARTIAL | Copy-ready isolated script supplied and shell syntax checked; owner execution pending. |
| No WP2/adaptive teaching/replacement runtime | PASS | No adaptive teaching or new orchestration engine added. |
| Owner PostgreSQL verification and acceptance | NOT TESTED | Not marked complete; stop at WP1 handoff. |
