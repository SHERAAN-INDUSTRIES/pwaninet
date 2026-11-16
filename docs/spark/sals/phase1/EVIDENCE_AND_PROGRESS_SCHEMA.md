# Evidence and progress schema proposal

Design only. Existing anchors: `pwanimate/models.py:PwanimateStudySession`, SparkAssessment, PwanimateStudyCheckpoint, SparkStudyArtifact; `services/assessment.py`; `services/study_session.py:set_target/recompute_progress`; `services/study_report.py:session_stats`; migrations 0023–0025. All new names below are proposed logical model names, not existing classes.

## Minimum persistence changes

| Record | Proposed fields/relations | Invariants |
|---|---|---|
| Existing study session | Nullable conversation SET_NULL; owner retained; active_objective pointer; custom unit stable UUID when applicable | Chat lifetime never owns academic lifetime; detached session readable; no custom unit stored in shared catalog |
| LearningObjectiveVersion | UUID, session, ordinal/version, goal text, unit identity, accepted concept definitions/IDs, policy_version, created/retired_at | Unique(session, ordinal); immutable accepted definition; active pointer changed under session lock |
| Existing SparkAssessment | Nullable objective version for legacy, contract/content version, published status, certification status/key provenance, generation key/hash; preserve session/user IDs | New scored assessments require matching active objective at admission; legacy stays uncertified |
| AssessmentQuestion | Assessment/content version, stable qid, type, immutable public prompt/options, private validated key/comparator, verifier version/provenance, concept/variant IDs | Unique(assessment, content_version, qid); one published version; keys inaccessible to public serializers |
| AssessmentSubmission | Question FK, objective version, immutable normalized/raw bounded answer, request key/hash, received_at, independence/assistance, lease/token/status | Unique(owner, operation, key) and unique(question version, attempt scope); scope/owner validated; no chat FK cascade |
| AssessmentEvaluation | Submission, revision/token, correct/incorrect/inconclusive, nullable correct, reason, comparator/grader version, eligibility, evaluated_at | Append-only outcomes; one authoritative revision; inconclusive has correct=null; terminal verdict consistency DB checks |
| Concept projection | Initially compute on read or scoped recomputation; optional cached row later | Derivable and versioned, never authoritative score evidence; no extra model necessary initially |

Stable concept definitions can remain immutable bounded JSON inside objective version for Phase 1; a shared prerequisite/concept graph is deferred. Submissions record the matching concept ID and objective version, enforced by service validation under objective lock; a future normalized concept FK is warranted only if graph/query needs demand it. Initial record ownership can be derived through question→assessment→session→user, avoiding duplicated owner fields where possible. Where user is duplicated for indexing/idempotency, service invariants are mandatory and foreign-key chains alone are not proof of equality. Use composite constraint/DB trigger only if multiple independent write paths make service-only enforcement inadequate; document whichever is selected before migration approval.

Custom student-created units require stable private identity, not normalized global label authority. For Phase 1 use an owner/session-scoped private UUID and label persisted independently of chats; enrolled canonical units keep their existing AcademicUnit FK. A reusable personal-unit catalog is a later model if multiple sessions must share that identity. Never enroll another user or publish it automatically.

Published question/answer evidence stores the minimal reproducible assessment snapshot, not entire documents or chat. Private sources carry IDs/version/hash; source access is checked at use. Export of finalized owner evidence may include the assessed prompt/answer/outcome but not active answer keys or full private third-party sources. Deletion removes evidence; immutability protects normal operations, not a user's right to delete records.

## Score API contract v2

Expose schema_version, objective_version, content_version, certification_status, counts {total,answered,graded,correct,incorrect,unanswered,pending,inconclusive}, completion_percent, grading_completion_percent, accuracy_percent (nullable), accuracy_fraction, mastery {state,policy_version,missing_requirements}, and eligibility_for_active_goal. Published count must be >0. Counts follow `../phase0/LEARNING_EVIDENCE_CONTRACT.md` exactly. Accuracy null when graded=0; mastery is insufficient_evidence/practising until reviewed concept requirements are met. Roadmap coverage remains separate.

Sample: total=5, answered=1, graded=1, correct=1, incorrect=0, unanswered=4, pending=0, inconclusive=0; completion=20, grading_completion=20, accuracy=100, fraction=1/1, mastery=insufficient_evidence. Nothing maps that 100 to retained mastery.

Legacy compatibility: preserve existing public_assessment id/mode/status/title/total/answered/correct/questions; v1 answered remains its documented legacy decisive-count meaning. Do not repurpose the field silently. Add legacy_metric_semantics and certification_status=legacy_uncertified; legacy mastery_percent/progress_percent are retained only as explicitly legacy values and never feed v2 learning state. V2 clients use the versioned fields; current JS boolean truthiness cannot safely render inconclusive and must be adapted before v2 writes are enabled. In unsupported v1 cases return a clear upgrade/unsupported error, never false-as-inconclusive or 0-as-unknown.

## Goal and retention isolation

Replacing a goal appends a version and changes active pointer atomically. Existing assessment version never changes goal. A submitted old-goal result may finalize historically; active goal's projection cannot include it. Previous score resets are not a sufficient fence. Legacy assessments have objective_version=null until provenance is explicitly established; no migration generates concept correctness from roadmap labels, summaries or percentage values.

Session detach is a required compatible change before preserved evidence release. Update conversation serializer accessor, session title/hyperlinks, history, checkpoint generator, report and resume paths to tolerate null; `build_study_orchestration_context` currently dereferences session.conversation. A new chat link is an explicit operation. Existing checkpoints are observations; source message SET_NULL already helps, but no checkpoint text becomes certified evidence merely by being retained.

## Deferred schema

Personal fact/candidate/approval, DocumentAccessGrant and richer private-unit catalog are conditional future additions, not mandatory for assessment correctness. Fact retrieval/sharing stays disabled until corresponding models/interfaces/gates exist. Academic export/delete interfaces are small owner-scoped service/API operations; a job model is needed only for asynchronous volume. No second conversation, quiz, LLM agent or generic event framework is proposed.
