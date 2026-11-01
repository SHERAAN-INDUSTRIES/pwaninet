# Spark AI Serving Architecture

**Status:** Living document · 2026-10-21 · supersedes parts of `local-models-design.md` §3–§8

This is the map of how Vasto serves AI today, what each tier gets, where the
models come from, what it costs, and where the road goes. It is written to be
read once and then kept alive as the system changes.

Three audiences: you (founder), whoever operates the servers, and whoever
touches the AI layer next. If a number in here is wrong, fix the document in
the same commit that changes the code.

---

## 0. Read this first — what changed on 2026-10-22

Three things were true when this document was first written. All three are
now fixed, and the fixes are pinned by tests in
`pwanimate/tests/test_vision_serving.py`:

1. ~~**Your daily caps are not enforced.**~~ **Fixed.** The chat path now
   calls `QuotaService.check_turn()` before any work and returns 429 with
   the same numbers the quota chip renders. The counters and the check read
   the same place, so the number shown is the number acted on.
2. ~~**Images are not analysed at upload.**~~ **Fixed.** Images now follow
   the same `pending → processing → ready` lifecycle as documents: one
   vision pass, off the request path, claimed atomically so two workers
   cannot both pay for it. A turn arriving while an image is still being
   read is refused with a clear message rather than analysed a second time.
3. ~~**The vision capability filter is hardcoded.**~~ **Fixed.** Capability
   lives in `pwanimate/ai/capabilities.py`, a table rather than a literal
   in the router. A model that is not listed is treated as text-only, which
   is the safe direction to be wrong in.

Two more gaps were closed at the same time:

* An **account-wide daily image budget** now exists
  (`pwanimate/billing/global_budget.py`). The Workers AI allocation is
  shared by every user, so the per-user cap is not enough to protect it.
* Images are **downscaled and stripped of EXIF** before they leave the
  building (`pwanimate/ai/vision_images.py`): it costs less, and a
  student's photo should not carry its GPS coordinates to a third-party
  inference provider.

Everything below is accurate as of today. Where something is a recommendation
rather than a fact, it is marked **[RECOMMENDATION]**.

---

## 1. The shape of the system

```
                    ┌─────────────────────────────────────┐
  Student ────────► │  Pwanimate Orchestrator              │
  (web / PWA /      │  ├─ Tool Router (regex)              │
   Android app)     │  ├─ LLM Tool Selector (LLM planner)  │
                    │  ├─ Retrieval (spark-vector)          │
                    │  └─ AIGateway                        │
                    └───────────────┬─────────────────────┘
                                    │ ordered candidate ladder
                                    ▼
        ┌───────────────┬───────────────┬───────────────┬──────────────┐
        │   Gemini      │    Groq       │  OpenRouter   │  Cloudflare  │
        │  (primary)    │  (spare)      │  (:free only) │  (AI-scoped) │
        └───────────────┴───────────────┴───────────────┴──────────────┘
                                    │
                     ┌──────────────┴──────────────┐
                     ▼                             ▼
              spark-vector (self-hosted)      Gemini / Groq / OpenRouter
              embeddings on your VPS          (external, quota-bound)
```

Two independent AI paths, deliberately separated:

| Path | What it does | Hosted where | Latency class |
|---|---|---|---|
| **Embeddings** (`spark-vector`) | document + query vectors for retrieval | **your VPS** (`pwaninet` container) | 50–200 ms, synchronous |
| **Generation** (`AIGateway`) | chat, tool use, vision | external providers | 1–15 s |

Embeddings are self-hosted and always will be — they are the hot path, called
on every search, and they are the one thing that must not depend on someone
else's quota. Generation is rented, because generation is where the quality
frontier moves faster than you can self-host it.

**Do not merge these.** The moment generation depends on a quota you do not
control, your search breaks.

---

## 2. The four engines

Defined in `pwanimate/ai/engines.py`. Not in the database — code plus
settings. `PWANIMATE_SPARK_ENGINES` replaces the whole registry;
`PWANIMATE_ENGINE_OVERRIDES` does a shallow per-slug merge. **Neither is
currently defined in `settings/base.py`**, so the registry is pure code today.

| Engine | Slug | Purpose | Ladder size |
|---|---|---|---|
| **Spark Speed** | `speed` | everyday Q&A | 12 rungs |
| **Spark Coda** | `coda` | code debugging | 9 rungs |
| **Spark Scholar** | `scholar` | lecture slides, notes, quizzes | 12 rungs |
| **Spark Vision** | `vision` | images, OCR, whiteboard math | **2 rungs** |

Every ladder ends in `mock/unavailable` — a friendly terminal rung, not an
error. Ladders are ordered by *capability* first, then cost. `speed` leads
with `gemini-3.8-flash`; `coda` leads with Groq because code is Groq's
strength; `scholar` leads with Gemini for long context.

`identify_engine(provider, model)` maps a serving pair back to a brand name
for the UI. Raw model names never surface to users — the chip says "Spark
Scholar", never "gemini-3.8-flash". That is a deliberate product decision
and it is the reason adding providers is cheap: users never learn a model
name, so you can swap models without a product change.

**Spark Vision is the weak one.** Two rungs, both external, both quota-bound
by third parties. It is also the engine your free tier depends on. That is
the gap this document closes.

---

## 3. Tiers — what each one buys

Defined in `pwanimate/billing/management/commands/seed_model_registry.py`
(idempotent seed) and stored in `billing.Tier`.

| | **Ember** | **Day Pass** | **Pro Monthly** | **Teams** |
|---|---|---|---|---|
| Price | KES 0 | KES 30 | KES 300 | KES 1,200 |
| Duration | — | 1 day | 30 days | 30 days |
| Chats / day | 20 | 60 | 200 | 500 |
| Chats / week | 60 | 300 | 1,000 | 2,500 |
| **Images / day** | **5** | **20** | **60** | **200** |
| Premium turns / day | 0 | 10 | 3 | 10 |
| Premium turns / period | 0 | 10 | 90 | 300 |
| Document uploads | *no tier limit* | | | |

Document uploads are **not** tier-limited — they are globally size-limited
(`PWANIMATE_MAX_DOC_SIZE` 25 MB, `PWANIMATE_MAX_EXTRACT_CHARS` 15,000). If you
intend to charge for storage or processing, that is a gap.

`ModelOffering` rows carry `min_tier`, `cost_class` (`free_route`,
`paid_flash`, `frontier`, `vision_external`), and per-M-token prices. The
router's tier gate (`router.py:234-244`) admits a candidate iff
`offering.min_tier.rank <= tier_rank`. Unregistered candidates are admitted
only if provably free (OpenRouter ids ending `:free`, or `mock`).

**The pricing logic is sound; the enforcement is missing.** See §0.1.

### The register is a ledger, not a control

`UsageRecord` writes provider, model id, cost class, token counts, image
count and `cost_usd_micros` on every turn. `UsageCounter` rolls it up per
user per window (`day`, `week`, `period`). Windows reset at local midnight
`Africa/Nairobi`; weeks start Monday.

This is excellent instrumentation and it is the raw material for everything
in §8–§10. It is **not** a training corpus — no prompts, no answers
labelled by outcome.

---

## 4. Model capabilities

Capability is **inferred, not declared**. If a turn carries an image,
`router.py:171-186` sets `required_capability = "image"` and the router
admits only image-capable models. That was a deliberate fix (2026-10-20) after
a text-only model was handed an image and refused the whole turn.

Today the filter is literal:

```python
supports_images = (
    p_name == "gemini" and model_id.startswith("gemini-")
) or (
    p_name == "groq" and model_id == "qwen/qwen3.8-27b" and image_count <= 3
)
```

OpenRouter is excluded outright. Gemini accepts any number of images; Groq
Qwen only ≤ 3.

**[RECOMMENDATION]** Replace this literal with a capability table on
`ModelOffering` (`capabilities` JSON already exists on the model). A new
provider should declare `{"image": true, "max_images": 5}` and the router
should read it. The current shape means every new vision provider requires
editing the router — a guaranteed mistake at 2am.

### 4.1 Provider reality check

| Provider | Free route? | Vision? | Notes |
|---|---|---|---|
| Gemini | no (paid key) | yes, unlimited images | primary; strong on long context |
| Groq | no (paid key) | yes, ≤3 images, one model | fast; good at code |
| OpenRouter | yes (`:free` models only) | **no** | free tier is text-only |
| Cloudflare Workers AI | **yes** (10k neurons/day) | yes | AI-scoped token; no zone access |
| Self-hosted (spark-vector) | yes, always | embeddings only | your VPS |

Note the asymmetry: **the only vision provider that can serve the free tier
without a third party's goodwill is Cloudflare.** That is the strategic core
of this document.

---

## 5. Spark Vision — the decision

### 5.1 What we measured

Live on your Cloudflare account, 2026-10-21, real image (720×1600 phone
screenshot), prompt = "describe precisely + transcribe all text":

| Model | Latency | Neurons | $/image | Prompt tokens |
|---|---|---|---|---|
| **`@cf/meta/llama-3.2-11b-vision-instruct`** | **10.7 s** | **35.8** | **$0.00039** | 4,844 |
| `@cf/meta/llama-4-scout-17b-16e-instruct` | 25.2 s | 88.9 | $0.00098 | 2,362 |
| `@cf/qwen/qwen3.8-27b` | 10.6 s | 164.8 | $0.00181 | 1,184 |

Quality on a synthetic medical slide: `llama-3.2-11b-vision` transcribed
"MRI AXIAL T2", "Diagnosis: Sinusitis", "Patient: Jane Doe Age: 34" verbatim
and described the layout correctly. Good enough for timetable extraction,
handwritten working, and diagram description.

Two setup facts worth recording:

- Llama 3.2 Vision requires a one-time `{"prompt": "agree"}` to accept Meta's
  community licence (**non-EU terms — confirm this fits your entity**). Done
  on your account.
- The OpenAI-compatible endpoint `/ai/v1/chat/completions` **rejects** the
  content-array image format. Use `/ai/run/` with OpenAI-style `messages` and
  an `image_url` data URI. `/ai/run/` is what works.

### 5.2 The recommendation

**Do not self-host the VLM. Do not bring your own model. Use Cloudflare
Workers AI's hosted open weights as the vision inference tier.**

Five reasons, in order of weight:

1. **Your own design doc already ruled out self-hosting.** D9 established the
   VPS is 2 vCPU / 7.7 GiB and "a VLM would not" fit. A CPU VLM
   (SmolVLM-500M, Qwen2-VL-2B) on 2 vCPU is 30 s – 5 min per image with poor
   OCR on the inputs you care about. You would trade 10 seconds for three
   minutes *and* worse answers.
2. **Pay-per-use beats owning hardware at your volume.** $0.39 per 1,000
   images. The cheapest VLM box that could host a usable model costs more
   than that in year one and needs babysitting.
3. **BYO is the wrong tool.** Cloudflare's custom/LoRA route is for changing
   model *behaviour*, not for relocating a model. You already get hosted
   Llama/Qwen/Mistral weights. Fine-tuning for lecture slides is a modelling
   project to do in §10, after you have labels — not an infrastructure
   project.
4. **It dissolves the D9 blocker.** D9 is stuck on "host TBD" because Oracle
   declined your card. There is no box to provision. The "vision box" becomes
   an endpoint you already have an account and a working AI-scoped token for.
5. **It preserves D2 in spirit.** D2 says the free tier must not depend on
   free-provider quota. Cloudflare is *your* account, your billing, your
   infrastructure relationship — not a lottery on a Gemini key. You keep
   `spark-vector` self-hosted so the hot path never touches any quota, and
   you cap the free tier in your own app so a spike cannot burn the shared
   allocation.

### 5.3 The Ember image budget — the real constraint

Cost is not the constraint. The constraint is that **10,000 neurons/day is
shared across every user of your Cloudflare account**.

```
10,000 neurons ÷ 35.75 neurons/image = 280 images/day free, account-wide
```

Ember at 5 images/day means **56 maxed-out free users exhaust the free
allocation**. After that every vision call bills you.

**[RECOMMENDATION] — three-stage policy:**

| Stage | Ember images/day | Global app budget | Monthly cost |
|---|---|---|---|
| Now (Workers Free) | 5 | 200/day | KES 0 |
| Budget binds → Workers Paid ($5/mo) | 10 | 2,500/day | ~$12 |
| Mature, cost-optimised | 20 | sized to margin | ~$0.39 / 1k images |

The **global budget is the control**; the per-user cap is the product
promise. When the global budget is exhausted, Ember vision degrades to the
text-only path with the honest *"Spark Vision is down"* notice you already
built — not a hard error, and never a silent wrong answer.

Paid tiers must **not** draw on the free budget. `ModelOffering.min_tier`
already expresses this: the `vision-external` offering (`cost_class`
`vision_external`, `min_tier = day_pass`) is the hook. Wire Ember →
free route, Day Pass+ → paid route, and the money follows the tier.

### 5.4 Load balancing, and whether you need a queue

**You do not need a load balancer. Your fallback chain already is one.**

`resolve_fallback_chain` produces an ordered candidate list;
`AIGateway.generate` walks it, and `QuotaTracker` records every 429 with a
60-second per-model cooldown so a throttled model moves to the back
automatically. Adding Cloudflare to the vision ladder gives you failover for
free — literally, it is one more dict in a list.

**Rate limits are a non-issue.** Workers AI: Text Generation 300 req/min,
Image-to-Text 720 req/min. Your entire university sending one image every
20 minutes is ~4 req/min. You will never approach it. (Models requiring
Workers Paid drop to 20 req/min — another reason to use the non-Paid models.)

**Queueing is a cost-control and UX mechanism, not a throughput mechanism.**

There are two distinct queues in play and they do different jobs:

| Queue | Purpose | Trigger |
|---|---|---|
| `docs_queue` (Celery) | move vision work off the request path | **should be** triggered at upload for images — currently isn't (§0.2) |
| in-app global image budget | protect the shared free allocation | checked before dispatching a vision call |

**[RECOMMENDATION]** Fix §0.2 first. With images analysed at upload and the
`vision_summary` stored on the attachment, vision becomes a **per-upload**
cost instead of a per-turn cost, the interactive path never blocks for 10
seconds, and the "analysed once" invariant becomes true. Everything else in
this section follows from that one fix.

### 5.5 What was built (2026-10-22)

All six items are done:

1. `pwanimate/ai/providers/cloudflare.py` — new `CloudflareLLMProvider`,
   vision-capable, reusing `PWANIMATE_CLOUDFLARE_API_TOKEN` /
   `PWANIMATE_CLOUDFLARE_ACCOUNT_ID` (already in `.env`). It reports the
   *neurons* an inference cost, which is what the account budget is
   denominated in.
2. `pwanimate/ai/capabilities.py` — the capability table. The router reads
   it; nothing is hardcoded in the hot path.
3. `pwanimate/ai/engines.py` — the vision ladder now leads with
   `cloudflare/@cf/meta/llama-3.2-11b-vision-instruct`, then
   `cloudflare/@cf/meta/llama-4-scout-17b-16e-instruct`, then Gemini and
   Groq as the paid backups.
4. `pwanimate/tasks/attachments.py` — the vision task is queued for images
   too, and claims the attachment atomically.
5. `pwanimate/billing/global_budget.py` — the account-wide daily image
   budget, atomic, and failing **open** (a budget that cannot be read must
   not take the product down with it).
6. `pwanimate/ai/vision_images.py` — downscaling and EXIF stripping, applied
   in every provider that sends an image.

**One operator action remains.** Llama 3.2 Vision needs its licence
accepted once, by a human, on your account:

```sh
curl -X POST \
  "https://api.cloudflare.com/client/v4/accounts/$CLOUDFLARE_ACCOUNT_ID/ai/run/@cf/meta/llama-3.2-11b-vision-instruct" \
  -H "Authorization: Bearer $PWANIMATE_CLOUDFLARE_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"prompt": "agree"}'
```

It has been accepted on the account as of 2026-10-22. The provider will say
so plainly if it is ever needed again.

---

## 6. Latency — what actually makes the response fast

Measured p50/p95 matters less than the number of *sequential round trips*.
Your current chat turn is: retrieve (1) → tool select (1) → generate (1) →
vision (0–1). Four sequential hops, the last being 10 s.

**Ranked by payoff:**

1. **Stop sending images on turns that already have a `vision_summary`.**
   Already designed (`orchestrator/service.py:405-422` injects the stored
   summary and skips the image). Enforce it by fixing §0.2 — today the image
   can still be re-sent under `vision_context_policy == "ask"`.
2. **Analyse at upload, not at first question.** Moves 10 s out of the chat
   path entirely. This is the single biggest latency win available.
3. **Stream the answer.** You already have SSE (`generate_stream`) with
   mid-stream interruption handling and delta coalescing. **But multimodal
   turns stay non-streaming today** ("vision→text fallback correctness first").
   Once vision is a background job with a stored summary, *every* turn can
   stream — vision no longer happens mid-turn. Re-enable streaming for
   multimodal.
4. **Cap the output.** `DEFAULT_TASK_POLICIES`: conversational 512 tokens,
   general 512, tool 1024, rag 4096. Most answers do not need 4096. A 512-
   token answer streams in ~2 s instead of ~12 s.
5. **Do not let the vision ladder retry serially on timeout.** A 10 s model
   that times out, then a 25 s model that times out, is 35 s of dead air.
   Set a per-attempt timeout well under the caller's patience budget and
   fail over fast. Currently `PWANIMATE_LLM_TIMEOUT` is 30 s — one attempt
   can eat the whole budget.
6. **Cache embeddings, not answers.** Retrieval is already fast; answer
   caching is a trap (stale, personalised).

**The single most important speed decision: keep the critical path free of
any model call that can take more than ~2 seconds.** Vision becomes a
background job with a stored artefact. Everything else on the critical path
is sub-second.

---

## 7. Self-hosting — the honest path

Your D9 split topology is right: `spark-vector` on the VPS (hot path), vision
elsewhere (cold path). What changes is that "elsewhere" can be rented
per-request instead of provisioned per-month.

**What is worth self-hosting, in order:**

| Model | Self-host? | Why |
|---|---|---|
| Embeddings (`multilingual-e5-base`, 768-d) | **Yes — already done** | hot path, high volume, tiny, zero marginal cost, no quota |
| A small VLM (SmolVLM-500M / Qwen2-VL-2B) | **No** | 2 vCPU cannot serve it at interactive latency; quality gap on slides |
| A mid VLM (Qwen2-VL-7B, Qwen3-VL) | Only with a GPU | needs 8–16 GB VRAM; a used L4 is ~$150–250/mo — your entire revenue at 100 users |
| A large VLM | Never | you cannot out-Google Google on their own weights |

**The one self-hosting move worth making later:** when and only when you have
(i) a labelled dataset of Kenyan lecture-slide images, (ii) a LoRA you have
fine-tuned on it, and (iii) a GPU you can rent for a few hours a week —
fine-tune *on* rented GPU, *serve* from Workers AI. You pay pennies for
training and keep pay-per-use serving. That is the only configuration where
self-hosting the weights beats renting the inference.

**Sequence:** rented inference (now) → rented inference + LoRA (§10) →
self-hosted *only* if the LoRA demonstrably beats the hosted base on your
own benchmark (§10.3) *and* the volume justifies a GPU. Do not skip to step
three.

---

## 8. Fine-tuning strategy

### 8.1 The prerequisite you do not have yet

**You have no outcome signal.** There is no thumbs up/down, no rating, no
task label, no prompt store. `UsageRecord` tells you *what was served and
what it cost*; `PwanimateMessage` tells you *what was said*. Nothing tells
you *whether it was right*.

Fine-tuning without labels is expensive guessing. So the first fine-tuning
step is not a training run — it is **instrumentation**:

1. **Persist the full prompt** (system instruction + retrieved context +
   messages) alongside the answer, with the serving model id. `UsageRecord`
   already stores token counts and model identity; store the text too,
   under a retention policy, with a user-facing switch.
2. **Add a feedback control** on every answer: helpful / not helpful, and a
   one-tap reason (wrong answer / didn't use my document / too slow / wrong
   language / other). Binary + reason is far cheaper for the student than a
   survey and far richer than nothing.
3. **Tag the task** on every turn: `explain`, `summarise`, `ocr`, `debug`,
   `quiz`, `chat`. This is the label you will fine-tune on.
4. **Version the prompt** in a `PromptTemplate` model. Right now prompts are
   string constants in `orchestrator/prompts.py`. You cannot A/B a prompt you
   cannot name.

With those four, three months of Kenyan usage gives you a dataset no
multinational lab has: **real Kenyan curriculum questions, real lecture
slides, real Kiswahili/English code-switching, real exam formats.** That
dataset *is* the moat. See §13.

### 8.2 What to fine-tune, and what not to

| Target | Fine-tune? | Why |
|---|---|---|
| Vision model (OCR on Kenyan slides, diagrams, handwriting) | **Yes — first target** | narrow, well-scoped, measurable, and Workers AI's LoRA tier exists |
| The chat persona / tone / Kenyan English register | **Yes — cheap LoRA** | small behaviour delta, big perceived quality |
| Retrieval / embeddings | **Yes — later** | your corpus is the differentiator |
| General reasoning | **No** | you will never beat frontier weights on reasoning; spend tokens, don't spend training budget |
| Tool selection | **Yes — cheap** | your 12 tools are a narrow classification problem; a small classifier beats an LLM planner and is 100× cheaper and faster |

**Start with tool selection, not vision.** It is a 12-way classification
over a domain you fully control, you already have the router, and a
fine-tuned classifier would cut latency and cost on *every* turn. Vision
fine-tuning is the higher-value prize but needs the image dataset first.

### 8.3 The measurable gate — never fine-tune without this

Before any fine-tune, define the benchmark:

- **Held-out set** of ≥ 200 real examples, split so no single student or
  document appears in both train and eval.
- **Metric** per target: OCR → character error rate and field-exact match on
  timetables; tool selection → accuracy + false-action rate (a wrong action
  is worse than no action); chat persona → LLM-as-judge against a rubric.
- **Ship gate:** the fine-tuned model must beat the hosted base on that
  metric by a margin larger than the eval's confidence interval, *and* be
  cheaper or faster, *and* not regress on the other tasks. Otherwise keep the
  base model. This is exactly what D9's "quality escape hatch" meant — apply
  it to every fine-tune, not just vision.

---

## 9. Data collection

### What exists today

| Store | Contains | Useful for |
|---|---|---|
| `billing.UsageRecord` | per turn: provider, model, cost class, tokens, image count, `cost_usd_micros` | cost attribution, tier design, capacity |
| `billing.UsageCounter` | per user per window rollups | quota enforcement |
| `pwanimate.PwanimateMessage` | role, content, citations, sources | conversation history |
| `pwanimate.PwanimateAttachment` | `vision_summary`, `vision_model`, `vision_error`, `vision_context_policy` | **the vision dataset** |
| `QuotaTracker` | in-memory 429 cooldowns | live health (not persisted) |

### What is missing

- No prompt persistence, no prompt versioning.
- No outcome/feedback signal.
- No dataset builder, no export, no labelling workflow, no training pipeline.
- `PwanimateMessage` does **not** record which model served it — only
  `UsageRecord` does, and the two are not joined. Join them.

### The dataset you should be building (and are accidentally building)

Every image attachment already carries `vision_summary` — a faithful
description of the image, produced by a real model, for a real student
question. That is a **labelled image dataset with a natural-language
reference answer**, accumulating automatically, today, for free.

Two things are missing to make it trainable:

1. The **user's question** that motivated the analysis is not stored on the
   attachment — it lives in the message. Join them.
2. The **ground truth** is missing. For timetables and diagrams you can get
   it cheaply: the structured form the student uploads. If a student uploads
   a timetable *and* the app can render it back as a table, you have
   (image → table) pairs with exact labels, for free, from real Kenyan
   timetables. That is the single best fine-tuning dataset you can obtain
   without a labelling budget.

**Start collecting ground truth now**: when a student corrects a vision
result ("no, the lecture is at 4pm"), store that correction as a label. One
tap from the student, one training example for you.

### Privacy and compliance (Kenya)

The Data Protection Act 2019 applies to student images, names, and academic
records. Before you build a training corpus:

- Get **explicit, unbundled consent** for model improvement, separate from
  the Terms of Service. Students must be able to opt out.
- Provide **deletion** that actually removes the derived artefacts, not just
  the row.
- Keep a **data-processing record** of what leaves the country. Sending
  student images to a US/EU inference provider is a cross-border transfer
  with legal meaning — document it.
- Images may contain **other people** (a classmate in a photo) who never
  consented. This is the sharpest legal edge in the whole product.
- Note the Meta licence position in §5.1 and keep a record of which models
  you are allowed to use.

---

## 10. Task-specialised models and routing

### The mechanism already exists

`ModelOffering` already carries `capabilities` (JSON), `min_tier`,
`cost_class` and prices. The router already resolves an ordered candidate
list and filters by tier. **You have 80% of a task-routing system.**

What is missing is the *task classifier* that picks the specialist.

### The design

```
turn arrives
   │
   ├─► task classifier  (fast, cheap, deterministic-first)
   │      output: {task, confidence}
   │      tasks: explain | summarise | ocr | debug | quiz | social | chat
   │
   ├─► task → specialist map
   │      ocr        → specialist-ocr        (fine-tuned VLM, cheap)
   │      debug      → specialist-code       (fine-tuned, Groq/CodeGemma)
   │      quiz       → specialist-pedagogy   (fine-tuned on Kenyan exam formats)
   │      summarise  → spark-vector local RAG + cheap LLM
   │      chat       → general ladder (Speed/Coda/Scholar)
   │
   └─► fall back to the engine ladder if the specialist is unavailable
```

The router's `request.metadata["fallback_chain"]` mechanism already carries
an engine-specific chain into the request, and `_prepend_offering` already
puts an explicitly requested offering first. A task→chain map slots straight
in.

### How to make the classifier fast and cheap

- **Do it with a fine-tuned classifier, not an LLM.** 12 tools + 7 tasks is
  a small classification problem. A distilled classifier runs in ~50 ms on
  CPU. Your current `LLMToolSelector` adds a full LLM round trip to every
  turn that mentions a tool — that is a whole extra second of latency for
  something a 50 MB classifier does better.
- **Deterministic-first.** The regex `ToolRouter` already handles many
  intents with zero latency and zero cost. Run it first; only escalate to
  the classifier (then to the LLM planner) when the regex does not fire with
  high confidence. This is a strict latency and cost improvement over the
  current always-LLM path.
- **Cache the classification** per (user, message hash). The same question
  repeated should not re-classify.
- **Confidence gate.** Below a threshold, fall back to the general ladder.
  A mis-specialised answer is worse than a general one.

### The specialised-model ladder, when you have them

Put the specialist **first** in the chain and the general models behind it,
and let the existing tier gate and cooldown logic handle the rest. A
specialist that is rate-limited moves to the back automatically — no special
logic needed.

---

## 11. Actions — turning Spark from a chatbot into an agent

This is the section that should expand your scope the most.

### Where you are

Twelve tools in `pwanimate/tools/`. **Eleven are read-only.** The single
write tool, `send_notification`, is strictly self-targeted. There is **no
messaging tool, no follow tool, no social action tool at all.** The tool
framework's own docstring states the contract: *"read-only capabilities over
existing domain services."*

That was a correct early decision. It is now the ceiling on what Spark can be.

### Why actions are the business

A chatbot is a feature. An agent that can *do things on the student's behalf*
is a platform — and platforms capture the daily habit that a chatbot never
does. Concretely, for a Kenyan university student:

| Action | Why it matters | Risk |
|---|---|---|
| **Message someone** | Spark drafts and sends a message to a classmate about notes, a group, or a lecturer | spam, wrong recipient, impersonation |
| **Follow someone** | follow a lecturer, a course rep, a study group | spam-follow, notification storms |
| **Post an announcement** | a group rep posts "Lecture moved to 2pm" | impersonation, moderation load |
| **Create a study group / event** | from a timetable conflict Spark detects | spam, moderation |
| **Submit an assignment** | fill and attach, human confirms | irreversible, academic-integrity risk |
| **Book a tutor slot** | convert a Q&A into a paid session | money movement, refunds |
| **Pay via M-Pesa** | top up, pay for a Day Pass, split a subscription with classmates | fraud, refunds, KES reconciliation |

M-Pesa Daraja is already integrated for subscriptions. **Payments are your
unfair advantage** — most global AI products cannot reach Kenyan students
without it. Agent actions that *spend money* (pay for a Day Pass, split a
subscription, book a tutor) are the ones a global competitor cannot copy
into Kenya without a local payments licence.

### The architecture for adding actions safely

Your tool framework is already the right shape — `BaseDomainTool` with
`parameters_schema`, `validate_parameters`, permission checks, and a
`ToolResult` taxonomy that distinguishes `permission_denied` from
`validation_error` from `tool_error`. Adding write actions is a policy
problem, not a framework problem.

Five non-negotiables, in order of importance:

1. **Confirmation before every consequential action.** The current LLM
   selector already gates `send_notification` behind explicit "notify me"
   phrasing and rejects scheduled reminders. That instinct is right and must
   extend: the model *proposes*, the student *approves*. Show the exact
   action ("Send to @amina: 'Notes for Tuesday are uploaded'") with a
   Confirm / Edit / Cancel. This is your defence against both spam and
   embarrassment — a student will forgive a bad draft, never a wrong send.
2. **Idempotency keys on every write.** If the model retries, the student
   must not get the message twice. Use a stable key derived from
   (user, action, target, conversation) and dedupe server-side.
3. **A dry-run mode that is real.** The model gets a full success response
   from a simulated write, so it can plan on the assumption the action
   happened, and the student's approval executes it for real.
4. **Rate limits on writes, not just on tokens.** A model that loops can
   send fifty messages. Cap writes per user per hour at the gateway, and
   make the cap visible.
5. **Undo where possible.** "Sent — undo" for 30 seconds. Not always
   possible (a sent message is sent), but design for it where you can.

Two structural notes:

- **The messaging app is frozen** (`messaging.frozen/`). Before Spark can
  message someone, that freeze has to be lifted deliberately. This is a
  product decision, not a technical one — but it is the gating decision for
  the highest-value action.
- **Academic integrity.** "Submit an assignment" is where an AI agent becomes
  a policy liability. Build the action, but design it as *assistance with
  explicit human authorship* — Spark prepares, the student submits, and the
  submission records that a human confirmed it. Do not build an autonomous
  submitter. Universities in Kenya will treat autonomous submission as
  academic misconduct, and the reputational cost dwarfs the feature.

### The honest ordering

Build in this order, because each step makes the next one safer and the data
from each step tells you what to build:

1. **Message someone** (draft + confirm + undo) — highest value, lowest risk,
   and it exercises the entire write-action pipeline.
2. **Follow someone** — trivial once messaging works; the notification
   system already exists.
3. **Pay via M-Pesa for something Spark recommends** (a Day Pass, a tutor
   slot, a shared subscription) — this is where your moat and your margin
   meet.
4. **Create a study group / event from a timetable conflict** — needs the
   groups app and moderation.
5. **Assignment assistance** — highest risk, do it last, never autonomous.

---

## 12. The gaps, ranked

Status as of 2026-10-22. Items 1, 2, 5, 6, 10 and 11 are **closed**; items
3, 4, 7, 8 and 9 are still open.

| # | Gap | Impact | Effort | Status |
|---|---|---|---|---|
| 1 | Daily tier caps are recorded but **not enforced** | free tier is unlimited; revenue model is fictional | small | **closed** |
| 2 | Images are **not queued at upload**; analysed lazily, sometimes twice | latency + double cost + broken "once" invariant | small | **closed** |
| 3 | No feedback signal / no prompt persistence | blocks all fine-tuning work | medium | **open** |
| 4 | Vision ladder depends on third-party quota | free tier depends on someone else's key | medium | **closed** (Cloudflare) |
| 5 | Image capability filter is hardcoded | every new vision provider needs a router edit | small | **closed** (table) |
| 6 | `QuotaService.check()` and the `queued` / `upgrade_required` states exist but are unused | dead code that looks like a feature | small | **closed** |
| 7 | Document uploads have no tier limit | unmetered cost | small | **open** |
| 8 | `QuotaTracker` is in-memory and per-process | cooldowns differ per worker; no cross-instance awareness | medium | **open** |
| 9 | `PwanimateMessage` does not record the serving model | cannot join answers to cost/model for evaluation | small | **open** |
| 10 | No global image budget | nothing protects the shared free allocation | small | **closed** |
| 11 | `vision_context_policy == "ask"` can re-send an image | double vision cost, contradicting the invariant | small | **closed** |

Item 3 is now the highest-value open item, and it is the prerequisite for
everything in §8–§10. It is also the cheapest: a thumbs-up/down control and
a task tag.

---

## 13. The uncomfortable questions

A founder's document should not only describe the system. It should argue with
you.

### 13.1 What is your moat, and is it the AI?

**The AI is not the moat.** You are using other people's models — Gemini,
Groq, OpenRouter, and (soon) Cloudflare. Any of them could add your features,
and OpenAI and Google are already ahead of you. If your moat is "we call
Gemini", you have no business.

Your actual moat candidates, ranked by how defensible they are in Kenya:

1. **The Kenyan curriculum corpus.** Exam papers, past papers, lecturer
   slides, course structure, the specific way a University of Nairobi or
   Kenyatta course is structured. Nobody outside Kenya has it. You can get
   it because your users upload it. **This is the one.**
2. **The social graph.** A study network across Kenyan universities, with
   real content and real stakes (grades, group work). Switching cost rises
   with every cohort. Network effects compound locally in a way they do not
   globally.
3. **Payments + distribution.** M-Pesa is already live. Combine it with
   campus ambassadors and WhatsApp — in Kenya, WhatsApp is bigger than your
   app and probably always will be.
4. **Local-language and code-switching quality.** Kiswahili/English academic
   register, Kenyan English. Small, real, and nobody is optimising for it.
5. **The model layer.** Weakest moat of all. Do not build your company on it.

**The uncomfortable implication: your fine-tuning strategy (§8–§10) is not a
cost optimisation, it is the moat.** Fine-tuning a hosted model on Kenyan
curriculum data does not make you a better chatbot. It makes the *base model
better at Kenyan university work in a way nobody else can replicate* — and
that is a business. If you do not intend to build that, do not build
fine-tuning at all; ship the hosted models and compete on distribution,
payments and the social graph, which is a perfectly good business too. But
know which one you are choosing.

### 13.2 Where the money actually comes from

Your tiers are KES 30 / 300 / 1,200. At 200 Pro Monthly subscribers you are
at KES 60,000/month — roughly USD 460. **That does not pay for a GPU, a
support team, or a marketing budget.** It pays for infrastructure and maybe
one contractor.

So which is it?

- **A large free base that monetises later** (Kenyan students are the
  cheapest possible user base, and the most likely to be your first paying
  cohort at 300 KES/month when they get a job). If this is the business, the
  free tier *must* be cheap — which is exactly why the Workers AI free
  allocation matters, and why you should be ruthless about the global image
  budget and the "analyse once" invariant. Every double-analysis is a
  student you could have served.
- **A small paying cohort with high willingness to pay** (tutors, campus
  administrators, study groups paying for seats). Teams at KES 1,200 with
  admin and receipts is this. Then the product is workflow, not chat, and
  the AI is a feature of the workflow.
- **An agent platform that transacts** (Spark books tutors, splits
  subscriptions, handles M-Pesa). Then you take a margin on transactions,
  and per-user AI cost is a cost of goods sold against real revenue.

**Pick one and design the tiers for it.** Right now the tiers look like a
chat app's tiers, which is the one option where you are guaranteed to lose
to free.

### 13.3 The questions that should keep you up

- **What happens when Gemini's free tier changes tomorrow?** Right now your
  entire free product depends on a Gemini key and a Groq key that you were
  given. If Gemini repriced, your free tier dies. Workers AI with your own
  account is the answer — and that is the real reason for this whole
  document, larger than the cost saving.
- **Your 2 vCPU box is the smallest thing in the architecture.** It serves
  daphne, Celery, Postgres, Redis, and spark-vector, and it has 7.7 GiB RAM.
  You are adding vision, a fine-tuning pipeline, and agent actions to it. At
  what point does a 29 GB disk and 2 vCPU become the thing that stops you
  shipping? Decide the trigger now: *when the web p95 exceeds X, or when
  disk exceeds Y, we provision a second box.*
- **Who answers support at 2am when Spark sends the wrong message?** Agent
  actions multiply support load by an order of magnitude. Your feedback
  system (§8.1) is also your support triage system. Build it before you
  ship writes.
- **What is the number you need to hit in 90 days?** Not revenue — usage. If
  Vasto is not serving N weekly active Kenyan students with M chat turns each
  by day 90, none of the fine-tuning or self-hosting plans matter, because
  you will not have the data.
- **Are you building for students or for the market?** Kenyan students are
  your users, but your *market* is whoever pays: students, universities,
  employers who want verified skills, or advertisers. These are different
  products. The Day Pass and Teams tiers hint that you are hedging.
- **If a global lab released a free, perfect Kenyan-curriculum tutor
  tomorrow, what would still be yours?** If the answer is "nothing", the
  defensible asset is the corpus and the graph, and you should be spending
  your time on data collection (§9) rather than on model serving (§5–§7).
  If the answer is "the relationships and the payments", you are building a
  network business and you should stop optimising inference cost entirely.

### 13.4 The three things to do next

1. **Enforce the caps and fix the vision pipeline** (§0.1, §0.2). These are
   small, they are correctness bugs, and they are what makes the billing
   system and the "analysed once" promise true.
2. **Move vision to Workers AI** (§5). It removes your largest third-party
   dependency from the free tier and costs you less than a cup of coffee per
   thousand images.
3. **Instrument for learning** (§8.1). Feedback + prompt versioning + task
   tags. This is the prerequisite for every other idea in this document, and
   it takes days, not months. Start it before you need it — you cannot
   retroactively collect the signal you didn't gather.

---

## Appendix A — File index

| Concern | Path |
|---|---|
| Engine registry | `pwanimate/ai/engines.py` |
| Gateway / fallback | `pwanimate/ai/gateway/gateway.py` |
| Router / chain / tier gate | `pwanimate/ai/gateway/router.py` |
| DTOs | `pwanimate/ai/gateway/types.py` |
| Provider ABC | `pwanimate/ai/gateway/base.py` |
| 429 cooldown tracker | `pwanimate/ai/gateway/quota_tracker.py` |
| Providers | `pwanimate/ai/providers/{gemini,groq,openrouter,mock}.py` |
| Embeddings (incl. self-hosted) | `pwanimate/ai/embeddings/*` |
| Billing models | `pwanimate/billing/models.py` |
| Billing services (quota/tiers) | `pwanimate/billing/services.py` |
| Tier + registry seed | `pwanimate/billing/management/commands/seed_model_registry.py` |
| Orchestrator | `pwanimate/orchestrator/{service,types,prompts}.py` |
| Vision task | `pwanimate/tasks/attachments.py` |
| Tool framework | `pwanimate/tools/{base,registry,router,selector}.py` |
| Tool domains | `pwanimate/tools/domain/*.py` |
| Settings | `pwaninet/settings/base.py` |
| spark-vector worker | `spark_vector/app.py` |
| Deploy | `deploy.sh`, `ops/clean-storage.sh` |
| Prior designs | `docs/local-models-design.md`, `docs/billing-tiers-design.md` |

## Appendix B — Verified Workers AI reference numbers

Measured on your account, 2026-10-21.

| Item | Value |
|---|---|
| Free allocation | 10,000 neurons/day (resets 00:00 UTC) |
| Paid rate | $0.011 / 1,000 neurons |
| Workers Paid minimum | $5/month |
| Rate limit, Text Generation | 300 req/min |
| Rate limit, Image-to-Text | 720 req/min |
| Rate limit, paid-only models | 20 req/min (50 with prepaid AI Gateway credits) |
| `@cf/meta/llama-3.2-11b-vision-instruct` | 4,410 neurons/M in, 61,493/M out |
| `@cf/meta/llama-4-scout-17b-16e-instruct` | 24,545 neurons/M in, 77,273/M out |
| `@cf/qwen/qwen3.8-27b` | 40,909 neurons/M in, 290,909/M out |
| Endpoint that works with images | `POST /accounts/{id}/ai/run/{model}` |
| Endpoint that does **not** | `POST /accounts/{id}/ai/v1/chat/completions` (rejects image content arrays) |
| Models requiring Workers Paid | kimi-k2.6, kimi-k2.7-code, glm-5.2, glm-5.3, glm-5.3-flash, deepseek-v4-flash-0731, deepseek-v4.1-flash-1017, deepseek-v4-pro-0813 |

**Your token:** `PWANIMATE_CLOUDFLARE_API_TOKEN` is **AI-scoped**. It works
for `/ai/run/` inference (verified) and **cannot** read zone settings, so
cache rules and purges need a separate token with zone permissions.
