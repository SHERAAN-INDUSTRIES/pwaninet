# SRI-01 — Student workflow acceptance checklist

Date: 2026-11-14. **Not executed in a real browser during SRI-01.** This is a future acceptance checklist, not permission to mutate student data during the read-only investigation. Use designated acceptance accounts and the owner's approved acceptance environment. Record automated, browser and runtime evidence separately.

## Evidence and setup

- [ ] Record release/source fingerprint, applied migration through the required schema, web and worker release/start metadata, consuming queues, recovery/beat state and served frontend fingerprint. Verify browser-loaded bytes match the tested release.
- [ ] Use one enrolled acceptance student with an eligible unit/semester, one unrelated account for isolation, and an indexed accessible academic document. Do not infer enrollment from document visibility.
- [ ] Label browser/device/viewport/theme; run desktop and mobile independently, including reload and restored history.
- [ ] Alias identifiers locally: conversation, user message, PendingAction, execution boundary, canonical session/generation, record, run/attempt, teaching state/turn, assistant result and pending question. Keep correlation evidence private; do not copy transcripts, credentials or full account identifiers into reports.
- [ ] Keep three distinct outcomes: automated checks passed; actual browser flow passed; deployment/runtime verified. None substitutes for the other.

## Consent approval, decline and control identity

- [ ] Request an enrolled OOP lesson without first activating Study Mode. A tracking decision appears before tracked lesson generation. The control describes whether this lesson will wait, what tracking permits, and that earlier content will not be imported.
- [ ] Identify the actual control's PendingAction and original run. If above composer, verify it is bound to that decision rather than an unrelated practice interaction. Placement alone is not a pass or failure.
- [ ] Approve once. Decision remains saved, successful control disappears or becomes a clear recorded outcome, original run continues, and exactly one prospective execution receipt exists. Repeat clicks/reconnect do not duplicate the lesson.
- [ ] Decline on a separate fresh acceptance flow. Spark still answers the requested lesson untracked, does not claim tracking is active and creates no enrollment-linked completion/mastery. Reload respects the recorded decline.
- [ ] Expired/stale/revoked decision displays a recoverable error without losing the student's draft or executing an unrelated action.
- [ ] Restore a consent control while another session's question is parked. Submission resolves only the displayed action/run; neither control silently replaces or answers the other.

## Canonical Study Mode header and lifecycle

- [ ] Fresh successful tracked lesson displays the same selected session ID/status as the canonical API; tracking posture is shown separately from lifecycle. A backend row plus absent header is a UI failure, not evidence that no row exists.
- [ ] Start Study works untracked. It must not silently grant academic consent or claim learning/mastery.
- [ ] Renew tracking on an already paused session. The session remains paused until explicit Resume; grant is not activation.
- [ ] Existing legacy/SALS candidate collision asks for explicit selection. Handoff increments generation, parks departing work and preserves its question/history; no merge or silent newest-session choice.
- [ ] Use a stale second tab to change selection/lifecycle. It rejects/refreshes coherently and never changes another session.
- [ ] Switch conversations during canonical fetch/consent resume. Header, controls and resources belong to the displayed conversation and current generation; no stale banner survives from the previous chat.
- [ ] Reload and restore on desktop/mobile. Header and session modal/sheet agree; paused/completed status is not shown as active. Completed sessions require an explicit new-session decision.

## Document-grounded lesson and durable learning

- [ ] Open the eligible unit's document and request a specific lesson/page. Spark uses accessible actual text, preserves document/page/source citations and states unavailable extraction honestly.
- [ ] An unrelated/foreign/inaccessible document does not provide attribution or sources. A multi-unit document asks for clarification where canonical enrollment cannot uniquely resolve it.
- [ ] After consent, submit both an explicit “Explain…” request and a question-form “What is…” request. For each, a genuinely completed answer creates the correctly classified completed event once, linked to the prospective authorized source and selected record/session.
- [ ] A teaching explanation remains visible even when Spark ends with a pending practice/next-step question. Reload/reconnect shows the same completed segment and question once, with sources intact.
- [ ] Saved learning history reports completed explanation or ordinary topic exposure accurately. Session creation, source request and retrieval alone do not count as completed learning.
- [ ] No authorized content before the decision is backfilled. A cutoff complaint alone earns no completed learning or verified mastery.

## Practice, grading and progress meaning

- [ ] Request practice; one owned question is assigned. It does not submit itself or skip to a solution.
- [ ] Submit an answer explicitly; exact interaction/run ownership is preserved, accepted controls retire, feedback is durable and retries do not double-grade or advance twice.
- [ ] Hints/full solutions record assistance; inconclusive open prose is not described as independently verified correct.
- [ ] Formal assessment uses the reviewed/bound evaluation path. Request activity, self-report, explanation, formative feedback and independently verified concept evidence have visibly different meanings.
- [ ] A single correct answer or legacy percentage never automatically becomes MASTERED; policy/prerequisite/retention requirements remain intact.

## Pause, resume and cross-chat recall

- [ ] Pause during a pending question from each desktop/mobile surface. Canonical session/controller agree; completed history and exact pending question remain.
- [ ] Attempt to submit while paused or unselected. No unauthorized continuation/evidence occurs; UI offers explicit return/resume.
- [ ] Explicitly resume the original session. Same question/run is recovered without duplicate lesson/assessment or lost document context.
- [ ] In a new conversation, ask for the named unit/semester history. Spark describes the actual saved topic, completed activity, paused status and outstanding work.
- [ ] New-chat absence of ask-scoped write authorization is described as such; retained historical learning is not reported deleted. Reading history does not reactivate or authorize writes.
- [ ] With multiple units/terms, ask a generic “where did we stop?” It clarifies rather than choosing or merging arbitrarily.
- [ ] “Return to session” opens its owned canonical experience; “continue here” creates the required new authorized execution boundary and does not steal the old run/question.

## Interrupted generation and response recovery

- [ ] During an acceptance-only long code/explanation response, interrupt the network or generation using supported controls. Partial content is clearly incomplete, not a completed lesson/run.
- [ ] Compare privacy-safe lengths/hashes of the final provider-normalized answer where retained, durable assistant content and browser raw answer. If evidence is unavailable, mark the provider-versus-renderer cause unknown.
- [ ] Token-limit/max_tokens and missing-terminal-marker fixtures do not become stop/success silently. Automatic continuation is bounded and does not duplicate visible text.
- [ ] Reload/reconnect recovers the exact durable result/question where available; otherwise shows truthful interrupted/failed state and a deliberate recovery option. It does not blindly replay uncertain provider work.
- [ ] Cancel/pause/revoke during generation. Any later persisted dialogue is not finalized as new authorized learning after the relevant boundary; stale attempts cannot overwrite newer outcomes.
- [ ] A completed Celery task containing an error payload is shown as application failure, not student-task success.

## Natural greetings and personal continuity

- [ ] In a fresh chat say “Wagwan Spark, how are you?” Spark replies briefly and naturally. When a real permitted recent paused OOP activity exists, it may offer to continue it without inventing coverage/mastery.
- [ ] Repeat with “hello” and “hi Spark, how are you?”, including active Study Mode and an open PDF. Pure greetings cause no textbook retrieval, tool selection, planner loop or multi-step checklist.
- [ ] With no eligible recorded prior activity, greeting stays generic. With multiple plausible activities, avoid an unjustified specific claim.
- [ ] Relevant approved response preferences are honored without exposing unrelated personal facts. Personal-memory and academic-tracking opt-outs remain independent.
- [ ] Accept a grounded continuation suggestion explicitly. It follows the actual session/question/consent contract; the greeting itself created no session, grant or evidence.

## Authentication, consent revocation and device behavior

- [ ] Signed-out/foreign-account access to conversation/session/action/interaction/run/record/history fails without leaking existence/content. Repeat via restored links and journal endpoints.
- [ ] Revoke/disable tracking through the supported acceptance flow. No new academic evidence is written; retained history follows the declared historical read/recall policy. A stale card/snapshot cannot revive an old grant.
- [ ] Disable/forget personal memory independently. Approved fact recall and delayed R2 deliveries respect revision/reset/deletion boundaries; ordinary academic records are not silently conflated with personal facts.
- [ ] Desktop and mobile: same control identity and results, readable header/lesson/question, keyboard and safe-area access, draft preservation, no overlapped composer or hidden success state. Check light/dark themes and document viewer restoration.

## Current SRI-01 verification status

Executed here: four Node contract scripts passed; isolated WP4 suite 14 tests OK with one PostgreSQL skip; one disposable capture-gap fixture passed by asserting the defect; offline parser/greeting and terminal-payload probes confirmed the documented mechanisms. These are **automated diagnostic results only**. No checkbox above is marked browser accepted. No live DB/provider/process deployment attestation or PostgreSQL run occurred in SRI-01. Owner-reported 353 WP4 tests do not change this distinction.

Pending roadmap remains: Phase B live academic continuity acceptance; WP4 real-browser unified Study Mode acceptance and pending summary/revision-document follow-on; R3 Progressive Hint Restraint; R4 Response Quality, Orchestration and Runtime Optimization; R5 End-to-End Student Acceptance; SALS 0.4 Academic Content Intelligence; SALS 0.5 Quizzes, Assignments and Mock Exams; later student-progress/revision features; separate Vasto-wide Lucide icon migration. SRI-01 is inserted before remaining feature work.
