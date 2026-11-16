# SALS 0.3 WP3 — Runtime Pipeline Audit & Cross-Turn Reuse Analysis

Status: **analysis only — no implementation in this document.** All timings below are
structural estimates derived from code inspection, not live measurements. No provider
calls were made. Live before/after numbers require the instrumentation described in §5
to be implemented and measured against real providers.

## 1. End-to-end pipeline map (composer → first rendered token)

```
Browser (pwanimate-chat.js)
  │  POST /api/pwanimate/chat/  Accept: text/event-stream  + CSRF
  │  typing indicator shown immediately; SSE reader armed
  ▼
PwanimateChatView.post()                                  [views.py]
  │  parses body on request thread; detects SSE
  ▼
_stream_chat_response()                                   [views.py]
  │  emits `event: status {"stage":"thinking"}` + 12s keep-alives instantly
  │  worker thread → _post_chat()
  ▼
_post_chat() — synchronous pre-model work (~20–40 DB queries)
  │  engine/offering resolve → conversation resolve/create
  │  → attachment auth + pending checks → quota check (QuotaService.check_turn)
  │  → study-session detect/start → WP2 follow-up short-reply resolve
  │  → persist_user_message → memory proposal (propose_from_message)
  │  → attachment linking → load_history → ConversationMemoryService.search
  │     └─ PersonalMemoryService.recall → PersistentMemoryService.retrieve
  │        (lexical DB scan; vector retrieval opt-in via SPARK_MEMORY_VECTOR_RETRIEVAL)
  │  → UnitLearningService.safe_context (enrollments/records/detect)
  │  → OrchestrationRequest assembled
  ▼
orchestrator.run()                                       [service.py]
  │  get_pending_friend_post_updates (notifications+follows+posts, DB)
  │  UserContextService().build (heavy: user relations, enrolled units, …)
  │  DocumentTurnContext.resolve (only when context_resources present)
  │  TeachingService.applies → session_for + attributed (UnitLearningService.detect)
  │
  ├─ TEACHING APPLIES ─────────────────────────────────────────────┐
  │   TeachingService.prepare  [transaction, select_for_update,    │
  │     deterministic phase machine, may create run+interaction]    │
  │   finish_agent → TeachingService.finish                        │
  │     ├─ cached turn.response_markdown → ZERO model calls         │
  │     └─ else ONE streamed answer call (_run_tool 'study_agent')  │
  │                                                                 │
  ├─ AGENT BRANCH (text-only, non-study, non-conversational) ───────┤
  │   StudyAgent.run  [orchestrator/agent.py]                       │
  │     personal_study context assembly (4+ DB service calls)       │
  │     LOOP ≤8 rounds:                                             │
  │       planner LLM call (huge tool-schema system prompt,         │
  │         task='tool', max_tokens=1600, temperature=0)            │
  │       → parse JSON → up to 3 tool executions                    │
  │         (retrieve_documents → UnifiedRetrievalService hybrid    │
  │          embed+DB; domain tools → owner-scoped DB)              │
  │     mandatory _revise_checklist LLM call if checklist exists    │
  │     → pause/run/interaction creation if needed                  │
  │   finish_agent → _run_tool(prepared) → ONE streamed answer call │
  │                                                                 │
  └─ FALLBACKS (StudyAgent returns None / no agent) ────────────────┤
      tool_selector.required_web_query (regex, free)
      → LLMToolSelector.select (ONE extra LLM call when might_need) │
      → _run_tool | _run_conversational | _run_rag                 │
      each ends in ONE streamed answer call                         │
  ▼
Post-response (does NOT delay first token, but delays `response` event)
  persist_assistant_message → create_follow_ups → PwanimateTurnTrace
  → SparkAgentRun.activity update → study checkpoint (Celery on_commit)
  → serialize payload → SSE `event: response`
  ▼
Browser: first `chunk` event → collapse activity trail → streaming row
  → Markdown render batched every 80ms → `response` event replaces row
```

## 2. Blocking-operation inventory before first answer token

| # | Stage | Type | Blocks first token? | Notes |
|---|-------|------|--------------------:|-------|
| 1 | Django auth/CSRF/body parse | CPU/DB | yes | unavoidable |
| 2 | Engine/offering/quota/conversation/attachment checks | DB (~10–15 q) | yes | additive; each fast on healthy PG |
| 3 | persist_user_message + memory proposal + attachment writes | DB writes | yes | required for ownership/idempotency |
| 4 | load_history + memory search + academic context + user context | DB (~10–20 q) | yes | rebuilt EVERY turn even when inputs unchanged |
| 5 | friend-post updates lookup | DB | yes | needed for response metadata |
| 6 | TeachingService.applies/prepare | DB txn + locks | yes | deterministic; already the fast path for study turns |
| 7 | **StudyAgent planner loop** | **LLM ×1–8 + tools ×0–15** | **yes — dominant** | each round = full planner call + up to 3 tool execs |
| 8 | _revise_checklist | LLM ×1 | yes | mandatory when checklist non-empty |
| 9 | LLMToolSelector.select | LLM ×1 | yes | only on fallback path |
| 10 | Answer generation (streamed) | LLM ×1 | first token here | `on_token` deltas → SSE `chunk` |
| 11 | Retrieval inside tools (embeddings) | provider + DB | yes | per tool call, per turn |
| 12 | Celery claim/dispatch | queue | resumed runs only | new turns run in request thread — no queue |

**Dominant cost hypothesis (to be verified by instrumentation):** for any
non-teaching tutoring question, stages 7+8 add **2–4 sequential LLM round-trips
before the answer call even starts**. At 5–10s/call on free-tier providers this
alone explains the observed ~30s. The ~25 DB queries in stages 2–5 are additive
but secondary (est. 50–500ms total on healthy infrastructure).

## 3. What is timed today vs. what is missing

**Exists:**
- `OrchestrationResponse.{retrieval_time_ms, generation_time_ms, total_time_ms}`
- `teaching_baseline` metrics: `response_ms`, `model_calls`,
  `answer_generation_calls`, `planning_calls`, `cache_hit`
- `SparkTeachingTurn.metrics.controller_ms`
- `PwanimateTurnTrace` row per turn (request_id, prompt/context versions,
  retrieval/memory traces, timing metadata) — the natural persistence point
- `SparkAgentRun` journal events + `attempt_id`/lease for durable runs
- Gateway logs provider/model/timing per call; chunk coalescing ≥24 chars

**Missing (needed for WP3 instrumentation):**
- request-received / context-assembled / orchestrator-start boundaries
- per-planner-round and per-tool-call durations inside StudyAgent
- first-provider-token timestamp (gateway only times the whole call)
- first-SSE-chunk-published and first-browser-render timestamps
- worker claim→start delay for Celery-resumed runs
- consolidated per-turn counters: model_calls, planning_calls, tool_calls,
  tokens in/out, cache hits — correlated by request_id/run_id

## 4. Cross-turn reuse analysis — the core question

### 4.1 What is ALREADY reused (verified in code)

| Asset | Store | Reused across | Mechanism |
|-------|-------|---------------|-----------|
| Tool observations (`observed`: tool+args+result) | `SparkAgentRun.state` | **resume of same paused run only** | `execute_claimed` → `StudyAgent.run(resume_state=…)` restores them; planner prompt receives them as `observations` |
| Tool call signatures (`seen`) | `SparkAgentRun.state` | same | prevents duplicate calls within/across resume |
| Context items from tools (`items`) | `SparkAgentRun.state` | same | re-fed to answer context without re-executing |
| Checklist | `SparkAgentRun.state` | same | `_normalize_checklist` restores; reconcile still runs |
| Conversation history | `PwanimateMessage` | every turn | `load_history` bounded by count/tokens |
| Teaching phase/mode/concept/exercise/hints | `SparkTeachingState`/`SparkTeachingTurn` | every teaching turn | deterministic `prepare`; cached `response_markdown` → zero model calls |
| Approved personal facts | `SparkPersonalMemory` | every turn | consent-filtered `PersistentMemoryService.retrieve` |
| Academic unit context | `SparkUnitLearningRecord` etc. | every turn | `UnitLearningService.safe_context` |

### 4.2 What is NOT reused — the actual waste

1. **Planner "thinking" is per-turn, never per-conversation.** A follow-up like
   "explain that step again" or "another example" starts `StudyAgent` with empty
   `observed`/`seen`/`checklist`. The planner re-derives a plan it already made
   one turn ago — ≥1 planner LLM call minimum, usually more.
2. **Tool results are re-fetched every turn.** `retrieve_documents`,
   `content_search`, `personal_study`, memory recall all re-execute. If turn N
   retrieved a document and turn N+1 asks a follow-up about it, the retrieval
   (embedding + hybrid search) runs again.
3. **No turn-scoped context memoization.** `user_context`, `academic_unit_context`,
   `conversation_memory` are rebuilt per message even when nothing changed.
4. **Checklist/plan state dies with the run.** A multi-turn lesson re-plans
   from scratch each message instead of continuing the prior checklist.

### 4.3 Reuse candidates, with safety constraints

| Candidate | Reuse mechanism | Safe? | Constraints |
|-----------|-----------------|-------|-------------|
| Prior-turn `observed` tool results | Seed new run state from last assistant turn's activity/trace | **Yes, bounded** | Exclude write/action tools; exclude `web_search` (freshness) or ≤5min TTL; owner+conversation scoped; cap N items; mark provenance "earlier turn" so planner treats as stale-able evidence |
| Deterministic read-tool results (`retrieve_documents`, `content_search`, `personal_study`) | Short-TTL cache keyed `(user, tool, canonical args hash, revision)` | **Yes, with TTL** | Owner-scoped rows only; invalidate on document/memory/concept writes; never cache write tools or approvals |
| Prior checklist for continuation queries | Carry checklist when classifier says "continue/same topic" | **Yes** | Only within same conversation+session; reset on topic change/stop |
| `user_context`/`academic_unit_context`/`conversation_memory` | Per-request memoization only | Low value | Rebuilt per HTTP request anyway; cross-turn caching risks serving revoked consent — **not recommended** beyond request scope |
| Teaching state | Already reused | n/a | Keep as-is |
| Cached teaching answer (`response_markdown`) | Already reused | n/a | Keep as-is |

### 4.4 What must NEVER be reused blindly

- **Write/action tools** (`create_reminder`, study blocks, `send_message`,
  `follow_user`, memory proposals): per-turn authorization regex + idempotency
  keys + approval flow must run every time.
- **`web_search`/current-events**: freshness-bound; cache ≤ minutes or never.
- **Consent-gated context**: academic tracking preference revision and memory
  consent are checked per turn; any cached context must be invalidated on
  `consent_revision`/`changed_at` changes or stored post-filter with short TTL.
- **Assessment integrity**: `TeachingService` evaluations, `AssessmentService`
  submissions and mastery evidence are deterministic per-turn — no caching.
- **Authorization**: conversation/session/attachment ownership re-validated
  every request regardless of cache hits.

## 5. Recommended reuse strategy (for WP3 implementation phase)

Ordered by leverage ÷ risk:

1. **Routine-tutoring fast path** (highest leverage): deterministic classifier
   routes "explain again / another example / continue / simpler / why" style
   follow-ups — *when an active teaching session or recent instructional turn
   exists* — straight to the single answer-generation call with existing
   conversation+academic context. Zero planner calls, zero tool calls. This is
   the "don't re-think" win and subsumes most reuse value for tutoring.
2. **Prior-observation seeding**: persist a bounded summary of the last turn's
   successful read-only observations (tool, args-hash, truncated result,
   timestamp) on the assistant message or turn trace; inject into the next
   `StudyAgent` state as `prior_observations` so the planner can answer
   follow-ups without re-calling tools. Planner prompt already accepts an
   `observations` array — this extends it across turns, not just resumes.
3. **Read-tool result cache**: small Django-cache/DB table for
   `(user, tool, args_hash)` → result with TTL ~10min, read tools only,
   invalidated on relevant writes. Defer unless measurements show retrieval
   dominates after (1)+(2).
4. **Checklist carry-over**: only if (1) proves insufficient for "continue"
   queries that still need multi-step work.

Explicitly **not** recommended: caching `user_context`/memory/academic context
across turns (consent-revocation risk outweighs the DB savings), and any reuse
of write-tool results or planner chain-of-thought.

## 6. Scenario budget model (pre-instrumentation estimates)

| Scenario | Today (est.) | After fast path (est.) | Notes |
|----------|-------------:|-----------------------:|-------|
| A. Simple tutoring follow-up | 3–5 LLM calls, ~15–35s | 1 answer call, ~3–8s | fast path |
| B. "Explain that step again" | same | 1 call or cached teaching turn | teaching cache may make it ~0 calls |
| C. Continue Linear Algebra lesson | teaching path already; ~1 call | unchanged | TeachingService already lean |
| D. Straightforward factual Q | planner bail→selector→answer: 2–3 calls | fast path 1 call if lesson context, else unchanged | classifier-gated |
| E. Solve+explain math problem | planner+tools+reconcile+answer: 3–5 calls | unchanged (complex → full agent) | realistic budget separate |
| F. Retrieve document context | +retrieval/embed per tool call | unchanged unless cache added | measure first |
| G. Generate assessment question | teaching path, ~1 call | unchanged | deterministic assessment preserved |
| H. Resume interrupted run | Celery queue + restore + remaining loop | unchanged; queue delay measured | durable path preserved |

These are hypotheses. §3's missing instrumentation must land first so every
claim in the final report is backed by `request_id`-correlated measurements.

## 7. Risks / open questions for implementation phase

- Fast-path classifier false positives: a "routine" query that actually needs a
  tool (e.g., "find the document about X") must never be fast-pathed. Mitigate
  with a denylist (search/find/create/remind/document/web/…) checked before
  the allowlist, plus tests.
- Prior-observation staleness: document/memory edits between turns. Mitigate
  with TTL + provenance marking + excluding mutable-source tools.
- Consent revocation between turns: any seeded context must be re-filtered or
  TTL-bounded; personal memory recall already re-checks consent per turn —
  seeded *tool results* containing memory text could bypass this → exclude
  memory-content observations from seeding, or re-validate.
- Celery queue delay on resumed runs: measure; if dominant, consider queue
  priority/routing — do not bypass durability.
- The ~25 pre-model DB queries: profile before optimizing; likely <10% of the
  30s but worth a `context_assembled` timestamp to confirm.
