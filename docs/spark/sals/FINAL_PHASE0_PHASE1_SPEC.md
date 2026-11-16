# Final SALS Phase 0/1 engineering specification

11 November 2026 · PwaniNet Spark only · Documentation/design handoff; no implementation authorization. Final planning verdict: **CONDITIONALLY READY**. Source observations are distinguished from proposed contracts throughout. Runtime deployment and automated-test success are not established.

## Input reconciliation and authority

All six audit documents under `audit/`, five Phase 0 documents under `phase0/`, four Phase 1 designs under `phase1/` and four test specifications under `tests/` are present. The segmented prompts were accumulated in order; those phase documents were created before this reconciliation. No absent document was silently treated as existing. Approved product policies override the audit's earlier open policy questions. This file resolves scope/contract conflicts; detailed validation and transaction documents define implementation invariants, and ARCHITECTURE_DECISION_RECORDS.md records rationale.

## Shared architectural direction

Extend existing `pwanimate/services/assessment.py:AssessmentService` and `services/study_session.py:StudySessionService`; retain PwanimateConversation, SparkAssessment, document/context/gateway infrastructure and Django frontend. `orchestrator/service.py:PwanimateOrchestrator.run` excludes study/selected-resource turns from initial StudyAgent admission; Phase 1 uses typed service/API actions and study context, not a parallel planner. Existing ToolRegistry may expose those services later without becoming a second agent. No Workspaces, provider/dependency replacement, generic event platform or microservices.

Configure capabilities server-side for assessment writes, private sharing, personal facts and mastery certification. Recheck in API/service/job and pending action finalization. Disabling new work leaves authorized history available. Authentication, ownership, consent, evidence isolation and source policy are mandatory shared invariants, never optional flags.

## Normative policies

1. Private documents: owner or explicit active grant; role alone grants no private read. Apply same action-specific policy before search ranking/counts and at actual file delivery. Enrolled restricted academic resources remain a separate explicit entitlement. Unit/category relevance is intersected after authorization, never a permission substitute.
2. Personal memory: globally off; each candidate fact requires approval before use. Legacy unversioned enabled=True does not establish consent. Until full approval feature ships, extraction and personal/cross-chat recall paths remain off/quarantined; active owned conversation context and structured academic continuity remain usable. No blanket opt-in can authorize unapproved facts via raw recall.
3. Mastery: independence, reasoning, varied applications and later retention. Phase 1 supplies accurate outcomes/counts; automatic demonstrated/retained promotion remains off until a reviewed versioned policy can validate those requirements. No invented numeric threshold or retention interval.
4. Initial certified formats: validated MCQ and bounded numerical literals/rationals only. Valid schema does not validate a mathematical key; keys require reviewed/computed provenance. Unsupported free text is not silently certified.
5. Unit scope: existing enrolled AcademicUnit plus private owner-scoped custom UUID/label. No tenant/catalog redesign; labels never authorize another student's space or document.
6. Academic records: independent of conversation lifetime, separate owner export/delete. Transcript deletion detaches source links and removes recall; it does not erase outcomes or copy personal text into academic records.

## Defect-to-resolution trace

| Existing defect/evidence | Required resolution | Acceptance |
|---|---|---|
| bool(graded.get('correct')) | Strict boolean parser; invalid→inconclusive/no credit | V01–V04 |
| Incomplete assessed accuracy labeled mastery | Completion/accuracy/counts separate; insufficient evidence | P01–P06 |
| Unvalidated question count/options/key | Atomic draft publication, exact count, verified reference | V05–V08 |
| MCQ unknown selection→LLM | Deterministic ID comparison; invalid request rejected | V09 |
| Arbitrary free-text/numeric grading | Bounded parser/comparator; unsupported refused; zero preserved | V10–V17 |
| JSON concurrent overwrite/duplicates | Normalized immutable submission/evaluation, unique keys/claim/fencing | C01–C08,C12 |
| Goal reset reuses historical assessments | Immutable objective version plus active pointer; historical finalize only | C09–C10,P08–P09 |
| Chat CASCADE removes academic progress | Nullable SET_NULL session link plus detached reader/deletion changes | E07–E09,M02 |
| Elevated private access/inconsistent filters | Shared policy and protected asset delivery, file membership validation | D01–D08 |
| Enabled-by-default unapproved recall | Consent revision/cutoff/quarantine and job failure-path protection | E01,E04–E05,E10 |
| Legacy truthy UI/keys exposure risk | Explicit v2 negotiation, allowlist serialization and nullable outcome handling | V18,K06,B01–B03 |

Root evidence is in the existing named services/models, current API AssessmentView and `static/js/spark-study-sessions.js`. Appendix audit records detailed risk probabilities without invented measurements.

## Data, transactions and compatibility

Required logical additions: LearningObjectiveVersion, AssessmentQuestion, AssessmentSubmission and AssessmentEvaluation. Extend existing assessment with nullable version/certification/generation identity, and session with active objective/private scope and nullable conversation SET_NULL. Stable concepts can remain bounded immutable JSON per objective; projection computed from outcomes initially. This is four narrow new records, not a second Quiz model. Add grants/facts only when their future capabilities ship.

First durable accepted answer wins per question/version/attempt. Actor/operation idempotency key+canonical hash gives safe replay, conflicting payload409. Resolve owner through session/assessment/question; do not trust model/client user IDs. Expensive work outside locks. Short admission/finalization locks follow session→objective→assessment→question→submission, with unique constraints and execution token. Durable pending rows support recovery if enqueue fails. Historical old-goal finalization never updates active new goal. Delete tombstones and consent revisions fence delayed jobs. Events/projections deduplicate by evaluation identity; no independently scored JSON mirror.

New score schema has N,A,G,C,W,U,P,I and null accuracy if G=0. N=U+P+I+C+W. Completion=A/N; accuracy=C/G; inconclusive not wrong. Assessment complete may mean all submitted while grading remains unresolved: display grading completion separately. Practice contributes only practice statistics. Legacy mastery fields remain explicitly legacy, never used by new projection.

Keep existing API route/actions and v1 reads. V2 write negotiation requires content/objective versions, typed answers and Idempotency-Key. V1 unsafe/unsupported writes receive explicit error; no fake false or zero for unknown. Update UI consumers before enabling v2 cohort writes. Legacy rows remain uncertified; never backfill mastery from summaries or percentage values.

## Review challenges and resolved scope

No new Concept graph, Projection table, generic durable-job table, orchestration engine, vector store or standalone learning-space model is required for Phase 1. Existing session is learning container. Question normalization is necessary for durable per-question uniqueness; keeping mutable answers inside one JSON array would retain the central race. Session CASCADE change is mandatory before independent retention, not optional cleanup. Export/delete basics cannot be deferred past launch of preserved academic data, even if rich settings UI is later. Explicit sharing and personal-fact approval may be deferred only with capabilities off and legacy access/recall secured. Current support-role reads are intentionally removed, not grandfathered.

Remaining technical choices: final model/constraint implementation and migration names after baseline resolution; calibration of mastery policy; operations backup/purge/URL revocation guarantees. None licenses implementation in this task. Work packages and exact prerequisites are in IMPLEMENTATION_WORK_PACKAGES.md and IMPLEMENTATION_READINESS_REPORT.md.
