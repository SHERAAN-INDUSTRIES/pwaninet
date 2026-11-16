# Phase 0 readiness report

Verdict: **CONDITIONALLY READY** for bounded implementation planning; **not production-release ready and not authorization to implement**. Date: 11 November 2026.

All six approved policies have concrete contracts in this directory. The current branch is main; intended release branch and deployed schema remain unverified. Source supports extension through existing AssessmentService, StudySessionService, document context, gateway and APIs. Policies override broad private-document privileges, default memory enablement and chat-linked learning cascades. No Workspaces integration or second agent is needed.

## Exact conditions before affected implementation/release

| Condition | Evidence | Resolution required | Gate |
|---|---|---|---|
| Delivery baseline | Extensive modified/untracked source; HEAD alone excludes it | Owner selects release branch/commit including reviewed prerequisites; do not discard unrelated work | Before schema work |
| Migration/runtime parity | Local migration sources 0022–0026; Dockerfile Python 3.11 versus generated migration headers | Sanitized applied-version manifest and isolated supported-runtime migration test; no production queries in this assignment | Before rollout |
| Real concurrency harness | settings/test.py uses SQLite and imports local settings | Isolated PostgreSQL/pgvector runner with fail-closed network/tasks/storage | Before accepting transaction implementation |
| Owner-only access coverage | Multiple policy implementations and elevated-role tests | Adopt shared contract; audit file/version binding and private asset delivery; new sharing stays off until grants proven | Before private-content release |
| Academic independence | Session.conversation CASCADE and direct deletion endpoints | Nullable SET_NULL session relationship, detached-session compatibility and immutable evidence ownership | Before new evidence rollout |
| Consent cutover | enabled=True, transcript embeddings/digests | Disable legacy unconsented recall and candidates; in-flight consent-generation guards | Before memory-related release |
| Numeric validation/key provenance | Current free text uses LLM; reference keys not certified | Bounded deterministic parser/comparator and reviewed/computed keys with invalid-generation quarantine | Before validated maths release |
| Mastery calibration | No reviewed threshold/delay policy supplied | Keep certification off; owner approves calibrated version before automatic promotion | Before mastery certification, not a blocker for honest accuracy |
| Retention operations | No approved backup/purge SLA | Define export/delete operational behavior and backlog/backup handling before launch of independently retained data | Before production release |

No thresholds, provider performance, running worker status, production DB state or test passes are invented. Full individual-fact approval, sharing UI, advanced reasoning/retention mastery and full export UI may remain deferred only behind disabled capabilities and explicit release gates; basic owner-controlled academic export/delete is required before launching independently retained progress.

## Migration considerations

Reuse session/assessment data. Proposed additive goal/objective version, immutable published-question and submission/evaluation evidence; make conversation relation optional with SET_NULL. Private grants only when sharing is released; personal-fact table only when approval is released. No duplicate transcripts, academic catalog or document pipeline. Old accuracy is never backfilled as certified mastery. Existing IDs and source observations remain readable.

Phase 1 must specify transactional first-answer reservation, idempotency, retry recovery, objective fencing, strict type-safe grading and key-safe compatibility. These designs and acceptance specifications are the next dependencies. Existing audit reports are historical and are not rewritten to imply that approved policies are already enforced.

Only documentation is authorized. No application code, tests, schema, settings, prompts, package or data changes were made. Final combined readiness and ordered work packages are in `../IMPLEMENTATION_READINESS_REPORT.md` and `../IMPLEMENTATION_WORK_PACKAGES.md` after reconciliation.
