# Spark Context Rail — Stress Test Plan

**Project:** Vasto / PwaniNet — Spark (Pwanimate)
**Scope:** Document-first context workspace — desktop split, consolidated chrome, floating controls, mobile sheet, offline tab, local PDF opening, page-aware context preservation.
**Prereqs:** dev/staging deployment of this branch; test accounts (student + restricted-doc owner); representative PDFs (see fixtures); Chrome + Android ( Capacitor WebView or Chrome mobile ) + Safari iOS if available.

> Do not run destructive load tests. All scenarios are single-user interactive unless marked otherwise.

---

## F — Fixtures

| ID | PDF | Purpose |
|---|---|---|
| F1 | ~100 KB, 5 pages, text | small/fast baseline |
| F2 | ~2 MB, 40 pages, text+images | medium |
| F3 | ~25 MB, 300 pages, image-heavy | large/memory |
| F4 | scanned/image-only PDF | OCR-less honesty check |
| F5 | DOCX + PPTX + TXT | non-PDF viewer regression |
| F6 | restricted/private doc owned by another student | authorization isolation |

Tools: DevTools Performance/Network (throttling: Offline, Slow 3G, Fast 3G, none); `performance.getEntriesByName('pw:*')` for the instrumentation marks; IndexedDB inspector for `pwani-offline`.

---

## S1 — Desktop 50/50 split & resizing

| # | Steps | Expected |
|---|---|---|
| S1.1 | 1920×1080, nav expanded, open rail | rail ≈ 50 % of (1920−240); chat ≥ 480 px |
| S1.2 | Collapse left nav, reopen rail | split recomputes vs 56 px rail; wider rail |
| S1.3 | Drag resizer to both extremes | clamps at min 280 and max (split or center-floor bound); ARIA values update live |
| S1.4 | Keyboard: focus resizer, ArrowLeft/Right/Home/End | 16 px steps; Home→min, End→max; values persist |
| S1.5 | Double-click resizer | snaps to equal split, clears saved pref |
| S1.6 | Reload page | deliberate width persists; untouched pref → 50 % again |
| S1.7 | Resize window 1200→1900→1300 px | limits recalc; rail clamps without overflow; chat never < 480 px |
| S1.8 | 1199 px width | desktop rail hidden; mobile sheet path used instead |

**Pass:** all width invariants hold; no horizontal scrollbar; viewer re-fits (handleResize) after each change.

## S2 — Consolidated chrome & viewport utilization

| # | Steps | Expected |
|---|---|---|
| S2.1 | Open F2 in Context tab | one 40 px header (tabs+title+⋯+close); no card header/footer bands; PDF ≥ ~85 % of rail height |
| S2.2 | ⋯ menu: Open original / Download / Save offline / Refresh / Fullscreen / Add / Remove / Close preview | each works; disabled states correct per surface |
| S2.3 | Truncate title with long filename | ellipsis; full name in `title` attr |
| S2.4 | Context tab with 8+ resources | chips stay one compact row, horizontal scroll, "+" inline; no vertical growth |
| S2.5 | Non-document previews (video/image/post/profile) | still render correctly; ⋯ menu degrades gracefully |
| S2.6 | Theme toggle light/dark | header, menu, floating bar, offline list all themed; contrast OK |

## S3 — Floating controls & auto-hide

| # | Steps | Expected |
|---|---|---|
| S3.1 | Open PDF, idle 4 s | pill fades out ~2.8 s |
| S3.2 | Move pointer / tap / scroll / press key inside viewer | pill reappears immediately |
| S3.3 | Tab-focus into viewer area | controls reachable; focus inside bar prevents hiding |
| S3.4 | `prefers-reduced-motion: reduce` | controls never auto-hide; no transitions |
| S3.5 | Page nav + zoom via pill during reading | works; page badge updates; Spark page context tracks |
| S3.6 | Narrow rail (~400 px) | pill doesn't cover page footer text (check padding/overlap) |

## S4 — Mobile bottom sheet

| # | Steps | Expected |
|---|---|---|
| S4.1 | Open citation on phone | sheet opens expanded (~90dvh), handle visible |
| S4.2 | Drag handle down >90 px | dismisses; up → expanded snap |
| S4.3 | Portrait + landscape | safe-area insets respected; no content under notch/nav bar |
| S4.4 | Open keyboard (composer) | sheet resizes via dvh; no overlap/jump |
| S4.5 | Pinch-zoom PDF vs sheet drag | no gesture conflict |
| S4.6 | Tabs Preview/Context/Offline in sheet | all three work; viewer state preserved across switches |

## S5 — Offline tab & local PDF

| # | Steps | Expected |
|---|---|---|
| S5.1 | Save F2 via rail ⋯ or picker quick-save | appears under "Saved on this device" with size/pages/sections |
| S5.2 | DevTools → Offline; open Offline tab → Read | PDF renders from local bytes; **Network tab shows zero PDF requests** |
| S5.3 | Navigate pages/zoom offline | works; `viewerPageState` updates |
| S5.4 | Re-open same doc | no reload (isSameDoc), instant |
| S5.5 | "+" add to context, go online, ask "summarize this page" | Spark resolves share UUID → correct page evidence + citation |
| S5.6 | Search box | filters saved + available lists correctly |
| S5.7 | Update badge: bump doc server-side, reopen tab online | "Update available" → Update re-saves |
| S5.8 | Remove | record + blob URL gone; Read disabled/absent |
| S5.9 | Corrupt blob (delete `file_blob` in IDB) | honest error, no crash; Metadata-only badge |
| S5.10 | Save F6 (restricted) as authorized user, switch to unauthorized account | record invisible to second account; server still 404s context |
| S5.11 | Logout / account switch | `pwani-offline` cleared; blob URLs revoked |
| S5.12 | Fill storage / tiny quota | friendly quota error, no partial record |

## S6 — Performance measurement (instrumented)

For F1/F2/F3, cold + warm cache, online:

| Metric | How |
|---|---|
| click → first request | `pw:preview-click` mark vs Network panel |
| request → headers / first bytes | Network timing |
| → first painted page | `pw:first-page-total` measure |
| → fully loaded | `pw:pdf-doc-loaded`, `pw:viewer-init` |
| bytes transferred | Network panel |
| Range support | check for `206 Partial Content` on PDF request |
| offline open | same marks with Network offline — should show no remote PDF fetch |

**Record baseline before/after comparison; do not invent numbers.**

## S7 — Context & regression

| # | Steps | Expected |
|---|---|---|
| S7.1 | Cycle Preview→Context→Offline→Preview with doc open | page number, zoom, scroll preserved; no refetch |
| S7.2 | Study-session restore | doc + page restored; viewer mounts correctly |
| S7.3 | Citation click mid-conversation | opens correct doc/page; `context_resources` snapshot correct |
| S7.4 | Quick actions + free-form page questions | unchanged behavior (run `test_document_context` suite) |
| S7.5 | Two docs in context, switch chips | each keeps own viewer/page state |
| S7.6 | Automated: `manage.py test pwanimate.tests.test_document_context`, `node documents/tests/test_document_viewer_js.js`, `manage.py check` | all pass |

## S8 — Stress conditions

- Rapid open/close of F3 (25 MB) ×10 — watch memory, no leaked viewers/blob URLs (IDB + `blob:` URLs in DevTools).
- Throttled Slow 3G open of F2 — spinner shows, no double-fetch, error path honest on failure.
- Offline → save attempt → online → retry — save completes once, no duplicates.
- Account A saves docs → logout → account B logs in → Offline tab empty; A logs back → docs restored only if not cleared by logout policy (verify intended behavior).
- Private-browsing / storage-denied browser — library degrades with clear message, chat unaffected.

## Exit criteria

- All S1–S5 pass on desktop + one mobile environment.
- S6 measurements recorded for the fixture set (numbers documented, not asserted).
- S7 automated suites green.
- No console errors during S1–S5 except known-benign warnings.
