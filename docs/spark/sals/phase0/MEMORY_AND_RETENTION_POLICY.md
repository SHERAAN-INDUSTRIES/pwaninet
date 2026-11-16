# Memory and retention contract

Approved policy: personal long-term memory off by default; individual approval for each extracted fact. Academic progress survives conversation deletion with separate export/deletion controls. These requirements supersede the audit's open product questions.

## Four purposes

| Purpose | Allowed source/use | Default and consent | Deletion behavior |
|---|---|---|---|
| Transcript | Current owned conversation history for an active response | Ordinary chat persistence; not personal-fact consent | Chat deletion removes transcript/derived chat recall |
| Cross-conversation recall | Owner-scoped older transcript excerpts | Separate explicit opt-in; off for legacy/unknown consent. Personal facts cannot bypass individual approval through raw recall | Disable blocks reads/new indexing; forget clears index/digests and sets cutoff |
| Approved personal fact | Specific proposed fact and approved purpose | Globally off; candidate approval required even when global switch on | Reject/revoke/delete immediately removes retrievability; changed value needs new approval |
| Academic evidence/progress | Submitted answers, objective versions and evaluations | Explicit study/assessment action; independent of personal-memory toggle | Preserved on chat deletion; separate owner export/delete |

Source: `pwanimate/models.py:SparkMemoryPreference` defaults enabled True; digest/message embeddings support transcript recall. `services/study_memory.py:preference/refresh/forget`, `services/conversation_memory.py:search/_lexical_fallback`, `tasks/conversation_memory.py:embed_message`, `api/study_assistant.py:MemoryView`. None establishes approved personal-fact storage. Explicit nickname/style preferences remain explicit settings; editing one is not consent to extract unrelated facts.

## Proposed consent lifecycle

Fact states: proposed → approved or rejected; approved → revoked; deletion removes content. Every candidate version has owner, exact text, purpose, source reference if available, consent timestamp/version and identifier. Only approved current facts with globally enabled memory may enter context/search. No embedding of pending/rejected candidates for retrieval, no inferred diagnosis/personality, no approval by model/tool response, no automatic reapproval after re-enable. Approved edits create a new candidate version. Rejected content should not be retained indefinitely just to remember rejection: keep minimal identifier/hash only when necessary for suppression and owner policy.

Global disable revokes retrieval permission immediately but does not silently erase approved facts. Re-enable does not resurrect revoked facts. Personal forget removes facts/derived vectors; academic delete is separate. Exact candidate content must be shown at approval, with expiry/revocation status resolved server-side. Phase 1 may defer the candidate model/API entirely; in that mode extraction and retrieval of personal facts remain disabled and the approval tests are future release gates.

In-flight work captures a consent revision/cutoff before dispatch, rechecks before reading/sending private data and at final write under a short consent lock. A revoked revision cannot publish a fact, index or digest; failure/retry paths must not recreate deleted rows. If a network request was already sent, revocation cannot undo transmission; discard returned data and prevent retries. Do not keep a DB lock around provider I/O. Background task identity includes owner/purpose/source version; no user identifier from the LLM.

Existing refresh/embed jobs check preference before work and after response; embedding failure handling writes state outside its success-path lock. Add failure-after-forget regression coverage. Existing reset_at blocks old message recall but is not a per-fact approval or retention policy.

## Legacy transition

Treat legacy enabled=True without explicit purpose/version evidence as unconsented for new cross-chat/personal recall. Preserve transcripts and quarantine old embeddings/digests from retrieval immediately at policy cutover; do not approve or re-index historical material automatically. Old digests must not feed new approved facts through welcome/context fallback. Owner inspection/forget remains available. Allow only explicitly opted-in, approved-purpose new processing; generic raw transcript recall containing personal facts remains off until purpose filtering can enforce approvals. Academic continuity uses structured owned evidence, not that fallback.

## Independent learning retention

Current `PwanimateStudySession.conversation` is CASCADE; assessments/artifacts/checkpoints cascade from session. Both API and UI delete conversation directly (`api/views.py:PwanimateConversationDetailView.delete`; `views.py:PwanimateConversationDeleteView.delete`). Proposed minimum change: make session.conversation nullable one-to-one SET_NULL, keep user ownership and unit/objective identity. Evidence records depend on session/objective, never on chat for lifetime. Source-message references are nullable SET_NULL. Persist only minimal assessment question/key/version/answer/evaluation necessary for reproducibility; do not copy personal chat text into permanent evidence.

All session APIs/templates/serializers must tolerate a detached session and omit dead chat links. When a student resumes, explicitly create/link an owned new conversation if requested; do not recreate deleted transcript. Source deletion detaches provenance; it must not retroactively certify uncertain observations. Reports containing personal transcript excerpts require removal/redaction on chat deletion; structured evidence can remain independently. User/account deletion remains a separately reviewed cascade, not permission to retain data forever.

Separate proposed academic export/delete actions resolve owner, requested unit/goal scope and confirmation server-side. Export includes outcomes/objective versions and grader provenance, never answer keys for uncompleted assessments or other owners' sources. Use private no-store delivery, expiring owner-bound export jobs; recheck at delivery. Academic deletion removes evidence/projections/artifacts, cancels scoped jobs and writes a purpose-specific tombstone/generation token preventing delayed reconstruction. It does not silently delete chats; describe separate choices. Export/delete UI may be deferred, but preserved learning data cannot be publicly released without a usable owner-controlled mechanism.

No fixed retention duration was approved. Proposed default: retain academic records until owner deletion/account deletion, subject to separately approved backup/log purge schedules. Backup expiry/provider retention and purge SLA remain operational decisions. Tests and stage-specific gates: `../tests/ACCEPTANCE_TEST_MATRIX.md`, `../tests/RELEASE_GATES.md`.
