# Spark gateway expansion — implementation checkpoint

This change extends PwaniNet/Vasto Spark's existing `AIGateway`, `LLMRouter`,
`QuotaTracker`, `LLMRequest` and `LLMResponse`. It introduces no orchestrator,
academic schema, migration, or student data operation.

## Audited boundaries

- `ai/gateway/gateway.py`: bounded candidate iteration; stream callback buffering;
  partial generation returns `interrupted` without another provider.
- `ai/gateway/router.py`: settings and engine-provided chains, overrides, image
  capability filtering, tier gates and named offerings. Expansion eligibility is
  checked after offering prepend as well as during initial selection.
- `ai/gateway/types.py`, `ai/completion.py`, existing Gemini/Groq/OpenRouter:
  shared finish normalization; only nonempty `stop` is academic completion.
- `billing/models.py`, `billing/services.py`, seed/check registry commands:
  provider/model usage attribution, model offering prices, tier entitlements.
- `orchestrator/agent.py`: planner requests task `tool`, JSON in text; previously
  retried gateway errors immediately. Gateway errors now exit that retry loop.
- `services/teaching.py`, `services/agent_execution.py`, `services/run_snapshots.py`:
  completion authorization and versioned snapshots remain owned by those services.
- Conversation memory tasks/services and personal memory use their existing paths;
  new models are withheld from memory and critical tasks pending validation.
- Existing isolated SQLite and disposable PostgreSQL runners were inspected.

## Catalog verified 2026-11-14

- `zai`: `glm-4.7-flash`, `glm-4.5-flash`; documented zero token prices.
- `alibaba`: `qwen3.8-flash`, `qwen3.7-plus`; account-specific trial quota.
- `tencent`: `hy3`, `hy4`; catalog IDs, account-specific trial eligibility.
- `deepseek`: `deepseek-flash`, `deepseek-v4-pro`; paid APIs; only confirmed
  promotional balance with provider-side spending restrictions is accepted here.

The four provider instances share `providers/compatible.py`. Message/context
construction reuses Groq's existing payload builder, while transport, error
sanitization, resource closing and strict response parsing are shared across the
new providers. Exact API IDs are independent of display names.

Each adapter reports basic text streaming and explicit false for tools, JSON and
images. Supported workloads are `general`, `conversational`, `summary`, and
`summarization`. Advanced model capabilities are deliberately NOT inferred from
OpenAI compatibility or family-level marketing claims. Academic teaching, math,
physics, programming-specific routing, planning, memory and structured output
still use existing providers. This is a conservative checkpoint, not completion
of the full requested capability matrix.

## Sources

- https://docs.z.ai/guides/overview/pricing
- https://docs.z.ai/guides/llm/glm-4.5
- https://docs.z.ai/api-reference/llm/chat-completion
- https://www.alibabacloud.com/help/en/model-studio/models
- https://www.alibabacloud.com/help/en/model-studio/new-free-quota
- https://intl.cloud.tencent.com/document/product/1300/78934
- https://intl.cloud.tencent.com/document/product/1300/78941
- https://api-docs.deepseek.com/quick_start/pricing/

Catalog verification is not account entitlement verification or live capability
verification. No live requests were made.
