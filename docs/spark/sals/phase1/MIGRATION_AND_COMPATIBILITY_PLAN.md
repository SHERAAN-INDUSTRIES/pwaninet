# Migration and compatibility plan

No migration is created or applied. Existing sources are `pwanimate/migrations/0022...0026`, current models/services and `pwaninet/settings/{base,production,test}.py`. Current migration application state and intended release branch are unknown. This plan uses logical operations, not invented migration filenames/numbers.

## Ordered rollout

1. Establish reviewed delivery branch and dependency baseline. Confirm all pre-existing migration sources included; compare a sanitized applied-version manifest in the later authorized deployment workflow. Never renumber an applied migration or assume untracked means unapplied. Test actual Python/Django/PostgreSQL/pgvector compatibility; Dockerfile currently declares Python 3.11.
2. Expand schema additively: nullable version/certification fields on assessments, objective/question/submission/evaluation tables and constraints; session.conversation nullable SET_NULL plus active objective pointer. Add indexes for owner/request lookup, assessment version/qid, pending leases and objective evaluations. Do not fill unsupported concept identities. Build large indexes with an appropriate nonblocking deployment strategy only after row-count measurement.
3. Deploy compatibility readers that accept old JSON, new evidence and detached sessions. Update both conversation-delete paths and any indirect/admin deletion semantics before enabling academic preservation. Deletion detaches session; no new transcript duplication. Verify existing study context/report/serializer/template null assumptions.
4. Quarantine legacy unconsented recall at policy switch; leave approved personal extraction disabled. Apply uniform private-document policy across text/files/inventories and replace broad role expectations. Storage delivery must be secured, not merely queryset-filtered.
5. Enable v2 validated generation/submission for isolated cohort behind server capability policy. New v2 writes use normalized immutable records; optional existing JSON fields become compatibility projection only, updated under lock. Avoid bidirectional writes: normalized rows are authoritative, JSON never overwrites them. Generation/finalization recheck flags.
6. Enable updated score UI/API consumers only after nullable/inconclusive handling and semantics tests pass. Preserve historical legacy display explicitly labeled; disable unsafe v1 write forms where no unambiguous mapping exists. Keep GET/history for disabled assessment capability.
7. Validate export/delete of independently retained progress and recovery runner; then widen release after all gates. Complete policy migration and cleanup only in separately reviewed later changes; no destructive cleanup necessary for Phase 1.

## Legacy transition

Existing conversations, messages, sessions, assessment IDs, question JSON and reports remain readable. Do not automatically convert old keys/LLM boolean outcomes to certified keys/evidence, or old mastery_percent to retained mastery. Optional imported questions are legacy_uncertified with preserved raw historical value; do not claim corrected certainty. New objective versions begin with insufficient evidence. Reports are historical snapshots, not silently recomputed claims. Existing completed assessments cannot reopen through migration.

Changing session.conversation cardinality requires updating indexes/null access patterns; historical conversation FK may remain unique for non-null values. Duplicate active unit sessions are handled by existing conditional constraint; do not accidentally revert to one active session per entire user. New stable private unit identity must not collide across users/custom labels. Changes must preserve enrolled canonical unit FKs.

Academic export/delete rechecks owner and scope. A dedicated delete action removes evidence, questions containing assessment-only private data as appropriate, artifacts/projections and pending work; no resurrection from compatibility JSON, events, digests or delayed tasks. User deletion semantics remain separate. Backup retention and deletion suppression tokens require an operational policy before launch.

## API compatibility

Existing endpoint `/api/pwanimate/study-sessions/<session_id>/assessments/` and action names remain. Explicit v2 negotiation adds idempotency header, typed answers and versions. New public serializers allowlist fields; keys stay server-side. V1 MCQ strings only adapt when unambiguous; old free-text is readable but cannot silently receive certified grading. Numeric JSON zero must not vanish through value-or-empty conversion. Errors get stable codes; no successful response containing a fabricated boolean for inconclusive outcomes.

Current frontend posts action/assessment_id/qid/answer, has no version/key, and treats correctness by truthiness. Backend-only opt-in can be built first, but the v2 cohort UI must be adapted before public writes; UI changes are future work packages, not performed here. Study and general-planner paths remain distinct; no global rerouting or prompt rewrite.

## Rollback and operational safety

Use feature flags to stop new v2 writes, leave read-only compatibility and new data intact. Do not roll back to old binaries whose conversation CASCADE would destroy newly detached/preserved evidence or whose scoring can certify false credit. Safe rollback target is the compatibility build with fixed deletion/policy, not arbitrary pre-Phase-1 source. Restrict writes during rollback if necessary. Reverse schema/drop tables is not a routine rollback; export/back up and explicitly approve destructive reversal separately.

Crash recovery uses pending submissions and fenced leases, not request-process memory. Enqueue on_commit can fail after commit; periodic recovery must discover durable pending rows. Database restore must consider idempotency/event replay and backup privacy promises. Production Compose auto-runs migrate on web start; staged migration and deployment orchestration must be reviewed to prevent unplanned rollout. No compose, manage.py, package install or application test was executed here.

Migration acceptance M01–M05 in `../tests/ACCEPTANCE_TEST_MATRIX.md` requires isolated PostgreSQL historical-model fixtures, additive upgrade, detached session preservation and compatibility-build rollback. Exact migration names/SQL are implementation review decisions after baseline resolution.
