# WP2 — Shared navigation migration

Date: 2026-11-17. WP2 was explicitly authorized after provisional technical acceptance of WP1. The seven unrelated Spark settings failures/errors remain tracked in the WP1 report and were not repaired or masked here.

## Result and scope

Migrated shared Vasto navigation and common shell controls using the existing `window.VastoIcons` implementation. The actual local v1.71.0 vendor and shared module were not changed or reimplemented. The WP1 browser fixture passed before migration and again at handover. Vendor SHA-256 remains `9ad0d8d291f2108b78772861b706555193bed6d236118e149e2a3a0cc0f4211c`.

Reviewed and replaced 47 static/source placeholders, including the install-copy success placeholder, plus three dynamically emitted common banner placeholders: **50 placeholder locations**. The guest prompt retains its existing Bootstrap-name input API through 12 validated mappings. This is a shared-control adapter, not an application migration. All four priority partials have no remaining Bootstrap icon placeholders.

## Files changed in WP2

- `templates/partials/desktop_left_rail.html`: 13 icons; existing destinations, conditions, badges and messaging/full-page bypass hooks retained. Dashboard, Spark, Workspace and profile images preserved.
- `templates/partials/top_nav.html`: 12 icons, including tablet navigation, notification controls, theme menu and avatar fallback. Added accessible names to the existing tablet notification control and profile link.
- `templates/partials/default_context_rail.html`: 5 icons for Campus Hub, materials, study groups and chevrons.
- `templates/partials/_form_field.html`: label chevron and validation warning. Input, checkbox, radio, select, textarea, required/error/value behavior unchanged.
- `templates/base.html`: mobile tabs; 2FA nudge/dismiss; back-to-top; install-link modal/copy states; guest prompt; common notification/message banner icons. Exact integration adjustments described below.
- `static/css/vasto-navigation-icons.css`: new, opt-in shared-control sizing, loaded by base outside the child extra_css block.
- `tests/lucide/navigation_browser.py`: new browser regression using actual Django templates/controllers and local assets.
- `tests/lucide/navigation_contract.cjs` and `shared-navigation-mappings.json`: reviewed placeholder inventory, registry/accessibility/coexistence checks and coverage of existing guest-prompt callers.
- `tests/lucide/test_infrastructure.py`: existing safe collection regression extended to verify the new stylesheet is collected byte-for-byte.
- This report.

The base template already contained unrelated admin loader changes and WP1 loading changes at takeover; those are preserved, not claimed as WP2 work. Unrelated working-tree changes, including concurrent backend/migration work, were left intact. No application backend, Spark runtime/settings/test fixtures, schema, authentication behavior, dependency or deployment configuration was modified. No commit was made.

## Exact mappings

All names below were checked against the actual vendored registry, including dynamic guest-prompt values. Some Bootstrap input tokens intentionally remain in callers and the adapter; their common prompt renders Lucide only.

- `bi-arrow-up` → `arrow-up`
- `bi-bell-fill` → `bell`
- `bi-bookmark-fill` → `bookmark`
- `bi-box-arrow-in-right` → `log-in`
- `bi-chat-dots` → `message-circle`
- `bi-chat-dots-fill` → `message-circle`
- `bi-check2` → `check`
- `bi-chevron-right` → `chevron-right`
- `bi-circle-half` → `contrast`
- `bi-clipboard` → `clipboard`
- `bi-cloud-arrow-down-fill` → `cloud-download`
- `bi-cloud-arrow-up-fill` → `cloud-upload`
- `bi-compass-fill` → `compass`
- `bi-database-fill-gear` → `database`
- `bi-display` → `monitor`
- `bi-download` → `download`
- `bi-exclamation-triangle-fill` → `triangle-alert`
- `bi-file-earmark-text` → `file-text`
- `bi-folder2-open` → `folder-open`
- `bi-gear-fill` → `settings`
- `bi-heart-fill` → `heart`
- `bi-house-door-fill` → `house`
- `bi-house-fill` → `house`
- `bi-journal-bookmark-fill` → `book-marked`
- `bi-link-45deg` → `link`
- `bi-moon` → `moon`
- `bi-people` → `users`
- `bi-people-fill` → `users`
- `bi-person-circle` → `circle-user-round`
- `bi-person-fill` → `user-round`
- `bi-person-plus` → `user-round-plus`
- `bi-phone` → `smartphone`
- `bi-play-circle-fill` → `circle-play`
- `bi-plus-circle-fill` → `circle-plus`
- `bi-repeat` → `repeat-2`
- `bi-shield-lock-fill` → `shield-check`
- `bi-shield-shaded` → `shield-check`
- `bi-sliders` → `sliders-horizontal`
- `bi-stars` → `sparkles`
- `bi-sun` → `sun`
- `bi-x-lg` → `x`

Semantic decisions: `contrast` preserves the half-circle theme-toggle meaning; `smartphone` describes Android installation; `shield-check` represents protected/admin controls; `database` represents Django administration where the vendor has no matching filled database/gear glyph. Theme system mode uses `monitor`. Brand images, social-network marks and emoji glyphs remain unchanged.

The checked JSON inventory records per-file counts for the 47 reviewed static/source placeholders; the dynamic banner branches and guest prompt are validated separately in the contract/browser tests.

## Screens, layout and lifecycle

Affected screens are pages inheriting the standard base shell: desktop sidebar and Campus Hub rail, tablet top navigation, mobile tabs/header, theme dropdown, profile fallback, notification controls and common shell prompts. Child applications with their own rails/nav retain their existing application-specific icons.

- New controls opt in with `.vasto-icon`; default 20px, compact/context/form/copy icons 16px, left-rail slots and mobile/tablet navigation 24px. The Campus Hub heading remains 20px. The guest feature symbol uses 24px within its existing 60px circle. Sizes are explicit SVG dimensions rather than Bootstrap font sizing; spacing and existing utility classes remain.
- CSS targets the opt-in marker, not `.lucide` globally or unrelated SVGs. Specificity keeps shared sizes stable even when a Spark child reloads its broad icon stylesheet later. Existing Spark icons retain their original 1em/1.75-stroke rules.
- Added `svg.vasto-icon` to the exact existing mobile icon transform/transition/filter selectors so active/focus/hover states still apply after replacement. No breakpoint, nav destination, Django condition, interaction ID or htmx attribute changed. A source comparison confirmed every existing URL tag and condition/loop tag sequence across all five templates was retained. The four partials' non-icon markup was otherwise identical apart from the two accessible names.
- The existing htmx full-page navigation controller manually replaces the left rail, context rail and navbar. Each existing replacement site now calls `VastoIcons.render` on that exact surface. Ordinary swaps continue through WP1's existing htmx listener.
- Install-copy success/restoration and dynamically inserted message/notification banners call the existing renderer at their mutation sites. The guest prompt calls `VastoIcons.setIcon`, preserving its ID/classes and its existing login/register destination logic.
- No MutationObserver was added. No second Lucide runtime, icon renderer, inline SVG injection system or CDN was introduced. Bootstrap Icons CSS/fonts remain installed and loaded.

Decorative icons explicitly carry `aria-hidden="true"`; visible text and existing icon-only button labels remain. The current shared header has a profile avatar/link, rather than a profile dropdown. Existing app-owned account-switcher/profile menu content is unchanged. The ordinary shared sidebar has no expansion/collapse button: the browser tests exercise its existing responsive hide/show behavior; app-specific collapsible rails were not redesigned.

## Automated verification

All commands ran from `/home/zayne/projects/pwaninet`. Browser tests used installed local Chrome with permitted sandbox escalation for browser sockets; no connected service, database or external asset fetch was required.

1. `venv/bin/python -u tests/lucide/navigation_browser.py`
   - **18 checks passed, 0 failed, 0 skipped; final clean exit 0.** Actual shared Django shell and form helper, htmx, Bootstrap and Lucide run in Chrome. Covers initial load, decorative accessibility, destinations/bypass hooks, no duplicate SVGs, 1440/900/390/320px geometry and alignment, responsive sidebar/tabs, active/inactive state, light/dark/system inheritance, theme menu, real full-document htmx navigation with all shared surfaces, full reload, dynamic insertion, Spark CSS load order, dynamic banners/dismiss, copy success/restore, staff/guest conditions and guest icon switching; no browser JavaScript errors in the exercised controllers.
   - Fixture deliberately excludes unrelated service/provider/media controllers. The first fixture iteration omitted the existing authentication bootstrap and was incorrectly treated as a guest by the real interceptor; retaining that production bootstrap fixed the fixture. No authentication code changed. One later screenshot-enabled run printed all 18 passes but returned signal status 143; the final foreground rerun above completed with exit 0.
2. WP1 browser fixture, run through Python Playwright with this command:

   ```bash
   venv/bin/python - <<'PY'
   from playwright.sync_api import sync_playwright
   from pathlib import Path
   with sync_playwright() as p:
       b = p.chromium.launch(executable_path='/opt/google/chrome/chrome', headless=True, args=['--no-sandbox', '--allow-file-access-from-files'])
       page = b.new_page()
       page.goto(Path('tests/lucide/browser.html').resolve().as_uri())
       page.wait_for_selector('#results')
       result = page.locator('#results').inner_text()
       print(result)
       assert result.count('PASS ') == 13 and 'FAIL ' not in result
       b.close()
   PY
   ```

   - **13 passed, 0 failed, 0 skipped; exit 0**, both before migration and after implementation. Real vendor/shared/Spark helper lifecycle, accessibility, scoped rendering, unknown names, missing vendor, htmx and observer settling remain green. Final log: `/tmp/wp2-wp1-browser-final.log`.
3. `node tests/lucide/registry.cjs`
   - **96 unique literal template icon names validated**, 0 unknown, against 2346 registry keys. Dynamic Django/JavaScript expressions are excluded by this linter and exercised separately.
4. `node tests/lucide/navigation_contract.cjs`
   - **47 reviewed placeholders and 12 dynamic guest mappings validated**. Also checks decorative accessibility, Bootstrap coexistence and all existing static JavaScript guest-prompt callers, including the reel `bi-play-circle-fill` caller. Exit 0.
5. `venv/bin/python manage.py test tests.lucide.test_infrastructure pwaninet.tests_static_storage --settings=pwaninet.settings.test --noinput`
   - **8 tests passed, 0 failed, 0 skipped**, final run 12.989s; system checks reported no issues. Includes real Django inheritance and safe temporary collectstatic output for the runtime, Spark assets and new shared stylesheet. No persistent database was used.
6. `DJANGO_SETTINGS_MODULE=tests.sals_wp4.ui_settings venv/bin/python tests/messaging/navigation_dom_browser.py`
   - **2 browser scenarios passed** (top and bottom mobile-nav positions), exit 0. One htmx request, no nested shell, stable per-frame header/nav geometry, matching direct/swapped DOM and retained native state. This existing fixture omits unrelated services and does not execute Lucide; the new suite above covers actual icon rendering. Log: `/tmp/wp2-existing-navigation.log`.
7. `node tests/offline/navigation.test.cjs`
   - **7 tests passed, 0 failed, 0 skipped**, exit 0. An earlier `node --test` wrapper reported one passing file; the direct command exposed all seven individual tests. Log: `/tmp/wp2-offline-navigation-direct.log`.
8. `node tests/spark/question-controls.cjs`, `node tests/spark/interaction-flow.cjs`, `node tests/spark/activity-explanations.cjs`, `node tests/spark/composer-interaction.cjs`
   - **4 scripts passed**, exit 0; their internal assertions do not expose one common test count. Spark controls, activity projection/privacy and composer interactions remain green.
9. Python compilation of the new browser runner, Node syntax check of the contract test, and scoped `git diff --check`: passed. Vendor checksum unchanged.

Local screenshots from the actual rendered shared shell were inspected at `/tmp/wp2-desktop.png` and `/tmp/wp2-mobile.png`. They show visible Lucide replacements and the real shared form helper's validation state; they are isolated fixture evidence, not screenshots of an authenticated production session. Generated screenshots and collected files were not committed.

The existing Spark settings result remains **23 passed / 2 failed / 5 errored / 0 skipped** from the previous PostgreSQL gate; it was not rerun or changed in WP2. Those two copy mismatches and five isolated-settings `app_version` errors remain separate. No full Django suite, provider-backed conversation, production CSP/cache behavior, Safari/Firefox or physical-device visual acceptance is claimed.

## Remaining Bootstrap surfaces and deployment

Bootstrap Icons remain in individual applications, including posts/reels, messaging, notifications, admin workspace, users/account switching and other app-specific templates/controllers. The base file still hosts app-owned sharing/repost/reel/media/download controls, dynamic like/bookmark/repost states, social brands, emoji glyphs and common legacy feedback states. These were deliberately not swept into WP2. Bootstrap close-button background images and unrelated SVG/brand assets are also untouched. No attempt was made to make every `bi-*` token disappear from the base script.

The `_form_field.html` helper is migrated and rendered in the browser fixture, but no current repository template include of it was found by the reference search. Existing app form widgets/Bootstrap validation backgrounds are not automatically converted by changing this helper.

The new stylesheet uses the existing `{% static %}` path and fingerprinting. The isolated collectstatic test confirms it is included; normal deployment must collect source assets and refresh the usual application/static caches. The generated, ignored `staticfiles/` working directory was not regenerated or committed. No production deployment was performed.

## Owner manual browser checklist

Use the normal application origin after deployment/collection, with the console and Network panel open:

- **Desktop sidebar (`/`, width ≥992px):** see outlined house/users/message/bell/settings/cloud icons; check active and inactive colors, hover/focus, badge placement and unchanged destinations. Verify the original Dashboard/Spark/Workspace logos and real avatar remain. Resize below/above the breakpoint to hide/show the rail; no missing SVGs afterward.
- **Top navbar (including 768–991px tablet width):** Home/Groups/Chats/Post/Notifications display Lucide; click their existing navigation paths and browser back/forward. Header and rail icons should survive htmx navigation with one shell only.
- **Mobile navigation (320–390px):** tabs stay aligned, labels and badges visible, active tab transform/color retained. Switch any existing top/bottom mobile-nav preference and navigate to `/groups/dashboard/`, `/notifications/` and the profile. Spark remains its existing brand image.
- **Profile/account menu:** verify the header avatar link and any existing account-switcher/profile dropdown still work. A missing profile photo now shows `user-round`; app-owned account menu icons remain Bootstrap in this package.
- **Notifications:** use header/tablet/rail links and a real incoming message/notification; common banner symbols and close buttons should render, dismiss and navigate normally. Badge counts should remain unchanged.
- **Theme switching:** open the existing theme dropdown; see contrast/sun/moon/monitor icons. Choose light, dark and system and repeat after navigation. Icons should inherit the foreground and retain sizing.
- **Shared context rail:** Campus Hub shows sparkles, file/users and right chevrons; follow materials/study-group links and navigate back. Both links and freshly replaced rail icons should work.
- **Form validation:** the shared helper shows a compact label chevron and warning triangle with unchanged error text/input attributes. As no live include was found, use `venv/bin/python tests/lucide/navigation_browser.py` or the local screenshots to review this helper; do not expect unrelated app forms to have changed automatically.
- **Common controls:** test back-to-top, 2FA nudge dismissal when eligible, install-modal copy/“Copied!”/restore, and guest sign-in prompts. Their existing behaviors and login destinations should remain.
- **Assets/console:** confirm the new `/static/css/vasto-navigation-icons.css` loads with the deployed cache fingerprint; one local Lucide runtime/shared module, no missing-name/runtime/CSP errors. Verify Spark's existing messages, interaction icons and activity trail retain their prior styling.

## Known issues and WP3 readiness

No known WP2 implementation failure remains in the executed focused tests. Owner authenticated browser/visual acceptance is outstanding, including real incoming notifications, user pictures, native preferences and deployment caches. The seven pre-existing Spark settings failures/errors remain explicitly out of scope and do not become WP2 fixes.

WP2 is ready for technical-lead review and owner acceptance. WP3 application migration requires separate explicit authorization. Work stops here; no WP3, backend/schema, packaging or dependency-removal work was started.
