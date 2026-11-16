# Atomic and idempotent submission design

Source: `AssessmentService.submit_answer` currently read-modify-writes SparkAssessment.questions without row locks; `AssessmentView` resolves user/session/assessment ownership but has no request idempotency/version contract. Existing run claim/journal/attempt patterns in `services/agent_execution.py`, `agent_journal.py`, `tasks/agent_runs.py` demonstrate short locks and retry fencing; reuse patterns, not run rows as assessment evidence.

## Proposed minimum durable objects

Use existing SparkAssessment as container, immutable published AssessmentQuestion rows per version, AssessmentSubmission (immutable accepted payload) and AssessmentEvaluation (append-only terminal outcomes). AssessmentSubmission contains unique owner/request key, canonical payload hash, question/version/objective, received time and captured policy/assistance context. Mutable evaluation lease/attempt fields may live on this row explicitly separated from its immutable answer fields; a separate generic job table is unnecessary. Unique(question_version, attempt_scope) admits one answer for the initial assessment attempt. A retake is a new assessment/attempt, never overwriting a scored answer. Evaluation has unique(submission, evaluation_revision) plus unique execution token; exactly one authoritative revision selected under lock. No automatic regrading changes past reported outcomes without a new audited revision.

Normalization is justified: JSON alone cannot enforce per-question uniqueness, concurrent independent updates or stable evidence after chat deletion. Do not normalize unrelated roadmap/report documents just for consistency.

## HTTP identity

Require Idempotency-Key for v2 writes, opaque ASCII 16–128 chars, scoped to actor + operation namespace (generate or submit). No key logging. Hash canonical validated payload including assessment/question IDs, contract/content/objective versions, normalized answer and intent. Same key/same hash returns same submission and current canonical result; same key/different hash → 409 idempotency_conflict. Client retry uses the same key. Deterministic canonicalization must not collapse distinct answers or change after first admission. Store accepted-request identity for the life of evidence; deletion retains only minimal purpose-specific suppression tokens as needed, not recoverable answer text. A distinct key for already-reserved question returns 409 question_already_submitted with owner-safe result location. Do not silently accept a competing answer.

Generation also reserves actor/request identity and objective version before provider work. Same key returns the reserved draft/published assessment; invalid generation is not retried under a new invisible operation. Explicit regeneration gets a new key. Reserve/finalize design prevents duplicate successful publication, though a network timeout can cause repeated external billing; no exactly-once-provider claim.

## Lock order and transaction boundaries

Global order for related writes: owned study session → objective version → assessment → question → submission/projection. Goal replacement locks the same session and active-objective pointer first. Lock only rows actually needed; a per-assessment lock briefly serializes admissions, not model latency. PostgreSQL unique constraints remain final backstops; handle IntegrityError in a savepoint then reload, never continue in a broken transaction.

1. Authenticate and resolve session AND assessment/question owner via relationships; return generic 404 on cross-user/mismatched scope. Parse grammar before reservation; recheck relevant feature/privacy permissions inside admission transaction.
2. Admission atomic block: check live objective/content version, assessment open and key validity; replay an existing idempotency identity before rejecting an already completed assessment. Reserve submission under question uniqueness, immutable content and owner. Commit; emit enqueue via on_commit.
3. Evaluate outside locks from immutable validated question snapshot. Initial MCQ/numeric paths are deterministic and can execute immediately; lease token/expiry supports recoverable worker evaluation without a second algorithm. Optional LLM explanation never changes certified result.
4. Finalization atomic block in the same lock order: verify attempt token, deletion generation and source/objective/version policy. Append one terminal evaluation, mark lease finished, recompute counts/projection from durable outcomes and update current compatible snapshot. Never merge another stale whole-JSON list. Commit before acknowledging success.
5. Return 201 decisive initial result or 202 pending with submission ID and owner-scoped status URL; 200 replay with canonical latest state/replayed=true. Malformed requests →400, ownership→404, stale versions/competing answers→409, disabled capability→403 with stable code. Contract negotiation determines response shape. No raw provider text in failures.

## Failure and race outcomes

| Trigger | Required result |
|---|---|
| Same key/payload concurrently | Unique identity wins once; both callers resolve same submission/result |
| Two questions concurrently | Both reservations/results survive; totals derived from rows, no lost update |
| Competing answers one question | First durable reservation wins; second gets 409; no scoring both |
| HTTP timeout after commit | Retry same key finds saved result; never requires a new attempt |
| Worker dies before admission commit | No accepted row; same request can safely reserve |
| Dies after admission / before finalization | Pending row recovered after lease expiry; new fenced execution; no duplicate evidence |
| Dies after finalization commit / before ack | Redelivery finds terminal authoritative result; no second evaluation/event effect |
| Old worker returns after lease reclaim | Old attempt token cannot finalize |
| Stale content/goal before admission | 409; no accepted submission |
| Goal replaced after admission | Outcome saved only under old objective; eligibility_for_active_goal=false; new goal projection untouched |
| Academic deletion while pending | Tombstone/generation recheck blocks finalization and retries from recreating evidence |
| Capability disabled while pending | Finalization records suspended/inconclusive reason or retains pending without credit per declared cancellation policy; no new certified credit; read/replay still works |

Historical goal finalization is permitted only for already accepted answers; goal change cannot create new admissions to stale tests. Source revocation during work stops new source reads; existing minimal lawful assessment evidence stays historical without resurfacing private source text. If key validity is revoked, mark evaluation ineligible rather than silently retaining certified credit.

## Recovery and events

Pending rows are the durable recovery queue; on_commit dispatch is an optimization. Periodic recovery scans pending/lease-expired submissions; broker enqueue failure must not leave them undiscoverable. Existing scheduler deployment is unverified: prove recovery runner availability. Domain event IDs derive from evaluation ID/revision; consumers enforce unique applied event identity. Recompute projections from evidence under scope lock, so duplicate/reordered events are harmless. No new broker/event platform required; outbox only if a required external delivery cannot be recovered from these rows.

Tests C01–C13 require real isolated PostgreSQL concurrent transactions/barriers, not sequential mock calls; see test isolation specification. No implementation was performed.
