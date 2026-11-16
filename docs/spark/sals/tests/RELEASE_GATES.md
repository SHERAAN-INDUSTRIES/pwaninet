# Phase 0/1 release gates

Design only. No test suite has been implemented/executed by this assignment. A complete specification is not a release pass.

| Gate | Required evidence | Acceptance IDs | Scope/status |
|---|---|---|---|
| G0 baseline | Intended release commit, resolved migration/runtime graph, isolated PostgreSQL harness, no live network | M05, isolation document | Unverified; required before schema acceptance |
| G1 private access | Shared policy on all retrieval/assets; no elevated bypass; bounded source/file identity and unit/category filtering | D01,D03–D08 | Required before protected-data release |
| G2 consent cutover | Off-by-default, legacy quarantine, in-flight success/failure fencing; same-chat context unaffected | E01,E04–E05,E10 | Required Phase1; extraction stays disabled |
| G3 independent retention | Both deletion paths detach; evidence readable; owner-controlled academic export/delete and anti-resurrection | E07–E09,M02 | Required before independently retained progress launches |
| G4 validated grading | Strict schema, exact count, trustworthy keys, deterministic MCQ/numeric, unsupported formats refuse credit | V01–V18 | Required before certified assessments |
| G5 transaction integrity | Real PostgreSQL races, idempotency, lease fencing/crash recovery, objective version isolation | C01–C13,M04 | Required before v2 writes launch |
| G6 truthful metrics | Count partition, null accuracy, no summary/legacy certification, goal/practice separation | P01–P09 | Required Phase1; automatic mastery promotion off |
| G7 compatibility/UI | Old chats/history/source context, disabled features, API negotiation, null/inconclusive labels, keyboard/mobile/themes | K01–K06,B01–B05,M01,M03 | Required before cohort expansion |
| GF1 personal-fact approval | Exact candidate approval/rejection/revocation/delete, no unapproved retrieval, interfaces and retention | E02–E03,E06 | Future capability; disabled until passed |
| GF2 explicit document sharing | Grant actions/recipient/version/expiry/revocation and storage delivery parity | D02 + D01 recipient parameters | Future capability; owner-only private access until passed |
| GF3 retained mastery | Approved versioned reasoning/breadth/independence/delay thresholds and reviewed policy fixtures | P10 plus P06–P09 | Future certification gate; accuracy/demo feedback may ship earlier |

All required parameterized cases must pass with no security/integrity failures; future tests may be labeled not-implemented only if corresponding server capability is disabled and that disabled behavior is tested. Do not count skips as passed functionality. Existing regression tests may need policy-aligned updates; old broad privilege/default consent assertions are not reasons to violate approved policy.

Readiness meanings: READY = all specification dependencies resolved for the stated scope, still subject to implementation approval and executed release gates. CONDITIONALLY READY = bounded implementation can be planned with exact prerequisites listed. BLOCKED = a dependency prevents the stated work entirely. Current planning verdict is CONDITIONALLY READY; no production release is approved.

CI artifacts: source commit/dirty-state manifest, runtime/DB versions, migration graph, case matrix results, concurrent DB assertions, fake-provider inventory, privacy-sanitized browser traces and rollback demonstration. Record no invented pass counts or timings. Network fail-closed and temporary storage are mandatory, not optional optimizations.

Operational gates: migration deployment must account for Compose auto-migrate; worker recovery/scheduler presence must be proven; storage privacy and grant revocation validated; deletion/export and backup purge SLA agreed. Initial streamed chat remains process-local; test existing behavior and document limitations rather than promising new durability outside scope.

Rollback proof must use compatibility readers/writers retaining fixed privacy/deletion semantics. Feature disable halts new writes and preserves authorized historical reads. Never reverse-drop evidence or deploy old CASCADE/deletion and unsafe grading code as routine rollback.
