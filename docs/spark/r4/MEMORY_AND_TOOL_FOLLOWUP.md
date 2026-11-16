# Cross-conversation memory and tool reachability follow-up

## Reproduced gaps

The reported wording was “what were we devouring last time?” It did not match the R4 recall route. Approved-fact retrieval required lexical overlap with a topic; generic last-session questions therefore supplied no facts. Academic lookup also depended on named-unit/current-record selection instead of the latest completed activity. Raw chat and digest recall were deliberately disabled by SRI-01; restoring them silently would bypass the existing approval/consent design.

A second class of gaps affected tools: broad explanation fast paths could answer account-data questions before tools; active teaching/document context could absorb unrelated platform actions; `send_notification` was excluded from planner schemas; and the write guard's shared verb list omitted update/remove/complete verbs and rejected “cancel” even for the cancellation tool.

## Repairs

- Shared whole-request learning-recall cues recognize the reported wording and common study/learning/covered/previous-session variants. Mixed operations do not match the generic whole-request pattern.
- Generic recall retrieves up to 50 recent approved learning observations, with the existing result/token budget. This includes automatic “is studying” observations stored in the semantic category, while excluding response preferences and mere future goals. Results retain fact/message/conversation IDs and source timestamps. Named unrelated queries never receive this fallback. Consent, owner and revoked/superseded status checks remain authoritative; vectors are not called for this route.
- Academic last-session lookup chooses a real finalized teaching turn with an owned assistant result and an authorized retained academic record. A newer empty session/record cannot override actual completed activity. Disabled academic recall remains disabled. Retained historical reads do not renew tracking consent or create/resume sessions.
- Registered `recall_memory` delegates to these existing services. It gives the existing agent an explicit, read-only lookup when preassembled memory is insufficient. It cannot read arbitrary transcripts, grant writes or claim mastery.
- Account-data requests retain tool consideration; ordinary concept questions remain eligible for direct answers. Explicit platform actions and account lookups can reach the existing planner from Study Mode/selected-document context, while API pending-question and canonical-run ownership checks remain unchanged.
- All 24 registered tools are now discoverable by the existing planner, plus its existing document-retrieval capability. Each tool retains owner/permission checks. Social messages/follows and memory proposals still require their existing review/approval flow.
- Tool-specific mutation verbs now permit rescheduling, cancelling/removing and completing study blocks. Negation and how-to questions remain blocked; deleting a block cannot authorize creating one.
- Immediate self-notifications require a persisted owned user request and explicit immediate intent. Same-source/same-normalized-summary calls serialize on the message row and reuse the saved notification ID. The key lives in the existing notification metadata; no schema migration or second receipt store was added. Source-row locks use `of=('self',)` to avoid incidental joined conversation locks. Future reminders stay with the scheduling tool. No real notification/message was sent during verification.
- Existing activity projection now recognizes memory lookup, immediate notifications and authorized-page reading on both backend and frontend. Existing icons, layout, auto-collapse and privacy filtering remain intact.

## Files changed in this follow-up

`pwanimate/services/recall_intent.py` (new), `services/persistent_memory.py`, `services/unit_learning.py`; `orchestrator/routing.py`, `orchestrator/service.py`, `orchestrator/agent.py`, `orchestrator/activity.py`; `tools/domain/memory.py`, `tools/domain/extra.py`, `tools/registry.py`, `tools/selector.py`; `static/js/pwanimate/activity-trail.js`; `tests/spark_r4/test_recall_tools.py` (new), `tests/spark_memory/test_pipeline.py`, `tests/spark/activity-explanations.cjs`.

The existing memory-resume deletion test's saved snapshot now contains its original user_message_id. Without that identity, the SRI snapshot fence correctly rejected the fixture before it reached memory refresh. The fence itself was not weakened.

## Verification

All data is synthetic in isolated SQLite, and model responses are mocked. No owner database, paid provider, migration, deployment or service restart was used. Counts overlap.

- First 13-case development run exposed three failures because actual “is studying” facts use the semantic category. The retrieval filter was corrected to include those existing academic records rather than change their stored category.
- Initial wider 112-test run had one stale snapshot-fixture error described above; corrected explicitly.
- Expanded regression: 129 tests OK, one PostgreSQL skip, 94.887 seconds. Covers R4 routing/budgets, personal memory pipeline and deletion refresh, runtime, activities and canonical WP4 baseline.
- Actual authenticated new-chat HTTP endpoint plus focused follow-up: 19 tests OK, 4.703 seconds. The endpoint passes the real prior topic into one mocked answer call without creating a new study session or consent card. This verifies context delivery, not a live model's answer quality.
- Final results are appended below. PostgreSQL notification race coverage is supplied but deliberately skipped on SQLite. Browser, deployed queues/workers and model compliance remain unverified.

## Owner verification

```bash
cd /home/zayne/projects/pwaninet
SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings .venv/bin/python manage.py test tests.spark_r4 tests.spark_memory.test_pipeline.MemoryPipelineTests tests.sals_wp3.test_runtime_pipeline pwanimate.tests.test_agent_activity pwanimate.tests.test_activity_explanations tests.sals_wp4.test_unified --noinput
.venv/bin/python -m tests.sals_wp0.run --suite sals03wp2 tests.spark_r4.test_recall_tools tests.sals_wp4 tests.spark_memory
```

The second command uses the existing disposable PostgreSQL infrastructure; never substitute a live student database. Verify the row-lock race, cross-chat recall after actual finalized lessons, disabled/revoked memory, multiple units, pending-question ownership, notification retries and all action approval surfaces. Verify no duplicated broadcasts/results across process interruption and align web/worker/assets through the owner's deployment process. Pre-upgrade in-flight notifications without replay metadata are not retroactively made idempotent; do not blindly replay uncertain old attempts.

## Limits

This repairs recall of actual authorized saved evidence. Older ordinary chat that was never extracted/approved or captured as academic activity is still not a saved learning record. No historical transcript import, speculative summary or bulk backfill was performed. Broader conversation-history recall needs an explicit per-student authorized data source/consent design; an account's existing approved-fact preference is not silently repurposed to authorize raw transcript retrieval.

The six legacy gateway test expectation failures and earlier combined-suite timeouts documented in IMPLEMENTATION_REPORT.md are not marked resolved by these tests. PostgreSQL/browser/SRI-01/Phase B/WP4 gates remain pending, as do R3, R5, SALS 0.4/0.5, provider expansion and the separate Lucide migration.

## Final executed results

- **132 tests reported, OK, two PostgreSQL-only skips**, 33.145 seconds (`/tmp/spark-memory-actions-final.log`): 130 non-skipped cases. Includes real new-chat HTTP context delivery, saved-fact retrieval/extraction/deletion, all registered planner schemas, nine mutation verb paths, notification replay/source denial, read-only stale-revision posture, R4 budgets, activities and the WP4 canonical baseline.
- A preceding 132-test run exposed one misleading `read_only` projection flag: enrollment eligibility alone produced false even for retained stale-revision history. Context history now explicitly sets read_only=true; underlying write authorization remains separate and unchanged. The final run above verifies the correction. This was not a newly granted write permission.
- Five Node scripts passed: activity explanations (including new memory/notification/page projections), privacy/replay, lifecycle resume, study-context synchronization and R1 follow-up handling.
- Final Python compilation, activity JS syntax and git whitespace checks passed.
- PostgreSQL notification/canonical races are SKIPPED locally, not counted as passed races. No live model, real browser, provider spend, deployment or owner service restart occurred.
