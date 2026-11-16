# R4 implementation and verification

Scope: existing Spark in PwaniNet/Vasto. Code is implemented locally; owner PostgreSQL, deployed worker and real-browser acceptance remain unverified. R4 does not close earlier gates or begin R3/R5/provider expansion. No migrations, credentials/configuration, frontend layouts, icon systems, owner services or student records were changed. The pre-existing extensive dirty working tree was retained. This file inventories only R4 edits, not the whole diff against HEAD.

## Exact source changes

- `pwanimate/orchestrator/routing.py` (new): conservative deterministic intent/uncertainty hints; no classifier LLM or mutation.
- `pwanimate/orchestrator/service.py`: strategy selected before expensive orchestration; saved-history recall parks teaching/incidental documents; ordinary explanations/math/code use existing direct finish; complex selected-document work reaches existing planner; page evidence reuse and document-only default retrieval; optional grounding for general direct answers; safe timing/query wrapper and tool/retrieval/context counters; preserve the existing academic output ceiling and explicit override for direct academic answers.
- `pwanimate/orchestrator/agent.py`: retain existing run/checkpoint/tool executor; no new-run seeding/writing of unversioned observation cache; permanent/aggregate exhaustion errors propagate without an immediate answer attempt; malformed plans are not repeated without new evidence; numeric planner diagnostics and duplicate counts; selected-document/unit intersection; anonymous inventory skipped; academic planner JSON structurally bounded and duplicate academic tool item removed.
- `pwanimate/orchestrator/timing.py`: strategy, numeric usage/provider/terminal diagnostics and scoped SQL execution counting through Django execute_wrapper. No SQL/parameters or private content retained.
- `pwanimate/orchestrator/prompts.py`: explicit tone/depth precedence, approved preference scope and honest saved-history/missing-record/exposure semantics.
- `pwanimate/orchestrator/activity.py`: canonical authorized-page reading stage with existing document-read activity category/icon contract.
- `pwanimate/ai/gateway/gateway.py`: numeric provider-attempt count on primary/fallback/interrupted responses. Candidate selection, eligibility, pricing and providers are unchanged.
- `pwanimate/services/agent_execution.py`: numeric perf recorder/snapshot for durable continuations. Claim, cancellation, result publication, exact question ownership and transition logic are unchanged.
- `pwanimate/services/casual_context.py`: whole-message emoji/slang greeting and thanks recognition; substantive mixed requests remain excluded.
- `pwanimate/services/unit_learning.py`: completed-topic/remembered-session history phrasing and reuse of existing read-only history selection. Authorization rules and historical records are unchanged.

## Exact test/tooling changes

- `tests/spark_r4/__init__.py`, `benchmark.py`, `test_runtime.py` (new): anonymous mocked benchmark plus routing/mixed intent, budgets, recall posture, scoped document citations/version/page reuse, fallback sources, complex selected IDs/unit, no duplicate tool execution, exhaustion/configuration/JSON recovery, numeric privacy and structurally bounded planner context.
- `tests/sals_wp3/test_runtime_pipeline.py`: replace unsafe cross-turn seeding expectation with no reuse of unattested entries. Legacy helper round-trip tests stay intact; no memory deletion occurs.
- `pwanimate/tests/test_document_context.py`: current-page integration verifies authorized identity and no redundant semantic retrieval after successful page resolution.
- `pwanimate/tests/test_activity_explanations.py`: anonymous synthetic user now supplies is_authenticated, required by the existing teaching eligibility contract.
- `pwanimate/tests/test_agent_activity.py`: SSE stub accepts existing timing argument; use the existing SRI isolated Python 3.14 inline-executor workaround; successful durable-result fixture explicitly reports stop. Runtime completion policy was not weakened.
- `scripts/test-spark-r4-local.sh` (new): reproducible isolated SQLite/mock-provider and Node suite entry point. It performs no Docker/PostgreSQL operation.
- `docs/spark/r4/`: five requested Markdown deliverables plus baseline/after/midpoint numeric JSON artifacts.

## Verification ledger

Final targeted results are appended below. Counts overlap and must not be added as unique tests. Axes middleware/backend warnings are inherited from isolated settings. Deliberately injected provider/worker/broker failures appear in logs and are not themselves test failures.

Initial focused routing/runtime run: 31 tests, one failed mixed-intent expectation. Router/test were corrected to retain mixed-task escalation. Focused next pass: 11 tests OK. Later 15-test routing pass: OK. Midpoint document/activity/SSE pass: 60 tests OK, one PostgreSQL skip. Runtime/gateway fallback/native streaming/R2 dispatch pass: 94 tests OK, two PostgreSQL skips, 20.805 seconds. Final expanded 98-test pass initially exposed one stale success fixture lacking terminal finish metadata; the fixture now explicitly reports stop and strict unknown/interrupted behavior remains.

The expanded legacy `pwanimate.tests.test_gateway` run (137 tests including R4/doc/activity labels) reported six failures and one stale activity-user fixture error. The fixture error was corrected separately. The six gateway mismatches remain visible and the gateway test file was not modified: Gemini legacy model alias expectation; Gemini thinking finish-reason vocabulary; Groq/OpenRouter finish-reason vocabulary; response serialization vocabulary; orchestrator finish-reason/continuation totals; thinking metadata/continuation totals. Existing SRI-01 uses canonical length and one continuation; legacy tests expect max_tokens and single-call totals. These appear to predate R4 from unchanged implementations and the SRI report; this is not a clean-tree baseline rerun proving their attribution. Do not report the full gateway suite green.

Two exploratory combined suites stalled at the old SSE test fixture and were stopped with Ctrl-C in their disposable test sessions. A later 240-second combined local runner and a 180-second combined SALS runner exceeded their process timeouts; neither is counted as passed. Smaller isolated scopes provide the evidence below. No owner process or application service was signalled.

Node contracts passed: activity explanations/privacy; lifecycle resume; canonical study context; real Marked/KaTeX math; question controls; merged composer; stream auto-collapse; R1 follow-up handler. JavaScript syntax, Python compilation, Bash syntax and git diff whitespace checks passed. These are mocked/static checks, not real-browser acceptance.

## Owner verification commands

From `/home/zayne/projects/pwaninet`, with the owner's normal virtualenv and disposable Docker tooling available:

```bash
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-spark-r4-local.sh
.venv/bin/python -m tests.sals_wp0.run --suite sals03wp2 tests.spark_r4 tests.sals_wp4 tests.sals_academic_continuity tests.sals_03_wp2 tests.spark_memory pwanimate.tests.test_document_context
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-wp4-concurrency-postgres.sh
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-r1-postgres.sh
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-r2-postgres.sh
SALS_TEST_PYTHON=.venv/bin/python bash scripts/test-sals-academic-continuity-postgres.sh
```

These use the existing disposable runner; do not substitute an owner/student database URL. R4 has no schema to apply. Preserve existing earlier migration/queue compatibility requirements and align web/worker/assets through the owner's deployment process; no restart/deployment is authorized or performed here. Reproduce the six legacy gateway expectation failures separately with isolated settings before deciding how to update those historical tests.

Repeat the offline benchmark without importing owner settings:

```bash
DJANGO_SETTINGS_MODULE=tests.spark_memory.settings .venv/bin/python -c 'import django; django.setup(); from tests.spark_r4.benchmark import measure; import json; print(json.dumps(measure(), indent=2))'
```

Complete every relevant scenario in ACCEPTANCE_CHECKLIST.md with synthetic accounts/documents. Live providers require separate authorization. PostgreSQL locking/concurrency, R2 broker recovery, deployed version alignment, source/access changes across durable pauses and real desktop/mobile/reconnect behavior are not certified by mocked tests.

## Compatibility and remaining limits

No parallel orchestrator or memory store, paid model, tool concurrency or destructive migration was introduced. Existing receipts and pending work remain durable. Disabling unversioned cross-turn reuse can increase tool work on some follow-ups; correctness takes precedence over reuse without current provenance. Existing durable checkpoint source/version revalidation has not been exhaustively certified by R4; owner revocation/version-change scenarios remain required. No global cache hit/transition ledger or preflight-wide SQL count is claimed; scoped metrics are documented in PERFORMANCE_REPORT.md.

The benchmark tests branches and call counts with an empty-plan synthetic provider, not teaching/research quality or live latency. Unsupported/general historical transcript recall still has no authorized source and must be disclosed honestly. The router is deliberately conservative and English/Swahili greeting examples do not certify all languages or mixed phrasing; uncertainty retains existing orchestration.

Pending: SRI-01 memory-resume PostgreSQL regression; Phase B live academic continuity; WP4 browser acceptance; R3 progressive hint restraint; R5 full student E2E; SALS 0.4 syllabus/content intelligence; SALS 0.5 quizzes/assignments/mock examinations; AI Gateway Z.AI/OpenRouter/Cloudflare expansion; separate Vasto-wide Lucide migration. Stop at R4.

## Final targeted results

- Final orchestration/context/document/activity suite: **98 tests, OK, one PostgreSQL skip**, 14.728 seconds (`/tmp/spark-r4-acceptance-local.log`). Includes the corrected explicit-stop durable fixture; no completion policy was relaxed.
- Final routing/budget/runtime-pipeline rerun: **40 tests, OK**, 1.253 seconds (`/tmp/spark-r4-final-budget.log`). Includes academic token-ceiling preservation, explicit override, structural academic JSON and all new R4 cases.
- Earlier native streaming/gateway fallback/R2 dispatch/runtime pass: **94 tests, OK, two PostgreSQL skips**, 20.805 seconds (`/tmp/spark-r4-runtime-final.log`). This overlaps the final scopes and predates the last anonymous-inventory/context/budget refinements; affected planner/direct paths were rerun in the final 98/40 scopes.
- Final Node contract rerun: all nine scripts passed; math script also verifies authored examples. Compilation, JS/Bash syntax and whitespace checks passed.
- A standalone diagnostic rerun of `UnifiedTests.test_handoff_parks_question_without_blocking_new_task` passed in 2.546 seconds. The full WP4 120-second and teaching/follow-up/continuity 180-second subsets exceeded timeout without a summary; they are not passed evidence. The isolated single-test success does not establish a cause for the combined-run timeout.
- The six legacy gateway expectation failures described above remain unresolved and visible. No full-green claim is made. Live PostgreSQL, provider generation, real browser, worker termination and deployed acceptance were not executed.

- Final isolated canonical WP4 baseline: **14 tests, OK, one PostgreSQL skip**, 24.285 seconds (`/tmp/spark-r4-canonical-final.log`). The earlier full-package timeout remains recorded; this narrower pass does not close PostgreSQL or browser acceptance.

- The additional teaching + academic-continuity focused run also reached its 120-second timeout without a summary; its last reported test was `ContinuityTests.test_owner_isolation_and_preconsent_exclusion`. Earlier listed cases reported OK, but this run is not certified passed. No cause is inferred from the timeout alone (`/tmp/spark-r4-teaching-focused.log`).

## Subsequent memory/tool repair

The user's cross-conversation “devouring last time” report and tool reachability repair are documented separately in [MEMORY_AND_TOOL_FOLLOWUP.md](MEMORY_AND_TOOL_FOLLOWUP.md), including exact added/changed files, authorization limits, final 132-test result and owner verification commands. Earlier measurements and failures above remain historical evidence; they are not silently replaced by the later pass.
