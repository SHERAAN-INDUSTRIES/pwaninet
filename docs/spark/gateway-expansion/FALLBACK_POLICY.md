# Fallback policy

Existing task/engine chains and safe explicit overrides determine ordering.
Missing keys, disabled models, unknown IDs, expired entitlements, unsupported
workloads, images and structured/tool requirements exclude expansion candidates.
Named offerings cannot bypass these checks. Tier permissions still apply.

The gateway attempts each eligible candidate once per request. Rate limits have
at least the existing cooldown (normally 60 seconds). Numeric and HTTP-date
Retry-After are respected. Network/timeout/503 failures receive a 15-second
cooldown. Repeated failures during an existing hold increase that hold, bounded
at 300 seconds unless Retry-After demands longer. There is no sleeping retry loop.
401/403 and 402 create provider-wide operator holds, cleared only by explicit
tracker reset or worker restart. A depleted balance is not a short rate limit.
Other API failures also receive the transient hold.

StudyAgent no longer retries a failed gateway call twice immediately. Invalid
planner text still has its existing bounded parse retries. Final response routing
sees the same unavailable candidates and skips them. Gateway exhaustion surfaces
through existing error/run recovery handling; this layer does not persist messages,
advance teaching state, mutate checkpoints, or manufacture evidence.

After any stream text is received, failure returns the partial response as
`interrupted`. It never appends another model's output. Unknown/missing finish
markers remain `unknown`, token exhaustion remains `length`; only explicit `stop`
can pass the existing academic completion classifier. Continuation uses the
existing durable-run lifecycle and consent boundaries.

Limitations: tracker state is in-memory per worker, not fleet-wide rate admission.
The application cannot inspect remote quota or guarantee cloud billing settings.
All-provider failure has no new orchestration loop. Existing general exhaustion
wording may still describe an operator hold as temporary; diagnostics carry the
specific errors/skip reasons. Generic providers do not expose raw response bodies.
