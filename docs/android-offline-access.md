# Vasto Android offline access — implementation and verification

Work performed in the Django monolith `/home/zayne/projects/pwaninet`, including its Pwanimate module. Vasto Workspaces and the separate frontend/backend projects were not changed. Existing unrelated working-tree edits were preserved.

## Diagnosis and release trace

The source Capacitor configuration, generated `android/app/src/main/assets/capacitor.config.json`, and configuration extracted from the existing debug APK all selected `server.url = https://pwaninet.app`. `www/index.html` immediately redirected to that host. Thus a fresh cold start could not render the application before downloading hosted HTML. The existing APK inspected was `android/app/build/outputs/apk/debug/app-debug.apk`; no existing release APK was present.

The hosted service worker cached HTML in an account-independent namespace, bypassed HTMX requests, and sent failed page navigation to `offline.html` even when a matching page existed. Native main-frame and legacy error callbacks could also replace the page with a bundled generic offline blocker. Network availability callbacks redirected that blocker to the root rather than preserving the requested route. Android used `LOAD_CACHE_ELSE_NETWORK`, without an explicit private-content storage policy.

The existing messaging IndexedDB database was shared between accounts. Several save methods returned an IDB request or nonexistent `transaction.complete` instead of waiting for commit; early batches were silently ignored when initialization was unfinished. History loading favored the newest server-rendered 40 messages and did not restore older retained history in that path. The existing virtual media stream used a shared download database and incomplete range validation.

Installed packages: Capacitor Android/Core 8.5.0, CLI 7.6.8, Filesystem 8.1.3, App 8.1.1, Camera 8.2.3, Push 8.1.2, Share 8.0.1, Status Bar 8.0.3, Social Login 8.5.18. No new Capacitor plugin was installed. The project already specifies AndroidX WebKit 1.12.1; the app now declares its direct dependency because its code uses that API. Existing build setup is Gradle 9.7.0, AGP 9.3.1, Java from `/opt/android-studio/jbr`, compile SDK 36, target SDK 35, min SDK 24.

There is no connected device (`adb devices -l` returned an empty list), so the installed-app online/offline reproduction and physical device details remain pending. Source/APK evidence establishes the remote-start configuration and blocker selection; it does not substitute for that reproduction.

## Implementation and ownership

- Startup uses bundled `www/index.html` and `www/launch.js`. Its navigation, styling and sign-in explanation require no network, fonts or icons. The launcher distinguishes first sign-in, returning-user and confirmed signed-out hints. These hints contain no saved private content and do not authorize access.
- The hosted application remains `https://pwaninet.app`; its cookies, service worker, private caches and IndexedDB remain on that origin. The local shell navigates there; it does not attempt to read hosted-origin storage. A returning launch requests the saved route after painting local navigation.
- Only `pwaninet.app` and the bundled `localhost` origin remain internal. Hosted requests bypass Capacitor's native HTML proxy so Chromium owns cookies, service-worker interception and response policies. Native plugin bootstrapping is explicitly injected for the exact previous production origin using AndroidX document-start support. No CORS, CSRF or session policy was relaxed. Native Google sign-in and other plugin flows still need device regression testing, including older supported WebViews without document-start support.
- Native asset errors no longer invoke the legacy whole-page fallback. Genuine main-frame failures open the local navigation interface with the failed route preserved. Network callbacks do not redirect students away from saved content.
- `pwaninet/offline.py` defines the read-route policy and authenticated access-revalidation endpoints. The middleware supplies authoritative account and rendered-export headers. Explicit upstream `no-store`, errors, sign-in pages, settings and write routes are excluded. Eligible HTML uses private revalidation policy; original token-bearing HTML is never saved as a route copy.
- `offline-capture.js` exports the rendered DOM after pagination and message updates. It removes scripts, forms, CSRF/session attributes, editable drafts, frames, event handlers and signed URL references. The saved response permits only a small read-only runtime through CSP. Post IDs deduplicate loaded feed cards; older feed pages are retained after successful refresh only when the server confirms current visibility. A failed refresh leaves the last good copy.
- `offline-pages-worker.js` partitions rendered routes by server-confirmed account and exact normalized query scope. A serialized generation counter prevents late requests or exports from restoring a previous account after logout, switching or revocation. Stored content is cleared on account changes. Access errors and explicit destructive changes invalidate managed copies; reconnection rechecks stored routes and post/download access. Revalidation of a background conversation does not mark it read.

## Offline route inventory

Every eligible route requires an authorized successful online visit and a completed snapshot. Normal Django navigation, query variants and previously loaded HTMX content are retained within that route's scope. An unvisited route opens a Vasto navigation state explaining the initial-visit requirement.

- `/`, `/unit/<id>/`: loaded feed/post cards, ordered and deduplicated, including additional scroll pages. New feed results and server filters need a connection.
- `/post/<uuid>/`: visited post details and successfully embedded images. Reactions, comments, uploads and edits remain online.
- `/users/user/<username>/`: visited profile content and loaded post pages. Profile editing and account settings are excluded.
- `/groups/dashboard/`, `/groups/<id>/`, `/groups/<id>/announcements/`: visited group content and messages actually present in that page's rendered view. Membership, role and management actions remain online. Group/direct message records have separate namespaces.
- `/documents/`, `/documents/document/<uuid>/`, and the listed `/documents/library/` subsections: visited listing and detail views. Embedded PDF/document viewers, private attachments and unvisited file bytes are not automatically made available by a page snapshot.
- `/pwanimate/`, `/pwanimate/<conversation-uuid>/`, `/pwanimate/history/`: completed rendered conversation/history content. In-progress AI output is excluded. New prompts, AI generation, resource fetching, resume operations and settings require a connection.
- `/messaging/`, `/messaging/conversation/<id>/`: visited conversation lists and rendered history, including older messages loaded by scrolling. Existing raw-message storage is now account-scoped, transaction-safe and separated from group IDs. Direct history rechecks membership and current message data before restoring older retained records through the existing normalization/decryption pipeline. Optimistic unsent messages are not presented as committed saved messages. The saved view automatically refreshes on reconnect and resumes existing live-message synchronization; an outage keeps it visible. Sending remains online. No new offline-send queue is introduced.
- `/notifications/`, including visited filter/query variants: previously loaded notifications and pagination. Retained rows are refreshed with the existing authenticated notification renderer, excluding archived/expired/deleted records. Reconnect automatically opens the current online view; a retry handles reachability returning without a new network event. Read/delete/follow actions require a connection.
- `/offline-media/`: visited rendered view only. The dedicated new Save for offline action supplies the verified in-page video flow; this does not claim that all older download-manager UI behaviors work inside a script-free saved page.

Messaging and notification caching were explicitly requested during implementation. Existing encrypted transport and account credentials are unchanged. Authorized rendered/decrypted text is stored privately in the WebView's origin storage; this does not introduce a separately encrypted-at-rest database or export encryption keys. Unvisited conversations, unsaved audio/documents, external embeds and fresh searches are not claimed available offline.

## Storage, limits and video

Cache Storage holds at most 120 rendered routes and approximately 60 MiB of rendered content per active account, with 30-day age expiry. Each snapshot is capped at three million characters and embeds at most roughly 1.2 million characters of successfully fetched image data. Shared caching is restricted to public static assets, bounded to 300 entries. Explicit `no-store` responses are not copied.

The existing message IndexedDB schema is reused in account and direct/group scoped databases. It retains up to 1,000 messages per conversation, 10,000 messages and approximately 30 MiB per namespace, for 30 days since successful retention/refresh. Deleted-message bodies are scrubbed. The old unowned chat/download databases are not exposed or assigned to an arbitrary first account after upgrade. Legacy native download files are not imported into the new dedicated offline-video store.

SQLite was not added: the existing WebView service worker and IndexedDB paths passed desktop Chromium persistence/transaction checks, and avoiding another native plugin limits migration/build impact. Android force-stop durability is still unverified; SQLite should be reconsidered if those device tests fail.

Eligible video controls offer deliberate Save for offline, progress, failure, completion and removal. The existing signed download endpoint rechecks author permission and visibility and provides the source MP4 where present. Only `video/mp4` is accepted. The entire stream and declared length are verified before one IndexedDB transaction publishes bytes and metadata together. Interrupted downloads never publish a ready item. Limits are 150 MiB per video and 400 MiB total; quota errors remain failures. URLs and tokens are not durable media references. Saved video bytes remain until removal, clearing or access reconciliation.

The offline player uses an account-checked same-origin virtual stream, with complete, open-ended and suffix range support plus 416 for invalid ranges. It makes no remote request to start a ready video. Unsaved video shows its saved poster, when available, and an internet requirement. HLS playlists are never advertised as complete offline media; HLS-only/non-MP4 content remains online. Desktop Chromium playback and seeking passed; Android WebView playback after force-stop is pending. Large selected videos currently occupy IndexedDB blobs rather than native private files, so low-memory/storage behavior on supported Android devices must be verified.

## Verification and remaining release gates

Passed locally:

- `npx cap sync android`.
- `.venv/bin/python manage.py check`.
- `.venv/bin/python -m unittest discover -s tests/offline -v`: nine tests covering route/header policy, exclusion of no-store/errors/writes, current-user post filtering and private-history membership boundaries.
- `.venv/bin/python manage.py test pwaninet.tests_static_storage --settings=pwaninet.settings.test --noinput`: six existing tests.
- `.venv/bin/python tests/offline/browser_checks.py`: 21 checks passed with no page errors. Results are saved in `docs/verification/android-offline-browser.json`. Uses real desktop Chromium with the actual service worker/capture/runtime and a local authenticated-response fixture. Covers feed order/deduplication, saved content after failed refresh, authorized previous-page retention, Pwanimate, conversations and older loaded messages, message commits and group/direct isolation, notifications and pagination, automatic reconnect refresh, logout/account isolation, completed/interrupted MP4 saves, offline playback/seeking and byte ranges. These are fixture integration tests, not production-session or installed-APK tests. The browser suite requires Python Playwright, `/opt/google/chrome/chrome` and FFmpeg; it generates its small test MP4 when absent.
- JavaScript syntax checks and `git diff --check`.
- `JAVA_HOME=/opt/android-studio/jbr ./android/gradlew -p android :app:compileReleaseJavaWithJavac --offline --console=plain`: final Android source compilation passed.
- The actual bundled launch HTML was opened in desktop Chromium with network disabled and its navigation/sign-in state asserted. Screenshot: `docs/verification/vasto-local-launch-offline.png`.

Not passed / pending:

- Full `assembleRelease`: offline attempt lacked cached dependencies. The normal build reached compilation but failed during dependency resolution for lint (`androidx.core:core-ktx:1.15.0` from `dl.google.com`, temporary DNS failure). No newly built release APK is claimed. Do not distribute the old debug APK as this fix.
- Existing Pwanimate UI suite plus static tests in the SQLite test configuration: database setup failed at the repository's PostgreSQL `gin_trgm_ops` migration before the Pwanimate tests could run. The six static tests passed separately without database creation.
- Native sign-in/plugins, production Django pagination/write regressions, actual account switching/revocation with two accounts, physical-device storage pressure, Android back/deep links, force-stop durability and installed WebView media seeking have not been executed. There is no connected device or recorded Android version.

## Exact device reproduction checklist

After resolving build dependency access, deploy the Django/static changes to the test host together with a newly built APK; an APK alone does not update hosted service-worker code.

1. Run `npx cap sync android`, then the repository's `JAVA_HOME=/opt/android-studio/jbr ./android/gradlew -p android assembleRelease --console=plain` with the normal release signing environment. Inspect the APK's `assets/capacitor.config.json`, `assets/public/index.html`, and `assets/public/launch.js`: no remote `server.url` or immediate launch redirect should exist.
2. Connect a test device; record `adb shell getprop ro.product.model`, `adb shell getprop ro.build.version.release`, WebView version, APK version and build/hash. Install using the project's test/release signing policy. Use a dedicated test account/device before clearing app data.
3. With a fresh app data state and airplane mode, launch Vasto. Local navigation and the first-sign-in explanation must appear. Check Messages, Notifications and Pwanimate entry points remain in the interface.
4. Go online, sign in, load multiple feed pages, a post, a profile, group/announcements, document views, Pwanimate history, several conversations with older scrolled messages, and notification pages/filters. Wait for snapshot completion. Save a permitted MP4 and wait for its completed state.
5. Run `adb shell am force-stop com.vasto.app`, enable airplane mode, then `adb shell monkey -p com.vasto.app -c android.intent.category.LAUNCHER 1`. Reopen each loaded route, use Android back and supported deep links, and open one unvisited route. Assert the original messages/notification pages remain readable and the missing route explains the online visit requirement.
6. Play and seek the saved video without connectivity. An unsaved video must explain its connection requirement. Interrupt another transfer, force-stop and reopen; it must never appear ready. Exercise storage-full errors and removal.
7. Restore connectivity. Verify new feed posts, new messages and notifications arrive, history remains ordered/deduplicated, edits/deletions apply and a simulated server 503 does not clear the last good view. Revoke membership/access from another test account, then reconnect and verify managed copies are invalidated.
8. Sign out or switch to a second test account, including while a slow request is in flight. Neither pages, structured messages nor saved videos from the first account may reopen. Check the existing account system, Google/native sign-in, online posts/comments/reactions/uploads and normal chat sending still work.

## Changed implementation files

`capacitor.config.json`; generated Android config/public launch assets; `android/app/build.gradle`; `MainActivity.java`; `www/index.html`; `www/launch.js`; `pwaninet/offline.py`; cache-header middleware; URL configuration; `messaging/views.py`; `templates/base.html`; `static/service-worker.js`; `static/js/offline-pages-worker.js`; `offline-capture.js`; `offline-view.js`; messaging `offline-cache.js`; chat `message-service.js`; downloads `download_storage.js`; `tests/offline/`; this report.
