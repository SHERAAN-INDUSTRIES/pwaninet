# Spark SALS — founder summary

11 November 2026 · Spark in PwaniNet only · Audit of the local working tree, including existing uncommitted work. Deployment and database state were not checked. No SALS features were implemented.

## What Spark already does well

Spark already has persistent owned conversations, bounded history, academic/enrollment context, document upload and retrieval, citations, current-page viewer context, study sessions/checkpoints, stored roadmaps, quizzes/results, reports, memory controls, personal planning and resumable agent interactions. SALS can build on this foundation. Rebuilding an agent or document pipeline would duplicate useful work.

Evidence: `pwanimate/models.py`; `services/conversation.py`; `services/study_session.py`; `services/assessment.py`; `orchestrator/service.py`; `orchestrator/document_context.py`; `retrieval/services/document_retrieval.py`; `api/study_assistant.py`; `static/js/spark-study-sessions.js`.

## What is missing or unreliable

The important gap is **trustworthy evidence of learning**, rather than the ability to explain topics. Teaching strategies mostly come from prompts. Existing checkpoints can associate substantive student text with demonstrated concepts without validating correctness. Roadmap methods exist but are not wired to the inspected API/tools. There is no stable concept/objective evidence model, reliable spaced-review policy, full assignment submission workflow or structured source-backed mock-exam system.

Current assessment weaknesses must be fixed before showing mastery claims:

- One correct answer with four unanswered questions produces 100% “mastery.” This was confirmed by an isolated probe of the actual source method; the value measures accuracy among graded answers.
- LLM verdict parsing treats the string `"false"` as true. Free-text mathematics has no deterministic equivalence validator in the inspected assessment service.
- Parallel submissions can overwrite JSON outcomes; old assessments can re-credit a newly reset goal.

See `AssessmentService.submit_answer/_recompute_mastery` and `StudySessionService.set_target` for evidence. No live provider calls or database tests were needed for the probes.

## What must be resolved before SALS

Document access policies differ between retrieval paths. Executive roles/staff can currently read other users' non-AI private documents; whether that is intended needs an explicit product decision. Unit filters do not propagate consistently through hybrid metadata search. Memory defaults to enabled and searches transcript text across chats; this is not an individually approved personal-fact memory system.

Clarification questions already support sequential answers and explicit Send. Quiz MCQs currently submit immediately on click and display all unanswered questions. Reuse the interaction ideas, while preserving a separate authoritative grading contract. Also, study-mode turns bypass the initial multi-tool planner: adding tools alone will not integrate SALS into those sessions.

Evidence: `services/study_session.py:is_user_authorized_for_document`; retrieval/search adapters; `models.py:SparkMemoryPreference`; `services/conversation_memory.py`; `static/js/pwanimate/pwanimate-chat.js`; `static/js/spark-study-sessions.js`; `orchestrator/service.py:run`.

## Recommended architecture and first milestone

Keep one Spark agent and gateway. Add small optional Python capabilities around existing study/assessment services, backed by shared authorization and explicit consent. Introduce versioned concepts/objectives and durable answer evidence only where existing JSON/session fields cannot enforce the required guarantees. Derive progress from eligible evidence; keep personal memory, transcripts and academic records distinct. Disablement should pause capabilities and preserve readable records.

**First milestone:** harden the existing assessment flow—strict grade/key validation, atomic/idempotent answers, accurate score labels and objective scoping—alongside document-policy and memory-consent decisions. Then connect unit entry and approved roadmap, implement one concept loop, and demonstrate return/revision. Assignment management and automated mock exams can wait.

The first complete demo should show one unit, an approved small roadmap, teaching from notes, independent validated answers, explanatory feedback, saved evidence, leaving/reopening at the right point, and a revision assessment. Personal chat remains available without awarding academic credit.

## Most significant engineering risks

Incorrect model-generated keys/grades, inflated learning claims, inconsistent private-document access, default cross-chat memory consent, concurrent answer loss, process-local initial streaming, and incomplete long-book context. Existing durable continuation does not make all initial requests durable. No performance numbers or live test pass claims are available from this audit.

The working tree contains extensive pre-existing application changes and untracked migrations. Confirm the release baseline and applied schema before implementing additive changes. Existing tests cover many privacy/context/resume paths; dedicated assessment/score contract coverage was not found in the inspected Spark tests.

## Decisions needed

1. Should private non-AI documents be accessible to executive student roles/staff, and under which explicit policy?
2. What should personal recall and academic-continuity defaults be, and which memories require explicit approval?
3. What evidence qualifies a concept as demonstrated, and should current percentages be labeled accuracy rather than mastery?
4. Which question formats are acceptable for the first demo? Recommended: validated MCQ and narrow numeric formats.
5. May students create custom/unenrolled unit spaces? What happens to learning records when a conversation is deleted?
6. Approve the proposed MVP order and defer assignments/full mock exams until the core learning loop is trustworthy.

## Audit documents

All under `docs/spark/sals/audit/`:

- `SALS_EXISTING_SYSTEM_AUDIT.md` — inventory, lifecycle, five scenarios, evidence and ranked risks.
- `SALS_CAPABILITY_MATRIX.md` — teaching/assessment capabilities and all eleven proposed module evaluations.
- `SALS_ARCHITECTURE_RECOMMENDATION.md` — existing/proposed integration, contracts, flags and dependencies.
- `SALS_DATA_AND_MEMORY_AUDIT.md` — ownership, consent, records, retention and minimal data additions.
- `SALS_MVP_IMPLEMENTATION_PLAN.md` — dependencies, APIs, possible migrations, tests and acceptance gates.
- `SALS_EXECUTIVE_SUMMARY.md` — this summary.

Only these audit documents were created. Application code, prompts, migrations, dependencies and user data were left unchanged. Implementation requires explicit product-owner approval.
