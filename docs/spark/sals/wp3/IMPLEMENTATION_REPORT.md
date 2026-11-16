# WP3 consent-based personal memory — 11 November 2026

Implemented consent cutover, owner-scoped proposed-fact workflow and settings controls. **Conditional implementation only: PostgreSQL migration/transaction/browser gates have not passed here.** No WP4 work, deployment, production access or provider calls.

## Behavior

New preferences default disabled. Effective enablement additionally requires consent_version=1; all existing rows receive version0, so legacy enabled=True cannot silently opt users in. Explicit PATCH enable sets current consent version and increments revision. Disable blocks retrieval but keeps facts available for owner inspection/deletion. Re-enable does not approve rejected/revoked/proposed facts.

SparkPersonalMemory stores exact text, purpose, proposed/approved/rejected/revoked status, fact version, consent revision, decision timestamp, owner and optional owner-validated source message. Each edit returns to proposed and increments version; approval checks displayed version and captured consent revision. Reject clears rejected text; revoke blocks reuse. Delete removes the row. Fact approval does not toggle global consent. Stale decisions after disable/re-enable fail until a new proposal/edit is reviewed. All mutations serialize on preference then fact row; recall reads approved facts while holding the same preference lock. This supports a coherent read snapshot; already transmitted LLM context cannot be recalled after revocation.

GET /api/pwanimate/memory/ returns effective enabled, facts and an empty legacy memories list. POST proposes an exact fact (optional source_message_id). PATCH with fact_id, version and action approve/reject/edit/revoke/delete modifies that owned fact; PATCH enabled toggles effective consent. DELETE forget removes facts and legacy digests/embeddings, advances cutoff/revision, preserves transcript/academic records. GET is private/no-store. Settings show exact proposals with explicit approve/reject, edit, revoke and delete controls; no chat redesign.

ConversationMemoryService semantic and lexical entry points now return only a bounded approved-fact set. No raw transcript search fallback, embeddings or vector store for facts. Legacy digest refresh and transcript embedding tasks unconditionally no-op before reading content, calling providers or writing status; retries and failure paths therefore cannot resurrect records. Welcome no longer reads legacy digests. Resumed agent snapshot memory is replaced by current approved facts rather than trusting cached legacy excerpts. Same-conversation history/checkpoints remain separate and unchanged.

## Migration

Created pwanimate/migrations/0027_personal_memory_consent.py depending on0026 and swappable user dependency. Adds consent_version/revision, alters preference default, creates personal fact table with source-message SET_NULL. No data is automatically approved or erased. Migration was **not applied**. Roll out schema before updated web/workers; restart/drain old workers so pre-cutover code cannot continue publishing old memory. Do not roll back to legacy raw-recall binaries: they would bypass approval. Quarantined legacy data may be forgotten explicitly; no automatic retention purge was invented.

Generation initially detected an unrelated pre-existing SparkAgentInteraction.input_type choice drift. That AlterField was removed from the new migration to keep WP3 scoped. Offline graph has no conflicts and leaf0027; the unrelated drift remains for separately authorized cleanup. No existing migration was edited.

## Files changed

pwanimate/models.py; migrations/0027_personal_memory_consent.py (new); services/personal_memory.py (new); services/study_memory.py; services/conversation_memory.py; services/agent_execution.py (snapshot memory only); tasks/conversation_memory.py; api/study_assistant.py (MemoryView only); static/js/spark-study-desk.js; templates/pwanimate/settings/partials/study_content.html; tests/sals_wp3/__init__.py, test_offline.py, test_database.py (new); tests/sals_wp0/run.py (test selection); this report.

## Executed checks

39 offline tests passed: four new WP3 default/legacy/no-op/recall-delegation/welcome checks plus35 existing validation/API/isolation/contracts. New tests do not establish DB approval/ownership/concurrency behavior. Node clarification, quiz and first-token collapse regressions passed. JS syntax, Python compilation and git diff --check passed. Offline MigrationLoader reports no conflicts; migration autodetection confirms only pre-existing interaction-choice drift remains in Spark.

makemigrations checked an unused loopback test endpoint and warned connection refused; generated schema was inspected without applying it. Full isolated WP0 runner failed before startup because Docker socket access is denied. Five DB tests added/selected: default/legacy plus approval/isolation, edit/reject/disable, stale consent, forget/transcript preservation, and two independent threads racing approve/disable. They are **not executed**. No production DB connection, live extraction or provider call was made.

## Deferred/limits

Automatic Spark fact extraction and chat-native proposal tool wiring are deferred; the owner proposal API/service and existing settings provide the minimum explicit flow. No claim that Spark autonomously proposes facts yet. No personal fact embeddings or raw cross-conversation recall; relevance ranking/TTL/export richer UI are deferred. Approved facts are bounded personalization context, not a search-quality guarantee. Existing tests expecting automatic digest welcome/default-on recall require revised policy expectations before full regression certification. Real-browser control usability, PostgreSQL migration forward/reverse/data-preservation, endpoint ownership and real consent races remain release gates.

Academic progress still has its existing chat-linked lifetime; WP3 does not implement WP4 independence. Forget deliberately leaves those records untouched. No assessment grading/mastery, document authorization or Workspaces changes. Stop after WP3; WP4 requires separate approval.
