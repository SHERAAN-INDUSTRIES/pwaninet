# Disposable PostgreSQL harness

Entry point from repository root:

```bash
.venv/bin/python -m tests.sals_wp0.run
```

Requires an accessible local Docker daemon and pgvector/pgvector:pg15 image (Docker may fetch it if absent). It does not start production Compose. No persistent volumes or existing databases are used. It creates one UUID-named container, tmpfs PGDATA and random loopback-only mapped port, with temporary generated credentials. A NOSUPERUSER CREATEDB role sals_wp0 owns the disposable DB. Vector is provisioned in template1 by the container bootstrap admin so the test role need not be superuser. Cleanup removes exactly the container created by this invocation and its temporary manifest/files, including on test failure. A hard process kill may require removal of the labeled sals.wp0=true container; never use broad volume-prune commands.

Settings tests/sals_wp0/settings.py require a generated manifest with exact database/user/host constraints. The runner strips inherited environment settings/credentials before Django setup. No DATABASE_URL/production broker is accepted. Manifest permissions are 0600. Only sals_wp0, test_sals_wp0 and postgres maintenance database on the generated endpoint may pass the native psycopg2 guard. Normal sockets permit only that endpoint; requests HTTP, connect_ex/datagrams, Celery apply_async and send_task fail closed. Email is locmem, files InMemoryStorage, cache/channels memory; no remote notification transport is configured. Guards are test-only process-scoped safeguards, not a security sandbox for arbitrary malicious subprocess/native libraries. Run through the entry point; importing settings alone does not install guards.

probe.verify uses independent psycopg2 connections, backend PID comparison, explicit barriers, SELECT FOR UPDATE, lock_timeout conflict, committed visibility and rolled-back update. It owns only wp0_probe in the newly created disposable DB. It does not contact any previously running DB. test_database.py uses TransactionTestCase to force Django test-database migration/setup, vector SQL and transactional DDL rollback; MigrationExecutor reverses the latest Spark leaf to its parent then reapplies it. This is an empty-fixture migration smoke test, not historical-data upgrade certification. Real tests run only inside disposable test_sals_wp0. Existing gateway contract and chunker tests are also selected by the runner.

## Executed results

- Docker runner attempted twice: blocked by daemon socket permission; no container created.
- Local initdb succeeded in /tmp; server startup failed: Unix socket Operation not permitted. No real locking probe executed. Server exited; no application database touched.
- Offline source migration loader succeeded, conflicts {}.
- 13 offline tests passed: four isolation tests plus nine existing gateway contract tests. Network/task and native-libpq denial tested; cursor_factory preserved through guard.
- Earlier offline selection of test_chunker errored because it requires a database; this exposed a guard handling bug for psycopg2 cursor_factory, which was fixed and covered by a passing infrastructure test. Chunker was not claimed passed and remains selected for the database-enabled runner.

Not executed successfully: vector SQL, full schema migration, reverse/forward migration, actual commit/rollback visibility and concurrent row locking. Do not substitute SQLite or sequential mocks for these gates. No production schema comparison or historical-data rollback proof was attempted.
