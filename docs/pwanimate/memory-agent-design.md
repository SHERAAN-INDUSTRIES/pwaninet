# Spark study continuity and tools

Implemented review version · 9 November 2026

[Architecture SVG](spark-architecture.svg) · [Editable Mermaid](spark-architecture.mmd)

## Student experience

Open `/pwanimate/` for Spark. After a meaningful study conversation, Spark generates a source-linked study digest in the background. On the next empty chat, the welcome reads the latest saved greeting, question, and study suggestions directly from SQL. It does not make another model request or choose a random topic. Without a digest, the welcome stays neutral. Suggestions start a new chat with the remembered subject; the Memory & plans settings tab can reopen the original conversation.

Open Spark Settings → Memory & plans (`/pwanimate/settings/study/`) to inspect summaries, pause memory, forget it, see saved plans, create reminders, and cancel reminders. Forgetting keeps transcripts but deletes recall vectors and digests, and sets a cutoff so delayed jobs cannot recreate old memory. Resuming memory only admits later messages. Deleting a source chat removes its digest and embeddings.

## Boundaries and implementation

- **Memory:** `services/study_memory.py` owns structured digest generation and privacy controls; `services/conversation_memory.py` owns authorized semantic/lexical recall. The source message watermark prevents older jobs overwriting newer digests. The new welcome uses the latest meaningful digest rather than searching vectors for “hello.”
- **Context:** existing bounded recent history, user/cohort context, and source-aware context packages remain in use. Message recall checks both embedding ownership and source conversation ownership. Semantic results below 0.55 similarity fall back to lexical matching.
- **Agent:** `orchestrator/agent.py` runs a provider-neutral JSON planning loop through the existing AI Gateway. Up to three planning rounds and six calls, with a 35-second admission budget between calls. This is not a hard interruption of an in-flight network request; provider timeouts still apply. Calls run sequentially in this version. Repeated identical calls are suppressed. Independent read parallelism can be introduced after latency measurement.
- **Execution:** only registered tools execute. JSON Schema validation rejects unknown fields, wrong types, and nested invalid plan sessions. The model never supplies the user identity or execution key. Personal writes require a persisted user turn plus explicit action language in the latest message. This conservative English intent gate may reject valid paraphrases; rejection is returned as evidence rather than fabricating success.
- **Sources:** authorized document chunks, web URLs, campus posts and announcements, notification counts, unread chats, profile/academic information, official timetable, personal plans, and reminders. The agent can collect several of these and return one streamed, cited answer. RAG results keep page/file citation metadata. Source text is untrusted data.
- **Compatibility:** existing social tools still create approval cards. Selected-resource, image/attachment, and explicit study-session turns retain the established orchestration paths. This review version does not yet replace those paths with the multi-tool loop. `SPARK_AGENT_ENABLED=False` disables the new loop.
- **Accounting:** successful agent responses include planner token usage in their response/usage totals. Background memory generation uses the configured gateway; dedicated memory cost accounting and quota policy need a production pass.
- **Personal planning:** separate SQL study plans keep official institution timetables unchanged. Sessions have topics and durations. This version saves plans; it does not yet generate recurring calendar events, edit plans, or automatically turn every session into an alert.

## Reminders and delivery

`SparkReminder` stores an aware UTC due time, IANA timezone, signed 32-bit native ID, cancellation state and owner-scoped idempotency key. SQL remains authoritative. `spark_reminders --watch` sweeps due rows every 15 seconds. It atomically creates a deterministic inbox notification and marks the reminder fired, so a restarted sweep cannot create duplicate inbox items. Push outcomes have durable attempt markers and up to five attempts. Network delivery is not exactly once.

The shared `spark-reminders.js` synchronizes scheduled records with Capacitor Local Notifications. It requests notification permission only when a saved reminder exists, schedules native alerts, and acknowledges only after the scheduling call succeeds. Denial and OS failure never acknowledge success. Cancellation is synchronized when the app is online/foreground again. Already scheduled alerts can run while the app is closed; remote cancellation cannot reach a device that remains closed/offline in this version. Native scheduling receipts describe scheduling, not proof that the student saw an alert.

This version uses **inexact** local alarms to avoid requiring exact-alarm special access for study reminders. Android can delay them. The plugin restores persisted schedules after reboot. No physical-phone closed-app/reboot/permission-revocation test was performed in this session. See [Capacitor Local Notifications](https://capacitorjs.com/docs/apis/local-notifications) and [Android alarm guidance](https://developer.android.com/develop/background-work/services/alarms).

An acknowledged native schedule suppresses server push for that reminder to avoid ringing twice; inbox delivery still occurs. This is a reminder-level decision, not per-device delivery routing. Multi-device push targeting, cancellation push, recurrence, editing/revisions, and exact-alarm opt-in are follow-up work. Desktop/web users receive inbox reminders and push when a working subscription exists.

## Local operation

Apply migrations 0017 and 0018. Run the existing document worker on `docs_queue`; it now also registers `refresh_study_memory`. Run `venv/bin/python manage.py spark_reminders --watch` under supervision. Production should supervise both services and monitor queue age and overdue reminders; development detached processes are not production supervision.

Optional bounded existing-chat backfill: `venv/bin/python manage.py spark_memory --user-id USER_ID --limit 5`. It preserves transcripts and respects memory preferences/cutoff. Generated copy depends on configured model availability.

Android dependency: `@capacitor/local-notifications` 8.3.1. Capacitor sync and debug APK build succeeded. Install the updated build before testing native reminder behavior; an old installed APK lacks this plugin.

## Verification and review

The new integration suite covers generated welcome persistence, no model call on welcome read, cross-student isolation, memory reset/disable, source deletion, reminder idempotency, invalid dates, owner-scoped cancellation/acknowledgment, due-sweep restart safety, combined tool observations, schema rejection, write authorization, action deduplication, execution bounds, plan persistence, and API authentication.

Client contract checks cover successful native scheduling, denied permission, cancellation, OS failure, and avoiding false device acknowledgments. These are mocked plugin checks, not physical-device evidence. Browser screenshots use the actual Django template and product script with empty account API fixtures; no real student notifications were sent.

The broader historical orchestrator tests still include old exact prompt-text expectations. These failures are recorded in the verification report; the new feature tests do not establish that the entire legacy suite is clean.

Final checks: 95-test focused PostgreSQL regression run passed; final 18-test feature run passed (overlapping coverage). Four native scheduling contract scenarios passed. Browser checks passed at 320, 390 and 1280 pixels, light/dark, including reminder-form submission. Android debug build passed. A live model selected both tools against synthetic fixture counts; no private data was used in that probe. The optional private-account count probe was rejected by automatic approval review and was not executed. The legacy 144-test run had 18 failures (17 historical prompt wording assertions and one greeting task assertion reproduced with the new agent disabled).

[Verification report](../verification/spark/study-assistant-results.json) · [Phone screenshot](../verification/spark/settings-study-390-dark.png) · [Desktop screenshot](../verification/spark/settings-study-desktop-modal.png)

Diagram rendering uses Mermaid CLI with `spark-architecture.config.json` and `spark-architecture.mmd`; export to `spark-architecture.svg`. The config disables HTML labels for portable SVG text and sets explicit sans-serif typography.

Settings integration: the standalone Study Desk was removed. Its old URL redirects to the existing Spark settings shell. Mobile segmented tabs and the desktop settings sidebar both include Memory & plans, using existing form controls, choice cards, theme colors, and spacing. Scoped initialization supports lazy tab opening and HTMX navigation. Twenty feature/route tests and responsive browser action checks passed.
