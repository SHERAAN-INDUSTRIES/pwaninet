# Voice calling

This module owns call signaling and the web calling interface. Server-side media
authorization is isolated in `media.py`. The authenticated application shell mounts
the calling UI once, outside HTMX page content. This remains a development feature, not a completed calling release.

## Local workflow

1. Install `requirements.txt` in the project virtual environment.
2. Run `python manage.py migrate calling` against the intended development database.
3. Set `LIVEKIT_URL`, `LIVEKIT_API_KEY`, and `LIVEKIT_API_SECRET` on the backend.
   A remote LiveKit Cloud development project avoids a local media server.
4. Set `CALLING_ENABLED=true` only for controlled development. Default is false.
5. Run `python manage.py expire_calls` regularly to persist missed invitations.
   Requests also check invitation deadlines before accepting or issuing tokens.
6. Run `python manage.py test calling --settings=calling.test_settings --noinput`.
   This creates/drops `test_pwaninet_calling` on local PostgreSQL; it does not migrate
   the application's database. The generic SQLite test settings cannot apply the
   project's existing PostgreSQL search indexes. Do not run concurrent copies of
   this suite against the same dedicated database.

## API contract

Use the existing session authentication (including CSRF on mutations) or DRF API
tokens. All routes are under `/api/calling/`.

- `POST /` with `{"conversation_id": 123}` creates a direct-call invitation.
  Returns 201 and the call snapshot. Caller/recipient IDs come from authenticated
  conversation membership, never from a supplied recipient list.
- `GET /` returns `{"calls": [...]}` for current open calls on app launch/reconnect.
- `GET /<call UUID>/` returns the authoritative snapshot and checks expiry.
- `POST /<call UUID>/` with `{"action": "..."}` transitions state. Supported actions:
  `ringing`, `accept`, `decline`, `cancel`, `leave`.
- `POST /<call UUID>/connection/` returns `server_url` and `participant_token` only
  for an accepted participant on the answering authenticated device session.

Snapshots contain `id`, `conversation_id`, `caller_id`, `status`, `revision`,
`created_at`, `expires_at`, `accepted_at`, `ended_at`, and `participants`.
Participants expose opaque `id`, `user_id`, and their own `status`. Tokens and
answering-session hashes are never part of snapshots or notification events.

Call states: inviting, active, ended, cancelled, declined, missed. `active` means
accepted; it does not claim that media is connected. The UI must use LiveKit SDK
connection/track events to represent actual audio connectivity. Only a recipient
device that has presented its incoming-call UI should acknowledge `ringing`.
The caller displays Calling until that acknowledgement. Expired/cancelled calls
stay terminal even if a late Answer action arrives.

The existing authenticated notification socket receives `call_update` with a
`call` snapshot. Events are private participant-group hints, published after
commit. Fetch HTTP state after reconnect and ignore lower snapshot revisions.
Events are best effort; there is no durable push outbox yet.

A second authenticated session cannot claim or leave a call answered elsewhere.
A shared API token represents one device session: native clients must provision
separate credentials per device for this protection to distinguish them.

## Boundaries established by the audit

- Django/DRF provides session and API-token authentication. Messaging has direct
  and group conversations plus explicit ConversationMember records.
- Group access additionally checks current approved group membership through
  `groups.services.chat_service`. Group call policy is not implemented here.
- User blocks exist and are checked in both directions at start and subsequent
  access. Stable user-row locks serialize overlapping call starts on PostgreSQL.
- Channels already has per-user notification groups; this module uses that channel
  without changing messaging or notification payload semantics.
- PushSubscription supports web push and native FCM. Its uniqueness is scoped to
  users, so a physical endpoint/token can be registered to multiple accounts.
  Account switching and exclusive device-token ownership need review before call
  pushes are enabled; ordinary subscription filtering alone is insufficient.
- Android has a custom FirebaseMessagingService for general rich notifications.
  It currently suppresses its system banner in the foreground and has no native
  call Answer/Decline handlers or call foreground service. Microphone permission
  is already declared. No iOS project is present in this checkout.
- No previous LiveKit integration was found. Existing voice modules concern voice
  messages/recording, not real-time calls, and have been left alone.

## Required before production or claiming end-to-end calling

1. Durable invitation/cancellation delivery with TTL, retries and account-bound
   device registration. No call pushes are sent by this foundation.
2. Android call notification/Telecom integration and native action handlers;
   background audio lifecycle. iOS requires a native project plus PushKit/CallKit.
3. LiveKit server-side room deletion/removal with durable retries on end/block,
   authenticated webhook reconciliation, and cleanup for abandoned active calls.
   Ending signaling currently does NOT disconnect already connected media.
4. Token expiry alone does not terminate existing audio. Self-hosted LiveKit does
   not revoke an already-issued join token on participant removal; old tokens can
   rejoin until expiry. Use explicit room lifecycle policy to address that window.
5. Invitation idempotency keys, call-specific abuse/rate limits, a defined busy
   policy for stale active sessions, and testing of simultaneous actions on PostgreSQL.
6. Real-device verification of the implemented frontend and audio lifecycle with a
   locked screen, background/closed app, Wi-Fi/mobile data, offline recipient,
   cancellation, two recipient devices, permission denial, and network recovery.
7. Group invitation/leave/host policy. The participant model can be reused but the
   current API deliberately rejects group starts. Workspaces integration will need
   its own authorization adapter and shared packaging; reuse is not automatic.

No live database migration, deployment, push delivery or real call was performed
as part of this setup. Do not enable this module in production yet.

References: https://docs.livekit.io/frontends/reference/tokens-grants/
and https://docs.livekit.io/transport/self-hosting/.


## Application UI

The direct-message header opens the real outgoing call flow. Group headers have
no calling action. `templates/calling/shell.html` provides the full-screen call,
incoming banner and minimized pill. It uses existing Bootstrap icons, buttons,
PwaniNet theme tokens and native safe-area variables. Theme changes apply live.

`static/js/calling/session.mjs` owns signaling, deadlines, device/session conflicts,
permission states, media readiness and the call clock. Presentation is independent
of lifecycle. `controller.mjs` adapts the existing authenticated notification socket
and REST API and owns focus, navigation, reconciliation and cross-tab ownership.
`media.mjs` alone owns LiveKit rooms, microphone tracks and output selection.

Calls persist through supported HTMX navigation between messaging and the normal
app shell. PwaniMate, authentication boundaries, external links and full browser
reloads retain their normal hard navigation. The same tab stores minimal UI recovery
hints and reconnects after verifying the call with the backend. This preserves the
original clock, mute/output settings and presentation but has a brief audio gap.
Closed apps/sites do not keep browser media running; native closed-app ringing is
a separate, deferred feature.

Minimizing does not reconnect media. Ending from the pill stays on the current
page with a brief result; ending full-screen shows the outcome and final duration.
A real remote audio-track subscription plus a published local microphone starts
the timer; invitation/room acceptance alone does not. Reconnection retains that
clock, and stalled media setup/recovery fails after 45 seconds. No timers invent
ringing or connectivity. There is no production mock adapter.

Mobile full-screen calls and the minimized pill share a loudspeaker action. Android
`CallAudioPlugin` routes through AudioManager and restores the prior mode on end.
It requires an updated Android build. Browsers use an exposed speaker device or
the browser output picker; unsupported browsers explain device-controlled routing
and do not pretend a loudspeaker route was activated. Desktop intentionally omits
the speaker control. Microphone
permissions never claim an unsupported Open settings action.

The local LiveKit client bundle is copied from the npm lockfile installation:
`npm ci --ignore-scripts` then `npm run vendor:livekit`. It is lazy loaded only on
an actual call attempt. Do not switch to an unversioned remote CDN script.

## Verification

- `npm run test:calling`: focused lifecycle and race tests using injected adapters.
- `python tests/calling/browser_checks.py`: actual Django templates, Chromium,
  phone/tablet/desktop dimensions, 150% text, theme changes, HTMX persistence,
  separate pill actions, cancellation, reduced motion and media-track cleanup.
- Browser integration tests intercept signaling and LiveKit only in Playwright.
  A separate smoke check loads the real bundled SDK and acquires/releases a
  Chromium-provided fake microphone. This does not prove remote audio transport.
- Mobile screenshots and browser results are under `docs/verification/calling/`.
  They show test participants and simulated connection events, not a real call.
- Actual LiveKit server audio, Bluetooth/native routing, OS keyboard behavior,
  native background calling and physical-device behavior remain unverified.


## Desktop and foreground ringing

At 992px and above, incoming and ongoing calls share a nonmodal bottom-left panel.
It has a drag handle (also keyboard arrow controls), bounded native resizing and
an expand/collapse button. Its geometry is included in per-tab recovery hints.
Phone layouts retain full-screen presentation and the header-adjacent pill.

`alerts.mjs` supplies a short repeating foreground ringtone/ringback and incoming
vibration; it stops on acceptance, rejection, expiry, cancellation, and page exit.
Browsers require prior user activation for sound and may not support vibration.
This does not provide native push delivery or ringing when the app is closed.

Android Java compilation is checked separately from browser tests. Speaker
routing still needs physical-device verification and rebuilding/installing the
app. Browser routing tests use an explicit mock native bridge; they are not proof
of real phone speaker switching. Reload recovery tests use real page reloads with
intercepted signaling/media; live audio continuity across reload remains to be
checked with two devices.

Finished calls now create one `voice_call` chat message in the same transaction as
termination. The caller is the sender, so both clients use their existing sent /
received bubble alignment. The linked call record provides the actual invitation
start time and terminal outcome; duration is deliberately omitted until the
backend has an authoritative audio-connected timestamp. Chat delivery occurs
after commit; the persisted message remains available if live delivery fails.
Clients cannot create or edit call-history records through message serializers.
Migration 0003 backfills finished calls without history, retaining their original end timestamps and avoiding duplicate records. The visible End call label is present on both
the call panel and the minimized mobile pill.


Chat navigation uses the pane partial only for `HX-Target: chatMainAreaWrapper`.
Page-level HTMX navigation must receive the complete conversation template so
its shell and styles are retained. Call completion also reconciles the open
conversation through its history API, covering a temporarily disconnected chat
socket. Both members receive the same caller-owned record with direction derived
from the viewing account.
