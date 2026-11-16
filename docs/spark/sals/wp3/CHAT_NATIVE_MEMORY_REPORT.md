# Chat-native personal memory — 11 November 2026

Implemented an optional non-blocking proposal tool and chat-native approval controls using the existing WP3 fact lifecycle, MemoryView API, tool registry and interaction surface styles. No separate agent, vector store, endpoint, model or migration added. This is not WP4.

## Flow and security

ProposePersonalMemoryTool offers only concise/detailed response length, bullet points/paragraphs format, and English/Kiswahili response language. It checks the actual owner-scoped persisted user-message ID injected by StudyAgent, not a model-supplied source. The whole latest message must match a direct first-person preference/request pattern; arbitrary personal text, sensitive information, document-derived statements and third-party preferences are not automatically extracted. Server-generated exact fact text prevents the model embedding extra data in a candidate. This deliberately narrow detection is safer than an unconstrained extraction prompt; broader facts are deferred.

SPARK_MEMORY_PROPOSALS_ENABLED=False disables tool proposals without private source access. Personal memory must also have effective WP3 opt-in. Under the existing preference-row lock, at most one candidate per user in24hours is admitted; existing identical text also suppresses repeats. Pending/rejected recent proposals consume the limit, so rejection does not trigger immediate repeated suggestions. No LLM call is made by the tool itself.

A candidate is saved with source_message and status proposed. Normal answer generation continues; no approval wait is introduced into SparkAgentRun. UI uses existing spark-interaction-block styling and the same WP3 versioned authenticated MemoryView PATCH actions. Approve, Edit and Reject display exact text. Approve never enables global memory; it uses current user/owner/fact version/captured consent revision checks already implemented by WP3. Repeated clicks lock locally; repeated/stale server requests fail version/status validation. Edit saves a new proposed version, requires a fresh approval, and disables Approve while unsaved changes are visible. Reject clears content via the existing service and removes the block. Failure keeps the block with an error; stale consent requires fresh review rather than blind retry.

Pending facts are restored from owner GET /memory/ filtered to the current conversation on initialization, completed/resumed chat list refresh and browser focus. No local storage of personal text. Disabled memory removes pending blocks on the next refresh; the API enforces disable immediately even before UI refresh. Approved facts alone enter future recall. Existing resumed-run snapshot refresh from WP3 remains in force. Fact source links do not grant access to another conversation. GET now select_related(source_message) avoids per-fact payload lookups.

## Changed files

- pwanimate/tools/domain/memory.py (new tool and bounded preference detector)
- pwanimate/tools/registry.py (registration)
- pwanimate/orchestrator/agent.py (server-only source message injection)
- pwanimate/services/personal_memory.py (source conversation ID in owner payload)
- pwanimate/api/study_assistant.py (MemoryView owner-list prefetch only)
- static/js/pwanimate/pwanimate-chat.js (restore/render/versioned decisions)
- static/css/pwanimate/pwanimate.css (opaque theme-aware responsive surface)
- tests/sals_wp3/test_chat_memory.py (new offline tests)
- tests/sals_wp3/test_database.py (additional tool rate/owner/approval cases)
- tests/sals_wp0/run.py (select new tests)
- tests/spark/memory-approval.cjs (new DOM tests)
- this report

## Executed checks

42 offline Python tests passed: existing35 + WP3 offline4 + chat-tool3, under WP0 standalone settings and transport guards. Node memory approval DOM test passed approve/reject/edit/reapproval-version behavior, repeated clicks, stale error, reload conversation scope, disable and solid theme token. Existing clarification, quiz and streaming-collapse tests passed. JavaScript syntax, Python compilation and git diff --check passed. No live LLM requests or production data accessed.

Full WP0 PostgreSQL runner attempted: blocked before startup by Docker socket permission denied. New DB tests are selected but **not executed**: owner/source isolation, opt-in checks, one-proposal frequency, no autoapproval and stale/repeated decisions. WP3's earlier DB approval/rejection/edit/forget/consent race tests remain unverified here. No new migration exists; WP3 migration0027 still must be applied in the eventual authorized environment. Real-browser visual/mobile verification was not performed; solid background uses the same existing light/dark --pwanimate-bg token.

## Limits and compatibility

The initial StudyAgent planner admits general text, not study-mode/selected-resource turns. This feature does not reroute those paths or add another loop. The normal tool selector cannot create a candidate without a server-injected source ID; it fails closed outside the supported agent path. The model may choose not to propose, even for a matching preference. Broad automatic personal facts, sensitive fact inference, extraction of historical transcripts, fact embeddings and chat-specific settings redesign are deferred.

Approval blocks reuse the existing WP3 persisted proposal/approval protocol and interaction UI primitives rather than pausing/replaying a separate run approval. Settings remain the fallback for view/edit/delete/revoke/disable. The browser polls on focus/turn completion, not continuously; other-tab changes may leave stale controls briefly visible, but backend consent/version checks remain authoritative. Old source conversation deletion follows WP3 source SET_NULL behavior; no academic lifetime changes were made. Production release remains conditional on DB/migration, concurrency and real-browser checks. No grading/mastery, academic progress, document authorization or Workspaces changes, push or deployment. Stop here; WP4 requires separate approval.
