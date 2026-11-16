# Provider readiness matrix

Verified **2026-11-15** from source/configuration metadata and the official URLs
below. “Configured” is local credential presence, not successful authentication.
Account region, sanctions/service eligibility, entitlement, balance and deployed
configuration remain unknown unless explicitly stated. A free offering today is
not a promise of permanent availability. No provider/account API was probed.

## Integration and capability matrix

| Provider | Current support | Free eligibility / zero-upfront fit | Auth readiness | Streaming | Tools / JSON | Remaining effort | Recommendation |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Gemini | Existing dedicated adapter, chat/RAG and vision routes | Provider Free tier is model/project dependent [G1]; owner tier unknown | Configured | Native | Upstream features not exposed as native gateway tools/JSON | Maintain; catalog/account audit | Retain existing fallback |
| Groq | Existing dedicated adapter, chat/code and limited vision | Free tier exists; paid Developer upgrade needs payment method [G2] | Configured | Native | Current adapter uses text, including planner JSON prompts | Maintain; propagate error metadata | Retain existing fallback |
| OpenRouter | Existing dedicated adapter, many literal `:free` rungs | Zero-price variants; shared request caps, no guaranteed capacity [O1–O3] | Configured; live operation unknown | Native | Models can support tools/structured output; adapter does not send those fields | Low for curated text models, moderate for strict structured contracts | Expand selectively, not by adding every free ID |
| Z.AI | Shared compatible adapter checkpoint; disabled, no enabled models | Two GLM Flash models documented free; no prepaid inference amount listed for them [Z1] | Key configured; auth/account entitlement unknown | Implemented; mock-tested | Disabled in Spark; family/API docs support features but exact-model proof needed | Low/moderate qualification work, not a new adapter | First direct-provider candidate |
| Cloudflare Workers AI | Existing LLM/vision and embedding adapters; no credentials | Free-plan allowance; only eligible models, not universally free [C1] | Token and account missing | Platform supports it; Spark adapter nonstreaming | Platform/model specific; not wired in current LLM adapter | Moderate: repair completion/system/model handling, qualify text/streaming | Second stage, conditional on no-upfront account readiness |
| Alibaba Model Studio | Disabled compatible checkpoint | Singapore International trial quota; 90-day validity; console Free Quota Only required [A1] | Missing; owner reports funding/verification barrier | Implemented, not live-verified | Blocked in checkpoint | Deferred account and capability qualification | Defer; do not fund or bypass verification |
| Tencent TokenHub | Disabled compatible checkpoint | One-time trial, frozen funds/credit prerequisite; not zero-upfront for unfunded account [T1] | Missing; owner reports barrier | Implemented, not live-verified | Blocked in checkpoint | Deferred account and capability qualification | Defer |
| DeepSeek official | Disabled compatible checkpoint; promotional-only gating | Metered API; granted balance may exist but no confirmed free entitlement [D1] | Missing | Implemented, not live-verified | Official models advertise features; blocked here | Later budget/account and contract work | No paid activation planned |
| Ollama | Specialized local OCR adapter | No hosted API fee; hardware/operations are not free | Hosted key N/A; service readiness unknown | No | OCR JSON only | Separate local serving work | Preserve OCR role; not a general fallback recommendation |
| Mock | Offline/testing and friendly-error fallback | No inference bill | N/A | Simulated | None | Completion semantics need review | Never count as usable inference capacity |

## Cost/account details for immediate candidates

### Z.AI

Official pricing lists `glm-4.7-flash` and `glm-4.5-flash` at zero input/output
prices [Z1]. Premium GLM and FlashX are different, paid models. General-purpose
base URL is `https://api.z.ai/api/paas/v4`; Bearer authentication and OpenAI
compatibility are documented [Z2]. The owner key exists locally, but this audit
does not verify signup identity/card requirements, regional eligibility, concurrency
entitlement or current key validity. No account-wide “free-only” switch was
established from the public sources. Application allowlists must exclude paid
IDs, built-in paid tools and automatic alias replacement. No permanent-free SLA
or infinite quota is implied.

The family pages advertise streaming, functions and structured output [Z3,Z4].
The API/SSE documentation describes text deltas, terminal finish and usage [Z5].
Exact free-model behavior and acceptance of the checkpoint's OpenAI-style
`stream_options.include_usage` remain unverified; that option is not established
by the inspected Z.AI API reference. Thinking defaults/output budgets also need
model-specific qualification.

Finish vocabulary includes `stop`, `tool_calls`, `length`, `sensitive`,
`model_context_window_exceeded`, `network_error` [Z2]. Only nonempty `stop` should
complete teaching. Preserve other reasons and classify conservatively.
Rate limits are account-managed; the public rate-limit link redirects to the
account dashboard [Z6], so no numeric GLM RPM is asserted. Crucially, HTTP 429
business code 1113 means balance/resource-package exhaustion, while 1302 means
rate limiting and 1305 overload [Z7]. Current adapter loses this distinction.

### Cloudflare Workers AI

Current documentation describes a daily allowance of **10,000 neurons**, resetting
at **00:00 UTC**, on Workers Free; exceeding it fails rather than automatically
upgrading. Paid Workers can incur overage at **$0.011/1,000 neurons** [C1]. This is
recurring quota, not an intrinsically free model. Some models require paid billing.
Do not infer that an existing DNS/Workers account has inference entitlement, or
that its plan is Free. Card requirements for this owner's account are unknown;
the direct REST setup documents account/token prerequisites, not a mandatory paid
upgrade [C2]. Stay on a verified Free plan and exclude paid-only models; this is
the documented cost boundary, not a separate application budget guarantee.

`@cf/zai-org/glm-4.7-flash` is a Cloudflare-hosted text/tool candidate [C3], distinct
from direct Z.AI. Its listed unit rates are $0.0605/M input and $0.40/M output;
Free-plan allowance can fund eligible requests. Same weights do not imply shared
serving infrastructure. `@cf/meta/llama-3.3-70b-instruct-fp8-fast` is another text
candidate [C4]. Existing Vision IDs remain cataloged [C5,C6]; the Llama 3.2 model
requires owner acceptance of its license. No acceptance was performed.
Do not select `@cf/qwen/qwen3-30b-a3b-fp8` for a new rollout: its official model
page schedules deprecation for **2026-11-20** [C7]. Model lifecycle matters more
than finding a cheap model on an old list.

Direct endpoint:
`https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/run/{model}`.
OpenAI-compatible base:
`https://api.cloudflare.com/client/v4/accounts/{account_id}/ai/v1` [C8].
Workers AI is inference. Cloudflare AI Gateway is a separate routing/logging
product; sharing `/ai/` conventions in newer documentation does not make them
interchangeable. The existing Spark adapter uses direct Workers AI; it does not
configure a Cloudflare gateway ID or management API. No second external gateway
is needed to get the free inference allowance.

#### Owner token procedure — instructions only

1. Sign in to the intended Cloudflare account; confirm Workers AI access and
   billing plan without upgrading or adding prepaid credits.
2. Open **Workers AI → Use REST API → Create a Workers AI API Token** [C2].
3. Review the prefilled grant. For direct inference, use account-scoped
   **Account → Workers AI → Read**, scoped to that one account; use a custom
   token if needed to remove unrelated grants [C9,C10]. Do not grant DNS/Zone,
   billing, account-wide administration, or Cloudflare AI Gateway Edit merely
   to invoke Workers AI. An existing DNS token should not be repurposed.
4. If available, constrain lifetime and stable server egress IPs. Copy the token
   once into the existing server secret mechanism; keep the account ID separate.
   The Spark names are `PWANIMATE_CLOUDFLARE_API_TOKEN` and
   `PWANIMATE_CLOUDFLARE_ACCOUNT_ID`. Generic aliases exist but increase ambiguity.
5. Do not print credentials, accept model licenses or send a request as part of
   this audit. A later synthetic smoke test needs separate explicit authorization.

The current adapter docstring claims earlier live verification and that AI-scoped
credentials existed; those claims are historical comments, not present readiness
proof. The inspected configuration says missing. Existing account infrastructure
may be reused only after account/permission verification; no unrelated credential
was accessed for reuse.

### OpenRouter

`PWANIMATE_OPENROUTER_API_KEY` is configured; `is_available()` only checks nonempty
key. Source chains can reach it, but actual account operation and database
registration are unknown. Free variants are supported as exact `:free` IDs [O1].
The documented limits are 20 RPM and 50 requests/day below $10 cumulative credit
purchases, 1,000/day after that threshold [O2,O3]. The audit does not recommend
buying credits. Multiple free models/keys do not multiply account-wide allowance.
Existing Spark cooldowns do not count this remote daily request budget.

Exact free IDs prevent automatic use of paid siblings, but the adapter accepts
arbitrary model strings and application-wide paid prevention is not universal:
callers without tier metadata can reach configured paid models. Provider spend
caps, no auto-recharge and curated free-only catalogs need owner verification.
No account-wide free-only status was read. Card/verification requirements and
account-region availability remain unverified. Public model listings do not
prove a specific account can use each endpoint.

OpenRouter may choose multiple hosting vendors [O4]. Spark treats all of them as
one `openrouter` provider, records no actual serving vendor and does not pass
provider-selection/data-policy controls. It adds a separately authenticated route,
but fallback independence depends on the selected host: a Google-backed route
may correlate with direct Gemini; an independently hosted open model can provide
more independence. Do not equate model author with inference host.

## OpenRouter workload candidates — proposals, not enablement

Public official pages currently list these exact free variants. They establish
catalog presence and described capabilities, not tested tutoring quality or
account-specific capacity. No model was added.

- **Casual conversation:** `liquid/lfm-2.5-2.6b:free` or
  `openai/gpt-oss-20b:free`, subject to current catalog and latency qualification
  [O8,O9]. Small models are not automatically suitable for mathematics or teaching.
  Liquid's current page discloses prompt/output retention and training use [O8];
  do not route student/personal context there without the required policy review.
- **General academic explanation and mathematics:** `openai/gpt-oss-120b:free`
  is a reasoning/structured/tool candidate [O5]. Require synthetic math, terminal
  metadata and teaching-completion tests before promoting it to critical routing.
- **Programming:** `qwen/qwen3-coder:free` describes code, tool use and repository
  reasoning [O6]. Native tool/JSON support of the exact free serving endpoint
  still needs verification; the current Spark payload only supports text.
- **Structured planner:** evaluate `openai/gpt-oss-120b:free` against the actual
  planner schema. `nvidia/nemotron-3-ultra-550b-a55b:free` advertises tool calling
  but explicitly lacks `response_format` on its current page [O7]; it is not an
  enforced-JSON planner route just because prompted JSON sometimes works.
- **Memory extraction:** no new model recommended for production. Current R2
  extraction is deterministic/local; adding an LLM would change data/consent
  boundaries. A future synthetic evaluation could assess GPT-OSS structured
  extraction, but only after privacy and exact-endpoint capability checks.

The Nemotron free page also carries a warning against submitting confidential or
personal data [O7]. That is material to personal-memory/student contexts; free
price does not authorize sending them. Provider data policies must be an
eligibility criterion [O4].

For maintainable discovery, the official model-list schema exposes IDs, pricing,
context limits, supported parameters and lifecycle metadata [O10]. A later admin
refresh can fetch/cache the public catalog, intersect it with an approved
allowlist, reject nonzero/unknown prices and expired models, and require review.
Do not auto-enable every discovered ID or make catalog HTTP requests on every
student turn. Spark currently has no such discovery mechanism.

## Deferred providers: money and account constraints

- **Alibaba:** documentation specifies Singapore International quota, current
  90-day validity, and automatic pay-as-you-go after expiry/exhaustion unless
  the owner enables console **Free Quota Only**, which defaults off [A1]. Actual
  allocation, account verification/card requirement and usable region are unknown;
  owner reports a funding barrier. Keep deferred.
- **Tencent:** current trial page describes 1M-token trials, one-year validity,
  campaign through 2026-12-31, and **$10 frozen balance/credit per model claim or
  postpaid activation**. Quota-only service stops if postpaid stays disabled [T1].
  The page is ambiguous about aggregate versus per-model quota; account catalog
  is authoritative. This is not an unfunded zero-upfront route. Card/region
  eligibility remains unknown; do not claim quota or change endpoints.
- **DeepSeek:** official `deepseek-flash` / `deepseek-v4-pro` are metered, with
  charges deducted from granted or topped-up balances [D1]. A free chat website
  supplies no API entitlement. No key or granted balance is confirmed. No paid
  activation or billing changes are planned; card/region/recharge specifics and
  provider-side no-spend controls were not established.

## Official source register

All links below were reviewed **2026-11-15**. Pricing/eligibility statements above
are scoped to that date. Account-specific claims remain unknown.

- [Z1 Z.AI pricing](https://docs.z.ai/guides/overview/pricing)
- [Z2 Z.AI chat API](https://docs.z.ai/api-reference/llm/chat-completion)
- [Z3 GLM-4.7 family](https://docs.z.ai/guides/llm/glm-4.7)
- [Z4 GLM-4.5 family](https://docs.z.ai/guides/llm/glm-4.5)
- [Z5 Z.AI streaming](https://docs.z.ai/guides/capabilities/streaming)
- [Z6 Z.AI account rate limits](https://z.ai/manage-apikey/rate-limits)
- [Z7 Z.AI errors](https://docs.z.ai/api-reference/api-code)
- [Z8 Z.AI OpenAI compatibility](https://docs.z.ai/guides/develop/openai/python)
- [C1 Workers AI pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/)
- [C2 Workers AI REST setup](https://developers.cloudflare.com/workers-ai/get-started/rest-api/)
- [C3 Cloudflare GLM-4.7-Flash](https://developers.cloudflare.com/workers-ai/models/glm-4.7-flash/)
- [C4 Cloudflare Llama 3.3 fast](https://developers.cloudflare.com/workers-ai/models/llama-3.3-70b-instruct-fp8-fast/)
- [C5 Cloudflare Llama 3.2 Vision](https://developers.cloudflare.com/workers-ai/models/llama-3.2-11b-vision-instruct/)
- [C6 Cloudflare Llama 4 Scout](https://developers.cloudflare.com/workers-ai/models/llama-4-scout-17b-16e-instruct/)
- [C7 Cloudflare Qwen scheduled deprecation](https://developers.cloudflare.com/workers-ai/models/qwen3-30b-a3b-fp8/)
- [C8 Cloudflare OpenAI compatibility](https://developers.cloudflare.com/workers-ai/configuration/open-ai-compatibility/)
- [C9 Cloudflare direct REST permissions](https://developers.cloudflare.com/ai-gateway/usage/rest-api/)
- [C10 Cloudflare custom token setup](https://developers.cloudflare.com/fundamentals/api/get-started/create-token/)
- [C11 Cloudflare model/task rate limits](https://developers.cloudflare.com/workers-ai/platform/limits/)
- [O1 OpenRouter free variants](https://openrouter.ai/docs/guides/routing/model-variants/free)
- [O2 OpenRouter limits](https://openrouter.ai/docs/api-reference/limits)
- [O3 OpenRouter official numeric constants](https://github.com/OpenRouterTeam/docs/blob/main/snippets/exports/constants.mdx)
- [O4 OpenRouter provider routing and policies](https://openrouter.ai/docs/guides/routing/provider-selection)
- [O5 OpenRouter GPT-OSS 120B free](https://openrouter.ai/openai/gpt-oss-120b:free)
- [O6 OpenRouter Qwen3 Coder free](https://openrouter.ai/qwen/qwen3-coder:free)
- [O7 OpenRouter Nemotron Ultra free](https://openrouter.ai/nvidia/nemotron-3-ultra-550b-a55b:free)
- [O8 OpenRouter Liquid free](https://openrouter.ai/liquid/lfm-2.5-2.6b:free)
- [O9 OpenRouter GPT-OSS 20B free](https://openrouter.ai/openai/gpt-oss-20b:free)
- [O10 OpenRouter model-list schema](https://openrouter.ai/docs/api/api-reference/models/list-all-models-and-their-properties)
- [A1 Alibaba trial quota and Free Quota Only](https://www.alibabacloud.com/help/en/model-studio/new-free-quota)
- [T1 Tencent trial prerequisites and billing](https://intl.cloud.tencent.com/document/product/1300/80423)
- [D1 DeepSeek models/pricing/balance](https://api-docs.deepseek.com/quick_start/pricing/)
- [G1 Gemini billing tiers](https://ai.google.dev/gemini-api/docs/billing)
- [G2 Groq billing FAQ](https://console.groq.com/docs/billing-faqs)
