# Configuration

Use existing server-side dotenv/environment loading; never commit a real `.env`.
Never expose keys to browser settings, tracing, fixtures, diagnostics or reports.
Disable Authorization capture in deployment HTTP tracing.

```dotenv
ZAI_API_KEY=
ALIBABA_DASHSCOPE_API_KEY=
TENCENT_TOKENHUB_API_KEY=
DEEPSEEK_API_KEY=
PWANIMATE_EXPANSION_ENABLED={"zai":true}
PWANIMATE_EXPANSION_MODELS={"zai":["glm-4.7-flash","glm-4.5-flash"]}
PWANIMATE_EXPANSION_ENTITLEMENTS={}
PWANIMATE_TASK_FALLBACK_CHAINS={}
```

All new providers default disabled; keys alone never enable traffic. Enable
provider AND exact models. To use them automatically, configure a task chain:

```json
{"general":[{"provider":"gemini","model":"gemini-3.8-flash"},{"provider":"groq","model":"openai/gpt-oss-120b"},{"provider":"zai","model":"glm-4.7-flash"},{"provider":"alibaba","model":"qwen3.8-flash"},{"provider":"tencent","model":"hy3"}]}
```

Existing engine-provided chains take precedence over task settings. Explicit
provider/model requests remain first subject to capability and cost gates.
When Z.AI is explicitly enabled, its key is configured and its free text models
are allowlisted, the default Speed/Coda/Scholar and non-engine global chains
include `glm-4.7-flash` then `glm-4.5-flash` before OpenRouter (or before the final
mock rung). Disabled/missing-key configurations add nothing. Explicit custom
engine/task/request chains retain control. Vision is unchanged, and per-request
capability gates still exclude Z.AI from RAG/teaching, tool/planner, JSON and image
requests. These are two models at one provider, not independent provider quotas.
Workers must load updated code/configuration through the normal deployment flow;
no restart or live provider probe is performed by configuration changes.

Alibaba/Tencent require `PWANIMATE_EXPANSION_ENTITLEMENTS` entries:

```json
{"alibaba":{"free_only_confirmed":true,"models":["qwen3.8-flash"],"expires_at":"2026-12-01T00:00:00+00:00"}}
```

The owner must verify actual remaining quota, expiry and selected model in the
account, then choose an expiry no later than the actual entitlement expiry.
This example date is not an entitlement. Enable Alibaba **Free Quota Only** in
the console before setting `free_only_confirmed`. It is disabled by default;
current documentation describes 90-day trial allocations. Use an International
Singapore key with `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`.
Other regions are intentionally unsupported in this checkpoint.

Tencent uses `https://tokenhub-intl.tencentcloudmaas.com/v1`. The owner must
confirm activation, model eligibility, any frozen balance, and a provider-side
restriction preventing postpaid spend. If no such restriction can be established,
do not enable it or attest `free_only_confirmed`.

Z.AI uses the general endpoint `https://api.z.ai/api/paas/v4`, never Coding Plan.
Premium GLM IDs are absent from the allowlist.

DeepSeek uses `https://api.deepseek.com`. A promotional entitlement entry also
requires `promotional_balance_confirmed:true`. Without confirmed provider-side
spending restrictions it must remain disabled. The free chat website is irrelevant.
Paid routing is intentionally not supported: the existing accounting records
usage after generation and cannot reserve a hard provider budget atomically.
No boolean claiming paid approval bypasses this gate.

`seed_gateway_expansion` previews offering status; `--apply` explicitly updates
only expansion offerings. No command was applied during implementation. Run it
again after disabling/expiring entitlements to update administrative status.
Registry capability metadata distinguishes funding from documented zero pricing;
null prices mean unknown, not free. Active offerings have zero account outlay only
under the confirmed funding restrictions. Historical usage is never rewritten.
Existing public engine choices are unchanged; no new model chips are advertised.

Application eligibility cannot prevent a provider charging accepted requests.
Provider-side billing restrictions are mandatory for quota/promotional routes.
Restart workers after configuration changes; cooldowns are process-local.
