"""Offline synthetic orchestration benchmark; never constructs a real gateway."""
import json
import statistics
import time
from unittest.mock import Mock, patch
from pwanimate.ai.gateway.types import LLMResponse
from pwanimate.context import ContextEngine
from pwanimate.orchestrator.service import PwanimateOrchestrator
from pwanimate.orchestrator.types import OrchestrationRequest
from pwanimate.orchestrator.timing import TimingRecorder
from pwanimate.retrieval.types import RetrievalResponse

WORKLOADS = {
    'greeting': 'Wagwan Spark!',
    'emoji_greeting': 'Wagwan chief 😂',
    'thanks': 'Thanks chief 😂',
    'explanation': 'Explain polymorphism.',
    'mixed': 'Good morning. Explain polymorphism.',
    'recall': 'What did I study last time?',
    'math': 'Calculate the determinant of this matrix.',
    'code': 'Debug this Java method.',
    'complex': 'Compare three previous examination papers.',
}


def measure(repeats=20):
    results = {}
    for label, query in WORKLOADS.items():
        samples = []
        for _ in range(repeats):
            gateway = Mock()
            def generate(req):
                return LLMResponse(content='{"calls": []}' if req.task == 'tool' and
                    'planner' in (req.system_instruction or '') else 'Synthetic answer.',
                    provider='mock', model='mock', finish_reason='stop',
                    prompt_tokens=20, completion_tokens=5, total_tokens=25, metadata={'provider_attempt_count': 1})
            gateway.generate.side_effect = generate
            def stream(req, callback):
                result = generate(req)
                callback(result.content)
                return result
            gateway.generate_stream.side_effect = stream
            retrieval = Mock()
            retrieval.retrieve.side_effect = lambda *a, **k: RetrievalResponse(query=query)
            router, selector, registry = Mock(), Mock(), Mock()
            router.route.return_value = None
            selector.required_web_query.return_value = None
            selector.might_need_tool.return_value = False
            registry.list_tools.return_value = []
            service = PwanimateOrchestrator(retrieval, ContextEngine(), gateway, router, registry, selector)
            timing = TimingRecorder()
            request = OrchestrationRequest(query=query, timing=timing)
            with patch('pwanimate.services.social_updates.get_pending_friend_post_updates', return_value=[]), \
                 patch('pwanimate.services.teaching.TeachingService.applies', return_value=False), \
                 patch.object(service, '_record_usage'), patch.object(service, '_request_metadata', return_value={}):
                start = time.perf_counter()
                response = service.run(request, on_token=lambda _: None)
                elapsed = (time.perf_counter() - start) * 1000
            samples.append({'elapsed_ms': elapsed, 'ttft_ms': timing.marks.get('first_token'),
                'planning': timing.counts['planning_calls'],
                'answers': gateway.generate_stream.call_count,
                'retrieval': retrieval.retrieve.call_count,
                'gateway_calls': gateway.generate.call_count + gateway.generate_stream.call_count,
                'tokens': response.total_tokens,
                'observed_tokens': timing.counts['total_tokens'],
                'database_queries_in_orchestrator': timing.counts['orchestrator_database_queries'],
                'tool_calls': timing.counts['tool_executions'],
                'provider_attempts': timing.counts['provider_attempts'],
                'classification_ms': timing.durations.get('classification_ms')})
        results[label] = {**samples[-1], 'elapsed_ms': round(statistics.median(s['elapsed_ms'] for s in samples), 3),
            'ttft_ms': statistics.median(s['ttft_ms'] for s in samples if s['ttft_ms'] is not None)}
    return results

if __name__ == '__main__':
    print(json.dumps(measure(), indent=2))
