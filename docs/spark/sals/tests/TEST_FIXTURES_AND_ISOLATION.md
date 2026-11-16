# Fixtures, test architecture and isolation

Specification only; no tests executed/created. Source baseline: `pwaninet/settings/test.py` imports local settings and uses SQLite :memory:, while production selects PostgreSQL and pgvector. Existing tests predominantly use Django TestCase, APIClient, ORM setUp and unittest.mock; `pwanimate/tests/test_study_assistant.py:response` constructs LLMResponse(provider='mock'); `test_study_mode.py` builds explicit messages/documents. Reuse conventions. The document_factory referenced by `documents/tests/test_signed_downloads.py` was not located in bounded conftest inspection; do not assume it exists.

## Test layers

| Layer | Isolation and responsibility | Case families |
|---|---|---|
| Pure unit | No Django DB/provider; strict JSON, numeric grammar and reference comparators | V01–V03,V05–V06,V10–V15 |
| Service | Controlled gateway fake; explicit owned domain objects | V04,V07–V08,P01–P10 |
| Authenticated API | APIClient with force_authenticate for ownership, separate real session/CSRF smoke | V09,V16–V18,D08,E,K |
| Database integration | Disposable PostgreSQL with supported pgvector extension | D/P/E; constraints and persisted counts |
| Concurrent transactions | TransactionTestCase or equivalent transactional pytest with independent real connections/threads/processes | C01,C03–C04,C06,C10,C12–C13 |
| Worker retry/consent | Explicit attempt tokens, fake clock/provider, controllable broker/worker boundary | C07–C08,C11,E04,E09,K04 |
| Browser | Disposable app instance + fake provider + synthetic users/files | B01–B05 |
| Migration | Disposable old/new schema DB; historical models via MigrationExecutor | M01–M05 |

SQLite can run truly pure or compatible lightweight tests, but never produces a pass for PostgreSQL locking/vector/conditional-constraint gates. No production DB credentials inherited. Test DB host/name allowlist and distinct unprivileged role; fail if configured DB is production or schema/shared volume is unexpected. Test harness performs migration/setup later under separate implementation authorization, not in this assignment.

## Deterministic fixture contract

Actors: owner A, unrelated B, staff S, superuser X, PRESIDENT P, DELEGATE D, shared recipient R, enrolled E and unenrolled N; combine role with ownership deliberately. Two institutions I1/I2, programmes, unit MAT-A/MAT-B, same-looking custom labels under different private UUIDs, active/revoked enrollment. Use no real names/emails/docs.

Resources: public/private/restricted documents with ready/processing/archived states; versions v1 historical/v2 latest; file F1 belongs A's doc and F2 another doc; known unique sentinel text per owner/unit/category for leak detection; text-only/pending/failed/completed embedding chunks. Deterministic valid dimensional vectors exercise actual pgvector filtering without remote embedding service. Collection membership never implies read grant. Proposed grants only in future-sharing tests.

Assessments: immutable five-question MCQ/numeric fixtures, stable UUID/qids, known valid reviewed/computed keys and objective v1/v2. Determinant fixture can use diagonal matrix diag(2,3,4) with independently checked determinant24 and a non-diagonal independently reviewed case to avoid only trivial examples. Numeric 1/2 equivalence, zero, tolerance boundaries and forbidden expression payloads. Wrong-key provider fixture must have valid JSON but fail reference verification. Substantive wrong student explanation, passive acknowledgement, practice and retention labels distinguish evidence eligibility.

Fake responses: exact true/false, strings, null/missing, duplicate JSON keys, truncated object, count mismatch, key mismatch, timeout, rate limit and provider exception. Patch the service's injected gateway/factory and fail any unpatched adapter/network call, including embeddings, OCR, TTS, welcome/digest and fallback candidates. No paid or live provider calls. Provider explanation is optional and must not change deterministic outcomes.

## Concurrency protocol

Use synchronization barriers at reservation/goal swap/finalization, not sleep-based timing. Each worker opens/closes its own DB connection; TransactionTestCase avoids parent TestCase transaction hiding rows. Assert both return payloads AND committed rows/counts with a third fresh connection. Database constraints, not a mock lock, decide winner. Deterministic timeouts fail cleanly without hanging CI. Add rollback/IntegrityError tests via savepoints. For worker crash cases use a disposable subprocess/worker killed at a known barrier or explicit transaction rollback; do not claim raising a sequential mocked exception proves process recovery. Lease reclaim/late worker require separately observed tokens and terminal DB state.

## Side-effect containment

Temporary media/storage root, fake private object storage/signed delivery, isolated cache/channel layer/Redis namespace or in-memory substitutes where not testing Redis. Stub notification/email/push and Celery dispatch; tasks only run explicitly in intended worker cases. CELERY_TASK_ALWAYS_EAGER alone is insufficient and may call real providers. Clear global ToolRegistry/cache/provider state between tests. Freeze clock for consent expiry/goal ordering; never mutate production env/settings. Browser traces redact answers/keys; use separate storage for each actor and clear client cache on account switch.

## Evidence reporting

Report case ID/parameter, source commit, contract version, DB/server/runtime versions, executed layer, fixture identity, failures and intentional future-gate skips. A future capability disabled test is distinct from a passing approval/export implementation test. Migration and browser evidence must be retained for review without private content. This design prescribes tests; it reports no pass count, benchmark or operational measurement.
