# Learning evidence semantics v1

Approved: independent questions, reasoning, varied applications and later retention checks are required for mastery. MCQ/numerical formats are the initial certified formats. Values below are proposed engineering semantics; quantitative promotion thresholds require a separately versioned calibrated policy. Until that exists, no automatic retained-mastery promotion is enabled.

Existing evidence: `pwanimate/services/assessment.py:public_assessment/submit_answer/_recompute_mastery`; `services/study_session.py:recompute_progress/set_target/_sanitize_demonstrated_concepts`; `models.py:SparkAssessment/PwanimateStudyCheckpoint`; `services/study_report.py:session_stats`; `static/js/spark-study-sessions.js:renderQuestion`.

## Authoritative definitions

| Value/state | Definition |
|---|---|
| Total N | Number of validated, published questions in an immutable assessment version; N >= 1 |
| Answered A | Distinct questions with an accepted durable submission, including pending/inconclusive; malformed rejected requests are not submissions |
| Graded G | Questions whose current authoritative evaluation is decisively correct or incorrect |
| Correct C / Incorrect W | Decisive outcomes; G = C + W |
| Unanswered U | N − A |
| Pending P | Accepted submissions with no terminal evaluation yet |
| Inconclusive I | Terminal evaluation could not establish correctness; distinct from W |
| Completion | 100 × A / N; submission completion, not correctness or successful grading |
| Grading completion | 100 × G / N; meaningful independently from submission completion |
| Accuracy | 100 × C / G when G > 0, otherwise null; always display denominator |
| Assessment coverage | Completed planned assessment items/requirements, with specified denominator and version; never question accuracy |
| Concept evidence | Owner/objective/concept-version-linked independent submission plus validated evaluation, item variant/source/key/grader provenance and assistance conditions |
| Insufficient evidence | Missing trusted keys, stable concept identity, breadth, reasoning, independence or evidence volume; not a failure score |
| Initial demonstrated understanding | Meets a versioned concept policy for independent, varied, validated outcomes and reasoning evidence; retention not yet established |
| Retained mastery | Initial understanding plus later independent retention evidence under a versioned policy; never inferred from recap or immediate repeated item |
| Inconclusive grading | Failure/unsupported/ambiguous evaluation, no certified correctness and no penalty as wrong; retain reason and retry identity |

Partition: N = U + P + I + C + W; A = P + I + C + W. Counts are distinct questions/current authoritative evaluations, not number of HTTP requests or evaluation retries. Round percentages for display only, keep integer numerators/denominators authoritative. Item completion status can be submitted while grading status remains pending/inconclusive.

Examples: five items with one correct and four unanswered → A=1,G=1,C=1,U=4, completion=20%, accuracy=100% (1/1), mastery=insufficient_evidence. No answers → completion=0%, accuracy=null. Five submitted with three correct/one wrong/one inconclusive → completion=100%, accuracy=75% (3/4), grading completion=80%; inconclusive=1, retained mastery unavailable. A wholly invalid generation has no published assessment and no scores, not N=0 divided by zero.

## Evidence exclusions and transitions

Prompt demonstrations, roadmap done flags, summaries, passive agreement and a substantive but unvalidated explanation are observations only. `_sanitize_demonstrated_concepts` currently checks message provenance/length and can substitute another substantive message; this cannot certify correctness. The existing mean of roadmap coverage and assessment accuracy is legacy progress, not mastery.

Practice outcomes count toward practice completion/accuracy but not automatic mastery. Explicit later retention assessments are distinct from practice and must be designated before delivery. Repeated question aliases or hints/revealed answers cannot count as new independent evidence. A numeric final answer alone does not prove reasoning: use separately validated reasoning checkpoints/step items where available; otherwise remain insufficient. No arbitrary free-text reasoning certification in Phase 1.

Goal replacement creates a new objective version, not mutation of old evidence scope. Historic evidence remains inspectable; new projection only uses that version. An in-flight old-goal answer can finish as historical evidence, but must never update the new active projection. Legacy items without verified key/concept/version remain legacy_uncertified, even if previously labeled 100% mastery.

Proposed mastery API state enum: insufficient_evidence, practising, demonstrated_initial, retained. Attach policy_version and missing_requirements; disabled projection reports evaluation_paused separately, not a new learning state. Policy thresholds are not invented in this design; current Phase 1 outputs default to insufficient_evidence/practising unless a reviewed policy explicitly supports more. No model can set the enum directly.

Contract assertions are specified, not executed, in `../tests/ACCEPTANCE_TEST_MATRIX.md` (P01–P10). Separate score schema: `../phase1/EVIDENCE_AND_PROGRESS_SCHEMA.md`.
