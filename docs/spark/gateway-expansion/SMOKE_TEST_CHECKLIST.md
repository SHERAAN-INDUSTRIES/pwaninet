# Optional owner-run live smoke checks

No live check was run. An owner must explicitly authorize API usage first.
Do not infer authorization from supplying a key. Confirm provider-side billing
restrictions and free model or quota/promotional entitlement before any probe.
Skip paid or unknown entitlements by default. Do not activate services, purchase
packages, unfreeze balances or modify subscriptions as part of this procedure.

1. Supply keys locally through the server environment; configure one provider,
   exact model and valid entitlement. Never paste keys into a report or shell echo.
2. For Tencent, review the owner's catalog (`GET /v1/models` in the official
   console/API tooling) and trial eligibility; public IDs alone are insufficient.
   Review Alibaba's Singapore quota and enable Free Quota Only.
3. After explicit authorization, run one selected model:

   ```bash
   .venv/bin/python manage.py spark_provider_smoke zai glm-4.7-flash --authorize-live
   ```

   The command makes at most two calls: one nonstreaming and one streaming,
   each capped at 16 output tokens, using synthetic `Reply only OK.` No gateway
   cascade, retry, student content, printed output content or printed credentials.
   Disabled or unentitled routes are skipped. An error stops the procedure.
4. Record provider/model, normalized finish, terminal-marker receipt and token
   counts. Require `stop` and usage in both modes. A valid request verifies
   authentication and model existence; it does not validate academic quality.
5. Confirm `LLMRouter.resolve_fallback_chain` includes this model for `general`
   under intended tier/configuration. Keep critical workloads excluded pending
   separate model-specific capability and teaching-quality verification.
6. Record actual quota decrement in the provider console and confirm no spend.
   Do not enable paid fallback based on this test.

The direct probe reports usage without creating student messages or usage ledger
records. Production usage continues through the existing billing path. Revoke
access or disable the model if usage, stop metadata or restrictions are uncertain.
