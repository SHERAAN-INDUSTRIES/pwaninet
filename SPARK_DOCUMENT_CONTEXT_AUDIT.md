# Spark — Side-by-Side Document Context Audit

**Scope:** Spark (Pwanimate) chat + context rail/sheet document viewer in the Vasto (formerly PwaniNet) monolith.
**Type:** Audit only. No code changes, no migrations, no UI changes were made.
**Date:** 2026-11-11
**Repository audited:** `/home/zayne/projects/pwaninet` (the `Vasto/vasto-b` directory is an empty skeleton; `Vasto/vasto-f` is a marketing-only frontend and contains no Spark code).

---

## 1. Executive summary

Spark **can** see a document opened in the side-by-side context rail — the plumbing exists end to end: the frontend tracks the active document and current page, sends them as `context_resources` on every `/api/pwanimate/chat/` request, and the orchestrator resolves them into authorized page-level chunks via `DocumentSemanticRetrievalService.get_page_chunks()`.

However, document context is **unreliable** because of a confirmed identifier-resolution bug in `PwanimateOrchestrator._resolve_context_resources()`:

- The "Add a book" context picker sends the document's **share UUID** in the `documentId` field (`pwanimate/api/views.py:1297` → `static/js/pwanimate/pwanimate-chat.js:1730`).
- The backend tries `Document.objects.filter(id=int(doc_id))` first (`pwanimate/orchestrator/service.py:662`). `int(<uuid>)` raises `ValueError`, `doc_obj` stays `None`, and — because the `share_id` lookup is an **`elif`**, not a fallback on parse failure — it is **never evaluated** (`service.py:665-670`).
- The resource is then silently dropped (`service.py:672-673` → `continue`). No page chunks, no document filter, no warning unless the separate availability inspector catches it.

Documents opened via **citation badges** carry the numeric primary key in `documentId` and resolve correctly — which is why the feature appears to work sometimes and fail other times.

Secondary confirmed issues: a stale-object page-number bug on same-document re-preview, a complete context bypass on deterministic tool routes (`_run_tool` never resolves `context_resources`), and page-number precedence where any "page N" in the user's text overrides the viewer's live page.

The indexing pipeline itself is sound: PyMuPDF extraction is 1-based, chunking preserves `page_number`/`page_end`, and exact page retrieval is a plain authorized SQL filter that does not require vector similarity — though it is gated on `is_active=True`, which is only set after embeddings complete.

---

## 2. Existing architecture and component map

| Layer | Component | Location |
|---|---|---|
| Chat UI | Spark chat client (8152 lines) | `static/js/pwanimate/pwanimate-chat.js` |
| Context rail template | Preview/Context tabs, doc viewer hosts | `pwanimate/templates/pwanimate/partials/pwanimate_context_rail.html` |
| Chat template + quick actions | `data-study-action` buttons | `pwanimate/templates/pwanimate/partials/chat_content.html:753-805` |
| Document viewer | PDF/DOCX/TXT viewer, page state, `onPageChange` | `documents/static/documents/js/document-viewer.js` |
| Chat API | `POST /api/pwanimate/chat/` (SSE) | `pwanimate/api/views.py` (`_post_chat`, ~line 291) |
| Context doc picker API | `GET /api/pwanimate/context/documents/` | `pwanimate/api/views.py:1201` (`PwanimateContextDocumentSearchView`) |
| Request DTO | `OrchestrationRequest.context_resources` | `pwanimate/orchestrator/types.py:47` |
| Orchestrator | `run()` → `_run_rag()` / `_run_tool()` / `_run_conversational()` | `pwanimate/orchestrator/service.py:965` |
| Context resolution | `_resolve_context_resources`, `_resolve_context_attachments` | `pwanimate/orchestrator/service.py:546-814` |
| Document retrieval | `DocumentSemanticRetrievalService` | `pwanimate/retrieval/services/document_retrieval.py` |
| Chunk model | `DocumentChunk` (pgvector, page provenance) | `pwanimate/models.py:18` |
| Ingestion | extract → chunk → persist → embed | `pwanimate/tasks/ingestion.py`, `pwanimate/tasks/embeddings.py`, `pwanimate/ingestion/` |
| Document model | `Document.share_id`, `status`, `is_available`, `visibility` | `documents/documents/models.py:117` |
| Study session state | `context_state` normalization/restore | `pwanimate/services/study_session.py:1100-1259` |

Two parallel "document" concepts exist and must not be conflated:

1. **Repository documents** (`documents.Document`) — indexed into `DocumentChunk` rows; resolved via `context_resources` → `_resolve_context_resources`.
2. **Chat attachments** (`PwanimateAttachment`) — text extracted on demand via `extract_attachment_text`; resolved via `_resolve_context_attachments` (`service.py:546-593`) and `_process_attachments` (`service.py:380-544`).

---

## 3. Frontend document state findings

### 3.1 State-flow map: open document → send message

1. **Entry points** produce a normalized resource via `SparkChat.normalizeResource(url, title, meta)` (`pwanimate-chat.js:3658-3857`):
   - Citation badge click → `previewResource(url, title, meta)` (`pwanimate-chat.js:2496-2525`). Meta comes from `data-*` attributes emitted in `appendAssistantMessage` (`pwanimate-chat.js:7039-7068`), populated from `_build_sources_summary` (`service.py:1912-1972`).
   - "+ Add a book" picker → `searchContextDocuments()` hits `/api/pwanimate/context/documents/` and calls `addResourceToContext(resource)` directly (`pwanimate-chat.js:1717-1750`).
   - Chat attachment click → `previewResource` with `sourceType: 'attachment'`, `attachmentId` (`pwanimate-chat.js:2454-2466`).
   - Study-session restore → `initial_context_resources` rendered server-side (`pwanimate/views.py:214-363`).
2. `previewResource()` stores the resource in `this.previewResourceState` and renders it into the Preview tab or mobile sheet (`pwanimate-chat.js:3864-3918`). `addResourceToContext()` pushes into `this.contextResources` and renders the Context tab (`pwanimate-chat.js:3982-4012`, `syncContextView` 4228-4285).
3. `renderSurface()` mounts `window.DocumentViewer` with `initialPage`, `documentId`, `documentShareId`, and an `onPageChange` callback (`pwanimate-chat.js:4436-4462`).
4. `DocumentViewer` tracks `currentPage`/`totalPages`, enforces 1-based bounds, and calls `onPageChange(currentPage, totalPages, state)` on every navigation (`document-viewer.js:73-227, 707-732`).
5. `onPageChange` mutates `details.pageNumber` / `details.totalPages` in place (`pwanimate-chat.js:4443-4452`). Because `details` **is** the same object reference held in `contextResources[activeContextIndex]` or `previewResourceState`, the live page propagates.
6. `handleSend()` builds the payload; `getContextResourcesForRequest()` returns `contextResources` plus `previewResourceState` if not already present (`pwanimate-chat.js:4014-4028`, payload assembly `6091-6094`), POSTed as `context_resources` to `/api/pwanimate/chat/` (`6108-6118`).

### 3.2 What the frontend tracks

| Field | Tracked? | Notes |
|---|---|---|
| Document identity | Yes | `documentId`, `documentShareId`, `documentVersionId`, `fileId`, `id` (= share UUID for docs) |
| Title / type / file type | Yes | `title`, `previewType`, `type`, `fileType`, `mediaUrl`, `thumbnailUrl` |
| Version | Partially | `documentVersionId` sent but **never read** by `_resolve_context_resources` (always uses `is_latest`) |
| Total pages | Yes | `totalPages` from viewer `getTotalPages()` |
| Current page | Yes | `pageNumber`, mutated by `onPageChange`; 1-based |
| Page changes (scroll/nav/direct) | Yes | All funnel through `goToPage` → `onPageChange` |
| Text selection | **No** | No selection capture exists |
| Loading/extraction status | Indirect | Backend emits `warnings` via `_collect_source_warnings` |

### 3.3 Confirmed frontend defect — stale page on same-document re-preview

In `previewResource()` (`pwanimate-chat.js:3871-3914`): when the same document is previewed again, the `isSameDoc` branch calls `previewDocViewer.goToPage(resource.pageNumber)` **without re-calling `renderSurface`**. However `this.previewResourceState` was already reassigned to the *new* normalized `resource` (line 3882), while the mounted viewer's `onPageChange` closure still captures the **old** `details` object (created in the earlier `renderSurface` call at line 4443).

Result: after re-previewing the same doc at a different page, subsequent page turns mutate the orphaned old object. `getContextResourcesForRequest()` sends `previewResourceState` (the new object) whose `pageNumber` remains frozen at the re-preview page. **The sent page number goes stale.** Probable, code-evidenced; not runtime-verified.

---

## 4. Outgoing chat payload comparison

All scenarios POST to `/api/pwanimate/chat/` with `Accept: text/event-stream`. Base payload (`pwanimate-chat.js:6060-6117`):

```jsonc
{
  "message": "<text>",
  "local_time": "<iso>", "timezone": "Africa/Nairobi",
  "conversation_id": "<uuid|null>", "create_conversation": true,
  "engine": "<slug|null>", "attachments": ["<uuid>"],
  "context_resources": [ /* §3 schema */ ],
  "study_mode": false, "study_session_id": null,
  "interaction_id" / "resume_run_id": "..."   // only when resuming a pending interaction
}
```

There is **no** top-level `document_id`, `page_number`, `context_type`, or `selected_text` field — document context exists **only** inside `context_resources[]`.

| # | Scenario | `context_resources` content | Page sent | Expected doc resolution |
|---|---|---|---|---|
| 1 | Plain message, nothing open | absent/`[]` | — | n/a (general RAG) |
| 2 | Doc added via "+ Add a book" picker | `[{type:"document", documentId:"<share-UUID>", documentShareId:"<share-UUID>", pageNumber:N, mediaUrl, fileType,...}]` | live `pageNumber` | **FAILS** — `int(UUID)` → `ValueError` → `elif share_id` unreachable → dropped |
| 3 | Doc opened via citation badge, not added to Context | preview auto-included: same shape but `documentId:"<numeric PK>"` | citation page initially; live via `onPageChange` | **Works** (PK resolves) |
| 4 | Doc in Context tab, free-form question | `[{...documentId:"<PK or UUID depending on entry path>"...}]` | live `pageNumber` | Works only if entered via citation/session-restore; fails if picker-added |
| 5 | "Explain page" / "Summarize page" / "Quiz me" quick actions | identical to #4 — `triggerDocumentStudyAction` injects prompt `"... Page {pageNumber} of \"{title}\" ..."` and calls `handleSend()` (`pwanimate-chat.js:4160-4181`) | prompt text embeds same page; backend `explicit_query_page` also parses it | Same resolution path as #4 |
| 6 | Manual question after page navigation | same object mutated by `onPageChange` → current page | live page | Same as #4; **except** stale-page bug after same-doc re-preview (§3.3), and any "page N" in the text overrides it (see §7.4) |
| 7 | Query matching a deterministic tool route (e.g. "search documents about X", "tell me about document \<uuid\>") | sent by client, **ignored by backend** | ignored | `_run_tool` never calls `_resolve_context_resources` — context bypassed entirely |

Sensitive values (UUIDs, URLs) are structurally present; no credentials are transmitted.

---

## 5. Backend request and orchestration trace

```
POST /api/pwanimate/chat/  (SSE)
  └─ PwanimateChatView.post → _post_chat            pwanimate/api/views.py:291
       ├─ message validation                          views.py:331-336
       ├─ engine/offering resolution                  views.py:340-390
       ├─ attachments resolved + quota check          views.py:~440-535
       ├─ context_resources = data.get(...)           views.py:537-539  (list guard only — NO schema validation)
       ├─ study-session branch (sends context_resources too)  views.py:559-665
       ├─ OrchestrationRequest(context_resources=...) views.py:708-728
       └─ orchestrator.run(request)                   views.py:733
            └─ PwanimateOrchestrator.run              orchestrator/service.py:965
                 ├─ StudyAgent path — SKIPPED when context_resources non-empty   service.py:1009-1017
                 ├─ tool_router.route(query)          service.py:1003 (patterns: tools/router.py)
                 ├─ tool_selector.select(...)         service.py:1037 (LLM-based; receives selected_resources)
                 ├─ tool_route? → _run_tool           service.py:1047-1060 → service.py:1345
                 │     └─ context_resources NEVER resolved here  ← CONFIRMED BYPASS
                 ├─ conversational? → _run_conversational        service.py:1063-1069
                 └─ else → _run_rag                   service.py:1072 → 1582
                       ├─ _resolve_context_attachments  service.py:1593 (PwanimateAttachment path)
                       ├─ _process_attachments          service.py:1611 (page-aware text extraction)
                       ├─ _resolve_context_resources    service.py:1619-1625  ← THE resolution boundary
                       │     per resource:
                       │       res_type/source_type filter      service.py:629-633
                       │       doc_id = document_id|documentId  service.py:638
                       │       share_id = share_id|document_share_id|documentShareId  service.py:639-643
                       │       id fallback (int PK else share UUID)              service.py:644-651
                       │       page_raw = explicit_query_page | page|page_number|pageNumber  service.py:652-657
                       │       Document lookup: id=int(doc_id) ELSE share_id=UUID  service.py:659-670  ← BUG
                       │       authorized_chunk_qs.exists(document_id)            service.py:676
                       │       filter_doc_ids.append                                service.py:685-686
                       │       get_document_overview_chunks (summary queries)      service.py:690-698
                       │       get_page_chunks(share_id, page) → [CURRENTLY OPEN PAGE…] marker  service.py:700-717
                       ├─ retrieval scoped: filters={"document_ids": filter_doc_ids}  service.py:1643
                       ├─ explicit_retrieval_results = page_chunks + selected docs    service.py:1675-1687
                       ├─ ContextItem(explicitly_selected=True, AuthorityLevel.EXPLICIT)
                       │     content = snippet[:3000], capped at 10 items            service.py:1690-1708
                       ├─ ContextEngine.build_context (EXPLICIT_RESOURCE grounding)   service.py:1718-1729, context/engine.py:246-247
                       ├─ _collect_source_warnings → inspect_open_document_availability service.py:1734, services/study_session.py:274
                       └─ LLMRequest(task="rag", context=context_pkg) → gateway.generate(_stream)  service.py:1758-1828
```

**Where context is lost, precisely:**

1. `service.py:659-673` — UUID-in-`documentId` resources dropped silently (P0).
2. `service.py:1047-1060` + `1345-1362` — deterministic tool routes bypass context resolution entirely (P1).
3. `service.py:652-657` — `explicit_query_page` (regex `\bpage|p\.\s*(\d+)` on raw query) takes precedence over the live viewer page (P2 semantics issue).
4. `pwanimate-chat.js:3905-3914` — stale `pageNumber` after same-doc re-preview (P1).

Page context **is** preserved through streaming: chunks are resolved once per request before generation; an in-flight SSE response cannot observe later page changes (correct behavior).

---

## 6. Document indexing and vector retrieval findings

### 6.1 Pipeline

```
process_document (documents/tasks/processing.py:170)
  ├─ thumbnails/previews/OCR/search-index          processing.py:192-208
  ├─ document.status = 'ready'                     processing.py:211
  └─ ingest_document_version.delay(version.id)     processing.py:218-219
       └─ pwanimate/tasks/ingestion.py:79
            ├─ extract_document(primary_file)       ingestion.py:135  → pwanimate/ingestion/extractors/
            │     PDF: PyMuPDF page-by-page, page_number = page_idx + 1   extractors/pdf.py:58-59,85
            │     skips non-text blocks (images/equations-as-vectors)     pdf.py:65-66
            ├─ chunk_document(extracted)            ingestion.py:173  → ingestion/chunker.py
            │     preserves page_number/page_end (1-indexed)              chunker.py:304-321,781
            ├─ create_document_chunks_from_data     ingestion.py:221  → is_active=False  chunker.py:786
            └─ embed_document_version               tasks/embeddings.py
                 ├─ provider.embed_texts per batch  embeddings.py:135
                 ├─ is_active=True ONLY on completed+validated embedding  embeddings.py:188-214
                 └─ failures → embedding_status='failed', is_active=False embeddings.py:163-186
```

### 6.2 Retrieval gates (`build_authorized_chunk_queryset`, `document_retrieval.py:46-147`)

All must hold for a chunk to be retrievable **by any path, including exact page lookup**:

- `DocumentChunk.is_active = True` (requires completed embeddings)
- `document.status = 'ready'`, `document.is_available = True`
- `document_version__is_latest = True` (in `get_page_chunks`, line 458)
- Visibility: public non-AI docs, own uploads, or restricted docs matching enrolment/programme; staff/`PRESIDENT`/`DELEGATE` get wider read (`document_retrieval.py:61-110`)

### 6.3 Page-aware retrieval capability — assessment

| Capability | Supported? | Mechanism |
|---|---|---|
| Entire current page | Yes | `get_page_chunks`: `Q(page_number<=P, page_end>=P) \| Q(page_number=P, page_end null)`, ordered by `chunk_index` (`document_retrieval.py:460-472`) |
| Neighboring pages | Not directly | No `page±N` expansion; semantic search may surface them |
| Relevant passage elsewhere in doc | Yes | `filters={"document_ids": filter_doc_ids}` scopes hybrid retrieval to the open doc (`service.py:1643`) |
| Exact vs similarity | Both | Page chunks injected as `AuthorityLevel.EXPLICIT` items ahead of retrieved context |
| Page index base | **1-based everywhere** — extractor (`pdf.py:59`), model help_text (`models.py:88`), viewer (`document-viewer.js`), frontend `pageNumber` | consistent |
| Printed page labels vs PDF position | **Not modeled** — `page_number` is PDF position; no printed-label offset field exists | gap for front-matter-offset documents |
| Equations/images/tables | Weak — image blocks skipped (`pdf.py:65-66`); tables become paragraph text | content-dependent |
| Scanned PDFs | `EmptyDocumentError` → no chunks → explicit user warning | handled honestly |

**Key consequence:** `get_page_chunks` does *not* need embeddings for correctness, yet inherits the `is_active=True` gate — so a document whose chunks exist but embeddings are pending/failed/quota-blocked returns **zero page chunks** and triggers the "no indexed text" warning even though extractable text exists in `DocumentChunk.content`. This is a probable availability gap, not a correctness bug.

### 6.4 Context budgeting

Explicit items are capped at 10 and each snippet truncated to 3000 chars (`service.py:1695-1701`); `ContextEngine.prioritize_explicit_resources` limits retrieved supplements to top-3 in `EXPLICIT_RESOURCE` mode (`context/engine.py:246-247`). Reasonable bounds; very large pages may still be truncated mid-chunk.

---

## 7. Tool-calling and context propagation findings

- `run()` skips `StudyAgent` whenever `context_resources` is non-empty (`service.py:1009-1012`) — good: the bounded agent can't drop rail context.
- But deterministic `tool_router` matches and LLM `tool_selector` selections route to `_run_tool` (`service.py:1047-1060`), which builds its `ContextPackage` **solely from the tool result** (`service.py:1366-1371`). `request.context_resources` is never consulted. A student asking "search documents about osmosis" with a PDF open gets generic document-search results; the open page is invisible. `_collect_source_warnings` still inspects `context_resources` (`service.py:1433`), so warnings can fire without any content being supplied — inconsistent.
- `ContentSearchTool` (`tools/domain/extra.py:34-56`) accepts only `query`/`content_type`/`limit` — **no document or page filters exist** on any retrieval tool.
- `tool_selector.select()` does receive `selected_resources=request.context_resources` (`service.py:1042`) and its prompt acknowledges selected materials — but its output only influences `sources`/`tool_route`, and on a tool route the resources still aren't resolved.
- The model is not asked to "discover" the open document — the application has the data; the failure is purely in propagation/routing, not prompting.

---

## 8. Confirmed root causes and unresolved hypotheses

### Confirmed (code-evidenced)

| # | Finding | Evidence |
|---|---|---|
| C1 | UUID-in-`documentId` resources silently dropped; `share_id` fallback unreachable due to `elif` | `orchestrator/service.py:659-673`; UUID source `api/views.py:1297` → `pwanimate-chat.js:1730` |
| C2 | `_run_tool` bypasses `context_resources` resolution entirely | `orchestrator/service.py:1047-1060, 1345-1371` |
| C3 | Stale `pageNumber` after same-document re-preview (`isSameDoc` path doesn't re-render; `onPageChange` closure bound to old object) | `pwanimate-chat.js:3881-3914, 4443-4452` |
| C4 | `explicit_query_page` overrides live viewer page whenever "page N"/"p. N" appears in the message | `orchestrator/service.py:618-619, 652-657` |
| C5 | Page retrieval gated on embedding completion (`is_active`), though page lookup is pure SQL | `document_retrieval.py:55-59, 449`; `tasks/embeddings.py:195` |
| C6 | `documentVersionId` sent by client but ignored; always `is_latest` | `service.py:700-717` vs `document_retrieval.py:455-458` |

### Probable (strongly indicated, not runtime-verified)

| # | Finding |
|---|---|
| P1 | Picker-added documents (the primary "Add a book" flow) are the main user-visible failure; citation/session-restored docs work — matching the "unreliable" report |
| P2 | Documents with pending/failed embeddings or quota-blocked embedding provider produce "no indexed text" warnings despite existing chunk text |
| P3 | Multi-resource context: page chunks from *all* attached docs' current pages are injected together (up to the 10-item cap), which can dilute the active document's page content |

### Unverified

| # | Item |
|---|---|
| U1 | Actual runtime behavior of `DocumentViewer` page events in the deployed build (static analysis only) |
| U2 | Whether any production data contains resources in legacy snake_case that hit different code paths |
| U3 | Printed-page-label vs PDF-position mismatches in real documents (no field exists to detect them) |

### Ruled out

| # | Hypothesis | Why |
|---|---|---|
| R1 | "Frontend never sends page/document info" | False — `context_resources` carries both; verified in payload assembly and backend consumption |
| R2 | "Quick actions use a different pipeline" | False — they inject a prompt and call `handleSend()` (`pwanimate-chat.js:4160-4181`) |
| R3 | "Streaming loses request context" | False — resolution happens once before generation; SSE only streams the answer |
| R4 | "Page numbers are 0-based in chunks" | False — extractor is `page_idx + 1` (`pdf.py:59`), model documents 1-indexed (`models.py:88`) |

---

## 9. Security and reliability considerations

- **Authorization is enforced twice and correctly**: `build_authorized_chunk_queryset` (chunk-level, visibility+enrolment aware) gates both page chunks and semantic results; `inspect_open_document_availability` uses `is_user_authorized_for_document`. Unauthorized docs yield empty results, not leaks.
- **Information-exposure nuance**: `inspect_open_document_availability` emits warnings like `"<title>" is open in the viewer, but has no indexed text` only after `is_user_authorized_for_document` passes (`study_session.py:340-370`) — acceptable. However `_resolve_context_resources` drops unauthorized/unresolvable docs **silently** (`service.py:672-683` logs server-side only), so the user gets a generic answer with no indication the open document was ignored. Diagnostics must distinguish "not found", "unauthorized", "not indexed", "page has no text" — without confirming existence of resources the user cannot access.
- **Identifier ambiguity is a security-relevant edge**: if a client sends both `document_id` and `document_share_id` pointing at *different* documents, current code silently prefers `document_id`. A confused or malicious client could attach document A's page context while the UI displayed document B. Resolution must verify agreement or fail closed.
- No secrets or document contents are logged; `logger.warning` lines reference IDs only.

---

## 10. Recommended remediation plan

### P0 — Correctness blockers

1. **Fix identifier resolution in `_resolve_context_resources`** (`service.py:659-673`): try *both* identifiers independently — resolve by `document_id` (int PK) and by `share_id` (UUID) as separate lookups, then reconcile (see §11 contract). Never let a truthy-but-unparseable `documentId` suppress the `share_id` path. Mirror the already-correct logic in `inspect_open_document_availability` (`study_session.py:315-338`).
2. **Canonical client contract**: picker endpoint should emit the numeric PK as `document_id` and UUID as `document_share_id` distinctly (`api/views.py:1297` currently conflates them). Frontend `normalizeResource` should stop defaulting `documentId` to `documentShareId` (`pwanimate-chat.js:3670`).

### P1 — Context fidelity

3. **Resolve `context_resources` before intent routing** (see §11): hoist `_resolve_context_resources`/`_resolve_context_attachments` into a single pre-routing stage in `run()`, pass the resolved page chunks + doc filter into whichever path executes (RAG, tool, conversational). Preserves authorization (same queryset) and avoids loading content for turns that don't need it (lazy chunk fetch only when a doc resource exists).
4. **Fix stale-page reference**: in the `isSameDoc` branch, either update `previewResourceState.pageNumber` from the viewer's `getCurrentPage()` at send time, or re-bind `onPageChange` to the new resource object (`pwanimate-chat.js:3905-3914`). Simplest robust fix: snapshot `pageNumber` from the live viewer inside `getContextResourcesForRequest()`.
5. **Honest diagnostics**: extend `_collect_source_warnings`/metadata to emit structured per-resource statuses — `resolved`, `not_found`, `unauthorized`, `unindexed`, `page_empty` — gated on authorization so no existence oracle is created.

### P2 — Semantics and efficiency

6. **Split "active viewer page" from "explicitly requested page"**: carry `pageNumber` (viewer truth) and let query text act as a *hint*, not an override — e.g., inject both the active page chunks and, only when the query unambiguously targets another page, that page's chunks, labeled distinctly. Suppress the override for quick-action prompts (they already embed the correct page).
7. **Decouple page lookup from embedding completion**: allow `get_page_chunks` to read `DocumentChunk.content` for `is_active=False`/`embedding_status='pending'` chunks (optionally via a separate `build_page_lookup_queryset`), since exact page retrieval needs no vector. Keep `is_active` gate for similarity search only.
8. **Version awareness**: honor `documentVersionId` when present (already sent by picker/citations metadata) instead of always `is_latest`.
9. **Printed-page labels**: evaluate storing a page-label map at ingestion if documents with front-matter offsets appear in practice.

---

## 11. Proposed architecture evaluation — shared pre-routing context stage

**Recommendation: adopt, with constraints.**

Introduce a `ResolvedDocumentContext` stage executed once in `run()` immediately after `has_attachments_or_context` is computed and **before** `tool_router.route()`:

```
run()
  ├─ resolve_context(request)          # NEW: attachments + context_resources →
  │     { rail_attachments, attachment_pages,
  │       filter_doc_ids, page_chunks, selected_post/user_results,
  │       resource_statuses }          # per-resource diagnostics
  ├─ StudyAgent skip-check (unchanged, uses has_attachments_or_context)
  ├─ tool_router / tool_selector       # may now receive resolved doc filter as parameters
  ├─ _run_tool / _run_rag / _run_conversational
  │     └─ consume pre-resolved context instead of re-resolving
```

Why this is safe and minimal:

- **Authorization preserved**: resolution still flows through `build_authorized_chunk_queryset(user)` — the same visibility/enrolment rules. No new access path is created.
- **No unnecessary content loading**: chunk rows are only fetched when a document-type resource exists; posts/profiles/web resources resolve as today; the stage is a no-op for `context_resources=[]`.
- **Fixes C2 for free**: `_run_tool` can receive `filter_doc_ids`/page chunks (e.g., `content_search` gains optional `document_ids`/`page_number` parameters), so tool routes stop silently ignoring the open document.
- **Single diagnostic point**: per-resource status computed once, emitted in `metadata.warnings`/`sources` regardless of route taken.
- **Streaming unaffected**: resolution remains per-request, pre-generation.

Cost: one extra resolution pass is avoided by passing results down (not re-calling inside `_run_rag`); refactor is confined to `service.py` plus optional tool-parameter plumbing.

---

## 12. Canonical identifier contract (proposed)

| Field | Meaning | Type | Source of truth |
|---|---|---|---|
| `document_id` | Internal DB primary key | integer | `Document.id` |
| `document_share_id` | Public/share UUID | UUID string | `Document.share_id` |
| `document_version_id` | Specific version | integer | `DocumentVersion.id` |
| `page_number` / `pageNumber` | 1-based viewer/chunk page | positive int | `DocumentViewer.currentPage` ↔ `DocumentChunk.page_number` |
| `context_type` / `type`/`previewType` | Resource kind | enum string | `document`, `attachment`, `post`, `profile`, `image`, `video`, `web` |

**Rules:**

1. Backend accepts snake_case and camelCase for backward compatibility, but internally normalizes to the canonical names above.
2. When **both** `document_id` and `document_share_id` are present, resolve **both**; if they map to different `Document` rows, **fail closed** — drop the resource and emit an `identifier_conflict` diagnostic. Never silently prefer one.
3. When only one is present, resolve it; when neither parses, emit `unresolvable_resource` (no existence leak).
4. `pageNumber` is the viewer's live page at send time — snapshot it in `getContextResourcesForRequest()` from the mounted viewer rather than trusting the stored object (fixes C3 at the source).
5. Active-viewer-page vs explicit-page semantics: `page_number` = what the student is looking at; query-derived page = an *additional* retrieval target, labeled separately in context items (e.g., `match_type: 'query_page'` vs `'active_viewer_page'`). Arbitrary "page N" mentions should widen retrieval, not silently replace the viewer page.

---

## 13. Automated regression-test matrix

All tests should run against `PwanimateOrchestrator._resolve_context_resources` / `_run_rag` with factory-built `Document`, `DocumentVersion`, `DocumentFile`, `DocumentChunk` rows and a stub user.

| ID | Scenario | Setup | Assert |
|---|---|---|---|
| T1 | Picker-style resource | `documentId=<share UUID>`, `documentShareId=<same UUID>`, `pageNumber=3` | Page-3 chunks returned; doc in `filter_doc_ids` |
| T2 | Citation-style resource | `documentId=<int PK>`, `documentShareId=<UUID>` | Same as T1 |
| T3 | Both IDs disagree | `documentId`=doc A PK, `documentShareId`=doc B UUID | Resource dropped; `identifier_conflict` diagnostic; **no** chunks from either |
| T4 | Only share UUID in `id` field | `id=<UUID>`, no documentId/shareId | Resolved via `id` fallback |
| T5 | Unauthorized document | Restricted doc, unrelated user | Empty result; no chunks; diagnostic does not confirm existence |
| T6 | Unindexed document | `Document.status='ready'`, zero chunks | `unindexed`/`no_extracted_text` warning |
| T7 | Pending-embedding chunks | chunks exist, `is_active=False`, `embedding_status='pending'` | Current: empty; after P2-7: page chunks returned |
| T8 | Page spanning chunk | `page_number=5, page_end=7`, request page 6 | Chunk included |
| T9 | Multi-page chunk ordering | several chunks on page | Ordered by `chunk_index`; marker `[CURRENTLY OPEN PAGE…]` prepended once |
| T10 | Quick-action prompt | `"Explain the concepts and content on Page 12 of \"X\"…"` + `pageNumber=12` | Page 12 chunks; query-page and viewer-page agree |
| T11 | Conflicting page mention | viewer page 12, query "compare with page 3" | Both pages retrievable, labeled distinctly (post-P2-6) |
| T12 | Tool route with open doc | query matching `content_search` + doc resource | Post-P1-3: doc filter/page context reaches tool path |
| T13 | Non-latest version chunks | old `DocumentVersion` chunks | Excluded unless `documentVersionId` explicitly targets it (post-P2-8) |
| T14 | Preview-only resource | `previewResourceState` not in `contextResources` | Included via `getContextResourcesForRequest` dedup logic |
| T15 | Re-preview same doc | second `previewResource` same share_id, new page | Sent `pageNumber` equals viewer's actual page (post-P1-4) |
| T16 | Streaming in-flight page change | page changes mid-SSE | Response context unchanged (snapshot semantics) |
| T17 | Scanned PDF | no extractable elements | `EmptyDocumentError` path → warning, honest answer |
| T18 | `documentVersionId` honored | resource pins v1 while v2 is latest | v1 chunks retrieved (post-P2-8) |

---

## 14. Acceptance criteria for a working system

1. Every document opened in Preview or Context — via citation, picker, attachment, or session restore — resolves to the correct `Document` regardless of which identifier fields the client populated.
2. The page sent is the page the student is actually viewing at send time, for manual messages, quick actions, and post-navigation questions alike.
3. Page chunks appear in the LLM context marked as current-page evidence; same-document semantic results supplement them; other documents never leak in unless separately retrieved.
4. Tool-routed turns preserve document/page context instead of bypassing it.
5. Missing, unauthorized, unindexed, and empty-page states produce distinct, honest diagnostics that reveal nothing about resources the user cannot access.
6. Identifier conflicts fail closed with an explicit diagnostic.
7. All T1–T18 regression tests pass; existing retrieval tests remain green.

---

## 15. Open technical questions

1. Should `get_page_chunks` bypass `is_active` for pending-embedding chunks, or should ingestion mark chunks active earlier (text available before vectors)? Trade-off: availability vs. consistent retrieval semantics.
2. Do any production clients send snake_case `context_resources` (mobile app, API consumers) that need contract tests beyond the web client?
3. Is printed-page labeling needed for the actual corpus (front matter, Roman numerals)? Requires corpus sampling.
4. Should `documentVersionId` pin retrieval when the viewer is showing a superseded version's file? The viewer currently always loads `latest_version`'s file — version pinning may be moot until the viewer supports versions.
5. Should page chunks from multiple simultaneously-open documents be merged, prioritized by `activeContextIndex`, or limited to the active document only?

---

## 16. Recommended implementation sequence

1. **P0-1/P0-2** — identifier resolution fix + picker payload correction (smallest change, fixes the dominant failure).
2. **P1-5** — structured per-resource diagnostics (observability before refactor).
3. **P1-3** — pre-routing context resolution stage (§11), including tool-path plumbing.
4. **P1-4** — page snapshot at send time.
5. **P2-6/P2-7/P2-8** — page semantics split, embedding-independent page lookup, version honoring.
6. Regression suite T1–T18 landed alongside each fix.

*Audit ends here. No implementation performed.*
