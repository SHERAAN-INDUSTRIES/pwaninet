# Spark AI Gateway expansion readiness audit

Date: **2026-11-15**. Investigation and planning only. Existing working-tree
changes, including the earlier expansion checkpoint, are inputs to this audit,
not changes made by it. The only intended new repository artifacts are the four
Markdown documents in this directory. Application source, `.env`, tests, registry
and service configuration were not written by this audit. Final timestamp review
found the latest non-audit file was the pre-audit `.env` update; source timestamps
also predated the audit. An attempted aggregate checksum baseline was truncated
by the tool output limit, so no byte-for-byte checksum comparison is claimed.

## Decision

The current gateway can support more providers **without a rewrite**. Recommend
a narrow Option B milestone: qualify direct Z.AI and curate existing OpenRouter
fallbacks, retaining Gemini/Groq. Cloudflare already has substantial Workers AI
implementation; treat it as a conditional second-stage repair/enablement project,
not a greenfield adapter. Defer Alibaba, Tencent and paid DeepSeek.

Do not enable additional academic routes until the existing completion,
continuation, attribution and capability gaps below have regression coverage.
Details and comparison are in [EXPANSION_PLAN.md](EXPANSION_PLAN.md).

## Most important findings

1. **Present code exceeds the initial assumed inventory.** The router recognizes
   ten IDs: eight hosted identities plus local Ollama and mock. Four hosted
   identities belong to the disabled earlier checkpoint. Z.AI's key is configured;
   all four expansion flags are false and allowlists empty. This audit did not
   implement, enable or remove that earlier work.
2. **Cloudflare is already implemented, but not locally configured.** Both LLM
   vision and embeddings adapters exist. Vision chains start with Cloudflare.
   Token/account metadata is missing in the inspected shell + `.env`; historical
   documentation saying credentials existed is not current proof. Live deployment
   credentials and account eligibility remain unknown.
3. **There are completion hazards before any expansion.** Cloudflare fabricates
   `stop` when finish metadata is absent. The unavailable mock rung also returns
   `stop`. Both can satisfy the generic completion classifier despite not proving
   completed academic teaching. Existing controller/consent guards still apply;
   no claim is made that historical evidence was actually corrupted.
4. **No-splice is a gateway property, not an end-to-end guarantee.** Gateway partial
   streams return `interrupted`, but orchestration can automatically append one
   continuation through the original request's fallback route. A different model
   can supply it. Combined usage/finish metadata then loses per-segment truth.
5. **Native tools/strict structured output are not implemented by the general
   request contract.** Current planning asks for JSON as text. Platform marketing
   claims are not adapter capability. The capability table is principally images;
   the checkpoint has separate conservative task gates.
6. **More aliases do not necessarily add capacity.** Global source defaults contain
   21 real pairs across only three chat accounts, plus friendly-error mock. Account
   exhaustion can cause many attempts across model-specific cooldown keys. OpenRouter
   adds another route but not necessarily independent hosting or additional daily
   quota for each model.
7. **Accounting is not a complete per-inference cost ledger.** Unknown offerings
   default to zero cost; multi-model planning/continuation usage can be assigned
   to one serving ID. A Redis global image budget exists, but it fails open and
   is neither an all-provider token budget nor a provider-side spending restriction.
8. **Registry state is not proven.** ModelOffering schema, admin-facing metadata,
   seed and consistency commands exist. No database was queried, so real rows,
   active flags, prices and access permissions are unknown. Source allowlists are
   not database registration, and key presence is not authentication.

## Architecture scalability assessment

The `BaseLLMProvider` generate/availability contract and normalized request/response
are sufficient for five, ten or more *text* provider identities. The current
router already names ten; another provider does not inherently require modifying
candidate iteration in the gateway. Multiple models per provider are supported
and deduplicated. A small factory/registration addition can be reasonable; a new
orchestrator is unnecessary.

Comfortable operational scale is more constrained:

- Provider factory, supported-name set, engine labels/health checks, task chains,
  quota display metadata and billing seed logic are separate update points.
- Engine catalogs and long global chains duplicate literals; schema/capability
  JSON is not universally enforced as runtime eligibility.
- Per-task configuration exists but is overridden by engine-provided chains, so
  adding a global/task model may not affect the selected public engine.
- Health is thread-safe but process-local. There is no proactive fleet-wide
  request admission, model lifecycle refresh, remote quota reconciliation or
  strong per-request wall-clock cap across the entire fallback chain.
- Generic OpenAI-style adapters are duplicated; the new shared transport borrows
  Groq's private payload builder. This can be factored narrowly later, without
  moving SALS ownership into transport code.
- Ten providers with a few qualified candidates each is structurally feasible;
  dozens of serial candidates and weak capability/account scoping are not a
  reliable availability strategy. Keep chains bounded and provenance explicit.

## Exact observed-failure interpretation

The owner-described sequence is consistent with the legacy behavior: Gemini 503
is an API error; Groq 429 is a cooldown; the planner historically allowed two
additional attempts before falling back to a final answer request. In the current
working tree, gateway errors no longer take those immediate planner retries.
Non-gateway parse errors still can. No new log collection or live reproduction
was performed, so the audit does not assert which revision was deployed during
the observed incident.

Now: a model-level 503 hold does not block every other model at that provider;
all-held candidates are skipped, but final answer orchestration may still call
the gateway after planning fails. Mock availability can hide real-provider
exhaustion. Without that mock rung the gateway raises a rate-limit or last-error
exception; generic messages do not always distinguish an operator hold from a
short wait. Cloudflare sends Retry-After details; Gemini/Groq/OpenRouter 429 paths
currently omit them. Mid-stream failures return before recording the candidate
hold, so partial failures do not necessarily affect the next request's health.

Z.AI's official 429 business codes distinguish insufficient funds from traffic
limits, but the checkpoint does not parse them. OpenRouter 402/403 metadata can
identify credit/budget causes; current generic HTTP classification treats all 403
as authentication and can indefinitely hold unrelated models on the account.
Invalid model 400/404 gets transient treatment instead of catalog quarantine.
Token-limit completion and partial stream termination are distinguishable via
`length` and `interrupted`; missing reasons remain unknown in the native streaming
adapters inspected, except the Cloudflare nonstream parser described above.

## Compatibility with SALS, WP4, SRI-01, R1 and R2

Additional text providers can stay behind the current interface without modifying
academic schema or consent systems, provided they preserve completion metadata
and do not bypass task/authorization boundaries.

- **SALS 0.1:** PersonalMemoryService uses owner-approved facts and consent-row
  serialization; provider expansion must not turn arbitrary generated text into
  approved memory.
- **SALS 0.2:** Conversation/API persistence checks completion and academic source
  ownership. Cross-conversation reading remains owner-scoped.
- **SALS 0.3 / WP1 / teaching:** TeachingService and mastery policy own phases,
  pending questions, consent and verified evidence. `stop` text is necessary for
  generated output but not sufficient for mastery. Controller-generated/cached
  lesson segments have their own existing completion path.
- **WP4:** Canonical Study Mode sessions, execution receipts and question ownership
  live outside providers. Preserve them across retries and pause/resume.
- **SRI-01 completion:** `ai/completion.py` permits nonempty explicit `stop` only.
  New provider finish reasons must not be defaulted to success.
- **SRI-01 durable outcome:** worker `application_success` requires completed run,
  durable message pointer, response completion and no error payload. Celery task
  return alone is not success. Attempt IDs and locks fence late/duplicate workers.
- **SRI-01 snapshots:** decoder supports versions 1–2 and rejects unknown fields,
  future versions and invalid prospective receipt ownership. Do not add opaque
  provider runtime objects, secrets or arbitrary new fields to run snapshots.
- **R1:** follow-up execution reuses saved source/receipts; consumed work replays
  saved results, interrupted work is fenced. A fallback must not execute tools or
  persist student messages a second time.
- **R2:** dispatch has consent revision/eligibility fences, claim tokens, leases,
  bounded jittered retries and a local deterministic extractor. Its retry policy
  is not the provider gateway's retry policy. Legacy transcript summaries are
  quarantined (`StudyMemoryService.refresh` returns false), not pending LLM work.
- **Greetings:** existing lightweight path is provider-independent; expansion
  should not add planner/retrieval calls to obtain a greeting.

No live student data was inspected to validate these invariants. Existing tests
supply regression evidence; the Cloudflare/mock/continuation exceptions need
additional coverage before widening academic routing.

## Testing capacity and audit execution

Mocked HTTP response testing is well supported. Existing suites cover adapters,
streaming, router fallback, engine status, usage metadata, local OCR/vision,
consent/teaching continuity, snapshots and durable outcomes.

A fresh, non-destructive audit run used only existing SimpleTestCase suites:

```bash
PYTHONDONTWRITEBYTECODE=1 \
DJANGO_SETTINGS_MODULE=tests.spark_memory.settings \
.venv/bin/python manage.py test \
  tests.gateway_expansion \
  pwanimate.tests.test_streaming.GatewayStreamingTests \
  pwanimate.tests.test_engines.SparkEngineRegistryTests \
  pwanimate.tests.test_engines.EngineStatusRollupTests \
  --noinput --verbosity 0
```

**30 tests ran: 29 passed, 1 failed.** The failure was
`SparkEngineRegistryTests.test_every_vision_rung_is_image_capable`: the standalone
settings do not load base vision allowlists/default Cloudflare model settings,
so the first Cloudflare vision pair is rejected. Base configuration does list
both Cloudflare models. This demonstrates a test-configuration dependency, not
proof of deployed failure. It was not fixed or suppressed. The run also reported
the isolated settings' existing django-axes middleware/backend warnings. These
SimpleTestCase suites did not create or connect to a database or use owner keys.

No tests were created. No full-suite pass is claimed. Earlier checkpoint reports
of legacy failures are historical evidence, not fresh audit results.

### Coverage inspected and later additions needed

- Success/token/finish: `pwanimate/tests/test_gateway.py`, `test_streaming.py` and
  `tests/gateway_expansion/test_contracts.py` provide mock success, terminal/EOF,
  token exhaustion, missing-key, rate/auth/server error and transition tests.
- Cloudflare: `test_vision_serving.CloudflareProviderTests` covers availability,
  endpoint, image payload, agreement error, 429, empty result and neurons. It
  lacks strict missing-terminal, system-instruction, override-model and native
  stream interruption coverage. These are TestCase tests and were inspected,
  not run in this no-database selection.
- Fallback: current tests cover Gemini→Groq and independent named transitions,
  all-exhausted candidates, Retry-After/date, task-chain precedence and no gateway
  splice. Some transitions use provider doubles rather than each real wire adapter;
  more route-specific contract fixtures are needed.
- Usage: provider counts and billing tests exist; add per-planner/continuation
  attempt attribution, unknown-price failure, missing trailers and partial-cost
  truth. Do not rewrite historical rows to make reconciliation pass.
- Quota: add Z.AI business 1113/1302/1305, OpenRouter `limit_source`, account-wide
  versus model limits, day expiry and correct retry times. Existing HTTP-only
  fixtures do not prove business-error classification.
- Capabilities: add exact-model tool/JSON/task qualification, tier/disabled model
  filtering on all entry points, named-offering gates, privacy eligibility and
  engine-status consistency. Catalog discovery should be mocked and reviewed.
- Persistence/academic completion: `tests/sals_wp4/test_sri_repairs.py`, SALS 0.2,
  SALS 0.3 WP1/WP2, memory and follow-up suites cover partial noncompletion,
  pause/reconnect, cancellation, receipts and historical context. Add Cloudflare
  missing-stop, outage-mock and cross-model orchestrator continuation scenarios.
- Planner exhaustion: add a direct assertion that no repeated planning happens
  when every real provider is unavailable, including mock-outage text and
  parse-failure interactions. Current bounded gateway-error logic is not enough
  to claim every planner exhaustion flow has full coverage.

For later database verification, existing `tests/sals_wp0/run.py` provisions a
throwaway PostgreSQL/pgvector container, strips inherited credentials and uses
isolation guards. Existing `scripts/test-gateway-expansion-postgres.sh` and SALS
runners can be reused. None was executed during this audit; no production or
owner PostgreSQL test command was run. Live probes remain separately authorized
synthetic checks, never prerequisites for offline contracts.

## Evidence gaps and deployment constraints

- No DB query: actual registry rows/prices/activity and user-tier access unknown.
- No runtime inspection: active process version/config, cooldowns and actual
  default model overrides unknown. Local metadata is not service readiness.
- No provider requests: authentication, live catalog entitlement, real trailers,
  account balance, geographic access and latency/quality remain unknown.
- Public GLM family capability claims are broader than proof for each free API ID.
- OpenRouter's actual host and data restrictions are not preserved by Spark;
  available models do not all qualify for personal/academic context.
- No guarantee of permanent free capacity or automatic account-free-only settings
  outside explicitly documented mechanisms.
- Existing image/usage budgets cannot independently prevent upstream charges.
- Existing settings/transport include legacy error/logging and secret-hygiene
  concerns. Do not expose raw response bodies, request exceptions containing
  credential-bearing URLs or Authorization tracing when extending them.

## Files inspected and evidence anchors

Paths below are relative to repository root. Key anchors are current working-tree
line locations; functions are the more stable references.

- `pwanimate/ai/gateway/base.py`; `types.py:92` request and `:134` response;
  `gateway.py:48` generate, `:289` stream; `router.py:103` candidates and `:290`
  tier gates; `quota_tracker.py:59` holds and `:145` status.
- `pwanimate/ai/providers/{gemini,groq,openrouter,cloudflare,ollama,mock,compatible}.py`;
  Cloudflare `:113` payload and `:243` parser; mock `:40` response; compatible
  provider/model catalogs and eligibility. `ai/exceptions.py`, `ai/completion.py`.
- `pwanimate/ai/embeddings/cloudflare.py`, `ai/capabilities.py`, `ai/engines.py:182`
  configuration health and `:205` status rollup, `ai/vision_images.py`.
- `pwaninet/settings/base.py` dotenv/config/LLM/vision/expansion settings; settings
  selector/local override locations; `.env` credential-presence metadata only.
- `pwanimate/billing/models.py:77` ModelOffering; `billing/services.py:80` offering
  index, free-route/tier helpers and `record_turn`; `billing/global_budget.py`;
  management `seed_model_registry.py`, `check_model_registry.py`,
  `seed_gateway_expansion.py`; no command was applied to a registry.
- `pwanimate/orchestrator/agent.py:132` limits and `:335` retries;
  `orchestrator/service.py:1356` usage and `:1454` continuation;
  `pwanimate/api/views.py:814` engine selection and `:895` persistence gates.
- `pwanimate/services/{teaching,agent_execution,agent_runs,run_snapshots,followups,
  personal_memory,persistent_memory,study_memory,conversation_memory,memory_dispatch}.py`;
  `tasks/{agent_runs,conversation_memory}.py`.
- `pwanimate/tests/{test_gateway,test_streaming,test_engines,test_vision_serving,
  test_multi_provider_embeddings}.py`; `tests/gateway_expansion/test_contracts.py`;
  `tests/sals_wp4/test_sri_repairs.py`; standalone settings and PostgreSQL runners.
- `docs/ai-serving-architecture.md`, existing gateway-expansion report;
  Git history: `0e49ce7` introduced Workers AI vision work, following `f2d4c7a`
  streaming work. Historical comments are not evidence of current live success.

## Official references and next milestone

The dated source register in [PROVIDER_READINESS_MATRIX.md](PROVIDER_READINESS_MATRIX.md)
contains official URLs for every current pricing/free-eligibility claim, endpoint,
model candidate, token procedure and deferred-account constraint. Verification
date is 2026-11-15; live/account-specific gaps are explicit.

Next milestone: **bounded reliability repair + direct Z.AI qualification + curated
OpenRouter routing**, with separate owner approval for any live synthetic probes.
Cloudflare account preparation and adapter hardening can follow without replacing
Spark's gateway or altering SALS/WP4/R1/R2 schemas. Stop here after documentation.
