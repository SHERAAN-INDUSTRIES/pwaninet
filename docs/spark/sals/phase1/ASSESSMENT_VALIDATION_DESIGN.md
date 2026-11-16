# Phase 1 assessment validation design

Design only. Sources re-read: `pwanimate/services/assessment.py`, `api/study_assistant.py:AssessmentView`, `models.py:SparkAssessment`, migrations 0023–0025, `static/js/spark-study-sessions.js:renderQuestion`. Policy definitions: `../phase0/LEARNING_EVIDENCE_CONTRACT.md`. Proposed changes extend those services; none is implemented.

## Confirmed roots

`generate` slices/skips malformed items, permits fewer questions than requested and blank keys, loosely accepts options and assigns random IDs without a versioned publication contract. `submit_answer` accepts option text/index/letter, falls back to LLM on invalid MCQ selection, coerces verdict with bool, replaces the JSON list and recomputes accuracy as mastery. `_parse_json_object` salvages outer braces; this is inappropriate for certified verdict ingestion. The answer input uses `str(value or '')`, so JSON numeric zero is converted to empty text at the API boundary. Require typed input validation before truthiness or conversion.

## Publication contract v2

Validate request count as exact integer (bool is invalid), 1–10 matching existing range; reject invalid values rather than silently clamp. Explicit question type mcq or numerical is separate from legacy mode immediate/deferred/mcq. Deferred means feedback remains withheld until assessment submission is complete; legacy deferred is not proof of enforcement. Published content is immutable and bound to content version/hash and objective version. Generate drafts with a schema_version and expected_count; exact usable count required. Failed/truncated batches remain drafts/quarantined and return generation_invalid; no partially published scored test.

MCQ fields: server-issued qid unique per version; nonblank prompt; exactly four options for initial release, each {id,label}; IDs bounded stable ASCII tokens unique within question, labels normalized for duplicate detection; exactly one correct_option_id present. Reject duplicate labels/IDs, unknown keys, nonstring fields, empty explanations where feedback requires one, or a key outside options. Never accept client answer keys. Public payload includes option IDs/labels, no correct ID/key/verifier evidence.

Key validation has two independent layers: structural validity and mathematical/reference validity. Certified items require a trusted reviewed reference or deterministic computed template with typed operands and versioned verifier. For determinant/inverse examples, verifier derives the expected result from validated template inputs, not from student text or LLM agreement. A human-reviewed item bank records reviewer/version. LLM-only generated keys, another LLM agreeing, or citations alone are insufficient: mark uncertified practice/draft until validated. Invalid keys cannot create scored evidence. Phase 1 need not build an unrestricted mathematical verifier.

## Numerical contract

Initial formats: bounded signed decimal/scientific literal or rational integer/integer string, parsed with Decimal/Fraction semantics, never float/eval. Proposed limits: raw trimmed input <=128 characters, integer/digit count <=64, exponent magnitude <=100, finite absolute magnitude <=10^100; zero denominator invalid. These are engineering safety defaults, not pedagogical thresholds. Validate both expected and submitted values against the same grammar/limits. Reject bool, null, NaN/Infinity/-Infinity, duplicate JSON keys, locale comma notation, function calls, units embedded in numeric strings, unbounded exponents and arbitrary expression syntax. Optional unit is a separate predefined enum; initial questions should be dimensionless unless a reviewed unit map is supplied. No implicit unit conversion.

Supported comparators: exact rational for exact questions; decimal tolerance for approximate questions. Persist absolute_tolerance >=0, relative_tolerance >=0 and comparator_version with the key; both finite bounded decimals. Exact uses zero tolerances. Approximate passes when |x−expected| <= max(abs_tol, rel_tol×|expected|), inclusive; use declared decimal precision and round only for display. Define zero expected value via abs_tol, never division by zero. No inferred tolerance from an answer's number of digits. Values mathematically equivalent (1/2, 0.5, 5e-1) compare equal when grammar allows them. Invalid syntax is rejected before durable answer admission; comparator/resource failure after admission becomes inconclusive.

## Verdict and unsupported formats

For any optional explanatory model output, strict whole-document JSON only; reject duplicate keys, extra unexpected fields and nonfinite constants. Required correct is exactly boolean (`type(value) is bool`), never truthiness. true/false accepted; strings true/false, 0/1, null, missing, malformed or prose-wrapped JSON rejected. Invalid output creates inconclusive evaluation with correct=null and reason_code, no credit and no wrong-answer penalty. Record evaluator/version and failure code, not raw provider error secrets.

MCQ compares selected_option_id to the validated key deterministically; unknown ID or ambiguous legacy text/index mapping returns validation error, no model fallback. Numerical grading uses only the declared comparator and verified key. Free text/voice transcripts/images are not certified answer formats in Phase 1. Return unsupported_answer_type for new certified submissions; old free-text results stay readable as legacy_uncertified. Explanatory LLM feedback cannot override deterministic correctness or mastery state. No DB lock spans any provider call.

## API and acceptance

Keep AssessmentView route; proposed explicit contract_version=2 negotiation prevents current UI treating null verdict as wrong. New answer accepts typed answer {type,selected_option_id} or {type,value} (value string, including '0'); goal/content versions and idempotency header required. Legacy adapter maps existing string answer only to unambiguous supported types, cannot certify old keys. Public allowlist serializers must strip keys from API, tool/activity events, errors and exports of active tests.

Acceptance IDs V01–V18 cover all validation negatives and mathematical boundaries in `../tests/ACCEPTANCE_TEST_MATRIX.md`. No validator code, test or prompt was changed.
