# Spark Context Rail Implementation — Document-First Reading Workspace

**Project:** Vasto / PwaniNet — Spark (Pwanimate)
**Repository:** `/home/zayne/projects/pwaninet`
**References:** `SPARK_CONTEXT_RAIL_VIEWER_AUDIT.md`, `SPARK_DOCUMENT_CONTEXT_IMPLEMENTATION.md`
**Mode:** Full implementation pass. No new PDF engine; the existing `DocumentViewer`/PDF.js pipeline is reused everywhere.

---

## 1. Implementation summary

The context rail is now a document-dominant reading workspace:

- **Desktop:** the rail defaults to a true 50/50 split of the workspace remaining after the global nav rail, remains user-resizable (pointer + keyboard + double-click reset), and keeps the nav rail independently sized.
- **Chrome:** the four stacked chrome bands around the PDF were consolidated into one ~40 px rail header (`Preview | Context | Offline` tabs, truncated document title, `⋯` overflow menu, close). The per-surface card header/footer bands are hidden on desktop document surfaces; all actions moved into the overflow menu or the existing floating control bar.
- **Viewer:** the existing `DocumentViewer.createFloatingBar()` pill (page nav + zoom) gained idle auto-hide (~2.8 s) with pointer/touch/scroll/keyboard/focus wake, and is disabled entirely under `prefers-reduced-motion`. A `minimalChrome` option suppresses the viewer's permanent toolbar inside Spark surfaces.
- **Mobile:** the bottom sheet keeps its Bootstrap offcanvas + shared drag-handle infrastructure, now defaults taller (`min(90dvh,800px)`) and auto-expands to near-full viewport when a preview/context document opens; safe-area bottom and landscape side insets are handled; sizing is `dvh`-based.
- **Offline:** a third **Offline** tab (desktop rail + mobile sheet) lists, searches, reads, updates, and removes locally saved documents. Saved PDFs open **from IndexedDB bytes** through the same `DocumentViewer`/PDF.js engine — no re-download, no second renderer.
- **Context integrity:** offline-opened documents normalize into the existing `context_resources` contract (share UUID + int PK + version), share `viewerPageState`/`_resourceStateKey`, and dedupe through `addResourceToContext` — the page-aware Spark pipeline is untouched.

---

## 2. Files modified

| File | Change |
|---|---|
| `static/js/pwanimate/pwanimate-chat.js` | Rail limits/split default, resizer dbl-click reset, 3-tab `switchWorkspaceTab`, rail title + overflow menu, offline tab renderer/actions, offline document open, `minimalChrome` viewer option, perf marks, mobile sheet expand |
| `pwanimate/templates/pwanimate/partials/pwanimate_context_rail.html` | 40 px consolidated header, Offline tab button, doc title, `⋯` dropdown, slim chips strip, Offline pane, resizer ARIA defaults |
| `pwanimate/templates/pwanimate/partials/chat_content.html` | Mobile Offline tab + pane, `dvh` sheet sizing |
| `static/css/pwanimate/pwanimate.css` | Header 40 px, hidden desktop card chrome, floating-bar hide state, offline list styles, sheet height/safe-area updates, context-max-width var |
| `documents/static/documents/js/document-viewer.js` (+ `staticfiles/` copy) | `minimalChrome` option, floating-bar auto-hide, viewer-replacement race fix, blob-URL `{data}` loading, perf marks |
| `static/js/pwanimate/offline-library.js` | DB v2 + `owner_id` index/filter, `document_pk`/`document_version_id`/`doc_updated_at` fields, `getBlobUrl`/`releaseBlobUrl`/`clearAll`, `storage.persist()`, quota error, logout/account-switch cleanup |
| `pwanimate/api/views.py` | `_offline_document_payload` now emits `document_pk` + `document_version_id` (additive) |

No models, migrations, or authorization rules were changed.

---

## 3. Desktop layout behavior

`calculateEffectiveContextLimits()` (`pwanimate-chat.js` ~4826) now computes:

```
splitWidth  = floor((viewportWidth − leftRailWidth) / 2)   // leftRail = 240 or 56
effectiveMax = max(280, min(splitWidth, viewportWidth − leftRail − 480))
```

- **Default:** with no saved preference (`savedPreferredWidth === null`), the rail opens at the equal split. A deliberate user resize (drag or arrow keys) writes `pwanimate_context_rail_width` and is respected/clamped on later opens and viewport resizes.
- **Double-click the resizer** clears the saved preference → snaps back to 50/50.
- ARIA `valuemin/valuemax/valuenow` are updated from live limits on every open/resize; keyboard resize (←/→/Home/End) uses the same bounds.
- The `650 px` cap and the `280–650` saved-width clamp are gone; saved values up to 2400 px are honored (still clamped to live limits).
- Spark keeps a ≥480 px floor; on narrow desktops (<~1400 px) the rail yields before the chat pane does.
- Grid sizing is unchanged (`--pwanimate-context-rail-width` → grid column 3), so the split is pure CSS-var driven.

## 4. Mobile behavior

- Sheet remains `offcanvas-bottom` + `bottom-sheet-handles.js` drag (dismiss >90 px down, expand snap to `viewport − safe-top − 16`).
- CSS height raised to `min(90dvh, 800px)`; `max-height` remains `100dvh − safe-top − 8px`.
- `_expandMobileSheetForReading()` applies the expanded snap automatically when Preview/Context opens with content (also used by `previewResource` on mobile).
- `.offcanvas-body` keeps bottom safe-area padding and now also pads `env(safe-area-inset-left/right)` for landscape cutouts.
- Inline `85vh/90vh` → `85dvh/90dvh`; `dvh` tracks the virtual keyboard so the sheet resizes instead of overlapping.
- PDF pinch-zoom/swipe gestures are unchanged; the drag handle is a separate strip, so no gesture conflict was introduced.

## 5. Offline tab architecture

```
Offline tab (desktop pane + mobile pane)
  └─ renderOfflineTab()
       ├─ PwaniOfflineLibrary.list()          → saved records (owner-filtered)
       ├─ GET /api/pwanimate/offline/library/ → remote catalog (online only)
       │     ├─ saved docs: Read | +context | Update? | Remove
       │     └─ available docs: Save
       └─ search input filters title/unit/category/filename/description
```

- **Read** → `openOfflineDocument(shareId)` → `lib.getBlobUrl()` → `previewResource('/documents/document/<share>/', title, {mediaUrl: blobUrl, fileType, documentShareId, documentId, documentVersionId, …})` → existing `DocumentViewer`.
- **+context** → `_offlineContextResource()` → `addResourceToContext()` (dedupes by share UUID/int PK — no duplicates).
- **Update** appears when remote `updated_at` > stored `doc_updated_at`; re-runs `save()`.
- **Remove** → `lib.remove()` (also revokes the blob URL).
- The pre-existing offline **modal** (`offline-library.js` reader/search) is preserved untouched.

## 6. Local PDF loading flow

```
IndexedDB 'pwani-offline'/'documents' (keyPath share_id, owner_id index)
  → record.file_blob (full bytes stored at save time)
  → URL.createObjectURL(blob)         [cached per share_id in blobUrlCache]
  → previewResource → renderSurface → new DocumentViewer(host, blobUrl, 'pdf', …)
  → PDFViewer.load():
       blob: URL detected → fetch(blobUrl).arrayBuffer()   (main thread)
       → pdfjsLib.getDocument({ data: arrayBuffer })       (no worker fetch)
       → renderPage → onPageChange → viewerPageState[shareUUID]
```

- **No network request for PDF bytes** — verified by construction: the only fetch is the same-origin `blob:` URL, which never hits the network.
- Blob URLs are cached per document and reused across re-opens (same `mediaUrl` → `isSameDoc` short-circuit → `goToPage`, no reload).
- Missing/corrupt blobs: `getBlobUrl` throws → user-facing alert; "Metadata only" badge shown when `file_blob` is absent (Read disabled).
- Revoked on `remove()`/`clearAll()`; never revoked while a viewer may still reference it (library owns lifecycle).

## 7. Offline authorization & account isolation

- Server-side authorization is unchanged and remains the only boundary for Spark retrieval/context (`is_user_authorized_for_document` on every bundle/file/save endpoint).
- Records are stamped `owner_id` (DB v2, new index) and `list()`/`getBlobUrl()` hide/refuse records owned by a different account.
- `clearAll()` runs on logout/account-switch form submits and on `PRIVATE_OFFLINE_INVALIDATED` service-worker messages — same signals as `offline-capture.js`.
- **Policy:** local reading is permitted for bytes saved while authorized (possession-at-save-time). Spark context attachment always revalidates server-side and fails closed — an offline-opened doc cannot mint context while unauthorized. Restricted docs saved before revocation remain readable locally until removed or storage cleanup; this matches the audit's recommended trade-off and is flagged as a product decision point.
- No credentials/tokens are stored in records.

## 8. Performance instrumentation & findings

Added `performance.mark/measure` (no behavior change):

| Mark/measure | Span |
|---|---|
| `pw:preview-click` → `pw:preview-viewer-ready` | click → viewer initialized (`pw:preview-to-viewer`) |
| `pw:viewer-init-start` → `pw:viewer-init-done` | DocumentViewer init |
| `pw:pdf-getdoc-start` → `pw:pdf-doc-loaded` | pdf.js document fetch/parse (`pw:pdf-getdoc`) |
| `pw:viewer-init-start` → `pw:first-page` | click-mount → first painted page (`pw:first-page-total`) |

Read via `performance.getEntriesByName('pw:*')` in DevTools — enables the cold/warm, small/medium/large PDF measurements required by the stress plan.

**Evidence-based fixes applied:**
- **Blob worker-fetch failure (confirmed):** pdf.js issued `Range` fetches for `blob:` URLs from its worker → "Unexpected server response (0)". Fixed by reading bytes on the main thread and passing `{data}` — also removes all network from the offline path.
- **Viewer-destroy race (confirmed):** `initialize()` called `this.viewer.getTotalPages()` after `await load()`; a mid-load destroy left `this.viewer = null` → "Cannot read properties of null (reading 'getTotalPages')". Fixed with a viewer-identity guard.
- **Not changed (unverified):** CDN Range support for remote PDFs, worker cold-start cost, thumbnail posters — instrumentation now exists to measure these; no speculative optimization was applied.

## 9. Accessibility decisions

- Overflow menu is a standard Bootstrap dropdown (keyboard navigable, `aria-expanded`).
- Hidden card chrome moved, not deleted — every action remains reachable via `⋯` or the floating bar.
- Floating bar auto-hide is **disabled under `prefers-reduced-motion`** (persistent controls). Otherwise it hides via opacity+visibility after ~2.8 s idle, reappears on pointer/touch/scroll/keydown/focus; it never hides while it contains focus.
- Offline list items use semantic buttons with `title`/`aria-label`s; search inputs have labels; tab buttons keep `role=tab`/`aria-selected`/`aria-controls`.
- Resizer remains `role=separator` with live ARIA values and keyboard support.

## 10. Testing commands & outcomes

| Check | Result |
|---|---|
| `node --check` on all three modified JS files | **pass** |
| `node documents/tests/test_document_viewer_js.js` | **10/10 pass** |
| `.venv/bin/python manage.py check` | **0 issues** |
| `.venv/bin/python manage.py test pwanimate.tests.test_document_context` | **35/35 pass** |
| Django template render of both modified partials | **renders OK** (offline panes present in output) |

**Not executable here:** real-browser verification (50/50 split visuals, blob PDF render, sheet gestures, offline open) requires a running dev server + browser — covered by the stress-test plan. No live CDN Range measurement was possible; flagged as pending.

## 11. Remaining limitations & follow-ups

- `isSameDoc` matches on share UUID first: if a doc is already mounted from the CDN URL, opening its offline copy navigates pages rather than swapping to the blob (harmless online; edge case offline+stale-mount).
- Whole-file `{data}` load for offline PDFs trades range streaming for reliability; ≤150 MB cap bounds memory, but very large PDFs may want chunked/IDB-stream loading later.
- `pwani-offline` IndexedDB persistence is requested (`storage.persist()`) but browser-granted persistence is not guaranteed; eviction under pressure remains possible ("Metadata only"/missing-blob states are handled).
- Legacy unstamped records (pre-owner_id) remain visible to any account on the device until logout cleanup — acceptable migration window.
- Mobile sheet has two snap states (default + expanded); intermediate detents could be added later.
- CDN Range verification + real timing baselines are pending (instrumentation ready).

## 12. Known issues fixed during implementation

- Django `{% trans %}` inside a JS template literal (would have rendered literally) — replaced with plain strings.
- `aria-valuenow="50%"` (invalid for `role=separator`) — set numeric placeholder; JS keeps it authoritative.
- Viewer destroy-during-load race → `getTotalPages` null crash — guarded.
- pdf.js blob-URL worker fetch → response 0 — `{data}` path added.
- **Duplicate viewer mount ("doc split into two pieces")** — `initialize()` re-runs (Refresh action, re-preview while a load was in flight, failed-load retry) appended a second toolbar/canvas/floating bar without clearing the first, and an in-flight `PDFViewer.load()` could still append its canvas after destroy. Fixed: `initialize()` now clears the container and destroys the stale inner viewer before remounting; `PDFViewer` tracks `_destroyed` and aborts before `createCanvas()`/after `getDocument()`.
