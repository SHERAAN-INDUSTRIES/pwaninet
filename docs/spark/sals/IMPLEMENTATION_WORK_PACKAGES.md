# Ordered implementation work packages

Proposals for later explicit authorization only. No delivery times estimated. Each package is separately reviewable; schema names are logical proposals. Common invariant: authenticated owner, no answer-key leak, no Workspaces, existing routes/chat/document context preserved.

## WP0 — Baseline and isolated test harness

Scope: select intended release baseline and verify runtime/migration dependencies; configure disposable PostgreSQL/pgvector, fake providers, no network, temporary storage/tasks. Affected: future isolated test settings, current pwaninet/settings/test.py conventions, CI configuration; do not alter runtime settings just for convenience. Schema/API: none in production. Dependencies: owner-selected branch/commit; inspect sanitized deployment manifest later. Tests: M05 and isolation guards. Acceptance: concurrency harness uses real connections; no production/paid side effects; actual versions recorded. Compatibility: existing SQLite unit runner can remain. Rollback: remove isolated runner config only. Risk: medium; blocks schema acceptance.

## WP1 — Strict grading and publication validation

Scope: strict JSON/types/count/options/keys; deterministic numeric parser and reviewed/computed templates, numerical zero handling; reject unsupported types. Affected: services/assessment.py, api/study_assistant.py input validation, existing gateway injection. Schema/API: validators can be implemented without DB changes behind inactive v2 contract; explicit validation errors. Dependencies: WP0; trusted reference fixtures. Tests: V01–V17, wrong-key verifier. Acceptance: string false never grants credit, invalid generation never publishes, unsafe expressions never execute, no LLM fallback for invalid MCQ. Compatibility: legacy reads untouched; unsupported old write receives clear error. Rollback: disable new writes, never restore unsafe certified grader. Risk: medium.

## WP2 — Uniform document policy and delivery

Scope: shared authorization plus all adapters/assets/inventory/file membership; remove elevated private bypass. Affected: document_retrieval.py, pwaninet_search.py, UnifiedSearchService, study_session.py helper, study_materials.py, documents/permissions.py, api.py, views.py/private_storage.py. Schema/API: owner-only first; no new grant table until sharing release; read interfaces preserve existing routes. Dependencies: WP0; storage delivery inspection. Tests: D01,D03–D08, current permissions tests revised intentionally. Acceptance: identical deny across paths, no foreign file substitution or stale inventory. Compatibility: public/restricted intended entitlements remain; private role privilege removal is intentional. Rollback: fail closed/private features off, do not restore broad private reads. Risk: high, targeted security package.

## WP3 — Consent cutover and legacy quarantine

Scope: off-by-default purpose-aware recall, no extraction until individual approval, revocation/job fences including failure writes. Affected: models SparkMemoryPreference if consent revision required, study_memory.py, conversation_memory.py, related tasks and MemoryView; existing welcome/settings consumers. Schema/API: additive consent revision/purpose metadata as needed; current off/forget controls retained with accurate semantics. Dependencies: WP0; independent from assessment schema. Tests: E01,E04–E05,E10. Acceptance: old enabled flag cannot opt users in; no delayed recreation; active chat works. Compatibility: retain transcripts and user inspection; defaults intentionally change. Rollback: disable recall/extraction; never re-enable unapproved legacy memory. Risk: high privacy.

## WP4 — Detach academic lifetime and objective identity

Scope: session.conversation nullable SET_NULL, immutable objective versions/active pointer, stable private unit UUID. Affected: models.py, StudySessionService.set_target/context/resume/report, both conversation deletion endpoints, serializers/views/templates that assume conversation non-null. Schema/API: additive objective version; FK alteration; existing sessions readable with null conversation. Dependencies: WP0, validated graph; merge compatibility reader changes with FK switch before exposure. Tests: E07,M01–M02,P08–P09; both UI/API deletion routes and detached report/context. Acceptance: chat deletion preserves academic owner records and no dead link crash; new goal excludes old evidence. Compatibility: no silent transcript recreation; existing session IDs and enrolled unit FKs preserved. Rollback: compatibility build and flag-off, never old CASCADE binary. Risk: high data preservation.

## WP5 — Immutable question/submission/evaluation persistence

Scope: normalized published questions, accepted answer payloads and append-only outcomes, owner/unique constraints; JSON compatibility snapshot only. Affected: models.py, AssessmentService, AssessmentView, future schema migrations after approval. Schema/API: three narrow tables plus assessment version/certification/generation identity; v2 status/read contract. Dependencies: WP1,WP4. Tests: V18,M01,M04, question owner/version mismatch and key secrecy. Acceptance: no application path overwrites answer history; legacy JSON remains readable/uncertified. Compatibility: migration does not certify legacy rows; normalized outcomes authoritative. Rollback: disable v2 writes/read-only compatibility; preserve new tables/data. Risk: high.

## WP6 — Atomic admission, retry and recovery

Scope: owner-scoped idempotency, reservation uniqueness, attempt leases, short finalization, deletion/goal/policy fencing; generation reservation too. Affected: AssessmentService/AssessmentView; small assessment task/recovery adapter reusing current Celery conventions; no new orchestration. Schema/API: unique request identities/leases included WP5, required v2 key/version and pending status response. Dependencies: WP5; recovery runner configuration verified. Tests: C01–C13 real PostgreSQL, broker loss and late worker. Acceptance: both parallel question answers survive; competing same question scored once; goal race cannot credit new goal; pending recovered after crash. Compatibility: v1 unambiguous adapter only; no exactly-once external billing claim. Rollback: pause admissions, drain/fence pending, retain authoritative evidence. Risk: high concurrency.

## WP7 — Honest score projection and API compatibility

Scope: distinct counts/completion/accuracy/mastery, explicit legacy labels, source/key eligibility; no automatic certification. Affected: AssessmentService/public serializers, StudySessionService.recompute_progress and reports, assessment GET/result payload. Schema/API: v2 metrics envelope; derived projection initially no table. Dependencies: WP4–WP6. Tests: P01–P09,K02–K03,K06,V18. Acceptance: 1/5 example20% completion/100%(1/1)accuracy/insufficient mastery; zero graded=null; practice/legacy isolated. Compatibility: preserve v1 read shape with legacy metadata; never silently rename answered semantics. Rollback: history/read-only compatibility, do not restore misleading mastery. Risk: medium.

## WP8 — Academic export/delete and anti-resurrection

Scope: minimal owner-scoped academic export/scoped deletion, pending-job generation fences and private delivery. Affected: new thin service/API actions alongside existing study endpoints, report/artifact deletion and retention service; source/detached-reader handling. Schema/API: explicit scope/confirmation and optional deletion generation marker; use job table only if asynchronous volume proves needed. Dependencies: WP4–WP6; approved operations backup purge SLA. Tests: E08–E09,D08,C13. Acceptance: owner can retrieve retained evidence independently and delete it separately from chats; no background rebuild/export after deletion. Compatibility: no transcript deletion side effect, no active answer keys in export. Rollback: disable new exports/writes while honoring deletion; do not restore deleted data from mirrors. Risk: high privacy/data lifecycle.

## WP9 — Thin frontend compatibility and regression gate

Scope: existing quiz renderer v2 negotiation, select-then-submit/pending/error/retry, null/inconclusive states and honest labels; preserve viewer/shell/theme. Affected: static/js/spark-study-sessions.js, existing session templates/score labels, minimal relevant settings. Schema/API: consume WP6–WP8, no new frontend framework. Dependencies: WP2–WP8 required gates. Tests: B01–B05,K01–K06,M03 and existing chat/document/continuation regression families. Acceptance: reload uses persisted outcome, same retry key, one question submission explicit, mobile/keyboard/themes usable, disabled features allow history. Compatibility: v2 enabled only for compatible client; general planner/study routing unchanged. Rollback: compatible read-only UI and feature flags. Risk: medium.

## Deferred packages

Personal fact candidate/approval UI/model, explicit sharing UI/grants, automatic mastery/retention calibration, richer reasoning rubric, assignments and full mock exams remain disabled until future gates GF1–GF3 and separate product scope. Unit roadmap/teaching expansion follows integrity foundation; no broad refactor is justified. A plain validated outcomes demo may ship before retained-mastery certification, with honest labels and all applicable privacy/integrity gates passed.
