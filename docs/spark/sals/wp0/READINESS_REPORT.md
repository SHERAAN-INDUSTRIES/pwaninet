# WP0 final verification update

Date: 11 November 2026. Verdict: **CONDITIONAL PASS**. The required disposable PostgreSQL foundation has passed in the user's local environment. This supersedes the earlier BLOCKED verdict caused by this agent session's Docker/socket restrictions. It does not certify production readiness or authorize WP1.

## Evidence and attribution

User-supplied successful execution:

```text
.venv/bin/python -m tests.sals_wp0.run
PASS independent connections, conflicting row lock, commit visibility, rollback
Migration conflicts: {}
26 tests passed in 31.306 seconds
Disposable test database created and destroyed
```

This update inspects the current runner, settings, guards, probe, database tests, Axes checks and legacy migration source to establish what that reported run covers. The agent did not rerun Docker/PostgreSQL in this update. The execution commit, exact runtime/server/extension versions and image digest were not supplied; coverage attribution assumes the local execution used the inspected harness. Earlier BASELINE.md and DATABASE_TEST_HARNESS.md describe the initial agent-session attempts, not the successful local run.

## What is established

| Area | Executed coverage established by reported success and inspected source | Limits |
|---|---|---|
| Independent PostgreSQL connections | probe.verify opens two libpq connections and checks distinct backend PIDs | Not application assessment concurrency certification |
| Row locking | Holder uses SELECT FOR UPDATE; second connection must receive LockNotAvailable under lock_timeout | Tests one controlled row conflict, not load/deadlock behavior |
| Commit and rollback | Second connection observes committed value1; attempted value9 is rolled back and value1 remains | Not worker-crash or submission-retry coverage |
| pgvector | Runner creates extension vector in template1; test_vector_and_transactional_ddl_rollback executes vector cosine distance and asserts zero for identical vectors | Not semantic retrieval quality, vector index performance or all dimensions/models |
| Transactional DDL | Same database test rolls back CREATE TABLE and asserts to_regclass returns null | Not every schema/data migration's reversibility |
| Initial migrations | Django test runner creates/migrates disposable test_sals_wp0 because TransactionTestCase requires a DB | Fresh database only; no production/historical-data upgrade proof |
| Reverse/forward migration | test_latest_spark_migration_backward_and_forward requires one Spark leaf, migrates to its same-app parent, then reapplies leaf in finally | Current inspected leaf0026 reverses to0025 then reapplies0026; not rollback of the whole project graph or historical-row preservation |
| Dependency graph | MigrationLoader.detect_conflicts returns {} | Conflict-free graph alone is not semantic correctness |
| Isolation/contracts/chunker | Runner selects four isolation tests, two DB tests, nine gateway contract tests and eleven chunker tests: 26 total, matching supplied result | Not all Spark tests, authentication, notification delivery or Phase1 acceptance |
| Cleanup | User reports test database destruction; runner finally removes its UUID container and temporary manifest | No independent artifact inventory supplied; this is reported cleanup plus source inspection |

The runner uses pgvector/pgvector:pg15 with tmpfs storage, a generated loopback port and NOSUPERUSER CREATEDB role. It never starts production Compose. It strips inherited settings/credentials and installs test-only HTTP/socket/libpq/task-dispatch guards; mock gateway/embeddings, memory cache/broker/storage/email are configured. These are scoped test safeguards, not a sandbox against arbitrary native code/subprocesses.

## Warning causes — no production authentication changes

### django-axes W002 and W003

`tests/sals_wp0/settings.py` reads the base INSTALLED_APPS literal, including axes, but intentionally defines a minimal MIDDLEWARE list without `axes.middleware.AxesMiddleware`. It does not define AUTHENTICATION_BACKENDS, so Django's default backend does not include `axes.backends.AxesStandaloneBackend`.

`.venv/lib/python3.14/site-packages/axes/checks.py` checks these entries and emits W002/W003. The existing production/base authentication configuration does include both entries (`pwaninet/settings/base.py`). Therefore these warnings describe the isolated WP0 profile, not evidence that production authentication is missing Axes. The selected WP0 suite does not test login lockout. No authentication settings were changed, and warnings were not suppressed. Future authentication tests require a dedicated profile accurately reflecting that behavior.

### No user_following table found

`users/migrations/0006_migrate_follow_relationships.py:migrate_follow_relationships` depends on0005_remove_user_following. It queries information_schema for the old M2M table and prints `No user_following table found - data cannot be recovered` before returning if none exists. In this fresh synthetic test database, there is no legacy table/data to recover. This is a non-fatal legacy migration message, not evidence of production data loss. It also does not prove successful historical relationship recovery; that would require a separate old-schema fixture test. No migration was changed.

## Remaining conditions and WP1 readiness

- Record the intended delivery commit, dirty-state manifest and exact runtime/PostgreSQL/pgvector image/extension versions in CI or the next reproducible run. No release branch was chosen by this update.
- Production deployed migration/runtime parity remains unverified. Fresh migration success is not permission to query production or deploy.
- Historical-data upgrade/rollback preservation, backup restoration, production authentication, full private-source access and worker recovery remain later gates.
- Phase1 submission race/idempotency/goal replacement tests are still unimplemented requirements; the generic row-lock probe is their foundation, not their substitute.
- Axes warnings are understood test-profile limitations; no need to change production authentication to clear them.

**WP1 validation-only work is ready to begin once explicitly authorized:** strict question/verdict validation, deterministic bounded numeric parsing, trusted-key validation and isolated unit/service tests. No additional database verification blocker is asserted for that bounded scope. Persistence changes remain subject to their own schema/concurrency/migration gates. WP1 was not started.

## Change scope

This verification update changes only `docs/spark/sals/wp0/READINESS_REPORT.md`. All pre-existing application, harness and documentation changes are preserved. No business logic, models, application migrations, prompts, production authentication, package installation, push or deployment changes were made.
