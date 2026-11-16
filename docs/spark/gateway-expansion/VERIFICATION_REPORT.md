**E Verification report — 2026-11-14**  
**Status**  
Conservative integration checkpoint; **the requested full milestone is not yet**  
 **  
 complete**. Four independent provider identities are wired into the existing  
   
 router and one shared adapter. No new orchestration engine or academic schema.  
   
 All expansion providers are disabled by default. No actual keys were inspected,  
   
 no live provider calls, no payment/account operations, no owner database access,  
   
 no migrations, no registry writes, no commits.  
**Exact files changed by this work**  
Modified (pre-existing unrelated edits preserved):  
- pwanimate/ai/gateway/gateway.py: record failure holds instead of immediately  
   
 clearing transient cooldown; record authentication holds.  
- pwanimate/ai/gateway/quota_tracker.py: Retry-After, transient backoff and  
   
 provider-wide operator holds; existing status/reset interfaces retained.  
- pwanimate/ai/gateway/router.py: register four provider IDs, per-task settings,  
   
 model eligibility filtering and post-offering safety check.  
- pwanimate/billing/services.py: free-route detection uses current expansion  
   
 model eligibility instead of guessing from model names.  
- pwanimate/orchestrator/agent.py: only planner exception capture/condition  
   
 changed by this task; gateway failures do not trigger immediate planner retries.  
- pwaninet/settings/base.py: server-side key and JSON configuration loading.  
Added:  
- pwanimate/ai/providers/compatible.py  
- pwanimate/billing/management/commands/seed_gateway_expansion.py  
- pwanimate/management/commands/spark_provider_smoke.py  
- tests/gateway_expansion/__init__.py  
- tests/gateway_expansion/test_contracts.py  
- scripts/test-gateway-expansion-postgres.sh  
- These five files under docs/spark/gateway-expansion/.  
The in-code catalog contains two IDs per provider (see architecture document).  
   
 No database models were registered during this session. The owner can preview  
   
 and apply registry entries with seed_gateway_expansion.  
**Automated verification**  
All commands use isolated settings that do not import base/local or read dotenv.  
DJANGO_SETTINGS_MODULE=tests.spark_memory.settings .venv/bin/python manage.py test tests.gateway_expansion --noinput  
 SPARK_MEMORY_SYNC_SCHEMA=1 DJANGO_SETTINGS_MODULE=tests.spark_memory.settings .venv/bin/python manage.py test tests.sals_wp4.test_sri_repairs tests.sals_03_wp2 tests.spark_memory --noinput  
   
- Initial expansion suite: 9 tests passed. Covers exact endpoints/model IDs,  
   
 enabled/missing keys, expiry/entitlement gates, usage, stop/unknown/length,  
   
 malformed payloads, 401/402/429/503, Retry-After, provider cascade exhaustion,  
   
 no immediate second attempts, and stream disconnect without splice.  
- Expanded contracts plus existing GatewayStreamingTests: 16 tests passed.  
   
 This exposed and fixed compatibility with injected legacy quota trackers.  
- Final verification: **18 tests passed** (14 expansion contracts plus 4 existing  
   
 gateway streaming tests), including engine-chain precedence and Retry-After  
   
 HTTP dates. A minimal no-installed-apps attempt could not import existing  
   
 retrieval model dependencies; the successful run used repository isolated settings.  
- SALS/SRI-01/memory: 216 tests ran; 1 error, 12 skipped. The error is  
 MemoryPipelineTests.test_existing_resume_executor_refreshes_snapshot_after_deletion  
   
 (execute.call_args is None). No academic evidence tests failed in this run.  
- Legacy gateway plus initial expansion: 63 tests ran; 6 failures. Existing  
   
 expectations include old Gemini default ID and max_tokens rather than current  
   
 canonical length; another expects 1878 prompt tokens where the current  
   
 orchestration path reports 3756.  
- Baseline reproduction, using an in-memory import overlay of HEAD gateway,  
   
 router, tracker and billing services plus StudyAgent with only this task's  
   
 retry edit removed, reproduced all 6 failures and the memory-resume error:  
   
 57 tests, 6 failures, 1 error. The working tree was never rolled back.  
- Broad legacy streaming run was interrupted after an SSE test-double mismatch  
   
 (fake_post_chat lacks existing timing keyword). No full streaming-suite pass  
   
 is claimed.  
- JavaScript checks passed: chat script syntax, teaching-flow, lifecycle-resume,  
   
 markdown-math-unit, question-controls. Shell syntax and Python compilation  
   
 passed; git diff --check passed.  
**Owner verification**  
Before deployment, review configuration, exact quota/expiry and provider-side  
   
 billing restrictions. Preview registry changes:  
.venv/bin/python manage.py seed_gateway_expansion  
 # Only after reviewing the preview:  
 .venv/bin/python manage.py seed_gateway_expansion --apply  
 # Disposable PostgreSQL/pgvector verification; never accepts a live DB URL:  
 bash scripts/test-gateway-expansion-postgres.sh  
   
The PostgreSQL runner provisions its own container, strips inherited credentials,  
   
 uses isolation guards, and removes its container. It was not run here. Optional  
   
 live probes require separate explicit authorization; see smoke checklist.  
**Remaining limitations and architectural conflict**  
- Advanced model-specific streaming options, tool calls, JSON, reasoning behavior  
   
 and usage trailers are not live-verified. Tools/JSON/images and all critical  
   
 workloads remain excluded rather than falsely advertised.  
- New providers do not yet serve academic teaching, math/physics/programming,  
   
 planner or memory tasks. Existing Gemini/Groq routes remain responsible.  
- Paid fallback is unsupported. Existing billing records usage after generation;  
   
 a hard multi-worker budget reservation would require a cross-cutting accounting  
   
 change. No destructive redesign or false budget guarantee was introduced.  
- Promotional DeepSeek requires owner-confirmed balance AND provider-side spend  
   
 prevention. Without both, keep it disabled.  
- Region switching, fleet-wide rate admission and live remote quota inspection  
   
 are not implemented. Eligibility is local and cooldowns are process-local.  
- Engine-provided chains retain precedence; existing selector choices are not  
   
 changed. Registry active flags require re-sync when configuration changes.  
- Mock contract success is not live availability or tutoring-quality evidence.  
- No claim is made that the entire automated suite passes or the full requested  
   
 gateway-expansion success criteria are met.  
