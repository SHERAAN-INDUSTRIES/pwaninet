# R4 performance evidence

Twenty iterations of the same anonymous synthetic workloads, actual orchestrator with immediate mocked provider and empty retrieval. No paid/live providers, student database or deployed service was used. JSON artifacts retain raw median measurements. Planning/answer/retrieval counts are deterministic; timings are local microbenchmarks, not production latency improvements.

## Before → after

- **greeting**: planner 0 → 0; answers 1 → 1; retrieval requests 0 → 0; gateway calls 1 → 1; local median 5.034 → 3.924 ms; recorder first-token median 11.38 → 9.94 ms.
- **emoji_greeting**: planner 1 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 2 → 1; local median 28.516 → 2.614 ms; recorder first-token median 37.19 → 12.61 ms.
- **thanks**: planner 1 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 2 → 1; local median 29.358 → 3.165 ms; recorder first-token median 37.12 → 8.32 ms.
- **explanation**: planner 0 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 1 → 1; local median 6.483 → 4.853 ms; recorder first-token median 14.59 → 10.12 ms.
- **mixed**: planner 0 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 1 → 1; local median 6.747 → 13.13 ms; recorder first-token median 15.12 → 30.68 ms.
- **recall**: planner 0 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 1 → 1; local median 7.77 → 2.415 ms; recorder first-token median 14.26 → 6.95 ms.
- **math**: planner 0 → 0; answers 1 → 1; retrieval requests 0 → 0; gateway calls 1 → 1; local median 8.581 → 9.544 ms; recorder first-token median 17.26 → 15.77 ms.
- **code**: planner 1 → 0; answers 1 → 1; retrieval requests 1 → 0; gateway calls 2 → 1; local median 50.105 → 11.933 ms; recorder first-token median 68.97 → 24.70 ms.
- **complex**: planner 0 → 1; answers 1 → 1; retrieval requests 1 → 1; gateway calls 1 → 2; local median 12.782 → 13.943 ms; recorder first-token median 23.02 → 23.84 ms.

## Interpretation and limits

Measured call reductions: emoji greeting/thanks and code each remove one planner call and one retrieval request. Explanations and saved-history recall remove broad retrieval. Ordinary math and plain greeting retain the same budgets. Complex paper comparison deliberately adds one planner call: preserving the requested workflow is more important than making an incorrectly shallow path look fast. The synthetic planner returns no calls, so this benchmark does not prove successful document comparison or research.

Each mocked provider response uses exactly 25 synthetic tokens. Thus two-call workloads consume 50 synthetic provider tokens versus 25 for one-call workloads. The response-level `tokens` field remains 25 when an empty planner falls back: it does not include that abandoned plan. R4 TimingRecorder now counts successful planner and answer token usage together. These fixed fixtures do not measure real prompt-token reduction or cost savings. No provider fallback is exercised by this harness; exhaustion/configuration/cooldown behavior is tested separately.

Document regression tests measure reuse of already resolved page evidence with zero additional unified retrieval calls, original citation and version retained. Without usable page evidence, the strategy requests only the selected document IDs; explicit source selection remains authoritative. The old default searched four source types; source narrowing is a code/test-verified reduction in scope, not a measured database latency improvement. Page retrieval itself still has an upstream cost.

The same-host timing differences are noisy, including unchanged-path changes. Recorder TTFT begins at recorder construction before patch/setup; total elapsed begins just before run, so TTFT can exceed elapsed. Both before/after share this definition. Neither metric includes HTTP dispatch, real provider/network/queue latency, browser paint or production PostgreSQL. Do not interpret the percentage differences as production gains.

Classification duration and strategy, answer/planner/model calls, numeric token/provider attempts, tool executions/duplicate skips, retrieval requests/duration, page evidence reuse and context-engine assembly duration are available through existing perf snapshots. Existing API lifecycle marks retain academic preflight/history timing and first-chunk/final-event marks. Durable continuation now also attaches numeric perf metadata.

SQL execution counts are now collected within synchronous Orchestrator.run using Django execute_wrapper, retaining no SQL or parameters. HTTP preflight, separate worker scopes, cache hit rates across all services and durable transition counts are not automatically collected globally by this patch. The anonymous benchmark performs no database/durable transitions; after-instrumentation orchestration query counts are zero, while the original baseline did not collect that counter. Durable transitions remain unknown here. Page evidence reuse and duplicate suppression are explicit counters, not fabricated global cache hits. Provider attempt counts require the real gateway metadata or a fixture supplying it. Failed attempts may lack usage; totals are observed successful-response usage, not a billing ledger.

Expected, unmeasured effects: less retrieval/model work should reduce live latency and cost; scoped source reads should reduce DB/search load. Fresh authorization remains uncached. Disabling unversioned cross-turn observation seeding can increase tool work in some follow-ups; this is an intentional access/freshness tradeoff. No tool concurrency or provider policy change was introduced.

A midpoint instrumentation probe (`instrumentation_probe.json`) exposed one attempted optional anonymous inventory SQL execution in the complex branch. The query hit only the offline in-memory SQLite database and the optional inventory handler caught the missing-schema failure. The final runtime skips personal inventory for unauthenticated users; final after.json reports zero scoped SQL executions for all nine anonymous workloads. This is an additional measured local work reduction, not a student-database benchmark. Classification duration in raw JSON is the final iteration sample, unlike the median elapsed/TTFT fields.
