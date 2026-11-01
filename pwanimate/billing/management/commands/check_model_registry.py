"""
Check the model registry against the live engine ladders.

Two things it proves:
1. **Every rung the app can reach is registered** — an unregistered rung
   cannot be priced, gated by tier, or accounted for.
2. **No FREE_ROUTE offering points at a paid model** — on OpenRouter that
   means the id must carry the `:free` suffix. This is the check that would
   have caught `google/gemini-3.8-flash` (paid) sitting in every ladder.

Exit code 0 = clean, 1 = problems found (use it in CI).
"""

from django.core.management.base import BaseCommand

from pwanimate.ai.engines import list_engines
from pwanimate.billing.models import CostClass, ModelOffering
from pwanimate.billing.management.commands.seed_model_registry import _slugify


class Command(BaseCommand):
    help = "Verify the model registry matches the engine ladders and the $0 guarantee."

    def add_arguments(self, parser):
        parser.add_argument("--quiet", action="store_true", help="Only print problems.")

    def handle(self, *args, **options):
        quiet = bool(options.get("quiet"))
        problems = []
        registered = 0
        checked = 0

        for engine in list_engines():
            for rung in engine.get("fallback_chain") or []:
                provider = rung.get("provider")
                model_id = rung.get("model")
                if not provider or not model_id or provider == "mock":
                    continue
                checked += 1
                offering = ModelOffering.objects.filter(provider=provider, model_id=model_id).first()
                if offering is None:
                    problems.append(
                        f"[{engine['slug']}] {provider}:{model_id} is not in the registry "
                        f"(run `manage.py seed_model_registry`)"
                    )
                    continue
                registered += 1
                if offering.cost_class == CostClass.FREE_ROUTE:
                    if provider == "openrouter" and not model_id.endswith(":free"):
                        problems.append(
                            f"[{engine['slug']}] {provider}:{model_id} is classed FREE_ROUTE but "
                            f"is not a ':free' id — free-tier traffic would be billed"
                        )
                    if float(offering.input_price_per_m_usd or 0) > 0 or float(
                        offering.output_price_per_m_usd or 0
                    ) > 0:
                        problems.append(
                            f"[{engine['slug']}] {provider}:{model_id} is classed FREE_ROUTE but "
                            f"carries a price"
                        )

        # Offerings that are priced but unreachable are not a problem — they are
        # the paid catalogue (frontier, external vision) waiting for their rung.
        orphan_free = ModelOffering.objects.filter(cost_class=CostClass.FREE_ROUTE).count()

        if not quiet:
            self.stdout.write(f"ladder rungs checked : {checked}")
            self.stdout.write(f"registered as offerings: {registered}")
            self.stdout.write(f"free-route offerings : {orphan_free}")

        if problems:
            for problem in problems:
                self.stderr.write(self.style.ERROR(f"  {problem}"))
            self.stderr.write(self.style.ERROR(f"Registry check failed: {len(problems)} problem(s)."))
            raise SystemExit(1)

        if not quiet:
            self.stdout.write(self.style.SUCCESS("Registry check passed: every rung is registered and every free rung is free."))
