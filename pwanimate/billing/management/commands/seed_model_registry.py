"""
Seed the tier catalogue and the model registry from the live engine ladders.

Idempotent: re-running updates the catalogue in place (it never duplicates a
row, and it never silently drops a tier that a subscription points at).

    python manage.py seed_model_registry
    python manage.py seed_model_registry --dry-run

The registry is derived from the ladders on purpose: a rung that is not in
the registry is a rung we cannot price, gate by tier, or account for.
`manage.py check_model_registry` reports any drift.
"""

import re
from typing import Dict, List, Tuple

from django.conf import settings
from django.core.management.base import BaseCommand

from pwanimate.ai.engines import list_engines
from pwanimate.billing.models import CostClass, ModelOffering, Tier

# The catalogue (docs/billing-tiers-design.md §3 and §10.3).
TIERS: List[Dict] = [
    {
        "code": "ember",
        "name": "Ember",
        "rank": 0,
        "price_kes": "0.00",
        "duration_days": 0,
        "is_default": True,
        "description": "Free tier — every Spark engine on the free routes, with local retrieval.",
        "entitlements": {
            "chats": {"day": 20, "week": 60},
            "images": {"day": 5},
            "premium_turns": {"day": 0, "period": 0},
        },
    },
    {
        "code": "day_pass",
        "name": "Day Pass",
        "rank": 1,
        "price_kes": "30.00",
        "duration_days": 1,
        "is_default": False,
        "description": "24 hours of premium models, priority and immediate image analysis.",
        "entitlements": {
            "chats": {"day": 60, "week": 300},
            "images": {"day": 20},
            "premium_turns": {"day": 10, "period": 10},
        },
    },
    {
        "code": "pro_monthly",
        "name": "Pro Monthly",
        "rank": 2,
        "price_kes": "300.00",
        "duration_days": 30,
        "is_default": False,
        "description": "A month of Spark with the frontier model, priority and generous limits.",
        "entitlements": {
            "chats": {"day": 200, "week": 1000},
            "images": {"day": 60},
            "premium_turns": {"day": 3, "period": 90},
        },
    },
    {
        "code": "teams",
        "name": "Teams",
        "rank": 3,
        "price_kes": "1200.00",
        "duration_days": 30,
        "is_default": False,
        "description": "Per seat, with shared collections, admin and company receipts.",
        "entitlements": {
            "chats": {"day": 500, "week": 2500},
            "images": {"day": 200},
            "premium_turns": {"day": 10, "period": 300},
        },
    },
]

# Paid offerings that are not (yet) ladder rungs, with their prices.
# USD per million tokens; the frontier class carries the real cost.
PAID_OFFERINGS: List[Dict] = [
    {
        "key": "gemini-flash-paid",
        "provider": "gemini",
        "model_id": "gemini-3.8-flash-paid",
        "display_name": "Gemini Flash (paid tier)",
        "cost_class": CostClass.PAID_FLASH,
        "min_tier_code": "day_pass",
        "input_price_per_m_usd": "0.075000",
        "output_price_per_m_usd": "0.300000",
    },
    {
        "key": "vision-external",
        "provider": "vision",
        "model_id": "external-vl",
        "display_name": "External vision model",
        "cost_class": CostClass.VISION_EXTERNAL,
        "min_tier_code": "day_pass",
        "input_price_per_m_usd": "0.010000",
        "output_price_per_m_usd": "0.030000",
    },
    {
        "key": "frontier-pro",
        "provider": "gemini",
        "model_id": "gemini-pro-latest",
        "display_name": "Frontier model",
        "cost_class": CostClass.FRONTIER,
        "min_tier_code": "pro_monthly",
        "input_price_per_m_usd": "1.500000",
        "output_price_per_m_usd": "6.000000",
    },
]


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or "model"


class Command(BaseCommand):
    help = "Seed the Spark tier catalogue and the model registry (idempotent)."

    def add_arguments(self, parser):
        parser.add_argument("--dry-run", action="store_true", help="Show the plan without writing.")

    # ------------------------------------------------------------------ tiers
    def _seed_tiers(self, dry_run: bool):
        for spec in TIERS:
            existing = Tier.objects.filter(code=spec["code"]).first()
            if dry_run:
                action = "update" if existing else "create"
                self.stdout.write(f"  [tier] {action} {spec['code']} ({spec['name']})")
                continue
            tier, created = Tier.objects.update_or_create(
                code=spec["code"],
                defaults={
                    "name": spec["name"],
                    "rank": spec["rank"],
                    "price_kes": spec["price_kes"],
                    "duration_days": spec["duration_days"],
                    "is_default": spec["is_default"],
                    "description": spec["description"],
                    "entitlements": spec["entitlements"],
                    "is_active": True,
                },
            )
            self.stdout.write(
                f"  [tier] {'created' if created else 'updated'} {tier.code} ({tier.name})"
            )

    # -------------------------------------------------------------- offerings
    def _ladder_pairs(self) -> List[Tuple[str, str]]:
        """Every (provider, model) the app can actually reach."""
        pairs: List[Tuple[str, str]] = []
        for engine in list_engines():
            for rung in engine.get("fallback_chain") or []:
                provider = rung.get("provider")
                model = rung.get("model")
                if provider and model and (provider, model) not in pairs:
                    pairs.append((provider, model))
        for item in getattr(settings, "PWANIMATE_LLM_FALLBACK_CHAIN", []) or []:
            if isinstance(item, dict):
                provider = item.get("provider")
                model = item.get("model")
            else:
                provider, model = str(item).split(":", 1) if ":" in str(item) else (str(item), None)
            if provider and model and (provider, model) not in pairs:
                pairs.append((provider, model))
        return pairs

    def _seed_offerings(self, dry_run: bool):
        tiers = {tier.code: tier for tier in Tier.objects.all()}
        free_tier = next((tier for tier in tiers.values() if tier.is_default), None)

        # Paid offerings are seeded FIRST and looked up by (provider, model):
        # a paid model can also sit in a ladder (gemini-pro-latest is in the
        # global Auto chain), so the row is RE-CLASSIFIED in place rather than
        # duplicated. Seeding never downgrades a priced offering to free.
        for spec in PAID_OFFERINGS:
            tier = tiers.get(spec["min_tier_code"])
            existing = ModelOffering.objects.filter(
                provider=spec["provider"], model_id=spec["model_id"]
            ).first()
            if dry_run:
                action = "re-classify" if existing else "create"
                self.stdout.write(
                    f"  [offering] {action} {spec['key']} (min tier: {spec['min_tier_code']})"
                )
                continue
            if existing:
                existing.cost_class = spec["cost_class"]
                existing.min_tier = tier
                existing.input_price_per_m_usd = spec["input_price_per_m_usd"]
                existing.output_price_per_m_usd = spec["output_price_per_m_usd"]
                existing.display_name = spec["display_name"]
                existing.is_active = True
                # Keep the key deterministic across environments: a registry
                # seeded from scratch and one re-classified in place must
                # expose the same key to the client.
                if existing.key != spec["key"] and not ModelOffering.objects.filter(
                    key=spec["key"]
                ).exists():
                    existing.key = spec["key"]
                existing.save()
                self.stdout.write(
                    f"  [offering] re-classified {existing.key} -> {spec['cost_class']} "
                    f"(min tier: {spec['min_tier_code']})"
                )
                continue
            ModelOffering.objects.create(
                key=spec["key"],
                provider=spec["provider"],
                model_id=spec["model_id"],
                display_name=spec["display_name"],
                cost_class=spec["cost_class"],
                min_tier=tier,
                input_price_per_m_usd=spec["input_price_per_m_usd"],
                output_price_per_m_usd=spec["output_price_per_m_usd"],
                is_active=True,
            )
            self.stdout.write(f"  [offering] created {spec['key']} (min tier: {spec['min_tier_code']})")

        count = 0
        for provider, model_id in self._ladder_pairs():
            if provider == "mock":
                continue  # the friendly-error rung is not a model
            existing = ModelOffering.objects.filter(
                provider=provider, model_id=model_id
            ).first()
            if existing:
                # Already registered (possibly as a paid offering) — never
                # downgrade it to a free row.
                count += 1
                continue
            key = _slugify(f"{provider}-{model_id}")
            if dry_run:
                self.stdout.write(f"  [offering] create {key} ({provider}:{model_id})")
                count += 1
                continue
            ModelOffering.objects.create(
                key=key,
                provider=provider,
                model_id=model_id,
                display_name=model_id,
                cost_class=CostClass.FREE_ROUTE,
                min_tier=free_tier,
                input_price_per_m_usd="0",
                output_price_per_m_usd="0",
                is_active=True,
            )
            count += 1
        self.stdout.write(f"  [offering] {count} ladder rungs registered")

    def handle(self, *args, **options):
        dry_run = bool(options.get("dry_run"))
        self.stdout.write("Seeding the Spark tier catalogue and model registry...")
        self._seed_tiers(dry_run)
        self._seed_offerings(dry_run)
        if dry_run:
            self.stdout.write(self.style.WARNING("Dry run — nothing written."))
            return
        self.stdout.write(self.style.SUCCESS("Registry seeded."))
