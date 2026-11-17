# Admin dashboard app

## Purpose

`admin_dashboard` is the staff command center: operational metrics, the AI console (providers, model switches, token usage), and feedback ticket workflows.

## Main data

The core models are `FeedbackTicket`, `FeedbackReply`, and `UserDailyActivity`. Authenticated requests update a per-user daily activity record for online and daily-active metrics. AI console pages read live data assembled in `admin_dashboard/services.py` (`provider_health`, `engine_health`, `model_catalog`, `token_usage`, `platform_overview`, `registry_health`, `ops_summary`) rather than defining models of their own.

## Routes and entry points

Mounted at `/dashboard/`. Every staff route repeats `@login_required` + `@user_passes_test(is_admin)` explicitly (no middleware gate), and non-staff requests are covered per-route in `admin_dashboard/tests.py`.

| Route | View | Notes |
| --- | --- | --- |
| `/dashboard/` | `dashboard_home` | Overview: stats, 14-day growth/DAU charts, unread feedback |
| `/dashboard/documents/` | `documents` | Site document inventory: indexing state tabs (fully/partially/not started), chunk & embedding-model diagnostics, views/downloads/uploader, re-index (ingest) or embed-only actions |
| `/dashboard/documents/<id>/reindex/` | `reindex_document` | **POST only**; queues latest-version ingestion/chunking+embeddings or embed-only retry for a document |
| `/dashboard/ai/providers/` | `ai_providers` | Provider keys/states, Spark engines, LLM + vision fallback chains, live rate limits |
| `/dashboard/ai/models/` | `ai_models` | Full model catalog with on/off switches |
| `/dashboard/ai/models/toggle/` | `toggle_model` | **POST only**; flips a registered `ModelOffering.is_active` |
| `/dashboard/ai/usage/` | `ai_usage` | Token/cost usage, `?days=7|30|90` (others fall back to 30) |
| `/dashboard/users/logins/` | `user_logins` | Merged audit feed: successes, failures/blocked, new devices |
| `/dashboard/admins/` | `admin_manager` | Staff roster + read-only for staff; superusers see **Revoke sessions** |
| `/dashboard/admins/<int:user_id>/revoke/` | `revoke_user_sessions` | **POST only, superuser-only**; deletes Django `Session` rows and `UserSession` rows |
| `/dashboard/feedback/` | `feedback_inbox` | Staff feedback inbox |
| `/dashboard/feedback/<uuid>/` | `feedback_detail` | Ticket thread + reply |
| `/dashboard/feedback/user/<uuid>/` | `user_feedback_view` | User-facing (stays on `base.html`) |
| `/dashboard/feedback/submit/` | `submit_feedback` | User-facing composer partial |

## Workspace shell (three-surface command center)

Page templates extend `admin_dashboard/base_workspace.html`, which opts the document into `adw-active` and replaces the topbar/left rail/context rail blocks. Design follows the student dashboard (`vdw-`) with Spark interaction rules:

- **Left rail owns navigation.** Real `<a href>` links with `hx-get` + `hx-target="#page-content-target"` + `hx-push-url="true"`; each view re-sends the rail out-of-band so active state and badges stay current. Collapses to a 56px icon strip (`adw_left_collapsed`).
- **Context rail is collapsed by default** (`--adw-ctx-width: 0px`) and shows live ops: online users, engines, provider health, quota cooldowns, registry health, model tally. The topbar opens it; the divider drags it up to an **equal 50/50 split with the centre panel** (480px centre floor, 280px rail floor). State persists in `adw_ctx_open` / `adw_ctx_width`.
- **Hard workspace boundary.** Entering or leaving `/dashboard/` (except the user-facing `feedback/user/` and `feedback/submit/` routes) always forces a native full-page navigation. `static/js/admin/admin-workspace.js` is a **non-defer `<head>` script** so its capture-phase click listener registers before `base.html`'s inline handlers; a marker check on `htmx:beforeSwap` (`.admin-workspace` XOR `body.adw-active`) is the belt-and-braces guard. This keeps admin chrome/CSS from leaking onto normal pages and vice versa.

### Response shapes

`render_workspace(request, page_template, content_partial, context, page_title)` in `views.py` picks the shape:

- normal request → full page template (`{% block workspace_content %}` wraps content in `<div class="admin-workspace" id="admin-workspace" data-admin-title="…">`);
- `HX-Request: true` → `partials/admin_navigation_partial.html`: the same wrapper around the content partial, plus out-of-band `#left-nav-rail` and `#context-rail-target` refresh. `#navbar-top` is deliberately never swapped.

Both shapes get `ops` / `engines` / `providers` / `registry` injected by `workspace_context()`.

## Model switch (kill switch)

- Only models present in the `ModelOffering` registry are toggleable; anything else renders inert with a `seed_model_registry` hint. Registration stays deliberate because enabling an unregistered model would publish it to the free tier unpriced.
- `toggle_model` validates `provider`/`model_id`, calls `services.set_model_active()`, invalidates the offering cache, and publishes an `admin.model.toggled` audit event. The HX response re-renders the models content with an inline flash; the non-HX path redirects with a Django message.
- Enforcement lives in `pwanimate/ai/gateway/router.py::LLMRouter.resolve_fallback_chain()` via `pwanimate/billing/services.py::is_offering_disabled()`: if every rung of a request's chain is switched off it raises `AIProviderConfigurationError("Every model for this request is switched off in the registry.", provider="registry")`.
- The disabled index is process-local with a **60-second TTL** (cleared by `invalidate_offering_index()`). Expect up to ~60s for a toggle to propagate across workers; the UI states this.

## Developer notes

- Keep staff-only operations protected by explicit permission checks on each view.
- Feedback submissions may include contact details or personal information; limit access and avoid putting sensitive values in logs.
- Daily activity data begins accumulating after the `UserDailyActivity` migration is deployed; it does not backfill historical daily activity.
- User logins merges `axes_accesslog`, `axes_accessfailurelog`, and `users_deviceverificationevent`; there is no location for failures, and success location comes from the account's `UserSession.location` (best-effort, IP-only when `GEOIP_PATH` is set).
- Admin revoke deletes both `django_session` and `users.UserSession` rows via `session_service.revoke_all_user_sessions()`. It is superuser-only.
- The activity table stores account-linked per-day records. Set and document a retention period before launch.
- Admin charts load Chart.js on demand (`window.vastoChartJsPromise`) so first-load and HTMX navigation do not depend on a CDN script race; each page registers its own `htmx:afterSwap`/`themeChanged` re-render hook with a separate guard flag.
- Rate-limit/cooldown state (`QuotaTracker`) is per worker process; copy in the UI says so.
- Provider credentials are read from settings (`PWANIMATE_GEMINI_API_KEY`, `PWANIMATE_GROQ_API_KEY`, `PWANIMATE_OPENROUTER_API_KEY`, `PWANIMATE_CLOUDFLARE_API_TOKEN` + `PWANIMATE_CLOUDFLARE_ACCOUNT_ID`, `ZAI_API_KEY`, `ALIBABA_DASHSCOPE_API_KEY`, `TENCENT_TOKENHUB_API_KEY`, `DEEPSEEK_API_KEY`; ollama/mock need none) — presence only, values never rendered.
