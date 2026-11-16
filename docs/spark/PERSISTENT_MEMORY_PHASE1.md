# Spark persistent memory — Phase 1

> Historical implementation record. For the current verification pass, migration 0030, academic consent settings, deletion protection and acceptance status, see [SALS 0.1 verification report](SALS_0_1_VERIFICATION_REPORT.md). Counts below describe the earlier run.

Scope: Spark in PwaniNet/Vasto only. No Workspaces, Zane, frontend, external database, paid service, or new orchestration engine.

## Initial architecture audit

The checkout already contains substantial uncommitted work. This change extends it without resetting it.

- `PwanimateConversation.user` owns persisted `PwanimateMessage` rows. Conversation history is already durable in PostgreSQL.
- `PwanimateMessageEmbedding` and pgvector support exist, as do an embedding factory, a legacy `embed_message` task, `SparkConversationDigest`, and `SparkMemoryPreference`.
- The committed conversation-memory service previously performed owner-scoped semantic search over message embeddings with lexical transcript fallback. In the current checkout, indexing returns `personal_fact_approval_required`; digest refresh is quarantined; conversation recall delegates exclusively to `PersonalMemoryService.recall`.
- `SparkPersonalMemory`, migration 0027, preference consent version/revision, fact approval, and `/api/pwanimate/memory/` already exist in the current checkout. Approved-fact recall previously ignored the query and returned the latest facts. Only explicit response-preference proposals were captured. Ordinary academic learning observations were not captured.
- `PwanimateChatView` retrieves memory before constructing `OrchestrationRequest`. `agent_execution.execute_claimed` reconstructs requests for durable continuations and refreshes memory. `StudyAgent` uses memory in planner JSON; `ContextPackage.format_context_text` includes it in answer context.
- `ConversationService.persist_assistant_message` is shared by initial and resumed runs. It already schedules background work with `transaction.on_commit`. Waiting responses use `task='agent_pause'`; legacy interim responses use `task='agent_interim'`.
- Spark already has durable runs, checkpoints, journal events, waiting/resume and approval logic. These remain the execution mechanism.
- Existing tests include approved-fact consent/privacy, streaming, interactions and an isolated pgvector WP0 runner. The latter requires Docker, unavailable in this execution environment.

**Established cause in this checkout:** transcript recall was intentionally replaced with an approved-fact-only gate; legacy indexing/digests were disabled. There was no academic fact extractor, and approved-fact retrieval ignored relevance. This explains missing cross-chat observations here. The history of who made those uncommitted changes, and the deployed server's exact state, were not established. Re-enabling raw transcript search would bypass the current consent design and is not part of this fix.

## Storage and authorization

Extend `SparkPersonalMemory`, rather than add a second fact store. Migration `0029_persistent_memory_phase1` adds category (`semantic`, `episodic`, `learning`), normalized-content fingerprint, logical topic key, importance/confidence, automatic origin, source conversation, last access, supersession pointer/state, and nullable 768-dimensional embedding/model fields. Existing text, owner, source message, UUID, timestamps, review/version and consent fields are reused. An owner/status/time index, active nonempty fingerprint uniqueness constraint and confidence/importance range constraints protect storage.

`SparkMemoryExtraction` is a durable per-user-message receipt, referencing the completed assistant message and consent revision. Receipt and fact updates commit in the same transaction. A retry after fact deletion cannot re-extract the same source. Deleting chat sources nulls memory provenance; the fact survives conversation deletion. Deleting an entire account cascades its memories.

PostgreSQL remains the production source of truth. New automatic extraction defaults OFF for every existing account. No data migration scans or imports old conversations. Existing approved facts remain usable.

## Runtime flow

1. Authenticate and resolve an owner-scoped conversation as before.
2. `ConversationMemoryService.search` → `PersonalMemoryService.recall` → `PersistentMemoryService.retrieve` for nonempty queries.
3. Retrieve relevant active authorized facts across the owner's conversations, then assemble the existing agent context.
4. Execute the existing planner/gateway/tools, streaming and durable run lifecycle.
5. Persist the assistant message through `ConversationService`.
6. After commit, `queue_extraction` dispatches `extract_memories` on the existing `docs_queue`, only for final responses and opted-in owners; pause/interim messages are excluded.
7. The worker rechecks consent revision, activation/reset timestamps and owner; scans at most 20 bounded new user messages between completed responses, including user statements before clarification pauses; extracts and commits facts plus receipts.

Resume refreshes memory after appending the continuation query, instead of trusting the durable snapshot's old memory list. No memory task invokes the planner or changes a run state, question block or approval.

## Extraction policy

Phase 1 uses a deterministic full-clause grammar and an explicit academic topic vocabulary. It makes **zero additional model/API calls**. It captures statements such as `I'm studying calculus`, `I understand substitution`, `I struggle with integration by parts`, `I no longer struggle with integration by parts`, `I want to learn physics`, and `I completed calculus`.

Only direct first-person statements are eligible. Source content, never assistant assertions, provides the observation. Stored learning records say **reports understanding/difficulty**, not assessed mastery. Confidence 1 means confidence in the student's explicit self-report, not proof of ability. Greetings, ordinary questions, third-party claims, quoted statements, unrecognized clauses, health/relationship/credential facts and unrestricted instructions are not automatic candidates. The vocabulary and exact clause validation are intentionally conservative; this is not a general-purpose language-understanding model.

For each topic, later self-reports of understanding/difficulty supersede earlier automatic ability observations atomically. Delayed older jobs cannot reverse newer observations. Owner-edited topic memories take priority over automatic updates. Exact normalized duplicates are rejected/reused. Existing response-preference proposal/review behavior remains available. Arbitrary or sensitive facts require the existing exact-text proposal and explicit owner approval; enabling automatic academic extraction does not authorize arbitrary sensitive extraction.

Consent, extraction, corrections, deletes, and clear-all serialize on the same preference row. Disabling memory also disables automatic extraction. Clear-all increments consent revision and establishes a reset cutoff, protecting against queued jobs and old messages even if a task is re-submitted with a new revision.

## Retrieval and prompt boundaries

Only approved rows for the authenticated owner participate; proposed/rejected/revoked/superseded rows never participate. Query terms are bounded and stop words removed. SQL filters by query terms, returning at most 200 candidates. Ranking weights relevance .70, importance .15, confidence .10 and age .05. Result count defaults to 8 in the centralized service, capped at 12; existing callers retain their default 6. Context selection uses a configurable approximate token budget, including per-record metadata overhead, and updates last access without refreshing fact age.

No transcript fallback, persistent retrieval cache or external index exists. Deleted facts and their nullable vectors disappear together. Editing/revoking invalidates a fact's embedding. Logs identify message IDs and errors, not memory contents.

Optional semantic recall searches the **fact table only**, filtered by owner, active state and matching embedding model. It never falls back to old message vectors. Lexical recall is the default and remains available when the embedding provider fails. The optional vector path does not generate stored fact embeddings; an indexing rollout is deferred until an appropriate local model is validated.

Both answer and planner system instructions explicitly treat memory as untrusted data, never tool authorization. Answer context escapes memory content with HTML escaping; planner context uses JSON serialization. Stored commands cannot become new system messages. These safeguards do not constitute a mathematical guarantee against all model-level prompt injection; tools still enforce their own authorization.

| Setting | Default | Meaning |
|---|---:|---|
| `SPARK_MEMORY_TOKEN_BUDGET` | 600 | Approximate budget for selected facts, capped at 2000 |
| `SPARK_MEMORY_MAX_RESULTS` | 12 | Maximum returned facts, hard cap 12 |
| `SPARK_MEMORY_SQL_TIMEOUT_MS` | 150 | PostgreSQL per-statement deadline, clamped 10–1000 ms |
| `SPARK_MEMORY_VECTOR_RETRIEVAL` | False | Optional model-matched fact-vector retrieval |

The PostgreSQL statement timeout is transaction-local and restored before returning. Result count, lexical predicate count and input sizes are bounded. Vector mode is opt-in; its provider must have a separately configured short timeout and should be local. It is not part of the default latency path.

## Backend API

All routes require the existing authenticated user. Cross-owner detail/correction/deletion returns 404. GET responses are private/no-store. Existing `facts` response shape and review UI contracts remain compatible.

| Method | Route | Body / behavior |
|---|---|---|
| GET | `/api/pwanimate/memory/` | Up to 100 recent owner facts, status/metadata, enabled and automatic flags; existing conversation/proposed filters |
| POST | `/api/pwanimate/memory/` | `{text, category?, source_message_id?}` creates/reuses proposed fact, does not approve |
| PATCH | `/api/pwanimate/memory/` | `{enabled: true/false}` enables/disables recall with explicit consent |
| PATCH | `/api/pwanimate/memory/` | `{automatic_extraction: true/false}` separately authorizes new academic extraction; memory must first be enabled |
| PATCH | `/api/pwanimate/memory/` | Existing `{fact_id, action, version, text?}` review/correction API |
| DELETE | `/api/pwanimate/memory/` | Clear facts, old vectors/digests; preserve transcripts and academic records |
| GET | `/api/pwanimate/memory/<uuid>/` | View owner memory details |
| PATCH | `/api/pwanimate/memory/<uuid>/` | `{text, version}` edits to proposed state; `{action: "approve", version}` approves reviewed correction |
| DELETE | `/api/pwanimate/memory/<uuid>/` | Delete owner memory and its vector, retaining retry receipt |

Enable recall and automatic extraction in **two explicit calls**. Then complete the acceptance conversation, allow the worker to process the queued task, and ask the calculus revision question in a new chat. The extracted integration-by-parts difficulty is supplied to Spark's existing generation context. No frontend was added, so automatic opt-in currently requires an authenticated API client.

## Reliability and known limitations

- Database, task-dispatch and extraction failures are logged and do not break the chat. Delivered extraction jobs retry up to three times with 30-second delay, late acknowledgement and worker-loss redelivery. Receipts plus owner serialization make retries idempotent.
- A broker publication failure is logged but has no durable outbox recovery in this phase. A stopped worker will process broker-queued jobs when restarted. A broker outage during publication may leave an interaction unextracted. Add a transactional outbox/reconciliation sweep before relying on guaranteed ingestion.
- Candidate and source caps, exact grammar and vocabulary intentionally trade recall for privacy and predictability. Free-form paraphrases, inference across answers and semantic duplicate detection are deferred. More than 20 user messages since a completed answer can exceed the extraction batch.
- Stored facts are source-linked self-reports. Spark receives the relevant difficulty; the live model's final wording/recommendation was not tested against a provider in this environment.
- No automatic vector indexing/backfill or new historical chat import is enabled. Vector dimensions reuse the project's existing 768-dimensional convention.
- SQLite service tests cannot prove PostgreSQL row-lock races, PostgreSQL timeout behavior, or pgvector operator correctness. Those must run in an actual isolated PostgreSQL environment before deployment.

## Files in this change

| File | Purpose |
|---|---|
| `pwanimate/models.py` | Extend existing fact/preferences, add extraction receipt |
| `pwanimate/migrations/0029_persistent_memory_phase1.py` | Schema upgrade; no historical extraction |
| `pwanimate/services/persistent_memory.py` | Extraction, ranking, bounded retrieval, dispatch |
| `pwanimate/services/personal_memory.py` | Existing consent/review service extended with automatic consent and retrieval delegation |
| `pwanimate/services/conversation.py` | Shared post-commit extraction hook |
| `pwanimate/services/agent_execution.py` | Fresh relevance retrieval on resumed execution |
| `pwanimate/tasks/conversation_memory.py`, `pwanimate/tasks/__init__.py` | Register retry-safe extraction task |
| `pwanimate/api/study_assistant.py`, `pwanimate/api/urls.py` | Reuse management endpoint and add owner detail route |
| `pwanimate/context/types.py`, `pwanimate/orchestrator/prompts.py`, `pwanimate/orchestrator/agent.py` | Untrusted-memory boundary in answer/planner context |
| `tests/spark_memory/` | Pipeline, upgrade, restart and PostgreSQL concurrency regressions with isolated offline settings |
| `pwanimate/tests/test_conversation_memory.py`, `tests/sals_wp3/test_chat_memory.py` | Replace stale transcript expectation with approved-fact policy and extend consent mocks |

## Validation

The offline test settings never import local settings or load developer credentials. They use SQLite with schema synchronization, retaining memory indexes and constraints while excluding unrelated PostgreSQL-specific indexes. `test_migration` separately reconstructs the historical pre-0029 memory tables and executes the **actual 0029 operations** over them. A separate Python interpreter reads a file-backed backup to verify process-independent recall. This is not a full PostgreSQL deployment migration test.

See final validation results below.

## Recommended Phase 2

1. Transactional outbox and periodic recovery for publication failures; bounded extraction batching with a durable cursor.
2. Consent UI for automatic academic extraction, fact inspection, corrections and deletion.
3. Broader structured extraction with grounded source spans, privacy classification, confidence calibration and a held-out evaluation set; retain manual approval for sensitive facts.
4. PostgreSQL concurrency/worker-kill fault injection, latency measurement and vector operator/index tests in CI.
5. Validated local fact embedding/indexing, semantic deduplication, multilingual topic normalization, and a calibrated recall evaluation set.
6. SALS learning observations from explicit quiz evidence, distinguishing demonstrated mastery from self-reports, with provenance and decay policies.

## Final validation results

- Targeted combined suite: **61 discovered, 60 passed, 1 skipped**, 16.948 seconds. The skipped test explicitly requires PostgreSQL row locks and exercises simultaneous extraction redelivery. No PostgreSQL concurrency success is claimed.
- Includes 23 new offline pipeline/upgrade/restart tests, existing consent/proposal and grouped-interaction database tests, approved-fact recall tests, legacy isolation checks, mocked streaming provider/gateway regressions, and activity taxonomy tests.
- `makemigrations --check --dry-run`: **No changes detected**.
- `compileall` on the new/extended memory services, extraction task, migration and regression package: passed.
- `git diff --check`: passed.
- The real 0029 schema operations passed an isolated upgrade from pre-0029 memory tables, preserving an approved fact and leaving automatic extraction disabled.
- Full SQLite migration-chain attempt stopped at the existing `users.0030` PostgreSQL `gin_trgm_ops` index. That is why the offline service harness synchronizes other tables instead of claiming full SQLite migration compatibility.
- Docker access was denied; no local running PostgreSQL or installed server-side pgvector extension was available. **No development/production PostgreSQL database was migrated.** The rollout command remains `.venv/bin/python manage.py migrate pwanimate` against a configured PostgreSQL development database before live acceptance testing.
- An exploratory wider test run was interrupted; a targeted compatibility run including the existing `ActivityStreamTests` exceeded its 90-second limit at the activity/SSE test. These runs are not counted as passing. The final bounded suite excludes that stalled test; streaming provider/gateway tests and grouped waiting/resume tests passed. No unrelated streaming implementation was changed.
- The isolated harness emits existing django-axes middleware/backend configuration warnings; these are test-settings warnings and were not suppressed.

Reproduce the final bounded suite from the repository root:

```bash
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  tests.spark_memory \
  tests.sals_wp3.test_chat_memory \
  tests.sals_wp3.test_offline \
  tests.sals_wp3.test_database.MemoryConsentTests \
  tests.sals_wp3.test_database.ChatProposalTests \
  tests.sals_wp3.test_database.InteractionLifecycleDatabaseTests \
  pwanimate.tests.test_conversation_memory \
  pwanimate.tests.test_streaming.GatewayStreamingTests \
  pwanimate.tests.test_streaming.GroqStreamingTests \
  pwanimate.tests.test_streaming.OpenRouterStreamingTests \
  pwanimate.tests.test_streaming.GeminiStreamingTests \
  pwanimate.tests.test_streaming.MockProviderStreamingTests \
  pwanimate.tests.test_agent_reconciliation.ActivityTaxonomyTests \
  --noinput --verbosity 1
```

For PostgreSQL, include `tests.spark_memory.test_pipeline` and `tests.spark_memory.test_migration` in the existing isolated WP0 runner (do not use the SQLite settings). That enables the concurrency test and full database migration chain. Before rollout, also exercise a real Celery worker restart and the live acceptance conversation with the configured gateway; the current tests verify delivery/retry contracts and persisted context, not a running broker/worker/model stack.
