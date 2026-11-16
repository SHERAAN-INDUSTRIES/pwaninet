# WP2 document access implementation — 11 November 2026

Status: application-path changes implemented; **security release remains conditional on PostgreSQL integration and protected storage verification**. No WP3 work, production access, migrations, push or deployment.

## Policy and integration

New documents/access_policy.py centralizes live read querysets/object checks. Public ready/available non-AI documents remain readable; private and AI-generated resources require owner. Elevated roles do not bypass private read policy. No document-level grant model exists meeting approved requirements; collection membership is not promoted into a file grant. Private sharing remains disabled, as permitted by the approved contract.

Restricted documents require current user institutional scope, matching institutional programme-unit mappings and current active enrollment or programme entitlement. Explicit inactive enrollment denies that unit even through programme fallback. Current account fields are reloaded; no positive permission cache introduced. Fuzzy legacy course-name authority and first-institution fallback are removed. Institutional mapping gaps now fail closed and require data correction rather than broader privilege.

Shared policy applies to vector/page candidate queries, lexical metadata search and hybrid's underlying paths, selected/restored page context helper, saved inventory, document metadata tool, document API queryset/permissions and detail/download/generated private asset delivery. Scope filters intersect before ranking/count/limit; empty explicit document/unit lists fail closed. API download file_id must belong to the selected document. Signed download rechecks live document access and uses no-store; generic errors avoid exposing storage failures. Private management gates prevent elevated roles from changing private visibility to obtain access.

## Files

- documents/access_policy.py (new)
- documents/api.py, permissions.py, views.py
- pwanimate/retrieval/services/document_retrieval.py
- pwanimate/retrieval/adapters/pwaninet_search.py
- search/services/unified_search_service.py
- pwanimate/services/study_session.py (document authorization helper only), study_materials.py (inventory)
- pwanimate/tools/domain/documents.py
- tests/sals_wp2/__init__.py, test_policy.py, test_database.py (new)
- tests/sals_wp0/run.py (select new database tests)
- this report

Separate user-requested streaming fix in the same turn: static/js/pwanimate/pwanimate-chat.js collapses current activity on first nonempty answer chunk in shared SSE reader, including resumed streams. tests/spark/stream-collapse.cjs added. It collapses once so user may reopen during later chunks.

## Executed verification

41 offline tests passed (six policy/SQL-construction/delegation/forwarding tests plus35 prior validation/API/isolation/contracts). Policy tests build real ORM query structures without connecting to DB; they do not establish the queries return correct rows at runtime. Python compilation, git diff --check and JavaScript syntax checks passed. Quiz-flow, clarification-flow and new stream-collapse Node regressions passed.

WP0 PostgreSQL runner attempted: denied Docker socket access before startup. Five new database tests are selected for future permitted runs: private owner/elevated roles, visibility revocation, unavailable/empty scope, missing institution denial, and enrollment revocation overriding programme fallback. These tests were **not executed** here. Earlier user-local WP0 success does not cover the new policy. No enrollment/file-serving integration or full prior document permission suite passed is claimed.

## Compatibility and security limits

Existing tests/workflows granting executive/staff private access intentionally conflict with approved new policy; those consumers need alignment. Legacy institutional mappings without explicit ownership may lose restricted visibility. Existing public/ready content remains accessible; owner draft metadata remains inspectable separately from content retrieval.

No storage/index/embedding redesign occurred. Previously exposed direct object-store/CDN URLs, browser/offline caches and public-origin originals/previews are **not revoked by ORM filtering**. Protected asset origin configuration and all raw URL generation/serializer paths need isolated/live storage review before claiming immediate content revocation end to end. Already downloaded bytes cannot be recalled. No explicit grant lifecycle/expiry support was fabricated. Full institution-role/path matrix, signed delivery/file substitution, current source restoration and cache-revocation integration remain required PostgreSQL/browser/storage gates. Private-access changes are not production-certified on the offline tests alone.

No grading/memory/mastery logic changed. The shared study-session module changed only its document authorization helper. Stop at WP2; WP3 requires separate approval.
