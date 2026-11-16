# Spark provider expansion plan — proposal only

Date: **2026-11-15**. No implementation, enablement, registry changes, credential
changes, live probes or service actions are authorized by this document.
Official pricing/capability evidence and account uncertainties are recorded in
[PROVIDER_READINESS_MATRIX.md](PROVIDER_READINESS_MATRIX.md).

## Recommendation

Choose **Option B in a bounded implementation milestone**: qualify the existing
Z.AI checkpoint for direct free-model text fallback and curate a small OpenRouter
set. Preserve Gemini/Groq. Make the existing completion/continuation hazards
release blockers. Add Cloudflare text streaming later under **conditional Option
C**, after the owner confirms Free-plan eligibility and supplies a least-privilege
token/account configuration. Do not activate Alibaba, Tencent or paid DeepSeek.

The improvement is a genuinely separate direct endpoint using an already-held
Z.AI key, rather than merely increasing model aliases at the same rate-limited
account. Additional OpenRouter entries provide model/host diversity, not a
multiplication of remote daily quotas. Cloudflare is promising independent hosting,
but is neither credential-ready nor completion-safe in its current adapter.

## Option comparison

Effort is relative to this working tree, which already contains a disabled shared
adapter. No estimates assume live access, model quality or a free-capacity SLA.

| Dimension | A: configuration only | B: direct Z.AI + curated OpenRouter | C: B + Workers AI | D: broader provider set |
| --- | --- | --- | --- | --- |
| Engineering | Lowest for text chains; not sufficient for strict JSON/stream contracts | Low/moderate; qualify existing transport and gates | Moderate; repair existing Cloudflare adapter and add streaming/text profiles | Highest; account-specific adapters, entitlement and pricing policy |
| Reliability | More choices behind existing accounts; shared exhaustion remains | Meaningful endpoint diversity; still no free-tier SLA | More independent hosting and a separate account allowance | Potential diversity, constrained by readiness and operational complexity |
| Independence | Depends on OpenRouter's actual serving vendors | Direct Z.AI adds a separate API/account | Cloudflare-hosted inference adds separate serving capacity | Varies; aggregators may overlap other endpoints |
| Compatibility | Existing text API; no arbitrary configurable base URL exists today | OpenAI-style protocol with Z.AI-specific errors/options | Native Workers AI already exists; OpenAI text endpoint also possible | Nominal OpenAI compatibility, divergent metadata and account errors |
| Free capacity | Shared OpenRouter request allowance, not per-model additive | Adds documented zero-price GLM models with account limits | Adds eligible Workers Free allowance, shared by embeddings/vision/text | Primarily time-limited trial/promotional allocations or metered usage |
| Security | No new key; still needs upstream data-policy restrictions | One server-side key already configured; sanitize business errors | New account-scoped Workers AI token; no DNS/gateway-management access | More keys, regions, processors and review requirements |
| Cost risk | Low only with exact free IDs, tier gates and no paid alias fallback | Low with exact GLM free allowlist; exclude built-in paid services | Low on verified Free plan; elevated if already on Paid plan | Elevated: postpaid/trial expiry, frozen balance and price/account uncertainty |
| Tests | Curated model/capability and exhaustion tests | Adapter/error/finish/usage + critical-workload qualification | Additional SSE, system prompt, model override, budget/plan tests | Larger entitlement and failure matrix |
| Maintenance | Lowest initial effort, catalog churn remains | Manageable small catalog plus one provider-specific error profile | More model lifecycle and account-limit monitoring | Largest; avoid until demand and access justify it |

Option A alone cannot solve all observed failures: OpenRouter is already present,
its shared account quota remains finite, and no existing adapter supports an
arbitrary owner-configured OpenAI base URL without code changes. Option D is not
justified for the owner's current zero-upfront objective.

## Proposed implementation order and release gates

### 1. Establish safety baselines before adding traffic

- Resolve Cloudflare's fabricated `stop`, missing separate system instruction,
  and default-model misattribution. Preserve old unrelated work.
- Ensure `mock:unavailable` remains an outage outcome and cannot finalize an
  academic turn or durable run as successful inference.
- Inspect and repair the *orchestrator* continuation path, not only the gateway:
  after visible partial text, use the existing durable continuation lifecycle;
  never silently splice another serving model. Preserve segment provenance and
  terminal metadata. Do not redesign Study Mode or snapshots.
- Capture the existing isolated test-settings gap (Cloudflare image allowlist)
  separately from actual production behavior. Reconcile tests that still expect
  `max_tokens` rather than canonical `length` when encountered later; do not
  loosen SRI-01 to satisfy old expectations.

These are existing transport/application-contract defects, not a reason to replace
the gateway. Do not advertise full reliability merely because a mock returns text.

### 2. Qualify Z.AI using the existing interface

Use `CompatibleLLMProvider('zai')`, current `LLMRequest`/`LLMResponse` and existing
router; do not create another router, orchestrator or duplicate four clients.
Start with `glm-4.7-flash`, retaining `glm-4.5-flash` only after separate checks.
Transport already exists, but current enable flags/model allowlists are empty.
Registry/configuration alone could expose *basic* text; it cannot establish safe
critical-workload support.

Required later work:

- Validate/remove unsupported streaming options and capture terminal usage without
  requiring OpenAI-specific flags unverified for Z.AI.
- Preserve raw finish reasons; map sensitive/filter/network/context failures
  conservatively without turning them into `stop`.
- Parse sanitized business error codes, especially balance 1113 versus rate 1302;
  avoid repeated retries of an exhausted account. Do not log raw bodies/headers.
- Add model-specific capability/task profiles, source URLs and verification dates.
  Keep unsupported tools/JSON/images explicit. An existing successful prompted
  JSON planner response is not proof of enforced schema support.
- Update health/engine/config registry visibility consistently; existing status
  helpers do not recognize Z.AI, and engine metadata chains override per-task
  settings. An enable flag alone will not add it to Speed/Scholar/Coda.
- Register exact API IDs independently from display names, with verified prices
  and tier access; preview any later seed operation before owner-run application.

### 3. Curate OpenRouter rather than expanding the whole ladder

Evaluate a small text/reasoning/code set from the readiness document. Review
upstream privacy/retention and serving-vendor independence before student use.
Do not send personal-memory context to endpoints warning against personal data.
Retain exact free IDs; never strip `:free` or auto-substitute a paid sibling.

A later admin catalog refresh should use the official model schema, cache it,
intersect with an approved allowlist, enforce known-zero pricing and lifecycle
limits, and require review before enablement. Do not discover/enable models on
every student request. For JSON/tool-critical routes, verify the exact endpoint
and use provider parameter requirements only after the adapter can express them.

### 4. Add Cloudflare only when account-ready

Reuse the existing direct Workers AI adapter for vision and extend its text path,
or factor a narrow shared text transport once protocol tests justify it. Keep
native `/ai/run/` vision behavior separate; do not migrate all providers merely
for abstraction symmetry. The current shared adapter has fixed provider bases,
so Cloudflare OpenAI support is not a configuration-only change.

Owner prerequisites are in the readiness document. No DNS credentials, token
creation, license acceptance, billing upgrade or paid-plan activation belongs in
a coding agent's automatic implementation flow. Verify the chosen model is still
Free-plan eligible and not scheduled for deprecation. Preserve distinct direct
Z.AI versus Cloudflare-hosted GLM provider/model attribution.

### 5. Defer funded integrations

Keep disabled Alibaba/Tencent/DeepSeek checkpoint entries inert. Later funded
work requires explicit account/region entitlement, expiry, provider-side spending
restrictions, verified pricing, approved budget policy and tests. Do not change
academic schema or student history to accommodate inference providers.

## Capability and routing strategy

Keep one router. Add small model profiles/validated metadata to existing registry
infrastructure, with a single authoritative eligibility decision shared by routing
and the selector. Avoid a second taxonomy alongside current task strings.

Initial qualification tiers:

- Basic text: conversational/general and bounded summary; no tools/images/JSON.
- Grounded teaching/RAG: academic reasoning/grounding, explicit terminal `stop`,
  known token ceilings and no unapproved retention. Teaching controller still
  decides state transitions; transport cannot grant consent or mastery.
- Planner/structured output: exact planner schema, malformed/truncated output,
  unknown tool names, permission checks and model capability validated together.
  Native function calls would require additive request/response contract support;
  they are not necessary merely to preserve current JSON-in-text planning.
- Memory: preserve local deterministic extraction and quarantined legacy summary
  jobs. Provider expansion must not introduce a new automatic extraction purpose.

Task chains should be short, capability-qualified and interleave independent
accounts. Preserve explicit safe overrides and tier checks. Candidate/model
cooldowns alone do not handle account-wide quota; add a reason/scope distinction
for overload, rate, quota expiry, balance, authentication, invalid model and
budget blocks. Keep Retry-After and operator-recoverable holds visible.

The current planner already avoids immediate `AIGatewayError` retries. A later
bounded availability check or shared exhausted-result signal can stop even one
pointless planner/final call when no real candidate is eligible. Keep parsing
retries distinct from unavailable-provider retries. Add an overall deadline and
attempt cap within existing gateway execution rather than allowing many serial
30-second reads. Do not sleep through request threads or add automatic replay of
academic side effects.

## Cost and accounting protections

- Fail closed on unknown prices/entitlements for new routes; remote Free-plan or
  free-only restrictions are necessary because local checks cannot undo charges.
- Separate documented zero-price models from temporary quota/promotional funding.
  Neither low price nor a free chat website is permanent API entitlement.
- Record every actual provider/model attempt or generation segment; do not roll
  tokens from two serving providers into one offering. Preserve existing records.
- Reuse `UsageRecord` and existing budgets where appropriate. The Redis image
  budget is a useful starting point but fails open, counts images rather than
  all neurons, and uses local rather than UTC reset. It is not a ready-made hard
  token/currency reservation system. No new financial guarantee should be claimed.
- Exclude provider keys/Authorization from diagnostics and tracing; sanitize
  existing raw error-body/request-exception paths before expanded deployment.

## Verification, owner approval and rollback

Later isolated mocks must cover success, missing/unknown terminal reason, length,
provider business errors, invalid models, partial disconnect, 401/403/402/429/503,
Retry-After, skipped credentials, task/tier/cost gates, all-provider exhaustion,
no immediate planner loop, no cross-model stream splice and correct per-attempt
usage. Include Cloudflare system/model overrides and no fabricated `stop`.
Run the existing SALS/WP4/SRI/R1/R2 regressions against disposable infrastructure;
use PostgreSQL locking tests only with the existing guarded container runner.

Only after explicit owner approval: one model at a time, synthetic nonpersonal
prompts, tightly capped nonstream/stream calls, no cascade, print only sanitized
metadata/counts, and verify account quota/billing state. A key being supplied is
not authorization for a live probe.

Rollback should disable the new provider/model flags and remove only its reviewed
chain entries, keeping Gemini/Groq/OpenRouter and existing durable runs intact.
Do not rewrite usage, delete messages, reset teaching state or downgrade snapshot
versions. Synchronize registry availability with flags, preserve old API IDs for
historical reading, and recheck active workers' configuration after an
owner-controlled deployment. No rollback action was performed during this audit.
