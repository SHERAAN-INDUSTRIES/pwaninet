# WP1 database-independent validation subset — 11 November 2026

Implemented without migrations, model changes, transaction refactoring, memory/document policy changes or new orchestration. Existing work preserved; no push/deploy. This is not full WP1 or production readiness.

## Changes

- pwanimate/assessment_validation.py: database-free strict JSON/boolean verdict parser; MCQ count/text/option/key/ID validation; bounded Fraction-based decimal/rational/scientific parser; explicit absolute/relative tolerances; outcomes correct/incorrect/invalid/inconclusive/evaluation_failed; separate metrics.
- pwanimate/services/assessment.py: reject partial malformed generation; no free-text LLM grading fallback; deterministic supported grading; unverified keys inconclusive, invalid inputs do not write; no mutation of historical mastery fields; additive public metrics/legacy labels and key-safe responses.
- pwanimate/api/study_assistant.py: preserve numeric zero at answer boundary, reject non-scalar/bool as empty invalid input.
- pwanimate/services/study_report.py: label stored percentages legacy/uncertified, expose insufficient evidence state.
- static/js/spark-study-sessions.js: neutral ungraded feedback, invalid response stays actionable, completion/accuracy denominator display and legacy score labels.
- tests/sals_wp1/__init__.py, test_validation.py, test_service.py, run_offline.py: deterministic regression suite and WP0-guarded database-free service runner.

## Semantics and limits

Reference answers are trusted only through a server-owned deterministic reference definition with matching exact prompt and recomputed expected value. Initial reference set contains one diagonal determinant item (2*3*4=24); this is intentionally narrow. Unknown/model-authored keys, including structurally valid keys, never award correctness. Matching a model-supplied reference ID alone is insufficient. Extend reference coverage through reviewed/computed definitions, not another model verdict. Generated non-MCQ questions must have supported numerical answers; arbitrary free-text generation is rejected rather than silently certified.

All newly generated quizzes are excluded from automatic mastery. Accurate deterministic feedback is not retained mastery. Existing JSON/IDs/history and legacy percentage values remain untouched; new metrics include completion and graded accuracy with null accuracy when no decisive grades. One correct/four unanswered yields completion20, accuracy100(1/1), mastery insufficient_evidence. Existing answered field retains legacy decisive-count semantics; new metrics.answered also counts submitted inconclusive answers. Strict parser rejects string/number/null verdicts; current submit service does not call an LLM grader at all.

No trusted general item bank, arbitrary symbolic validation, free-text reasoning certification, calibrated mastery, goal-version isolation, immutable evidence, idempotency or concurrency corrections are claimed. Those remain separately authorized work. Existing JSON persistence race conditions are unchanged. Legacy unverified historical correct/incorrect values remain readable and clearly uncertified, never backfilled as learning evidence.

## Actual verification

`.venv/bin/python -m tests.sals_wp1.run_offline`: 29 passed (12 pure validation, 4 fake-persistence service, 4 isolation, 9 gateway contract). No application DB connection or live provider call. Tests cover malformed verdicts/duplicate JSON keys, missing/duplicate questions/options, wrong verified keys, unknown selections, numeric equivalence/tolerance boundaries/unsafe inputs, partial/empty/inconclusive metrics and service non-credit behavior.

`node tests/spark/interaction-flow.cjs`: passed existing clarification regression.

`node --check static/js/spark-study-sessions.js`, Python compilation and git diff --check: passed.

Initial plain unittest import failed because services package eagerly imports Django; the pure module was moved outside that package, then pure/offline tests passed. No broad package refactor was made.

Full WP0 PostgreSQL runner attempted in this agent session: blocked by Docker socket permission. Its earlier successful user-local 26-test run remains valid as recorded in WP0, but does not establish DB integration of this subset. No PostgreSQL-backed assessment/API test or real-browser visual test was executed here. Run such integration checks later in an authorized isolated environment before release. Do not begin evidence/transaction changes without separate authorization.

## Validation checkpoint — 11 November 2026

Verdict: **CONDITIONAL validation checkpoint; not release-ready.** This section supersedes the earlier 29-test count, but does not claim the full gateway suite or PostgreSQL integration passed. No application logic changed in this checkpoint. Added tests/sals_wp1/test_api_checkpoint.py and included it in run_offline.py; updated this report only.

### Executed results

- Final `.venv/bin/python -m tests.sals_wp1.run_offline`: **35 tests passed**. Six added API checkpoint tests exercise the real AssessmentView.post → AssessmentService → public_assessment path, with session lookup/assessment queryset and persistence mocked. They check numeric zero as incorrect, invalid unsafe/non-scalar/bool input without save, inconclusive unverified keys, evaluation_failed reference mismatch, readable legacy partial history, and generation count failure returning400 without persistence. Authenticated identity is supplied with APIRequestFactory/force_authenticate and the assessment owner/session filter is asserted; this is not proof of real DB ownership enforcement or full middleware/auth behavior.
- Expanded offline run replacing the gateway contract-only selection with the entire test_gateway module: **81 tests run, 80 passed, 1 failed** (performed before the sixth API case was added). Existing test_gemini_successful_generation expects gemini-2.5-flash but provider normalization returns gemini-3.8-flash. This same mismatch predates this checkpoint. No production model configuration or test expectation was changed to hide it.
- `node tests/spark/interaction-flow.cjs`: passed clarification interaction regression. This exercises clarification controls/shared continuation, **not the assessment quiz DOM**.
- `node --check static/js/spark-study-sessions.js` and `git diff --check`: passed.
- `.venv/bin/python -m tests.sals_wp0.run`: failed before container startup, Docker socket permission denied. No PostgreSQL assessment tests ran here. The prior successful user-local WP0 run remains foundation evidence only; WP0's current test selection does not include new WP1 tests and cannot certify them without explicitly selecting them in a future isolated integration run.

### Credit and compatibility inspection

Supported wrong numerical/MCQ answers produce correct=false; invalid input returns correct=null without saving; unverified references produce inconclusive with no decisive grade; reference corruption produces evaluation_failed with correct=null. These paths never increment correct count and never invoke the model grader. Existing session percentages remain unchanged; response mastery_state is insufficient_evidence. Malformed verdict parsing tests reject string booleans and nonfinite/malformed JSON. Public output strips private keys. Historical valid boolean results remain readable as legacy_uncertified; malformed historical string booleans are not treated as decisive grades.

The inspected quiz and Markdown report label historical percentages legacy/uncertified, and immediate answer feedback displays separate completion/accuracy denominator and insufficient mastery evidence. Report accuracy uses decisive legacy answered count, which is internally consistent but does not represent submission completion. No blanket claim about every older chat/template surface is established by this focused inspection.

### Uncovered edge cases and remaining limitations

1. **Pending quiz requests:** renderQuestion has no in-flight submission guard; controls disable only after a successful result. Rapid MCQ clicks can still send concurrent answers. JSON persistence races/idempotency remain explicitly deferred. Clarification-flow tests do not cover this quiz behavior.
2. **Inconclusive reload:** public_question exposes submitted/outcome/feedback, but quiz filters by answered (decisive only), reconstructs fresh controls and does not restore inconclusive feedback. Immediate inconclusive response disables controls until reload; retry UX is inconsistent. No credit leakage was found from this, but usability is incomplete.
3. **History/aggregate presentation:** quiz history shows an unlabeled correct/answered/total tuple; metrics.completion is not displayed there. Session/report aggregate progress remains a labeled historical percentage, not a recomputed completion meter. A partially answered assessment's live response is accurate, but dashboard/history is incomplete.
4. **Practice classification:** generation sets counts_toward_mastery=False for every new assessment as a safe no-certification guard; current UI interprets that flag as practice. Noncertification and practice intent need a distinct presentation contract later; no new schema was introduced here.
5. **Option aliases:** grade accepts labels, option IDs and one-based indices. Colliding numeric labels/IDs can make an otherwise selected valid option invalid through ambiguous aliases. Fail-closed, but not a complete v2 ID-based protocol.
6. **Validation schema:** count/options/text/keys/IDs are checked, but unknown question fields are not rejected. Mode coercion still assumes text; malformed nonstring mode can raise outside the intended400 path. Typed versioned request schemas remain incomplete.
7. **Reference coverage:** exactly one deterministic reference currently certifies correctness. Other valid generated items remain unverified/inconclusive; a broad usable validated curriculum is not implemented.
8. **Evidence semantics:** schema-free outcomes cannot certify goal-version independence or retained understanding. Historical legacy scores may already be inaccurate; the subset labels them without rewriting them. Counts, completion and accuracy are feedback, never evidence of retained mastery.

Database-dependent follow-ups: actual authenticated API queries/save/reload, five-item decisive/invalid/inconclusive integration, source migration compatibility with legacy fixtures, goal-change contamination and concurrent submission tests. Immutable evidence/idempotency/transaction changes require separate authorization; no such implementation was started. Real-browser quiz/report behavior, themes/mobile and assessment request races remain unverified. Full gateway offline failure must also be resolved or explicitly scoped before a release pass.
