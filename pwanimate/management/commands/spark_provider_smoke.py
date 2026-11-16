"""Owner-invoked synthetic probes only. Never cascades or prints content/keys."""
from django.core.management.base import BaseCommand, CommandError
from pwanimate.ai.gateway.types import LLMRequest, ChatMessage
from pwanimate.ai.providers.compatible import CompatibleLLMProvider, PROVIDERS
from pwanimate.ai.exceptions import AIGatewayError

class Command(BaseCommand):
    help = 'Optional live probe; requires explicit --authorize-live and confirmed free entitlement.'

    def add_arguments(self, parser):
        parser.add_argument('provider', choices=sorted(PROVIDERS))
        parser.add_argument('model')
        parser.add_argument('--authorize-live', action='store_true')

    def handle(self, *args, **options):
        if not options['authorize_live']:
            raise CommandError('Live usage requires owner authorization: --authorize-live')
        adapter = CompatibleLLMProvider(options['provider'])
        request = LLMRequest(task='general', model=options['model'], max_tokens=16,
                             messages=[ChatMessage('user', 'Reply only OK.')])
        if not adapter.eligible(request, options['model']):
            self.stdout.write('SKIP: disabled, missing key, or no confirmed free entitlement.')
            return
        for streaming in (False, True):
            try:
                result = adapter.generate_stream(request, lambda _: None) if streaming else adapter.generate(request)
                self.stdout.write(str({'provider': result.provider, 'model': result.model,
                    'streaming': streaming, 'finish_reason': result.finish_reason,
                    'terminal_finish_received': result.metadata['terminal_finish_received'],
                    'prompt_tokens': result.prompt_tokens, 'completion_tokens': result.completion_tokens,
                    'total_tokens': result.total_tokens, 'usage_reported': result.metadata['usage_reported']}))
                if result.finish_reason != 'stop' or not result.metadata['usage_reported']:
                    raise CommandError('Probe incomplete or usage missing; do not approve critical workloads.')
            except AIGatewayError as exc:
                raise CommandError(f'Probe failed: {type(exc).__name__}, status={exc.status_code}; no cascade.') from None
