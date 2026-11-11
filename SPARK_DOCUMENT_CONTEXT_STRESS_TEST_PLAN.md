# Spark Document Context — Stress Test Plan

**Purpose:** Dedicated stress-testing campaign for the page-aware document
context pipeline implemented per `SPARK_DOCUMENT_CONTEXT_IMPLEMENTATION.md`.
Run in a staging/test environment only — **no destructive or uncontrolled
production load tests**.

**Prerequisites**

- Staging deployment with the implementation merged.
- A corpus of test documents (see §1) owned by test student accounts.
- `PWANIMATE_PAGE_MAX_CHUNKS`, `PWANIMATE_PAGE_EVIDENCE_CHAR_BUDGET`,
  `PWANIMATE_EXPLICIT_CONTEXT_MAX_ITEMS` left at defaults unless a scenario
  says otherwise.
- Access to `PwanimateTurnTrace` / orchestrator logs and the
  `metadata.document_context` block in chat responses.

---

## 1. Test corpus

| Fixture | Spec | Purpose |
|---|---|---|
| D1 small PDF | ~10 pages, text-based | baseline correctness |
| D2 large PDF | 300–800 pages, dense text | budget, latency, chunk caps |
| D3 long-page PDF | pages with 3–10k words | oversized-page truncation |
| D4 scanned PDF | image-only, no text layer | unindexed/unsupported honesty |
| D5 mixed PDF | text + equations/tables/figures | modality honesty, math pages |
| D6 multi-version doc | v1 + v2 uploaded | version pinning |
| D7 pending-embed doc | extraction done, embeddings queued/failed | text_only path |
| D8 restricted doc | unit/programme-restricted | permission isolation |
| D9 private doc | owner-only visibility | cross-student isolation |
| A1 chat attachment | PDF uploaded via attachment rail | attachment parity |

## 2. Scenario matrix

### S1 — Identity & resolution stress

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S1.1 | Picker-added doc | Add D1 via context picker → ask page question | `document_context.resolved_count=1`; answer cites D1 page N |
| S1.2 | Citation-opened doc | Click a Spark citation → ask follow-up | Resolves via numeric `document_id`; same page evidence |
| S1.3 | Preview-only doc | Open D1 in Preview, never "+ Add to Context" | Auto-included via `previewResourceState`; resolves |
| S1.4 | Legacy client | POST `context_resources` with UUID in `documentId` only | Resolves (was the P0 bug) |
| S1.5 | Both IDs match | Send int `document_id` + matching `documentShareId` | `resolved` |
| S1.6 | Conflicting IDs | int ID of D1 + share UUID of D2 | `identifier_conflict`; **no content from either**; warning shown |
| S1.7 | Invalid + valid | `documentId="garbage"`, valid `documentShareId` | Resolves via share ID |
| S1.8 | Missing doc | Random UUID | `inaccessible`; generic warning; no existence leak |
| S1.9 | Multi-doc | D1 + D2 + D6 in context | All authorized resolve; `filter_document_ids` has 3; evidence budget respected |

### S2 — Page tracking & navigation

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S2.1 | Scroll navigation | Scroll PDF viewer → ask "what is on this page?" | `viewer_page` in metadata equals visible page |
| S2.2 | Control navigation | Next/prev page buttons → ask | Same as above |
| S2.3 | Direct page jump | Type page number in viewer → ask | Same |
| S2.4 | Rapid navigation | Flip 10 pages quickly, send mid-animation | Snapshot = page at send time, not earlier |
| S2.5 | Navigate during streaming | Send question on p.5, turn to p.9 while answer streams | Response still grounded on p.5 |
| S2.6 | Same-doc re-preview | Preview D1 p.3 → preview D1 citation p.20 → navigate → ask | Page state follows the live viewer, not stale object |
| S2.7 | Preview ↔ Context tab | Same doc in both surfaces | No duplicate resources; single page state |
| S2.8 | Mobile sheet | Repeat S2.1–S2.3 on mobile viewport | Same snapshot semantics |
| S2.9 | Remount | Close/reopen rail viewer → ask | Page persists via canonical key |

### S3 — Retrieval correctness

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S3.1 | Exact page | p.12 question on D1 | `retrieval_origin=active_viewer_page`, `match_type=exact_page`, page 12 chunks first |
| S3.2 | Explicit page | "Explain page 30" while viewing p.12 | Both pages retrieved; p.30 tagged `explicit_page` |
| S3.3 | Comparison | "Compare this page with page 20" | Both present, labeled separately |
| S3.4 | Incidental mention | "On page 12 it mentions page 4" | p.4 not force-targeted |
| S3.5 | Document-wide | "Summarize the entire document" on D2 | `document_overview` origin; not only current page |
| S3.6 | "Summarize this page" | Page-scoped summary | Page evidence, NOT document overview |
| S3.7 | Spanning chunk | Page inside a multi-page chunk | Citation shows `pp. X–Y` range |
| S3.8 | Cross-doc isolation | D1 question with D2 also in context | Evidence only from D1 unless query warrants D2 |
| S3.9 | Neighbor pages | Page-scoped question | ±1 pages present as `neighbor_page`, ≤2 chunks each |
| S3.10 | Semantic supplement | Question needing info off-page | Hybrid results still arrive via `retrieval_resp` |

### S4 — Embedding lifecycle

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S4.1 | Pending embeddings | D7, ask page question | Page text returned with `text_only`; `embeddings_pending` warning |
| S4.2 | Failed embeddings | Force embedding failure on D7 | Same usable text path |
| S4.3 | Deactivated chunks | Old version chunks (`is_active=False`, status `completed`) | **Excluded** from page lookup |
| S4.4 | Semantic still strict | Same pending doc, semantic query | Inactive chunks absent from vector results |
| S4.5 | Recovery | Embeddings complete → re-ask | `text_only` gone; normal retrieval |

### S5 — Authorization & privacy

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S5.1 | Private doc | Student B selects Student A's private doc ID | `inaccessible`; no content; generic warning |
| S5.2 | Restricted doc | Non-enrolled student selects D8 | `inaccessible`; enrolled student resolves |
| S5.3 | Unavailable | D-unavailable in context | `unavailable` warning, honest answer |
| S5.4 | Draft/processing | D-draft in context | `awaiting_processing` warning |
| S5.5 | Deleted doc | Soft-deleted doc share ID | `inaccessible` (no distinction from missing) |
| S5.6 | IDOR attempt | Forge `document_id` of unauthorized doc | Fail closed before any content fetch |

### S6 — Scale & performance

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S6.1 | Large PDF | D2 (500+ pages), page question | p95 resolution+page-fetch < ~800ms; `page_chunks_partial` may flag honestly |
| S6.2 | Long page | D3 dense page | ≤ `PWANIMATE_PAGE_MAX_CHUNKS` chunks; boundary truncation; partial warning if capped |
| S6.3 | Multi-doc | 5 docs in context | `filter_document_ids` ≤5; total page evidence ≤ char budget |
| S6.4 | Repeated questions | 20 rapid questions same page | Cached resolution per request; no leak; stable latency |
| S6.5 | Concurrent sessions | 25 simulated students, mixed docs | No cross-user leakage; no 5xx; query counts bounded |
| S6.6 | Query count | Single doc question | Document resolution ≤ ~6 queries; page fetch ≤ ~4 |

### S7 — Route coverage

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S7.1 | RAG route | Open-ended page question | Doc evidence + warnings in response metadata |
| S7.2 | Deterministic tool | "search my documents for X" (content_search) | `metadata.intent=tool`; `document_context` present; page evidence merged (≤6 items) |
| S7.3 | Quick actions | Explain page / Summarize page / Quiz page | Same context contract as free-form |
| S7.4 | Conversational | "hi" with doc open | Normal greeting; doc warnings still surface if doc broken |
| S7.5 | Web search + doc | Current-events question with doc open | Web results authoritative; doc evidence doesn't pollute |
| S7.6 | SSE streaming | Any doc question | Stream intact; snapshot stable through stream |

### S8 — Honesty & citations

| # | Scenario | Steps | Pass criteria |
|---|---|---|---|
| S8.1 | Scanned PDF | Question on D4 | Spark states no readable text; never claims to see the page |
| S8.2 | Figure-only page | D5 page with only an image | Honest limitation; surrounding text may be cited correctly |
| S8.3 | Citation accuracy | Answer citing p.30 content while viewing p.12 | Citation badge points to p.30, not p.12 |
| S8.4 | Version cite | D6 v2 question | `version_number`/`document_version_id` = latest |
| S8.5 | Partial coverage | Oversized page capped | `page_context_partial` warning; no "I read the whole page" claim |

## 3. Measurement checklist

Record per scenario: resolution latency, page-fetch latency, total orchestration
ms, `total_items`, `estimated_tokens`, DB query count (django-debug-toolbar or
`CaptureQueriesContext`), warning codes emitted, and `metadata.document_context`
status JSON.

**Budgets (initial targets, tune after baseline):**

- Identity resolution: < 50ms p95 for ≤3 docs
- Page evidence fetch: < 300ms p95
- Total added overhead vs no-context turn: < 15%
- Explicit context items: ≤ `PWANIMATE_EXPLICIT_CONTEXT_MAX_ITEMS`
- Page evidence chars: ≤ `PWANIMATE_PAGE_EVIDENCE_CHAR_BUDGET`

## 4. Exit criteria

- All S1–S3, S5, S7 scenarios pass.
- S4/S6 pass or deviations documented with root cause.
- S8 honesty scenarios: zero instances of Spark claiming unread/unavailable
  content.
- No authorization regressions; no cross-user data observed in any capture.
- Latency budgets met or explicitly re-baselined with justification.
