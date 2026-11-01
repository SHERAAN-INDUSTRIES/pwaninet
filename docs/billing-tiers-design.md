# Billing & Tiers Design — Pwanimate Spark

**Status:** design (2026-10-20) — nothing implemented yet beyond what §"Build sequence" marks as done.
**Owner:** Pwanimate. **Stack:** M-Pesa Daraja (STK push), fee-per-transaction, no recurring infrastructure.

---

## 1. Goals and hard constraints

1. **$0 recurring cost for the free tier.** The free tier must cost us nothing in API spend. Every rung reachable by a free-tier student must be a genuinely free model (§4).
2. **The free tier is never denied service.** Caps *degrade* (queue + notify), they never block or 402. A student mid-assignment is never told "upgrade".
3. **Model locks are enforced server-side** (D8). The client can POST any model name today — that hole is closed at the gateway, not in the UI.
4. **Retrieval is tier-invisible** (D7): local `spark-vector` embeddings for every tier, so a paid tier buys *answer quality*, not search.
5. **No fixed infrastructure per tier.** Everything scales on the box we already run (2 vCPU, 7.7 GiB) plus per-call API cost paid out of revenue.
6. **The UI renders from the API.** No hardcoded model cards, no hardcoded prices (§7).

## 2. The cost model (this decides the prices)

**Measured token shape of one real turn** (from our own logs): prompt ≈ 8–12k tokens (our RAG context is *large* — the page-scoped attachment block alone is ~37k characters), output ≈ 1–2k tokens.

**Cost per call** (list prices, Oct 2026 — re-verify monthly, they drift):

| Class | Example | Input $/M | Output $/M | **Cost per turn** |
|---|---|---|---|---|
| `FREE_ROUTE` | Gemini free tier, Groq free tier, OpenRouter `:free` | 0 | 0 | **$0.00** (rate-limited, not billed) |
| `PAID_FLASH` | paid Gemini Flash tier, cheap OpenRouter models | ~0.075 | ~0.30 | **~$0.0014** |
| `FRONTIER` | frontier/pro-class models (e.g. `gemini-pro-latest`) | ~1.50 | ~6.00 | **~$0.030** |
| `VISION_EXTERNAL` | external VL per image | — | — | **~$0.005 / image** |

**What one price can afford** (1 USD ≈ 130 KSh):

| Price | USD | Frontier turns affordable/month | `PAID_FLASH` turns affordable/month |
|---|---|---|---|
| 30 KSh | $0.23 | ~7 | ~160 |
| 50 KSh | $0.38 | ~12 | ~270 |
| 300 KSh | $2.31 | ~77 | ~1,650 |
| 900 KSh | $6.92 | ~230 | ~4,900 |

*(less the ~1% + KSh 0.40 Daraja fee, and assuming a frontier turn costs $0.03)*

### 2.1 The answer to "day pass 30, monthly 50?"

**Not 50 for the month — 50 is priced for a *different product*.**

- **50 KSh/month ($0.38)** only breaks even if the customer uses ~12 frontier turns a month. Sell it as "a month of the premium model" and the heavy user (the one who actually wants to pay) costs us 3–5× what they paid. That is the classic mistake: your best customers subsidise your worst margin.
- **900 KSh/month (30 × 30)** is a different market entirely (~$6.90/month) — priced for institutions/teams, not for a student comparing it to a $2 data bundle.

**Recommendation:**

| Tier | Price | Duration | Frontier allowance | Why it holds |
|---|---|---|---|---|
| **Day Pass** | **30 KSh** | 24 h | 20 frontier turns/day | Worst case 20 × $0.03 = $0.60 vs $0.23 revenue… **so cap it at 10/day** ($0.30, ≈$0.38 gross) |
| **Pro Monthly** | **300 KSh** | 30 d | 5 frontier turns/day (150/mo = $4.50)… **cap at 3/day** (90/mo = $2.70 vs $2.31 + margin from flash) | Profitable at heavy use, attractive at light use |
| **Pro Lite** *(optional 5th)* | **50 KSh** | 30 d | **no frontier** — paid-flash + priority + external vision | 270 flash turns = $0.38 ⇒ break-even, never loses |
| **Teams** | **1,200 KSh/seat** (min 5) | 30 d | same as Pro | Negotiated, shared, admin |

**The rule underneath:** sell *model class and capability*, never "unlimited frontier". Every tier's worst case must be under its price.

## 3. Tier catalogue

| | **Ember** (free) | **Day Pass** | **Pro Monthly** | **Teams** |
|---|---|---|---|---|
| Price | KSh 0 | KSh 30 | KSh 300 | KSh 1,200/seat (min 5) |
| Duration | — | 24 h | 30 d | 30 d, renews as a group |
| Engines visible | all 4 | all 4 | all 4 | all 4 |
| Model class | `FREE_ROUTE` only | + `PAID_FLASH` | + `FRONTIER` (3/day) | + `FRONTIER` (10/day) |
| Chats/day | 20 (then queue + notify) | 60 | 200 | 500/seat-pool |
| Images/day | 5 (local VLM, queued) | 20 (external VL, immediate) | 60 (external VL) | 200 (external VL) |
| Chat priority | queue | priority | priority | priority |
| Embeddings/retrieval | local (identical) | local | local | local |
| Study Mode | ✓ | ✓ | ✓ | ✓ |
| Collections | personal | personal | personal | **shared + admin** |
| Receipts / VAT | — | ✓ | ✓ | ✓ + company |

*(Chats/day caps are **soft**: over the cap the request queues and notifies — never a block.)*

## 4. The $0 guarantee: every free-tier rung must be free

**Rule:** a `FREE_ROUTE` offering may only be served on a provider's *free* allowance (Gemini free tier, Groq free tier, OpenRouter `:free` ids).

**Verified 2026-10-20 against OpenRouter's live catalog:** `google/gemini-3.8-flash` has **no `:free` variant** — it is a *paid* model, and it sat in the 2nd–3rd rung of **every** engine ladder. Any free-tier chat falling past Gemini and Groq would have been **billed**. Replaced with `nvidia/nemotron-3-ultra-550b-a55b:free` (a 550B-class free flagship; the catalog has 17 free ids including `nemotron-3.5-super-vl:free` for vision).

Other free ids available for future rungs: `minimax/minimax-m3.1-flash:free`, `thinkingmachines/inkling:free`, `google/gemma-4-31b-it:free`, `cohere/north-mini-code:free` (code), `poolside/laguna-s-2.1:free` (code), `nvidia/nemotron-3-5-lightning:free`.

> **Operational rule:** any new ladder rung must be added to the registry with a cost class, and the CI check (§9) fails if a `FREE_ROUTE` offering points at a non-`:free` OpenRouter id or a paid Gemini/Groq model.

## 5. Which models in which tier (the matrix)

**Registry rows** (seeded from the current ladders; `min_tier` = the lowest tier allowed to reach it):

| Offering key | Provider / model | Class | min_tier | Notes |
|---|---|---|---|---|
| `gemini-flash-38` | gemini `gemini-3.8-flash` | FREE_ROUTE | Ember | identity primary for Speed/Scholar |
| `gemini-flash-36` | gemini `gemini-3.6-flash` | FREE_ROUTE | Ember | |
| `gemini-flash-lite-35` | gemini `gemini-3.5-flash-lite` | FREE_ROUTE | Ember | |
| `groq-gpt-oss-120b` | groq `openai/gpt-oss-120b` | FREE_ROUTE | Ember | Coda identity primary |
| `groq-gpt-oss-20b` | groq `openai/gpt-oss-20b` | FREE_ROUTE | Ember | |
| `groq-qwen-38-27b` | groq `qwen/qwen3.8-27b` | FREE_ROUTE | Ember | also the vision backup |
| `or-nemotron-ultra` | openrouter `nvidia/nemotron-3-ultra-550b-a55b:free` | FREE_ROUTE | Ember | replaced the paid gemini rung |
| `or-nemotron-super` | openrouter `nvidia/nemotron-3-super-120b-a12b:free` | FREE_ROUTE | Ember | |
| `or-nemotron-lightning` | openrouter `nvidia/nemotron-3.5-lightning:free` | FREE_ROUTE | Ember | |
| `or-liquid-lfm` | openrouter `liquid/lfm-2.5-2.6b:free` | FREE_ROUTE | Ember | |
| `or-super-vl` | openrouter `nvidia/nemotron-3.5-super-vl:free` | FREE_ROUTE | Ember | free vision backup (local VLM era) |
| `gemini-flash-paid` | gemini paid tier | PAID_FLASH | Day Pass | ~$0.0014/turn |
| `vision-external` | external VL (per image) | VISION_EXTERNAL | Day Pass | ~$0.005/image, immediate |
| `frontier-pro` | frontier/pro-class (e.g. `gemini-pro-latest`) | FRONTIER | Pro Monthly | **3 turns/day** on Pro, 10 on Teams |

**How a tier maps onto an engine:** the *engine* (Speed/Coda/Scholar/Vision) stays visible to everyone (§3). What a tier changes is **which rung of the ladder may be used**:
- Ember → the ladder is filtered to `FREE_ROUTE` rungs only.
- Day Pass+ → `PAID_FLASH` rungs become eligible (inserted after the free ones, so the free route still leads when it's healthy).
- Pro/Teams → `FRONTIER` rungs become eligible but are **allowance-gated** (3/day), and are only reached *after* the free/flash rungs fail — they are never the default path.

**The `gemini-pro-latest` question is now answered** (it was §12's open item): it is **Pro/Teams only**, and it must **leave the global Auto chain** (`PWANIMATE_LLM_FALLBACK_CHAIN`) — otherwise every free student reaches a frontier model by failover. It becomes a registry row with `min_tier = Pro Monthly`, reachable only when the chain filter admits it.

## 6. Enforcement (server-side)

**Tier resolution** (per request, cached in the request): `Subscription` (active, unexpired) → `Tier.rank`; no subscription or expired → `Ember` (rank 0).

**Chain filtering — the important one.** `LLMRouter.resolve_fallback_chain()` currently returns the engine ladder (or the global Auto chain). The filter runs *after* resolution and *before* the first attempt:

```
for candidate in resolved_chain:
    offering = registry.lookup(candidate.provider, candidate.model)
    if offering.min_tier.rank > user_tier.rank:  -> drop the candidate
```

Consequences:
- A free student's ladder contains **only** `FREE_ROUTE` rungs ⇒ the $0 guarantee is structural, not a convention.
- The frontier model is unreachable via failover for free students ⇒ no "one unlucky chain" leak.
- Providers without keys are still skipped silently, as today.

**Explicit requests.** Today the chat API accepts `provider`/`model` from the client (`views.py:450-451`). After the registry lands, a request naming an offering above the user's tier returns **403** with a machine-readable body:

```json
{"error": "model_locked", "required_tier": "pro_monthly",
 "offering": "frontier-pro", "upgrade_url": "/api/pwanimate/billing/mpesa/stk/"}
```
The UI turns that into a lock badge + upgrade CTA. A free-tier user is never *denied* — they are offered the path to unlock.

**Caps → degrade, never block.** Over-cap chats enter the existing queue + notify path (the same mechanism the free vision tier uses). No 402, no "upgrade to continue" wall.

**Cost accounting.** Every turn records `offering_id`, `cost_class`, `tokens` → `UsageCounter` (per user per day) and a `UsageRecord` for margin reporting (cost per tier, per day). This is what tells us whether 300 KSh is right.

## 7. API surface (the UI renders only from this)

| Endpoint | Purpose |
|---|---|
| `GET /api/pwanimate/billing/tiers/` | tiers + prices + the caller's tier + per-offering lock state + entitlement counters + upgrade copy. Also returns `premium[]` — the capability-named premium offering the caller's tier may use, with `allowed`, `required_tier` and `remaining_today` — so the picker renders its chip from the API and never hardcodes a model name |
| `GET /api/pwanimate/billing/quota/` | compact snapshot for the composer spinner: tier, state, chats/images used-limit-remaining, today's tokens, reset time |
| `GET /api/pwanimate/billing/usage/` | the full snapshot for the hover modal and the settings dashboard (all windows, tokens, 14-day series, entitlements) |
| `GET /api/pwanimate/billing/subscription/` | current subscription, expiry, usage counters (chats/images today) |
| `POST /api/pwanimate/billing/mpesa/stk/` | start a payment: `{phone, tier_code}` → `{checkout_id, message, expires_in}` |
| `POST /api/pwanimate/billing/mpesa/callback/` | Daraja webhook (public, rate-limited, idempotent) |
| `POST /api/pwanimate/billing/manage/cancel/` | cancel auto-renew (no auto-renew in v1 → 501 until then) |

**Requesting a premium model (D8 hard lock).** The chat payload may name an **offering** (a registry key, e.g. `frontier-pro`) — never a raw model name:

- Offering unknown → **400**.
- Offering locked for the caller's tier → **403** with `{error: "model_locked", offering, required_tier, required_tier_name, price_kes, message}` → the UI shows the lock and the upgrade path. The request is **not** silently downgraded: a student asking for a premium answer gets it or sees the price.
- Offering allowed → it is served **first** in the resolved chain; the free routes remain as the fallbacks, and the frontier model is only reached if they fail (within the day's allowance).

**UI behaviour:** the engine cards and the "premium" chips render their lock state from `tiers/`. A locked chip is visibly locked with the required tier and price; tapping it opens the payment sheet (phone → STK). No prices or tier names are hardcoded in JS (there is an existing settings-UI test asserting no pricing text — it will be updated *deliberately* when the billing UI lands, not silently).

## 8. M-Pesa Daraja flow (STK push)

**Built (2026-10-20), sandbox-ready with a demo mode.** The flow below is implemented
in `pwanimate/billing/daraja.py` (client), `pwanimate/billing/payments.py` (verification +
activation) and `pwanimate/billing/api.py` (endpoints). With no credentials configured the
client runs in **demo mode**: no network call, a synthetic checkout id, and a simulated
callback that goes through the *real* verification path — so the demo cannot drift from
production. Going live is only a matter of setting the credentials and flipping
`DARAJA_ENV=production`.

### 8.1 What Daraja needs from us (the checklist)

| # | Item | Where it comes from | Used for |
|---|---|---|---|
| 1 | **Registered business** with a KRA PIN, holding an M-Pesa **Paybill or Till** | Safaricom M-Pesa for Business | the shortcode we charge to |
| 2 | **Consumer Key** | Daraja portal (sandbox, then live) | HTTP Basic auth on every call |
| 3 | **Consumer Secret** | Daraja portal | HTTP Basic auth on every call |
| 4 | **Business ShortCode** (the paybill/till number) | Daraja portal | `BusinessShortCode` / `PartyB` |
| 5 | **Passkey** | Daraja portal | builds the request `Password` = base64(shortcode+passkey+timestamp) |
| 6 | **Initiator username + Security Credential** | Daraja portal | only for the reconciliation APIs (`Transaction Status`), not for STK push itself |
| 7 | **Approved STK-push access** for that shortcode | Daraja onboarding | we are calling `/stkpush/v1/processrequest` |
| 8 | **Callback URL allow-listed** in the Daraja account | Daraja portal → "CallBackURL"/domain allowlist | `https://pwanimate.app/api/pwanimate/billing/mpesa/callback/` must be added to the account's allowlist, and the domain must resolve publicly (Cloudflare tunnel ✓) |
| 9 | **Test MSISDNs** (sandbox) | Daraja sandbox docs | e.g. `2547XXXXXXXX` with any amount; the PIN prompt is simulated |
| 10 | **Live shortcode + credentials** (production) | Daraja live portal | switching `DARAJA_ENV=production` |

Nothing else is needed: no SDK, no webhook signature secret, no monthly fee — Daraja is
fee-per-transaction (~1% + KSh 0.40).

### 8.2 The flow

1. Client posts `{phone, tier_code}` → server validates the tier, creates a `Payment`
   (`status=pending`, `reference=SPARK-…`, **amount taken from the tier, never the client**),
   and sends the STK push with `TransactionType=CustomerPayBillOnline` (the customer cannot
   change the amount).
2. Daraja pushes the STK prompt to the phone; the customer enters their PIN.
3. Daraja calls back `/api/pwanimate/billing/mpesa/callback/` with `ResultCode` and the
   transaction code.
4. The server: **idempotent** (unique constraint on the Daraja transaction code) → on
   success, create/extend the `Subscription` (`tier`, `starts_at`, `ends_at = starts_at +
   duration_days`), mark the payment `paid`, notify the user (the `notifications` app already
   exists ✓).
5. On failure/timeout → `Payment.status = failed|expired`; the tier never activates.

**Endpoints**

| Endpoint | Auth | Purpose |
|---|---|---|
| `POST /api/pwanimate/billing/mpesa/stk/` | student | start a payment (`202` + checkout id + "check your phone") |
| `POST /api/pwanimate/billing/mpesa/callback/` | public (verified) | the Daraja webhook |
| `POST /api/pwanimate/billing/mpesa/demo-complete/` | student, demo only | simulate that webhook (demo mode) |
| `GET /api/pwanimate/billing/subscription/` | student | current subscription + recent payments |

### 8.3 Verification caveat (important) Daraja's basic callback is **not cryptographically signed** — anyone who learns the URL can post a fake "success". Mitigations, in order of cost:
- Only accept a callback whose `AccountReference` matches an existing **pending** `Payment` row *and* whose `Amount` equals that row's amount (both server-side facts a forger cannot know).
- Reconcile daily against Daraja's transaction-query API for the business paybill; quarantine mismatches.
- Rotate the paybill credentials if a forgery ever lands.

**Fees:** ~1% + KSh 0.40 per transaction ⇒ ~2.3% at 30 KSh, ~1.1% at 300 KSh. No fixed cost ✓ ($0-budget constraint holds).

**No auto-renew in v1.** A Day Pass/Monthly is a single payment; renewal is a second STK push the user initiates (recurring Daraja mandates need extra paperwork and dispute handling — out of scope for v1).

## 9. Build sequence (ordered, each independently shippable)

This is the canonical order — the accounting stages from §10.7 are folded in, because the dashboard cannot be truthful before the counters exist.

1. **Registry + models, no behaviour change.** `Tier`, `ModelOffering`, `Subscription`, `Payment`, `UsageRecord`, `UsageCounter`; `manage.py seed_model_registry` (seeds tiers + offerings from the ladders). Tests: schema, seeding, ordering. *Nothing changes for users yet.*
   **✅ BUILT (2026-10-20):** `pwanimate/billing/` app — `Tier`, `ModelOffering`, `Subscription`, `UsageRecord`, `UsageCounter` + `seed_model_registry` (idempotent, derived from the live ladders) + `check_model_registry` (the $0 guard: every rung registered, every free rung actually free) + 18 tests.
2. **Tier resolution + chain filtering.** Resolve the caller's tier; filter resolved chains by `min_tier`; **remove `gemini-pro-latest` from the global Auto chain**; add the CI check that a `FREE_ROUTE` offering is a genuinely free id (§4). *Effect: the $0 guarantee becomes structural.*
   **✅ BUILT (2026-10-20):** the router now withholds any candidate above the caller's tier (`LLMRouter._filter_by_tier`), the caller's tier rank travels in the request metadata (`OrchestrationRequest.billing_metadata()`), and an unregistered candidate is withheld from the free tier unless it is provably free (an OpenRouter ':free' id). **Verified:** an Ember request resolves to `[gemini-3.8-flash, gpt-oss-120b]` — the frontier rung of the global Auto chain is gone; a Pro request keeps it as the last resort, with the free routes still leading. The frontier rung stays in the global chain because the filter (not the list) is what gates it — one place to reason about, and Pro keeps it as a fallback.
3. **Usage accounting.** `UsageService.record()` on every chat turn, writing `UsageRecord` + rolling `UsageCounter` (§10.2). *Effect: we finally keep the token numbers we already receive.*
   **✅ BUILT (2026-10-20):** `UsageService.record_turn()` (append-only record + atomic `F()` rollups for the day/week/period windows, real provider token counts, per-offering cost) hooked into the orchestrator's three return paths — fail-open, never breaks a turn. Verified live: a real turn (Gemini → Groq fallback) recorded `prompt=3950 completion=80 total=4030 class=free_route cost=0`.
4. **Quota windows + entitlements.** `Tier.entitlements`, `QuotaService.check()`, the day/week/period windows, and the queue+notify degradation for over-cap actions (§10.3–10.4). *Effect: caps are real.*
   **🟡 PARTIAL:** the calculation is built and tested — `QuotaService.check()` returns `allowed` / `queued` / `upgrade_required` with used, limit, remaining and reset times, over the day (local midnight EAT), week (Monday) and period windows; entitlements come from `Tier.entitlements`; a subscription raises them and an expired one falls back to the free tier. **Still to do:** the request path must consult it (over-cap chats enter the queue+notify path instead of running) — that is the piece that makes the cap *enforced* rather than *reported*.
5. **Indicators.** The composer chip (`14/20 chats left today`) and the picker's frontier-allowance line, both derived from the same counters the enforcement uses (§10.5).
   **🟡 BACKEND READY:** `GET /api/pwanimate/billing/quota/` returns exactly the compact payload the chip/spinner needs (tier, state, chats used/limit/remaining, images, today's tokens, reset time), and `GET /api/pwanimate/billing/tiers/` returns the **premium capability** the caller's tier may use (`premium[]`: key, capability label, allowed, required tier, remaining today) so the picker can render the chip without hardcoding a model name. **Still to do:** the UI (the quota spinner and its hover modal, the premium chip).
6. **Usage dashboard.** The settings → Usage tab, the `/billing/usage/` API, the 14-day sparkline, the entitlement list and the upgrade CTA (§10.6). *Update the existing settings-UI test deliberately here — its intent was "don't fabricate numbers", and real accounting satisfies it.*
   **🟡 BACKEND READY:** `GET /api/pwanimate/billing/usage/` returns the full snapshot (all windows, tokens, the 14-day series, the entitlement list) and `GET /api/pwanimate/billing/tiers/` the catalogue. **Still to do:** the settings tab UI and the CTA wiring.
7. **Daraja: STK + callback + activation.** Sandbox first, then live; idempotent callbacks; receipts; activation notification (§8).
8. **Teams**: seats, admin, shared collections, company receipts.
9. **Margin reporting**: cost per tier per day (from `UsageRecord.cost_usd_micros`) — the number that tells us when to adjust 300 KSh, and the early-warning for free-route extinction.

## 10. Usage accounting, quota windows, and the Usage dashboard

*(This section came out of a review question: "do we show the user their real quota and token usage?" — the honest answer was **no**, and this is the design that fixes it. It is deliberately buildable **before** payments exist.)*

### 10.1 What exists today (the honest inventory)

| Thing | State |
|---|---|
| Per-user usage/quota records | **none** — no models, no counters |
| Token accounting | providers return `prompt_tokens / completion_tokens / thoughts_tokens / total_tokens`; the view **logs** them (`views.py:575-586`) and discards them |
| Settings → Usage tab | renders a profile card, the conversation count, a *"Detailed Usage Metrics in Preparation"* placeholder and a static capability list. A test (`test_settings_ui.py`) asserts it shows **no fabricated tokens, prices or tiers** — the intent was "don't invent numbers" |
| Provider cooldown tracker (`ai/gateway/quota_tracker.py`) | **global per provider/model**, not per user — it answers "is this provider throttled right now", not "how much has this student used" |
| Free-tier daily caps (20 chats / 5 images) | designed (§3) but **not enforced anywhere** |

**Conclusion:** there is nothing to display yet. To show real usage we must first *keep* what we already receive.

### 10.2 Accounting model (record once, read fast)

**`UsageRecord`** — one row per metered action (append-only, the source of truth):

| Field | Notes |
|---|---|
| `user` | FK |
| `created_at` | when it happened (drives the day/week/period windows) |
| `kind` | `chat` · `image` · `tool` · `retrieval` |
| `offering` | FK → `ModelOffering` (which model actually served it) |
| `cost_class` | `FREE_ROUTE` / `PAID_FLASH` / `FRONTIER` / `VISION_EXTERNAL` (denormalised, so history survives catalogue edits) |
| `prompt_tokens`, `completion_tokens`, `total_tokens` | real provider numbers, never estimated |
| `images` | count for vision turns |
| `cost_usd_micros` | our own cost estimate, computed at write time from the class price table (§2) |
| `source` | `chat` / `study` / `api` |

**`UsageCounter`** — a rollup for fast reads (`user`, `window_type` ∈ {day, week, period}, `window_start`, `chats`, `images`, `premium_turns`, `prompt_tokens`, `completion_tokens`, `cost_usd_micros`), unique on `(user, window_type, window_start)`. Updated in the same transaction as the record, so the dashboard never scans raw history.

**Write path:** the orchestrator already knows the serving model and the token counts → one call to `UsageService.record(...)` after the turn completes (inside the existing `transaction.atomic()` that persists the assistant message). Failed turns are recorded with `kind='chat'` and zero tokens? **No** — failed turns record nothing (no tokens were spent beyond the provider's error), except retries, which *do* cost money and must be recorded (the retry path in `tasks/embeddings.py` is a different meter: ingestion, not chat).

### 10.3 Quota windows — daily, weekly, period

**Three windows, three different jobs** (the user's daily+weekly instinct is right; the third is needed for paid tiers):

| Window | Bounds | Why |
|---|---|---|
| **Day** (rolling, resets 00:00 EAT) | daily cost + abuse | the number that actually protects the $0 budget, and the one a student understands ("today") |
| **Week** (Mon 00:00 EAT) | sustained use | catches "at the cap every single day" — a daily cap alone permits 7× the intended monthly use |
| **Period** (subscription length: 24 h / 30 d) | the paid allowance | the entitlement a customer bought; daily/weekly caps must not be so tight that a monthly subscriber feels starved |

**Units — what is capped vs what is displayed:**

- **Capped (actions):** chats, images, premium/frontier turns. These are the things a student recognises and the things that cost money.
- **Displayed (not capped):** prompt/completion tokens. Tokens are a *by-product of context size* (our page-scoped attachment block alone is ~10k tokens), so capping tokens would punish students for using the features we built. Tokens are shown for **transparency and for our margin reporting** — not as a "score".

**Entitlements per tier** (the cap values; the numbers in §3 are the starting point):

| | Ember | Day Pass | Pro Monthly | Teams |
|---|---|---|---|---|
| chats/day | 20 | 60 | 200 | 500 (pooled) |
| chats/week | 60 | 300 | 1,000 | 2,500 |
| images/day | 5 | 20 | 60 | 200 |
| frontier turns/day | 0 | 10 | 3 | 10 |
| tokens | *unlimited, displayed* | same | same | same |

*(A "chats/week" of 60 for Ember ≈ the daily cap × 3, not × 7: the weekly window is the real ceiling, the daily one is the smoothing.)*

### 10.4 Enforcement (and the "never denied" rule)

`QuotaService.check(user, kind)` runs **before dispatch** and returns one of:

- `allowed` → proceed.
- `queued` → over a cap but on a tier whose over-cap behaviour is queue+notify (Ember chats, all images) → the request enters the **existing queue + notify path** (the same mechanism the free vision tier uses) and the user is told when it lands. **Never a 402, never a silent drop.**
- `upgrade_required` → the action is only available on a higher tier (frontier turns on Ember) → the UI shows the lock + CTA; the *request* is not silently downgraded, because a student asking for a premium answer should either get it or see the price.

**Ordering of checks:** tier entitlement first (is this action permitted at all?), then daily, then weekly, then period. A request blocked by the daily cap but inside the weekly allowance is *queued*, not blocked.

### 10.5 Indicator surfaces (the student sees it before it bites)

1. **Chat composer** — a small, quiet chip next to the send button: `14/20 chats left today` (and `3 images left` when images are staged). It turns amber at ≤ 20% remaining and shows `Queued — your next answer lands in ~2 min` when the cap is hit. It is *derived from the same counters the dashboard uses*, so it can never disagree with the dashboard.
2. **Settings → Usage dashboard** — the full picture (§10.6).
3. **Engine picker** — premium chips show the frontier allowance remaining (`2/3 premium turns left today`).
4. **Upgrade CTA** — appears at the moment a cap is reached, never before it.

### 10.6 The Usage dashboard (settings → Usage)

Replaces the "in preparation" placeholder with the real thing. Every number comes from `UsageCounter`/`UsageRecord`; **no estimates, no fabricated prices** (the existing settings-UI test is updated deliberately at that point — its intent was "don't invent numbers", and real accounting satisfies it):

```
┌ Ember · Free ─────────────────────────────┐
│  Today          14/20 chats · 3/5 images    │  ← rings/bars, live
│  This week      26/60 chats                 │
│  Frontier       not included in your tier  │  ← or 2/3 turns left (paid)
│  Resets in      4h 12m (daily) · Mon 00:00 (weekly)
├ Real usage (last 14 days) ─────────────────┤
│  ▁▂▃▅▂▁▁▃▄▂▁▃  chats per day             │  ← sparkline, real
│  Prompts 412k · Answers 38k tokens this period
│  23 chats · 2 images · 0 premium turns
├ What your tier includes ───────────────────┤
│  ✓ All four Spark engines  ✓ Local retrieval (unlimited)
│  ✓ Campus documents  ✓ Peer discovery
│  [ Upgrade to Day Pass — KSh 30 ]          │  ← only when it unlocks something
└────────────────────────────────────────────┘
```

**API:** `GET /api/pwanimate/billing/usage/` →
```json
{"tier": {"code": "ember", "name": "Ember", "price_kes": 0},
 "windows": {"day": {"chats": {"used": 14, "limit": 20, "resets_at": "..."},
                     "images": {"used": 3, "limit": 5},
                     "premium_turns": {"used": 0, "limit": 0}},
              "week": {"chats": {"used": 26, "limit": 60}}},
 "tokens": {"period": {"prompt": 412000, "completion": 38000}},
 "series": [{"date": "2026-10-14", "chats": 12, "images": 0}, ...],
 "entitlements": [{"key": "vision", "label": "Image analysis", "included": true, "limit": 5}]}
```

### 10.7 Build order

The canonical sequence is §9 (accounting is stage 3, caps stage 4, indicators stage 5, dashboard stage 6) — all of it **independent of payments**, so the student-visible truth can ship before Daraja exists.

**Truthfulness checklist (applies to every number we ship):** it is recorded, not estimated · it comes from the same counter the enforcement uses · the reset time is stated · a queued request says "queued" · a cap we don't enforce is never shown.

## 11. Open questions

- **Teams commercials**: who pays (student or institution), min seats, seat reassignment, VAT/invoicing for Kenyan institutions.
- **Pro Lite (50 KSh)**: ship it as a 5th tier or use it to make Pro Monthly feel expensive?
- **Refund policy**: within 24 h? proportional?
- **Price drift**: provider list prices change monthly; the break-even table must be re-verified (and the allowance caps adjusted) — a 2× price rise on frontier models turns Pro Monthly from profitable to loss-making.
- **Free-route extinction**: if Gemini/Groq/OpenRouter free tiers disappear, Ember becomes $0-cost-impossible; the fallback is to fund Ember from Pro revenue (§9.7 margin reporting would show it) or to move Ember to `PAID_FLASH` with a hard cap and shrink the free tier's promise.
- **Vision allowance for paid tiers**: priced per image (~$0.005) — is 20/day the right number, or should external vision be metered by credits instead of a cap?
- **Student verification**: is a phone number enough to hold a paid tier, or do we need a verified student ID (institutional fraud risk)?
