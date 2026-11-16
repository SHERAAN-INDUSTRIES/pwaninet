# SALS architecture decision records

11 November 2026. Status: product policies approved as design requirements; engineering choices proposed/recommended, not implemented. Source references describe existing code. Supersedes conflicting open decisions in the audit; preserves the audit as historical evidence.

| ADR | Decision and rationale | Evidence / alternative rejected | Consequence and validation |
|---|---|---|---|
| 001 | Extend the current Spark monolith and services | Existing PwanimateOrchestrator, AIGateway, ToolRegistry and study/assessment services already cover execution; separate agent adds duplication | Keep study/general planner admission distinction; K01,K05 |
| 002 | Private owner-only with explicit grants and no role bypass | Document retrieval/page/search/API predicates differ and grant broad executive reads | Remove broad reads deliberately; sharing off until grant lifecycle works; D01–D07 |
| 003 | Separate transcript, recall, approved facts and learning evidence | MemoryPreference defaults True; digest/embedding are not approved personal facts | Off-by-default legacy quarantine; future fact approval mandatory; E01–E06,E10 |
| 004 | Retain existing session independently of chat via nullable SET_NULL | Session.conversation CASCADE currently deletes assessments/artifacts/checkpoints | Prefer small FK/read compatibility change over duplicate academic workspace; E07,M02 |
| 005 | Immutable objective versions fence evidence | set_target resets fields but _recompute_mastery includes all session assessments | Old accepted outcome remains historical, no new goal credit; P08,C10 |
| 006 | Normalize question/submission/evaluation only where integrity requires it | Whole SparkAssessment.questions JSON cannot enforce per-question reservations and independent concurrent writes | Four new records including objective version; no second Quiz entity; C01–C13,M04 |
| 007 | First accepted answer per question/attempt, explicit retake | Overwriting answers permits changing scored evidence and races | Duplicate replay returns same identity; competing request409; C01–C05 |
| 008 | Deterministic initial MCQ/numerical grading with verified keys | Current bool coercion/LLM fallback and unchecked generated keys | Strict schema is necessary but not mathematical proof; unsupported formats uncredited; V01–V18 |
| 009 | Accuracy/completion separate from mastery | _recompute_mastery is graded accuracy; roadmap mean also misleads | Null accuracy with zero denominator; mastery policy off until calibrated; P01–P10 |
| 010 | Short reservation/finalization transactions and fenced recovery | Existing run claim/lease patterns useful; fresh request threads are not durable enough | No provider under locks; pending DB rows discoverable after enqueue loss; C06–C13 |
| 011 | Explicit v2 contract with legacy labeled reads | Current JS expects truthy boolean, no idempotency/version fields | No silent null-as-wrong, no legacy certification; unsupported v1 writes fail clearly; K02,K06,B01–B03 |
| 012 | Derived projections/in-process notifications first | Existing Celery/journal avoids need for generic event platform | Recompute by evidence ID, add outbox only for proven external durability need; C11 |
| 013 | Stable private unit scope, no shared catalog redesign | AcademicUnit exists; resolve_unit code/name/custom label not ownership namespace | Owner-scoped UUID/custom label; enrolled FK preserved; D03–D04 |
| 014 | Non-destructive compatibility rollback | Old CASCADE/unsafe grader binary would violate new guarantees | Flags/read-only compatible build, preserve schema/evidence; M03 |
| 015 | Real PostgreSQL gates for races | settings/test.py SQLite does not model row locks/pgvector | Independent connections/barriers, fake network/providers; C family and M05 |

Open bounded choices: exact schema/constraint mechanism after dependency baseline; operational backup/purge guarantees; calibration thresholds/retention intervals; future sharing/fact interface scope. Approved privacy/retention policies are not reopened for approval. Automatic mastery stays disabled until policy calibration; no delay threshold invented here.
