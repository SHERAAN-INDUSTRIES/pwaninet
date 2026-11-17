# WP1 Codex verification and stabilization

Date: 2026-11-17. Scope: shared Lucide infrastructure only. No commit, deployment, database change, or WP2 migration performed.

## A. Hermes implementation audit

Evidence is the initial working tree, actual bundle/code, Git history and executed tests, not the previous agent's narrative. `WP1_IMPLEMENTATION_REPORT.md` was absent. `WP0_ICON_AUDIT.md` was present and untracked and was read.

At takeover, `static/js/vasto-icons.js` and `static/css/vasto-icons.css` were untracked; the shared inline loader was an unstaged addition to `templates/base.html`. These are the WP1 changes matching the supplied Hermes description. There is no Hermes-authored WP1 commit or report establishing authorship conclusively: attribution beyond these matching changes cannot honestly be proven. Recent relevant history is `e3a5ff5` (Spark Runtime) and `6d17e49` (Spark orchestrator), both attributed to the repository owner. The vendor and Spark helper already existed in committed history; neither is a newly introduced WP1 runtime.

Unrelated changes include admin workspace work, document generation, Spark chat/clarification work, font removals, and other assets. They were preserved. In particular, Codex did not edit `pwanimate-chat.js`, `chat_content.html`, backend services, models, or authentication.

Claim-by-claim findings:

- **Lucide v1.71.0 embedded license header: verified.** Header at the start of the actual file says `@license lucide v1.71.0 - ISC`. This verifies the artifact's self-identification, not independently authenticated upstream provenance.
- **Local 485 KB runtime: verified.** 485,198 bytes (about 474 KiB); no bundle replacement or upgrade.
- **Approximately 246-line shared module: broadly consistent with initial source.** Line count was not a quality guarantee; the repaired module is 221 lines.
- **Global `window.VastiIcons`: implementation defect, not merely a reporting typo.** Both module and inline loader used the wrong name. Worse, the API object referenced undefined `ready`, throwing before it could export anything.
- **Loading via base/extra_js: present but incomplete.** The inline loader lived inside an overridable block and waited until DOMContentLoaded. Reliability depended on inheritance and it added inline CSP requirements. Spark separately loaded the vendor.
- **Scoped rendering: partly true.** Vendor received the supplied root, but ignored an icon that was itself the root; `setIcon` attempted to render the old element's parent after removal (now null).
- **Idempotency: false for SVG identity.** The actual vendor retains `data-lucide` and replaces SVGs again on every scan. Counting SVGs alone concealed replacement and listener loss.
- **Unknown names safe: incomplete.** The original canonical validator accepted spellings the actual vendor could not resolve, and rendering bypassed that validator. Missing-registry validation failed open.
- **Spark compatibility: unverified and unsafe as originally claimed.** Direct vendor rescans replace SVGs, and Spark's observer sees those replacements. The new shared renderer prevents this feedback path.
- **No application icon migration: verified for the identified initial WP1 changes.** Codex additionally repaired two invalid existing Lucide placeholders, not a Bootstrap migration.
- **New collected JS missing: verified.** Existing ignored `staticfiles/` had vendor and Spark assets but no `js/vasto-icons.js` at takeover. The new file collects successfully in isolation.

## B. Defects discovered

1. `static/js/vasto-icons.js`: undefined `ready` prevented evaluation; wrong public namespace contradicted the intended contract.
2. Same file: raw vendor rescans replaced completed SVGs; a supplied placeholder root was excluded; `setIcon` lost its parent and returned an unrendered placeholder. Impact: missing/stale icons and lost direct listeners during repeat scans.
3. Same file: `htmx.onEvent` is not the local htmx public event API, so the hook was never installed. A page-global initialization guard also prevented useful subsequent rendering.
4. Same file: observer treated generated SVGs as new placeholders, allowing repeated mutations; disconnect unnecessarily depended on library availability. Invalid selector strings could throw.
5. `templates/base.html`: inline asynchronous loader was inside `extra_js`; inheritance could remove it. Spark had independent vendor tags in chat and sessions. All current active extra_js overrides found in messaging, documents and Spark call `block.super`, but this is not a guarantee for future overrides.
6. `pwanimate_context_rail.html`: existing `arrow-clockwise` and `arrows-fullscreen` literal names are absent from this Lucide registry. Correct names are `rotate-cw` and `maximize`.
7. `static/css/vasto-icons.css`: unreferenced WP1 draft stylesheet. Applying its global size/stroke defaults would alter Spark's appearance; it remains deliberately unlinked. Only the erroneous namespace in its comment was corrected.

## C. Changes made by Codex

- Repaired the existing shared module rather than replacing its architecture: canonical `VastoIcons`, valid readiness function, precise registry validation, root-inclusive rendering, completed-SVG exclusion, working `setIcon`, native htmx listener, repeated initialization, duplicate module guard, and explicit observer teardown independent of vendor readiness.
- Replaced only the WP1 inline loader with two ordinary local external script tags immediately before `extra_js`, outside overridable blocks. Removed the two redundant Spark vendor tags.
- Added one delegation line to `sparkRenderIcons`: when shared infrastructure is present, use its SVG-preserving renderer. Existing fallback, public Spark APIs, Bootstrap mapping, observer, requestAnimationFrame batching and htmx listener remain intact.
- Corrected the two invalid existing Spark context-rail names without changing controls or application behavior.
- Added focused browser, registry, Django inheritance and collection regressions under `tests/lucide/`.

## D. Final architecture and public contract

One runtime source: `static/vendor/lucide/lucide.min.js`.

- Self-identified version: **1.71.0**.
- SHA-256: **`9ad0d8d291f2108b78772861b706555193bed6d236118e149e2a3a0cc0f4211c`**.
- Size: **485198 bytes**.
- License: embedded header, plus `static/vendor/lucide/LICENSE` (ISC).
- API confirmed from actual code/execution: `lucide.createIcons`, `lucide.createElement`, `lucide.icons`; registry has 2346 keys including aliases.
- Runtime order: base body footer loads `{% static 'vendor/lucide/lucide.min.js' %}`, then `{% static 'js/vasto-icons.js' %}`, then child `extra_js`. Spark helpers/chat can call Lucide immediately. These small ordering changes avoid deferring a dependency past synchronous Spark consumers. The vendor download does pause the parser at the bottom of the body, after page markup; a broader defer conversion of application scripts was intentionally avoided.
- No new inline JavaScript, CDN, external fetch, second runtime, font removal, or global sizing stylesheet. Existing Django static URL/cache-fingerprint handling applies.
- All applicable pages inheriting the shared base receive the tags, including an extra_js override without `block.super`. Standalone error/offline/test pages and Django admin have separate rendering and are not implicitly migrated.

`window.VastoIcons` exports:

- `render(root)`: root may be a Document, Element, DocumentFragment or selector. Render known placeholders in the root, including the root itself; skip SVG nodes. Return existing SVG count within the supplied root (including it if it is an SVG); invalid roots or missing vendor return 0. Unknown names remain unchanged. Uses the pinned vendor's `createIcons` with a single-placeholder query scope so siblings and existing SVGs are never replaced.
- `setIcon(element, name)`: validate against the actual registry; replace an attached icon with the requested rendered SVG; preserve ordinary attributes, accessibility and utility classes; remove prior Bootstrap/Lucide icon classes and temporary swap markers. Return the rendered element, or false for invalid input/missing library. Direct listeners on an intentionally replaced element cannot survive; delegate them or attach to stable wrappers. Ordinary repeated `render` preserves direct listeners.
- `observe(root)`: explicitly install one MutationObserver per root for added placeholders; repeated calls return the same root. No second automatic document-wide observer. Returns root or null. Callers own scoped observer cleanup.
- `disconnect(root)`: remove that root's observer; return whether one existed, even if Lucide has since disappeared.
- `init()`: initial document render and one document-level `htmx:afterSwap` listener. Automatically called at DOMContentLoaded, or immediately when already ready. Repeated calls render new placeholders without duplicating the listener. Optional root argument is retained for compatibility; this does not constrain the page-wide htmx listener.
- `ready()`: additive retained convenience method, reports library availability.

No `VastiIcons` alias is retained; all relevant references use `VastoIcons`. htmx event listeners live for the document lifetime. Ordinary DOM insertion outside Spark requires `render(insertedRoot)` or explicit `observe(container)`; no competing global observer is introduced. `currentColor` and the vendor's decorative `aria-hidden` default are retained; explicit aria attributes, role, title, class, style and tabindex survive rendering. Modern browser APIs match the existing Spark baseline; this is not an IE compatibility layer.

## E. Tests and actual results

Commands run from the repository root:

1. `venv/bin/python manage.py test tests.lucide.test_infrastructure pwaninet.tests_static_storage --settings=pwaninet.settings.test --noinput`
   - Final result: **8 passed, 0 failed, 0 skipped** (2 new infrastructure tests and 6 existing static-storage tests). The collection test uses a temporary STATIC_ROOT, real configured finders and the local fingerprinted storage backend.
   - Inheritance regression renders the actual base with unrelated URL/include/custom-navigation tags removed to avoid authentication/database fixtures; original block boundaries are retained. It tests a real Django child override without `block.super`, single loading and vendor-before-module-before-child ordering. It is not an authenticated full-page test.
   - Earlier test-development runs failed on missing `app_version` fixture context; that fixture was repaired. One concurrent intermediate run copied the module before an edit and compared after the edit; rerun with stable sources passed. These are not hidden as passing runs.
2. `/opt/google/chrome/chrome --headless --no-sandbox --disable-gpu --disable-dev-shm-usage --user-data-dir=/tmp/wp1-chrome --allow-file-access-from-files --virtual-time-budget=3000 --dump-dom file:///home/zayne/projects/pwaninet/tests/lucide/browser.html`
   - **13 passed, 0 failed, 0 skipped**, in the fixture's `#results`: API, real vendor initialization, scope, repeated init/SVG identity/listeners, setIcon, unknown names/invalid selectors, missing vendor, htmx target itself, light/dark foreground inheritance, dynamic observation/disconnect, Spark mapping/setIcon, Spark SVG identity, Spark dynamic insertion and observer settling.
   - Uses the actual local vendor and helper files in real headless Chrome, no network assets. Chrome initially failed inside the sandbox on local socket restrictions; the permitted external run completed. Other browsers and authenticated product visuals remain untested.
3. `node tests/lucide/registry.cjs`
   - **80 literal template names passed**, 0 unknown after the two context-rail corrections; 2346 actual registry keys. Dynamic Django expressions are excluded and remain covered by lifecycle/interaction tests and owner checks, not asserted by this linter.
4. Existing Spark scripts: `node tests/spark/<name>.cjs`, for `question-controls`, `interaction-flow`, `activity-explanations`, `activity-privacy`, `stream-collapse`, `composer-interaction`, `followup-repair`, `teaching-flow`, `quiz-flow`, `unit-learning`, `study-context`, `lifecycle-resume`, `academic-preflight`, `memory-approval`, `memory-settings`, `native-reminders`, `markdown-math-unit`.
   - **17 scripts passed, 0 failed**. These scripts use their own assertions and do not expose a common individual-test count; 17 is a script count.
5. `venv/bin/python manage.py test pwaninet.tests_static_storage pwanimate.tests.test_settings_ui --settings=pwaninet.settings.test --noinput`
   - **Blocked during database creation**, before test execution: SQLite cannot apply PostgreSQL `gin_trgm_ops` migration SQL. 36 tests discovered (6 static + 30 settings UI), 0 executed in this attempt. The six static tests were subsequently executed successfully in the focused run. Thirty settings UI tests remain unexecuted; no schema or production settings workaround was introduced.
6. `node --check static/js/vasto-icons.js`, `node --check static/js/pwanimate/spark-icons.js`, and scoped `git diff --check`: passed.

No repository-wide frontend test suite is configured (`npm test` is a placeholder). An attempted disposable jsdom install failed with DNS `EAI_AGAIN`; no dependency or lockfile was changed, and real Chrome provided DOM evidence instead. No full Django suite, provider-backed Spark session, mobile/Safari/Firefox acceptance, or production CSP/network verification is claimed.

## F. Deployment and staticfiles

`STATICFILES_DIRS` includes source `static/`; `STATIC_ROOT` is generated `staticfiles/`. It is ignored in `.gitignore`, with zero tracked files. `deploy.sh` invokes collectstatic (exec with run fallback), and `docker-compose.prod.yml` runs it during startup. `scripts/setup-docker.sh` also invokes it. Do not commit generated output.

Isolated collection verified byte-for-byte output for:

- `js/vasto-icons.js`
- `vendor/lucide/lucide.min.js`
- `js/pwanimate/spark-icons.js`
- `js/pwanimate/pwanimate-chat.js`
- `css/pwanimate/pwanimate.css`

The working-tree `staticfiles/` was not regenerated; its missing new JS is expected until a build/deployment collects it. Normal deployment must collect the updated source, restart application workers as usual to refresh cached static fingerprints, and serve the resulting assets. The isolated collection uses the local `FingerprintedStaticFilesStorage`, matching the local branch of base.py's STORAGES routing. Production's legacy `STATICFILES_STORAGE` mentions compressed manifests, but effective STORAGES routing can select the fingerprinted local or optional S3 backend; do not infer hashed output from that legacy setting. S3 collection and live production headers were not exercised.

No CSP policy declaration was found in the inspected Django/deployment configuration. New icon loading requires only same-origin external scripts, consistent with a `script-src 'self'` policy, and adds no inline-script exception. Actual edge/proxy policy, static responses and cache invalidation must still be verified by the owner. No deployment configuration changed.

## G. Spark compatibility evidence

Spark keeps `sparkIconSpec`, `sparkSetIcon`, `sparkRenderIcons`, its complete Bootstrap mapping, semantic names, one lifecycle guard, its existing document observer, frame batching and htmx listener. Shared init adds no observer. Two htmx render entry points can receive a swap, but the second pass preserves completed SVG identity. Shared module reevaluation is guarded. Spark setIcon's existing swap-marker return contract is preserved by the renderer.

Real Chrome checks verify attribute preservation through Spark setIcon, mapping, new message-style insertion, stable SVG identities, and a settled observer. Existing tests exercise question controls, interaction/clarification flows, activity explanations/privacy, quiz/unit flows and stream handling. No clarification recovery source was altered. Full AI-backed conversation and visual/layout acceptance are still the owner's responsibility.

## H. Owner manual acceptance checklist

Use the running deployment's origin with these paths, authenticated as an ordinary user where applicable:

- `/` and `/pwanimate/`: hard reload with Network open. Exactly one local vendor execution and one shared module; both HTTP 200 before Spark scripts. `typeof VastoIcons.render === 'function'`; no `ready is not defined`, CSP, unknown-icon or missing-asset errors. No CDN Lucide request.
- `/pwanimate/settings/` and `/pwanimate/sessions/`: verify initial icons, settings modal/mobile page and session list after htmx navigation. The settings page previously had no separate vendor tag; icons should now render through the base loader.
- `/pwanimate/`: open the context rail menu; refresh and fullscreen icons should display. Exercise their existing controls.
- Start/open a conversation. Receive streamed messages, open an interactive question, submit/select answers, and open a clarification card. Confirm new icons appear, choices remain clickable and the separate clarification behavior matches its own workstream's baseline.
- Exercise the agent activity trail (brain, globe, book/search, wrench, shield/check), pause/resume study controls and dynamically inserted messages. No flicker, endless DOM mutation, rising idle CPU, or listener loss.
- Switch light/dark and test desktop plus narrow mobile layout. Confirm foreground inheritance, sizes, alignment, keyboard focus and accessible button labels. Compare Spark with its previous layout; the draft global sizing CSS is not loaded.
- Navigate through htmx updates and full reloads; inspect `document.querySelectorAll('script[src*="vendor/lucide/lucide.min.js"]').length` and the corresponding `js/vasto-icons.js` count: each 1 on applicable full pages.
- After deployment collection, request `/static/js/vasto-icons.js`, `/static/vendor/lucide/lucide.min.js` and `/static/js/pwanimate/spark-icons.js`. Confirm correct content, JavaScript MIME type, cache version and no stale copy. Check deployed CSP console/network output.

## I. WP2 readiness

**WP1 implementation is ready for technical-lead review and owner acceptance, with automated evidence above. Acceptance is not yet granted.** Remaining gates are the owner's authenticated browser/visual/CSP/static-deployment checklist and the 30 existing Django settings UI tests on a PostgreSQL-capable test configuration. These are explicit limitations, not claimed passes. No shared-runtime defect remains known from the executed focused tests. Do not begin WP2 without explicit authorization.

## J. Final verification gate — PostgreSQL settings UI suite (2026-11-17)

The technical lead provisionally accepted the WP1 implementation and requested execution of the previously blocked 30 settings UI tests. This section supersedes the earlier statement that those 30 tests were unexecuted. **All 30 now executed, but the suite did not pass: 23 passed, 2 assertion failures, 5 errors, 0 skipped.** WP2 remains unauthorized.

### Setup inspected and used

Inspected `tests/sals_wp0/run.py`, `settings.py`, `isolation.py`, and `probe.py`, the existing PostgreSQL wrapper scripts, the UI settings variant `tests/sals_wp4/ui_settings.py`, and the relevant migration extension declarations. Used the existing PostgreSQL runner unchanged, with its standalone `tests.sals_wp0.settings`; it does not import production/local settings or load `.env`.

The runner created container `sals-wp0-44ac180e881e4cc593034b67a01818c5` from the locally available `pgvector/pgvector:pg15` image (image ID `sha256:88164492558c9773a1e17a8305abaca7863e05b3d8bbcdb792774890a2047e06`). The database reported PostgreSQL 15.19. Storage was tmpfs, with a newly assigned loopback port (`127.0.0.1:32768`), fresh credentials and a dedicated `sals_wp0` role. Django created and migrated `test_sals_wp0`; no migration disabling, SQLite index stripping, shared database, development data, production credentials or persistent volume was used.

`vector` was installed in the disposable template database by the runner and inherited by the test database. Repository migrations installed `pg_trgm` before the trigram index. Read-only checks inside the test database confirmed `pg_trgm`, `plpgsql` and `vector`. The runner's independent-connection row-lock/commit/rollback probe passed, and migration conflict detection returned `{}`. The complete migration setup finished successfully before test execution; the previous SQLite/PostgreSQL incompatibility is resolved for this run.

### Exact execution command and outcome

From `/home/zayne/projects/pwaninet`:

```bash
venv/bin/python -m tests.sals_wp0.run pwanimate.tests.test_settings_ui > /tmp/wp1-postgres-settings-ui.log 2>&1
```

The sandbox denied access to the Docker socket during the initial read-only availability check. The test command was subsequently run with permitted sandbox escalation, using the existing runner's isolation guards. No automatic approval rejection occurred.

Completed Django output:

```text
Ran 30 tests in 18.878s
FAILED (failures=2, errors=5)
Destroying test database for alias 'default'...
PASS independent connections, conflicting row lock, commit visibility, rollback
Migration conflicts: {}
Found 30 test(s).
```

The 18.878 seconds is test execution time, excluding the substantially longer full database migration setup. Command exit status was **1**: the runner raises `RuntimeError('Isolated tests failed')` after a nonzero test result. No test result is still pending.

### Failures and environment limitations

Two assertion failures are pre-existing copy mismatches, confirmed against the committed HEAD templates as well as the working tree:

- `PwanimatePersonalizationFormTests.test_successful_form_submission_persists_preferences`: expects `Your Pwanimate preferences have been updated.`; actual success response contains `Your Spark preferences have been updated.`
- `PwanimateSettingsHTMXNegotiationTests.test_desktop_modal_about_swap`: expects `What is Pwanimate?`; actual response contains `What is Spark?`.

Five errors share `django.template.base.VariableDoesNotExist` for `app_version` during full-base template rendering:

- `PwanimateSettingsHTMXNegotiationTests.test_full_page_load_without_htmx`
- `PwanimateSettingsNavigationAndHeaderTests.test_left_rail_contains_single_settings_link_and_no_Vasto_settings`
- `PwanimateSettingsNavigationAndHeaderTests.test_mobile_menu_contains_single_settings_link_and_no_Vasto_settings`
- `PwanimateSettingsNavigationAndHeaderTests.test_modal_header_is_clean_settings`
- `PwanimateVersionAndSettingsRenderingTests.test_index_view_context_includes_pwanimate_version`

The standalone PostgreSQL settings omit `pwaninet.context_processors.release_metadata`, which supplies `app_version`. The existing base template evaluates `static_fingerprint|default:app_version`; that expression also exists in committed HEAD before WP1. The existing SQLite UI settings variant explicitly adds this context processor, confirming that the minimal PostgreSQL settings lack the full-page UI context configuration. These five tests therefore executed but could not complete their assertions in this configuration. No production template, test expectation or settings file was changed to hide these results. A follow-up PostgreSQL UI configuration with the normal release metadata context processor is needed to resolve these five environment errors; the two copy assertions also need separate review. This verification run does not establish a green settings UI suite or attribute these failures to the shared Lucide code.

Django also emitted `axes.W002` and `axes.W003` because the isolated settings omit Axes middleware/backend configuration. They were warnings, not skipped tests or migration blockers. Test setup emitted `No user_following table found - data cannot be recovered`; it did not stop migration or test execution.

### Cleanup, changes and gate status

The runner destroyed `test_sals_wp0` and removed its temporary container in its `finally` cleanup. Verified removal with:

```bash
docker ps -a --filter name=sals-wp0-44ac180e881e4cc593034b67a01818c5 --format '{{.Names}} {{.Status}}'
```

The command returned exit status 0 with no matching container. Temporary manifest/database storage was managed and removed by the runner. The local test log remains at `/tmp/wp1-postgres-settings-ui.log` for this session; the substantive result is preserved here.

Only this report was appended during the final verification gate. Application code, tests, migrations, production configuration and unrelated working-tree changes were not edited. No commit or deployment was made. **The requested PostgreSQL execution is complete; the settings UI verification gate is not green (23 pass / 2 fail / 5 error / 0 skip).** Owner browser acceptance and the lead's decision on these existing test/configuration issues remain outstanding. Work stops at WP1.
