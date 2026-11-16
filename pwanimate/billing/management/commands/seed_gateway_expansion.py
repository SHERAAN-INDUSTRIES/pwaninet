"""Explicit owner-run registry update; does not change historical usage."""
from django.core.management.base import BaseCommand
from pwanimate.ai.providers.compatible import CompatibleLLMProvider, MODELS
from pwanimate.ai.gateway.types import LLMRequest
from pwanimate.billing.models import ModelOffering, CostClass

class Command(BaseCommand):
    help = 'Register expansion catalog; dry-run by default, --apply writes offerings only.'

    def add_arguments(self, parser):
        parser.add_argument('--apply', action='store_true')

    def handle(self, *args, **options):
        for provider, models in MODELS.items():
            adapter = CompatibleLLMProvider(provider)
            for model, kind in models.items():
                active = adapter.eligible(LLMRequest(task='general'), model)
                self.stdout.write(f'{provider}:{model} active={active} funding={kind}')
                if options['apply']:
                    ModelOffering.objects.update_or_create(provider=provider, model_id=model, defaults={
                        'key': f'{provider}-{model}', 'display_name': model,
                        'cost_class': CostClass.FREE_ROUTE, 'is_active': active,
                        'input_price_per_m_usd': 0, 'output_price_per_m_usd': 0,
                        'capabilities': {**adapter.capabilities(model), 'funding': kind,
                            'region': 'international', 'endpoint': adapter.base_url,
                            'free_only': True, 'paid_routing_supported': False}})
        if options['apply']:
            from pwanimate.billing.services import invalidate_offering_index
            invalidate_offering_index()
