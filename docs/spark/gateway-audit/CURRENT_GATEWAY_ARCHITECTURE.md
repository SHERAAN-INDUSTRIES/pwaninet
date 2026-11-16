# Current Spark AI Gateway architecture

Audit date: **2026-11-15**. Scope: PwaniNet/Vasto Spark, working tree at
`/home/zayne/projects/pwaninet`. This includes the earlier, uncommitted provider
expansion checkpoint. Nothing in that checkpoint was enabled or completed by
this audit. Deployment state, provider authentication and live database registry
rows are **unverified**.

## Evidence and configuration boundary

Inspected source, tests, history and official documentation. Credential checks
returned only configured/missing status, using the shell environment and the
repository `.env`; values were never included in this report. Base settings load
that file through dotenv; inherited environment takes precedence. Other settings,
containers and running workers may differ. No Django production settings, live
DB connection, provider inference, registry seeding or service restart was used.

`PWANIMATE_GEMINI_API_KEY`, `PWANIMATE_GROQ_API_KEY`,
`PWANIMATE_OPENROUTER_API_KEY`, and `ZAI_API_KEY`: **configured**.
Both Spark-specific Cloudflare token/account settings and their generic aliases:
**missing**. Alibaba, Tencent and DeepSeek credentials: **missing**.
Expansion enable flags are false for all four checkpoint providers; expansion
model allowlists, entitlements and per-task chains are empty/default.
Environment overrides exist for the default provider and Gemini/Groq/OpenRouter
models; their values are deliberately withheld. The model IDs below are source
catalog/default IDs, not an assertion about effective deployed overrides.

## Request path and contracts

1. `pwanimate/api/views.py` resolves the selected Spark engine and tier, saves the
   user turn, and puts the engine's fallback chain into orchestration metadata.
2. `orchestrator/service.py` chooses conversational, tool/planner or RAG paths and
   constructs `LLMRequest`. Greetings can use the lightweight context path without
   investigation. Generation policies set task token budgets.
3. `ai/gateway/router.py:103` composes/deduplicates candidates, filters credentials,
   images, tier and expansion eligibility, applies named offerings, and prioritizes
   healthy candidates. Explicit provider/model requests lead where eligible.
4. `ai/gateway/gateway.py:48` or `:289` iterates candidates once, calls adapters,
   records holds and returns the normalized response or an exhaustion error.
5. Orchestration sanitizes/possibly continues output, attributes usage, and returns
   to the existing conversation/teaching/durable execution services. Those
   services own consent, persistence, evidence and run transitions.

`BaseLLMProvider` requires only `generate()` and local-only `is_available()`.
Streaming is duck-typed via optional `generate_stream(request, callback)`.
`LLMRequest` includes task, three message roles (system/user/assistant), context,
system instruction, overrides, temperature, token cap, metadata and attachments.
`LLMResponse` carries text, provider, API model ID, finish reason, token counts,
citations and metadata. There are no native tool-result messages, normalized
`tool_calls`, or first-class JSON schema fields. The planner requests JSON in
ordinary text and validates server-owned tool names/arguments; this is different
from a model-native function-call API.

## Provider inventory: implemented is not operationally verified

| Provider ID | Current implementation / local readiness | Native streaming in Spark | Native tools / enforced JSON in Spark | Usage |
| --- | --- | --- | --- | --- |
| `gemini` | Dedicated REST adapter; key configured, selectable when chain/tier allows | Yes | Not sent by current payload builder; planner uses prompted JSON | Provider usage plus thinking metadata |
| `groq` | Dedicated OpenAI-style REST adapter; key configured | Yes | Not sent by current payload builder | Provider token counts, reasoning details |
| `openrouter` | Dedicated OpenAI-style REST adapter; key configured | Yes | Not sent by current payload builder | Token counts/reasoning; no actual serving-upstream attribution |
| `cloudflare` | Workers AI `/ai/run/` adapter; Vision chain head; missing token/account | No; gateway emits the completed answer once | Not wired; platform capability differs by model | Tokens; optional neurons/cost estimate |
| `ollama` | Specialized local OCR `/api/generate`; not a general chat adapter | No | OCR JSON format only in structured mode; no tools | Local evaluation counts |
| `mock` | Always locally available; test and friendly-unavailable terminal rung | Simulated chunks | None | Estimated counts, not upstream usage |
| `zai` | Shared `compatible.py` checkpoint, key configured but disabled/no models enabled | Implemented SSE, mocked only | Explicitly false; critical tasks excluded | Counts and raw usage metadata |
| `alibaba` | Same checkpoint; key missing, disabled, no entitlement | Implemented SSE, mocked only | Explicitly false | Counts; remote quota unknown |
| `tencent` | Same checkpoint; key missing, disabled, no entitlement | Implemented SSE, mocked only | Explicitly false | Counts; remote quota unknown |
| `deepseek` | Same checkpoint; key missing, disabled; promotional gate only, paid routing blocked | Implemented SSE, mocked only | Explicitly false | Counts; no paid budget reservation |

No live auth or model availability is established for any row. Ollama service
reachability and installed model are unknown; it does not need a hosted API key.
The existing Cloudflare embedding adapter is separate from LLM generation and
shares the token/account setting names. Its source default is
`@cf/baai/bge-base-en-v1.5`; that is not a conversational model.

## Model and registry inventory

Three different catalog layers exist:

- `ai/engines.py`: code/settings catalog for Speed, Coda, Scholar and Vision.
- `PWANIMATE_LLM_FALLBACK_CHAIN`, vision/attachment chains, per-task chains and
  engine overrides in settings; models are mostly literal IDs.
- `billing.models.ModelOffering`: database offerings, tier, prices, capabilities
  JSON and active flag; `UsageRecord` stores provider/model and costs.

These layers are not automatically synchronized. **Actual database row existence,
active state and correctness are unknown for every provider in this audit.** Seed
and check commands exist; they were inspected, not run against a database.
`check_model_registry` checks engine/global pairs and prices; it does not prove
remote availability or comprehensively cover the checkpoint's per-task catalog.

Source global fallback inventory (21 real candidate pairs plus `mock:unavailable`):

- Gemini: `gemini-3.8-flash`, `gemini-3.7-flash`, `gemini-3.6-flash`,
  `gemini-3.5-flash-lite`, `gemini-flash-lite-latest`, `gemini-flash-latest`,
  `gemini-pro-latest`.
- Groq: `openai/gpt-oss-120b`, `openai/gpt-oss-20b`, `qwen/qwen3.8-27b`.
- OpenRouter: `nvidia/nemotron-3-ultra-550b-a55b:free`,
  `nvidia/nemotron-3.5-lightning:free`, `google/gemma-4-31b-it:free`,
  `xingchen-agi/xing4.0-29b-a4b:free`,
  `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free`,
  `liquid/lfm-3-1.7b:free`, `google/gemma-4-26b-a4b-it:free`,
  `liquid/lfm-2.5-2.6b:free`, `thinkingmachines/inkling-2-small:free`,
  `dots-studio/dots-3-note-preview:free`, `recursive/flint-v1.0:free`.

This is a source inventory, **not verification that every literal still exists,
is free for this account, or is suitable for teaching**. The paid Gemini Pro
rung is explicitly reclassified by the billing seed; comments claiming the entire
ladder is zero-cost are therefore unreliable evidence.

Vision source chain: Cloudflare `@cf/meta/llama-3.2-11b-vision-instruct`, Cloudflare
`@cf/meta/llama-4-scout-17b-16e-instruct`, configured Gemini vision model, then
Groq `qwen/qwen3.8-27b`. Local attachment extraction can use Ollama separately;
there are additional image-analysis chains, not one universal route.

Disabled checkpoint catalog: Z.AI `glm-4.7-flash`, `glm-4.5-flash`; Alibaba
`qwen3.8-flash`, `qwen3.7-plus`; Tencent `hy3`, `hy4`; DeepSeek `deepseek-flash`,
`deepseek-v4-pro`. Their seed command exists but was not applied here.

## Routing, capabilities and health

Engine chains in request metadata take precedence over per-task settings. Without
an engine chain, `PWANIMATE_TASK_FALLBACK_CHAINS[request.task]` can replace global
ordering. The default task policies are conversational/general 512, tool 1024,
RAG 4096 output tokens, with a fallback policy for unknown tasks.

Images are inferred from attachments and checked using `ai/capabilities.py`, which
is an image-only table, not a general task/tool/JSON registry. Gemini is broadly
allowed by prefix; Groq has a specific three-image limit. Cloudflare images require
configured model allowlisting; OpenRouter images are rejected by its adapter.
The checkpoint adapter has its own separate `eligible()` and `capabilities()`:
only general/conversational/summary/summarization; no images, tools, JSON or RAG.

`QuotaTracker` is a process-local singleton with a thread lock. Every candidate
gets independent provider:model keys; provider-wide holds also exist. There is
no fleet-wide request admission, proactive token bucket, remote quota balance,
or persistence of cooldown across process restart.

`QuotaTracker.get_status()` recognizes only Gemini/Groq/OpenRouter/mock credentials.
`engines._provider_configured()` additionally recognizes Cloudflare but not Z.AI
or other checkpoint providers. Engine status does not enforce every tier/task
capability constraint. An OpenRouter Nemotron entry is mislabeled as Gemini in
tracker metadata. These are display-health gaps, not changes to the actual API ID.

## Failure control map and observed exhaustion

| Concern | Controlling component / current behavior |
| --- | --- |
| Eligibility / priority | `LLMRouter.resolve_fallback_chain`, engine/settings chains, tier helpers |
| Transport / timeout | Dedicated adapter using requests; normally source default 2s connect / 30s read; not a whole-run wall clock |
| Failover | `AIGateway.generate` / `generate_stream`; one attempt per candidate |
| Cooldown | `QuotaTracker.record_failure`: 429 at least 60s default; other transient API/network failures 15s; Retry-After when supplied |
| Authentication / balance | HTTP 401/402/403 cause provider-wide indefinite holds; not all providers supply equivalent error detail |
| Planner retries | `StudyAgent.run`: 8 rounds, 15 tool calls, 90s deadline checked around work; at most 2 parse/non-gateway retries before observations |
| Exhausted planner | Current checkpoint no longer immediately retries `AIGatewayError`; may return to final response path |
| Stream failover | Before provider text: allowed. After text callback: return partial `interrupted`, flush buffer, no gateway-level splice |
| Exhaustion UI | Gateway errors or `mock:unavailable` text; orchestration/API error mapping, persisted incomplete notice and durable run error state |

Gemini 503 becomes `AIProviderAPIError` and a model hold. Groq 429 becomes
`AIProviderRateLimitError`; same-model calls are skipped until cooldown. Multiple
aliases on the same provider remain independently eligible, even if the outage or
daily quota affects the whole account. A final generation may follow a planner
failure and attempt remaining models. Once timeouts are serial across many models,
the planner's nominal deadline cannot interrupt a single long gateway invocation.

The tracker preserves Retry-After only if the adapter puts it in `exc.details`.
Cloudflare and the shared checkpoint do; current Gemini/Groq/OpenRouter HTTP 429
branches do not. Bounded backoff doubles an *unexpired remaining hold*, capped at
300s except longer Retry-After, rather than maintaining a robust consecutive-failure
counter. Normal routing skips held candidates, so this is not full exponential
backoff across successive post-cooldown failures. Duplicate `clear()` definitions
and obsolete comments remain maintenance debt; both clear the new holds today.

Z.AI business code 1113 is HTTP 429 insufficient balance; code 1302 is rate limiting.
The checkpoint discards business codes, treating both as short cooldowns. Invalid
models are generic 400/404 API errors with a 15s hold, not quarantined catalog
entries. HTTP 403 can mean budget or model eligibility, not just bad credentials;
the current provider-wide authentication hold can over-block usable models.

## Completion, persistence and cost boundaries

Gemini/Groq/OpenRouter/checkpoint retain raw finish metadata; absent terminal
reason becomes `unknown`, exhaustion `length`, exceptions after partial tokens
`interrupted`. `ai/completion.py` accepts only nonempty `stop`. TeachingService,
API persistence and durable execution apply that classifier alongside their
existing state/consent ownership. Native tools are not teaching completion.

Three exceptions require investigation before expansion:

1. Cloudflare defaults missing finish metadata to `stop`, omits the separate
   request `system_instruction` in its payload, and can report its default model
   instead of a request override when the response omits model ID.
2. `mock:unavailable` returns an outage notice with `stop`. It can pass the generic
   completion classifier. Whether a particular teaching transition also passes
   other guards depends on the caller; no database evidence was inspected.
3. `orchestrator/service.py:1454` may automatically generate one continuation for
   partial length/interrupted output, appending it to the same answer/callback.
   It uses original request overrides, not necessarily the serving model; fallback
   can switch providers. Token counts are added while original provider/model and
   terminal metadata remain. Gateway-only no-splice tests do not cover this path.

Usage is recorded by orchestration after generation. `UsageService.record_turn`
uses ModelOffering prices; unknown offerings are currently recorded at zero cost
with a warning. Multi-provider planner/continuation usage can be aggregated and
attributed to the final/original model instead of each actual invocation. Partial
stream counts can be estimated or missing. None is a hard paid-spend reservation.

There is existing Redis-backed `billing/global_budget.py` for image request counts.
It fails open on Redis errors, treats nonpositive configured limits as unlimited,
and resets by application-local day, while Cloudflare resets at UTC. It does not
meter all text+embedding+vision neuron usage. Reuse its infrastructure only after
reviewing those semantics; a zero-upfront guarantee must also rely on provider-side
Free plan restrictions.
