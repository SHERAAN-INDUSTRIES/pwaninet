"""Request-scoped pipeline timing for SALS WP3 observability.

A single TimingRecorder travels with the OrchestrationRequest so every layer
(HTTP view, orchestrator, agent planner, gateway wrapper) can stamp marks and
count calls without threading extra parameters. The snapshot is attached to
response metadata and the durable PwanimateTurnTrace so real provider latency
can be distinguished from local queueing and DB work after the fact.
"""

import time
from collections import Counter
from functools import wraps
from typing import Any, Dict


def measure_orchestration(fn):
    """Count SQL executions in this synchronous orchestrator scope only.

    No SQL/parameters are retained. HTTP preflight and separate worker threads
    are outside this scope and must not be described as measured by it.
    """
    @wraps(fn)
    def measured(self, request, *args, **kwargs):
        timing = getattr(request, 'timing', None)
        if timing is None:
            return fn(self, request, *args, **kwargs)
        from django.db import connection
        timing.count('orchestrator_database_queries', 0)
        def execute(executor, sql, params, many, context):
            timing.count('orchestrator_database_queries')
            return executor(sql, params, many, context)
        with connection.execute_wrapper(execute):
            try:
                response = fn(self, request, *args, **kwargs)
            except Exception:
                timing.count('orchestrator_failures')
                raise
            finally:
                timing.mark('orchestrator_done')
        response.metadata['perf'] = timing.snapshot()
        return response
    return measured


class TimingRecorder:
    """Monotonic clock + named marks/counters for one chat turn."""

    __slots__ = ("_t0", "marks", "counts", "durations", "strategy")

    def __init__(self) -> None:
        self._t0 = time.perf_counter()
        self.marks: Dict[str, float] = {}
        self.counts: Counter = Counter()
        self.durations: Dict[str, float] = {}
        self.strategy = 'unclassified'

    def record_response(self, response, role='answer') -> None:
        """Numeric gateway diagnostics only; never copy prompts or payloads."""
        for name in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            value = getattr(response, name, 0)
            if isinstance(value, int) and value >= 0:
                self.count(name, value)
        metadata = getattr(response, 'metadata', None) or {}
        attempted = metadata.get('attempted_candidates')
        count = metadata.get('provider_attempt_count')
        # Mock/custom gateways may not report attempt metadata.
        if type(count) is int and count >= 0:
            self.count('provider_attempts', count)
        elif isinstance(attempted, list):
            self.count('provider_attempts', len(attempted))
        reason = getattr(response, 'finish_reason', None)
        if isinstance(reason, str) and reason in {'stop','length','interrupted','cancelled','error','unknown'}:
            self.mark(('planner' if role == 'planner' else 'answer') + '_' + reason)

    def _elapsed(self) -> float:
        return round((time.perf_counter() - self._t0) * 1000.0, 2)

    def mark(self, name: str) -> None:
        """Record the first time a named lifecycle point was reached (ms)."""
        self.marks.setdefault(name, self._elapsed())

    def count(self, name: str, n: int = 1) -> None:
        self.counts[name] += n

    def observe(self, name: str, ms: float) -> None:
        """Accumulate wall time spent in a repeated operation (ms)."""
        self.durations[name] = round(self.durations.get(name, 0.0) + float(ms), 2)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "total_ms": self._elapsed(),
            "marks": dict(self.marks),
            "counts": dict(self.counts),
            "durations": dict(self.durations),
        }
