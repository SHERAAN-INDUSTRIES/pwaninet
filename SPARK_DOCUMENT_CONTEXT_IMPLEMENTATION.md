# Spark Document Context — Implementation Report

**Scope:** Complete remediation of `SPARK_DOCUMENT_CONTEXT_AUDIT.md` — canonical
document identity, live page tracking, shared pre-routing context resolution,
page-aware retrieval, page-reference semantics, context budgeting, diagnostics,
modality honesty, and citation provenance.

**Repository:** `/home/zayne/projects/pwaninet` (Vasto / PwaniNet — Spark / Pwanimate)

---

## 1. Files modified

| File | Change |
|---|---|
| `pwanimate/orchestrator/document_context.py` | **NEW** — shared `DocumentTurnContext` resolver, page-intent parser, identifier collection, diagnostics. |
| `pwanimate/orchestrator/service.py` | `run()` resolves document context before routing; `_resolve_context_resources()` delegates documents to the shared resolver; `_run_tool()` merges document evidence; `_run_rag()` uses bounded/deduped explicit items + diagnostics metadata; `_collect_source_warnings()` consumes resolver diagnostics; `extract_attachment_text()` uses shared page intent; `_truncate_context_text()` helper; sources summary gains `page_end`/`document_version_id`. |
| `pwanimate/orchestrator/types.py` | `OrchestrationRequest.document_context` field. |
| `pwanimate/retrieval/services/document_retrieval.py` | `build_authorized_chunk_queryset(require_active=)` — embedding-lifecycle-aware relaxation for deterministic lookups; `get_page_chunks(include_unembedded=)`; `text_only`/`embedding_status` metadata; `content_hash` in chunk metadata. |
| `pwanimate/tools/domain/extra.py` | `ContentSearchTool` accepts optional `document_ids` scoping (narrows only). |
| `pwanimate/services/study_session.py` | `is_user_authorized_for_document(require_ready=)` flag for lifecycle diagnostics. |
| `pwanimate/api/views.py` | Context-document picker emits canonical `document_id` (integer PK) + `document_share_id` (UUID). |
| `static/js/pwanimate/pwanimate-chat.js` | `viewerPageState` keyed by document identity; `_resourceStateKey()`; `_snapshotResourcePages()` immutable send-time snapshot; `onPageChange` writes canonical state; `addResourceToContext` dedup includes `documentShareId`. |
| `pwanimate/tests/test_document_context.py` | **NEW** — 35 regression tests. |

## 2. Components introduced

### `DocumentTurnContext` (`pwanimate/orchestrator/document_context.py`)

Produced once per turn in `PwanimateOrchestrator.run()` **before** intent/tool
routing whenever `context_resources` is non-empty:

```
request → DocumentTurnContext.resolve(user, context_resources, query)
        → per-resource DocumentResourceResolution
        → filter_document_ids (authorized PKs for retrieval scoping)
        → page_intent (document_wide / page_scoped / comparison / explicit_pages)
```

Chunk text is fetched **lazily** via `collect_page_evidence(user, query)` — only
by routes that consume it (`_run_rag`, `_run_tool`). Identity + authorization
resolution is cheap (a handful of indexed queries per resource).

### Resolution statuses

`resolved`, `invalid_resource`, `identifier_conflict`, `inaccessible`,
`awaiting_processing`, `unavailable`, `unindexed`, plus per-resolution flags
`embeddings_pending`, `document_wide`, `viewer_page`, `target_pages`, and
`detail` (`page_out_of_bounds`, `embeddings_pending`).

## 3. Identifier compatibility (Phase A)

`collect_document_identifiers()` gathers **integer PK candidates** and **UUID
candidates independently** from every alias:

- PK: `document_id`, `documentId` (when integer-parsable), legacy `id` (integer)
- UUID: `documentShareId`, `document_share_id`, `share_id`, `id`,
  and `documentId`/`document_id` **when they contain a UUID** (legacy picker bug)

Resolution rules:

1. Each family resolves independently → set of distinct `Document` rows.
2. **Zero matches** → `inaccessible` (missing and unauthorized are deliberately
   indistinguishable — no existence oracle).
3. **>1 distinct document** → `identifier_conflict`, fail closed, warning emitted.
4. **Exactly one** → authorization check via
   `is_user_authorized_for_document(user, doc, require_ready=False)`.
   Visibility/ownership/deletion/AI-generation rules unchanged; the relaxed flag
   only distinguishes lifecycle states for diagnostics.
5. Lifecycle: `archived`/`is_available=False` → `unavailable`; non-`ready`
   status → `awaiting_processing`; zero chunks → `unindexed`; chunks but none
   embedding-active → `resolved` + `embeddings_pending`.
6. **Version pinning:** `documentVersionId` is honored only when it equals the
   document's `latest_version.id` — the viewer always renders the latest file,
   so a superseded version would mismatch evidence. Otherwise latest is used.

A malformed legacy field (e.g. `documentId: "garbage"`) produces no candidate
and cannot suppress a valid `documentShareId`.

**Picker fix:** `PwanimateContextDocumentSearchView` now returns
`document_id: document.id` (int PK) alongside `document_share_id` — both are
sent and cross-verified by the resolver.

## 4. Context-resolution flow (Phase C)

```
run()
 ├─ DocumentTurnContext.resolve()          ← once, pre-routing
 ├─ tool_router.route / selector           ← unchanged
 ├─ _run_tool()                            ← merges doc evidence into ContextPackage
 ├─ _run_conversational()                  ← unchanged (never has doc context)
 └─ _run_rag()
     ├─ _resolve_context_attachments()     ← unchanged
     ├─ _resolve_context_resources(document_context=ctx)
     │    ├─ post/profile resolution        ← unchanged
     │    └─ ctx.collect_page_evidence()    ← lazy, cached per turn
     ├─ UnifiedRetrievalService.retrieve(filters={"document_ids": [...]})
     ├─ explicit ContextItems (bounded)
     └─ ContextEngine.build_context()
```

`_run_tool()` now prepends up to
`PWANIMATE_TOOL_DOC_CONTEXT_MAX_ITEMS` (default 6) document ContextItems to the
tool's own items, so deterministic routes (`content_search`, `document_detail`,
`web_search`, etc.) never silently discard the selected document. Tool results
remain authoritative for their domain; document evidence is additive.

`ContentSearchTool` accepts an optional `document_ids` parameter that narrows
lexical document search to authorized IDs (the adapter already enforced its own
access rules — this can only narrow, never widen).

## 5. Retrieval architecture (Phase D)

`get_page_chunks(user, share_id, page, document_version_id, include_unembedded=True)`:

- Deterministic SQL page filter (`page_number <= P <= page_end`, 1-based).
- `build_authorized_chunk_queryset(require_active=False)` now means:
  `is_active=True` **OR** `embedding_status IN ('pending','processing','failed')`.
  This makes extracted text usable while embeddings are pending/failed **without**
  resurrecting chunks deactivated after completion (`is_active=False` +
  `embedding_status='completed'` — superseded versions stay excluded).
- Semantic/vector retrieval is untouched: `build_candidate_queryset` still
  requires `is_active=True`.
- Returned chunks carry `text_only`/`embedding_status` metadata when their
  embedding is incomplete.

`collect_page_evidence()` priority order per resolved document:

1. **Active viewer page** (`retrieval_origin="active_viewer_page"`)
2. **Explicit target pages** (`"explicit_page"`) — from page intent
3. **Neighbor pages ±1** (`"neighbor_page"`, ≤2 chunks/side) — only for
   page-scoped questions
4. **Document overview** (`"document_overview"`) — document-wide questions,
   plus viewer/explicit pages still included

Oversized pages are bounded by `PWANIMATE_PAGE_MAX_CHUNKS` (default 8) using
query-term relevance, then re-ordered by `chunk_index`. A global char budget
(`PWANIMATE_PAGE_EVIDENCE_CHAR_BUDGET`, default 16000) caps total page evidence;
truncation sets `page_chunks_partial` → `page_context_partial` warning so Spark
never claims full-page coverage it didn't receive.

## 6. Page-state synchronization (Phase B)

- `viewerPageState` maps `_resourceStateKey(details)` → `{page, totalPages}`.
  Key prefers `documentShareId`, then `documentId`, `att:<attachmentId>`,
  `mediaUrl`, `url` — stable across object replacement.
- `renderSurface()` seeds the key at mount and the `onPageChange` callback
  writes it on every page turn (scroll, controls, direct selection).
- `getContextResourcesForRequest()` → `_snapshotResourcePages()` returns
  **shallow-copied** resources with the live page overlaid at send time.
  Quick actions, free-form messages, retries, and study-session sync all share
  this path; an in-flight response keeps the page visible at submission.
- Same-document re-preview (`isSameDoc`) and remounts are covered because the
  key — not the object reference — carries state. Preview/Context tabs and the
  mobile sheet share the same canonical map.

## 7. Page-reference semantics (Phase E)

`extract_page_intent(query)`:

| Query | Behavior |
|---|---|
| "Explain the second equation on this page" | viewer page only |
| "Explain page 35" | viewer page **+** page 35 (`explicit_page`) |
| "Compare this page with page 20" | viewer page **+** page 20, comparison flag |
| "Summarize the entire document" | overview + viewer page |
| "Summarize this page" | **page-scoped** (document-wide requires a doc-level noun) |
| "On page 12 it mentions page 4" | page 12 may be a target; page 4 is **not** |

Rules: only the **first** page mention can be an explicit target, and only when
an intent verb precedes it or it leads the query; comparison constructions
collect all mentions. The viewer page is **always** included when present —
query text supplements, never silently replaces, the visible page.
`extract_attachment_text()` (chat attachments) uses the same parser.

## 8. Context budgeting (Phase F)

- `PWANIMATE_EXPLICIT_CONTEXT_MAX_ITEMS` (default 12, was hardcoded 10)
- `PWANIMATE_EXPLICIT_SNIPPET_MAX_CHARS` (default 3000)
- `_truncate_context_text()` truncates at paragraph → sentence → word
  boundaries instead of mid-equation cuts.
- Dedup: `(source, object_id)` **and** `content_hash` (new chunk metadata).
- Page evidence itself is budgeted (see §5); `ContextEngine`'s existing
  token/item caps still apply downstream.
- `page_context_partial` warning makes partial coverage detectable.

## 9. Diagnostics (Phase G)

`DocumentTurnContext.to_warnings()` emits privacy-preserving warnings consumed
by `_collect_source_warnings()` on **all** routes (RAG, tool, conversational):

| Code | Message essence |
|---|---|
| `document_inaccessible` | "I couldn't access one of the selected documents…" (no existence leak) |
| `identifier_conflict` / `invalid_resource` | inconsistent/incomplete reference ignored |
| `document_processing` | still being processed |
| `document_unavailable` | currently unavailable |
| `document_unindexed` | no indexed text (e.g. scanned PDF) |
| `embeddings_pending` | text available, semantic indexing still processing |
| `page_out_of_bounds` | page N outside document range |
| `page_text_unavailable` | page N has no readable extracted text |
| `page_context_partial` | only part of the page(s) fit the budget |

Structured (non-user-facing) diagnostics ride in
`context_pkg.metadata["document_context"]` and `response.metadata["document_context"]`
— statuses, IDs, pages, flags only; **no content, tokens, or credentials**.

## 10. Modality honesty & citations (Phase H/I)

- Scanned/image-only documents surface as `unindexed` → the existing
  `UNAVAILABLE_SOURCE_INSTRUCTION_ADDENDUM` instructs Spark not to claim it read
  them. No new multimodal dependency was added.
- `get_page_chunks` metadata already carries `document_id`,
  `document_share_id`, `document_version_id`, `version_number`,
  `page_number`, `page_end`, `chunk_id`, `retrieval_origin`,
  `active_viewer_page`. `_format_chunk_result` now also emits `content_hash`.
- `generate_citation()` already emits `[Title, vN, pp. X–Y]` for spanning
  chunks — page ranges are preserved.
- `_build_sources_summary` now includes `page_end` and `document_version_id`
  for the citation UI.

## 11. Test commands & results

```bash
.venv/bin/python manage.py check                       # 0 issues
.venv/bin/python manage.py test pwanimate.tests.test_document_context \
    pwanimate.tests.test_page_context \
    pwanimate.tests.test_retrieval                      # 77 tests — OK
```

`test_document_context.py` (35 tests) covers: page-intent semantics, identifier
collection, resource classification, truncation, picker-style UUID-in-documentId,
integer IDs, matching/conflicting IDs, invalid legacy + valid share, missing doc,
private-doc isolation (owner vs other student), unavailable/draft/unindexed/
pending-embedding diagnostics, active-page priority, explicit targets,
unembedded text retrieval (+ semantic exclusion of inactive chunks), page bounds,
document-wide overview, multi-document context, pre-routing resolution,
tool-route evidence merge, and no-document conversations.

**Pre-existing failures (not caused by this change):**
`test_orchestrator_phase3` (35 errors — tests call `classify_intent_category`,
a method absent from the orchestrator), `OrchestratorPromptsTestCase` (~15
failures — assert literal strings in `prompts.py`, which this change did not
touch), `test_authenticated_valid_greeting` (intent `rag` vs `conversational`),
`test_chat_links_attachment_to_user_message` (MagicMock JSON serialization in
test fixture). Verified unrelated: none exercise the modified code paths.

## 12. Known limitations & remaining risks

- **Neighbor pages** are fetched only for page-scoped questions and capped at
  ±1 page / 2 chunks — deliberate; wider context comes from semantic retrieval.
- **`document_wide` + explicit page** edge cases fetch overview + targets; very
  large documents still rely on the char budget.
- **`is_active=False` + `embedding_status='completed'`** chunks remain excluded
  from page lookup — intentional (deactivated/superseded content).
- **Attachment path** (`extract_attachment_text`) shares page intent but keeps
  its own chunk-sampling logic; it does not use `DocumentTurnContext`.
- **StudyAgent path** is skipped whenever `context_resources`/`attachments`
  exist (pre-existing behavior, preserved).
- Frontend page-state is in-memory; a full page reload restores page from the
  persisted resource's `pageNumber` (last synced value) — acceptable.
- `documentVersionId` mismatch silently falls back to latest (viewer always
  shows latest); documented rather than rejected to avoid breaking clients.

## 13. Definition-of-done checklist

- [x] Picker-added and citation-opened documents resolve reliably
- [x] UUID and integer identifier handling backward compatible and secure
- [x] Visible page snapshotted at submission (immutable request snapshot)
- [x] RAG and tool routes receive the same trusted document context
- [x] Current-page evidence prioritized; semantic retrieval supplements
- [x] Extracted page text usable while embeddings pending (auth-gated)
- [x] Page-reference semantics predictable (active vs explicit vs comparison)
- [x] Citations carry document/version/page(-range) provenance
- [x] Unsupported visual content reported honestly (unindexed + addendum)
- [x] Context budgeting prevents prompt overgrowth; partial coverage flagged
- [x] Authorization enforced on every path; fail-closed on conflicts
- [x] Regression suite executed: 77/77 relevant tests pass
- [x] SSE streaming, auth, UI, and non-document conversations preserved
