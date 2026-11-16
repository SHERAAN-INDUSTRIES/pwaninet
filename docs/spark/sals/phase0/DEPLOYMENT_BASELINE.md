# Phase 0 deployment and engineering baseline

11 November 2026. Design/source verification only; no application imports, tests, migrations, provider calls or database access. Scope is Spark inside PwaniNet. All later model/interface names marked proposed are design contracts, not implemented objects.

## Repository and release identity

Repository `/home/zayne/projects/pwaninet`, current branch `main`, HEAD `6084e059d43e2aca15a84d6a48c77b3fd3feb40c`. Git status shows extensive pre-existing modified/untracked source and migrations, plus deleted files. The current branch is verified; the **intended delivery branch is not established** by this fact. Do not merge, reset, commit unrelated changes or assume HEAD describes the working tree. A pre-documentation SHA-256 snapshot was captured for change verification. No AGENTS.md was returned in the scoped repository search; user-supplied audit restrictions apply.

Inputs: all six existing documents in `docs/spark/sals/audit/`. They remain historical discovery reports; the approved policies and current Phase 0/1 contracts supersede their unresolved product choices, not their source observations.

## Source-confirmed execution

`pwaninet/urls.py` mounts `/pwanimate/` and `/api/pwanimate/`. `pwanimate/api/views.py:PwanimateChatView` requires authentication, resolves owned conversations/sessions, persists messages, loads bounded history/recall, constructs orchestration requests and returns JSON/SSE. `services/conversation.py:ConversationService` owns transcript operations. `orchestrator/service.py:PwanimateOrchestrator.run` routes among general planner, deterministic tools, conversation and RAG. Its initial StudyAgent admission excludes study-mode and selected-resource/attachment turns. Reuse this separation; new registry entries alone will not enable study-mode commands. `DocumentTurnContext`, ContextEngine, ToolRegistry and AIGateway remain shared.

Fresh SSE uses a process-local queue/thread; continuation uses `api/agent_runs.py`, durable interaction/event/run records, `services/agent_execution.py` and `tasks/agent_runs.py`. Continuation dispatch has an inline-thread fallback. Neither guarantees all initial requests survive process death.

## Database and migrations

`pwaninet/settings/base.py`, `local.py`, `production.py` select PostgreSQL; test.py overrides to SQLite `:memory:`. Production Compose declares `pgvector/pgvector:pg15`; this is configuration, not a live server-version observation. No explicit ATOMIC_REQUESTS setting was found in inspected settings. Use short explicit transaction.atomic blocks; verify actual isolation/version before rollout. PostgreSQL row locks require an open transaction and separate connections for concurrency tests. SQLite cannot certify select_for_update semantics or pgvector behavior.

Migration sources: `pwanimate/migrations/0022_remove_pwanimatestudysession_pwanimate_unique_active_study_session_per_unit_and_more.py` depends on documents 0020 and pwanimate 0021, adds unit_scope, pauses duplicate active rows, replaces the conditional unique constraint. 0023 adds mastery/roadmap; 0024 creates SparkAssessment; 0025 adds practice exclusion/artifacts; 0026 extends durable agent execution. They are pre-existing untracked files; no applied migration state was read. Migration header runtime versions are not proof of installed versions. `Dockerfile` starts from Python 3.11; compatibility with the intended Django/dependency set must be established in isolated CI, not inferred from source comments.

## Tests and worker isolation

`pwanimate/tests/test_study_mode.py` and `test_study_assistant.py` use Django TestCase, explicit ORM fixtures, APIClient and mocked responses. `documents/tests/test_permissions.py` encodes current elevated-role privileges that must change with policy. `documents/tests/test_signed_downloads.py` references a document_factory; its fixture implementation was not located in the bounded conftest search. No AssessmentService/mastery_percent/set_roadmap references were returned in the inspected Spark tests. Tests have not been run.

`settings/test.py` imports local settings and selects Gemini vision/OCR names; it does not establish globally disabled network/provider/Celery behavior. Phase 1 requires dedicated isolated PostgreSQL test configuration, fail-on-unmocked network, fake providers, temporary storage and patched task dispatch. Eager tasks alone do not prevent model calls.

`settings/base.py` declares Redis Celery, default/media/docs/search queues, Spark tasks on docs_queue, ingestion reconciliation and agent recovery schedules. `docker-compose.prod.yml` worker consumes these queues. No beat service was found in that file; an external scheduler may exist, but is unverified. Do not infer that periodic recovery runs.

## Deployment and deletion boundaries

Production Compose starts web with migrate and collectstatic before Uvicorn; **do not start it during audit**. It references runtime environment files and mounted source/data. Credentials, running containers, production DB, settings resolved from environment, CI execution and live storage rules were not inspected.

`pwanimate/views.py:PwanimateConversationDeleteView.delete` and `api/views.py:PwanimateConversationDetailView.delete` call conversation.delete(). `models.py:PwanimateStudySession.conversation` is non-null one-to-one CASCADE; assessments/artifacts/checkpoints depend on session. Current deletion therefore removes academic records. This violates the approved future retention requirement and is a release blocker until schema and both deletion paths change.

Readiness evidence required later: selected release commit containing relevant untracked code; resolved migration graph versus sanitized applied-version manifest; tested Python/Django/PostgreSQL/pgvector matrix; isolated concurrency runner; worker/scheduler configuration; storage delivery/revocation validation. No deployment verification is claimed.
