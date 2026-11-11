# Offline pages: original snapshots with native storage

The replacement bundled reader was removed at the user's request. There are no alternative Home, conversation-list, chat or video screen designs. The same Django-rendered DOM is used online and offline.

## Layout and behavior

The exporter preserves forms, inputs, textareas, buttons, wrappers, classes, layout data attributes, Bootstrap attributes, theme and mobile-navigation placement. This fixes the missing search form and the unstyled messaging composer: the old exporter removed the form that supplied the composer's styling and positioning. CSRF/password/draft values, executable page scripts and event handlers are removed without removing the layout elements. Supported safe theme/navigation handlers become inert data attributes interpreted by the offline runtime.

Stylesheets are serialized from the loaded CSS rules into the existing link elements. Font URLs, CSS backgrounds, image sources, posters and available media are rewritten to local resources. Successfully saved images are no longer restricted by the former 1.2 MB/page image budget or removed merely for crossing that budget. Browser resources are account-scoped; Android resources go directly into app-private, no-backup phone files. Approved CDN origins come from the authenticated server configuration. Cookies are forwarded only to the first-party host; signed URLs are not retained in the saved HTML.

Only the existing Bootstrap UI library and the offline runtime run in a saved page. The full website's initialization, API polling, sending and write handlers are not replayed. Dropdowns, theme changes, page navigation, original form/input appearance, textarea sizing, native media controls, scrolling, scroll restoration and navigation hiding/showing work locally. Sending and other server mutations still need a connection. The composer remains in its original form and position; this change does not introduce an offline-send queue.

The only offline notice is **You are offline viewing cached pages**. It has no timestamp. It floats above bottom navigation/composer surfaces so it does not move or cover controls. No full-page “you are offline” blocker replaces saved content. An unavailable route resumes the last available saved page; with no saved content at all, only the small notice can be shown until a first online visit.

## Native SQLite and phone files

`OfflineStorePlugin` stores original snapshot HTML in SQLite, with application-level AES-GCM payload encryption backed by Android Keystore. Numeric/index metadata remains visible; this is not SQLCipher database-file encryption. Snapshots have an 80 MiB encrypted-payload budget and a 100-page limit. The last successfully stored route is restored on startup. Scroll positions are retained independently when browsing a native saved page.

Images, fonts, posters, audio and videos are app-private phone files, separate from WebView IndexedDB. Writes use partial files and completion checks before resources are published. Asset budgets are 8 MiB per image/font/style, 30 MiB per audio/video, and 300 MiB overall; old resources can be evicted under the budget. Media caching runs after the initial page snapshot, with up to five video/audio dependencies scheduled per visited route, so downloading a reel cannot hold the whole page hostage. Only completed resources are wired into a saved player. Unsupported, oversized or unavailable media is not claimed saved. Encrypted or streaming-only media still requires its existing supported download path.

The Android file handler supplies correct media MIME types, range support and the exact first-party CORS origin for fonts. Original URL navigation and service-worker fallback can reopen native snapshots using `offline_saved=1`; normal online requests and Google sign-in retain their existing handling. The local startup document only resumes a saved/hosted page and supplies no replacement page layout.

Account binding is server-confirmed, with generation checks to prevent late operations from restoring a signed-out account. Explicit logout/account changes clear native records and files. Existing service-worker route/post/message revalidation remains active for its copies. Native media storage is app-private but its media files are not separately encrypted. Fresh production access/revocation scenarios still require end-to-end release verification; this change does not make a remote revocation enforceable while a device has no connection.

## Build and deployment

The Android build automatically copies **only runtime dependencies** from the existing website with `scripts/bundle-offline-assets.py`; it does not generate redesigned pages. `npm run native:copy` also performs this preparation before Capacitor copy. Deploy the Django native-state contract and hosted capture/resource scripts together with the updated APK. Existing browser snapshots remain usable while the native cache is populated through successful online visits.

The separate `offlineCheck` test application does not replace the installed Vasto application or its data. It has no Internet permission, allowing real native snapshot/file tests without disconnecting the user's phone. Google/Firebase configuration is disabled only for that isolated test variant; production Google sign-in code is unchanged.

## Verification scope

- Original Django top-navigation and conversation components are rendered for browser geometry comparisons, using the real stylesheets. Tests compare online versus saved search/header/profile/theme/bottom-navigation positions and composer form/textarea/button geometry.
- Browser checks exercise offline image bytes, theme controls, scroll navigation effects, scroll restoration and the exact date-free notice.
- Existing service-worker tests cover retained history, pagination, reconnect, account isolation, video playback/seeking and interrupted downloads.
- Device instrumentation verifies SQLite encryption/reopen, transaction rollback, account cleanup, hidden-message retention and original-page rendering without Internet permission. The native media test also checks local-file video loading/playback/seeking inside a saved page.
- A debug APK build and Django system checks are required before handoff.

These controlled tests do not replace a production-session rollout test covering real CDN credentials, all supported media formats, storage pressure, account switching and revoked content. Production deployment and installation over the user's main app are not performed by these checks.

Final controlled verification: 7 original-layout browser checks, 22 snapshot regression checks, 15 offline contract/policy tests, 2 debug-build helper tests, and 6 Android device tests passed. The device test loaded, played and sought a native-file video inside a SQLite-backed snapshot without Internet permission. The debug APK build and Django checks passed. Results: [verification JSON](verification/native-offline-snapshots.json).

### Localhost testing through USB

The debug build uses `http://localhost:8000` through `adb reverse` and a separate Capacitor file origin, `https://vasto-debug`. Native authentication, resource caching, snapshot navigation, MIME handling and CORS now use those configured origins. Only a debuggable build may trust its exact configured development server (including scheme and port); release builds keep the production origin restriction. Cached media files remain in the phone’s app-private directory, while SQLite stores their metadata and saved page records. The laptop supplies the initial download.

Voice messages whose audio URL exists only in `data-src` are included in deferred media caching. The saved page uses the existing voice/track play buttons with local media rather than replaying the live chat scripts. Rebuild the Android APK after native changes and revisit pages online to replace older incomplete snapshots.

### Online navigation takes precedence

Startup no longer requests saved snapshots while connected. The Android request
interceptor serves snapshot HTML and bundled offline scripts only without a
network connection, and network errors do not activate offline mode while a
connection is present. Saved-page navigation drops offline URL flags when online.
The worker waits for live navigation rather than substituting a snapshot after
1.5 seconds; online HTTP errors remain HTTP errors. Actual offline navigation
still opens account-scoped saved pages. Returning online restores the live page.
These native changes require the rebuilt APK; hosted script changes alone cannot
replace the installed app's request interceptor.

The extra browser-only “Save for offline” / “Remove offline video” button is no
longer injected below media cards. Media cards retain their existing download
entry, and page snapshot capture continues independently of that removed UI.
