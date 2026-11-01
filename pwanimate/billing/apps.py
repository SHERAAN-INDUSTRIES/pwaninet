"""
Billing & usage app (docs/billing-tiers-design.md).

The tier catalogue, the model registry, per-user usage accounting and the
quota decisions the request path enforces.
"""

from django.apps import AppConfig


class BillingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "pwanimate.billing"
    label = "pwanimate_billing"
    verbose_name = "Pwanimate Billing & Usage"
