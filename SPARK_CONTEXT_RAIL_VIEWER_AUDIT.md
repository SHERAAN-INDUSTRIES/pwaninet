# Spark Context Rail, Document Viewer & Offline Reading Audit

**Project:** Vasto / PwaniNet — Spark (Pwanimate)
**Repository:** `/home/zayne/projects/pwaninet`
**Mode:** Audit and architectural investigation only — no code, template, CSS, or model changes were made.
**Date basis:** source inspection of the working tree; performance figures are labeled as measured vs. hypothesized throughout.

---

## 1. Executive summary

The Spark context rail is a right-hand grid column (`--pwanimate-context-rail-width`) on a three-zone desktop shell (left nav rail / chat / context rail) that is **hard-capped at 650 px** by `calculateEffectiveContextLimits()` in `static/js/pwanimate/pwanimate-chat.js:4807-4816`. The default is 360 px, the minimum 280 px, and the saved preference is clamped to `[280, 650]` at line 154. There is **no code path that can produce a 50/50 split today** — the cap, not the viewport, is the binding constraint.

The document viewer (`DocumentViewer` → `PDFViewer` → PDF.js, `documents/static/documents/js/document-viewer.js`) already renders **inside a floating pill bar** (`createFloatingBar()`, lines 451-580) with page navigation and zoom — the "floating controls" the redesign wants largely exist. What surrounds the PDF is duplicated chrome: a 48 px rail header with Preview/Context tabs, a per-surface sub-header (type badge + location pill + "+ Add to Context"), a context chips strip + "Add a book" button, a card header (badge + location + Remove), and a footer (title + Open Original + external link). In the Context tab a single document can consume **four stacked chrome bands** before the PDF viewport begins.

Tab switching does **not** unmount viewers: inactive panes are hidden via CSS (`display:none`, pwanimate.css:4149-4157) while the `DocumentViewer` instances (`previewDocViewer`, `contextDocViewer`) persist, and canonical page state lives in `viewerPageState` keyed by document identity. This is the correct foundation to preserve.

Offline infrastructure is real but **split-brained**: `offline-library.js` stores full file bytes (`file_blob`) plus extracted chunks in IndexedDB (`pwani-offline`/`documents`, keyed by `share_id`, 150 MB cap), yet the offline reader only renders extracted text chunks — **the stored PDF bytes are never opened by `DocumentViewer`**. Separately, `offline-capture.js`/`offline-resources.js`/`native-offline.js` snapshot rendered DOM for the native/Capacitor offline shell, and `service-worker.js` deliberately does **not** cache document media. Downloaded PDFs are therefore app-managed IndexedDB blobs, not OS downloads-folder files — and currently unreadable as PDFs offline.

PDF loading goes through `pdfjsLib.getDocument(this.fileUrl)` where `fileUrl` is `document_file.file.url` — a **public, unsigned Cloudflare R2/S3 URL** (`AWS_QUERYSTRING_AUTH = False`, 7-day `CacheControl`, settings/base.py:630-695). pdf.js will issue HTTP Range requests when the server advertises `Accept-Ranges` (R2/Cloudflare does), so the viewer does not necessarily download the whole file before first paint — but this is **unverified at runtime** (see §8). No per-open authentication call exists; authorization is enforced only at picker/bundle time, which is consistent with the public-URL model but worth noting for the offline policy (§9).

**Bottom line:** the 50/50 split is a small, well-scoped change (one clamp + CSS grid), the viewer already has floating controls, the real work is (a) collapsing duplicated chrome, (b) wiring the stored `file_blob` into the existing `PDFViewer` via `URL.createObjectURL`, and (c) verifying/measuring the actual first-page latency path before optimizing it.

---

## 2. Existing desktop component hierarchy

### 2.1 Shell grid (≥1200 px)

`body.pwanimate-active .app-shell-layout` is a CSS grid with three columns — `pwanimate.css:2365-2380`:

```
grid-template-columns:
    var(--pwanimate-current-left-rail-width, 240px)   /* col 1: nav */
    minmax(0, 1fr)                                     /* col 2: Spark chat */
    var(--pwanimate-context-rail-width, 0px);          /* col 3: context rail */
```

- Left rail: fixed `240 px` expanded / `56 px` collapsed (`--pwanimate-left-rail-collapsed-width`, pwanimate.css:8-11, 155; width rules at 2391-2407).
- Center panel: `minmax(0,1fr)`, `overflow:hidden` (2410-2425).
- Context rail: `width`/`max-width` = the CSS var, `display:none` unless `body.pwanimate-context-rail-open` (2427-2469).
- Breakpoint: rails hidden below `1200 px` (`@media (max-width:1199.98px)`, 2330-2347); navbar clearance ≥992 px (2350-2362).
- `html/body.pwanimate-active` locks the app to `100dvh` with `overflow:hidden` (2228-2247).

### 2.2 Context rail DOM (`pwanimate/templates/pwanimate/partials/pwanimate_context_rail.html`, 258 lines)

```
.pwanimate-context-rail-wrapper            (position:relative, h-100, flex-column)
├── #pwanimateRailResizer                   role=separator, aria 280–650, 10px hit area at left:-5px
├── .pwanimate-context-rail-header          height:48px fixed (CSS 2130-2136 + inline)
│   ├── #desktopWorkspaceTabs  [Preview | Context (badge)]
│   └── #pwanimateContextRailCloseBtn
└── #pwanimate-right-rail.pwanimate-right-rail  (flex-grow-1, overflow-y:auto — CSS 2195-2205)
    └── .tab-content
        ├── #desktopTabPreviewPane  (h-100 flex column)
        │   ├── sub-header: type badge + location pill + "+ Add to Context"
        │   ├── .pwanimate-preview-host
        │   │   ├── #desktopPreviewDocViewer  (.pwanimate-surface-viewer-host)
        │   │   ├── #desktopPreviewVideo / #desktopPreviewImageContainer /
        │   │   └── #desktopPreviewDocContainer (fallback card)
        │   └── .pwanimate-preview-card-footer (title/subtitle + primary open action)
        └── #desktopTabContextPane
            ├── #desktopContextEmpty (empty state + "Add context item")
            └── #desktopContextActive
                ├── .pwanimate-context-strip-wrapper  (chips list #desktopContextChipsList
                │                                    + "Add a book" button)   ← lines 176-184
                └── #desktopContextSurfaceCard (.pwanimate-preview-card)
                    ├── .pwanimate-preview-card-header (badge + location + Remove)  ← 189-203
                    ├── .pwanimate-preview-card-body → #desktopContextDocViewer etc. ← 206-234
                    └── .pwanimate-preview-card-footer (title + Open Original + ↗)   ← 236-243
```

### 2.3 Mobile hierarchy (`chat_content.html:135` onward)

`#pwanimateResourcePreviewSheet` — Bootstrap `offcanvas offcanvas-bottom d-xl-none`, inline `height:85vh; max-height:90vh`; CSS caps it at `min(88dvh,760px)` / `calc(100dvh - safe-top - 8px)` (pwanimate.css:2049-2068). Contains: drag handle (`.pwanimate-bottom-sheet-handle`), tab header (Preview/Context + close), Preview pane with sub-header (badge + location + Add to Context), hosts (`#previewSheetDocViewer` etc.), and mirrored Context pane. Safe-area padding applied to body bottom (2064-2067).

---

## 3. Audit A — why the rail cannot exceed its current width

### Confirmed constraints (all verified in source)

| Constraint | Location | Value |
|---|---|---|
| JS configured max | `pwanimate-chat.js:4811` | `configuredMax = 650` |
| JS configured min | `pwanimate-chat.js:4812` | `configuredMin = 280` |
| Center-panel reservation | `pwanimate-chat.js:4810` | `minCenterWidth = 480` |
| Effective max formula | `pwanimate-chat.js:4814` | `min(650, vw − leftRail − 480)` |
| Saved-width clamp | `pwanimate-chat.js:154` | `savedPreferredWidth ∈ [280, 650]` |
| ARIA bounds (template) | `pwanimate_context_rail.html` resizer | `aria-valuemin=280`, `aria-valuemax=650` |
| Grid column | `pwanimate.css:2368` | `var(--pwanimate-context-rail-width)` |
| Rail element | `pwanimate.css:2433-2434` | `width`/`max-width` = same var |

**Root cause (confirmed):** `calculateEffectiveContextLimits()` returns `max ≤ 650` unconditionally. Even on a 2560 px display with a collapsed 56 px left rail, the effective max is `min(650, 2560−56−480) = 650`. The `minCenterWidth = 480` reservation only binds on narrow desktops (< ~1400 px), where it correctly shrinks the rail — but nothing ever *raises* the ceiling toward a 50/50 split.

Secondary notes:
- `updateContextRailWidth()` (4818-4834) writes the var to both `documentElement` and `body`; `closeContextRail()` sets it to `0px` (4895-4896). Width is purely CSS-var driven — **no JS layout math depends on the rail being ≤650 px**.
- Resizer drag clamps to `limits.min/max` (4969 area); keyboard resize uses the same bounds (5042). Both read `calculateEffectiveContextLimits()`, so changing the formula fixes pointer **and** keyboard paths.
- `window.innerWidth < 1200` routes to the mobile sheet instead (4855-4857) — desktop rail only exists ≥1200 px.

### Minimum safe change for a true 50/50 split

1. Replace the fixed `configuredMax = 650` with a viewport-proportional cap, e.g. `Math.floor((availableWidth − leftWidth) * 0.5)` (optionally minus a small gutter), keeping `configuredMin = 280` (or lowering to ~320 for readability) and keeping `minCenterWidth` as a *floor for the chat pane* rather than a rail cap.
2. Widen the saved-preference clamp at line 154 to match (or store unclamped and clamp at render).
3. Update the resizer ARIA bounds — already dynamic via `updateContextRailWidth()` (4823-4827), so only the template defaults need adjusting.
4. Add a "snap to 50%" affordance (double-click resizer or a toolbar toggle) — optional but cheap.

No template restructuring is required for the split itself; the grid already sizes col 3 from the var. **Risk:** at exactly 50 %, the chat pane must keep a usable minimum (~480-560 px); enforce `rail ≤ vw − leftRail − minCenter`.

---

## 4. Audit B — space consumed around the PDF viewport

### 4.1 Chrome inventory (desktop, document in Context tab — worst case)

| Band | Source | Approx. height |
|---|---|---|
| Rail header (tabs + close) | template ~line 40; CSS fixed 48 px | **48 px** |
| Context chips strip + "Add a book" | lines 176-184 | ~70-90 px |
| Surface card header (badge + location + Remove) | lines 189-203 | ~44 px |
| Viewer toolbar (format badge, reload, fullscreen, download) | `document-viewer.js:301-448`, compact padding 6px 10px | ~36-40 px |
| Floating pill bar (page + zoom + scroll) | `document-viewer.js:451-580`, absolute, bottom 12 px compact | overlays PDF (~34 px tall, does not consume layout) |
| Card footer (title + Open Original + ↗) | lines 236-243 | ~56-64 px |
| **Total fixed chrome (Context tab)** | | **~254-286 px** |

Preview tab is lighter: header 48 px + sub-header ~40 px + viewer toolbar ~38 px + footer ~56 px ≈ **182 px** fixed. On a 900 px-tall viewport the PDF gets ~70 % (Preview) or ~65 % (Context) of rail height; on 768 px it drops below 60 %.

Horizontal: at the current 360 px default width, PDF page width ≈ 360 − scrollbar(~6 px) − card padding. PDF.js `fitContainer`/`max-width:100%` (pwanimate.css:4121-4138) letterboxes A4/Letter pages heavily — most of the width is whitespace at 100 % zoom.

### 4.2 What's essential vs. relocatable

- **Essential while reading:** page indicator/navigation, zoom, close rail, tab switch.
- **Relocatable/on-demand:** format badge, document title (duplicate of footer + chat context), reload, download, fullscreen, "Add to Context", Remove, Open Original, chips strip, empty-state CTAs.
- The viewer's **floating bar already implements** the target pattern (absolute, bottom-center, pill, `max-width:calc(100% - 24px)`). It has **no auto-hide** — it is always visible (confirmed: no opacity/visibility timer in `createFloatingBar`).

### 4.3 Recommended compact "document-first" anatomy

```
┌─ thin top bar (~40 px): [≡ tabs: Preview|Context|Offline] [title…] [⋯ menu] ┐
│                                                                            │
│                       PDF viewport (≈90 % of remaining space)              │
│                                                                            │
│            (floating pill: ‹ Page x/y › | − 100% + )  ← auto-hide          │
└────────────────────────────────────────────────────────────────────────────┘
```

- Merge the four desktop chrome bands into **one 40 px bar**: tabs on the left, active-document title (truncated) center, overflow menu (download, fullscreen, open original, remove-from-context, add-to-context) right.
- Move page/zoom into the existing floating pill; add idle auto-hide (fade after ~2.5 s, reappear on pointermove/tap/focus/page interaction) with `prefers-reduced-motion` honored and controls remaining keyboard-focusable.
- Keep the chips strip inside the **Context tab only** (it already is) and collapse it to a single horizontally scrollable row without the "Add a book" button (move into the ⋯ menu or tab header "+").
- **Overlap risk (confirmed possible):** the floating bar is centered and up to `calc(100% − 24px)` wide; on narrow rails it can cover page-number/footer text at the bottom of a page. Mitigate with bottom padding on the PDF scroll container equal to bar height + 8 px, or offset the pill above the safe area.
- Do **not** remove functionality — relocate it into the overflow menu; every current action maps 1:1.

---

## 5. Audit C — mobile bottom sheet

### Confirmed behavior

- **Sizing:** `offcanvas-bottom`, `height:85vh` inline, CSS `min(88dvh,760px)` and `max-height: calc(100dvh − safe-top − 8px)` — i.e. it can already reach ~92 % of viewport height minus safe area on short screens, capped at 88dvh/760px on tall ones (pwanimate.css:2050-2058; template inline style chat_content.html:135).
- **Drag:** shared `static/js/bottom-sheet-handles.js` — pointer drag on `[data-bottom-sheet-handle]`: drag down >90 px dismisses; drag up snaps to `maxHeight = max(280, viewportHeight − max(16, safeTop+8))` and marks `data-bottom-sheet-size="expanded"` (lines 45-86, 96-105). **There are only two states: default (~85-88dvh) and expanded (≈full height).** No intermediate snap points.
- **Safe areas:** bottom padding `env(safe-area-inset-bottom)` on `.offcanvas-body` (2064-2067); top inset used in drag math (96). Cutout/landscape insets (`safe-area-inset-left/right`) are handled for the *end* drawer but **not** explicitly for the bottom sheet — probable minor gap on landscape phones with side cutouts.
- **Gesture conflicts:** viewer touch handlers (`touchstart/move` swipe for page turns, pinch zoom — document-viewer.js ~820-970) live inside the sheet body; the drag handle is a separate element so conflicts are limited to the handle zone. PDF pinch-zoom and sheet drag do not overlap. **Probable** issue: horizontal swipe page-turn near the sheet's top edge could fight the handle only if the handle is tall — it is 5 px + margins, fine.
- **Keyboard/IME:** sheet is a Bootstrap offcanvas; no explicit `visualViewport` resize handling found in pwanimate code — keyboard opening will resize `100dvh`-based heights dynamically on modern browsers, but the fixed `height:85vh` inline style uses `vh` (not `dvh`) — **probable** minor jump on iOS Safari when the keyboard opens.
- **Scrolling:** `.offcanvas-body` is `overflow-y:auto` (2089-2095); inner tab panes are `overflow:hidden` flex columns — inner scroll is delegated to viewer containers. OK.

### Recommended expanded reading state

- Add a third snap point or make "expanded" the default when a document preview is active: ~`calc(100dvh − safe-top − 56 px)` leaving a visible sliver + handle of the chat underneath.
- Keep the handle visible at all times in reading mode (it is the only dismiss affordance besides the close button and swipe-down).
- Floating controls: reuse the desktop auto-hide pill; re-show on tap anywhere on the PDF, on keyboard focus, and on page-change; never hide while a text-selection or pinch gesture is in progress; honor `prefers-reduced-motion` (instant toggle, no fade).
- Accessibility: controls must remain in the tab order even when visually hidden (`opacity`/`visibility` pattern, not `display:none`); announce page changes via the existing `aria-live`-friendly badge; ensure the sheet is `aria-modal` focus-trapped by Bootstrap already.

---

## 6. Audit D — offline document infrastructure

### 6.1 What exists (all confirmed in source)

| System | File | Storage | Contents |
|---|---|---|---|
| Spark offline library | `static/js/pwanimate/offline-library.js` | IndexedDB `pwani-offline` v1, store `documents`, keyPath `share_id` | `file_blob` (full bytes, ≤150 MB), `chunks[]` (extracted text w/ page numbers), metadata, `saved_at` |
| Server offline API | `pwanimate/api/views.py:1317-1477` | — | `GET /api/pwanimate/offline/library/` (authorized docs w/ chunks), `GET .../documents/<share_id>/` (bundle JSON), `GET .../file/` (streams bytes via `FileResponse`), `POST/DELETE .../save/` (mirrors to "Spark Offline" collection) |
| DOM snapshot offline | `static/js/offline-capture.js`, `offline-resources.js` | IndexedDB `vasto_offline_media_<user>` + service-worker `OfflinePages` | rendered page snapshots for the native offline shell — **not** document files |
| Native bridge | `static/js/native-offline.js` | Capacitor `OfflineStore` plugin | `cacheResource`, `saveSnapshot`, `rememberSnapshot` — page/resource snapshots, owner-scoped, cleared on logout/account switch |
| Service worker | `static/service-worker.js` | Cache Storage `vasto-v*`/`vasto-offline-v*` | static assets only; `/media/` documents and `/api/` are **not** cached as files (network-first/fallback) |

### 6.2 Key findings

- **CONFIRMED:** downloaded PDFs *are* stored as reusable app-managed blobs (`file_blob` in IndexedDB) — not merely OS downloads. `saveDocument()` fetches `bundle.file.url` (the offline file endpoint) and persists the blob (offline-library.js:94-119).
- **CONFIRMED:** the offline reader (`renderReader`, 232-272) renders **extracted text chunks only** — `file_blob` is stored but never passed to `DocumentViewer`/PDF.js. There is currently **no path that opens a saved PDF from local storage**. This is the single biggest gap for the Offline tab.
- **CONFIRMED:** authorization is enforced server-side on every save/bundle/file request via `is_user_authorized_for_document` (views.py:1351-1368, 1382-1390, 1422-1426, 1454-1459) — good. Once saved, the local copy has **no expiry/revocation check** — possession ≠ continued authorization (policy decision needed, §9).
- **CONFIRMED:** offline search exists (`searchChunks`, substring over stored chunks) — usable for the Offline tab's search box today.
- **CONFIRMED:** version tracking is **absent** in the stored record — `saveDocument` stores `bundle.file` metadata but no `document_version_id`; a re-saved doc overwrites by `share_id`. New-version detection requires comparing `updated_at`/version on next online visit — **probable** small addition.
- **UNVERIFIED:** IndexedDB persistence under storage pressure — browsers may evict `pwani-offline` without `navigator.storage.persist()`; no persistence request exists in the code (grep found none). Quota: 150 MB/file cap is client-side only.
- **RULED OUT:** conflation with offline AI — Spark inference requires network regardless; the offline store powers reading + local chunk search only. Keep it that way.
- **RULED OUT:** service-worker caching of PDFs — deliberately not cached; do not add (would bypass authorization and duplicate IndexedDB).

### 6.3 Native/Capacitor

`native-offline.js` wraps a `Capacitor.Plugins.OfflineStore` native plugin for page snapshots; `android/` and `www/offline-assets/` exist. Whether the native plugin stores document files separately from `pwani-offline` IndexedDB is **unverified** — the web IndexedDB path is the confirmed store on all platforms (Capacitor WebView shares it).

---

## 7. Audit E — Offline tab integration (assessment)

Feasible without a second rendering engine:

1. **Discovery:** `PwaniOfflineLibrary.list()` (all saved records) + `fetchJson('/api/pwanimate/offline/library/')` for the saveable catalog — both exist today.
2. **Open locally:** `record.file_blob` → `URL.createObjectURL(blob)` → pass as `fileUrl` to the existing `DocumentViewer`/`PDFViewer`. pdf.js `getDocument(blobUrl)` works identically; `ViewerFactory` needs no change. Page state flows through the existing `onPageChange` → `viewerPageState` → `context_resources` pipeline **if** the resource is normalized with the same `document_share_id` — the record already stores `share_id`, `title`, `file` metadata; add `document_id`/version fields to the record for canonical identity.
3. **Add to Spark context:** an offline-opened doc can emit the same `context_resources` entry (share UUID + page) — Spark answers online using server-side chunks; the local blob is only the *viewing* surface. Clean separation.
4. **Sync/invalidation:** on next online visit, compare record `saved_at`/version vs. `bundle.updated_at`; offer "Update" (re-`saveDocument`) or "Remove". Server "Spark Offline" collection already mirrors saves.
5. **Revocation policy (recommendation):** treat local copies as *reading* artifacts authorized at save-time; on reconnect, if `is_user_authorized_for_document` now fails, mark the record `access_revoked` and hide/disable Spark-context attachment while still allowing local reading (or purge — product decision; document the trade-off: purge is safer for restricted docs, retain is better UX for expired-session edge cases). **Do not** let an offline-opened doc silently attach to Spark context without revalidation — Spark's server-side resolver already fail-closes, so context attachment is safe by construction.

---

## 8. Audit F — document loading & performance

### 8.1 Confirmed load sequence (source trace)

```
click (citation / picker card / context chip / study-session restore)
  → previewResource()/normalizeResource()          pwanimate-chat.js:~3660-3860
  → openContextRail()/switchWorkspaceTab()         4845-3990 (no network)
  → renderSurface() → new DocumentViewer(host, details.mediaUrl, …)   4504-4512
  → viewer.initialize()                            document-viewer.js:116-…
      → showLoading() (spinner)
      → ViewerFactory.createViewer → PDFViewer
      → PDFViewer.load():                          1140-1156
          1. ensure pdfjsLib (script already deferred in base.html:3682;
             pdf.worker.min.js set at 1144-1146)
          2. pdfjsLib.getDocument(this.fileUrl)    ← fileUrl = document_file.file.url
          3. await loadingTask.promise            ← pdf.js fetches via Range if supported
          4. createCanvas(); renderPage(currentPage)
  → onPageChange → viewerPageState + study sync    4484-4502
```

- `media_url` comes from the picker payload: `document_file.file.url` (`pwanimate/api/views.py:1285,1310`) → **public R2/S3 URL** (`AWS_QUERYSTRING_AUTH=False`, `custom_domain` CDN optional, `CacheControl max-age=604800`).
- **No authentication call per open** — the URL is public/unsigned. Authorization happens at picker/bundle resolution time only.
- **No duplicate requests by design:** viewer mounts once per surface; `previewDocViewer`/`contextDocViewer` are destroyed on re-preview of a *different* resource (2981-2987) but persist across tab switches.
- pdf.js **does** support Range requests and will use them when the server sends `Accept-Ranges: bytes`. Cloudflare R2 supports Range. Whether the deployed CDN/proxy actually returns it end-to-end is **unverified** — this is the key runtime check.
- `serve_download` and `PwanimateOfflineDocumentFileView` use `FileResponse` — Django's FileResponse does **not** emit `Accept-Ranges` (it streams whole-body). These endpoints are for downloads/offline-save, not the viewer path, so this does not affect preview — but if any viewer path ever points at them, range loading silently degrades to full download. **Probable** non-issue today; flag for the offline-blob path (blob URLs always "range" trivially).

### 8.2 Performance hypotheses (unverified — no runtime measurement was possible in this audit)

| Hypothesis | Basis | Likely impact |
|---|---|---|
| pdf.min.js + worker cold-load dominates first open | Script is `defer` in base.html:3682; worker spawns per `getDocument` | High on first open per session; warm after |
| Full-file download if Range unsupported | FileResponse paths lack ranges; CDN path probably OK | High for large PDFs if CDN misconfigured |
| Re-preview of same doc remounts viewer | destroy+recreate on resource change (2981-2987) | Medium — pdf.js re-parses doc |
| No thumbnail/poster while PDF loads | Only spinner; `preview_path`/`thumbnail_path` exist on DocumentFile (models.py:498-534) | Perceived latency — could show preview JPEG instantly |
| `getDocument` gets no caching hints | pdf.js has its own cache; CDN 7-day cache helps repeat users | Low-medium |

**Measurement plan (for implementation phase):** throttle to Fast 3G; time click→first-request, →headers, →first-bytes, →first-page-painted, →`loadingTask` resolve; count requests/bytes for 100 KB / 2 MB / 25 MB PDFs; repeat-open warm vs cold. Do not invent numbers — none were measured here.

### 8.3 Quick wins identified

- Pass `{ url, withCredentials:false }` explicitly and consider `disableRange=false` (default) — verify behavior.
- Show `document_file.preview_url` (JPEG preview, already generated — models.py:498+) as an instant poster under the spinner.
- Cache the last-viewed `DocumentViewer` per document identity instead of destroy/recreate on tab-return (only if memory allows — pdf.js holds the doc).
- Preload `pdf.min.js`/worker on Spark page idle (`requestIdleCallback`) — script is already deferred; worker spawn is per-document.
- For offline docs: blob URL removes network entirely — first page should be near-instant; measure anyway.

---

## 9. Audit G — shared identity & context preservation requirements

The prior implementation (`SPARK_DOCUMENT_CONTEXT_IMPLEMENTATION.md`) established: canonical identity = int PK + share UUID + version; `viewerPageState` keyed by `_resourceStateKey()`; request-time immutable snapshots; fail-closed conflict handling. The redesign **must not regress**:

- **Tab switching:** confirmed safe — panes hide via CSS, viewers persist, `switchWorkspaceTab()` (3924-3985) only toggles classes + `handleResize()`. Keep this pattern for the new Offline tab: render the offline list in its own pane; never unmount `desktopContextDocViewer`/`previewDocViewer`.
- **Offline-opened docs:** must normalize into `contextResources` with `document_share_id` + `document_id` (record needs `document_id` added — currently only `share_id` is stored; the bundle payload includes `document_id`? verify at implementation: `_offline_document_payload` returns `document_id` = share UUID per line ~1320s — **confirm field semantics before reuse**; the picker uses int PK for `document_id` — keep the distinction).
- **Page tracking:** offline viewer's `onPageChange` writes `viewerPageState` under the same key function — reuse verbatim.
- **Version:** store `document_version_id` + file `size_bytes`/etag in the offline record; surface "Update available" when online metadata differs.
- **Study sessions/citations:** they call `previewResource()` with the same resource shape — unchanged.
- **No duplicate resources:** `addResourceToContext` dedupes by identity fields (3990+) — offline adds must flow through it, not bypass.

---

## 10. Audit H — recommended architecture

### Desktop (≥1200 px)

```
app-shell-layout grid:
  col1: left rail (240/56 px, unchanged)
  col2: Spark chat — minmax(0,1fr), floor ~minCenterWidth
  col3: context rail = min(50% of (vw − leftRail), vw − leftRail − minCenter)
        default open width: 50% split; user-resizable [280 … cap]
```

- One 40 px top bar: `[Preview|Context|Offline]` tabs · document title · `⋯` overflow (download, fullscreen, open-original, add/remove context, save-offline).
- Viewer area = remaining height; floating pill (existing component) auto-hides after idle, reappears on interaction; page badge doubles as the location pill (merge `desktopContextLocationBadge` into the pill — removes one chrome band).
- Context tab keeps chips strip but collapses "Add a book" into the tab-bar `+` or overflow menu.
- Footer card eliminated on document surfaces (title lives in top bar; actions in ⋯ menu). Keep footer only for non-viewable card fallbacks.

### Mobile (<1200 px)

- Keep `offcanvas-bottom` sheet; add expanded reading snap ≈ `100dvh − safe-top − 48 px` as default when a document is previewing.
- Same merged top bar (tabs + ⋯); floating pill with auto-hide; safe-area bottom padding preserved; switch inline `85vh` to `dvh`-based value to fix keyboard jump.
- Offline tab lists saved docs; "Read" opens the blob in the same `previewSheetDocViewer` host.

### Offline storage & local loading

- Reuse `pwani-offline` IndexedDB; extend record with `document_id`, `document_version_id`, `file.etag`/`size_bytes`, `access_state`.
- `URL.createObjectURL(record.file_blob)` → `DocumentViewer` — zero new rendering code.
- Request `navigator.storage.persist()` on first save; handle `QuotaExceededError` with honest messaging.
- Revocation: server remains authoritative for Spark context; local reading permitted post-revocation only per product policy (§7.4) — recommend marking revoked and blocking context attachment, keeping bytes until user deletes or storage pressure requires eviction.

### Page-aware context integration

Unchanged pipeline; new code only *produces* resources (offline open) and *consumes* the same viewer contract. `viewerPageState`, snapshots, diagnostics all carry over.

### Quick wins vs. deeper work

**Quick wins:** rail cap formula + saved-clamp; merge chrome bands into top bar; auto-hide on existing floating pill; blob-URL offline opening; preview-image poster; Offline tab pane reusing `PwaniOfflineLibrary`.
**Deeper:** snap-point system for the sheet; storage persistence/quota management; version-sync UX; runtime perf instrumentation; possible viewer instance reuse.

---

## 11. Security & authorization implications

- File URLs are **public unsigned CDN URLs** today — authorization is enforced at metadata/picker/bundle level, not per-file-request. This is existing behavior; the redesign does not change it, but the audit should note that "knowing the URL = access" for non-private docs. Private/`storage_private` files go through `private_resource_asset`/owner checks — the picker already emits correct URLs; preserve that routing for offline saves (the offline file endpoint re-checks authorization — good).
- Offline copies outlive authorization — needs the explicit policy in §7.4/§9; never let offline possession mint Spark context without server revalidation (already fail-closed).
- Do not cache document bytes in Cache Storage (SW) — IndexedDB is the sanctioned store; keeps logout/account-switch cleanup semantics (`native-offline.js` owner checks, `offline-capture.js` invalidation).
- Logout cleanup: `pwani-offline` IndexedDB is **not** cleared on logout today (only native snapshots are, via `PRIVATE_OFFLINE_INVALIDATED` + submit listener). **Probable gap** — add owner-scoping or clear-on-logout for the library DB.

---

## 12. Prioritized implementation plan

| # | Item | Effort | Risk |
|---|---|---|---|
| P0 | Rail width: proportional cap + saved-clamp + 50 % snap | S | Low — one function + one constant |
| P0 | Offline "Read": blob URL → existing DocumentViewer in Preview pane | M | Low — reuses engine; add `document_id` to record |
| P1 | Merge chrome into one top bar + ⋯ overflow menu (desktop & mobile) | M | Medium — many element IDs referenced in JS; keep IDs, restyle/re-parent carefully |
| P1 | Auto-hide floating pill (idle timer, interaction wake, reduced-motion) | S-M | Low — self-contained in document-viewer.js |
| P1 | Offline tab pane (list/search/read/remove/update) in rail + sheet | M | Low — `PwaniOfflineLibrary` API exists |
| P2 | Sheet expanded reading snap + `dvh` fix + keyboard/IME check | S | Low |
| P2 | Preview-image poster during PDF load | S | Low |
| P2 | `storage.persist()` + quota handling + logout cleanup for `pwani-offline` | S | Low |
| P2 | Perf instrumentation + Range verification on CDN | S | None (measurement) |
| P3 | Viewer instance reuse / pdf.js doc cache per identity | M | Medium — memory |
| P3 | Version-sync "Update available" flow | M | Low |

---

## 13. Risks & dependencies

- **Element-ID coupling:** `pwanimate-chat.js` references ~40 rail element IDs; any DOM restructure must preserve them or update JS in the same change. Recommend restyle/re-parent over rename.
- **PDF.js blob + worker:** worker must be same-origin — `/static/vendor/pdfjs/pdf.worker.min.js` is fine for blob URLs.
- **Memory:** two live `DocumentViewer` instances (preview + context) already coexist; offline reading adds a third surface only if mounted simultaneously — reuse the preview host to avoid a third pdf.js doc.
- **CDN Range support:** must be verified in staging; if absent, first-page latency for large PDFs stays poor regardless of UI work — server/CDN config may be the real fix.
- **Bootstrap offcanvas constraints:** custom snap heights fight Bootstrap's transforms; the existing drag script already manipulates inline height — extend it rather than replace.
- **Dependencies:** none new required — pdf.js, Bootstrap, IndexedDB, Capacitor plugin all present.

---

## 14. Test & acceptance criteria

1. At 1920×1080, expanded left rail: context rail opens at ~50 % of `(vw − 240)`; Spark pane ≥ 560 px; resizer reaches the cap both directions; ARIA values match.
2. Left rail collapsed: split recomputes; saved width persists across reload within new bounds.
3. Document preview: PDF viewport ≥ 85 % of rail height on 768 px-tall desktop; all previous actions reachable (tabs, ⋯ menu, floating pill, close).
4. Tab cycling Preview→Context→Offline→Preview: viewer page number, zoom, and `viewerPageState` unchanged; no re-fetch (check Network).
5. Offline: save doc → go offline → Offline tab → Read → PDF renders from blob; page nav works; "Add to context" produces correct `context_resources` entry; Spark answer still cites server-side page text when back online.
6. Mobile: sheet expands to reading snap; handle drag dismiss/expand works; safe-area insets respected portrait+landscape; keyboard open doesn't jump layout.
7. Perf: record click→first-page timings for small/medium/large PDFs cold+warm; verify Range requests in DevTools (206 responses) on the CDN URL.
8. Regression: run `pwanimate/tests/test_document_context.py` + viewer JS tests (`documents/tests/test_document_viewer_js.js`) — all must still pass.

---

## 15. Issue classification index

| Issue | Class |
|---|---|
| Rail capped at 650 px | **confirmed** |
| 50/50 split impossible today | **confirmed** |
| Duplicated chrome bands consume ~250 px vertical | **confirmed** |
| Floating controls exist but never auto-hide | **confirmed** |
| Tab switches preserve viewer/page state | **confirmed** |
| Offline stores file_blob but never renders PDF | **confirmed** |
| Offline record lacks document_id/version/etag | **confirmed** |
| Service worker does not cache document files | **confirmed** |
| File URLs are public unsigned CDN URLs | **confirmed** |
| Django FileResponse lacks Range (download paths only) | **confirmed** |
| pdf.js Range loading works end-to-end via CDN | **unverified** |
| First-page latency bottleneck location | **unverified** (hypotheses in §8.2) |
| IndexedDB eviction/persistence under pressure | **unverified** |
| `pwani-offline` not cleared on logout | **probable** |
| Bottom-sheet `vh` vs `dvh` keyboard jump | **probable** |
| Landscape side-cutout insets on bottom sheet | **probable** |
| Native OfflineStore stores document files separately | **unverified** |
| Viewer unmounts on tab switch | **ruled out** |
| Offline AI inference exists/could be conflated | **ruled out** |

---

## 16. Diagrams

### Existing loading architecture

```
Picker card / citation / study restore
        │ previewResource() → normalizeResource()
        ▼
renderSurface() ──► new DocumentViewer(host, mediaUrl=file.url)
        │                │
        │                ▼
        │         PDFViewer.load()
        │           ├─ pdf.min.js (defer, base.html)
        │           ├─ pdf.worker.min.js (spawn)
        │           └─ pdfjsLib.getDocument(fileUrl)
        │                 │
        │                 ▼
        │         GET https://<cdn>/media/...  (public, unsigned,
        │             CacheControl 7d, Range IF server supports)
        │                 │
        │                 ▼
        │         canvas renderPage(n) → onPageChange
        │                 │
        ▼                 ▼
viewerPageState[key] = {page,totalPages}  (request-time snapshot)
        │
        ▼  POST /api/pwanimate/chat/  context_resources[]
DocumentTurnContext → auth → page evidence → Spark
```

### Recommended future architecture

```
Same trigger paths ──► previewResource()
        │
        ├─ online doc  ──► DocumentViewer(file.url)      [unchanged]
        │
        └─ offline doc ──► PwaniOfflineLibrary.get(shareId)
                           └─► file_blob → URL.createObjectURL
                               └─► same DocumentViewer(blobUrl)
                                   (no network; pdf.js local)
        │
        ▼  both paths
viewerPageState[key] ──► context_resources snapshot ──► Spark (online only)

Rail UI:
  top bar [Preview|Context|Offline] + title + ⋯menu
  PDF viewport (~90%) + floating auto-hide pill
  Offline pane: list/search/read/remove/update (IndexedDB)
```

---

*End of audit. No application code, templates, styles, or models were modified.*
