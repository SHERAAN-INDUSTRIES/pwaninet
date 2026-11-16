# WP0 baseline — 11 November 2026

Repository: /home/zayne/projects/pwaninet. Current branch main; HEAD 6d17e49a8d64f7050992fd6a11e758b02291ae74. No delivery branch was inferred or created. Existing modifications to pwanimate/orchestrator/agent.py, ai/gateway/types.py and tests/test_gateway.py predate WP0 (planner payload fix). Existing SALS documents were untracked. All were preserved; no push/deploy/reset occurred.

Read FINAL_PHASE0_PHASE1_SPEC.md, IMPLEMENTATION_WORK_PACKAGES.md and test isolation/release specifications. Scope: existing PwaniNet Spark only. No WP1 business implementation authorized or performed.

Runtime observed: .venv Python 3.14.8, Django 6.1.2. PostgreSQL 16.15 binary successfully initialized a fresh temporary cluster; server startup failed creating its Unix socket: Operation not permitted. No running DB connection was established. Installed PostgreSQL directories: 16 and 18. No vector.control at the inspected extension paths. Docker CLI present; daemon socket access denied. No system packages installed.

Existing settings/test.py imports local and uses SQLite; production/base use PostgreSQL; Dockerfile uses Python 3.11 and production Compose uses pgvector:pg15. These are distinct configurations, not verified deployment parity. The WP0 profile never executes base/local settings or loads .env; it reads the literal installed app list with AST to preserve app/migration coverage.

Offline MigrationLoader(None) ran successfully under isolated settings: conflicts {}; Spark leaf 0026_sparkagentrun_attempt_id_and_more. Dependencies include 0022 unit-scope constraint/data migration → 0023 mastery/roadmap → 0024 assessment → 0025 practice/artifacts → 0026 durable-run changes, plus documents/user dependencies. Graph loading verifies source dependencies only, not executable SQL or production application state.

Existing gateway uses provider adapters; embedding factory defaults to remote Gemini absent override. Conversation persistence queues embeddings/digests, and Celery schedules can reach brokers/tasks. WP0 guards HTTP, sockets, native libpq destinations and task dispatch; defaults mock and memory transports/storage/email/cache. No provider keys, production data or runtime secrets were inspected.
