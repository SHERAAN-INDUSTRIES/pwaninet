# SALS 0.3 / WP2 — Adaptive Teaching Engine

Status: implementation and local verification handoff; **owner verification and acceptance pending**. Spark in the existing PwaniNet/Vasto application only. No WP3 implementation, PostgreSQL execution, Docker execution, production migration, or deployment was performed.

## A. Existing architecture reused

The audit found that the existing study prompts already encourage explanation and adaptation. However, study-mode generation bypassed the bounded agent planner, had no persistent instructional phase, and did not require a saved opportunity to respond before advancing. Its hints were not connected to WP1 assistance provenance. The frontend also hid assistant answer bodies whenever a run paused, making an explanation accompanying a question invisible.

The implementation reuses:

- `PwanimateStudySession`, its objective, status and source conversation; SALS 0.2's enrolled-unit/semester learning record and tracking authorization.
- `StudyAgent`, `PwanimateOrchestrator`, `SparkAgentRun`, existing snapshots, `claim_run`, continuation executor, journal, cancellation and retry endpoints.
- `SparkAgentInteraction` and the existing single-question widget, explicit Send, draft preservation, accepted-answer removal and run restoration.
- WP1 concepts, prerequisite edges, immutable question bindings, reviewed determinant references, assessment submissions, verified evidence, recalculation and mastery policy.
- The existing final-answer model path and authorized selected-document context.
- Existing Markdown, DOMPurify and KaTeX rendering, Bootstrap/theme classes, API authentication, and isolated SQLite and PostgreSQL test runners.

No institution, enrollment, study-session, assessment, or orchestration engine was replaced.

## B. Teaching state machine and transition rules

`SparkTeachingState` is one checkpoint per existing study session. It holds an objective, selected existing concept, educational phase, mode, difficulty, current exercise, hint level, original run, version and handled-question identities. `SparkTeachingTurn` preserves educational outcomes and idempotent receipts, including source answered interaction and the next question. These records contain educational progress, not private model reasoning.

| Trigger | Educational result |
| --- | --- |
| Start guided study | EXPLAIN; DIAGNOSE when a recorded prerequisite lacks verified competence |
| Start PRACTICE mode | PRACTICE directly |
| DIAGNOSE + continue | EXPLAIN |
| EXPLAIN + continue | DEMONSTRATE |
| DEMONSTRATE + continue | PRACTICE |
| Answer a practice question | EVALUATE; record a verified or inconclusive result and appropriate recommendation |
| Choose another problem from EVALUATE/ADAPT | PRACTICE with updated difficulty |
| Ask for clarification or a simpler explanation | EXPLAIN using another representation; lower difficulty when requested |
| Hint levels 1–3 | Stay with the current problem; record assistance |
| Hint level 4 | ADAPT with full explanation; no independent-work claim |
| Explicit full solution | Supply the explanation and end the current question sequence |
| QUICK or stop | End the question sequence without forcing an assessment |

Questions are deduplicated. New questions use the same run; answering requires an explicit submission. The controller never automatically marches through all phases. A correct answer raises the next difficulty by one, bounded at three; repeated incorrect answers lower it. A missing prerequisite is described as missing evidence, not proof of ignorance.

State changes run under database transactions and row locks. Constraints enforce one state per session, one receipt per source/interaction, one turn per answered interaction, and bounded difficulty/hint levels. Replayed receipts do not advance the lesson again. A model failure after an accepted answer retains its continuation; the existing retry endpoint reuses the receipt and restores the same pending checkpoint.

## C. Mathematical teaching policy and representative example

The server prompt requires the operation, its validity, the expression's meaning, and its contribution to the original objective. A small authored example bank makes representative behavior auditable; general/specialized requests use the existing answer model. Selected documents use the grounded model path. Attachments retain the existing multimodal path.

For a line through P(1,2,3) with direction (2,−1,4):

1. The point anchors the line; the nonzero vector determines its orientation.
2. Vector addition gives `r(t)=P+t*d`, hence `x=1+2t`, `y=2−t`, `z=3+4t`.
3. The same parameter identifies one point across all coordinates.
4. Subtract the origins and divide by nonzero direction components: `t=(x−1)/2=(y−2)/(−1)=(z−3)/4`.
5. Equal ratios eliminate the parameter and express the same line.
6. At t=1, the point is (3,1,7), and all ratios equal one. A zero direction component fixes its coordinate instead of creating a zero denominator. The zero vector does not define a line.
7. The student chooses clarification, a worked derivation, practice, or stopping through the existing question control.

Full authored mathematics, recursion and photosynthesis outputs are in `docs/spark/SALS_WP2_REPRESENTATIVE_OUTPUTS.md`. Semantic rubrics test affine equivalence at multiple rational parameter values, fixed-coordinate conditions, factorial termination/unwinding, new versus demonstrated examples, and conservation of atoms. These exceed keyword-only assertions, but do **not** certify arbitrary live model output quality.

## D. Tutoring modes and hints

- QUICK: concise result with the necessary justification; no compulsory question.
- GUIDED: default; one concept followed by an explicit choice of next action.
- DEEP: derivation, assumptions, intuition and alternative representations.
- PRACTICE: student attempts before receiving solutions.

Hints progress through a conceptual nudge, formula/strategy, partial step, and full explanation. Authored partial hints are separate from full solutions. Assistance persists independently of any grading result. Mode changes preserve history and affect subsequent transitions, including a currently pending run.

The chat appends a theme-aware mode selector to new teaching responses. It calls the owner-scoped preference endpoint rather than sending another chat request. Reloaded history preserves explanation bodies; the selector itself is added to newly received teaching responses, and saved mode remains available through the API.

## E. Integration with WP1 mastery

Existing concepts resolve only within the owned unit/semester, using an unambiguous existing identity/title. No syllabus or concepts are automatically invented. Cross-unit concepts are rejected; a different known topic clears an incompatible old binding. Unit attribution and current tracking authorization are checked before new linked teaching history is persisted. Paused sessions, revoked consent and historical/read-only terms reject writes. Revoked tracking routes new conversations through normal answering rather than producing a teaching error.

Instructional receipts distinguish topic exposure, completed explanation, guided practice, independent attempt, verified assessment and retention evidence. Formative checks never become formal mastery evidence. Open prose that cannot be deterministically evaluated is inconclusive.

For the existing reviewed determinant bank, practice calls WP1's delivery and submission services. Requested difficulty affects selection among reviewed questions; it never changes their verified difficulty or answer keys. WP1 remains the only authority that can assign MASTERED.

`effective_assistance` combines the immutable original binding with teaching assistance received **before the original grading timestamp**. Hints/full solutions exclude independence. Later assistance does not retroactively erase an earlier independent attempt. Retry, submission and evidence uniqueness safeguards remain intact. Known reviewed wrong-answer signatures produce a focused explanation and a question about the student's method, rather than an asserted diagnosis.

## F. Files, API and migrations

| Area | New or changed files |
| --- | --- |
| Models | `pwanimate/teaching_models.py`; import in `pwanimate/models.py` |
| Migration | `pwanimate/migrations/0033_adaptive_teaching.py`, depending on WP1 `0032` |
| Teaching | `pwanimate/services/teaching.py`, `pwanimate/teaching_lessons.py` |
| Mastery integration | `pwanimate/services/concept_mastery.py`: assistance provenance and reviewed difficulty selection |
| Orchestration | `pwanimate/orchestrator/agent.py`, `service.py`, `types.py`: teaching hook, scoped prompt/context, final-answer reuse |
| Durable execution | `pwanimate/services/agent_runs.py`, `agent_execution.py`: preserve selected-resource context and visible teaching pauses; retry remains within the original runtime |
| HTTP | `pwanimate/api/teaching.py`, `urls.py`, `views.py` |
| Memory boundary | `pwanimate/services/persistent_memory.py`: exclude paused teaching bodies from ordinary memory extraction |
| Frontend | `static/js/pwanimate/pwanimate-chat.js`: visible lesson bodies during pauses and mode selection; existing safe renderer and question widget retained |
| Python tests | `tests/sals_03_wp2/`: teaching, quality, persistence, concurrency and baseline modules |
| Migration harnesses | `tests/sals_02/test_persistence.py`, `tests/sals_03_wp1/test_persistence.py`: rebuild descendant teaching tables without losing PostgreSQL foreign keys |
| Node tests | `tests/spark/teaching-flow.cjs`, `markdown-math-unit.cjs`, `fixtures/teaching-examples.json` |
| Handoff | `scripts/test-sals03-wp2-local.sh`, `test-sals03-wp2-postgres.sh`; disposable suite label in `tests/sals_wp0/run.py` |

API: `GET/PATCH /api/pwanimate/study-sessions/<session_id>/teaching/`.

GET returns the current checkpoint and the latest 100 educational history entries. PATCH accepts only mode, optional expected version, objective and existing concept ID. Objective/concept changes are blocked while the current run is active. Student-supplied phase, verdict, assistance or mastery fields are rejected. Responses are private/no-store; foreign sessions return 404.

The migration adds teaching tables and constraints without backfilling unapproved chat into learning evidence. It must be deployed through normal migration procedures before using the new code; no existing application database was migrated in this task.

The checkout already contained substantial changes from earlier work. The table lists this work package's changes; it does not attribute all existing dirty files to WP2.

## G. Executed local verification

Final local results: **233 Python tests executed: 214 passed, 19 PostgreSQL-only tests skipped, zero failures. All five Node suites passed; JavaScript and Bash syntax checks passed.**.

Reproducible command:

```bash
cd /home/zayne/projects/pwaninet
bash scripts/test-sals03-wp2-local.sh
```

The script explicitly selects `tests.spark_memory.settings` and syncs disposable SQLite test schemas. It covers WP2, WP1, academic units, persistent memory, consent, original interaction lifecycle, conversation memory, gateway streaming and activity contracts, followed by five Node contract/renderer suites, JavaScript syntax, and PostgreSQL-script Bash syntax. PostgreSQL-only cases are skipped, not reported as passes.

Additional checks executed:

- `makemigrations pwanimate --check --dry-run`, using isolated SQLite settings and the real migration graph: no changes detected.
- Actual `0031`, `0032`, `0033` operations through disposable migration harnesses; fresh-interpreter teaching state/history reload using a SQLite backup.
- Injected answer-model failure followed by the existing HTTP retry endpoint: same run, saved original answer, no duplicate transition/question.
- `git diff --check`: clean.

Two existing django-axes configuration warnings remain nonfatal in the isolated settings. Deliberately denied requests generate expected 400/403/404 logs. No browser/device acceptance or live provider request was performed.

### Baseline for WP3

| Authored scenario | Controller/formatting response time | Model calls | Planner calls | Educational transitions |
| --- | ---: | ---: | ---: | ---: |
| Mathematics | 74.698 ms | 0 | 0 | 1 |
| Programming | 62.573 ms | 0 | 0 | 1 |
| Theory | 78.445 ms | 0 | 0 | 1 |

Measured in the final executed `test-sals03-wp2-local.sh` run (233 tests, 106.246 seconds for the Python suite).

These are single samples of local SQLite controller/formatting work, not medians, network response times, or student-facing end-to-end latency. Each authored scenario made zero model calls, zero planner calls and one educational transition. Generic-topic tests verify one existing answer-generation path using a mock; provider attempts and fallback latency were not measured. Runtime metadata therefore reports unknown actual model-call count for that path, rather than equating one logical generation with one provider attempt. No broad runtime optimization was implemented.

## H. Owner-run PostgreSQL handoff

Run these commands yourself on Ubuntu. The script reuses the existing isolated WP0 runner: a newly named, localhost-only pgvector container, random test credentials, tmpfs storage, a restricted disposable database role, isolated Django settings, guards and finally-cleanup. It never loads production settings or accepts a production database URL. Each phase creates and removes its own disposable environment. No command below was executed against PostgreSQL or Docker by the implementing agent.

```bash
cd /home/zayne/projects/pwaninet
export SALS_TEST_PYTHON="$PWD/.venv/bin/python"
if [ ! -x "$SALS_TEST_PYTHON" ]; then
  export SALS_TEST_PYTHON="$PWD/venv/bin/python"
fi

# Python dependencies and Docker availability
bash scripts/test-sals03-wp2-postgres.sh prerequisites

# Download the disposable pgvector image
bash scripts/test-sals03-wp2-postgres.sh prepare

# Real migration graph/schema, prior upgrade paths, and migration 0033
bash scripts/test-sals03-wp2-postgres.sh schema

# Teaching checkpoint/history persistence and original-run continuation
bash scripts/test-sals03-wp2-postgres.sh persistence

# Teaching evidence integrity, consent and owner/unit isolation
bash scripts/test-sals03-wp2-postgres.sh integrity

# Real row-lock races, duplicate claims/receipts, and hint-versus-grading order
bash scripts/test-sals03-wp2-postgres.sh concurrency

# WP1 enrollment/concept ownership and policy checks
bash scripts/test-sals03-wp2-postgres.sh ownership

# WP2, WP1, memory, academic unit, consent and existing Spark regressions
bash scripts/test-sals03-wp2-postgres.sh regressions

# Remove only disposable resources labelled sals.suite=sals03wp2
bash scripts/test-sals03-wp2-postgres.sh cleanup
```

New PostgreSQL cases are in `tests.sals_03_wp2.test_concurrency.PostgreSQLTeachingConcurrencyTests`: concurrent initial replay, duplicate answer claims, concurrent mode changes, and hint/grade ordering. WP1's existing concurrency suite also runs. The migration phase performs isolated schema creation/migrations inside the runner; do not substitute a production `manage.py migrate` command for it.

## I. Known limitations and deferred work

- PostgreSQL behavior, live browser layout/restoration, Celery with a real broker, and owner acceptance remain unverified here.
- The semantic teaching rubrics cover authored mathematics, recursion and photosynthesis exemplars. Arbitrary live LLM explanations still need human evaluation using the same operation/validity/meaning/goal rubric; prompt compliance is not a correctness guarantee.
- Formal verified grading remains the small reviewed WP1 determinant bank. Other numeric/options checks are formative. Unsupported prose/code responses remain inconclusive, without mastery credit.
- Concept resolution and unit attribution abstain on uncertainty; they do not synthesize a course ontology. Existing academic attribution limitations are inherited.
- Attachments retain the existing multimodal behavior; they do not enter the new persistent teaching controller. Selected documents use the existing grounded answer path, but live document tutoring was not browser-tested.
- The selector is attached to newly received teaching responses; no unrelated history/settings redesign was undertaken. Saved mode/history remain API-accessible after reload.
- DIAGNOSE consults recorded prerequisites. It does not invent prerequisite graphs or claim to infer understanding from a self-report.
- Benchmarks contain no live provider/network data. Provider-attempt instrumentation, latency distributions, broader recovery optimization and runtime performance work are deferred to WP3. The existing runtime was extended only where teaching continuation and answer preservation required it.

## J. WP2 acceptance checklist

PASS below means implemented and supported by executed local evidence. It does not substitute for pending PostgreSQL/browser/owner acceptance.

| Deliverable | Status | Evidence |
| --- | --- | --- |
| Audit and reuse existing runtime/session/assessment infrastructure | PASS | A; original-run executor regression |
| Six educational phases and flexible explicit transitions | PASS | `TeachingTests` progression, navigation and stop/quick cases |
| Persistent versioned checkpoints and replay-safe history | PASS | `TeachingPersistenceTests`; source/interaction receipt tests |
| Mathematical what/why/meaning/goal and zero-vector/component conditions | PASS for authored examples | `TeachingQualityTests`; actual KaTeX rendering of authored output |
| General live mathematical explanation quality | PARTIAL | Server policy implemented; live generated output/human rubric review pending |
| Prerequisite diagnosis without unsupported assertions | PASS | Missing-evidence and reviewed error-signature tests |
| Correct, incorrect, inconclusive and repeated-difficulty adaptation | PASS | Formative/verified result and harder reviewed question tests |
| Progressive hints and explicit full solutions | PASS | Four-level, partial-versus-full, assistance and direct-solution tests |
| QUICK/GUIDED/DEEP/PRACTICE and persistent mode changes | PASS | Service/API mode tests; theme-aware existing-component UI |
| One active question, explicit Send, removal and retained drafts | PASS | Python run tests and Node lifecycle/question/quiz contracts |
| Original-run continuation, cancellation and generation-failure retry | PASS | Real executor + existing retry endpoint regression; no duplicate history |
| WP1 alone controls mastery; hints cannot masquerade as independent work | PASS | Assistance timestamp/provenance tests and WP1 regression suite |
| No formal evidence from casual/unsupported/unrelated content | PASS | Attribution, unrelated-topic, greetings and formative-only tests |
| Enrollment, term, concept, session and user isolation | PASS | Owner/consent tests; WP1/0.2 ownership regressions |
| Concurrent DB locking/uniqueness behavior | NOT TESTED here | Four new PostgreSQL cases plus existing WP1 races handed off |
| Schema upgrade paths | PARTIAL | Actual migration-operation tests and no missing migrations |
| Representative quality rubrics across three subjects | PASS for authored exemplars | Algebraic equivalence, recursion and stoichiometry checks |
| Local latency/model/planner/transition baseline | PARTIAL | Local controller samples recorded; live provider latency unmeasured |
| Live browser/device and real-broker end-to-end behavior | NOT TESTED | Requires owner environment verification |
| Exact isolated PostgreSQL commands and cleanup | PASS as handoff | Actual script/module names, Bash syntax checked; execution pending |
| No WP3 or future work package implementation | PASS | Scope limited to teaching, required continuation integration and handoff |
| Overall WP2 completion/acceptance | PARTIAL | Local implementation verified; owner PostgreSQL/browser verification and acceptance pending |
