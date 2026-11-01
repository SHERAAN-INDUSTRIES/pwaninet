# Local Models Design — Self-Hosted Embedding & Vision Workers

**Status:** Draft (Phase 1: embeddings; Phase 2: vision)
**Date:** 2026-10-19
**Scope:** Local/self-hosted model workers for Spark (Pwanimate) — where they run, how they integrate, how we cut over, and how we degrade.
**Non-goals (sequenced later):** payment pipeline, plans/tiers (separate design doc), fine-tuning on Vasto/Spark data (gated on explicit upload consent — see §9), training models from scratch.

**Tier context (decided, detailed in the billing doc):** 4 tiers — **Ember (free) / Pro Day Pass / Pro Monthly / Teams (extreme)**. Free tier runs the Spark family (self-hosted where built, Spark-branded provider free routes as interim); paid tiers unlock external provider models; models are locked in UI **and** enforced server-side with a hard lock + upgrade CTA.

**Agreed build order (2026-10-19):** ① **Spark-brand the existing free-provider models** (engines layer, no local models needed) → ② **`spark-vector` into production** (uninterrupted doc embedding — the current pain) → ③ **`spark_vision`** (host TBD — see D9) → ④ **billing/tiers doc** built on top of the working systems. Working systems first, monetization second.

---

## 1. Goals

1. **$0 recurring infra** — run on the existing production VPS; no new hardware in Phase 1.
2. **Never deny service & free-tier independence** — free-tier embeddings *and* vision must not depend on free-provider quota; `spark_vision` serves the free tier's 5 images/day from Phase 1 (see D3).
3. **Privacy win** — document text no longer leaves the box for embedding; vectors and chunks stay in our Postgres.
4. **Swap-ability** — every local worker speaks an OpenAI-compatible surface so a future GPU box / Cloudflare BYOM / rented GPU is an **endpoint URL change**, not a code change.

## 2. Background — how embedding actually works today (verified)

Two-sided pipeline; the LLM never touches vectors:

```
Ingestion (once per document revision):
  upload → extract → chunk → embed(chunk) → store [vector | text | embedding_model stamp]
                                        └── DocumentChunk (pgvector, 768 dims)

Retrieval (every chat message):
  question → embed(query) with THE SAME active model → cosine top-k in pgvector
           → retrieved chunk TEXT + citations → prompt → LLM answers
```

- Retrieval is pure vector math, **tier-agnostic**: a free user and a paying user retrieve the same chunks; the LLM model only changes answer quality over that context.
- The two-sided invariant: queries and documents must share one embedding model, enforced by filtering `embedding_model = active_model`.

Existing machinery this design builds on (do not rebuild):

| Mechanism | Location | Relevance |
|---|---|---|
| Provider contract + error taxonomy | `pwanimate/ai/embeddings/base.py` | `embed_texts(task_type)`, `embed_query`, `get_dimensions`, `get_model_name`, `is_configured`; typed errors incl. `EmbeddingDimensionMismatchError`, `EmbeddingTimeoutError` |
| Factory dispatch via `PWANIMATE_EMBEDDING_PROVIDER` | `pwanimate/ai/embeddings/factory.py` | add one branch for the local provider |
| Model stamp on every chunk | `DocumentChunk.embedding_model` (`pwanimate/models.py`) | stamping makes provider swaps safe |
| Re-embed on model change | `pwanimate/tasks/embeddings.py` (excludes chunks already completed under active model) + backfill management command | cutover = background re-embed, no schema change |
| Retrieval filters by active model | `retrieval/services/document_retrieval.py::build_candidate_queryset` | mixed vector spaces can never pollute search |
| Lexical fallback in hybrid mode | `retrieval/services/unified_retrieval.py` (semantic leg failure → continue with lexical) | our failure mode for a dead worker, already implemented |
| Infra | `docker-compose.prod.yml` → `db` (pgvector/pg15), `redis`, `web`, `celery_worker`, network `pwaninet_net` | new worker joins `pwaninet_net` |
| Query-embedding task types | `task_type` param (`RETRIEVAL_DOCUMENT` / `RETRIEVAL_QUERY`) | maps to e5's `passage:` / `query:` prefixes |

## 3. Decision record

| # | Decision | Choice | Rejected alternatives & rationale |
|---|---|---|---|
| D1 | Local embedding model | **`intfloat/multilingual-e5-base` (768)** | **BGE-M3 (1024)** → breaks the 768 invariant, forces full re-embed; **bge-base-en-v1.5 (768)** → weaker for Swahili/mixed Kenyan student content; **multilingual-e5-small** → 384 dims, breaks invariant; **training from scratch** → GPU money pit |
| D2 | Worker shape | **Separate microservice `spark-vector`** (FastAPI + ONNX int8), OpenAI-compatible | In-Celery embedding → model RAM duplicated per worker, restarts reload model, sync query path tangled with task queue |
| D3 | Vision sequencing | **`spark_vision` promoted into Phase 1** — the free tier's 5 images/day run on it (queue + notify absorbs CPU latency) | Provider-route interim for free vision → leaves free tier hostage to provider quota, contradicting "free = Spark family"; the old Phase-2 plan is superseded |
| D4 | Worker-down behavior | **Degrade to lexical (FTS) search; never auto-flip providers** | Auto-flip back to cloud on failure → full re-embed storm + semantic poisoning risk |
| D5 | Stamp name | Stable literal **`multilingual-e5-base`** (no version suffix, no digest) | A renamed stamp triggers re-embed of everything — name changes are deliberate version bumps only |
| D6 | Free-tier chat/code/scholar (interim) | Spark-branded **provider free routes** until local LLMs are proven; engines migrate local over time | All-local day one → CPU chat at ~5–15 tok/s is a bad live experience; keeping free routes now costs $0 and preserves speed while the tier story stays "Spark family" |
| D7 | Embeddings/RAG by tier | **Local for every tier** | Gating retrieval costs provider money for zero user value — retrieval is tier-invisible; tiers differentiate *answer models*, not context |
| D8 | Model locking | Model registry (DB) + **server-side tier enforcement** at the gateway; UI locks render from API, hard lock + upgrade CTA on tap | UI-only locking = free tier can POST any model name; hardcoded JS model cards must be replaced by API-driven list (billing doc owns registry schema) |
| D9 | Inference hosting | **Split topology:** `spark-vector` stays on the VPS (tiny, synchronous query path); `spark_vision` runs on a **dedicated separate box — host TBD** (Oracle declined the signup card; re-pick at vision-build time: cheap VPS / retry Oracle with different card or region / on-VPS only if the spec check allows), linked by a private tunnel | All-on-VPS → vision competes with the site (VPS specs now confirmed: 2 vCPU / 7.7 GiB — spark-vector fits with headroom, a VLM would not); Colab/Kaggle free GPUs → sessions expire, ToS forbids API serving (fine for one-off benchmarks); paid GPU → no budget |

### 3.1 Build ① — Spark engines layer (implemented 2026-10-20)

The interim branding step (build order ①) is done: users now select **Spark engines**, never raw provider models.

- **Registry:** `pwanimate/ai/engines.py` — engines carry `slug / name / icon / tagline / description / fallback_chain`. Visible engines: **`speed` (Spark Speed), `coda` (Spark Coda), `scholar` (Spark Scholar), `vision` (Spark Vision — unhidden 2026-10-20 via the interim Gemini multimodal route, see §3.2)**. Live in code/settings (`PWANIMATE_SPARK_ENGINES` full replacement, `PWANIMATE_ENGINE_OVERRIDES` per-slug merge); billing doc later promotes this to the DB registry with `min_tier` (D8).
- **Fallback ladders (extended 2026-10-20, model IDs live-verified against provider catalog APIs same day):** each visible engine carries an 11–13-rung interleaved ladder — identity primary first, then verified IDs (gemini-3.6-flash, qwen3.8-27b, gpt-oss-20b, nemotron-3.5-lightning:free, nemotron-3-super-120b:free, liquid-lfm:free, flash-lite…) — so a single provider outage never drains an engine; Coda omits the 2.6B liquid model (code quality). Groq's public catalog is slim (gpt-oss-120b/20b, qwen3.8-27b only); retired rungs: `groq/compound-mini` (delisted), `nex-agi/nex-n2.5-pro:free` (no free tier anymore), `openrouter/google/gemini-flash-latest` (renamed), and `openrouter/google/gemini-3.8-flash` (**no `:free` variant — a paid model**; replaced 2026-10-20 by `nvidia/nemotron-3-ultra-550b-a55b:free` after checking the live OpenRouter catalog, so every rung reachable by a free-tier student is genuinely $0). Every ladder ends in `mock:unavailable` for a friendly message instead of a raw 502; **the status rollup counts only real providers** (mock rungs are excluded from `is_configured / fallbacks_ready / powered_by` so they can never prop up an engine's indicator). Providers without API keys are skipped silently at request time.
- **Routing:** chat request accepts `engine` (validated before any side effects; engine primary wins over legacy `provider`/`model`). `OrchestrationRequest.engine_metadata()` carries `engine` + `fallback_chain` into `LLMRequest.metadata`, and `LLMRouter.resolve_fallback_chain` now honors a request-level chain for **non-image** requests too (previously vision-only) — so each engine's ordered chain is respected instead of the global settings chain. Auto (no engine) = unchanged global behavior.
- **Capability routing is inferred, not opt-in (2026-10-20):** if a turn carries an image, the router itself detects it (it already counts the attachments) and admits **only image-capable rungs** (Gemini flash-class, Groq Qwen vision ≤3 images) — the caller no longer has to remember to set `required_capability`. The tool path used to omit the flag, so an image was handed to a text-only model (`groq/gpt-oss-120b`) and the provider refused the whole turn. Covered by `test_capability_routing.py`.
- **Surfaces:** `/api/pwanimate/quota-status/` returns `engines[]` (branded, provider health rolled up: `is_configured / primary_status (ready|rate_limited|unconfigured) / rate_limited / fallbacks_ready / powered_by`); legacy `models[]` kept during transition. Picker is **engines-only** (dropdown + modal cards from the API — the hardcoded model list in `pwanimate-chat.js` is retired, with a static 3-engine fallback if the API is unreachable); providers demoted to a "Powered by" badge; requests send `payload.engine`; legacy localStorage model selections migrate back to Auto.
- **Tests:** `pwanimate/tests/test_engines.py` (18) — registry/overrides/status rollup, request-level chain ordering, quota-status shape, 400s for unknown engines, engine → primary + metadata propagation, Auto-mode cleanliness, ladder-shape + mock-exclusion pins.

### 3.2 Build ③ — brand-only attribution, live vision, token streaming (implemented 2026-10-20)

Shipped together after live-review of the picker and chat:

- **Vision is live now (D3 interim).** `vision` unhidden: chain = Gemini native multimodal (free tier) → Groq Qwen (`qwen/qwen3.8-27b`, ≤3 images). The local `spark_vision` worker remains the D3 endgame and takes the head of this chain when it ships (checklist 11+). Note: image analysis already worked inside every engine's chat via attachments (`required_capability: image` → Gemini native parts); the gap was that no engine advertised it.
- **"Spark Vision is down" is said by the model, not by a banner (2026-10-20).** If no image-capable model can serve the turn, the turn does **not** fail: the text model answers and mentions the outage **in its answer**, once, and only when the question actually depends on the image. There is no repeating banner — the earlier per-turn notice was spam. The one extra element is a **single choice**: *"Keep this image in the conversation?"* → **Keep it** (they may want to ask about it again) or **Drop it** (never sent again). The answer is stored on the attachment (`vision_context_policy`) and asked once; later turns just carry a short note. One message, one place (`SPARK_VISION_UNAVAILABLE_NOTICE` in `orchestrator/service.py`).
- **Images are analysed ONCE (2026-10-20).** The upload task runs a single vision pass and stores the description on the attachment (`vision_summary`); every later turn injects that description as context and sends **no image**, so a photo is analysed once rather than once per question. PDFs already behaved this way (page OCR at upload, stored as chunks).
- **Brand-only attribution — models never surface.** `identify_engine(provider, model)` (engines.py) maps a serving pair back to an engine display name (primary match wins, then first ladder membership in registry order; `mock` never attributes). Chat responses carry `serving_engine`; the header renders `Auto (Speed)`, the chip shows the engine — raw provider/model names removed from chip, tooltips, and notices. The "Free-tier limit reached for X — switched to Y" and "Answered by X (quota exceeded)" notices were **removed**; the vision-degradation notice keeps its honesty but drops the provider name.
- **Token streaming (SSE).** Protocol on the existing chat endpoint: the client sends `Accept: text/event-stream, application/json` → the server streams `event: status` (progress stages) → `event: chunk {"text"}` (visible LLM deltas) → one final `event: response` (full JSON payload — unchanged contract). Plumbing: providers gained `generate_stream(request, on_token)` (Groq/OpenRouter `stream:true` + usage include, Gemini `:streamGenerateContent?alt=sse`, mock chunks; all reuse `_parse_response` via synthesized payloads) → `AIGateway.generate_stream` mirrors the fallback loop → orchestrator `run(..., on_token=)` threads the callback through `_generate` → the view emits chunks from its worker thread. **Fallback policy:** failure *before* the first token = silent failover (identical to non-streaming); failure *mid-stream* cannot be replayed — the partial text is returned with `metadata.interrupted = true`. Deltas coalesce to ≥24-char/newline chunks; providers without native streaming degrade to one chunk. **v1 scope:** multimodal (image) turns stay non-streaming (vision→text fallback correctness first — the client degrades to final-render UX automatically). SSE lines decode as explicit UTF-8: requests' header guessing mangles charset-less `text/event-stream` as Latin-1 (the "â" garbage bug) — regression-pinned in tests.
- **Client.** `pwanimate-chat.js` renders a live bubble from the first chunk (markdown re-render throttled to 80 ms) and swaps in the fully-rendered row on the final event. Service-worker cache bumped to `1.6.4/build116` so browsers fetch the new JS.
- **Tests:** `pwanimate/tests/test_streaming.py` (11) — provider SSE fixtures (incl. multi-byte UTF-8), gateway failover / mid-stream interruption / coalescing / degraded-provider, `_generate` swap, SSE protocol ordering; `test_engines.py` updated for visible vision + `identify_engine`.

## 4. Phase 1 — `spark-vector` service spec

**Container:** `spark_vector`, own image (ONNX runtime), joins `pwaninet_net`, **not published to host ports** (internal only), `restart: unless-stopped`, healthcheck against `/healthz`.

**Endpoints (OpenAI-compatible):**

```
POST /v1/embeddings
  { "model": "multilingual-e5-base",
    "input": "text" | ["text", ...],
    "task_type": "RETRIEVAL_QUERY" | "RETRIEVAL_DOCUMENT" }   # non-OpenAI extension, defaults to document
  → { "object": "list",
      "model": "multilingual-e5-base",
      "data": [ { "object": "embedding", "index": 0, "embedding": [768 floats] } ],
      "usage": { "prompt_tokens": n, "total_tokens": n } }

GET  /v1/models   → catalog of the served model
GET  /healthz     → { "status", "model", "warm": bool, "queue_depth": n }
```

**Model contract inside the service:**
- Apply e5 prefixes per `task_type`: `query: ` for queries, `passage: ` for documents (accept literal `query`/`passage` passthrough too, for tests).
- L2-normalize vectors (e5 recommendation; harmless for cosine ops in pgvector).
- **Validate output shape = 768 at the service boundary**; refuse to serve a model whose dimension differs from what settings expect (protects the invariant at the edge).

**Resources & performance targets** (measured 2026-10-20 on the dev box — 4 vCPU, *busy*; prod box is 2 vCPU, see §12):
- ~278M params, int8 ONNX ≈ ~300 MB weights; **measured RSS ≈ 800 MB** (fits the 7.7 GB prod box alongside the app's 2.0 GB).
- **Measured throughput ≈ 500–600 tokens/s** (one ~230-token chunk ≈ 0.5–0.6 s; 4 chunks ≈ 2.1 s; 8 chunks ≈ 2.9 s).
- **Corrected targets** (the original "batch of 32 < 2.5 s" was ~5× optimistic and is retired): single query **p50 < 80 ms, p95 < 200 ms** and **batch of 4 < 2.5 s**. A 32-chunk batch takes ~8–9 s at the dev-box rate, so the client **splits ingestion into sub-batches of 4** (§5).
- **Measured on prod (2026-10-20, Azure `web-core-vm`, 2 vCPU, dedicated):** single query **p50 18.2 ms / p95 23.6 ms**, one ~150-token chunk 145 ms, **batch of 4 p50 612 ms** (~155 ms/item, ≈1,000 tokens/s), query-during-bulk 236 ms, 768 dims with L2 norm 1.000000. Comfortably inside the targets on 2 vCPU, so sub-batch 4 stands (no need to drop to 2–3).
- Max 64 inputs per request *and* a **4096-token budget per request** (`SPARK_VECTOR_MAX_TOKENS_PER_REQUEST`): over-budget requests are refused with **413** instead of monopolising the slot.
- Model weights **baked into the image at build time** — no download at boot. Asset source (implemented): `Xenova/multilingual-e5-base` ONNX int8 at pinned commit `1ec9243030a27d1a115d5c340572074c125b58b2`, `onnx/model_int8.onnx` sha256 `9ddfd8b4…3bc87da`, verified by `spark_vector/fetch_assets.py` during `docker build`; a `MANIFEST.json` records provenance. Bumping = deliberate commit + checksum change, never float to main.

**Service operating model (implemented 2026-10-20 — learned the hard way):**
- **One inference slot, two lanes.** Interactive `RETRIEVAL_QUERY` requests are admitted **ahead of** bulk `RETRIEVAL_DOCUMENT` batches; bulk runs whenever no query is waiting (so neither lane starves the other).
- **Bounded backlog:** above `SPARK_VECTOR_MAX_WAITERS` (32) the service answers **503 + `Retry-After`** so callers back off instead of piling work onto a saturated box.
- **Dedicated inference executor** (one worker): a cancelled request can never starve the shared event-loop threadpool.
- **Admission waits are async-native.** A blocking wait inside the async endpoint stalled the event loop, which stalled the request *holding* the slot: one bulk request measured **120 s** while its own inference took 2.8 s, and the queued query timed out. The service now awaits the slot, so overlapping requests no longer serialise into a hang. (Pinned by `test_waiting_does_not_block_the_event_loop`.)
- **Observability:** every request logs `embed lane=… texts=… waited=…ms inference=…ms`; `/healthz` reports `queue_depth / active / waiting_query / waiting_document`.
- **Benchmark harness:** `spark_vector/bench.py` (stdlib-only) measures query p50/p95/p99, batch latency, the 768-dim contract, and the *query-during-bulk* case that must not starve. Runs on the prod box via a throwaway container on `pwaninet_net`.

**Why both paths share it:** the sync query path (every chat message) and the async ingestion path (Celery) hit the same service; a single instance with an internal batching queue keeps one model copy in RAM.

**Deployment topology (two boxes, one direction of traffic):**

```
VPS (site box: daphne, celery, db, redis)          Dedicated vision box (host TBD — D9)
┌──────────────────────────────────────┐           ┌──────────────────────────────────┐
│  spark-vector  (embeddings, sync,    │  private  │  spark_vision  (VLM, async,      │
│  ~1 GB RAM, stays in pwaninet_net)   │──tunnel──▶│  0.5–2.5 GB, single inference    │
│  celery vision_queue task ───────────┼──HTTPS────▶│  slot + internal queue)          │
└──────────────────────────────────────┘  + token  └──────────────────────────────────┘
```

- **Rule: Redis and Postgres never cross the boxes.** The VPS Celery `vision_queue` task performs an outbound HTTPS call to the vision box; the box never initiates connections back. No inbound public ports on Oracle — private tunnel only (**Tailscale or Cloudflare Tunnel** — we already run Cloudflare; choice = open question §12), plus a shared bearer token.
- `spark-vector` stays on the VPS because query embedding sits on the *interactive* request path (a WAN hop would land on every chat message); its footprint is ~1 GB / sub-100 ms. It only moves off-box if the user's VPS spec check (§12) shows no headroom.
- **Vision host still open** (Oracle declined the signup card): candidates — retry Oracle (different card/region), a cheap VPS (Hetzner/Contabo/Vultr/RackNerd-class, ~$3–15/mo: the first real infrastructure spend, justified at vision build time), or the VPS itself *only if* the spec check allows headroom. Constraints whichever is chosen: images must match the host arch (aarch64 if ARM — ONNX Runtime and llama.cpp both have good aarch64 support), the same tunnel + no-public-ports rules apply, and the VLM benchmark (item 13) runs on *that* box.

## 5. Django-side adapter

**New:** `pwanimate/ai/embeddings/selfhosted.py` → `SelfHostedEmbeddingProvider(BaseEmbeddingProvider)`, registered in `factory.py` under name `selfhosted` (alias `local`).

**Configuration (env / settings):**

```
PWANIMATE_EMBEDDING_PROVIDER=selfhosted
PWANIMATE_SELFHOSTED_EMBEDDINGS_URL=http://spark_vector:8100/v1/embeddings
PWANIMATE_SELFHOSTED_EMBEDDINGS_MODEL=multilingual-e5-base   # must equal served model (D5)
PWANIMATE_SELFHOSTED_EMBEDDINGS_TIMEOUT=10                   # seconds
PWANIMATE_SELFHOSTED_EMBEDDINGS_TOKEN=                       # optional shared bearer token
PWANIMATE_SELFHOSTED_EMBEDDING_BATCH_SIZE=4                  # texts per HTTP request
```

**Behavior:**
- `is_configured()` → base URL present (no API key concept).
- `embed_query()` → `task_type=RETRIEVAL_QUERY`; `embed_texts()` passes `task_type` through.
- **Sub-batching (2026-10-20):** `embed_texts()` splits its input into requests of at most `PWANIMATE_SELFHOSTED_EMBEDDING_BATCH_SIZE` (default 4). One big request would hold the worker's single inference slot (and exceed its token budget), so ingestion would stall interactive queries and trip the 10 s timeout. 4 chunks ≈ 2.1 s of inference = the worst-case wait an interactive query can face during ingestion.
- `get_model_name()` → the configured model name (**this string is the DB stamp and the retrieval filter**).
- `get_dimensions()` → 768.
- Error mapping onto the existing taxonomy: connect/read failure → `EmbeddingTimeoutError`; 429 → `EmbeddingQuotaExhaustedError` (should not occur locally); 4xx → `EmbeddingInvalidRequestError` (incl. the 413 token-budget refusal); 5xx → `EmbeddingServerResponseError` (incl. the 503 busy/backoff signal); wrong-length vector → `EmbeddingDimensionMismatchError`; unparseable body → `EmbeddingMalformedResponseError`.
- Retries: **no** client-side retry on the sync query path (timeout budget is the UX); Celery ingestion already has task-level retries.

## 6. Cutover plan (cloud provider → local)

The existing stamp + reconciliation machinery does the heavy lifting; this is an ordered runbook:

1. **Build & ship** service + adapter + tests. Provider setting stays `gemini` → zero behavior change; verify `/healthz` in prod network.
2. **Benchmark** query/batch latency on the prod box; confirm targets (§4). If missed, tune (fewer uvicorn workers, int8 vs fp16, smaller batch) — *not* a model swap; D1 is locked before first flip (renaming the stamp later = full re-embed).
3. **Popularity-first re-embed ordering** — shipped as `python manage.py reembed_corpus` (checklist item 5): it enumerates document versions whose chunks are not yet on the target model, orders them by `DocumentAnalytics.popularity_score` (desc), and enqueues the existing `embed_document_version` Celery task in waves (`--concurrency`, default 4). During the transition, not-yet-re-embedded chunks are intentionally unfindable by semantic search (by design of the active-model filter) — lexical still finds them. Popularity-first means the *most useful* documents come back first.
4. **Flip** `PWANIMATE_EMBEDDING_PROVIDER=selfhosted` in `.env`, then **restart the web process AND every celery worker** — *mandatory*: a running worker keeps the old provider in its environment, so it keeps stamping the old model and you get a mixed corpus (verified the hard way on 2026-10-20: a stale worker re-stamped 22 chunks with `gemini-embedding-2` after the flip). Restart order: `docker compose -f docker-compose.prod.yml up -d --force-recreate web celery_worker spark_vector`.
5. **Re-embed the corpus** (background, popularity-first):
   ```bash
   # dry run first — prints the plan (documents, popularity, chunk counts)
   docker compose -f docker-compose.prod.yml exec web \
     python manage.py reembed_corpus --dry-run
   # then run it (resumable: re-running only picks up what is left)
   docker compose -f docker-compose.prod.yml exec web \
     python manage.py reembed_corpus --wait --concurrency 2
   ```
   New uploads embed with the local model automatically: upload → `ingest_document_version` (Celery) → `embed_document_version.delay()` → the Django request never waits on embedding.
6. **Verify completion:** `SELECT embedding_model, embedding_status, count(*) FROM pwanimate_documentchunk GROUP BY 1,2;` → target: zero rows off the active model and zero `pending/failed`.
7. **Rollback:** flip back to the previous provider → one full re-embed in reverse (accepted one-time cost; prior API key stays configured). Never toggle repeatedly — each flip is a corpus-wide rebuild.

**Operational rules:**
- One slot, two lanes: interactive queries jump the bulk queue in the worker (§4) — never raise the worker's embedding concurrency above the spark-vector slot (1) without re-measuring.
- A stuck doc (spark-vector down) is **not** silently re-embedded by a cloud provider (D4): the chunk stays `pending`/`failed` and the Celery task retries with backoff. The document is never mis-stamped.
- The Django server is never blocked by embedding: ingestion is Celery-only.

## 7. Failure modes

| Failure | System behavior | User impact |
|---|---|---|
| Worker down | `embed_query` raises → hybrid mode's semantic leg aborts, **lexical fallback continues** (already implemented) | Answers still grounded via full-text search; semantic ranking temporarily weaker |
| Worker slow | 10 s timeout → same as down | Same as above; chat stays responsive |
| Wrong dimension returned | `EmbeddingDimensionMismatchError` → semantic leg aborted | Same as above; invariant protected |
| Pure-semantic request path with worker down | **Verify** orchestrator degrades (hybrid catches; pure-semantic must not surface a raw error) → build task #8 if not | Must be graceful |
| pgvector/DB down | Existing app-level failure handling | Out of scope here |
| Auto-flip to cloud on failure | **Forbidden** (D4) — would trigger re-embed storm | — |

**Monitoring ($0):** Docker healthcheck + `restart: unless-stopped`; plus a cheap cron health ping (script hits `/healthz`, notifies via the existing `notifications` app or logs). Choice of ping mechanism = open question §12.

## 8. `spark_vision` — free-tier vision engine (Phase 1)

- **Role:** primary vision engine for the **Ember (free)** tier — 5 images/day, served locally. Paid tiers (Day Pass / Pro Monthly / Teams) route vision to external VL providers (enforcement via the gateway; model registry lives in the billing doc).
- **Shape:** FastAPI container **on the dedicated vision box (D9 — host TBD)**, OpenAI-compatible `POST /v1/chat/completions` accepting image input; quantized small VLM candidates to benchmark: **SmolVLM-500M, Qwen2-VL-2B-Instruct, PaliGemma-3B** (pick by OCR / whiteboard-math / circuit-diagram accuracy on lecture-slide-like inputs). Architecture must match the chosen host (aarch64-friendly runtimes: ONNX Runtime / llama.cpp).
- **Async by design:** Celery `vision_queue` task on the VPS → outbound HTTPS through the private tunnel → single inference slot on the vision box → answer lands in the conversation → user notified via the existing `notifications` app (in-app + web push already work). CPU latency of seconds-to-a-minute is acceptable *because* the path is queue + notify — and it's now isolated from the site's CPU entirely.
- **Text-only fallback stays** as the last resort if the worker is down (today's `vision_text_fallback` path) — but the *tier* fallback (sending a free user to provider vision) does **not** happen: free vision degrades to queue/notify, not to provider quota.
- **Quality escape hatch:** benchmark first (build item 13, on the Oracle box). If no candidate meets the free-tier quality bar, revert free vision to Spark-branded provider free routes (D3 reversal) and ship `spark_vision` as an overflow engine instead — decided by benchmark, not assumed.
- **Text-only chat models unchanged here** — D6 interim means Spark Speed/Coda/Scholar run on Spark-branded free routes until local LLMs are benchmarked (§9).

## 9. Upgrade paths (post-revenue, no code rework expected)

- **Dedicated GPU** (rented box / vast.ai / Cloud Run) → point `*_URL` env vars at it; same OpenAI surface.
- **Cloudflare BYOM** (hosted fine-tuned LoRA) → another adapter or URL target if/when needed.
- **Fine-tuning on Spark/Vasto data** — **gated**: requires the explicit upload-time consent toggle from the legal phase before any training run. Never trains on uploads silently.
- **Local chat/code/scholar LLMs (post-Phase-1)** — D6 interim keeps Spark Speed/Coda/Scholar on Spark-branded free routes; migration to local engines is decided by benchmarking small quantized LLMs (e.g. Qwen2.5-3B-Instruct Q4) on real student tasks (code debugging, slide synthesis). The OpenAI-compatible `/v1/chat/completions` surface means each engine flips from provider → local via config, same as vision.
- Dimension invariant (768) is only ever broken by a **deliberate, versioned** re-embed project — e.g., a future BGE-M3 move with dual-column migration, not an ad-hoc swap.

## 10. Testing & acceptance

1. **Adapter contract tests** (mirroring `test_multi_provider_embeddings.py`, mocked HTTP): batch + query happy paths, timeout, 4xx/5xx mapping, dimension mismatch, malformed JSON, missing URL → `is_configured()` false.
2. **Service unit tests:** prefix correctness (`query:`/`passage:`), normalization, dimension guard, 64-input cap.
3. **Integration round-trip:** ingest doc → paraphrased query retrieves it → citations render.
4. **Failure drill:** stop `spark_vector` mid-chat → chat succeeds, retrieval degrades to lexical, warning logged, no user-facing error.
5. **Load:** query-latency p50/p95 and batch throughput on prod-like CPU.
6. **Stale-stamp check:** after flip completes, DB shows zero chunks off the active model.

**Acceptance:** all of the above green + §6 step 5 clean.

## 11. Build checklist (ordered)

1. [x] `spark-vector` service: FastAPI + ONNX, Dockerfile (model baked at build), compose entry on `pwaninet_net`, healthcheck.
2. [x] Model asset pipeline: pinned model revision + checksum at image build.
3. [x] `SelfHostedEmbeddingProvider` + factory branch + settings/env plumbing.
4. [ ] Tests: contract + service unit + integration round-trip. *(done 2026-10-20: adapter 21/21, in-container 22/22, live Django round-trip 59.2 ms; full-DB ingest rehearsal deferred to cutover — item 10)*
5. [x] Popularity-first re-embed ordering + progress-report management command — `manage.py reembed_corpus` (pop-ordered waves, resumable, `--dry-run`/`--wait`; proven end-to-end against spark-vector 2026-10-20).
6. [x] Cutover runbook (§6) as an ops script/checklist + rollback notes.
7. [ ] Health monitoring: Docker healthcheck + cron `/healthz` ping (choose notify channel).
8. [ ] Confirm/fix pure-semantic degradation path (must fall back, not error to user).
9. [x] Load benchmark against §4 targets; tune. *(dev box 2026-10-20, then **prod box 2 vCPU: query p50 18.2 ms / p95 23.6 ms, batch-of-4 612 ms, query-during-bulk 236 ms, 768 dims — PASS** on the corrected targets; sub-batch 4 confirmed)*
10. [x] **Cut over embeddings** (2026-10-20 on prod: worker built + warm, `.env` seeded, `deploy.sh` now owns build/start/health/benchmark/migrate, corpus of 127 chunks across 6 documents migrated popularity-first — "Corpus fully migrated to the target model"). Embedding API spend: **$0**.
11. [ ] Provision the vision box (host per D9 — TBD at this point; if ARM: arm64 Docker base), private tunnel (Tailscale or Cloudflare Tunnel) + shared bearer token, **no public ports**.
12. [ ] `spark_vision` service on the vision box: image-input chat endpoint, single inference slot + internal queue, VPS Celery `vision_queue` → outbound HTTPS call, notify-on-done via `notifications` app.
13. [ ] VLM benchmark **on the vision box** (SmolVLM-500M / Qwen2-VL-2B / PaliGemma on OCR-math-whiteboard tasks) → pick or invoke the D3 escape hatch.
14. [ ] Tier-aware vision routing hook: Ember → `spark_vision` (+5/day count, ledger in billing doc), paid tiers → external VL (gateway check; UI comes from billing doc's model registry).
   *Deferred:* local chat/code LLMs (D6 benchmark), GPU/BYOM, fine-tuning (consent-gated).

## 12. Open questions

- **VPS specs confirmed** (2026-10-20, Azure `web-core-vm`): **2 vCPU, 7.7 GiB RAM (5.8 GiB available), 4 GiB swap**, app currently using 2.0 GiB. spark-vector measures ~800 MB RSS → fits with ~4.5 GiB headroom, and the §4 targets are written for 2 vCPU. **Benchmarked on that box** (query p50 18.2 ms, batch-of-4 612 ms) and **the corpus is cut over** — no open items here.
- **Bulk ingestion window** — *resolved for the current corpus*: prod holds 127 chunks (6 documents), so the migration finished in well under a minute at ~1,000 tok/s. For a much larger future corpus, decide whether ingestion runs off-hours; the query-during-bulk worst case measured on prod is 236 ms (batch of 4), which is invisible to students.
- Exact latency numbers on the prod CPU (benchmark decides whether one uvicorn worker suffices).
- Notify channel for health ping: log-only vs `notifications` app vs push.
- VLM selection benchmark (build item 13) — decision deadline: before free-tier vision ships; fallback if no candidate passes = D3 escape hatch. **Must run on the vision box** (real serving hardware), not the dev machine.
- **Vision host decision** (was Oracle Always-Free; signup card declined): retry Oracle vs cheap VPS (~$3–15/mo, first infrastructure spend) vs on-VPS if the spec check allows — resolve before build item 11.
- Tunnel choice: Tailscale (simple mesh, needs agent on VPS) vs Cloudflare Tunnel (we already run CF, no agent on VPS beyond cloudflared).
- **VPS specs pending** — user runs `nproc && free -h` on prod; decides whether spark-vector stays (expected) and how tight the embedding benchmark targets are. *(RESOLVED 2026-10-20 — see the confirmed-specs entry above.)*
- Whether queue + notify for free vision needs a max-wait policy (e.g. drop with "try again" after N minutes) — interacts with billing doc's queue-priority design.
- Whether `/v1/embeddings` later serves other indexers (posts/groups unified search) — out of scope now, but the surface allows it.
- **`gemini-pro-latest` tier lock — RESOLVED** (2026-10-20, see `docs/billing-tiers-design.md`): it is a **Pro Monthly / Teams** offering, it **leaves the global Auto chain** (`PWANIMATE_LLM_FALLBACK_CHAIN`), and it is reached only through the tier-filtered ladder with an allowance (3 turns/day on Pro, 10 on Teams). A free student's resolved chain contains only `FREE_ROUTE` rungs, so the $0 guarantee is structural rather than a convention. The registry schema, the enforcement points and the M-Pesa Daraja flow live in that document.
