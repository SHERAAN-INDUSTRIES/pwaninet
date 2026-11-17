# WP0 ICON AUDIT — Vasto / PwaniNet

**Deliverable:** `docs/refactors/lucide/WP0_ICON_AUDIT.md`
**Status:** FOUNDATION — no application icons have been changed yet.
**Scope:** Cypriot-only audit of icon dependencies, the existing `pwanimate` Lucide
implementation, and per-app icon usage. Migration starts only after sign-off.

---

## 1. Actual discovered application structure

Django installable apps and their template roots (verified by reading `apps.py` / template dirs):

| App | Template root | Own icon-bearing templates | Primary icon dependency in own templates |
|-----|---------------|----------------------------|------------------------------------------|
| `admin_dashboard` | `admin_dashboard/templates/admin_dashboard` | many | **Bootstrap Icons** (`bi-*` classes) |
| `calling` | `calling/templates/calling` | none directly | none detected |
| `core` | `core/templates` | empty `core/templates/core/` | none detected |
| `courses` | `courses/templates/courses` | `partials/` + `unit_detail.html` | none directly |
| `documents` | `documents/templates/documents` | `academic_dashboard.html`, `base.html`, `home.html` | none directly |
| `groups` | `groups/templates/groups` | `group_chat.html` (28 KB) | none detected |
| `messaging` | `messaging/templates/messaging` | `conversation_list.html`, `conversation_detail_refactored.html`, `partials/` | **Bootstrap Icons** |
| `messages` | *(alias for `messaging`)* | — | — |
| `notifications` | `notifications/templates/notifications` | `components/notification_card_*.html`, `partials/` | **Bootstrap Icons** |
| `posts` | `posts/templates/posts` | `home.html` + `partials/` | **Bootstrap Icons** |
| `pwanimate` | `pwanimate/templates/pwanimate` | ~30 templates | **Lucide** (`<i data-lucide>`) + Spark mapping layer |
| `recommendations` | `recommendations/templates` | empty `recommendations/templates/recommendations/` | none detected |
| `releases` | `releases/templates/releases` | `partials/dashboard_content.html`, `create_content.html`, `edit_content.html`, `add_item_content.html`, `publish_content.html`, `list_content.html`, `archive_content.html`, `detail_content.html` | **Bootstrap Icons** |
| `search` | `search/templates/search` | `search_results.html`, `partials/tab_content.html` | **Bootstrap Icons** |
| `users` | `users/templates/users` | `notification_preferences.html`, `onboarding/`, `partials/`, `emails/` | none directly detected (2FA is code-only, no icon CSS) |
| `workspace` | `workspace/templates/workspace` | `sync.html` | none directly detected |

Shared partials used across apps (contain `bi-*` usage; not app-owned):
- `templates/partials/desktop_left_rail.html` — sidebar rail icons (`bi-house-door-fill`, `bi-people-fill`, `bi-chat-dots-fill`, `bi-folder2-open`, `bi-journal-bookmark-fill`, `bi-bell-fill`, `bi-person-fill`, `bi-gear-fill`, `bi-cloud-arrow-down-fill`, `bi-cloud-arrow-up-fill`, `bi-shield-lock-fill`, `bi-database-fill-gear`, `bi-plus-circle-fill`).
- `templates/partials/top_nav.html` — top-nav icons + theme toggle + profile (`bi-house-fill`, `bi-people-fill`, `bi-chat-dots-fill`, `bi-plus-circle-fill`, `bi-bell-fill`, `bi-shield-shaded`, `bi-circle-half`, `bi-sun`, `bi-moon`, `bi-display`, `bi-person-fill`).
- `templates/partials/_form_field.html` — form field prefix/suffix chevron + warning icon (`bi-chevron-right`, `bi-exclamation-triangle-fill`).
- `templates/partials/default_context_rail.html` — context rail segment icons (`bi-stars`, `bi-file-earmark-text`, `bi-chevron-right`, `bi-people`).

Also relevant: `templates/base.html` loads `bootstrap-icons.css` and defines a
`font-family: "bootstrap-icons"` rule; `templates/partials/` is the shared navigation
layer that WP2 targets.

---

## 2. Existing icon dependencies

| Library | Version / asset | Status | Location | Used by |
|---------|-----------------|--------|----------|---------|
| **Lucide** | Standalone minified build (`lucide.min.js`, 485 198 bytes, ISC). **Version number is NOT embedded** in the bundled artifact (no `version` string, no `package.json`, no `package-lock.json` anywhere in the repo). | Self-hosted, **vendored locally**. Used by `pwanimate` + Spark. | `static/vendor/lucide/lucide.min.js` (+ `LICENSE`) | `pwanimate` |
| **Bootstrap Icons** | CSS `bootstrap-icons.css` (85 875 bytes) + webfont `bootstrap-icons.woff2` (130 396 bytes) + `bootstrap-icons.woff` (176 032 bytes). Fully self-hosted. | **Self-hosted**, no CDN. | `static/css/bootstrap-icons.css`, `static/css/fonts/bootstrap-icons.woff{2}` (CSS resolves `fonts/bootstrap-icons.woff2` relative to `static/css/`), `staticfiles/css/fonts/bootstrap-icons.woff2` (verified present in the collected static). | admin_dashboard, messaging, notifications, posts, releases, search, + shared partials |
| **Font Awesome** | `font-awesome-4.0.3.css` (21 658 bytes) | **Dead / non-app dependency.** Present only inside DRF's installed package (`venv/.../site-packages/rest_framework/static/rest_framework/...`). No `fa-` class is emitted by any app template or JS. | DRF package ||
| **Text fonts** | Nunito, Inter, Roboto, custom (woff2/truetype) | Decorative type only, not icons. | `static/fonts/`, `static/vendor/` | all |
| **Custom SVG / inline SVG** | Brand PNG (`pwanimate-transparent.png`), `favicon.svg`, inline SVG in templates, `static/css/repository.css` uses `.bi-circle::before{content:"\f26e"}` (Glyph via CSS `::before` instead of the `bi-` class) | Preserve; do not infer-from. | various | repository, pwanimate |
| **Unicode / literal emoji** | No literal Unicode emoji characters exist in templates. The only "emoji" surface is Bootstrap Icons' `bi-emoji-*` webfont glyphs (`bi-emoji-smile` etc.) used in `base.html` (comments composer, guest-auth modal, etc.). | Preserve emoji as glyphs; do not replace with Lucide. | `templates/base.html` | all |

Notes / observations:
- `bootstrap-icons.css` sets `font-family: "bootstrap-icons"` globally and maps every
  `bi-*` class to a private-use codepoint via `::before`. This means Bootstrap Icons
  render through a font; Lucide renders through inline SVG. These are **different
  rendering mechanisms** and must not be mixed on the same control without a decision.
- Font Awesome is a DRF sub-package artifact. It is not loaded by the app and adds no
  icons to the UI. Recommend leaving it (no action) or removing the file from the DRF
  install if it can be proven unused — do not touch it in WP0.
- There are **two** `fonts/` directories: `static/fonts/` (text fonts only) and
  `static/css/fonts/bootstrap-icons.woff2` (icon webfont). The CSS reference is relative
  to `static/css/`, so the icon font resolves correctly in dev. The `staticfiles/` copy
  was verified to contain the same files (bootstrap-icons.woff2, bootstrap-icons.woff,
  bootstrap-icons.css, lucide.min.js, LICENSE, nunito/inter/roboto/custom).

---

## 3. How `pwanimate` currently implements Lucide

### 3.1 Loading
- `static/vendor/lucide/lucide.min.js` is loaded **locally** (no CDN) via
  `<script src="{% static 'vendor/lucide/lucide.min.js' %}"></script>` in
  `pwanimate/templates/pwanimate/index.html` and `.../sessions/index.html`.
- `static/css/pwanimate/pwanimate.css` references the vendor path (no external fetch).

### 3.2 Icon markup pattern
- Placeholder: `<i data-lucide="copy" style="width:1.1rem;height:1.1rem;"></i>`.
- No `fa-`/`bi-` classes coexist on the same element in pwanimate templates.
- Colors are inherited via CSS (`currentColor` in `pwanimate.css`); sizes are set with
  inline `style="width/height"` or CSS classes.

### 3.3 Rendering / initialization
- `window.lucide.createIcons()` is the single render entry point. It is **idempotent**
  (re-running on the same root updates/merges rather than duplicating SVGs).
- `static/js/pwanimate/spark-icons.js` (self-contained IIFE) provides:
  - `SPARK_BI_ICON_MAP` — a Bootstrap Icons → Lucide name map (e.g. `bi-check2`→`check`,
    `bi-search`→`search`, `bi-bookmark`→`bookmark`). Note: some Bootstrap *aliases*
    like `check-circle-2`/`bi-circle` accept digit-bearing Lucide names; the map covers
    both the `bi-*` surface and a seeded semantic set (`brain`, `list-checks`, `globe`,
    `book-search`, `wrench`, `clipboard-check`, `shield-check`, `message-circle-question`,
    `file-pen-line`, `refresh-cw`, `circle-dot`, `check-circle-2`, `circle-alert`,
    `circle-x`, `ban`, `sparkles`, `file`).
  - `sparkIconSpec(value)` — accepts a legacy `bi-*` class string (or a bare Lucide name),
    returns `{ name, extra }` and swallows unknown tokens into a `class` list.
  - `sparkRenderIcons(root)` — calls `window.lucide.createIcons({ root })` when defined.
  - `sparkSetIcon(el, value)` — swaps a `<i data-lucide>` (or rendered `<svg>`) placeholder
    with a fresh `<i data-lucide>`, preserving every attribute except `class` /
    `data-lucide` / `data-spark-icon-swap`, then re-renders the parent.
  - **Lifecycle** (`attachSparkIconLifecycle`): runs `sparkRenderIcons(document)` on
    `DOMContentLoaded` (or immediately if ready), listens to `htmx:afterSwap` and a
    `MutationObserver` on `document.documentElement` (childList + subtree) to re-render any
    `[data-lucide]` that appears in dynamically injected nodes. Scans are queued and flushed
    once per `requestAnimationFrame` to avoid rescanning the whole document per microtask.
- `spark-icons.js` self-no-ops when `window.lucide` is absent, so it can load site-wide.

### 3.4 Can this be reused site-wide? — YES, with consolidation.
- The rendering contract is: placeholder `<i data-lucide="name">`, then a `createIcons`
  call scoped to the inserted DOM. This is exactly the contract a shared foundation needs.
- The Bootstrap Icons → Lucide mapping is already expressed in `spark-icons.js`; it can be
  promoted to a shared, version-pinned mapping module and extended centrally.
- Risks when porting: (a) `sparkSetIcon` carries over `data-lucide` and `class` attributes
  in a way that is specific to Spark's `i` placeholder; (b) the `MutationObserver`
  subtree scan is broad — a scoped variant should be used where the insertion point is
  known; (c) inline `<script>` blocks in pwanimate templates are craft-specific and must
  be replaced by a shared loader.

---

## 4. Icon usage inventory by app

Legend: `L` = Lucide, `B` = Bootstrap Icons, `—` = none, `–` = only via shared partial.

| App | Templates | Dependency seen | Classification of discovered icons |
|-----|-----------|-----------------|------------------------------------|
| `pwanimate` | ~30 templates (top_nav, chat_content, people_section, conversation_list, context_rail, left_rail, person_card, sessions_*, settings_*) | `L` | Lucide already in use; preserving. Spark semantic activity icons (`brain`, `globe`, `book-open`, `list-checks`, `wrench`, `shield-check` …). |
| `admin_dashboard` | `dashboard_content.html`, `user_logins_content.html`, `base_workspace.html` | `B` | Interface icons to migrate. Includes brand/visual wheel (dashboard stats, panel icons). |
| `notifications` | `notification_card_*.html`, `partials/notifications_content.html`, `notification_badge.html`, `expanded_actors.html`, `notification_list_*.html` | `B` | Interface icons to migrate (bell, person, people, send, chevron-down, plus dynamic `bi-{{ context.icon }}` and `action.icon`). |
| `posts` | `home.html`, `partials/post_card.html`, `reel_card.html`, `search_results_content.html`, `shared_posts_content.html`, `post_detail_content.html`, `offline_media_viewer_content.html`, `link_preview_card.html`, `storage_manager.html`, `posting_progress_modal.html` | `B` | Interface icons to migrate (search, x, person-plus, heart, repeat, send …). |
| `releases` | `partials/dashboard_content.html` and the create/edit/add_item/publish/list/archive/detail content partials | `B` | Interface icons to migrate (plus-lg, check-circle-fill, exclamation-triangle-fill, pencil-square, upload, eye, archive, info-circle). |
| `messaging` | `conversation_list.html`, `conversation_detail_refactored.html`, `partials/conversation_right_rail.html`, `suggestion_cards.html`, `user_search_results.html` | `B` | Interface icons to migrate. |
| `search` | `partials/tab_content.html` | `B` | Interface icons to migrate (lightbulb-fill, search, people, patch-check-fill, star-fill, file-earmark-text, chat-square-text, shield, person-x, file-earmark-x, chat-square-x, shield-x). |
| Shared partials | `desktop_left_rail.html`, `top_nav.html`, `_form_field.html`, `default_context_rail.html` | `B` | Interface icons to migrate (migration will centralize the `bi→Lucide` map so these use the shared helper). |
| `calling` | `shell.html`, `partials/` | — | none detected |
| `core` | `templates/core` | — | none detected |
| `courses` | `partials/`, `unit_detail.html` | — | none detected |
| `documents` | `academic_dashboard.html`, `base.html`, `home.html` | — | none detected |
| `groups` | `group_chat.html` | — | none detected |
| `recommendations` | `templates/recommendations` | — | none detected |
| `users` | `notification_preferences.html`, `onboarding/*`, `partials/`, `emails/*` | — | none directly; 2FA services are Python-only (no icon CSS) |
| `workspace` | `sync.html` | — | none detected |

Decorative / brand / illustration to **preserve** (not Lucide-ify):
- pwanimate brand logo (`pwanimate-transparent.png`) and `favicon.svg` (inline SVG).
- `templates/base.html` Bootstrap Icons emoji glyphs (`bi-emoji-smile`) are a specific
  glyph set — these are brand/visual, not Lucide controls.
- `static/css/repository.css` uses `bi-circle::before { content: "\f26e" }` — a Bootstrap
  glyph injected via CSS; any CSS-driven icon surface must be reviewed individually.

Classification summary:
1. **Interface icons to migrate** — the bulk of `bi-*` in admin_dashboard, messaging,
   notifications, posts, releases, search, and the shared partials.
2. **Existing Lucide to preserve** — pwanimate + Spark icons (`<i data-lucide>`).
3. **Brand identity / logo to preserve** — pwanimate logo, favicon.
4. **Domain-specific illustration or asset to preserve** — `pwanimate-transparent.png`,
   `favicon.svg`.
5. **Decorative symbol requiring manual review** — Bootstrap emoji glyphs, the
   `bi-circle::before` CSS glyph, any inline SVG not part of the control set.

---

## 5. Proposed canonical Lucide architecture

Adopted from the `pwanimate` pattern, consolidated and pinned site-wide:

- **Version pinning:** record the deployed version of `lucide.min.js` (currently not
  embedded in the artifact — see WP0 §10). Recommended: bundle the exact version via a
  pinned npm package (`lucide-static` or the `lucide` package export) and commit the
  vendor file + checksum so it can never drift from the lock.
- **Local vendor only:** `static/vendor/lucide/lucide.min.js` remains the single source;
  application code never fetches Lucide from a CDN at runtime.
- **Template helper:** a small Django inclusion tag / `{% lucide "name" %}`-style helper
  that emits `<i data-lucide="name"></i>` (or a `<span>` wrapper with `aria-hidden`).
  This centralizes class strings and prevents raw hand-rolled markup.
- **Shared JS module** (migrated from `spark-icons.js`): `sparkIconSpec` mapping,
  `sparkRenderIcons(root)` (scoped `createIcons`), `sparkSetIcon(el, value)` (attribute-
  preserving swap), and a **scoped** icon scan (only the nearest insertion container +
  a lightweight MutationObserver, not a global subtree scan) for dynamic content.
- **Themes:** icons use `currentColor` so they inherit the foreground color of their
  container; sizes are fixed via standardized CSS classes (`.icon-sm`/`16px`,
  `.icon-md`/`20px`, `.icon-lg`/`24px`) instead of inline `style="width/height"`.
- **Hover/active states:** use `currentColor` fill and CSS `filter`/`transform` for
  tint/opacity, never stroke re-declaration.
- **Idempotency & duplication:** `createIcons` is idempotent on the same root; the shared
  module caches a single render pass per container and skips re-render of already-rendered
  `<svg>.lucide` nodes when the same root is rescanned.
- **Dynamic insertion:** new `[data-lucide]` nodes are discovered and rendered within the
  nearest known container (HTMX `afterSwap` target, modal open, dropdowns, lazy-loaded
  panels) rather than rescanning the whole document after every update. A MutationObserver
  is retained as the safety net only for unknown insertion points.
- **CSP / XSS:** placeholders are plain `<i>` elements with a `data-lucide="name"` string
  (no user content rendered as SVG markup). The shared helper validates icon names against
  the pinned Lucide registry (denylist of unknown names fails closed to a no-op or a
  visible empty placeholder that is hidden from accessibility). No `innerHTML` injection
  of icon markup.
- **Bundle growth:** Lucide is already vendored once (pwanimate). The shared layer adds
  only the mapping table + `createIcons` delegation — no second runtime. Do not add a
  second Lucide bundle.

Standard sizes (subject to WP1 review): navigation 20px, compact control 16px, larger
action 24px, default stroke 1.75px, foreground via `currentColor`.

---

## 6. Migration sequence and risk assessment

| Phase | Work | Risk | Mitigation |
|-------|------|------|------------|
| 0 | Write this audit; pin Lucide version; restore/verify `staticfiles` integrity (vendor/lucide, bootstrap-icons css+font); add the shared icon helper + JS module; document CSP/inline-script policy. | Low | Keep all existing `bi-*` templates working until their turn in Phase 2/3. |
| 1 | Migrate **shared navigation layer** (desktop_left_rail, top_nav, _form_field, default_context_rail, plus base.html/modal/notification/search/profile surfaces referenced from WP2). | Medium | Use the shared helper; replace `bi-*` with the mapping in review order; keep `aria-label`/`title`/keyboard contracts; run before touching per-app templates. |
| 2 | Per-app migration in increasing volume: **search** → **admin_dashboard** → **notifications** → **posts** → **releases** → **messaging**. Handle `bi-{{ dynamic }}` / `action.icon` via the mapping (fallback: documented exception). | Medium–High | Audit dynamic class strings first; verify each proposed Lucide name against the pinned version; add regression tests per app. |
| 3 | `calling`, `groups`, `documents`, `workspace`, `users`, `core`, `courses`, `recommendations` — confirm no icons exist; no changes required (except shared-partial inheritance). | Low | None. |
| 4 | QA: automated icon-name/rendering checks + full manual visual pass (light/dark, mobile/desktop, AJAX/HTMX/modal, console errors, Spark regression). | Medium | Per WP4. |

Risk details:
- **High-volume `bi-*` replacement** (especially `top_nav`, `desktop_left_rail`,
  `notification_card_*`, `posts` home) is the biggest change surface. Mitigate by
  changing one file at a time and keeping the Bootstrap Icons layer present until its
  app is migrated.
- **Dynamic class strings** (`bi-{{ payload.context.icon }}`, `action.icon`) cannot be
  mapped by name alone; they need a per-context registry — treat as exceptions and
  document with justification.
- **Theme compatibility:** Lucide uses `currentColor`; Bootstrap Icons use a font glyph
  colored by CSS. Mixed surfaces (e.g. an icon inside a button that already uses a
  `bi-*` color class) must be converted atomically to avoid flashing.
- **Deployment:** `staticfiles` must be regenerated and verified to contain the pinned
  Lucide vendor file + CSS. Do not rely on CDN for `staticfiles` build.
- **pwanimate is already migrated** — no migration of `pwanimate` is required for WP1–WP4.

---

## 7. Expected file changes

Added (not present today):
- `templates/` tag/helper for a Lucide icon (e.g. `templates/lucide.html` or an inclusion
  tag module) — emits `<i data-lucide="name"></i>` with optional `aria-hidden`/`title`.
- `static/js/` shared module consolidating `spark-icons.js` mapping + `createIcons`
  delegation + scoped `sparkSetIcon` + scoped dynamic-insertion scan.
- Documentation: WP1 canonical Lucide architecture + version-pin manifest.

Modified (supports Lucide where present; converts `bi-*` → Lucide in review order):
- `templates/partials/desktop_left_rail.html`, `top_nav.html`, `_form_field.html`,
  `default_context_rail.html` (shared navigation).
- `base.html` (icon CSS/JS includes, theme support).
- `static/css/repository.css` (legacy `bi-circle::before` glyph surface — review individually).
- App templates under admin_dashboard, messaging, notifications, posts, releases, search.
- `staticfiles/` regeneration script/config (guaranteed to include pinned vendor assets).

Preserved (no change):
- `pwanimate` templates and `spark-icons.js` (already Lucide-native).
- Brand logo, favicon, emoji glyphs, bootstrap-icons css/font (until their owning app is migrated).
- Font Awesome 4.0.3 (DRF-only, dead).

Deleted: none in WP0. Icon assets are not deleted until references are verified gone.

---

## 8. Suggested automated tests

- **Icon-name catalog:** a machine-readable manifest of every `data-lucide="…"` and
  `bi-*` name in templates; a linter rejects unknown Lucide names against the pinned
  registry and rejects `bi-*` that has no mapping.
- **Duplicate-check:** detect two `data-lucide` icons with the same `name` in the same
  tree (optional, visual tolerance) and detect duplicate icon *meanings* in adjacent UI.
- **Deprecated-icon scan:** fail CI when a `bi-*` class has no entry in the shared mapping
  (catches the `bi-circle::before` + dynamic `bi-{{ … }}` cases).
- **Rendering smoke test:** headless render of a representative page with the Lucide
  bundle present and absent; assert `svg.lucide` count > 0, no empty placeholders
  (`!svg.querySelector("path, circle, rect, line, polyline, polygon, ellipse")`), and
  `window.lucide.createIcons is a function`. No network fetch when offline.
- **Dynamic-content test:** insert a template fragment containing `[data-lucide]` via
  HTMX `afterSwap` and a custom insertion; assert the icon is rendered and no duplicate.
- **Console/offline test:** run the page with `about:blank`/offline network disabled and
  assert: no Lucide network request, no JS console errors, icons still render.
- **Theme test:** run light + dark; assert icon `color` inherits `currentColor` and no
  glyph is missing/tofu.
- **Accessibility test:** icon-only buttons have `aria-label` (or `aria-hidden` for
  decorative); decorative icons are hidden from AT.
- **Regression tests:** existing Spark interaction tests (study-session icon state,
  activity trail) must still pass.

---

## 9. Areas requiring manual visual verification

- Desktop navigation (top_nav) and left sidebar rail — alignment, icon size, active/inactive state.
- Mobile navigation (hamburger, context sheet, bottom nav) at 320px–768px.
- Dropdown menus, profile menu, notification bell, search controls.
- Modal close + navigation buttons; buttons with icon-only `aria-label`s.
- Spark activity trail (brain/globe/book-open/list-checks/wrench/shield-check activity
  states) and status-colored icons — **do not regress Spark**.
- Dynamic content: modals, dropdowns, off-canvas menus, htmx-swapped panels, lazy-loaded
  panels, stage/media inserts.
- Light and dark themes (icon foreground inheritance, `currentColor`, filter filters).
- `staticfiles` cold build — icons render with no network.
- Contrast of Lucide glyph strokes on primary/warning/secondary color backgrounds.

---

## 10. Open architectural decisions

1. **Pin the exact Lucide version.** The vendored `lucide.min.js` is a full-icon
   standalone build but carries no version string; the repo has no `package.json` or
   lockfile for it. Resolve by pinning the version (e.g. via `lucide-static` npm package
   or the official export), regenerating the vendor file from the pinned source, and
   committing the version + SHA-256. Until resolved, the exact version is **unknown**.
2. **Scope of the shared Lucide helper.** Decide whether to emit `<i data-lucide>`
   (with a JS render step) or inline `<svg>` at template compile time. The `pwanimate`
   contract is `<i data-lucide>` + `createIcons`; inline SVG would move the
   bootstrap-to-lucide mapping into a preprocessor and remove the runtime JS dependency —
   pick one and keep it consistent site-wide.
3. **Dynamic `bi-{{ … }}` / `action.icon` strings.** These are per-context mappings that
   need a registry. Document exceptions with justification until each context is mapped.
4. **CSS-driven glyph surface in `repository.css`** (`bi-circle::before` content trick).
   Decide whether to fold it into the shared icon helper's CSS (as a `<span class="icon">`
   with `data-lucide`) or retire it; it cannot be migrated by name lookup.
5. **Bootstrap Icons vs Lucide coexistence.** The shared partials and many app templates
   currently use `bi-*` alongside unused Lucide. Decide the cutover order (shared partials
   first) so no app sees both families simultaneously.
6. **`staticfiles` identity.** Confirm the committed `staticfiles/` snapshot is regenerated
   and version-pinned for deployed environments; do not depend on the source `static/`
   tree in production.
7. **CSP policy.** Verify that inline `<script>` blocks and the Lucide `<script>` tag are
   permitted by the site CSP; the shared foundation must not introduce new inline scripts.

---

*Audit produced for WP0. No application icons have been modified. Next step: WP1 — propose
the pinned, canonical Lucide integration.*
