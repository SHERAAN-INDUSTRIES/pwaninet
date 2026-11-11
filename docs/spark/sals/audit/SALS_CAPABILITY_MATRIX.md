# SALS capability matrix

11 November 2026 · PwaniNet working tree · Source verification only unless marked “probe.” No live tests or model calls. See [inventory](SALS_EXISTING_SYSTEM_AUDIT.md) for scope and evidence limits.

Status: **Implemented and verified** = inspected executable logic establishes the narrow capability, not a deployed/runtime guarantee. **Partially implemented** = supporting application functionality with meaningful gaps. **Prompt-dependent only** = instruction/model behavior without a persistent mechanism. **Not implemented** = not found in inspected Spark/academic/service/UI scope. **Unable to determine** = infrastructure or model outcome cannot be established statically.

## Existing platform and teaching capabilities

| Capability | Status | Evidence | Limitation and action |
|---|---|---|---|
| Persistent owned conversation and messages | Implemented and verified (source) | `pwanimate/models.py:PwanimateConversation/PwanimateMessage`; `services/conversation.py:get_owned_conversation` | Runtime DB state unverified; preserve identifiers/history |
| Server history and checkpoint resume | Implemented and verified (source) | `ConversationService.load_history`; `StudySessionService.build_study_orchestration_context` | Bounded excerpts can omit relevant old turns; expose limits |
| Cross-conversation semantic/lexical recall | Partially implemented | `services/conversation_memory.py:search/_lexical_fallback` | Owner/reset filters present; personal/academic scoping absent |
| Conversation summaries/digests | Implemented and verified (source) | `services/study_memory.py:refresh/welcome` | Model-generated continuity, not confirmed understanding |
| Preferences and customization | Implemented and verified (source) | `models.py:PwanimatePreferences`; `services/preferences.py` | Explicit profile settings, not automatically approved fact memory |
| Approved personal facts: create/update/delete individually | Not implemented | Inspected memory models/services and MemoryView | Global digest controls do not implement fact-level consent/editing |
| Memory on/off and forgetting | Implemented and verified (source) | `api/study_assistant.py:MemoryView`; `StudyMemoryService.forget` | Defaults enabled; forget deletes digests/vectors, keeps transcript and learning records |
| Expiration/retention policy for memory | Not implemented | MemoryPreference/Digest/Embedding definitions | reset_at is cutoff, not TTL; product retention decision required |
| Academic context and enrollments | Partially implemented | `context/user_context.py:UserContextService`; `documents/academic/models.py` | Legacy/canonical catalogs, shared units, ambiguous topic-to-unit mapping |
| Document upload, preview and background ingestion | Implemented and verified (source) | `documents/tasks/processing.py`; `pwanimate/tasks/ingestion.py` | Deployment/scanned-document completeness unverified |
| Chunking/embeddings/vector search | Implemented and verified (source) | `ingestion/chunker.py`; `tasks/embeddings.py`; `retrieval/services/document_retrieval.py` | Retain active-model/version filtering; benchmark large books |
| Keyword/hybrid retrieval | Partially implemented | `UnifiedRetrievalService`; `pwaninetSearchAdapter.search_documents_lexical` | Domain filters inconsistent between paths; repair before unit exam claims |
| Viewer-to-Spark exact-page context | Implemented and verified (source) | `orchestrator/document_context.py:DocumentTurnContext`; `get_page_chunks` | UI runtime coverage not tested; preserve IDs/version/page/file provenance |
| Citations and provenance | Implemented and verified (source) | `DocumentSemanticRetrievalService.generate_citation`; `PwanimateMessage.citations/sources` | Provenance does not establish source truth |
| Whole-book summary quality | Unable to determine | `get_document_overview_chunks` caps initial pool at 120 | Do not describe sampling as full-book reading |
| Step-by-step teaching/worked examples/math procedures | Prompt-dependent only | `orchestrator/prompts.py:SYSTEM_INSTRUCTION_BASE`, study addenda | No deterministic explanation correctness checker |
| Prerequisite breakdown | Prompt-dependent only | Teaching/adaptive addenda | No persisted prerequisite graph or versioned concept roadmap |
| Adjust explanation difficulty | Prompt-dependent only | Personalization and adaptive teaching addenda | No explicit calibrated learner policy |
| Teach from uploaded material | Partially implemented | `_run_rag`, ContextEngine, selected resources | Retrieval explicit; pedagogical fidelity model-dependent |
| Questions/checks during teaching | Prompt-dependent only | Study prompt addenda | Clarification UI exists but is not proof of understanding |
| Detect confusion/change strategy | Prompt-dependent only | Adaptive study instructions | No validated confusion/repeated-error classifier |
| Repeated mistake tracking | Partially implemented | Checkpoint.misconceptions; SparkAssessment questions | Free-form observations/results, no stable concept error taxonomy |
| Ask for reasoning; distinguish recall from understanding | Prompt-dependent only | Adaptive teaching prompts | Need rubric/evidence evaluation before persistence as mastery |
| Recommend prerequisites or decide advance/revisit | Prompt-dependent only | Study prompt/checkpoint.recommended_next_step | No evidence-based advancement gate |
| Generate independent practice | Partially implemented | `assessment.py:generate(practice=True)`; `study_report.py:generate_pack` | LLM keys and format only lightly validated |
| Wait for structured student answer | Implemented and verified (source) | `AssessmentView`; SparkAssessment.correct initially None | Generic chat questions have no assessment attempt by themselves |
| Hints/explain errors | Partially implemented | Assessment feedback/explanation; teaching prompts | Explanation shown after answer; no structured hint cost/state |
| Change practice difficulty | Prompt-dependent only | Assessment generator says vary difficulty | No calibrated item difficulty or adaptation rule |
| Persist attempts | Partially implemented | SparkAssessment JSON student_answer/correct/graded_at | One stored answer/item; no immutable multi-attempt/submission ledger |
| Track taught and observed concepts | Partially implemented | PwanimateStudyCheckpoint concepts_explained/concepts_demonstrated | Substantive-message filter does not prove correctness |
| Completed lessons/weak concepts | Partially implemented | Session.status, roadmap JSON, checkpoint misconceptions | No stable concept identities or lesson-completion evidence |
| Retention testing/spaced review | Not implemented | No due-review policy in inspected study services | Reminders schedule time but do not evaluate retention |
| Resume unfinished learning | Implemented and verified (source) | Owned session detail/status, context restore, checkpoints | Preserve evidence distinction and document revocation behavior |

## Assessment, assignment and interaction capabilities

| Capability | Status | Evidence | Limitation and recommended action |
|---|---|---|---|
| Quiz creation/structured questions/MCQ | Partially implemented | `models.py:SparkAssessment`; `AssessmentService.generate` | Count/options/nonempty key consistency not strictly enforced |
| Numerical answer input | Partially implemented | Free-text input; submit_answer | Input possible; no deterministic numeric tolerance/equivalence path |
| Typed explanation input | Partially implemented | `spark-study-sessions.js:renderQuestion` | Single-line field; truncates answers at 1,000 chars server-side |
| Voice-transcribed answers | Partially implemented | `PwanimateVoiceTranscriptionView`, chat voice methods | Chat transcription exists; no explicit quiz-answer voice binding |
| Sequential clarification questions/Send/dismissal | Implemented and verified (source) | `pwanimate-chat.js:_buildInteractionBlock` area :5729; `services/interactions.py` | Browser lifecycle untested; clarification and grading schemas differ |
| Sequential assessment/explicit MCQ Submit | Not implemented | `spark-study-sessions.js:renderQuizPanel/renderQuestion` | All unanswered rendered; click submits immediately; reuse interaction UX deliberately |
| Assessment ownership and hidden keys | Implemented and verified (source) | `AssessmentView._session/post`; `public_question/public_assessment` | Avoid returning raw questions JSON in future tools/events |
| Answer validation/grading | Partially implemented | `AssessmentService.submit_answer` | MCQ aliases deterministic; invalid MCQ text falls back to LLM; free text verdict uses unsafe bool coercion (probe) |
| Mathematical grading | Partially implemented | Same service | No symbolic/numeric validation found; key can itself be mathematically wrong |
| Partial credit/rubric | Not implemented | Boolean verdict and item schema | Postpone or add versioned rubric/mark evidence; never fabricate precision |
| Score persistence | Partially implemented | Item correct, mastery_percent/progress_percent | Accuracy mislabeled mastery; objective reset and concurrency gaps |
| Assignment tracking/submission/deadline | Not implemented | Assignment document category; planner reminders only | Separate actual assignment workflow later; categories are resources |
| Mock exam generation | Prompt-dependent only | Generic teaching/drafting/assessment generation | No source-backed paper blueprint, exam attempt or marking record |
| Past-paper identification | Partially implemented | DocumentCategory past_paper/exam, unit links and retrieval | No multi-paper topic/question extraction/comparison engine |
| Runtime streaming/model quality/production migration parity | Unable to determine | SSE/gateway code; existing tests | Not executed in this audit |

## Candidate module evaluation: reuse, gaps and dependencies

These are capability boundaries, not eleven Django apps or services. Every database/API name marked proposed is a future design, not current code.

| Module | Existing functionality and reuse | Missing capability | Dependencies | Potential database change | API direction |
|---|---|---|---|---|---|
| Unit Learning Spaces | Session.unit/unit_scope; sessions page; enrollments; get_or_create_unit_session | Stable unit entry/reopen contract and explicit selected-unit authorization | Identity, academic resolver, sessions | Initially none; later unit-space identity only if multiple goals cannot fit sessions | Existing sessions endpoints; proposed unit resolve/open adapter |
| Learning Roadmaps | Session.roadmap, normalize/set/update methods | Version/approval, concept IDs/objectives/prerequisites; methods not wired in inspected API/tools | Unit/session, source retrieval | Versioned roadmap/objective plus stable concept IDs, additive | Proposed approve/read/update roadmap under existing sessions namespace |
| Teaching Engine | Existing prompts/context/gateway/RAG | Structured lesson phase and validated adaptation policy | Roadmap/session/context | Optional lesson state in existing bounded context; avoid transcript duplication | Existing chat; proposed context skill contract |
| Understanding Engine | Checkpoint observations, assessment feedback | Reasoning rubric, validated evidence and misconception taxonomy | Assessment and concept IDs | Evidence linkage/observation type; no personality profile | Proposed evaluate result inside assessment service; no independent public orchestrator |
| Assessment Engine | SparkAssessment, AssessmentService, AssessmentView, quiz UI | Strict schema, key verification, atomic/idempotent answers, concept linkage | Source/context, identity, gateway, validator | Immutable submission/evaluation/version fields or tables | Extend existing assessments endpoint, keep key-safe response |
| Mastery Engine | Session score fields/recomputation | Concept evidence threshold, goal scope, uncertainty and retention | Valid assessments, concepts/version | Concept-state projection and policy/evidence version | Proposed read-only concept progress projection |
| Assignment Engine | Assignment resources, reminders | Actual assignment/submission/rubric/deadline workflow | Identity, documents, notifications, permissions | New assignment/submission only when approved | Deferred owner-scoped assignment APIs |
| Revision Engine | Practice flag, reports, reminder/block scheduler | Evidence-based due reviews and retention outcomes | Mastery/evidence, planner | Review due state/policy or small review record | Proposed due list/create revision assessment using existing assessment endpoint |
| Document Intelligence | Uploads, versions, chunks, embeddings, hybrid/page retrieval | Filter parity, long-book coverage, paper blueprint | Shared document authorization, indexing | No pipeline replacement; optional approved source manifest/blueprint later | Reuse context/search/doc endpoints; source-bounded request contract |
| Academic Memory | Sessions/checkpoints/assessments/artifacts | Reliable evidence semantics; separate privacy categories | Owned academic records, source messages | Evidence/consent scope additive; do not store everything in digest table | Extend session/report/record privacy controls |
| Session Planner | Plans/blocks/reminders, PendingAction/run interactions | Explicit approval of autonomous learning-session proposal | Session, action approval, capability policy | Typed proposal or versioned PendingAction payload; no generic second planner | Proposed learning-session action handled by existing confirmation route |

## Candidate module evaluation: events, disablement and tests

Events below are **proposed in-process domain notifications**; existing SparkAgentEvent remains a run UI journal. A broker/event bus is unnecessary for the MVP. Persist essential state first, invoke side effects after commit, and add a transactional outbox only if recovery needs demonstrate it.

| Module | Produce / consume proposed events | Independently disabled? Safe behavior | Minimum tests |
|---|---|---|---|
| Unit Learning Spaces | session.opened / enrollment.changed | Yes: hide unit entry, keep normal chat and owned historical session reads | Reopen same scope; ambiguity; cross-user/cross-institution |
| Learning Roadmaps | roadmap.approved/versioned / sources.available | Yes: no proposal/update; preserve prior roadmap and evidence | Validation, revision, approval replay, no automatic done flags |
| Teaching Engine | lesson.observation / session.opened, roadmap.approved | Yes: ordinary Spark response with no lesson advancement | Prompt/context contract, source provenance, disabled fallback |
| Understanding Engine | understanding.observed / answer.evaluated | Yes: feedback-only, no automatic advancement | Incorrect substantive reasoning, passive agreement, missing evidence |
| Assessment Engine | assessment.created, answer.evaluated / practice.requested, review.due | Yes: block generation/submission; historical results read-only | Key secrecy, verdict schema, math fixtures, concurrent/replayed answers |
| Mastery Engine | concept.state_changed / answer.evaluated | Yes: freeze projection and show evaluation paused; preserve evidence for later versioned rebuild | Minimum evidence, concept scope, stale goals, uncertainty, no self-report credit |
| Assignment Engine | assignment.due/submitted / approved assignment changes | Yes: no new work/submission; existing records readable, deadline side effects explicitly paused | Ownership, deadlines/timezones, attachment access, duplicate submissions |
| Revision Engine | review.due/completed / concept.state_changed | Yes: no automatic due creation/nudges; keep recorded history and manual practice | Due rules, timezone, restart, opt-out and repeat review |
| Document Intelligence | source.ready/failed / document lifecycle | SALS enrichment yes; shared viewer/auth remains core. Disable optional source analysis, report grounding unavailable, retain citations | Unit filter parity, revocation, latest version, scanned/large docs |
| Academic Memory | learning.evidence_recorded / answer.evaluated, session.checkpointed | Optional recall/derived continuity yes; core record ownership never disabled. Opt-out must not silently delete scores | Reset/forget scope, delayed jobs, source deletion and explicit learning-record removal |
| Session Planner | session.proposed/approved / learner request, review.due | Yes: no auto proposals; manual study entry remains if enabled | Deny/expire/replay approval, exact approved unit/goal, feature disabled after proposal |

## Complexity decisions

Merge Teaching and Understanding into one tutoring service boundary for MVP, while keeping observations distinct from validated outcomes. Assessment and Mastery should have separate contracts but can live in the same Python package. Academic Memory should extend owned learning records and continuity, not duplicate the personal memory store. Unit Spaces are a view/projection over sessions and units initially. Defer Assignment Engine and multi-paper mock-exam analysis. Revision starts with one explicit due-review policy and existing practice assessments; avoid a scheduling framework. The shared document infrastructure, gateway, authentication, consent enforcement and data ownership are never optional dependencies.
